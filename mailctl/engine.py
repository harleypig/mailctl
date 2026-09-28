"""The engine: every piece of work mailctl does, for any front-end.

A front-end -- the CLI today -- turns what a person asked for into the
plain values below, calls the engine, and decides what to show and whether
to go on. The engine never learns how it was called: it takes no parsed
arguments, never prints or prompts, and reports through return values,
exceptions (``MailctlError``), and two optional callbacks -- ``progress``
for protocol chatter and ``on_event`` for the steps of a change as they
happen.

Every change is split the same way. A ``plan_*`` function is read-only and
returns what would change; the front-end renders it and decides; an
``execute``-style function carries out the plan it was handed. A dry run is
a plan that is never executed.

The engine never touches a protocol. It works against one ``Provider``
(``mailctl.providers.base``), chosen by the ``provider`` setting, in the
provider-neutral model: criteria, an ``ActionSpec``, folders, messages.
The provider translates that into its host's terms and back. What a host
can do is read from its declared capabilities; the engine never asks which
provider it has.
"""

import email.utils
import os
import re
import stat
import tomllib
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from pathlib import Path

from . import MailctlError
from .config import (
    Config,
    LegacySetting,
    Source,
    config_dir,
    expand_path,
    legacy_config_dir,
)
from .criteria import Criteria

# The PLACE_* names are re-exported (``X as X``): a front-end builds and
# renders the neutral model through the engine alone, never importing a
# provider or a component.
from .providers.base import (
    PLACE_AFTER as PLACE_AFTER,
)
from .providers.base import (
    PLACE_BEFORE as PLACE_BEFORE,
)
from .providers.base import (
    PLACE_FIRST as PLACE_FIRST,
)
from .providers.base import (
    PLACE_LAST as PLACE_LAST,
)
from .providers.base import (
    ActionSpec,
    DisplayDiff,
    ExtensionState,
    FolderCreation,
    FolderListing,
    MailActionPlan,
    MailActionResult,
    MessageSummary,
    Placement,
    Progress,
    Provider,
    action_names,
    decode_header_value,
    refuse,
    same_folder,
    validate_specifics,
)
from .providers.registry import provider_for
from .rules import Analysis, Rule, Shadow, analyze_placement, audit

DEFAULT_SCRIPT_NAME = "mailctl"

# The script an account got under the tool's old name. It is still ours: an
# account holding it, with nothing active, has it reused rather than a
# second script created beside it.
LEGACY_SCRIPT_NAME = "mxfilter"
DEFAULT_MAX_MESSAGES = 500

# Folder plan outcomes.
FOLDER_NONE = "none"
FOLDER_EXISTS = "exists"
FOLDER_MISSING = "missing"
FOLDER_SIEVE_CREATES = "sieve-creates"
FOLDER_IMAP_CREATE = "imap-create"
FOLDER_BOTH_CREATE = "imap-and-sieve-create"
FOLDER_UNCREATABLE = "uncreatable"


# ############################################################################
# Inputs
# ############################################################################


@dataclass(frozen=True)
class RuleRequest:
    """A rule to merge into the account's rule set.

    ``script``, ``activate``, and ``placement`` need a provider declaring
    ``rule_sets`` and ``ordering``. ``specifics`` carries provider-only
    parameters under namespaced keys, checked against the provider's
    schema before any network work (:func:`check_rule`).
    """

    criteria: Criteria
    actions: ActionSpec
    name: str | None = None
    script: str | None = None
    replace: bool = False
    placement: Placement | None = None
    activate: bool = False
    specifics: Mapping[str, object] = field(default_factory=dict)


# ############################################################################
# Events -- the steps of a change, reported as they happen
# ############################################################################


@dataclass(frozen=True)
class ScriptBackedUp:
    """The server's copy was saved; sent before anything is uploaded."""

    script: str
    path: Path


@dataclass(frozen=True)
class ScriptUploaded:
    """The new script was validated and uploaded, and maybe activated."""

    script: str
    activated: bool


@dataclass(frozen=True)
class FolderCreated:
    """A planned folder was created over IMAP, on execute."""

    result: FolderCreation


EventSink = Callable[[object], None]


# ############################################################################
# The provider
# ############################################################################


# ----------------------------------------------------------------------------
@contextmanager
def connect(
    config: Config,
    *,
    rules: bool = True,
    mail: bool = False,
    progress: Progress | None = None,
) -> Iterator[Provider]:
    """Open the requested halves of the configured provider, then close them.

    ``rules`` is the half that stores rules, ``mail`` the half that holds
    messages and folders. The configuration is validated first, so an
    unknown provider or a setting it refuses costs no connection.
    """
    provider = provider_for(config)
    provider.validate(config)

    with provider.open(
        config, rules=rules, mail=mail, progress=progress
    ) as live:
        yield live


# ----------------------------------------------------------------------------
def require_capability(provider: Provider | type[Provider], name: str) -> None:
    """Refuse work needing a capability the provider does not declare."""
    if getattr(provider.capabilities, name):
        return

    raise refuse(
        provider.name,
        CAPABILITY_CONSTRUCTS[name],
        f"it does not declare the {name!r} capability",
    )


# What a request asks for, in words, when it needs each capability.
CAPABILITY_CONSTRUCTS = {
    "ordering": "place a rule at a position in evaluation order",
    "rule_sets": "name or activate one of several rule sets",
    "stop": "end evaluation after a rule",
}


# ############################################################################
# Reading the account
# ############################################################################


@dataclass(frozen=True)
class ScriptText:
    """One script's source, exactly as the server holds it.

    ``provider`` reads it; it is not part of the value.
    """

    name: str
    source: str
    provider: Provider | type[Provider] = field(compare=False, repr=False)

    # ------------------------------------------------------------------------
    def rule_names(self) -> list[str]:
        """Parse on demand, so an unparseable script can still be shown."""
        return self.provider.rule_names(self.source)


@dataclass(frozen=True)
class RulesReport:
    """A script's rules in evaluation order, and which cannot fire."""

    script: str
    rules: list[Rule]
    findings: list[Shadow]


@dataclass(frozen=True)
class SieveProbe:
    """What the ManageSieve server says about itself."""

    capabilities: list[str]
    active: str | None
    others: list[str]


@dataclass(frozen=True)
class ImapProbe:
    """What the IMAP server says about itself."""

    capabilities: list[str]
    delimiter: str
    folder_count: int
    unsubscribed: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------------
    @property
    def has_move(self) -> bool:
        return "MOVE" in self.capabilities

    # ------------------------------------------------------------------------
    @property
    def has_uidplus(self) -> bool:
        return "UIDPLUS" in self.capabilities

    # ------------------------------------------------------------------------
    @property
    def has_filter_sieve(self) -> bool:
        """Whether Dovecot's ``imap_filter_sieve`` is enabled.

        Detection only: mailctl has no FILTER=SIEVE code path.
        """
        return any(
            item.upper().startswith("FILTER=SIEVE")
            for item in self.capabilities
        )


# ----------------------------------------------------------------------------
def list_scripts(provider: Provider) -> tuple[str | None, list[str]]:
    """Return ``(active, others)``."""
    return provider.list_rule_sets()


# ----------------------------------------------------------------------------
def read_script(provider: Provider, name: str | None = None) -> ScriptText:
    """Return a named script, or the active one."""
    name = name or provider.active_rule_set()

    if not name:
        raise MailctlError("no active script; name one explicitly")

    return ScriptText(name, provider.read_rule_set(name), provider)


# ----------------------------------------------------------------------------
def read_rules(provider: Provider, script: str | None = None) -> RulesReport:
    """Read a script's rules and audit their order.

    The audit is about order, so a provider that does not declare
    ``ordering`` has nothing for it to find.
    """
    name = script or provider.active_rule_set()

    if not name:
        raise MailctlError(
            "no active script on the server, so there are no rules to "
            "show. 'mailctl list' shows what the account has."
        )

    rules = provider.read_rules(provider.read_rule_set(name))
    findings = audit(rules) if provider.capabilities.ordering else []

    return RulesReport(name, rules, findings)


# ----------------------------------------------------------------------------
def list_folders(provider: Provider) -> FolderListing:
    """Return the folder list, sorted, with the delimiter."""
    return provider.list_folders()


# ----------------------------------------------------------------------------
def probe_sieve(provider: Provider) -> SieveProbe:
    """Read what the rule half advertises, and its rule-set listing."""
    capabilities = provider.rules_capabilities()
    active, others = provider.list_rule_sets()

    return SieveProbe(capabilities, active, others)


# ----------------------------------------------------------------------------
def probe_imap(provider: Provider) -> ImapProbe:
    """Read what the mail half advertises, and its folder shape."""
    listing = list_folders(provider)

    return ImapProbe(
        provider.mail_capabilities(),
        listing.delimiter,
        len(listing.folders),
        listing.unsubscribed,
    )


# ############################################################################
# Extensions -- what the server advertises, less what is disabled
# ############################################################################


# ----------------------------------------------------------------------------
def report_extensions(
    provider: Provider, probe: SieveProbe, config: Config
) -> list[ExtensionState]:
    """One state per extension mailctl knows or the server lists, by name.

    Empty for a provider that does not declare ``extensions``.
    """
    if not provider.capabilities.extensions:
        return []

    return provider.report_extensions(probe.capabilities, config)


# ############################################################################
# Folder subscription -- a setting, reported and changed like one
# ############################################################################


@dataclass(frozen=True)
class SubscriptionPlan:
    """A folder's subscription, as it is and as it was asked to be."""

    requested: str
    folder: str
    delimiter: str
    subscribe: bool
    subscribed_now: bool

    # ------------------------------------------------------------------------
    @property
    def changes(self) -> bool:
        """Whether executing the plan would change anything."""
        return self.subscribe != self.subscribed_now


# ----------------------------------------------------------------------------
def case_variant_hint(variants: list[str]) -> str:
    """A sentence naming a missing folder's case variants, or nothing.

    For an error about a folder that is missing: the likeliest reason is
    that the one meant is spelled with different case.
    """
    if not variants:
        return ""

    listed = ", ".join(repr(folder) for folder in variants)

    return (
        f"{listed} {'exists' if len(variants) == 1 else 'exist'}, but "
        f"folder names are case-sensitive. "
    )


# ----------------------------------------------------------------------------
def plan_subscription(
    provider: Provider, name: str, subscribe: bool
) -> SubscriptionPlan:
    """Work out a subscription change without making it.

    The name is normalized like every other folder name, so one copied out
    of the folder listing works. Subscribing needs the folder to exist;
    unsubscribing does not, since a subscription can outlive its folder and
    removing that stale entry is a legitimate thing to want.
    """
    folder = provider.normalize(name)
    subscribed_now = provider.is_subscribed(folder)

    hint = case_variant_hint(provider.case_variants(folder))

    if subscribe and not provider.exists(folder):
        raise MailctlError(
            f"no folder named {folder!r} on the server, so there is nothing "
            f"to subscribe to. {hint}'mailctl folders' lists what exists."
        )

    if not subscribe and not subscribed_now and not provider.exists(folder):
        raise MailctlError(
            f"no folder or subscription named {folder!r} on the server. "
            f"{hint}'mailctl folders' lists what exists."
        )

    return SubscriptionPlan(
        name, folder, provider.delimiter(), subscribe, subscribed_now
    )


# ----------------------------------------------------------------------------
def execute_subscription(provider: Provider, plan: SubscriptionPlan) -> None:
    """Apply a subscription plan; a plan that changes nothing does nothing.

    The provider confirms a subscription took effect, so a server that
    answers OK without acting on it raises here.
    """
    if not plan.changes:
        return

    if plan.subscribe:
        provider.subscribe(plan.folder)

    else:
        provider.unsubscribe(plan.folder)


# ############################################################################
# Backups
# ############################################################################


@dataclass(frozen=True)
class BackupPlan:
    """The active script, and where a copy of it would be written."""

    script: str
    source: str
    target: Path
    provider: Provider | type[Provider] = field(compare=False, repr=False)


# ----------------------------------------------------------------------------
def plan_backup(
    provider: Provider, config: Config, output: str | None = None
) -> BackupPlan:
    """Fetch the active script and resolve where its copy goes."""
    name = provider.active_rule_set()

    if not name:
        raise MailctlError(
            "no active script on the server, so there is nothing to back "
            "up. 'mailctl list' shows what the account has."
        )

    source = provider.read_rule_set(name)
    target = provider.backup_target(output, name, config.backup_dir)

    return BackupPlan(name, source, target, provider)


# ----------------------------------------------------------------------------
def execute_backup(plan: BackupPlan) -> Path:
    """Write the server's exact bytes to the planned target."""
    return plan.provider.write_backup(plan.source, plan.target)


# ----------------------------------------------------------------------------
def count_rules(
    provider: Provider | type[Provider], source: str
) -> int | None:
    """Return how many rules a script holds, or None if it will not parse.

    A script too broken to parse is the one most worth backing up, so this
    reports rather than raises.
    """
    try:
        return len(provider.rule_names(source))

    except MailctlError:
        return None


# ############################################################################
# The old name -- what the rename left behind
# ############################################################################


@dataclass(frozen=True)
class ConfigDirPending:
    """The old config directory is there and the new one is not.

    Nothing in ``old`` is read, so the config and the script backups in it
    are stranded until ``plan_config_migration`` moves them.
    """

    old: Path
    new: Path


@dataclass(frozen=True)
class MigrationEntry:
    """One thing in the old directory, as a path relative to it."""

    relative: Path
    mode: int
    directory: bool


@dataclass(frozen=True)
class ConfigReference:
    """A config-file path that points inside the old directory.

    ``written`` is the value as the file spells it; ``replacement`` is the
    same place under the new directory, which is what it becomes.
    """

    key: str
    written: str
    replacement: str


@dataclass(frozen=True)
class ConfigMigrationPlan:
    """What moving the old directory's contents to the new one would do.

    ``conflicts`` are destinations that already exist. Any at all and the
    plan is refused on execute: nothing at the destination is overwritten.
    """

    old: Path
    new: Path
    entries: tuple[MigrationEntry, ...]
    conflicts: tuple[Path, ...]
    references: tuple[ConfigReference, ...]

    # ------------------------------------------------------------------------
    @property
    def files(self) -> tuple[MigrationEntry, ...]:
        return tuple(entry for entry in self.entries if not entry.directory)


@dataclass(frozen=True)
class FileMoved:
    """One file (or symlink) was moved to the new directory."""

    source: Path
    destination: Path


@dataclass(frozen=True)
class ReferenceRewritten:
    """A config-file path was pointed at the new directory -- or, where
    ``rewritten`` is False, could not be found as written to change."""

    reference: ConfigReference
    rewritten: bool


@dataclass(frozen=True)
class OldDirRemoved:
    """The old directory was empty once its contents moved, and is gone."""

    path: Path


# The config-file keys that hold a path mailctl itself opens.
PATH_KEYS = ("password_file", "backup_dir")


# ----------------------------------------------------------------------------
def check_config_dir(
    old: Path | None = None, new: Path | None = None
) -> ConfigDirPending | None:
    """Report the old config directory when the new one does not exist.

    Once the new directory exists the finding stops: what is left in the
    old one is then the user's own choice, not a setup the rename broke.
    """
    old = legacy_config_dir() if old is None else old
    new = config_dir() if new is None else new

    if old.is_dir() and not os.path.lexists(new):
        return ConfigDirPending(old, new)

    return None


# ----------------------------------------------------------------------------
def check_legacy_settings(config: Config) -> list[LegacySetting]:
    """Report old ``MXROUTE_*`` names this run ignored -- names only."""
    return config.legacy_settings()


# ----------------------------------------------------------------------------
def plan_config_migration(
    old: Path | None = None, new: Path | None = None
) -> ConfigMigrationPlan | None:
    """Work out what moving the old directory's contents would do.

    Returns None when there is no old directory. Read-only: it lists what
    would move, which destinations are already taken, and which config
    paths point inside the old directory and would stop resolving.
    """
    old = legacy_config_dir() if old is None else old
    new = config_dir() if new is None else new

    if not old.is_dir():
        return None

    entries = _migration_entries(old)
    conflicts = []

    if os.path.lexists(new) and not _real_dir(new):
        conflicts.append(new)

    for entry in entries:
        destination = new / entry.relative

        if not os.path.lexists(destination):
            continue

        # A directory already there is merged into, not overwritten.
        if entry.directory and _real_dir(destination):
            continue

        conflicts.append(destination)

    return ConfigMigrationPlan(
        old=old,
        new=new,
        entries=entries,
        conflicts=tuple(conflicts),
        references=_config_references(old, new),
    )


# ----------------------------------------------------------------------------
def execute_config_migration(
    plan: ConfigMigrationPlan, on_event: EventSink | None = None
) -> None:
    """Move the planned files, keeping their modes, and tidy up after.

    Files are renamed, not copied, so each keeps its inode and its mode --
    a ``0600`` backup stays ``0600``. A directory created here takes the
    mode of the one it stands in for. A destination that appeared since
    the plan was made stops the move rather than being overwritten.
    """
    if plan.conflicts:
        raise MailctlError(
            f"refusing to migrate: {len(plan.conflicts)} path(s) already "
            f"exist in {plan.new} and would be overwritten"
        )

    emit = on_event or (lambda _event: None)

    try:
        _ensure_dir(plan.new, _mode_of(plan.old))

        for entry in plan.entries:
            if entry.directory:
                _ensure_dir(plan.new / entry.relative, entry.mode)

        for entry in plan.files:
            source = plan.old / entry.relative
            destination = plan.new / entry.relative

            if os.path.lexists(destination):
                raise MailctlError(
                    f"{destination} appeared while migrating; it was left "
                    f"alone and {source} was not moved"
                )

            os.rename(source, destination)
            emit(FileMoved(source, destination))

    except OSError as exc:
        raise MailctlError(f"could not migrate {plan.old}: {exc}") from exc

    for reference in plan.references:
        rewritten = _rewrite_reference(plan.new / "config.toml", reference)
        emit(ReferenceRewritten(reference, rewritten))

    if _remove_empty_tree(plan.old):
        emit(OldDirRemoved(plan.old))


# ----------------------------------------------------------------------------
def _real_dir(path: Path) -> bool:
    """A directory that is not a symlink to one."""
    return path.is_dir() and not path.is_symlink()


# ----------------------------------------------------------------------------
def _mode_of(path: Path) -> int:
    """The permission bits of ``path`` itself, not of a link's target."""
    return stat.S_IMODE(path.lstat().st_mode)


# ----------------------------------------------------------------------------
def _migration_entries(old: Path) -> tuple[MigrationEntry, ...]:
    """Everything under ``old``, parents before children.

    A symlink is moved as a link, never followed, so a link to a directory
    is an entry of its own rather than a tree to descend into.
    """
    entries = []

    for root, dirnames, filenames in os.walk(old):
        base = Path(root)
        dirnames.sort()

        for name in sorted(dirnames + filenames):
            path = base / name
            directory = _real_dir(path)

            entries.append(
                MigrationEntry(
                    path.relative_to(old), _mode_of(path), directory
                )
            )

    entries.sort(key=lambda entry: entry.relative.parts)

    return tuple(entries)


# ----------------------------------------------------------------------------
def _config_references(old: Path, new: Path) -> tuple[ConfigReference, ...]:
    """Find config-file paths that name a place inside the old directory.

    Only the keys that hold a path are looked at. A file that will not
    parse is moved as it is and reported by ``load_config`` when it is
    next read, so it yields nothing here.
    """
    path = old / "config.toml"

    try:
        with path.open("rb") as handle:
            values = tomllib.load(handle)

    except (OSError, tomllib.TOMLDecodeError):
        return ()

    found = []

    for key in PATH_KEYS:
        written = values.get(key)

        if not isinstance(written, str) or not written:
            continue

        expanded = expand_path(written)

        if expanded.is_relative_to(old):
            replacement = str(new / expanded.relative_to(old))
            found.append(ConfigReference(key, written, replacement))

    return tuple(found)


# ----------------------------------------------------------------------------
def _rewrite_reference(path: Path, reference: ConfigReference) -> bool:
    """Point one config-file path at the new directory, in place.

    Only the quoted value as written is replaced, so the rest of the file
    -- comments, layout -- is untouched. Writing into the existing file
    keeps its mode. Returns False when the value cannot be found as
    written (an escaped string, say), leaving the file as it was.
    """
    try:
        text = path.read_text(encoding="utf-8")

    except OSError:
        return False

    replacement = _toml_string(reference.replacement)
    updated = text

    for quote in ('"', "'"):
        updated = updated.replace(
            f"{quote}{reference.written}{quote}", replacement
        )

    if updated == text:
        return False

    try:
        with path.open("w", encoding="utf-8", newline="") as stream:
            stream.write(updated)

    except OSError:
        return False

    return True


# ----------------------------------------------------------------------------
def _toml_string(value: str) -> str:
    """Spell ``value`` as a TOML basic string."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')

    return f'"{escaped}"'


# ----------------------------------------------------------------------------
def _ensure_dir(path: Path, mode: int) -> None:
    """Create ``path`` with ``mode`` unless it is already a directory.

    An existing directory keeps the mode the user gave it.
    """
    if _real_dir(path):
        return

    path.mkdir(parents=True)
    os.chmod(path, mode)


# ----------------------------------------------------------------------------
def _remove_empty_tree(root: Path) -> bool:
    """Remove ``root`` and the directories under it, if all are empty.

    Anything still holding a file is left alone. Returns whether ``root``
    itself is gone.
    """
    for directory, _dirnames, _filenames in os.walk(root, topdown=False):
        try:
            os.rmdir(directory)

        except OSError:
            continue

    return not os.path.lexists(root)


# ############################################################################
# Restore
# ############################################################################


@dataclass(frozen=True)
class RestorePlan:
    """A backup file to be uploaded, whole, over a stored script.

    ``diff`` is a raw diff, not the normalized one a merge shows: restore
    uploads the file's exact bytes, so any difference -- formatting
    included -- is a real change and is shown as one.
    """

    source: Path
    script: str
    before: str
    after: str
    diff: DisplayDiff
    active: str | None
    activate: bool

    # ------------------------------------------------------------------------
    @property
    def changes(self) -> bool:
        return self.before != self.after


@dataclass(frozen=True)
class BackupFile:
    """A backup read from disk, exactly as written, ready to restore."""

    path: Path
    text: str


# ----------------------------------------------------------------------------
def read_backup_file(
    path: str | Path, allow_empty: bool = False
) -> BackupFile:
    """Read a backup file exactly as written, newline translation off.

    Needs no server, so a front-end calls it before connecting: a mistyped
    path or an empty file is reported without costing a login. ``~`` and
    ``$VAR`` / ``${VAR}`` are expanded, as for every other path setting.

    A file holding nothing but whitespace is refused unless
    ``allow_empty``: uploading it removes every rule, and an empty file is
    as likely to be a truncated copy or the wrong path as a deliberate
    wipe.
    """
    source = expand_path(str(path))

    try:
        with source.open(encoding="utf-8", newline="") as handle:
            text = handle.read()

    except (OSError, UnicodeDecodeError) as exc:
        raise MailctlError(f"could not read backup {source} -- {exc}") from exc

    if not text.strip() and not allow_empty:
        raise MailctlError(
            f"backup {source} is empty; restoring it would remove every "
            f"rule from the script. Pass --allow-empty if that is what you "
            f"want."
        )

    return BackupFile(source, text)


# ----------------------------------------------------------------------------
def plan_restore(
    provider: Provider,
    backup: BackupFile,
    script: str | None = None,
    activate: bool = False,
) -> RestorePlan:
    """Work out replacing a script with a backup, without doing it.

    The target is ``script``, or the active script when none is named; no
    other stored script is touched, and whether the target ends up active
    follows the same rule as every other upload (``activates``). The
    current script may be one mailctl cannot parse: restore does not
    merge, and the current bytes are backed up before anything is sent,
    so overwriting it loses nothing (ADR 0005).
    """
    source, after = backup.path, backup.text
    name, before, active = fetch_active(provider, script)

    # Not a guess at a name: with nothing active there is no "the script"
    # to mean, and the recovery case is served by naming one, which is
    # then activated because nothing else runs.
    if script is None and active is None:
        raise MailctlError(
            "no active script on the server to restore over. Name the "
            "script to restore with --script NAME; with nothing active it "
            "is activated. 'mailctl list' shows what the account has."
        )

    return RestorePlan(
        source=source,
        script=name,
        before=before,
        after=after,
        diff=provider.raw_diff(before, after, name),
        active=active,
        activate=activates(name, active, activate),
    )


# ----------------------------------------------------------------------------
def execute_restore(
    provider: Provider,
    config: Config,
    plan: RestorePlan,
    on_event: EventSink | None = None,
) -> Path | None:
    """Back up the current script, then upload the backup file over it.

    Returns the path of the backup taken, or None when the file already
    matches the server and nothing was sent.
    """
    if not plan.changes:
        return None

    return upload_script(
        provider,
        config,
        plan.script,
        plan.before,
        plan.after,
        on_event,
        activate=plan.activate,
    )


# ############################################################################
# Actions
# ############################################################################


# ----------------------------------------------------------------------------
def reject_actions(config: Config, requested: Iterable[str]) -> None:
    """Refuse actions the configured provider will not emit, and say why.

    Needs no connection, so a front-end calls it before connecting.
    """
    provider_for(config).refuse_actions(requested)


# ----------------------------------------------------------------------------
def check_rule(
    config: Config,
    request: RuleRequest,
    provider: Provider | type[Provider] | None = None,
) -> None:
    """Refuse a rule the provider cannot express, before any network work.

    Every refusal goes through one error (``providers.base.refuse``) naming
    the provider, the construct, and why. ``provider`` defaults to the one
    ``config`` selects; :func:`plan_rule` passes its own.
    """
    provider = provider or provider_for(config)
    caps = provider.capabilities

    folder = request.actions.fileinto or config.default_folder or ""
    unsupported = action_names(request.actions, folder) - caps.actions

    if unsupported:
        raise refuse(
            provider.name,
            f"emit {', '.join(sorted(unsupported))}",
            f"it declares only {', '.join(sorted(caps.actions)) or 'none'}",
        )

    if request.placement is not None:
        require_capability(provider, "ordering")

    if request.script or request.activate:
        require_capability(provider, "rule_sets")

    if request.actions.stop:
        require_capability(provider, "stop")

    validate_specifics(provider.name, caps.specifics, request.specifics)


# ----------------------------------------------------------------------------
def resolve_stop(
    provider: Provider | type[Provider], spec: ActionSpec
) -> ActionSpec:
    """Settle a spec's ``stop`` default from the provider's capabilities.

    None asks for the provider's default, which is to stop where it
    declares ``stop``: a host that cannot end evaluation is not asked to,
    so a rule nobody asked to stop is never refused for it. An explicit
    True or False is left as it was.
    """
    if spec.stop is not None:
        return spec

    return replace(spec, stop=provider.capabilities.stop)


# ----------------------------------------------------------------------------
def default_rule_name(criteria: Criteria) -> str:
    """Derive a stable rule name from the first criterion."""
    term = criteria.terms[0]

    slug = re.sub(r"[^A-Za-z0-9]+", "-", term.value).strip("-").lower()

    return f"{term.header.lower()}-{slug}"[:60] or DEFAULT_SCRIPT_NAME


# ############################################################################
# The target folder
# ############################################################################


@dataclass(frozen=True)
class FolderPlan:
    """Where filed mail goes, and how that folder comes to exist.

    ``delimiter_assumed`` is true when there was no IMAP session to read
    the delimiter from, so the provider's assumed one was used instead.

    ``case_variants`` holds existing folders that differ from ``folder``
    only in case. Folder names are case-sensitive, so none of them is the
    target -- but a missing or to-be-created folder with one beside it is
    most likely a typo, and the front-end should say so (#56).

    ``mailbox_disabled_by`` is set when the server advertises ``mailbox``
    but ``disabled_extensions`` turns it off, so ``:create`` is not used;
    it names where that setting came from.
    """

    requested: str
    folder: str
    delimiter: str
    delimiter_assumed: bool
    status: str
    subscribe: bool
    case_variants: tuple[str, ...] = ()
    mailbox_disabled_by: Source | None = None

    # ------------------------------------------------------------------------
    @property
    def use_create(self) -> bool:
        """Whether the rule should say ``fileinto :create``."""
        return self.status in (FOLDER_SIEVE_CREATES, FOLDER_BOTH_CREATE)

    # ------------------------------------------------------------------------
    @property
    def imap_creates(self) -> bool:
        """Whether the execute step makes the folder over IMAP."""
        return self.status in (FOLDER_IMAP_CREATE, FOLDER_BOTH_CREATE)


# ----------------------------------------------------------------------------
def plan_folder(
    provider: Provider,
    config: Config,
    requested: str | None,
    *,
    create: bool = False,
    subscribe: bool = True,
    delimiter: str | None = None,
) -> FolderPlan:
    """Normalize the target folder and decide how it gets to exist.

    With an IMAP session the folder is made over IMAP by
    :func:`create_folder` and subscribed unless declined -- Sieve's
    ``:create`` makes it only at delivery time, when nothing is running to
    subscribe to it (#40). When the server also advertises ``mailbox`` the
    rule says ``fileinto :create`` as well, so it recreates the folder if
    it is later deleted; otherwise it stays a plain ``fileinto``. With no
    IMAP session ``:create`` is the only route. Read-only: nothing is
    created here, and a folder that cannot be created is reported as such
    for :func:`check_folder` to refuse, so the front-end can show the plan
    first.

    With no ManageSieve session (the existing-mail pass alone) the Sieve
    route is simply unavailable. ``mailbox`` named in
    ``disabled_extensions`` counts as not advertised: the rule stays a
    plain ``fileinto`` and IMAP, where there is a session, makes the
    folder.
    """
    requested = requested or config.default_folder or ""
    mail = provider.has_mail

    if not requested:
        return FolderPlan("", "", "", False, FOLDER_NONE, subscribe)

    if not mail:
        folder, assumed = provider.assumed_folder(requested, delimiter)

    else:
        assumed = provider.delimiter()
        folder = provider.normalize(requested)

    shape = {
        "requested": requested,
        "folder": folder,
        "delimiter": assumed,
        "delimiter_assumed": not mail,
        "subscribe": subscribe,
    }

    if mail and provider.exists(folder):
        return FolderPlan(status=FOLDER_EXISTS, **shape)

    if mail:
        shape["case_variants"] = tuple(provider.case_variants(folder))

    delivery = provider.delivery_create(config)
    has_mailbox = delivery.usable

    if delivery.disabled_by is not None:
        shape["mailbox_disabled_by"] = delivery.disabled_by

    if not create:
        return FolderPlan(status=FOLDER_MISSING, **shape)

    if not mail:
        status = FOLDER_SIEVE_CREATES if has_mailbox else FOLDER_UNCREATABLE

    else:
        status = FOLDER_BOTH_CREATE if has_mailbox else FOLDER_IMAP_CREATE

    return FolderPlan(status=status, **shape)


# ----------------------------------------------------------------------------
def check_folder(plan: FolderPlan) -> None:
    """Refuse a folder that was asked to be created and cannot be."""
    if plan.status != FOLDER_UNCREATABLE:
        return

    if plan.mailbox_disabled_by is not None:
        raise MailctlError(
            f"the Sieve 'mailbox' extension is disabled by mailctl "
            f"(disabled_extensions, from "
            f"{plan.mailbox_disabled_by.describe()}) and --no-imap was "
            f"given, so {plan.folder!r} cannot be created"
        )

    raise MailctlError(
        f"the server does not advertise the Sieve 'mailbox' extension "
        f"and --no-imap was given, so {plan.folder!r} cannot be created"
    )


# ----------------------------------------------------------------------------
def create_folder(provider: Provider, plan: FolderPlan) -> FolderCreation:
    """Create the planned folder over IMAP, subscribing unless declined."""
    if not plan.imap_creates:
        raise MailctlError(
            f"folder {plan.folder!r} is not planned for IMAP creation "
            f"({plan.status})"
        )

    return provider.create_folder(plan.folder, subscribe=plan.subscribe)


# ----------------------------------------------------------------------------
def folder_pending(provider: Provider, plan: FolderPlan) -> bool:
    """Whether a folder planned for IMAP creation has not been made yet."""
    return plan.imap_creates and not provider.exists(plan.folder)


# ----------------------------------------------------------------------------
def realize_folder(
    provider: Provider, plan: FolderPlan, on_event: EventSink | None = None
) -> FolderCreation | None:
    """Create a folder planned for IMAP creation, once.

    Returns None, and does nothing, for any other plan or for a folder an
    earlier execute step already made -- ``add`` creates it before the
    upload, and its existing-mail pass must not try again.
    """
    if not folder_pending(provider, plan):
        return None

    result = create_folder(provider, plan)

    if on_event:
        on_event(FolderCreated(result))

    return result


# ############################################################################
# Adding and removing rules
# ############################################################################


@dataclass(frozen=True)
class RulePlan:
    """A rule merged into the script, not yet uploaded."""

    name: str
    script: str
    before: str
    after: str
    criteria: Criteria
    actions: list
    placement: Analysis
    diff: DisplayDiff
    folder: FolderPlan
    active: str | None
    activate: bool


@dataclass(frozen=True)
class RemovalPlan:
    """A rule taken out of the script, not yet uploaded."""

    rule: str
    script: str
    before: str
    after: str
    diff: DisplayDiff
    active: str | None
    activate: bool


@dataclass(frozen=True)
class MovePlan:
    """A rule moved within the script, not yet uploaded.

    Positions are 0-based over the whole script. ``placement`` judges the
    rule where it lands, both ways: what would stop it running, and what it
    would now stop.
    """

    rule: str
    script: str
    before: str
    after: str
    from_index: int
    to_index: int
    count: int
    placement: Analysis
    diff: DisplayDiff
    active: str | None
    activate: bool

    # ------------------------------------------------------------------------
    @property
    def changes(self) -> bool:
        return self.from_index != self.to_index


# ----------------------------------------------------------------------------
def fetch_active(
    provider: Provider, requested: str | None = None
) -> tuple[str, str, str | None]:
    """Return ``(script_name, source, active)`` for the script to edit.

    ``active`` is the name of the script the server runs now, or None.

    The name always comes from LISTSCRIPTS and is written back to. It is
    never guessed: whatever the webmail's managesieve plugin calls its
    script is a server-side config value (``managesieve_script_name``) that
    nothing about the account exposes, so a guess would create a *second*
    script and quietly leave the real one in charge. MXRoute has also said
    it intends to move off DirectAdmin, Crossbox, and Roundcube, and is
    mid-migration from Dovecot 2.3 to 2.4 -- what is discovered at runtime
    survives that, and a hardcoded name would not.

    With nothing active and nothing requested, a script mailctl wrote under
    its old name (``LEGACY_SCRIPT_NAME``) is picked up again, so an account
    set up before the rename does not grow a duplicate beside it.
    ``DEFAULT_SCRIPT_NAME`` is used only when there is neither.
    """
    active = provider.active_rule_set()
    _active, others = provider.list_rule_sets()

    fallback = (
        LEGACY_SCRIPT_NAME
        if LEGACY_SCRIPT_NAME in others
        else DEFAULT_SCRIPT_NAME
    )
    name = requested or active or fallback

    if name == active or name in others:
        return (name, provider.read_rule_set(name), active)

    return (name, "", active)


# ----------------------------------------------------------------------------
def activates(name: str, active: str | None, requested: bool) -> bool:
    """Whether uploading ``name`` should also make it the active script.

    Only one script runs, so switching it is a change of its own and is
    never a side effect of editing another: ``--script other`` edits
    ``other`` and leaves the running script alone unless activation was
    asked for. With nothing active, activating is the only way the upload
    does anything at all.
    """
    return requested or active is None or name == active


# ----------------------------------------------------------------------------
def missing_extensions(
    provider: Provider, spec: ActionSpec, folder: FolderPlan
) -> list[str]:
    """Return the extensions the rule needs that the server does not list."""
    needed = provider.required_features(
        resolve_stop(provider, spec), folder.folder, folder.use_create
    )

    return provider.missing_features(needed)


# ----------------------------------------------------------------------------
def placement_analysis(
    provider: Provider | type[Provider],
    before: str,
    name: str,
    criteria: Criteria,
    actions: list,
    placement: Placement | None = None,
) -> Analysis:
    """Judge a rule at the position it will actually occupy.

    A rule of the same name is dropped from the comparison set first. With
    a replace the old copy is being overwritten, so leaving it in would
    have the new rule shadowed by the version it replaces -- and would put
    the indexes out by one, since ``position`` counts the other rules only.

    A provider that does not declare ``ordering`` evaluates every rule on
    its own, so no position can shadow one and the analysis is empty.
    """
    if not provider.capabilities.ordering:
        return Analysis()

    present = provider.read_rules(before)
    rules = [entry for entry in present if entry.name != name]

    candidate = provider.candidate_rule(name, criteria, actions)

    # Resolved against every name in the script, including the one being
    # replaced -- that is how "no placement, so leave it where it is" finds
    # where it currently is.
    #
    # NOT rule_from_criteria's index=-1 default: analyze_placement clamps
    # with max(0, at_index), so a -1 here would mean the FRONT of the
    # script rather than the end of it.
    at_index = provider.position(
        [entry.name for entry in present], placement, name
    )

    return analyze_placement(rules, candidate, at_index=at_index)


# ----------------------------------------------------------------------------
def plan_rule(
    provider: Provider,
    config: Config,
    request: RuleRequest,
    folder: FolderPlan,
) -> RulePlan:
    """Merge a rule into the active script without uploading it.

    Never overwrites: the rule is merged into the parsed existing script,
    and a parse failure is raised rather than fallen back from (ADR 0002).
    A rule the provider cannot express is refused before the script is
    read (:func:`check_rule`). A rule needing an extension named in
    ``disabled_extensions`` is refused; the one with a fallback,
    ``mailbox``, was already dropped by :func:`plan_folder`.
    """
    check_rule(config, request, provider)
    request.criteria.require_terms()
    check_folder(folder)

    actions = provider.translate_actions(
        resolve_stop(provider, request.actions),
        folder.folder,
        folder.use_create,
    )
    provider.check_actions(config, actions)
    name = request.name or default_rule_name(request.criteria)
    script, before, active = fetch_active(provider, request.script)

    after = provider.add_rule(
        before,
        name,
        request.criteria,
        actions,
        replace=request.replace,
        placement=request.placement,
    )

    return RulePlan(
        name=name,
        script=script,
        before=before,
        after=after,
        criteria=request.criteria,
        actions=actions,
        placement=placement_analysis(
            provider,
            before,
            name,
            request.criteria,
            actions,
            request.placement,
        ),
        diff=provider.diff(before, after, script),
        folder=folder,
        active=active,
        activate=activates(script, active, request.activate),
    )


# ----------------------------------------------------------------------------
def plan_removal(
    provider: Provider,
    rule: str,
    script: str | None = None,
    activate: bool = False,
) -> RemovalPlan:
    """Take a named rule out of the script without uploading the result."""
    name, before, active = fetch_active(provider, script)

    if not before.strip():
        raise MailctlError(f"script {name!r} is empty")

    after = provider.remove_rule(before, rule)

    return RemovalPlan(
        rule,
        name,
        before,
        after,
        provider.diff(before, after, name),
        active,
        activates(name, active, activate),
    )


# ----------------------------------------------------------------------------
def plan_move(
    provider: Provider,
    rule: str,
    placement: Placement,
    script: str | None = None,
    activate: bool = False,
) -> MovePlan:
    """Reorder a named rule without restating it, and without uploading.

    Refused, before the script is read, by a provider that does not
    declare ``ordering``.
    """
    require_capability(provider, "ordering")

    name, before, active = fetch_active(provider, script)

    if not before.strip():
        raise MailctlError(f"script {name!r} is empty")

    after = provider.move_rule(before, rule, placement)

    present = provider.read_rules(before)
    names = [entry.name for entry in present]
    from_index = names.index(rule)
    to_index = provider.position(names, placement, rule)

    candidate = present[from_index]
    others = present[:from_index] + present[from_index + 1 :]

    return MovePlan(
        rule=rule,
        script=name,
        before=before,
        after=after,
        from_index=from_index,
        to_index=to_index,
        count=len(present),
        placement=analyze_placement(others, candidate, at_index=to_index),
        diff=provider.diff(before, after, name),
        active=active,
        activate=activates(name, active, activate),
    )


# ----------------------------------------------------------------------------
def upload_script(
    provider: Provider,
    config: Config,
    name: str,
    before: str,
    after: str,
    on_event: EventSink | None = None,
    before_put: Callable[[], object] | None = None,
    *,
    activate: bool,
) -> Path:
    """Back up, validate, and upload a script; activate it if asked.

    ``activate`` has no default: whether the upload also switches which
    script the server runs is decided by the plan (``activates``), and a
    caller that forgot to pass it would otherwise switch it silently.

    The backup is written, and announced, before the server sees anything,
    so a rejected upload still leaves the user knowing where the copy is.
    ``before_put`` runs once CHECKSCRIPT has accepted the new script and
    before it is stored -- the point where a prerequisite is worth making.
    """
    emit = on_event or (lambda event: None)

    path = provider.backup(before, name, config.backup_dir)
    emit(ScriptBackedUp(name, path))

    provider.check_rule_set(after)

    if before_put:
        before_put()

    provider.store_rule_set(name, after)

    if activate:
        provider.activate_rule_set(name)

    emit(ScriptUploaded(name, activate))

    return path


# ----------------------------------------------------------------------------
def execute_script_change(
    provider: Provider,
    config: Config,
    plan: RulePlan | RemovalPlan | MovePlan,
    on_event: EventSink | None = None,
) -> Path:
    """Upload a planned rule change; return the backup's path.

    A rule's target folder, when it is planned for IMAP creation, is
    created here -- after the server has accepted the script and before it
    is stored -- so neither a dry run nor a rejected script leaves a stray
    folder, and the rule never goes live pointing at a missing one.
    """
    before_put = None

    if isinstance(plan, RulePlan):
        provider.check_actions(config, plan.actions)
        folder = plan.folder

        def before_put():
            realize_folder(provider, folder, on_event)

    return upload_script(
        provider,
        config,
        plan.script,
        plan.before,
        plan.after,
        on_event,
        before_put,
        activate=plan.activate,
    )


# ############################################################################
# The existing-mail pass
# ############################################################################


# ----------------------------------------------------------------------------
def require_mail_action(folder: FolderPlan, spec: ActionSpec) -> None:
    """Refuse an existing-mail pass that would do nothing to a message."""
    if not folder.folder and not spec.discard and not spec.flags:
        raise MailctlError(
            "nothing to do -- use --fileinto, --discard, --mark-read, "
            "or --flag"
        )


# ----------------------------------------------------------------------------
def mail_pass_is_noop(spec: ActionSpec, source: str, destination: str) -> bool:
    """Whether the existing-mail pass would change nothing at all.

    A rule that only keeps mail, or files it into the folder it is already
    in, has nothing to do to delivered mail -- searching for it and asking
    about it would be a prompt that changes nothing.
    """
    return (
        not spec.discard
        and not spec.flags
        and (not destination or same_folder(destination, source))
    )


# ----------------------------------------------------------------------------
def source_folder(provider: Provider, name: str) -> str:
    """Normalize the folder the existing-mail pass reads from.

    The same normalization the target gets, so ``--folder Lists/X`` and
    ``--fileinto Lists/X`` are recognised as one folder, and the search
    selects the server's real name for it.
    """
    return provider.normalize(name)


# ----------------------------------------------------------------------------
def plan_mail(
    provider: Provider,
    criteria: Criteria,
    spec: ActionSpec,
    source: str,
    destination: str,
) -> MailActionPlan:
    """Select the matches in ``source`` read-only and plan what is done.

    Selection is the provider's: it answers which delivered messages the
    rule matches, however its host evaluates rules.
    """
    criteria.require_terms()

    return provider.select_mail(
        criteria, source, destination, list(spec.flags), spec.discard
    )


# ----------------------------------------------------------------------------
def check_message_cap(plan: MailActionPlan, max_messages: int) -> None:
    """Refuse a plan larger than the cap, whole.

    Processing the first N and reporting success would read as "it handled
    everything", so a partial batch is never processed.
    """
    if plan.count <= max_messages:
        return

    raise MailctlError(
        f"{plan.count} message(s) match but --max-messages is "
        f"{max_messages}. NO existing message was touched -- a "
        f"partial batch is never processed, because handling the first "
        f"{max_messages} and reporting success would read as "
        f"having handled them all. Re-run with --max-messages "
        f"{plan.count} (or higher) to process every match. Note that "
        f"--yes does NOT lift this cap: it skips the confirmation "
        f"prompt, whereas the cap is a ceiling you set deliberately. "
        f"Any Sieve rule in this command was already uploaded and "
        f"applies to new mail regardless."
    )


# ----------------------------------------------------------------------------
def execute_mail(
    provider: Provider,
    plan: MailActionPlan,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    folder: FolderPlan | None = None,
    on_event: EventSink | None = None,
) -> MailActionResult:
    """Carry out an approved plan, re-checking the cap first.

    ``folder`` is the destination's plan; one due for IMAP creation is
    created here, after the decision, rather than while planning.
    """
    check_message_cap(plan, max_messages)

    if folder is not None:
        realize_folder(provider, folder, on_event)

    return provider.apply_mail(plan)


# ############################################################################
# Deriving a rule from a message
# ############################################################################


@dataclass(frozen=True)
class PickedMessage:
    """The message a rule is derived from, and how it was chosen.

    ``candidates`` is how many messages a search matched; above one, the
    most recent was taken.
    """

    uid: int
    folder: str
    headers: object
    candidates: int = 1


@dataclass(frozen=True)
class DerivedCriteria:
    """Criteria built from a message, and the headers it lacked."""

    criteria: Criteria
    skipped: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------
def pick_message(
    provider: Provider,
    folder: str,
    uid: int | None = None,
    search: str | None = None,
) -> PickedMessage:
    """Fetch one message's headers, by UID or by the newest search match."""
    candidates = 1

    if uid is None:
        uids = provider.search_messages(folder, search or "")

        if not uids:
            raise MailctlError(f"no message in {folder!r} matched {search!r}")

        candidates = len(uids)
        uid = max(uids)

    headers = provider.message_headers(folder, uid)

    return PickedMessage(uid, folder, headers, candidates)


# ----------------------------------------------------------------------------
def derive_criteria(
    message,
    derive: str = "auto",
    match: str = "any",
    compare: str = "contains",
) -> DerivedCriteria:
    """Build criteria from a message's headers.

    The default order is deliberate: a List-Id identifies a mailing list far
    more reliably than a sender address does, so it wins when present.

    The result is not validated, so the caller can report the skipped
    headers before ``criteria.require_terms()`` refuses an empty set.
    """
    criteria = Criteria(match=match, compare=compare)
    skipped = []

    wanted = [
        item.strip().lower()
        for item in (derive or "auto").split(",")
        if item.strip()
    ]

    if wanted == ["auto"]:
        wanted = ["list-id"] if message.get("List-Id") else ["from"]

    for header in wanted:
        raw = message.get(header)

        if not raw:
            skipped.append(header)
            continue

        criteria.add(header, extract_value(header, raw))

    return DerivedCriteria(criteria, skipped)


# ----------------------------------------------------------------------------
def extract_value(header: str, raw: str) -> str:
    """Reduce a header to the part worth matching on.

    An address header keeps only the address, and a List-Id keeps only the
    bracketed identifier -- the display name around either is cosmetic and
    changes between messages.
    """
    value = decode_header_value(raw).strip()

    if header in ("from", "to", "cc", "sender", "reply-to"):
        address = email.utils.parseaddr(value)[1]

        return address or value

    if header == "list-id":
        match = re.search(r"<([^>]+)>", value)

        return match.group(1) if match else value

    return value


# ############################################################################
# Finding and reading messages
# ############################################################################

DEFAULT_LIST_LIMIT = 20

# Undecodable 8-bit bytes surface as these; no output stream can encode one.
LONE_SURROGATES = re.compile("[\ud800-\udfff]")

# Tags after which the crude HTML rendering starts a new line.
HTML_BREAKS = frozenset(
    (
        "address",
        "article",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    )
)

HTML_HIDDEN = frozenset(("head", "script", "style", "template", "title"))


@dataclass(frozen=True)
class MessageListing:
    """The newest messages in a folder that matched, newest first.

    ``more`` is true when candidates beyond the limit were not examined,
    so there may be further matches to page to.
    """

    folder: str
    messages: list[MessageSummary]
    more: bool = False


@dataclass(frozen=True)
class Attachment:
    """A part of a message that is not its text.

    ``name`` is the decoded file name, empty when the part has none;
    ``size`` is the decoded payload in bytes.
    """

    name: str
    content_type: str
    size: int


@dataclass(frozen=True)
class MessageContent:
    """One message, decoded for reading and never written anywhere.

    ``headers`` holds every header in order, RFC 2047-decoded and unfolded.
    ``body`` is the text a person reads: the text/plain parts, or -- when a
    message has only HTML -- a crude text rendering of it, which
    ``body_from_html`` flags. ``source`` is the exact RFC 822 bytes the
    server holds. All of it is untrusted, attacker-controlled text: a
    front-end must neutralize it for its own medium before display.
    """

    uid: int
    folder: str
    headers: list[tuple[str, str]]
    body: str
    body_from_html: bool
    attachments: list[Attachment]
    flags: tuple[str, ...]
    source: bytes

    # ------------------------------------------------------------------------
    @property
    def size(self) -> int:
        return len(self.source)

    # ------------------------------------------------------------------------
    def header(self, name: str) -> str:
        """Return the first value of a header, or "" when it is absent."""
        wanted = name.lower()

        return next(
            (value for key, value in self.headers if key.lower() == wanted),
            "",
        )


# ----------------------------------------------------------------------------
def list_messages(
    provider: Provider,
    folder: str = "INBOX",
    criteria: Criteria | None = None,
    search: str | None = None,
    limit: int | None = DEFAULT_LIST_LIMIT,
) -> MessageListing:
    """List the newest messages in ``folder``, read-only.

    Select them with ``criteria`` -- the same model a rule uses, re-checked
    against real headers -- or with a raw IMAP ``search`` expression, never
    both; with neither, every message is listed. ``limit`` None lists all.
    Nothing is marked read.
    """
    if criteria is not None and not criteria:
        criteria = None

    if criteria is not None and search:
        raise MailctlError(
            "give criteria or a raw IMAP search expression, not both"
        )

    if limit is not None and limit < 1:
        raise MailctlError(
            f"the message limit must be at least 1, not {limit}"
        )

    folder = provider.normalize(folder)

    messages, more = provider.list_messages(
        folder, criteria=criteria, expression=search, limit=limit
    )

    return MessageListing(folder, messages, more)


# ----------------------------------------------------------------------------
def read_message(provider: Provider, folder: str, uid: int) -> MessageContent:
    """Fetch one message whole and decode it, without marking it read.

    The folder is selected read-only and the body fetched with
    ``BODY.PEEK[]``, so \\Seen is left exactly as it was. Nothing is
    written to disk: attachments are described, never saved.
    """
    if uid < 1:
        raise MailctlError(f"message UIDs start at 1, not {uid}")

    folder = provider.normalize(folder)

    source, flags = provider.message_source(folder, uid)

    return parse_message(source, uid=uid, folder=folder, flags=flags)


# ----------------------------------------------------------------------------
def parse_message(
    source: bytes,
    uid: int = 0,
    folder: str = "",
    flags: tuple[str, ...] = (),
) -> MessageContent:
    """Decode RFC 822 bytes into a :class:`MessageContent`. Offline.

    Decoding never raises on bad input: an unknown or lying charset
    decodes as UTF-8 with replacement characters, since a message that
    cannot be read perfectly should still be readable.
    """
    message = email.message_from_bytes(source)

    headers = [(name, _header_text(value)) for name, value in message.items()]

    bodies: list[tuple[str, bool]] = []
    attachments: list[Attachment] = []
    _collect_parts(message, bodies, attachments)

    return MessageContent(
        uid=uid,
        folder=folder,
        headers=headers,
        body="\n\n".join(text for text, _ in bodies),
        body_from_html=any(from_html for _, from_html in bodies),
        attachments=attachments,
        flags=tuple(flags),
        source=source,
    )


# ----------------------------------------------------------------------------
def _collect_parts(part, bodies: list, attachments: list) -> None:
    """Walk a MIME tree, sorting leaves into body text and attachments.

    Of a multipart/alternative only one child is read -- the text/plain
    one if there is one, else the last, which RFC 2046 makes the richest --
    since the others are the same content again, not extra parts. An
    attached message is one attachment rather than a tree to descend into,
    or its text would be shown as though it were this message's.
    """
    kind = part.get_content_type()

    if kind == "message/rfc822" or _marked_attachment(part):
        attachments.append(_attachment(part))

    elif kind == "multipart/alternative" and part.is_multipart():
        children = part.get_payload() or []

        chosen = next(
            (c for c in children if c.get_content_type() == "text/plain"),
            children[-1] if children else None,
        )

        if chosen is not None:
            _collect_parts(chosen, bodies, attachments)

    elif part.is_multipart():
        for child in part.get_payload():
            _collect_parts(child, bodies, attachments)

    elif kind == "text/plain":
        bodies.append((_decoded_text(part), False))

    elif kind == "text/html":
        bodies.append((html_to_text(_decoded_text(part)), True))

    else:
        attachments.append(_attachment(part))


# ----------------------------------------------------------------------------
def _marked_attachment(part) -> bool:
    """Whether a part declares itself a file rather than message text."""
    return (
        part.get_content_disposition() == "attachment"
        or _filename(part) is not None
    )


# ----------------------------------------------------------------------------
def _attachment(part) -> Attachment:
    """Describe a part as an attachment, without keeping its content."""
    name = _filename(part) or ""

    return Attachment(
        name=_header_text(name) if name else "",
        content_type=part.get_content_type(),
        size=_payload_size(part),
    )


# ----------------------------------------------------------------------------
def _filename(part) -> str | None:
    """A part's file name; "" when it declares one that cannot be read.

    The stdlib's RFC 2231 decoding raises TypeError on continuations
    (``filename*0*``) mixed with a whole ``filename*``, so a malformed
    name is reported as unnamed rather than failing the whole message.
    """
    try:
        return part.get_filename()

    except (TypeError, ValueError):
        return ""


# ----------------------------------------------------------------------------
def _payload_size(part) -> int:
    """The decoded size of a part; an attached message counts whole."""
    if part.is_multipart():
        return sum(len(child.as_bytes()) for child in part.get_payload())

    return len(part.get_payload(decode=True) or b"")


# ----------------------------------------------------------------------------
def _decoded_text(part) -> str:
    """Decode a text part's payload by its declared charset, defensively."""
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"

    try:
        text = payload.decode(charset, errors="replace")

    # LookupError for an unknown name; ValueError for one no lookup can
    # take at all, such as a name containing NUL.
    except (LookupError, ValueError):
        text = payload.decode("utf-8", errors="replace")

    return text.replace("\r\n", "\n")


# ----------------------------------------------------------------------------
def _header_text(raw) -> str:
    """Decode a header value to clean text, joined back onto one line.

    Undecodable 8-bit bytes arrive as lone surrogates, which no output
    stream can encode, so they become replacement characters here rather
    than an exception in whichever front-end prints them.
    """
    value = decode_header_value(raw)
    value = LONE_SURROGATES.sub("\ufffd", value)

    # Unfold (RFC 5322 2.2.3): a line break before whitespace is folding.
    return re.sub(r"\r?\n(?=[ \t])", "", value)


# ----------------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    """Collect an HTML document's visible text, one block per line."""

    # ------------------------------------------------------------------------
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.hidden = 0

    # ------------------------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if tag in HTML_HIDDEN:
            self.hidden += 1

        elif tag in HTML_BREAKS:
            self.chunks.append("\n")

    # ------------------------------------------------------------------------
    def handle_endtag(self, tag):
        if tag in HTML_HIDDEN:
            self.hidden = max(0, self.hidden - 1)

        elif tag in HTML_BREAKS:
            self.chunks.append("\n")

    # ------------------------------------------------------------------------
    def handle_data(self, data):
        if not self.hidden:
            self.chunks.append(data)


# ----------------------------------------------------------------------------
def html_to_text(markup: str) -> str:
    """Render HTML as crude plain text: tags dropped, blocks on new lines.

    Deliberately crude -- no tables, links, or styling survive -- which is
    why a message read this way is flagged as converted. The original is
    always in ``MessageContent.source``.
    """
    extractor = _TextExtractor()
    extractor.feed(markup)
    extractor.close()

    lines = (
        " ".join(line.split())
        for line in "".join(extractor.chunks).split("\n")
    )

    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
