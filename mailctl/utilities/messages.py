"""Finding and reading messages, read-only, decoded for a person.

Nothing here marks a message read or writes one anywhere: a folder is
examined, a body is peeked at, and an attachment is described, never
saved.
"""

import email
import email.utils
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from html.parser import HTMLParser

from .. import MailctlError
from ..criteria import Criteria
from ..engine import Session
from ..providers.base import (
    SORT_KEYS,
    SORT_SENT,
    SORT_SIZE,
    FetchedMessage,
    MessageSummary,
    SortOrder,
    Transport,
    decode_header_value,
)
from .mail import header_values
from .rules import require_capability
from .uids import (
    check_uidvalidity,
    current_uidvalidity,
    read_pinned,
    require_pinnable,
)

# ############################################################################
# Finding and reading messages
# ############################################################################

DEFAULT_LIST_LIMIT = 20

# Messages read per round from the newest end of a listing, so a small
# limit on a large folder reads only about what it shows. The same size as
# the IMAP component's command chunk, so a round is one FETCH.
LIST_PAGE = 250

# How the IMAP component writes a message's INTERNALDATE: local time, the
# zone already dropped.
RECEIVED_FORMAT = "%Y-%m-%d %H:%M:%S"

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
    """The messages in a folder that matched: newest first, or in
    ``order`` where one was asked for.

    ``more`` is true when there may be further matches past the limit.
    ``uidvalidity`` is what the listed UIDs are valid under, None where
    the host reports none.
    """

    folder: str
    messages: list[MessageSummary]
    more: bool = False
    order: SortOrder | None = None
    uidvalidity: int | None = None


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
    ``uidvalidity`` is what ``uid`` is valid under, None where the host
    reports none.
    """

    uid: int
    folder: str
    headers: list[tuple[str, str]]
    body: str
    body_from_html: bool
    attachments: list[Attachment]
    flags: tuple[str, ...]
    source: bytes
    uidvalidity: int | None = None

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
    session: Session,
    folder: str = "INBOX",
    criteria: Criteria | None = None,
    raw: str | None = None,
    limit: int | None = DEFAULT_LIST_LIMIT,
    order: SortOrder | None = None,
) -> MessageListing:
    """List the newest messages in ``folder``, read-only.

    Select them with ``criteria`` -- the same model a rule uses, re-checked
    against real headers -- or with a ``raw`` query in the host's own
    search language, never both; with neither, every message is listed.
    A raw query is refused, before anything connects, by a provider that
    does not declare ``raw_query``. ``limit`` None lists all. Nothing is
    marked read.

    With ``order`` the matches are listed in that order instead, and the
    limit is taken after sorting: the ``limit`` largest, or newest. A
    server that sorts is asked to, and only the first matches in its
    order are fetched; otherwise every candidate is fetched, a page at a
    time, and sorted here (:func:`sorted_matches`).
    """
    if criteria is not None and not criteria:
        criteria = None

    if raw:
        require_capability(session, "raw_query")

    if criteria is not None and raw:
        raise MailctlError("give criteria or a raw query, not both")

    if limit is not None and limit < 1:
        raise MailctlError(
            f"the message limit must be at least 1, not {limit}"
        )

    if order is not None and order.key not in SORT_KEYS:
        raise MailctlError(
            f"cannot sort by {order.key!r}; sort by one of "
            f"{', '.join(SORT_KEYS)}"
        )

    transport = session.transport
    folder = session.dialect.normalize(folder, transport.list_folders())

    if order is None:
        messages, more = newest_matches(
            transport, folder, criteria, raw, limit
        )

    elif session.dialect.sorts_messages(transport.mail_capabilities()):
        ordered = transport.sort_messages(folder, order, criteria, raw)
        messages, more = first_matches(
            transport, folder, ordered, criteria, limit
        )

    else:
        messages, more = sorted_matches(
            transport, folder, criteria, raw, order, limit
        )

    return MessageListing(
        folder, messages, more, order, current_uidvalidity(session, folder)
    )


# ----------------------------------------------------------------------------
def newest_matches(
    transport: Transport,
    folder: str,
    criteria: Criteria | None,
    expression: str | None,
    limit: int | None,
) -> tuple[list[MessageSummary], bool]:
    """Up to ``limit`` matches, newest (highest UID) first, and whether
    candidates beyond the limit went unexamined.

    ``criteria`` is searched for and re-checked against the fetched
    headers, as the existing-mail pass does; ``expression`` is a raw host
    search taken as given; with neither, every message is a candidate.
    """
    uids = candidates(transport, folder, criteria, expression)

    return first_matches(
        transport, folder, sorted(uids, reverse=True), criteria, limit
    )


# ----------------------------------------------------------------------------
def candidates(
    transport: Transport,
    folder: str,
    criteria: Criteria | None,
    expression: str | None,
) -> list[int]:
    """The UIDs the host's search returns, before any re-check."""
    if criteria is not None:
        return transport.search(folder, criteria)

    return transport.search_messages(folder, expression or "ALL")


# ----------------------------------------------------------------------------
def first_matches(
    transport: Transport,
    folder: str,
    ordered: list[int],
    criteria: Criteria | None,
    limit: int | None,
) -> tuple[list[MessageSummary], bool]:
    """Up to ``limit`` matches, in the order of ``ordered``, and whether
    candidates beyond the limit went unexamined.

    Fetched a page at a time from the front, so a small limit reads only
    about what it shows; ``criteria`` is re-checked against the fetched
    headers, which keeps the order and drops what does not match.
    """
    step = min(LIST_PAGE, limit or LIST_PAGE)
    matches: list[MessageSummary] = []
    examined = 0

    while examined < len(ordered) and not (limit and len(matches) >= limit):
        chunk = ordered[examined : examined + step]
        fetched = {
            item.summary.uid: item
            for item in transport.fetch_summaries(chunk, folder)
        }

        for uid in chunk:
            examined += 1
            item = fetched.get(uid)

            # A UID the search returned and the fetch did not was expunged
            # in between; it is simply gone.
            if item is None:
                continue

            if criteria is not None and not criteria.matches(
                header_values(item.headers)
            ):
                continue

            matches.append(item.summary)

            if limit and len(matches) >= limit:
                break

    return matches, examined < len(ordered)


# ----------------------------------------------------------------------------
def sorted_matches(
    transport: Transport,
    folder: str,
    criteria: Criteria | None,
    expression: str | None,
    order: SortOrder,
    limit: int | None,
) -> tuple[list[MessageSummary], bool]:
    """Up to ``limit`` matches in ``order``, sorted here, and whether more
    matched than are returned.

    For a server that cannot sort. The first in order may be anywhere in
    the folder, so every candidate is fetched: one FETCH per page of
    :data:`LIST_PAGE`, never one per message, and on a large folder that
    is the whole folder's headers. The keys match IMAP SORT's (RFC 5256):
    ties keep UID order in either direction.
    """
    uids = sorted(candidates(transport, folder, criteria, expression))
    matches: list[FetchedMessage] = []

    for start in range(0, len(uids), LIST_PAGE):
        for item in transport.fetch_summaries(
            uids[start : start + LIST_PAGE], folder
        ):
            if criteria is None or criteria.matches(
                header_values(item.headers)
            ):
                matches.append(item)

    # Stable, and reverse=True keeps equal keys in their UID order.
    matches.sort(key=_sort_key(order.key), reverse=order.reverse)
    shown = matches[:limit] if limit else matches

    return [item.summary for item in shown], len(shown) < len(matches)


# ----------------------------------------------------------------------------
def _sort_key(key: str):
    """The client-side reading of one of :data:`SORT_KEYS`."""
    if key == SORT_SIZE:
        return lambda item: item.summary.size

    if key == SORT_SENT:
        return _sent

    return lambda item: _received(item.summary)


# ----------------------------------------------------------------------------
def _sent(item: FetchedMessage) -> datetime:
    """The Date header, in UTC where it names no zone; the arrival time
    where it is missing or unreadable, as RFC 5256's DATE key does."""
    value = item.headers.get("Date")

    try:
        sent = email.utils.parsedate_to_datetime(str(value))

    except (TypeError, ValueError, IndexError):
        return _received(item.summary)

    return sent if sent.tzinfo else sent.replace(tzinfo=UTC)


# ----------------------------------------------------------------------------
def _received(summary: MessageSummary) -> datetime:
    """When the server took the message in, as an aware time; the start
    of time when the host gave none."""
    try:
        return datetime.strptime(summary.date, RECEIVED_FORMAT).astimezone()

    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


# ----------------------------------------------------------------------------
def read_message(
    session: Session, folder: str, uid: int, uidvalidity: int | None = None
) -> MessageContent:
    """Fetch one message whole and decode it, without marking it read.

    The folder is selected read-only and the body fetched with
    ``BODY.PEEK[]``, so \\Seen is left exactly as it was. Nothing is
    written to disk: attachments are described, never saved.

    ``uidvalidity`` is what ``uid`` was valid under when it was listed;
    where the folder's is now another, the message is refused rather than
    shown, since the UID may name a different one.
    """
    if uid < 1:
        raise MailctlError(f"message UIDs start at 1, not {uid}")

    require_pinnable(session, uidvalidity)

    transport = session.transport
    folder = session.dialect.normalize(folder, transport.list_folders())

    source, flags = read_pinned(
        session,
        folder,
        uidvalidity,
        lambda: transport.message_source(folder, uid),
    )
    content = parse_message(source, uid=uid, folder=folder, flags=flags)

    return replace(
        content,
        uidvalidity=check_uidvalidity(session, folder, uidvalidity),
    )


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
