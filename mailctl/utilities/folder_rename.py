"""A folder renamed, and the rules that file into it repointed (#5).

Three writes, not one. RENAME moves the folder and every folder under it,
but leaves subscriptions where they were (RFC 3501 section 6.3.5), so the
new names are subscribed and the old ones dropped as a step of their own;
without it the renamed folder vanishes from webmail. And every rule filing
into an old name is repointed, as an edit of that one string, so the rest
of the script stays byte for byte as it was.

The order keeps the window in which a rule points at a missing folder as
short as it can be: the rewritten script is backed up and validated
(CHECKSCRIPT) first, then the folder is renamed and its subscriptions
fixed, and the script is stored straight after. A write that fails is
never retried; the error says what state the account is left in and how
to put it back. What landed is then read back, not assumed.
"""

from dataclasses import dataclass
from pathlib import Path

from .. import MailctlError
from ..config import Config
from ..engine import Session
from ..providers.base import DisplayDiff, FolderListing, same_folder
from .events import (
    EventSink,
    FolderRenamed,
    ScriptBackedUp,
    SubscriptionChanged,
)
from .folders import case_variant_hint
from .scripts import activates, fetch_active, upload_script

# ############################################################################
# The plan
# ############################################################################


@dataclass(frozen=True)
class FolderMove:
    """One folder RENAME moves, and whether it was subscribed before."""

    old: str
    new: str
    subscribed: bool


@dataclass(frozen=True)
class RuleRetarget:
    """One filing action repointed from an old folder name to its new one."""

    rule: str
    old: str
    new: str


@dataclass(frozen=True)
class FolderRenamePlan:
    """A folder rename worked out, nothing changed yet.

    ``moves`` is the folder itself first, then every folder under it, which
    the server renames with it. ``messages`` is what the folder holds now,
    for the check afterwards. ``missing_parents`` are levels above the new
    name that do not exist; RENAME is expected to create them (RFC 3501
    section 6.3.5, a SHOULD).

    ``before`` and ``after`` are the active script as stored and as it
    will be; they are equal when no rule files into a folder that moves,
    and then the script is neither validated nor uploaded.
    """

    requested_old: str
    requested_new: str
    delimiter: str
    moves: tuple[FolderMove, ...]
    messages: int
    missing_parents: tuple[str, ...]
    retargets: tuple[RuleRetarget, ...]
    script: str
    before: str
    after: str
    diff: DisplayDiff
    active: str | None
    activate: bool

    # ------------------------------------------------------------------------
    @property
    def old(self) -> str:
        return self.moves[0].old

    # ------------------------------------------------------------------------
    @property
    def new(self) -> str:
        return self.moves[0].new

    # ------------------------------------------------------------------------
    @property
    def subscribed(self) -> bool:
        """Whether the folder itself is subscribed now."""
        return self.moves[0].subscribed

    # ------------------------------------------------------------------------
    @property
    def children(self) -> tuple[FolderMove, ...]:
        """The folders under it, which move with it."""
        return self.moves[1:]

    # ------------------------------------------------------------------------
    @property
    def rules_change(self) -> bool:
        return self.after != self.before

    # ------------------------------------------------------------------------
    @property
    def renames(self) -> dict[str, str]:
        """Every old name and the name it becomes."""
        return {move.old: move.new for move in self.moves}


# ----------------------------------------------------------------------------
def _under(folder: str, parent: str, delimiter: str) -> bool:
    """Whether ``folder`` sits somewhere below ``parent``."""
    return bool(delimiter) and folder.startswith(parent + delimiter)


# ----------------------------------------------------------------------------
def _check_names(
    listing: FolderListing, old: str, new: str, requested: str
) -> None:
    """Refuse a rename the server would refuse, or that is a mistake."""
    delimiter = listing.delimiter

    if same_folder(old, "INBOX"):
        raise MailctlError(
            "INBOX cannot be renamed: IMAP moves its messages into the new "
            "folder and leaves an empty INBOX behind (RFC 3501 section "
            "6.3.5), which is not a rename.",
            code="rename_inbox",
            fields={"operation": "filter apply"},
        )

    if not listing.exists(old):
        raise MailctlError(
            (
                f"no folder named {old!r} on the server, so there is "
                f"nothing to rename. "
                f"{case_variant_hint(listing.case_variants(old))}"
            ).rstrip(),
            code="no_such_folder",
            fields={"operation": "folder list"},
        )

    if same_folder(old, new):
        raise MailctlError(f"{requested!r} is already named {old!r}")

    if _under(new, old, delimiter):
        raise MailctlError(
            f"cannot rename {old!r} to {new!r}: a folder cannot be moved "
            f"inside itself"
        )

    if listing.exists(new):
        raise MailctlError(
            f"not renaming {old!r}: {new!r} already exists. Rename or "
            f"remove it first; merging two folders is not a rename."
        )

    # A case-only rename ('Lists' to 'lists') has the old name as its one
    # variant, and is what was asked for.
    variants = [
        name
        for name in listing.case_variants(new)
        if not same_folder(name, old)
    ]

    if variants:
        raise MailctlError(
            f"not renaming {old!r} to {new!r}: {case_variant_hint(variants)}"
            f"The new name would put a second folder beside "
            f"{'it' if len(variants) == 1 else 'them'}."
        )


# ----------------------------------------------------------------------------
def plan_folder_rename(
    session: Session, old_name: str, new_name: str
) -> FolderRenamePlan:
    """Work out renaming a folder and repointing its rules; change nothing.

    Both names are normalized like every other folder name, so one copied
    out of the folder listing works and a new one goes where the server
    puts new folders. Every folder under the old one moves with it, and
    every rule in the active script filing into any of them is repointed.
    """
    transport = session.transport
    dialect = session.dialect
    listing = transport.list_folders()
    delimiter = listing.delimiter
    old = dialect.normalize(old_name, listing)
    new = dialect.normalize(new_name, listing)

    _check_names(listing, old, new, new_name)

    moves = [FolderMove(old, new, listing.is_subscribed(old))]
    moves += [
        FolderMove(
            folder, new + folder[len(old) :], listing.is_subscribed(folder)
        )
        for folder in sorted(listing.folders)
        if _under(folder, old, delimiter)
    ]

    taken = [move.new for move in moves[1:] if listing.exists(move.new)]

    if taken:
        raise MailctlError(
            f"not renaming {old!r}: folders it would move a subfolder onto "
            f"already exist ({', '.join(repr(name) for name in taken)})"
        )

    parts = new.split(delimiter) if delimiter else [new]
    parents = [delimiter.join(parts[:depth]) for depth in range(1, len(parts))]

    name, before, active = fetch_active(session)
    renames = {move.old: move.new for move in moves}
    references = dialect.folder_references(before) if before.strip() else []
    retargets = tuple(
        RuleRetarget(
            reference.rule, reference.folder, renames[reference.folder]
        )
        for reference in references
        if reference.folder in renames
    )
    after = dialect.retarget_folders(before, renames) if retargets else before

    return FolderRenamePlan(
        requested_old=old_name,
        requested_new=new_name,
        delimiter=delimiter,
        moves=tuple(moves),
        messages=transport.message_count(old),
        missing_parents=tuple(
            parent for parent in parents if not listing.exists(parent)
        ),
        retargets=retargets,
        script=name,
        before=before,
        after=after,
        diff=dialect.raw_diff(before, after, name),
        active=active,
        activate=activates(name, active, False),
    )


# ############################################################################
# Carrying it out
# ############################################################################


@dataclass(frozen=True)
class PostCondition:
    """One fact about the account after a rename, read back from it.

    ``operation`` names the command that would set a failed check right,
    and ``arguments`` what it is given; a front-end offers it in its own
    terms. Empty where there is none.
    """

    label: str
    ok: bool
    detail: str = ""
    operation: str = ""
    arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class FolderRenameResult:
    """What a rename did, and what reading the account back found.

    ``subscribed`` and ``unsubscribed`` are the subscription changes made
    after RENAME; ``subscription_errors`` the ones that failed, which do
    not stop the rename: the folder has moved, and its rules still need
    to follow it. ``backup`` is None when no rule changed.
    """

    plan: FolderRenamePlan
    backup: Path | None
    subscribed: tuple[str, ...]
    unsubscribed: tuple[str, ...]
    subscription_errors: tuple[str, ...]
    checks: tuple[PostCondition, ...]

    # ------------------------------------------------------------------------
    @property
    def ok(self) -> bool:
        """Whether every check afterwards held."""
        return all(check.ok for check in self.checks)


# ----------------------------------------------------------------------------
def _rules_left(plan: FolderRenamePlan) -> str:
    count = len({retarget.rule for retarget in plan.retargets})

    return f"{count} rule{'s' if count != 1 else ''}"


# ----------------------------------------------------------------------------
def _interrupted(
    session: Session,
    plan: FolderRenamePlan,
    error: MailctlError,
    renamed: bool,
    backup: Path | None,
) -> MailctlError:
    """The error for a rename stopped part-way: what failed, what state the
    account is in, and how to finish or undo it. Nothing is retried."""
    saved = f" The script was backed up to {backup}." if backup else ""

    if not renamed:
        return MailctlError(
            f"{error}\nNothing was renamed, and the script on the server "
            f"is as it was.{saved}"
        )

    try:
        stored = session.transport.read_rule_set(plan.script) == plan.after

    except MailctlError:
        stored = None

    if stored:
        before = (
            f"{error}\n{plan.old!r} was renamed to {plan.new!r} and the "
            f"rewritten script was stored as {plan.script!r}, but it could "
            f"not be activated."
        )

        return MailctlError(
            f"{before}{saved}",
            code="rename_not_activated",
            fields={
                "before": before,
                "saved": saved,
                "operation": "filterset list",
            },
        )

    state = (
        "is still the old one"
        if stored is False
        else "could not be read back to tell"
    )

    before = (
        f"{error}\n{plan.old!r} was renamed to {plan.new!r}, but the script "
        f"{plan.script!r} {state}: {_rules_left(plan)} still file into the "
        f"old name, so mail they match cannot be filed there.{saved}"
    )

    return MailctlError(
        f"{before} To undo it, rename {plan.new!r} back to {plan.old!r}; "
        f"then the rename can be tried again.",
        code="rename_interrupted",
        fields={
            "before": before,
            "operation": "folder rename",
            "arguments": (plan.new, plan.old),
        },
    )


# ----------------------------------------------------------------------------
def execute_folder_rename(
    session: Session,
    config: Config,
    plan: FolderRenamePlan,
    on_event: EventSink | None = None,
) -> FolderRenameResult:
    """Rename the folder, fix its subscriptions, and upload the script.

    Through ``scripts.upload_script``: the script is backed up and
    validated before anything else, the folder is renamed and its
    subscriptions carried over once the server has accepted the script,
    and the script is stored straight after. With no rule to repoint the
    script is left alone and only the folder half runs.

    A failed subscription is recorded, not raised. Any other failure
    raises, naming the state the account is left in; nothing is retried.
    The account is then read back (:func:`verify_folder_rename`).
    """
    emit = on_event or (lambda event: None)
    transport = session.transport
    subscribed: list[str] = []
    unsubscribed: list[str] = []
    errors: list[str] = []
    renamed = False
    backup: Path | None = None

    def record(event: object) -> None:
        nonlocal backup

        if isinstance(event, ScriptBackedUp):
            backup = event.path

        emit(event)

    def move_folders() -> None:
        nonlocal renamed

        transport.rename_folder(plan.old, plan.new)
        renamed = True
        emit(FolderRenamed(plan.old, plan.new, len(plan.children)))

        listing = transport.list_folders()

        for move in plan.moves:
            if move.subscribed and not listing.is_subscribed(move.new):
                try:
                    transport.subscribe(move.new)
                    subscribed.append(move.new)
                    emit(SubscriptionChanged(move.new, True))

                except MailctlError as exc:
                    errors.append(str(exc))

            if listing.is_subscribed(move.old):
                try:
                    transport.unsubscribe(move.old)
                    unsubscribed.append(move.old)
                    emit(SubscriptionChanged(move.old, False))

                except MailctlError as exc:
                    errors.append(str(exc))

    try:
        if plan.rules_change:
            upload_script(
                session,
                config,
                plan.script,
                plan.before,
                plan.after,
                record,
                move_folders,
                activate=plan.activate,
            )

        else:
            move_folders()

    except MailctlError as exc:
        raise _interrupted(session, plan, exc, renamed, backup) from exc

    return FolderRenameResult(
        plan=plan,
        backup=backup,
        subscribed=tuple(subscribed),
        unsubscribed=tuple(unsubscribed),
        subscription_errors=tuple(errors),
        checks=verify_folder_rename(session, plan),
    )


# ############################################################################
# Reading it back
# ############################################################################


# ----------------------------------------------------------------------------
def verify_folder_rename(
    session: Session, plan: FolderRenamePlan
) -> tuple[PostCondition, ...]:
    """Read the account back and say whether the rename landed.

    Not merely that the new folder exists: that it is subscribed where the
    old one was (webmail draws the subscription list, not the folder
    list), holds at least the messages the old one did, took the folders
    under it along, and that no rule in the script still files into an
    old name. Read-only; a read that fails is a failed check, not an
    error, so every other check is still reported.
    """
    transport = session.transport
    checks: list[PostCondition] = []

    try:
        listing = transport.list_folders()

    except MailctlError as exc:
        return (
            PostCondition("the folder list could be read", False, str(exc)),
        )

    old, new = plan.old, plan.new

    checks.append(PostCondition(f"{old!r} is gone", not listing.exists(old)))
    checks.append(PostCondition(f"{new!r} exists", listing.exists(new)))

    if plan.subscribed:
        label = f"{new!r} is subscribed, as {old!r} was"

        checks.append(
            PostCondition(label, True)
            if listing.is_subscribed(new)
            else PostCondition(
                label,
                False,
                "webmail will not show it",
                operation="folder subscribe",
                arguments=(new,),
            )
        )

    checks.append(
        PostCondition(
            f"{old!r} is off the subscription list",
            not listing.is_subscribed(old),
        )
    )

    if plan.children:
        missing = [
            move.new for move in plan.children if not listing.exists(move.new)
        ]
        hidden = [
            move.new
            for move in plan.children
            if move.subscribed and not listing.is_subscribed(move.new)
        ]
        count = len(plan.children)

        checks.append(
            PostCondition(
                f"the {count} folder{'s' if count != 1 else ''} under it "
                f"moved with it",
                not missing,
                ", ".join(f"{name!r} is missing" for name in missing),
            )
        )
        checks.append(
            PostCondition(
                "those subscribed before are subscribed now",
                not hidden,
                ", ".join(f"{name!r} is not" for name in hidden),
            )
        )

    if listing.exists(new):
        try:
            count = transport.message_count(new)

            checks.append(
                PostCondition(
                    f"{new!r} holds {count} message"
                    f"{'s' if count != 1 else ''}; {old!r} held "
                    f"{plan.messages}",
                    count >= plan.messages,
                    "" if count >= plan.messages else "messages are missing",
                )
            )

        except MailctlError as exc:
            checks.append(
                PostCondition(f"{new!r}'s message count", False, str(exc))
            )

    renames = plan.renames

    try:
        left = sorted(
            {
                reference.rule
                for reference in session.dialect.folder_references(
                    transport.read_rule_set(plan.script)
                )
                if reference.folder in renames
            }
        )

        checks.append(
            PostCondition(
                f"no rule in {plan.script!r} files into {old!r} or a folder "
                f"under it",
                not left,
                ", ".join(f"{rule!r} still does" for rule in left),
            )
        )

    except MailctlError as exc:
        checks.append(
            PostCondition(
                f"the rules in {plan.script!r} could be read", False, str(exc)
            )
        )

    return tuple(checks)
