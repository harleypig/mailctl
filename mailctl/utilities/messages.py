"""Finding and reading messages, read-only, decoded for a person.

Nothing here marks a message read or writes one anywhere: a folder is
examined, a body is peeked at, and an attachment is described, never
saved.
"""

import email
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from .. import MailctlError
from ..criteria import Criteria
from ..providers.base import MessageSummary, Provider, decode_header_value

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
