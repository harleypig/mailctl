"""Messages as data: summaries, the existing-mail plan, and header reading.

Everything here is offline. A FETCH response goes in and plain values come
out, so the session and any front-end share one reading of a message.
"""

from dataclasses import dataclass, field
from email.errors import HeaderParseError
from email.header import Header, decode_header, make_header

from ... import MailctlError
from .folders import same_folder

# UIDs per IMAP command. Every UID goes into the command line, and servers
# cap line length -- RFC 7162 section 4 asks clients to keep command lines
# under 8192 octets. 250 UIDs of up to ten digits plus separators is about
# 2.7 KB, leaving room for a long folder name. Kept well below the default
# --max-messages (500) so the two stay independent: the cap is a policy on
# how much to touch, this is transport and never the user's concern.
BULK_CHUNK = 250

__all__ = [
    "BULK_CHUNK",
    "MailActionPlan",
    "MailActionResult",
    "MessageSummary",
    "PartialExecution",
    "chunked",
    "decode_header_value",
    "flag_names",
    "header_values",
    "structure_has_attachment",
    "summarize",
]


@dataclass(frozen=True)
class MessageSummary:
    """One matched message, as data.

    Deliberately carries no rendering of itself: how a message is displayed
    is the front-end's business, and a record that knows how to draw itself
    is the thing a second front-end has to work around.
    """

    uid: int
    date: str
    sender: str
    subject: str
    folder: str
    size: int = 0
    flags: tuple[str, ...] = ()
    has_attachments: bool = False


@dataclass(frozen=True)
class MailActionPlan:
    """What the existing-mail pass would do, worked out but not yet done.

    Produced by a read-only search, so building a plan is always safe. The
    caller decides whether to render it, confirm it, or execute it -- which
    is what makes a dry run a matter of not calling ``execute``, rather than
    of a flag changing what some deeper function prints.
    """

    source: str
    destination: str
    flags: list[str]
    discard: bool
    messages: list[MessageSummary] = field(default_factory=list)

    # ------------------------------------------------------------------------
    @property
    def count(self) -> int:
        """How many messages the plan covers."""
        return len(self.messages)

    # ------------------------------------------------------------------------
    @property
    def uids(self) -> list[int]:
        """The UIDs the plan would act on."""
        return [message.uid for message in self.messages]

    # ------------------------------------------------------------------------
    @property
    def moves(self) -> bool:
        """Whether executing this plan relocates mail."""
        return bool(
            self.destination
            and not self.discard
            and not same_folder(self.destination, self.source)
        )

    # ------------------------------------------------------------------------
    @property
    def is_empty(self) -> bool:
        """Whether the plan would do nothing at all."""
        return not self.messages


@dataclass(frozen=True)
class MailActionResult:
    """What executing a plan actually did."""

    flagged: int = 0
    moved: int = 0
    deleted: int = 0


class PartialExecution(MailctlError):
    """A chunked pass failed after some chunks were already applied.

    ``result`` counts what completed -- every chunk before the failing one
    was flagged and moved or deleted in full -- and ``total`` is the size
    of the whole plan.
    """

    # ------------------------------------------------------------------------
    def __init__(
        self,
        cause: Exception,
        result,
        total: int,
        fallback: bool,
        destination: str = "",
    ):
        self.result = result
        self.total = total

        done = result.moved or result.deleted or result.flagged

        # Whether a re-run is safe depends on the mode, and saying "safe"
        # where it is not is the dangerous error. MOVE is atomic per command
        # and a delete or flag re-applies harmlessly, so the search simply
        # finds what is left. The COPY fallback is the exception: a batch
        # can be copied and not yet expunged, so it still matches in the
        # source and a re-run copies it a second time.
        if fallback:
            advice = (
                f"The failing batch may have been copied to {destination!r} "
                f"but not yet removed from the source (this server has no "
                f"MOVE), so a message can appear in both. Re-running copies "
                f"them again: check {destination!r} for copies from that "
                f"batch first, or remove the duplicates afterwards."
            )

        else:
            advice = (
                "The failing batch may be partly applied. Re-running the "
                "same command is safe: it searches again, so mail already "
                "moved or deleted is not matched twice, and a flag already "
                "set stays set."
            )

        super().__init__(
            f"{cause} -- stopped part-way: {done} of {total} message(s) were "
            f"fully processed, in batches of {BULK_CHUNK}; later batches were "
            f"not touched. {advice}"
        )


# ----------------------------------------------------------------------------
def chunked(items: list, size: int = BULK_CHUNK):
    """Yield ``items`` in consecutive slices of at most ``size``."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


# ----------------------------------------------------------------------------
def decode_header_value(raw: "str | Header") -> str:
    """Decode RFC 2047 encoded words, falling back to the raw value."""
    try:
        return str(make_header(decode_header(raw)))

    # HeaderParseError is not a ValueError: a bad base64 encoded word
    # raises it, and one such header must not abort a whole listing.
    except (UnicodeDecodeError, LookupError, ValueError, HeaderParseError):
        return str(raw)


# ----------------------------------------------------------------------------
def header_values(message) -> dict[str, list[str]]:
    """Map upper-cased header names to every occurrence of that header.

    Each occurrence contributes both its decoded and its raw form. Sieve
    compares against the MIME-decoded value, so that is the one that
    matters; keeping the raw form as well means a search for the literal
    encoded text still finds its message, and costs only a wider candidate
    set.
    """
    collected: dict[str, list[str]] = {}

    for name, raw in message.items():
        key = name.upper()
        decoded = decode_header_value(_utf8_header(raw))

        values = collected.setdefault(key, [])
        values.append(decoded)

        if decoded != raw:
            values.append(raw)

    return collected


# ----------------------------------------------------------------------------
def _utf8_header(raw: "str | Header") -> "str | Header":
    """Read a raw 8-bit header as the UTF-8 it usually is.

    ``email`` hands a header holding 8-bit bytes back as a ``Header`` of
    ``unknown-8bit`` chunks, which render as replacement characters -- so a
    raw UTF-8 header (RFC 6532), the only way a non-ASCII address can
    appear, would never equal the text a search was for. A value that is
    not valid UTF-8 is returned as it came, and read as before.
    """
    if not isinstance(raw, Header):
        return raw

    try:
        return "".join(
            chunk.decode(
                "utf-8" if charset in (None, "unknown-8bit") else charset
            )
            if isinstance(chunk, bytes)
            else chunk
            for chunk, charset in decode_header(raw)
        )

    except (UnicodeError, LookupError):
        return raw


# ----------------------------------------------------------------------------
def flag_names(raw) -> tuple[str, ...]:
    """Turn a FETCH FLAGS response into plain strings."""
    return tuple(
        flag.decode("ascii", "replace") if isinstance(flag, bytes) else flag
        for flag in raw or ()
    )


# ----------------------------------------------------------------------------
def structure_has_attachment(node) -> bool:
    """Whether a parsed BODYSTRUCTURE holds a part that is not message text.

    The same line ``engine.parse_message`` draws on the full source: a
    text/plain or text/html part is text unless it is marked as an
    attachment or carries a file name; any other leaf -- a PDF, an inline
    image, an attached message -- is an attachment. Cheap because it reads
    only the structure the server already sent with the summary.
    """
    if not isinstance(node, tuple) or not node:
        return False

    if isinstance(node[0], list):
        return any(structure_has_attachment(part) for part in node[0])

    kind = (
        f"{_ascii(node[0])}/{_ascii(node[1])}".lower() if len(node) > 1 else ""
    )

    if kind not in ("text/plain", "text/html"):
        return True

    params = node[2] if len(node) > 2 and isinstance(node[2], tuple) else ()

    if any(_ascii(item).lower() == "name" for item in params[::2]):
        return True

    # The disposition's position shifts with the part type, but it is the
    # only extension field shaped (bytes, tuple-or-None).
    for item in node[7:]:
        if (
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], bytes)
            and (item[1] is None or isinstance(item[1], tuple))
        ):
            fields = item[1] or ()

            return _ascii(item[0]).lower() == "attachment" or any(
                _ascii(name).lower() == "filename" for name in fields[::2]
            )

    return False


# ----------------------------------------------------------------------------
def _ascii(value) -> str:
    """Render a BODYSTRUCTURE atom, which IMAPClient hands over as bytes."""
    if isinstance(value, bytes):
        return value.decode("ascii", "replace")

    return "" if value is None else str(value)


# ----------------------------------------------------------------------------
def summarize(uid: int, data: dict, message, folder: str) -> MessageSummary:
    """Build a summary from one FETCH response and its parsed headers.

    Items the FETCH did not ask for come back as defaults, so the
    existing-mail pass and the message listing share this one reading.
    """
    internal = data.get(b"INTERNALDATE")

    return MessageSummary(
        uid=uid,
        date=internal.strftime("%Y-%m-%d %H:%M:%S") if internal else "",
        sender=decode_header_value(_utf8_header(message.get("From", ""))),
        subject=decode_header_value(_utf8_header(message.get("Subject", ""))),
        folder=folder,
        size=data.get(b"RFC822.SIZE") or 0,
        flags=flag_names(data.get(b"FLAGS")),
        has_attachments=structure_has_attachment(data.get(b"BODYSTRUCTURE")),
    )
