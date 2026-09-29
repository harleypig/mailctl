"""A saved probe per host, and what a later probe says has changed since.

A baseline records what the servers said on one day (#18); a check
compares a fresh probe with it and says what each difference means for
this account (#19). The baseline **records and never decides**: nothing
branches on it, a check reports drift and refuses nothing, and a baseline
is only ever replaced by an explicit save that shows what changed first.

One file per host -- the ``host`` setting, which names the account's
server -- under ``config.baseline_dir()``, written ``0600`` like a backup.
What describes the server is kept once; what describes one account on it
(the active rule set, and any capability the provider says names the
account) is kept per account, keyed by the ``user`` setting. Both hosts a
provider connects to are in the stored endpoints, so a baseline taken
against a different IMAP host shows as drift rather than passing
unnoticed.

Saving writes a local file, never the server: ``plan_save_baseline`` reads
the servers and the file, ``execute_save_baseline`` writes the file.
"""

import difflib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from .. import MailctlError
from ..config import Config, baseline_dir
from ..engine import Session
from ..providers.base import (
    Capability,
    DriftTerms,
    ProbeRecord,
    ServerDescription,
)
from .backup_files import write_private
from .reports import (
    TIME_FORMAT,
    dump_document,
    load_probe,
    probe_document,
    probe_servers,
)

__all__ = [
    "BASELINE_VERSION",
    "INFO",
    "SERIOUS",
    "AccountRecord",
    "Baseline",
    "BaselineCheck",
    "BaselineSavePlan",
    "Drift",
    "baseline_path",
    "check_baseline",
    "compare_probes",
    "execute_save_baseline",
    "find_baseline",
    "plan_save_baseline",
    "read_baseline",
]

# The version of the baseline file around the probe document, which
# carries its own. Raise it when a key of this file changes meaning.
BASELINE_VERSION = 1

SERIOUS = "serious"
INFO = "info"

# The kinds of drift, in the order a report lists them within a severity.
ENDPOINT = "endpoint"
HALF = "half"
IDENTITY = "identity"
STAGE = "stage"
EXTENSION_REMOVED = "extension-removed"
EXTENSION_ADDED = "extension-added"
CAPABILITY_REMOVED = "capability-removed"
CAPABILITY_ADDED = "capability-added"
CAPABILITY_CHANGED = "capability-changed"
DELIMITER = "delimiter"
NAMESPACE = "namespace"
ACTIVE_RULE_SET = "active-rule-set"

KINDS = (
    ACTIVE_RULE_SET,
    DELIMITER,
    EXTENSION_REMOVED,
    NAMESPACE,
    CAPABILITY_REMOVED,
    CAPABILITY_ADDED,
    CAPABILITY_CHANGED,
    EXTENSION_ADDED,
    IDENTITY,
    STAGE,
    HALF,
    ENDPOINT,
)

RULES = "rules"
MAIL = "mail"

# A host name as a file name: letters, digits, dots, and hyphens, which
# is every DNS name and nothing that could leave the directory.
HOST_NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")

# ############################################################################
# Records
# ############################################################################


@dataclass(frozen=True)
class AccountRecord:
    """What one account's probe said that the server's part does not."""

    taken: datetime
    active_rule_set: str | None
    rules_capabilities: tuple[Capability, ...] = ()
    mail_capabilities: tuple[Capability, ...] = ()


@dataclass(frozen=True)
class Baseline:
    """A baseline file, read and checked.

    ``server`` has no active rule set and none of the account's
    capabilities; :meth:`record_for` puts one account's back.
    """

    path: Path
    host: str
    server: ProbeRecord
    accounts: dict[str, AccountRecord]
    text: str

    # ------------------------------------------------------------------------
    def record_for(self, user: str) -> ProbeRecord:
        """The probe as taken for ``user``; the server's part alone when
        that account was never saved."""
        account = self.accounts.get(user)

        if account is None:
            return self.server

        return _join(self.server, account)


@dataclass(frozen=True)
class Drift:
    """One difference between a baseline and a fresh probe.

    ``kind`` is one of :data:`KINDS`; ``half`` is ``"rules"``, ``"mail"``,
    or None where the difference is about neither; ``name`` is what
    changed (an extension, a capability, an identity field, a namespace
    kind, an endpoint label) or ``""`` where the kind says it all.
    ``before`` and ``after`` are the values, None where there was none.
    """

    severity: str
    kind: str
    half: str | None
    name: str
    before: object = None
    after: object = None


@dataclass(frozen=True)
class BaselineSavePlan:
    """A probe about to be saved, and the baseline it would replace.

    ``previous`` is None for a first save. ``diff`` is the unified diff of
    the file, empty for a first save; ``drift`` is what that difference
    means, empty for a first save. ``requires_known`` is False where the
    active rule set would not parse, so every lost extension was counted
    as one it needs.
    """

    path: Path
    host: str
    record: ProbeRecord
    previous: Baseline | None
    text: str
    diff: list[str]
    drift: list[Drift]
    requires_known: bool = True


@dataclass(frozen=True)
class BaselineCheck:
    """A fresh probe compared with the saved one.

    ``account_recorded`` is False where the baseline holds no part for
    this account, so the active rule set was not compared.
    ``requires_known`` is False where the active rule set would not parse,
    so every lost extension was taken to be one it needs. ``stored`` is
    the probe compared with, as saved for this account; ``record`` the one
    taken now.
    """

    baseline: Baseline
    stored: ProbeRecord
    record: ProbeRecord
    account_recorded: bool
    requires_known: bool
    drift: list[Drift]

    # ------------------------------------------------------------------------
    @property
    def serious(self) -> list[Drift]:
        return [item for item in self.drift if item.severity == SERIOUS]


# ############################################################################
# Where, and reading one
# ############################################################################


# ----------------------------------------------------------------------------
def baseline_path(config: Config, directory: Path | None = None) -> Path:
    """The file the baseline for ``config``'s host lives in."""
    host = (config.host or "").strip().lower()

    if not host:
        raise MailctlError(
            "no host is configured, so there is no baseline to name; set "
            "--host or MAILCTL_HOST"
        )

    if not HOST_NAME.fullmatch(host) or ".." in host:
        raise MailctlError(
            f"host {config.host!r} cannot name a baseline file: only "
            f"letters, digits, dots, and hyphens are used"
        )

    return (directory or baseline_dir()) / f"{host}.json"


# ----------------------------------------------------------------------------
def find_baseline(
    config: Config, directory: Path | None = None
) -> Path | None:
    """The baseline file for ``config``'s host, if one has been saved."""
    path = baseline_path(config, directory)

    return path if path.exists() else None


# ----------------------------------------------------------------------------
def read_baseline(path: Path) -> Baseline | None:
    """Read and check the baseline at ``path``; None where there is none.

    A file that is not a baseline this mailctl reads -- not JSON, another
    version, a key missing or mistyped -- is refused naming the file, never
    read as something it is not and never overwritten unasked.
    """
    try:
        text = path.read_text(encoding="utf-8")

    except FileNotFoundError:
        return None

    except (OSError, UnicodeDecodeError) as exc:
        raise MailctlError(f"could not read baseline {path}: {exc}") from exc

    try:
        document = json.loads(text)

    except ValueError as exc:
        raise _unreadable(path, f"it is not JSON ({exc})") from exc

    if not isinstance(document, dict):
        raise _unreadable(path, "it is not a JSON object")

    version = document.get("version")

    if version != BASELINE_VERSION:
        raise _unreadable(
            path,
            f"it is version {version!r}, and this mailctl reads version "
            f"{BASELINE_VERSION}",
        )

    try:
        host = document["host"]

        if not isinstance(host, str) or f"{host}.json" != path.name:
            raise ValueError(f"its host {host!r} is not the one it is for")

        server = document["server"]

        if not isinstance(server, dict) or not isinstance(
            server.get("rules"), dict | None
        ):
            raise TypeError("its server part is not a probe document")

        if server["rules"] is not None:
            server = {
                **server,
                "rules": {**server["rules"], "active_rule_set": None},
            }

        record = load_probe(server)
        accounts = {
            _typed_key(user): _load_account(value)
            for user, value in _typed_dict(document["accounts"]).items()
        }

    except MailctlError as exc:
        raise _unreadable(path, str(exc)) from exc

    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise _unreadable(path, f"{type(exc).__name__}: {exc}") from exc

    return Baseline(path, host, record, accounts, text)


# ----------------------------------------------------------------------------
def _unreadable(path: Path, why: str) -> MailctlError:
    return MailctlError(
        f"the baseline {path} cannot be read: {why}. It was left as it is; "
        f"move it aside and run 'mailctl save-baseline' to take a new one."
    )


# ----------------------------------------------------------------------------
def _typed_dict(value: object) -> dict:
    if not isinstance(value, dict):
        raise TypeError(f"expected an object, got {type(value).__name__}")

    return value


# ----------------------------------------------------------------------------
def _typed_key(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError("an account is not named")

    return value


# ----------------------------------------------------------------------------
def _load_account(value: object) -> AccountRecord:
    section = _typed_dict(value)
    active = section["active_rule_set"]

    if active is not None and not isinstance(active, str):
        raise TypeError("an active rule set is not a string")

    return AccountRecord(
        taken=_parse_time(section["taken"]),
        active_rule_set=active,
        rules_capabilities=_load_capabilities(section["rules_capabilities"]),
        mail_capabilities=_load_capabilities(section["mail_capabilities"]),
    )


# ----------------------------------------------------------------------------
def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("a time is not a string")

    return datetime.strptime(value, TIME_FORMAT).replace(tzinfo=UTC)


# ----------------------------------------------------------------------------
def _load_capabilities(value: object) -> tuple[Capability, ...]:
    if not isinstance(value, list):
        raise TypeError("a capability list is not a list")

    found = []

    for item in value:
        name, text = _typed_dict(item)["name"], item["value"]

        if not isinstance(name, str) or not isinstance(text, str | None):
            raise TypeError("a capability is not a name and a value")

        found.append(Capability(name, text))

    return tuple(found)


# ############################################################################
# Saving
# ############################################################################


# ----------------------------------------------------------------------------
def plan_save_baseline(
    session: Session,
    config: Config,
    directory: Path | None = None,
    *,
    now: datetime | None = None,
) -> BaselineSavePlan:
    """Probe the servers and work out the baseline file that would result.

    Reads the servers, the active rule set (to say which lost extensions
    it needs), and the existing file; writes nothing. A file that cannot
    be read is refused here, so it is never replaced unseen. The other
    accounts saved for the same host are kept as they are.
    """
    path = baseline_path(config, directory)
    previous = read_baseline(path)
    record = probe_servers(session, config, now=now)
    terms = session.dialect.drift_terms()
    host = path.stem
    server, account = _split(record, terms)
    accounts = dict(previous.accounts) if previous else {}
    accounts[config.user] = account
    text = _render(host, server, accounts)

    if previous is None:
        return BaselineSavePlan(path, host, record, None, text, [], [])

    diff = list(
        difflib.unified_diff(
            previous.text.splitlines(),
            text.splitlines(),
            f"{path.name} (saved)",
            f"{path.name} (now)",
            lineterm="",
        )
    )
    requires = _active_requires(session, record)
    drift = compare_probes(
        previous.record_for(config.user),
        record,
        requires,
        terms,
        account_recorded=config.user in previous.accounts,
    )

    return BaselineSavePlan(
        path, host, record, previous, text, diff, drift, requires is not None
    )


# ----------------------------------------------------------------------------
def execute_save_baseline(plan: BaselineSavePlan) -> Path:
    """Write the planned baseline file, ``0600`` in a ``0700`` directory."""
    return write_private(plan.text, plan.path, "baseline")


# ----------------------------------------------------------------------------
def _split(
    record: ProbeRecord, terms: DriftTerms
) -> tuple[ProbeRecord, AccountRecord]:
    """The server's part of a probe, and the account's."""
    rules, rules_own = _without_account(record.rules, terms)
    mail, mail_own = _without_account(record.mail, terms)
    server = replace(record, rules=rules, mail=mail, active_rule_set=None)

    return server, AccountRecord(
        record.taken, record.active_rule_set, rules_own, mail_own
    )


# ----------------------------------------------------------------------------
def _without_account(
    value: ServerDescription | None, terms: DriftTerms
) -> tuple[ServerDescription | None, tuple[Capability, ...]]:
    if value is None:
        return None, ()

    own = tuple(
        item
        for item in value.capabilities
        if item.name.upper() in terms.account
    )
    rest = tuple(item for item in value.capabilities if item not in own)

    return replace(value, capabilities=rest), own


# ----------------------------------------------------------------------------
def _join(server: ProbeRecord, account: AccountRecord) -> ProbeRecord:
    """A server's part with one account's put back."""

    def with_own(value, own):
        if value is None:
            return None

        return replace(
            value,
            capabilities=tuple(
                sorted(
                    value.capabilities + own,
                    key=lambda item: (item.name.upper(), item.value or ""),
                )
            ),
        )

    return replace(
        server,
        rules=with_own(server.rules, account.rules_capabilities),
        mail=with_own(server.mail, account.mail_capabilities),
        active_rule_set=account.active_rule_set,
    )


# ----------------------------------------------------------------------------
def _render(
    host: str, server: ProbeRecord, accounts: dict[str, AccountRecord]
) -> str:
    """The baseline file's text: the server once, then each account."""
    document = probe_document(server)

    if document["rules"] is not None:
        del document["rules"]["active_rule_set"]

    return dump_document(
        {
            "version": BASELINE_VERSION,
            "host": host,
            "server": document,
            "accounts": {
                user: {
                    "taken": account.taken.strftime(TIME_FORMAT),
                    "active_rule_set": account.active_rule_set,
                    "rules_capabilities": _capability_list(
                        account.rules_capabilities
                    ),
                    "mail_capabilities": _capability_list(
                        account.mail_capabilities
                    ),
                }
                for user, account in sorted(accounts.items())
            },
        }
    )


# ----------------------------------------------------------------------------
def _capability_list(items: tuple[Capability, ...]) -> list[dict]:
    return [{"name": item.name, "value": item.value} for item in items]


# ############################################################################
# Checking
# ############################################################################


# ----------------------------------------------------------------------------
def check_baseline(
    session: Session,
    config: Config,
    directory: Path | None = None,
    *,
    now: datetime | None = None,
) -> BaselineCheck:
    """Probe the servers and compare what they say with the baseline.

    Read-only. The active rule set is read so a lost extension it
    ``require``s can be told from one it does not use; where it will not
    parse, every lost extension is treated as one it might need.
    """
    path = baseline_path(config, directory)
    baseline = read_baseline(path)

    if baseline is None:
        raise MailctlError(
            f"no baseline has been saved for {path.stem} (looked for "
            f"{path}); 'mailctl save-baseline' records one"
        )

    record = probe_servers(session, config, now=now)

    if record.provider != baseline.server.provider:
        raise MailctlError(
            f"the baseline {path} was taken through provider "
            f"{baseline.server.provider!r}, and this run uses "
            f"{record.provider!r}; the two do not describe the same thing. "
            f"'mailctl save-baseline' replaces it."
        )

    requires = _active_requires(session, record)
    recorded = config.user in baseline.accounts
    stored = baseline.record_for(config.user)
    drift = compare_probes(
        stored,
        record,
        requires,
        session.dialect.drift_terms(),
        account_recorded=recorded,
    )

    return BaselineCheck(
        baseline, stored, record, recorded, requires is not None, drift
    )


# ----------------------------------------------------------------------------
def _active_requires(
    session: Session, record: ProbeRecord
) -> frozenset[str] | None:
    """What the active rule set requires, lower-cased; None if unknown."""
    if not record.active_rule_set or not session.capabilities.extensions:
        return frozenset()

    source = session.transport.read_rule_set(record.active_rule_set)

    try:
        return frozenset(
            name.lower() for name in session.dialect.rule_set_requires(source)
        )

    except MailctlError:
        return None


# ----------------------------------------------------------------------------
def compare_probes(
    before: ProbeRecord,
    after: ProbeRecord,
    requires: frozenset[str] | None,
    terms: DriftTerms,
    *,
    account_recorded: bool = True,
) -> list[Drift]:
    """Every difference between two probes, and how much each matters.

    Pure. ``requires`` is what the active rule set declares it needs,
    lower-cased; None means it could not be read, so losing any extension
    is serious. ``account_recorded`` False skips the active rule set,
    which ``before`` then does not hold. Serious drift comes first.

    Serious: an extension the active rule set requires is gone; the
    folder delimiter or the personal namespace changed; a mail capability
    the provider relies on came or went; the active rule set is another
    one. Everything else is informational.
    """
    found: list[Drift] = []

    found += _endpoints(before, after)
    found += _half(RULES, before.rules, after.rules, terms, set())
    found += _half(MAIL, before.mail, after.mail, terms, terms.relied)

    if before.rules is not None and after.rules is not None:
        found += _extensions(before.extensions, after.extensions, requires)

        if account_recorded and before.active_rule_set != (
            after.active_rule_set
        ):
            found.append(
                Drift(
                    SERIOUS,
                    ACTIVE_RULE_SET,
                    RULES,
                    "",
                    before.active_rule_set,
                    after.active_rule_set,
                )
            )

    if before.mail is not None and after.mail is not None:
        if before.delimiter != after.delimiter:
            found.append(
                Drift(
                    SERIOUS,
                    DELIMITER,
                    MAIL,
                    "",
                    before.delimiter,
                    after.delimiter,
                )
            )

        found += _namespaces(before, after)

    return sorted(
        found,
        key=lambda item: (
            item.severity != SERIOUS,
            KINDS.index(item.kind),
            item.half or "",
            item.name.upper(),
        ),
    )


# ----------------------------------------------------------------------------
def _endpoints(before: ProbeRecord, after: ProbeRecord) -> list[Drift]:
    was = {fact.label: fact.text for fact in before.endpoints}
    now = {fact.label: fact.text for fact in after.endpoints}

    return [
        Drift(INFO, ENDPOINT, None, label, was.get(label), now.get(label))
        for label in sorted(was.keys() | now.keys())
        if was.get(label) != now.get(label)
    ]


# ----------------------------------------------------------------------------
def _half(
    half: str,
    before: ServerDescription | None,
    after: ServerDescription | None,
    terms: DriftTerms,
    relied: frozenset[str] | set[str],
) -> list[Drift]:
    """Identity and capability changes on one half."""
    if before is None and after is None:
        return []

    if before is None or after is None:
        return [
            Drift(
                INFO,
                HALF,
                half,
                "",
                before is not None,
                after is not None,
            )
        ]

    found = _identity(half, before, after)

    # Two lists read at different stages of a login may differ with the
    # server unchanged, so they are not compared, and that is said.
    if before.after_login != after.after_login:
        return [
            *found,
            Drift(
                INFO, STAGE, half, "", before.after_login, after.after_login
            ),
        ]

    skipped = terms.account | terms.carriers
    was = _capabilities(before, skipped)
    now = _capabilities(after, skipped)

    for key in sorted(was.keys() | now.keys()):
        old, new = was.get(key), now.get(key)
        severity = SERIOUS if key in relied else INFO

        if new is None:
            found.append(
                Drift(severity, CAPABILITY_REMOVED, half, old.name, old.value)
            )

        elif old is None:
            found.append(
                Drift(
                    severity,
                    CAPABILITY_ADDED,
                    half,
                    new.name,
                    None,
                    new.value,
                )
            )

        elif old.value != new.value:
            found.append(
                Drift(
                    severity,
                    CAPABILITY_CHANGED,
                    half,
                    new.name,
                    old.value,
                    new.value,
                )
            )

    return found


# ----------------------------------------------------------------------------
def _identity(
    half: str, before: ServerDescription, after: ServerDescription
) -> list[Drift]:
    was, now = dict(before.identity), dict(after.identity)

    return [
        Drift(INFO, IDENTITY, half, field, was.get(field), now.get(field))
        for field in sorted(was.keys() | now.keys())
        if was.get(field) != now.get(field)
    ]


# ----------------------------------------------------------------------------
def _capabilities(
    value: ServerDescription, skipped: frozenset[str]
) -> dict[str, Capability]:
    """Capabilities by upper-cased name, those in ``skipped`` left out."""
    return {
        item.name.upper(): item
        for item in value.capabilities
        if item.name.upper() not in skipped
    }


# ----------------------------------------------------------------------------
def _extensions(
    before: tuple[str, ...],
    after: tuple[str, ...],
    requires: frozenset[str] | None,
) -> list[Drift]:
    was = {name.lower(): name for name in before}
    now = {name.lower(): name for name in after}
    found = []

    for key in sorted(was.keys() - now.keys()):
        needed = requires is None or key in requires
        found.append(
            Drift(
                SERIOUS if needed else INFO,
                EXTENSION_REMOVED,
                RULES,
                was[key],
                was[key],
            )
        )

    for key in sorted(now.keys() - was.keys()):
        found.append(
            Drift(INFO, EXTENSION_ADDED, RULES, now[key], None, now[key])
        )

    return found


# ----------------------------------------------------------------------------
def _namespaces(before: ProbeRecord, after: ProbeRecord) -> list[Drift]:
    """One drift per namespace kind whose entries changed.

    The personal namespace is where a new folder goes, so a change there
    is serious; the others mailctl does not use.
    """
    found = []

    for kind in sorted(
        {space.kind for space in before.namespaces}
        | {space.kind for space in after.namespaces}
    ):
        was = [
            [space.prefix, space.delimiter]
            for space in before.namespaces
            if space.kind == kind
        ]
        now = [
            [space.prefix, space.delimiter]
            for space in after.namespaces
            if space.kind == kind
        ]

        if was != now:
            found.append(
                Drift(
                    SERIOUS if kind == "personal" else INFO,
                    NAMESPACE,
                    MAIL,
                    kind,
                    was,
                    now,
                )
            )

    return found
