"""Marking messages: setting and clearing read, flagged, and keywords.

The write twin of reading a message. :func:`plan_mark` is read-only -- the
folder is examined and only the flags are read, so looking does not mark
anything read -- and says, per message, what would change;
:func:`execute_mark` carries a plan out as at most two writes, one adding
flags and one removing them, never one call told which to do.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from .. import MailctlError
from ..engine import Session
from .rules import require_capability
from .uids import check_uidvalidity, require_pinnable

# The two system flags mark sets and clears by name, as IMAP spells them.
SEEN = "\\Seen"
FLAGGED = "\\Flagged"

# A keyword is an IMAP atom (RFC 3501 section 9, flag-keyword): printable
# ASCII, no space, and none of these atom-specials. A leading backslash
# would make it a system flag, which only the named options set.
ATOM_SPECIALS = frozenset('(){%*"\\]')


@dataclass(frozen=True)
class MessageMark:
    """What marking does to one message.

    ``flags`` is what it has now; ``add`` and ``remove`` are only the
    flags that would really change, so both are empty for a message that
    already looks as asked.
    """

    uid: int
    flags: tuple[str, ...]
    add: tuple[str, ...] = ()
    remove: tuple[str, ...] = ()

    # ------------------------------------------------------------------------
    @property
    def changes(self) -> bool:
        return bool(self.add or self.remove)


@dataclass(frozen=True)
class MarkPlan:
    """What marking ``uids`` in ``folder`` would change, worked out but not
    yet done. ``messages`` follows the order the UIDs were given in, and
    ``uidvalidity`` is what they were valid under, None where the host
    reports none."""

    folder: str
    add: tuple[str, ...]
    remove: tuple[str, ...]
    messages: list[MessageMark] = field(default_factory=list)
    uidvalidity: int | None = None

    # ------------------------------------------------------------------------
    @property
    def changing(self) -> list[MessageMark]:
        """The messages the plan would change."""
        return [message for message in self.messages if message.changes]

    # ------------------------------------------------------------------------
    @property
    def is_empty(self) -> bool:
        """Whether every message already looks as asked."""
        return not self.changing


@dataclass(frozen=True)
class MarkResult:
    """How many messages each write was sent for."""

    added: int = 0
    removed: int = 0


# ----------------------------------------------------------------------------
def check_keyword(keyword: str) -> str:
    """Return ``keyword`` if it can be stored as one; refuse it otherwise."""
    if keyword.startswith("\\"):
        raise MailctlError(
            f"{keyword} is a system flag, not a keyword; of the system "
            f"flags only \\Seen (read) and \\Flagged are set and cleared, "
            f"by name"
        )

    if not keyword or any(
        not "!" <= char <= "~" or char in ATOM_SPECIALS for char in keyword
    ):
        raise MailctlError(
            f"{keyword!r} cannot be a keyword: a keyword is one word of "
            f'printable ASCII, without spaces or any of ( ) {{ % * " ] \\'
        )

    return keyword


# ----------------------------------------------------------------------------
def mark_flags(
    read: bool | None = None,
    flagged: bool | None = None,
    add_keywords: Iterable[str] = (),
    remove_keywords: Iterable[str] = (),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The flags to add and to remove, from what a person asked for.

    ``read`` and ``flagged`` are True to set, False to clear, and None to
    leave alone. A flag asked to be both set and cleared is refused.
    """
    add: list[str] = []
    remove: list[str] = []

    for flag, wanted in ((SEEN, read), (FLAGGED, flagged)):
        if wanted is True:
            add.append(flag)

        elif wanted is False:
            remove.append(flag)

    add += [check_keyword(keyword) for keyword in add_keywords]
    remove += [check_keyword(keyword) for keyword in remove_keywords]

    add, remove = _unique(add), _unique(remove)
    both = [flag for flag in add if _key(flag) in {_key(r) for r in remove}]

    if both:
        raise MailctlError(
            f"cannot both set and clear {', '.join(both)}; ask for one"
        )

    if not add and not remove:
        raise MailctlError("nothing to mark", code="no_marks")

    return tuple(add), tuple(remove)


# ----------------------------------------------------------------------------
def plan_mark(
    session: Session,
    folder: str,
    uids: Iterable[int],
    add: Iterable[str] = (),
    remove: Iterable[str] = (),
    uidvalidity: int | None = None,
) -> MarkPlan:
    """Read the flags ``uids`` have in ``folder`` and plan what changes.

    Read-only: the folder is examined and nothing is marked read. A UID the
    folder does not hold refuses the whole plan, naming it, rather than
    marking the rest and reporting success. Refused before anything
    connects by a provider that does not declare ``mark``.

    ``uidvalidity`` is what the UIDs were valid under when they were
    listed; where the folder's is now another, the plan is refused before
    the UIDs are read (:func:`~mailctl.utilities.uids.check_uidvalidity`).
    """
    require_capability(session, "mark")
    require_pinnable(session, uidvalidity)

    add, remove = tuple(add), tuple(remove)
    wanted = _unique_uids(uids)

    if not wanted:
        raise MailctlError("name at least one message UID to mark")

    if not add and not remove:
        raise MailctlError("nothing to mark: no flag to set or clear")

    transport = session.transport
    folder = session.dialect.normalize(folder, transport.list_folders())
    validity = check_uidvalidity(session, folder, uidvalidity)

    held = {
        item.summary.uid: item.summary.flags
        for item in transport.fetch_summaries(wanted, folder)
    }
    missing = [uid for uid in wanted if uid not in held]

    if missing:
        raise MailctlError(
            f"no message with uid {', '.join(map(str, missing))} in "
            f"{folder!r}; nothing was marked"
        )

    return MarkPlan(
        folder,
        add,
        remove,
        [_mark(uid, held[uid], add, remove) for uid in wanted],
        validity,
    )


# ----------------------------------------------------------------------------
def execute_mark(session: Session, plan: MarkPlan) -> MarkResult:
    """Carry out an approved plan: add, then remove, each one write over
    the messages that need it. A plan that changes nothing sends nothing.

    The folder's UIDVALIDITY is checked again first, so UIDs renumbered
    since the plan was made are refused before either write.
    """
    require_capability(session, "mark")

    gaining = [message.uid for message in plan.messages if message.add]
    losing = [message.uid for message in plan.messages if message.remove]

    if (gaining or losing) and plan.uidvalidity is not None:
        check_uidvalidity(session, plan.folder, plan.uidvalidity)

    if gaining:
        session.transport.add_flags(plan.folder, gaining, list(plan.add))

    if losing:
        session.transport.remove_flags(plan.folder, losing, list(plan.remove))

    return MarkResult(added=len(gaining), removed=len(losing))


# ----------------------------------------------------------------------------
def _mark(
    uid: int,
    flags: tuple[str, ...],
    add: tuple[str, ...],
    remove: tuple[str, ...],
) -> MessageMark:
    """One message's change: only the flags it lacks, or has."""
    has = {_key(flag) for flag in flags}

    return MessageMark(
        uid,
        tuple(flags),
        tuple(flag for flag in add if _key(flag) not in has),
        tuple(flag for flag in remove if _key(flag) in has),
    )


# ----------------------------------------------------------------------------
def _key(flag: str) -> str:
    """IMAP compares flag names without regard to case."""
    return flag.casefold()


# ----------------------------------------------------------------------------
def _unique(flags: list[str]) -> list[str]:
    """``flags`` with case-insensitive repeats dropped, first kept."""
    kept: dict[str, str] = {}

    for flag in flags:
        kept.setdefault(_key(flag), flag)

    return list(kept.values())


# ----------------------------------------------------------------------------
def _unique_uids(uids: Iterable[int]) -> list[int]:
    """The UIDs in the order given, repeats dropped; below 1 refused."""
    result: list[int] = []

    for uid in uids:
        if uid < 1:
            raise MailctlError(f"message UIDs start at 1, not {uid}")

        if uid not in result:
            result.append(uid)

    return result
