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
from pathlib import Path

from . import MxFilterError
from .config import Config
from .criteria import Criteria, escape_sieve_string
from .imap import (
    FolderCreation,
    ImapSession,
    MailActionPlan,
    MailActionResult,
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
    """The new script was validated, uploaded, and activated."""

    script: str


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
        return folder.casefold() in {
            name.casefold() for name in self.subscribed
        }

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

    if subscribe and not imap.exists(folder):
        raise MxFilterError(
            f"no folder named {folder!r} on the server, so there is nothing "
            f"to subscribe to. 'mxfilter folders' lists what exists."
        )

    if not subscribe and not subscribed_now and not imap.exists(folder):
        raise MxFilterError(
            f"no folder or subscription named {folder!r} on the server. "
            f"'mxfilter folders' lists what exists."
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
    """A backup file to be uploaded, whole, over the active script.

    ``diff`` is a raw diff, not the normalized one a merge shows: restore
    uploads the file's exact bytes, so any difference -- formatting
    included -- is a real change and is shown as one.
    """

    source: Path
    script: str
    before: str
    after: str
    diff: DisplayDiff

    # ------------------------------------------------------------------------
    @property
    def changes(self) -> bool:
        return self.before != self.after


# ----------------------------------------------------------------------------
def read_backup_file(path: str | Path) -> tuple[Path, str]:
    """Read a backup file exactly as written, newline translation off."""
    source = Path(path).expanduser()

    try:
        with source.open(encoding="utf-8", newline="") as handle:
            return source, handle.read()

    except (OSError, UnicodeDecodeError) as exc:
        raise MxFilterError(
            f"could not read backup {source} -- {exc}"
        ) from exc


# ----------------------------------------------------------------------------
def plan_restore(sessions: Sessions, path: str | Path) -> RestorePlan:
    """Work out replacing the active script with a backup, without doing it.

    Only the active script is ever the target, so no other stored script is
    touched. The current script may be one mxfilter cannot parse: restore
    does not merge, and the current bytes are backed up before anything is
    sent, so overwriting it loses nothing (ADR 0005).
    """
    source, after = read_backup_file(path)
    sieve = _sieve(sessions)
    name = sieve.active_script_name()

    if not name:
        raise MxFilterError(
            "no active script on the server to restore over. 'mxfilter "
            "list' shows what the account has."
        )

    before = sieve.get_script(name)

    return RestorePlan(
        source=source,
        script=name,
        before=before,
        after=after,
        diff=DisplayDiff(script_diff(before, after, name), reformats=False),
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
    """

    requested: str
    folder: str
    delimiter: str
    delimiter_assumed: bool
    status: str
    subscribe: bool

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


@dataclass(frozen=True)
class RemovalPlan:
    """A rule taken out of the script, not yet uploaded."""

    rule: str
    script: str
    before: str
    after: str
    diff: DisplayDiff


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

    # ------------------------------------------------------------------------
    @property
    def changes(self) -> bool:
        return self.from_index != self.to_index


# ----------------------------------------------------------------------------
def fetch_active(
    sessions: Sessions, requested: str | None = None
) -> tuple[str, str]:
    """Return ``(script_name, source)`` for the script to edit.

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
        return (name, sieve.get_script(name))

    return (name, "")


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
    script, before = fetch_active(sessions, request.script)

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
    )


# ----------------------------------------------------------------------------
def plan_removal(
    sessions: Sessions, rule: str, script: str | None = None
) -> RemovalPlan:
    """Take a named rule out of the script without uploading the result."""
    name, before = fetch_active(sessions, script)

    if not before.strip():
        raise MxFilterError(f"script {name!r} is empty")

    after = remove_rule(before, rule)

    return RemovalPlan(
        rule, name, before, after, display_diff(before, after, name)
    )


# ----------------------------------------------------------------------------
def plan_move(
    sessions: Sessions,
    rule: str,
    placement: Placement,
    script: str | None = None,
) -> MovePlan:
    """Reorder a named rule without restating it, and without uploading."""
    name, before = fetch_active(sessions, script)

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
) -> Path:
    """Back up, validate, upload, and activate a script.

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
    sieve.set_active(name)

    emit(ScriptUploaded(name))

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
