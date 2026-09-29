"""A report for a server mailctl does not recognise, redacted for filing.

Each half's server is matched against the server modules mailctl knows
(IMAP ``ID``, ManageSieve ``IMPLEMENTATION``); one that matches none is
worked with as the plain protocol. That is ordinary for a new host, and it
is also the one thing mailctl cannot enumerate in advance, so this builds
what an issue about it needs: each server's identity and capabilities, the
account's shape as counts, and mailctl's version.

Nothing identifying the account leaves here. The address, its domain and
local part, the configured hosts, every folder name, and every rule-set
name are replaced wherever they appear, and so is anything shaped like an
address or an IPv4 address; an account capability keeps its name and loses
its value. The password is never read, and neither is any rule set's text
or any message. Read-only, and it sends nothing anywhere: the report is
data and a document for the person to read, edit, and file themselves.
"""

import re
from dataclasses import dataclass

from .. import __version__
from ..config import Config
from ..engine import Session
from ..providers.base import Capability, Fact, Namespace, ServerDescription
from .reports import TIME_FORMAT, probe_servers

__all__ = [
    "MAIL",
    "RULES",
    "ReportedServer",
    "ServerReport",
    "build_server_report",
    "render_server_report",
    "unrecognised_halves",
    "unrecognised_servers",
]

RULES = "rules"
MAIL = "mail"

# The folder every IMAP server has (RFC 3501); naming it identifies nobody.
INBOX = "INBOX"

# A name shorter than this is not replaced on its own: a two-letter local
# part or folder would rewrite half the capability names in the report.
SHORTEST_REPLACED = 3

ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

# ############################################################################
# Which servers mailctl recognises
# ############################################################################


# ----------------------------------------------------------------------------
def unrecognised_halves(
    rules: ServerDescription | None, mail: ServerDescription | None
) -> list[str]:
    """The halves, of those described, whose server matched no module."""
    return [
        half
        for half, value in ((RULES, rules), (MAIL, mail))
        if value is not None and value.software is None
    ]


# ----------------------------------------------------------------------------
def unrecognised_servers(session: Session) -> list[str]:
    """The halves the session has whose server mailctl does not recognise.

    Asks each server to describe itself, which reads and changes nothing.
    """
    transport = session.transport
    rules = transport.describe_rules_server() if session.has_rules else None
    mail = transport.describe_mail_server() if session.has_mail else None

    return unrecognised_halves(rules, mail)


# ############################################################################
# The report
# ############################################################################


@dataclass(frozen=True)
class ReportedServer:
    """One half's server as the report shows it, already redacted.

    ``service`` is the provider's name for the half; ``software`` is None
    where mailctl recognised none.
    """

    half: str
    service: str
    software: str | None
    identity: tuple[tuple[str, str], ...]
    capabilities: tuple[Capability, ...]
    after_login: bool


@dataclass(frozen=True)
class ServerReport:
    """Everything the issue body says, every value already redacted.

    * ``unrecognised`` -- the services whose server matched no module.
    * ``rule_sets`` / ``active_rule_set`` -- how many rule sets the account
      has and whether one is active; never their names.
    * ``folders`` -- how many folders; never their names.
    * ``*_word`` -- the provider's nouns the body is written in.
    """

    title: str
    version: str
    provider: str
    taken: str
    unrecognised: tuple[str, ...]
    endpoints: tuple[Fact, ...]
    servers: tuple[ReportedServer, ...]
    extensions: tuple[str, ...]
    rule_sets: int | None
    active_rule_set: bool | None
    delimiter: str | None
    namespaces: tuple[Namespace, ...]
    folders: int | None
    extensions_word: str
    rule_set_word: str
    rule_sets_word: str


# ----------------------------------------------------------------------------
def build_server_report(
    session: Session, config: Config, *, now=None
) -> ServerReport | None:
    """A redacted report of the servers, or None where all are recognised.

    Reads what ``probe_servers`` reads, plus the folder and rule-set
    listings, which are used only to count and to know what to replace.
    ``now`` stands in for the clock.
    """
    record = probe_servers(session, config, now=now)
    unrecognised = unrecognised_halves(record.rules, record.mail)

    if not unrecognised:
        return None

    words = session.wording
    service = {RULES: words.rules_service, MAIL: words.mail_service}
    names: list[str] = []
    rule_sets = folders = None
    active = None

    if record.rules is not None:
        if session.capabilities.rule_sets:
            current, others = session.transport.list_rule_sets()
            names += [current or "", *others]
            active = current is not None
            rule_sets = len(others) + 1 if active else len(others)

        else:
            names.append(record.active_rule_set or "")
            active = record.active_rule_set is not None

    if record.mail is not None:
        listing = session.transport.list_folders()
        names += _folder_names(listing.folders, listing.delimiter)
        folders = len(listing.folders)

    scrub = _scrubber(config, names)
    account = session.dialect.drift_terms().account
    servers = tuple(
        _reported(half, service[half], value, scrub, account)
        for half, value in ((RULES, record.rules), (MAIL, record.mail))
        if value is not None
    )

    return ServerReport(
        title=_title(servers),
        version=__version__,
        provider=record.provider,
        taken=record.taken.strftime(TIME_FORMAT)[:10],
        unrecognised=tuple(service[half] for half in unrecognised),
        endpoints=tuple(
            Fact(scrub(fact.label), scrub(fact.text))
            for fact in record.endpoints
        ),
        servers=servers,
        extensions=tuple(scrub(name) for name in record.extensions),
        rule_sets=rule_sets,
        active_rule_set=active,
        delimiter=record.delimiter,
        namespaces=tuple(
            Namespace(space.kind, scrub(space.prefix), space.delimiter)
            for space in record.namespaces
        ),
        folders=folders,
        extensions_word=words.extensions,
        rule_set_word=words.rule_set,
        rule_sets_word=words.rule_sets,
    )


# ----------------------------------------------------------------------------
def _folder_names(folders: list[str], delimiter: str | None) -> list[str]:
    """Every folder name, and each part of one, except INBOX."""
    found = []

    for folder in folders:
        parts = folder.split(delimiter) if delimiter else [folder]
        found += [folder, *parts]

    return [name for name in found if name.upper() != INBOX]


# ----------------------------------------------------------------------------
def _scrubber(config: Config, names: list[str]):
    """A function replacing everything that identifies the account.

    Longest first, so an address goes before its domain, and without
    regard to case, since servers and users write both either way.
    """
    local, _, domain = config.user.rpartition("@")
    replacements = {
        config.user: "<address>",
        domain: "<domain>",
        local: "<user>",
        config.host: "<host>",
        config.imap_host: "<host>",
    }

    for name in names:
        replacements.setdefault(name, "<name>")

    lowered = {
        key.lower(): value
        for key, value in replacements.items()
        if len(key) >= SHORTEST_REPLACED
    }
    longest_first = sorted(lowered, key=len, reverse=True)
    pattern = (
        re.compile("|".join(map(re.escape, longest_first)), re.IGNORECASE)
        if lowered
        else None
    )

    # The patterns go first: once a domain is replaced, what is left of
    # an address around it no longer looks like one.
    def scrub(value: str) -> str:
        value = IPV4.sub("<ip>", ADDRESS.sub("<address>", value))

        if pattern is None:
            return value

        return pattern.sub(
            lambda match: lowered[match.group(0).lower()], value
        )

    return scrub


# ----------------------------------------------------------------------------
def _reported(
    half: str,
    service: str,
    value: ServerDescription,
    scrub,
    account: frozenset[str],
) -> ReportedServer:
    """One half's description with every value redacted.

    A capability describing the logged-in account keeps its name and
    loses its value, whatever the value looks like.
    """
    return ReportedServer(
        half=half,
        service=service,
        software=value.software,
        identity=tuple(
            (scrub(field), scrub(text)) for field, text in value.identity
        ),
        capabilities=tuple(
            Capability(scrub(item.name), _value(item, scrub, account))
            for item in value.capabilities
        ),
        after_login=value.after_login,
    )


# ----------------------------------------------------------------------------
def _value(item: Capability, scrub, account: frozenset[str]) -> str | None:
    if item.value is None:
        return None

    if item.name.upper() in account:
        return "<redacted>"

    return scrub(item.value)


# ----------------------------------------------------------------------------
def _title(servers: tuple[ReportedServer, ...]) -> str:
    """The issue title: the unrecognised servers, as they name themselves."""
    named = []

    for server in servers:
        if server.software is not None:
            continue

        identity = dict(server.identity)
        name = identity.get("name") or identity.get("implementation")
        named.append(
            f"{server.service} {_line(name)}"
            if name
            else f"{server.service} (no identity)"
        )

    return f"Unrecognised server: {'; '.join(named)}"


# ############################################################################
# The issue body
# ############################################################################


# ----------------------------------------------------------------------------
def render_server_report(report: ServerReport) -> str:
    """The report as a Markdown issue body, ending in a newline.

    Every value sits on its own line in code spans, with whitespace runs
    collapsed, so nothing a server sent can start a line of its own.
    """
    lines = [
        "## Unrecognised server",
        "",
        f"mailctl {_code(report.version)}, provider "
        f"{_code(report.provider)}, does not recognise the "
        f"{' or the '.join(report.unrecognised)} server, so it works with "
        f"the plain protocol there.",
        "",
        f"- Probed: {report.taken}",
    ]

    for fact in report.endpoints:
        lines.append(f"- {_line(fact.label)}: {_code(fact.text)}")

    for server in report.servers:
        stage = "after" if server.after_login else "before"
        lines += [
            "",
            f"### {server.service}",
            "",
            f"- Recognised as: {_code(server.software or 'none')}",
            "- Identity:" if server.identity else "- Identity: none sent",
        ]
        lines += [
            f"  - {_line(field)}: {_code(text)}"
            for field, text in server.identity
        ]
        lines.append(f"- Capabilities, read {stage} login:")
        lines += [
            f"  - {_code(' '.join(filter(None, (item.name, item.value))))}"
            for item in server.capabilities
        ]

        if server.half == RULES:
            lines += _rules_lines(report)

        else:
            lines += _mail_lines(report)

    lines += [
        "",
        "## Left out",
        "",
        "Replaced wherever they appeared: the account's address, its "
        "domain and local part, the configured hosts, folder and "
        f"{report.rule_set_word} names, and anything shaped like an "
        "address or an IPv4 address. An account capability's value is "
        f"redacted. Never read: the password, any {report.rule_set_word}'s "
        "text, and any message.",
    ]

    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------
def _rules_lines(report: ServerReport) -> list[str]:
    lines = []

    if report.extensions:
        lines.append(f"- {report.extensions_word}:")
        lines += [f"  - {_code(name)}" for name in report.extensions]

    if report.active_rule_set is not None:
        active = "yes" if report.active_rule_set else "no"
        lines.append(f"- Active {report.rule_set_word}: {active}")

    if report.rule_sets is not None:
        lines.append(f"- {report.rule_sets_word}: {report.rule_sets}")

    return lines


# ----------------------------------------------------------------------------
def _mail_lines(report: ServerReport) -> list[str]:
    lines = [
        f"- Delimiter: {_code(report.delimiter or '')}",
        "- Namespaces:" if report.namespaces else "- Namespaces: none",
    ]
    lines += [
        f"  - {space.kind}: prefix {_code(space.prefix)}, delimiter "
        f"{_code(space.delimiter or '')}"
        for space in report.namespaces
    ]

    if report.folders is not None:
        lines.append(f"- Folders: {report.folders}")

    return lines


# ----------------------------------------------------------------------------
def _line(value: str) -> str:
    return " ".join(value.split())


# ----------------------------------------------------------------------------
def _code(value: str) -> str:
    """``value`` as one Markdown code span; a backtick cannot close it.

    An empty value is written ``(empty)``, as an empty span does not
    render as one.
    """
    text = _line(value).replace("`", "'")

    return f"`{text}`" if text else "(empty)"
