"""Message UIDs kept between runs, checked against the folder's numbering.

A UID names one message only together with the folder's UIDVALIDITY (RFC
9051 section 2.3.1.1). A server that cannot keep a folder's UIDs --
because it was deleted and made again, or its store rebuilt -- gives it a
new UIDVALIDITY, and every UID from before may then name another message.
RFC 4549 section 4.1 has a client drop what it remembered and cancel what
it meant to do with those UIDs, which is what a refusal here does.

Only a provider declaring the ``uidvalidity`` capability can be checked; a
pin given to one that does not is refused rather than ignored.
"""

from collections.abc import Callable
from typing import TypeVar

from .. import MailctlError
from ..engine import Session
from .rules import require_capability

# UIDVALIDITY is a non-zero unsigned 32-bit number (RFC 9051 section 9,
# nz-number32).
MAX_UIDVALIDITY = 2**32 - 1

T = TypeVar("T")


# ----------------------------------------------------------------------------
def require_pinnable(session: Session, uidvalidity: int | None) -> None:
    """Refuse a pin the provider cannot check or no folder could have.

    Needs no connection, so a utility calls it before any other work.
    """
    if uidvalidity is None:
        return

    require_capability(session, "uidvalidity")

    if not 1 <= uidvalidity <= MAX_UIDVALIDITY:
        raise MailctlError(
            f"a UIDVALIDITY is a number from 1 to {MAX_UIDVALIDITY}, not "
            f"{uidvalidity}"
        )


# ----------------------------------------------------------------------------
def current_uidvalidity(session: Session, folder: str) -> int | None:
    """What ``folder``'s UIDs are valid under now; None where the provider
    has no such value or the host reported none. Read-only."""
    if not session.capabilities.uidvalidity:
        return None

    return session.transport.uidvalidity(folder)


# ----------------------------------------------------------------------------
def check_uidvalidity(
    session: Session, folder: str, expected: int | None
) -> int | None:
    """Refuse UIDs issued under ``expected`` when ``folder`` has another.

    ``expected`` None checks nothing and returns what the folder has now,
    for reporting. Otherwise the value the folder reports must equal it,
    and a host that reports none cannot vouch for the UIDs, so it is
    refused too. Read-only; called before the UIDs are used for anything.
    """
    if expected is None:
        return current_uidvalidity(session, folder)

    require_pinnable(session, expected)

    current = session.transport.uidvalidity(folder)

    if current is None:
        raise MailctlError(
            f"the server reports no UIDVALIDITY for {folder!r}, so UIDs "
            f"given as valid under {expected} cannot be checked; none was "
            f"used"
        )

    if current != expected:
        raise stale(folder, expected, current)

    return current


# ----------------------------------------------------------------------------
def read_pinned(
    session: Session,
    folder: str,
    uidvalidity: int | None,
    read: Callable[[], T],
) -> T:
    """Return ``read()``, a read of one UID in ``folder``, refusing it as
    stale where it failed and the UID was pinned to a UIDVALIDITY the
    folder no longer has.

    The read selects the folder, and the selection reports its
    UIDVALIDITY, so checking after it costs no round trip; the caller
    checks a read that succeeded. A failed one is checked here, so a UID
    lost in a renumbering is refused as stale, not as a missing message.
    """
    try:
        return read()

    except MailctlError:
        if uidvalidity is not None:
            check_uidvalidity(session, folder, uidvalidity)

        raise


# ----------------------------------------------------------------------------
def stale(folder: str, given: int, current: int) -> MailctlError:
    """The one refusal for UIDs from a numbering the folder no longer has.

    Returned rather than raised, so the traceback points at the caller.
    """
    return MailctlError(
        f"the UIDs for {folder!r} were valid under UIDVALIDITY {given}, but "
        f"the folder's is now {current}: the server has renumbered it, so "
        f"they may name other messages. None was used, and nothing was "
        f"changed.",
        code="uidvalidity_changed",
        fields={
            "folder": folder,
            "given": given,
            "current": current,
            "operation": "search",
        },
    )
