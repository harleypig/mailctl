"""The engine: every piece of work mxfilter does, for any front-end.

A front-end -- the CLI today -- turns what a person asked for into the
plain values below, calls the engine, and decides what to show and whether
to go on. The engine never learns how it was called: it takes no parsed
arguments, never prints or prompts, and reports through return values,
exceptions (``MxFilterError``), and two optional callbacks -- ``progress``
for protocol chatter and ``on_event`` for the steps of a change as they
happen.

Every change is split the same way. A ``plan_*`` function is read-only and
returns what would change; the front-end renders it and decides; an
``execute``-style function carries out the plan it was handed. A dry run is
a plan that is never executed.
"""

import email.utils
import re
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from functools import partial
from html.parser import HTMLParser
from pathlib import Path

from . import MxFilterError
from .config import Config, expand_path
from .criteria import Criteria, escape_sieve_string
from .imap import (
    FolderCreation,
    ImapSession,
    MailActionPlan,
    MailActionResult,
    MessageSummary,
    case_variant_hint,
    decode_header_value,
    normalize_folder,
    same_folder,
)
from .rules import (
    Analysis,
    Rule,
    Shadow,
    analyze_placement,
    audit,
    rule_from_criteria,
)
from .rules import read_rules as read_rule_list
from .sieve import (
    MXROUTE_FORBIDDEN_ACTIONS,
    UNIMPLEMENTED_ACTIONS,
    DisplayDiff,
    Placement,
    SieveSession,
    backup_script,
    display_diff,
    merge_rule,
    move_rule,
    parse_script,
    remove_rule,
    resolve_backup_target,
    resolve_position,
    rule_names,
    script_diff,
    write_backup,
)

DEFAULT_SCRIPT_NAME = "mxfilter"
DEFAULT_MAX_MESSAGES = 500

# (channel, message) -- channel is "sieve" or "imap".
Progress = Callable[[str, str], None]

# Folder plan outcomes.
FOLDER_NONE = "none"
FOLDER_EXISTS = "exists"
FOLDER_MISSING = "missing"
FOLDER_SIEVE_CREATES = "sieve-creates"
FOLDER_IMAP_CREATE = "imap-create"
FOLDER_UNCREATABLE = "uncreatable"


# ############################################################################
# Inputs
# ############################################################################


@dataclass(frozen=True)
class ActionSpec:
    """What a rule, or the existing-mail pass, should do to a message.

    ``fileinto`` is the folder as the user named it, before normalization;
    None falls back to ``Config.default_folder``. ``flags`` are IMAP flag
    names, unescaped and in the order they should be added.
    """

    fileinto: str | None = None
    discard: bool = False
    flags: tuple[str, ...] = ()
    keep: bool = False
    stop: bool = True


@dataclass(frozen=True)
class RuleRequest:
    """A rule to merge into the account's script."""

    criteria: Criteria
    actions: ActionSpec
    name: str | None = None
    script: str | None = None
    replace: bool = False
    placement: Placement | None = None
    activate: bool = False


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
# Sessions
# ############################################################################


@dataclass
class Sessions:
    """The open server sessions a piece of work runs against."""

    sieve: SieveSession | None = None
    imap: ImapSession | None = None


# ----------------------------------------------------------------------------
@contextmanager
def connect(
    config: Config,
    *,
    sieve: bool = True,
    imap: bool = False,
    progress: Progress | None = None,
) -> Iterator[Sessions]:
    """Open the requested sessions and close them on the way out.

    IMAP is opened first, so a login failure there is reported before any
    ManageSieve traffic.
    """

    def channel(name: str):
        return partial(progress, name) if progress else None

    with ExitStack() as stack:
        sessions = Sessions()

        if imap:
            sessions.imap = stack.enter_context(
                ImapSession(config, progress=channel("imap"))
            )

        if sieve:
            sessions.sieve = stack.enter_context(
                SieveSession(config, progress=channel("sieve"))
            )

        yield sessions


# ----------------------------------------------------------------------------
def _sieve(sessions: Sessions) -> SieveSession:
    """Return the ManageSieve session, or raise if none was opened."""
    if sessions.sieve is None:
        raise MxFilterError("no ManageSieve session is open")

    return sessions.sieve


# ----------------------------------------------------------------------------
def _imap(sessions: Sessions) -> ImapSession:
    """Return the IMAP session, or raise if none was opened."""
    if sessions.imap is None:
        raise MxFilterError("no IMAP session is open")

    return sessions.imap


# ############################################################################
# Reading the account
# ############################################################################


@dataclass(frozen=True)
class ScriptText:
    """One script's source, exactly as the server holds it."""

    name: str
    source: str

    # ------------------------------------------------------------------------
    def rule_names(self) -> list[str]:
        """Parse on demand, so an unparseable script can still be shown."""
        return rule_names(parse_script(self.source))


@dataclass(frozen=True)
class RulesReport:
    """A script's rules in evaluation order, and which cannot fire."""

    script: str
    rules: list[Rule]
    findings: list[Shadow]


@dataclass(frozen=True)
class FolderListing:
    """The account's folders, the hierarchy delimiter, and which folders
    are subscribed (LSUB) -- the ones webmail actually draws."""

    delimiter: str
    folders: list[str]
    subscribed: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------------
    def is_subscribed(self, folder: str) -> bool:
        return any(same_folder(name, folder) for name in self.subscribed)

    # ------------------------------------------------------------------------
    @property
    def unsubscribed(self) -> list[str]:
        """Folders that exist but that webmail will not show."""
        return [name for name in self.folders if not self.is_subscribed(name)]


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

        Detection only: mxfilter has no FILTER=SIEVE code path.
        """
        return any(
            item.upper().startswith("FILTER=SIEVE")
            for item in self.capabilities
        )


# ----------------------------------------------------------------------------
def list_scripts(sessions: Sessions) -> tuple[str | None, list[str]]:
    """Return ``(active, others)``."""
    return _sieve(sessions).list_scripts()


# ----------------------------------------------------------------------------
def read_script(sessions: Sessions, name: str | None = None) -> ScriptText:
    """Return a named script, or the active one."""
    sieve = _sieve(sessions)
    name = name or sieve.active_script_name()

    if not name:
        raise MxFilterError("no active script; name one explicitly")

    return ScriptText(name, sieve.get_script(name))


# ----------------------------------------------------------------------------
def read_rules(sessions: Sessions, script: str | None = None) -> RulesReport:
    """Read a script's rules and audit their order."""
    sieve = _sieve(sessions)
    name = script or sieve.active_script_name()

    if not name:
        raise MxFilterError(
            "no active script on the server, so there are no rules to "
            "show. 'mxfilter list' shows what the account has."
        )

    rules = read_rule_list(parse_script(sieve.get_script(name)))

    return RulesReport(name, rules, audit(rules))


# ----------------------------------------------------------------------------
def list_folders(sessions: Sessions) -> FolderListing:
    """Return the folder list, sorted, with the delimiter."""
    imap = _imap(sessions)

    return FolderListing(
        imap.delimiter, sorted(imap.folders), list(imap.subscribed_folders)
    )


# ----------------------------------------------------------------------------
def probe_sieve(sessions: Sessions) -> SieveProbe:
    """Read the ManageSieve capabilities and script listing."""
    sieve = _sieve(sessions)
    capabilities = sieve.capabilities()
    active, others = sieve.list_scripts()

    return SieveProbe(capabilities, active, others)


# ----------------------------------------------------------------------------
def probe_imap(sessions: Sessions) -> ImapProbe:
    """Read the IMAP capabilities and folder shape."""
    imap = _imap(sessions)

    listing = list_folders(sessions)

    return ImapProbe(
        imap.capabilities(),
        imap.delimiter,
        len(listing.folders),
        listing.unsubscribed,
    )


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
def plan_subscription(
    sessions: Sessions, name: str, subscribe: bool
) -> SubscriptionPlan:
    """Work out a subscription change without making it.

    The name is normalized like every other folder name, so one copied out
    of the folder listing works. Subscribing needs the folder to exist;
    unsubscribing does not, since a subscription can outlive its folder and
    removing that stale entry is a legitimate thing to want.
    """
    imap = _imap(sessions)
    folder = imap.normalize(name)
    subscribed_now = imap.is_subscribed(folder)

    hint = case_variant_hint(folder, imap.folders)

    if subscribe and not imap.exists(folder):
        raise MxFilterError(
            f"no folder named {folder!r} on the server, so there is nothing "
            f"to subscribe to. {hint}'mxfilter folders' lists what exists."
        )

    if not subscribe and not subscribed_now and not imap.exists(folder):
        raise MxFilterError(
            f"no folder or subscription named {folder!r} on the server. "
            f"{hint}'mxfilter folders' lists what exists."
        )

    return SubscriptionPlan(
        name, folder, imap.delimiter, subscribe, subscribed_now
    )


# ----------------------------------------------------------------------------
def execute_subscription(sessions: Sessions, plan: SubscriptionPlan) -> None:
    """Apply a subscription plan; a plan that changes nothing does nothing.

    Subscribing is confirmed by re-reading LSUB (``ImapSession.subscribe``),
    so a server that answers OK without acting on it raises here.
    """
    if not plan.changes:
        return

    imap = _imap(sessions)

    if plan.subscribe:
        imap.subscribe(plan.folder)

    else:
        imap.unsubscribe(plan.folder)


# ############################################################################
# Backups
# ############################################################################


@dataclass(frozen=True)
class BackupPlan:
    """The active script, and where a copy of it would be written."""

    script: str
    source: str
    target: Path


# ----------------------------------------------------------------------------
def plan_backup(
    sessions: Sessions, config: Config, output: str | None = None
) -> BackupPlan:
    """Fetch the active script and resolve where its copy goes."""
    sieve = _sieve(sessions)
    name = sieve.active_script_name()

    if not name:
        raise MxFilterError(
            "no active script on the server, so there is nothing to back "
            "up. 'mxfilter list' shows what the account has."
        )

    source = sieve.get_script(name)
    target = resolve_backup_target(output, name, config.backup_dir)

    return BackupPlan(name, source, target)


# ----------------------------------------------------------------------------
def execute_backup(plan: BackupPlan) -> Path:
    """Write the server's exact bytes to the planned target."""
    return write_backup(plan.source, plan.target)


# ----------------------------------------------------------------------------
def count_rules(source: str) -> int | None:
    """Return how many rules a script holds, or None if it will not parse.

    A script too broken to parse is the one most worth backing up, so this
    reports rather than raises.
    """
    try:
        return len(rule_names(parse_script(source)))

    except MxFilterError:
        return None


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
        raise MxFilterError(
            f"could not read backup {source} -- {exc}"
        ) from exc

    if not text.strip() and not allow_empty:
        raise MxFilterError(
            f"backup {source} is empty; restoring it would remove every "
            f"rule from the script. Pass --allow-empty if that is what you "
            f"want."
        )

    return BackupFile(source, text)


# ----------------------------------------------------------------------------
def plan_restore(
    sessions: Sessions,
    backup: BackupFile,
    script: str | None = None,
    activate: bool = False,
) -> RestorePlan:
    """Work out replacing a script with a backup, without doing it.

    The target is ``script``, or the active script when none is named; no
    other stored script is touched, and whether the target ends up active
    follows the same rule as every other upload (``activates``). The
    current script may be one mxfilter cannot parse: restore does not
    merge, and the current bytes are backed up before anything is sent,
    so overwriting it loses nothing (ADR 0005).
    """
    source, after = backup.path, backup.text
    name, before, active = fetch_active(sessions, script)

    # Not a guess at a name: with nothing active there is no "the script"
    # to mean, and the recovery case is served by naming one, which is
    # then activated because nothing else runs.
    if script is None and active is None:
        raise MxFilterError(
            "no active script on the server to restore over. Name the "
            "script to restore with --script NAME; with nothing active it "
            "is activated. 'mxfilter list' shows what the account has."
        )

    return RestorePlan(
        source=source,
        script=name,
        before=before,
        after=after,
        diff=DisplayDiff(script_diff(before, after, name), reformats=False),
        active=active,
        activate=activates(name, active, activate),
    )


# ----------------------------------------------------------------------------
def execute_restore(
    sessions: Sessions,
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
        _sieve(sessions),
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
def reject_actions(requested: Iterable[str]) -> None:
    """Refuse actions this tool will not generate, and say why.

    ``redirect`` is refused because MXRoute has publicly disabled it -- a
    policy, so the alternative is named. The rest are simply not
    implemented here, and mxfilter has no evidence either way about whether
    this server supports them.
    """
    requested = set(requested)

    for name, explanation in MXROUTE_FORBIDDEN_ACTIONS.items():
        if name in requested:
            raise MxFilterError(explanation)

    for name, label in UNIMPLEMENTED_ACTIONS.items():
        if name in requested:
            raise MxFilterError(
                f"mxfilter does not generate the Sieve '{label}' action. "
                f"This is a conservative choice of ours, not a documented "
                f"MXRoute restriction -- the MXRoute control panel is where "
                f"this feature lives if you need it. To see whether the "
                f"server advertises the extension at all, run "
                f"'mxfilter test'."
            )


# ----------------------------------------------------------------------------
def sieve_actions(spec: ActionSpec, folder: str, use_create: bool) -> list:
    """Build the sievelib action tuples for the requested actions.

    Flags are emitted before ``fileinto`` so the delivered copy carries
    them, and ``stop`` last so later rules do not also fire.
    """
    actions: list[tuple] = []

    for flag in spec.flags:
        actions.append(("addflag", escape_sieve_string(flag)))

    if spec.discard:
        actions.append(("discard",))

    elif folder:
        if use_create:
            actions.append(
                ("fileinto", ":create", escape_sieve_string(folder))
            )

        else:
            actions.append(("fileinto", escape_sieve_string(folder)))

    if spec.keep:
        actions.append(("keep",))

    if not actions:
        raise MxFilterError(
            "no action requested -- use --fileinto, --discard, --mark-read, "
            "--flag, or --keep"
        )

    if spec.stop:
        actions.append(("stop",))

    return actions


# ----------------------------------------------------------------------------
def required_extensions(
    spec: ActionSpec, folder: str, use_create: bool
) -> set[str]:
    """Return the Sieve extensions the generated rule will need.

    ``folder`` is the resolved target, so a folder that came from
    ``Config.default_folder`` rather than ``spec.fileinto`` counts too.
    """
    needed = set()

    if spec.flags:
        needed.add("imap4flags")

    if not spec.discard and folder:
        needed.add("fileinto")

    if use_create:
        needed.add("mailbox")

    return needed


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
    the delimiter from, so the Maildir++ heuristic was used instead.

    ``case_variants`` holds existing folders that differ from ``folder``
    only in case. Folder names are case-sensitive, so none of them is the
    target -- but a missing or to-be-created folder with one beside it is
    most likely a typo, and the front-end should say so (#56).
    """

    requested: str
    folder: str
    delimiter: str
    delimiter_assumed: bool
    status: str
    subscribe: bool
    case_variants: tuple[str, ...] = ()

    # ------------------------------------------------------------------------
    @property
    def use_create(self) -> bool:
        """Whether the rule should say ``fileinto :create``."""
        return self.status == FOLDER_SIEVE_CREATES


# ----------------------------------------------------------------------------
def plan_folder(
    sessions: Sessions,
    config: Config,
    requested: str | None,
    *,
    create: bool = False,
    subscribe: bool = True,
    delimiter: str | None = None,
) -> FolderPlan:
    """Normalize the target folder and decide how it gets to exist.

    ``:create`` is preferred when the server advertises ``mailbox``, since
    then Sieve makes the folder at delivery time. Otherwise the folder is
    made over IMAP by :func:`create_folder`, and the rule stays a plain
    ``fileinto``. Read-only: nothing is created here, and a folder that
    cannot be created is reported as such for :func:`check_folder` to
    refuse, so the front-end can show the plan first.

    With no ManageSieve session (the existing-mail pass alone) the Sieve
    route is simply unavailable.
    """
    requested = requested or config.default_folder or ""
    imap = sessions.imap

    if not requested:
        return FolderPlan("", "", "", False, FOLDER_NONE, subscribe)

    if imap is None:
        assumed = delimiter or "."
        folder = normalize_folder(requested, assumed, None)

    else:
        assumed = ""
        folder = imap.normalize(requested)

    shape = {
        "requested": requested,
        "folder": folder,
        "delimiter": assumed or imap.delimiter,
        "delimiter_assumed": imap is None,
        "subscribe": subscribe,
    }

    if imap is not None and imap.exists(folder):
        return FolderPlan(status=FOLDER_EXISTS, **shape)

    if imap is not None:
        shape["case_variants"] = tuple(imap.case_variants(folder))

    has_mailbox = sessions.sieve is not None and not (
        sessions.sieve.missing_extensions({"mailbox"})
    )

    if not create:
        return FolderPlan(status=FOLDER_MISSING, **shape)

    if has_mailbox:
        return FolderPlan(status=FOLDER_SIEVE_CREATES, **shape)

    if imap is None:
        return FolderPlan(status=FOLDER_UNCREATABLE, **shape)

    return FolderPlan(status=FOLDER_IMAP_CREATE, **shape)


# ----------------------------------------------------------------------------
def check_folder(plan: FolderPlan) -> None:
    """Refuse a folder that was asked to be created and cannot be."""
    if plan.status == FOLDER_UNCREATABLE:
        raise MxFilterError(
            f"the server does not advertise the Sieve 'mailbox' extension "
            f"and --no-imap was given, so {plan.folder!r} cannot be created"
        )


# ----------------------------------------------------------------------------
def create_folder(sessions: Sessions, plan: FolderPlan) -> FolderCreation:
    """Create the planned folder over IMAP, subscribing unless declined."""
    if plan.status != FOLDER_IMAP_CREATE:
        raise MxFilterError(
            f"folder {plan.folder!r} is not planned for IMAP creation "
            f"({plan.status})"
        )

    return _imap(sessions).create_folder(plan.folder, subscribe=plan.subscribe)


# ----------------------------------------------------------------------------
def folder_pending(sessions: Sessions, plan: FolderPlan) -> bool:
    """Whether a folder planned for IMAP creation has not been made yet."""
    return plan.status == FOLDER_IMAP_CREATE and not _imap(sessions).exists(
        plan.folder
    )


# ----------------------------------------------------------------------------
def realize_folder(
    sessions: Sessions, plan: FolderPlan, on_event: EventSink | None = None
) -> FolderCreation | None:
    """Create a folder planned for IMAP creation, once.

    Returns None, and does nothing, for any other plan or for a folder an
    earlier execute step already made -- ``add`` creates it before the
    upload, and its existing-mail pass must not try again.
    """
    if not folder_pending(sessions, plan):
        return None

    result = create_folder(sessions, plan)

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
    sessions: Sessions, requested: str | None = None
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

    ``DEFAULT_SCRIPT_NAME`` is used only when the account has no scripts at
    all, which is a genuine first run with nothing to collide with.
    """
    sieve = _sieve(sessions)
    active = sieve.active_script_name()
    name = requested or active or DEFAULT_SCRIPT_NAME

    _active, others = sieve.list_scripts()

    if name == active or name in others:
        return (name, sieve.get_script(name), active)

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
    sessions: Sessions, spec: ActionSpec, folder: FolderPlan
) -> list[str]:
    """Return the extensions the rule needs that the server does not list."""
    needed = required_extensions(spec, folder.folder, folder.use_create)

    return _sieve(sessions).missing_extensions(needed)


# ----------------------------------------------------------------------------
def placement_analysis(
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
    the indexes out by one, since ``resolve_position`` counts the other
    rules only.
    """
    present = read_rule_list(parse_script(before))
    rules = [entry for entry in present if entry.name != name]

    stops = any(action[0] == "stop" for action in actions)
    action_names = tuple(action[0] for action in actions)

    candidate = rule_from_criteria(name, criteria, action_names, stops=stops)

    # Resolved against every name in the script, including the one being
    # replaced -- that is how "no placement, so leave it where it is" finds
    # where it currently is.
    #
    # NOT rule_from_criteria's index=-1 default: analyze_placement clamps
    # with max(0, at_index), so a -1 here would mean the FRONT of the
    # script rather than the end of it.
    at_index = resolve_position(
        [entry.name for entry in present], placement, name
    )

    return analyze_placement(rules, candidate, at_index=at_index)


# ----------------------------------------------------------------------------
def plan_rule(
    sessions: Sessions, request: RuleRequest, folder: FolderPlan
) -> RulePlan:
    """Merge a rule into the active script without uploading it.

    Never overwrites: the rule is merged into the parsed existing script,
    and a parse failure is raised rather than fallen back from (ADR 0002).
    """
    request.criteria.require_terms()
    check_folder(folder)

    actions = sieve_actions(request.actions, folder.folder, folder.use_create)
    name = request.name or default_rule_name(request.criteria)
    script, before, active = fetch_active(sessions, request.script)

    after = merge_rule(
        before,
        name,
        request.criteria.sieve_conditions(),
        actions,
        matchtype=request.criteria.sieve_matchtype(),
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
            before, name, request.criteria, actions, request.placement
        ),
        diff=display_diff(before, after, script),
        folder=folder,
        active=active,
        activate=activates(script, active, request.activate),
    )


# ----------------------------------------------------------------------------
def plan_removal(
    sessions: Sessions,
    rule: str,
    script: str | None = None,
    activate: bool = False,
) -> RemovalPlan:
    """Take a named rule out of the script without uploading the result."""
    name, before, active = fetch_active(sessions, script)

    if not before.strip():
        raise MxFilterError(f"script {name!r} is empty")

    after = remove_rule(before, rule)

    return RemovalPlan(
        rule,
        name,
        before,
        after,
        display_diff(before, after, name),
        active,
        activates(name, active, activate),
    )


# ----------------------------------------------------------------------------
def plan_move(
    sessions: Sessions,
    rule: str,
    placement: Placement,
    script: str | None = None,
    activate: bool = False,
) -> MovePlan:
    """Reorder a named rule without restating it, and without uploading."""
    name, before, active = fetch_active(sessions, script)

    if not before.strip():
        raise MxFilterError(f"script {name!r} is empty")

    after = move_rule(before, rule, placement)

    present = read_rule_list(parse_script(before))
    names = [entry.name for entry in present]
    from_index = names.index(rule)
    to_index = resolve_position(names, placement, rule)

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
        diff=display_diff(before, after, name),
        active=active,
        activate=activates(name, active, activate),
    )


# ----------------------------------------------------------------------------
def upload_script(
    sieve: SieveSession,
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

    path = backup_script(before, name, config.backup_dir)
    emit(ScriptBackedUp(name, path))

    sieve.check_script(after)

    if before_put:
        before_put()

    sieve.put_script(name, after)

    if activate:
        sieve.set_active(name)

    emit(ScriptUploaded(name, activate))

    return path


# ----------------------------------------------------------------------------
def execute_script_change(
    sessions: Sessions,
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
        folder = plan.folder

        def before_put():
            realize_folder(sessions, folder, on_event)

    return upload_script(
        _sieve(sessions),
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
        raise MxFilterError(
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
def source_folder(sessions: Sessions, name: str) -> str:
    """Normalize the folder the existing-mail pass reads from.

    The same normalization the target gets, so ``--folder Lists/X`` and
    ``--fileinto Lists/X`` are recognised as one folder, and the search
    selects the server's real name for it.
    """
    return _imap(sessions).normalize(name)


# ----------------------------------------------------------------------------
def plan_mail(
    sessions: Sessions,
    criteria: Criteria,
    spec: ActionSpec,
    source: str,
    destination: str,
) -> MailActionPlan:
    """Search ``source`` read-only and work out what would be done."""
    criteria.require_terms()

    return _imap(sessions).plan_actions(
        criteria,
        source=source,
        destination=destination,
        flags=list(spec.flags),
        discard=spec.discard,
    )


# ----------------------------------------------------------------------------
def check_message_cap(plan: MailActionPlan, max_messages: int) -> None:
    """Refuse a plan larger than the cap, whole.

    Processing the first N and reporting success would read as "it handled
    everything", so a partial batch is never processed.
    """
    if plan.count <= max_messages:
        return

    raise MxFilterError(
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
    sessions: Sessions,
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
        realize_folder(sessions, folder, on_event)

    return _imap(sessions).execute(plan)


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
    sessions: Sessions,
    folder: str,
    uid: int | None = None,
    search: str | None = None,
) -> PickedMessage:
    """Fetch one message's headers, by UID or by the newest search match."""
    imap = _imap(sessions)
    candidates = 1

    if uid is None:
        uids = imap.raw_search(folder, search or "")

        if not uids:
            raise MxFilterError(f"no message in {folder!r} matched {search!r}")

        candidates = len(uids)
        uid = max(uids)

    headers = imap.fetch_message_headers(folder, uid)

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
    sessions: Sessions,
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
        raise MxFilterError(
            "give criteria or a raw IMAP search expression, not both"
        )

    if limit is not None and limit < 1:
        raise MxFilterError(
            f"the message limit must be at least 1, not {limit}"
        )

    imap = _imap(sessions)
    folder = imap.normalize(folder)

    messages, more = imap.list_messages(
        folder, criteria=criteria, expression=search, limit=limit
    )

    return MessageListing(folder, messages, more)


# ----------------------------------------------------------------------------
def read_message(sessions: Sessions, folder: str, uid: int) -> MessageContent:
    """Fetch one message whole and decode it, without marking it read.

    The folder is selected read-only and the body fetched with
    ``BODY.PEEK[]``, so \\Seen is left exactly as it was. Nothing is
    written to disk: attachments are described, never saved.
    """
    if uid < 1:
        raise MxFilterError(f"message UIDs start at 1, not {uid}")

    imap = _imap(sessions)
    folder = imap.normalize(folder)

    source, flags = imap.fetch_message_source(folder, uid)

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
