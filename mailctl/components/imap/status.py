"""Every folder's message counts, from one ``LIST`` (RFC 5819).

``LIST "" "*" RETURN (STATUS (MESSAGES UNSEEN))`` answers for every folder
in one round trip: a ``LIST`` line per folder, each selectable one followed
by its ``STATUS`` line. IMAPClient has no command for it, so the session
sends it through imaplib (``client.py``) and reads the ``STATUS`` lines
imaplib collected; this module builds the arguments and reads those lines,
offline. ``SIZE`` is asked for only where ``STATUS=SIZE`` (RFC 8438) is
advertised.
"""

from dataclasses import dataclass

from imapclient import imap_utf7
from imapclient.exceptions import ProtocolError
from imapclient.response_parser import parse_response
from imapclient.util import chunk

from ... import MailctlError

__all__ = [
    "LIST_STATUS",
    "STATUS_SIZE",
    "FolderStatus",
    "list_status_arguments",
    "parse_status",
]

# The capabilities, as a server advertises them.
LIST_STATUS = "LIST-STATUS"
STATUS_SIZE = "STATUS=SIZE"


@dataclass(frozen=True)
class FolderStatus:
    """One folder's counts, as its ``STATUS`` line gave them.

    ``folder`` is decoded from modified UTF-7, as IMAPClient decodes the
    names ``LIST`` returns, so the two compare equal. An item the line did
    not carry is None.
    """

    folder: str
    messages: int | None = None
    unseen: int | None = None
    size: int | None = None


# ----------------------------------------------------------------------------
def list_status_arguments(sizes: bool) -> tuple[str, ...]:
    """The arguments of a ``LIST`` over every folder returning STATUS."""
    items = "MESSAGES UNSEEN SIZE" if sizes else "MESSAGES UNSEEN"

    return ('""', '"*"', "RETURN", f"(STATUS ({items}))")


# ----------------------------------------------------------------------------
def parse_status(data: list) -> list[FolderStatus]:
    """Read the untagged ``STATUS`` responses imaplib collected.

    ``data`` is imaplib's form, the word ``STATUS`` already taken off: a
    line of bytes per response, or, where the mailbox name came as a
    literal, the pair ``(line up to the literal, literal)`` followed by
    the rest of the line.
    """
    lines = [item for item in data if item not in (b"", None)]

    try:
        parsed = parse_response(lines)

    except ProtocolError as exc:
        raise MailctlError(
            f"could not read a STATUS response -- {exc}"
        ) from exc

    if len(parsed) % 2:
        raise MailctlError(
            f"could not read a STATUS response -- expected a mailbox and "
            f"its items, got {parsed!r}"
        )

    return [_status(name, items) for name, items in chunk(parsed, 2)]


# ----------------------------------------------------------------------------
def _status(name, items) -> FolderStatus:
    """One mailbox name and its parenthesised items, as a record."""
    if not isinstance(items, tuple) or len(items) % 2:
        raise MailctlError(
            f"could not read the STATUS items for {name!r} -- got {items!r}"
        )

    values = {
        key.upper(): value
        for key, value in chunk(items, 2)
        if isinstance(key, bytes) and isinstance(value, int)
    }

    return FolderStatus(
        _mailbox(name),
        values.get(b"MESSAGES"),
        values.get(b"UNSEEN"),
        values.get(b"SIZE"),
    )


# ----------------------------------------------------------------------------
def _mailbox(name) -> str:
    """A mailbox name as the parser returned it, as ``LIST`` names it.

    The parser reads an unquoted all-digit name as an int, and the atom
    ``NIL`` as None; both are names here, as IMAPClient treats them.
    """
    if isinstance(name, bytes):
        return imap_utf7.decode(name)

    return "NIL" if name is None else str(name)
