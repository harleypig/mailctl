"""Who sends the mail in a folder, and how much of it is left unread.

Read-only: one search, then the headers and flags of what it found,
fetched a page at a time, and counted here. Nothing is marked read. The
report is the input to "make filters for these", so a row's key is the
same value ``search --like`` would derive for that header.
"""

import email.utils
import re
from collections import Counter
from dataclasses import dataclass

from .. import MailctlError
from ..criteria import Criteria
from ..engine import Session
from ..providers.base import FetchedMessage, decode_header_value
from .mail import extract_value, header_values
from .messages import LIST_PAGE, LONE_SURROGATES

# Every header the search matched is read, so this bounds the work a
# report does on a live account. Ten times the existing-mail pass's
# ceiling: reading headers changes nothing, moving mail does.
DEFAULT_MAX_MESSAGES = 5000

GROUPINGS = ("address", "domain", "list-id")


@dataclass(frozen=True)
class SenderCount:
    """One row: a sender, a domain, or a list, and its mail.

    ``key`` is None for the mail that has none -- no parsable From, or no
    List-Id. ``name`` is the display name or list description last seen
    for it, decoded; empty where there is none, and always for a domain.
    """

    key: str | None
    name: str
    total: int
    unread: int

    # ------------------------------------------------------------------------
    @property
    def unread_percent(self) -> float:
        """Unread as a share of the total, 0-100, to one decimal."""
        return round(100 * self.unread / self.total, 1) if self.total else 0.0


@dataclass(frozen=True)
class SenderReport:
    """The mail in ``folder`` that matched, counted by ``by``.

    ``messages`` and ``unread`` count every message that matched and
    ``groups`` every distinct key; ``senders`` holds the rows kept after
    the minimum and the top were applied, busiest first.
    """

    folder: str
    by: str
    messages: int
    unread: int
    groups: int
    senders: list[SenderCount]


# ----------------------------------------------------------------------------
def count_senders(
    session: Session,
    folder: str = "INBOX",
    criteria: Criteria | None = None,
    by: str = "address",
    top: int | None = None,
    minimum: int = 1,
    max_messages: int = DEFAULT_MAX_MESSAGES,
) -> SenderReport:
    """Count the mail in ``folder`` by sender, read-only.

    ``criteria`` selects the mail as ``search`` does -- the host's search,
    then a re-check against the real headers -- and None counts every
    message. ``by`` is one of ``GROUPINGS``. A row is kept when it counts
    at least ``minimum`` messages; ``top`` keeps only that many, None all.

    Refused whole, before any header is read, when the search finds more
    than ``max_messages``: a count of the first N would read as a count
    of all of them.
    """
    if by not in GROUPINGS:
        raise MailctlError(
            f"cannot group senders by {by!r}; use one of "
            f"{', '.join(GROUPINGS)}"
        )

    for setting, label, value in (
        ("top", "the number of rows", top),
        ("minimum", "the minimum count", minimum),
        ("max_messages", "the message ceiling", max_messages),
    ):
        if value is not None and value < 1:
            raise MailctlError(
                f"{label} must be at least 1, not {value}",
                code="at_least_one",
                fields={"setting": setting, "value": value},
            )

    if criteria is not None and not criteria:
        criteria = None

    transport = session.transport
    folder = session.dialect.normalize(folder, transport.list_folders())

    if criteria is not None:
        uids = transport.search(folder, criteria)

    else:
        uids = transport.search_messages(folder, "ALL")

    if len(uids) > max_messages:
        why = (
            f"A count of the first {max_messages} would read as a count of "
            f"them all."
        )

        raise MailctlError(
            f"the search found {len(uids)} message(s) in {folder!r} but "
            f"the ceiling is {max_messages}, so no header was read. {why} "
            f"Narrow the search, or raise the ceiling to {len(uids)} or "
            f"higher.",
            code="senders_ceiling",
            fields={
                "count": len(uids),
                "folder": folder,
                "limit": max_messages,
                "why": why,
            },
        )

    totals: Counter = Counter()
    unread: Counter = Counter()
    names: dict[str | None, str] = {}

    # Newest first, so the name kept for a key is the one it uses now.
    newest = sorted(uids, reverse=True)

    for start in range(0, len(newest), LIST_PAGE):
        for item in transport.fetch_summaries(
            newest[start : start + LIST_PAGE], folder
        ):
            if criteria is not None and not criteria.matches(
                header_values(item.headers)
            ):
                continue

            key, name = sender_key(item, by)
            totals[key] += 1
            unread[key] += not _seen(item)

            if name and key not in names:
                names[key] = name

    rows = sorted(
        (
            SenderCount(key, names.get(key, ""), total, unread[key])
            for key, total in totals.items()
            if total >= minimum
        ),
        key=_busiest_first,
    )

    return SenderReport(
        folder=folder,
        by=by,
        messages=sum(totals.values()),
        unread=sum(unread.values()),
        groups=len(totals),
        senders=rows[:top] if top else rows,
    )


# ----------------------------------------------------------------------------
def sender_key(item: FetchedMessage, by: str) -> tuple[str | None, str]:
    """The key a message is counted under, and the name to show with it.

    An address is lower-cased; a domain is what follows its last ``@``;
    a list is the bracketed identifier of its List-Id, as ``--like``
    derives it. The address is parsed before the display name is decoded,
    so an encoded comma in the name cannot split it.
    """
    headers = item.headers

    if by == "list-id":
        raw = headers.get("List-Id")

        if not raw:
            return None, ""

        key = _clean(extract_value("list-id", raw)).strip().lower() or None
        text = _clean(decode_header_value(raw))
        name = text.split("<", 1)[0].strip().strip('"').strip()

        return key, name

    raw = headers.get("From")

    if raw is None:
        return None, ""

    display, address = _split_address(raw)
    address = address.strip().lower()

    if by == "domain":
        domain = address.rpartition("@")[2] if "@" in address else ""

        return domain or None, ""

    return address or None, _clean(decode_header_value(display)).strip()


# ----------------------------------------------------------------------------
def _split_address(raw) -> tuple[str, str]:
    """A From header's display name, still encoded, and its address.

    A trailing ``<address>`` is taken as it stands: an encoded word may
    hold ``]`` or ``;``, which RFC 5322 parsing reads as structure and
    loses the address to. Anything else is left to ``parseaddr``, and a
    value it cannot parse at all is kept whole. A raw 8-bit header is
    read as the UTF-8 it usually is (RFC 6532) before any of that.
    """
    text = _clean(raw if isinstance(raw, str) else decode_header_value(raw))
    bracketed = re.search(r"<([^<>\s]+)>\s*$", text)

    if bracketed:
        return text[: bracketed.start()].strip().strip('"'), bracketed[1]

    display, address = email.utils.parseaddr(text)

    return display, address or text


# ----------------------------------------------------------------------------
def _clean(text: str) -> str:
    """Undecodable bytes arrive as lone surrogates, which no output can
    encode; they become replacement characters here."""
    return LONE_SURROGATES.sub("\ufffd", text)


# ----------------------------------------------------------------------------
def _seen(item: FetchedMessage) -> bool:
    return any(flag.lower() == "\\seen" for flag in item.summary.flags)


# ----------------------------------------------------------------------------
def _busiest_first(row: SenderCount) -> tuple:
    """Total, then unread, both descending, then the key; none last."""
    return (-row.total, -row.unread, row.key is None, row.key or "")
