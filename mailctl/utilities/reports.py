"""What the account and the configured provider say about themselves.

Read-only: the probes behind ``mailctl test`` and ``mailctl probe``, the
provider's own wording and connection facts, and the state of each Sieve
extension.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .. import MailctlError
from ..config import Config
from ..engine import Session
from ..providers.base import (
    NAMESPACE_KINDS,
    Capability,
    ExtensionState,
    Fact,
    Namespace,
    ProbeRecord,
    ServerDescription,
    Wording,
)
from ..providers.registry import provider_for
from .folders import list_folders

# The version of the document ``dump_probe`` writes. Raise it when a key
# changes meaning or goes away; a stored baseline says which it was.
PROBE_VERSION = 1

# How a probe document writes a time: UTC, to the second.
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# ############################################################################
# Probing the account
# ############################################################################


@dataclass(frozen=True)
class RulesProbe:
    """What the rule half says about itself, and its rule sets."""

    capabilities: list[str]
    active: str | None
    others: list[str]


@dataclass(frozen=True)
class MailProbe:
    """What the mail half says about itself, and its folder shape.

    ``facts`` is what its advertised capabilities mean for mailctl, in the
    provider's words.
    """

    capabilities: list[str]
    delimiter: str
    folder_count: int
    unsubscribed: list[str] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)


# ----------------------------------------------------------------------------
def probe_rules(session: Session) -> RulesProbe:
    """Read what the rule half advertises, and its rule-set listing."""
    capabilities = session.transport.rules_capabilities()
    active, others = session.transport.list_rule_sets()

    return RulesProbe(capabilities, active, others)


# ----------------------------------------------------------------------------
def probe_mail(session: Session) -> MailProbe:
    """Read what the mail half advertises, and its folder shape."""
    listing = list_folders(session)
    capabilities = session.transport.mail_capabilities()

    return MailProbe(
        capabilities,
        listing.delimiter,
        len(listing.folders),
        listing.unsubscribed,
        session.dialect.mail_facts(capabilities),
    )


# ----------------------------------------------------------------------------
def probe_servers(
    session: Session, config: Config, *, now: datetime | None = None
) -> ProbeRecord:
    """Read everything a provider's record observes, and change nothing.

    Each half the session has is asked what its server is and advertises;
    the rule half also for its extensions and the active rule set, the
    mail half for its delimiter and namespaces. Every list is sorted, so
    two probes of an unchanged server differ only in when they were taken.
    ``now`` stands in for the clock.
    """
    taken = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    transport = session.transport
    rules = mail = None
    extensions: tuple[str, ...] = ()
    active = delimiter = None
    namespaces: tuple = ()

    if session.has_rules:
        rules = _sorted_description(transport.describe_rules_server())
        active = transport.active_rule_set()

        if session.capabilities.extensions:
            extensions = tuple(sorted(transport.rules_capabilities()))

    if session.has_mail:
        mail = _sorted_description(transport.describe_mail_server())
        delimiter = transport.list_folders().delimiter
        namespaces = tuple(
            sorted(
                transport.mail_namespaces(),
                key=lambda space: (
                    NAMESPACE_KINDS.index(space.kind),
                    space.prefix,
                ),
            )
        )

    return ProbeRecord(
        taken=taken,
        provider=session.name,
        endpoints=tuple(session.dialect.connection_facts(config)),
        rules=rules,
        extensions=extensions,
        active_rule_set=active,
        mail=mail,
        delimiter=delimiter,
        namespaces=namespaces,
    )


# ----------------------------------------------------------------------------
def _sorted_description(value: ServerDescription) -> ServerDescription:
    """``value`` with its identity and capabilities in a stable order."""
    return ServerDescription(
        tuple(sorted(value.identity)),
        tuple(
            sorted(
                value.capabilities,
                key=lambda item: (item.name.upper(), item.value or ""),
            )
        ),
        value.after_login,
    )


# ----------------------------------------------------------------------------
def dump_probe(record: ProbeRecord) -> str:
    """A probe as a versioned JSON document, laid out for diffing.

    One value per line, keys in a fixed order, lists as sorted in the
    record. Only ``taken`` and the ``endpoints`` (the hosts configured)
    vary between two probes of an unchanged server.
    """
    return dump_document(probe_document(record))


# ----------------------------------------------------------------------------
def dump_document(document: dict) -> str:
    """``document`` as JSON text, one value per line, ending in a newline."""
    # What a server sends may hold anything. ensure_ascii (the default)
    # escapes everything outside printable ASCII, which leaves nothing a
    # terminal would act on; do not turn it off.
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"


# ----------------------------------------------------------------------------
def probe_document(record: ProbeRecord) -> dict:
    """A probe as the plain data ``dump_probe`` writes out."""
    document = {
        "version": PROBE_VERSION,
        "taken": record.taken.strftime(TIME_FORMAT),
        "provider": record.provider,
        "endpoints": [
            {"label": fact.label, "value": fact.text}
            for fact in record.endpoints
        ],
        "rules": None,
        "mail": None,
    }

    if record.rules is not None:
        document["rules"] = {
            **_server_document(record.rules),
            "extensions": list(record.extensions),
            "active_rule_set": record.active_rule_set,
        }

    if record.mail is not None:
        document["mail"] = {
            **_server_document(record.mail),
            "delimiter": record.delimiter,
            "namespaces": [
                {
                    "kind": space.kind,
                    "prefix": space.prefix,
                    "delimiter": space.delimiter,
                }
                for space in record.namespaces
            ],
        }

    return document


# ----------------------------------------------------------------------------
def _server_document(value: ServerDescription) -> dict:
    return {
        "identity": dict(value.identity),
        "capabilities_after_login": value.after_login,
        "capabilities": [
            {"name": item.name, "value": item.value}
            for item in value.capabilities
        ],
    }


# ----------------------------------------------------------------------------
def load_probe(document: object) -> ProbeRecord:
    """Read a document ``probe_document`` wrote back into a record.

    Strict: another version, a missing key, or a value of the wrong type
    is refused with ``MailctlError`` saying which, rather than read as
    something it is not.
    """
    if not isinstance(document, dict):
        raise MailctlError("not a probe document: not a JSON object")

    version = document.get("version")

    if version != PROBE_VERSION:
        raise MailctlError(
            f"probe document version {version!r}; this mailctl reads "
            f"version {PROBE_VERSION}"
        )

    try:
        return _load_probe(document)

    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise MailctlError(
            f"not a probe document: {type(exc).__name__}: {exc}"
        ) from exc


# ----------------------------------------------------------------------------
def _load_probe(document: dict) -> ProbeRecord:
    taken = datetime.strptime(
        _typed(document["taken"], str), TIME_FORMAT
    ).replace(tzinfo=UTC)
    rules = mail = None
    extensions: tuple[str, ...] = ()
    active = delimiter = None
    namespaces: tuple[Namespace, ...] = ()

    if document["rules"] is not None:
        section = _typed(document["rules"], dict)
        rules = _load_server(section)
        extensions = tuple(
            _typed(name, str) for name in _typed(section["extensions"], list)
        )
        active = _typed(section["active_rule_set"], str, None)

    if document["mail"] is not None:
        section = _typed(document["mail"], dict)
        mail = _load_server(section)
        delimiter = _typed(section["delimiter"], str, None)
        namespaces = tuple(
            Namespace(
                _kind(space["kind"]),
                _typed(space["prefix"], str),
                _typed(space["delimiter"], str, None),
            )
            for space in _typed(section["namespaces"], list)
        )

    return ProbeRecord(
        taken=taken,
        provider=_typed(document["provider"], str),
        endpoints=tuple(
            Fact(_typed(item["label"], str), _typed(item["value"], str))
            for item in _typed(document["endpoints"], list)
        ),
        rules=rules,
        extensions=extensions,
        active_rule_set=active,
        mail=mail,
        delimiter=delimiter,
        namespaces=namespaces,
    )


# ----------------------------------------------------------------------------
def _load_server(section: dict) -> ServerDescription:
    identity = _typed(section["identity"], dict)

    return ServerDescription(
        tuple(
            (_typed(name, str), _typed(value, str))
            for name, value in identity.items()
        ),
        tuple(
            Capability(
                _typed(item["name"], str), _typed(item["value"], str, None)
            )
            for item in _typed(section["capabilities"], list)
        ),
        _typed(section["capabilities_after_login"], bool),
    )


# ----------------------------------------------------------------------------
def _kind(value: object) -> str:
    if value not in NAMESPACE_KINDS:
        raise ValueError(f"unknown namespace kind {value!r}")

    return value


# ----------------------------------------------------------------------------
def _typed(value, kind: type, *others):
    """``value``, if it is a ``kind`` or one of ``others`` (None allowed)."""
    if isinstance(value, kind) or any(value is other for other in others):
        return value

    raise TypeError(f"expected {kind.__name__}, got {type(value).__name__}")


# ----------------------------------------------------------------------------
def wording(config: Config) -> Wording:
    """The words the configured provider's host is described in.

    Needs no connection. A front-end fills its report from this rather
    than writing one host's names and policies into itself.
    """
    return provider_for(config).wording


# ----------------------------------------------------------------------------
def connection_facts(config: Config) -> list[Fact]:
    """Where the configured provider connects, as ``config`` resolves it."""
    return provider_for(config).dialect.connection_facts(config)


# ############################################################################
# Extensions -- what the server advertises, less what is disabled
# ############################################################################


# ----------------------------------------------------------------------------
def report_extensions(
    session: Session, probe: RulesProbe, config: Config
) -> list[ExtensionState]:
    """One state per extension mailctl knows or the server lists, by name.

    Empty for a provider that does not declare ``extensions``.
    """
    if not session.capabilities.extensions:
        return []

    return session.dialect.report_extensions(probe.capabilities, config)
