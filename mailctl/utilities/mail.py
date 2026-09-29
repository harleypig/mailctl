"""Mail already delivered: the existing-mail pass, and rules from a message.

Sieve only ever sees new mail, so this is the half that applies a rule to
what is already in the mailbox. The ``--max-messages`` ceiling is checked
again when a plan is executed, not only when it is made.

Selection is done here, on the transport's candidates: the host's search
narrows the mailbox, and :func:`recheck` keeps only the messages the rule
really matches (ADR 0007).
"""

import email.utils
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from email.message import Message

from .. import MailctlError
from ..criteria import Criteria, Term, merge_criteria
from ..engine import Session
from ..providers.base import (
    ActionSpec,
    FetchedMessage,
    MailActionPlan,
    MailActionResult,
    MessageSummary,
    decode_header_value,
    same_folder,
)
from .events import EventSink
from .folders import FolderPlan, realize_folder

DEFAULT_MAX_MESSAGES = 500


# ############################################################################
# The existing-mail pass
# ############################################################################


# ----------------------------------------------------------------------------
def require_mail_action(folder: FolderPlan, spec: ActionSpec) -> None:
    """Refuse an existing-mail pass that would do nothing to a message."""
    if not folder.folder and not spec.discard and not spec.flags:
        raise MailctlError("nothing to do", code="no_mail_action")


# ----------------------------------------------------------------------------
def mail_pass_is_noop(spec: ActionSpec, source: str, destination: str) -> bool:
    """Whether the existing-mail pass would change nothing at all.

    A rule that only keeps mail, or files it into the folder it is already
    in, has nothing to do to delivered mail -- searching for it and asking
    about it would be a prompt that changes nothing. Nor has one that
    discards and keeps: an explicit ``keep`` outlives ``discard`` (RFC 5228
    section 4.4), and a discarding rule files nowhere.
    """
    if spec.flags:
        return False

    if spec.discard:
        return spec.keep

    return not destination or same_folder(destination, source)


# ----------------------------------------------------------------------------
def source_folder(session: Session, name: str) -> str:
    """Normalize the folder the existing-mail pass reads from.

    The same normalization the target gets, so ``--folder Lists/X`` and
    ``--fileinto Lists/X`` are recognised as one folder, and the search
    selects the server's real name for it.
    """
    return session.dialect.normalize(name, session.transport.list_folders())


# ----------------------------------------------------------------------------
def plan_mail(
    session: Session,
    criteria: Criteria,
    spec: ActionSpec,
    source: str,
    destination: str,
) -> MailActionPlan:
    """Select the matches in ``source`` read-only and plan what is done.

    Read-only by construction -- the host is only searched and read -- so
    a caller can always build a plan first and decide afterwards. That is
    what a dry run is: a plan that is never executed.
    """
    criteria.require_terms()

    transport = session.transport
    uids = transport.search(source, criteria)
    matched = (
        matching(criteria, transport.fetch_headers(uids, source))
        if uids
        else []
    )

    # The rule's own meaning: a discarding rule files nowhere, and an
    # explicit keep outlives the discard (RFC 5228 section 4.4), so only a
    # discard without keep deletes.
    plan = MailActionPlan(
        source,
        "" if spec.discard else destination,
        list(spec.flags),
        spec.discard and not spec.keep,
        [candidate.summary for candidate in matched],
        keep=spec.keep,
    )

    if not plan.copies or not matched:
        return plan

    held, unidentified = already_held(session, criteria, matched, destination)

    return replace(plan, held=held, unidentified=unidentified)


# ----------------------------------------------------------------------------
def already_held(
    session: Session,
    criteria: Criteria,
    matched: list[FetchedMessage],
    destination: str,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Which matches ``destination`` already holds, read-only.

    Returns the UIDs of the matches whose Message-ID the destination has,
    and of those with no Message-ID, which cannot be looked for. A copy
    has the same headers and body as its original, so one search of the
    destination with the rule's own header and body tests finds every
    copy; the date and state filters are left out, since a copy is read
    or flagged on its own. A rule of date and state filters alone has no
    such test, so every message with a Message-ID is fetched instead.
    """
    transport = session.transport

    if not transport.list_folders().exists(destination):
        return (), ()

    ids = {
        candidate.summary.uid: message_id(candidate.headers)
        for candidate in matched
    }
    unidentified = tuple(uid for uid, value in ids.items() if value is None)

    if len(unidentified) == len(ids):
        return (), unidentified

    if criteria.terms or criteria.body:
        copies = replace(
            criteria,
            since=None,
            before=None,
            older_than=None,
            unread=False,
            flagged=False,
        )

    else:
        # RFC 3501 section 6.4.4: an empty HEADER string matches every
        # message that has the field.
        copies = Criteria(terms=[Term("Message-ID", "")])

    uids = transport.search(destination, copies)
    present = {
        message_id(candidate.headers)
        for candidate in (
            transport.fetch_headers(uids, destination) if uids else []
        )
    }
    held = tuple(
        uid for uid, value in ids.items() if value and value in present
    )

    return held, unidentified


# ----------------------------------------------------------------------------
def message_id(headers: Message) -> str | None:
    """A message's Message-ID, or None when it has none.

    Folding whitespace is dropped: a msg-id holds none (RFC 5322 section
    3.6.4), and a server may fold a long one differently in each copy.
    """
    value = headers.get("Message-ID")

    if value is None:
        return None

    return "".join(str(value).split()) or None


# ----------------------------------------------------------------------------
def recheck(
    criteria: Criteria, candidates: Iterable[FetchedMessage]
) -> list[MessageSummary]:
    """Keep the candidates whose headers really match ``criteria``.

    The host's search narrows the mailbox; the fetched headers then decide.
    That second pass is not belt-and-braces -- IMAP can only substring
    match, so it is the only thing that makes ``--compare is`` and
    ``--compare matches`` mean the same here as they will in Sieve.
    """
    return [candidate.summary for candidate in matching(criteria, candidates)]


# ----------------------------------------------------------------------------
def matching(
    criteria: Criteria, candidates: Iterable[FetchedMessage]
) -> list[FetchedMessage]:
    """The candidates :func:`recheck` keeps, with their headers."""
    return [
        candidate
        for candidate in candidates
        if criteria.matches(header_values(candidate.headers))
    ]


# ----------------------------------------------------------------------------
def header_values(message: Message) -> dict[str, list[str]]:
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
        decoded = decode_header_value(raw)

        values = collected.setdefault(key, [])
        values.append(decoded)

        if decoded != raw:
            values.append(raw)

    return collected


# ----------------------------------------------------------------------------
def check_message_cap(plan: MailActionPlan, max_messages: int) -> None:
    """Refuse a plan larger than the cap, whole.

    Processing the first N and reporting success would read as "it handled
    everything", so a partial batch is never processed.
    """
    if plan.count <= max_messages:
        return

    why = (
        f"NO existing message was touched -- a partial batch is never "
        f"processed, because handling the first {max_messages} and "
        f"reporting success would read as having handled them all."
    )
    raise MailctlError(
        f"{plan.count} message(s) match but the ceiling is {max_messages}. "
        f"{why} A ceiling of {plan.count} (or higher) processes every "
        f"match; confirming does not lift it, since the ceiling is set "
        f"deliberately.",
        code="max_messages",
        fields={
            "count": plan.count,
            "limit": max_messages,
            "why": why,
        },
    )


# ----------------------------------------------------------------------------
def execute_mail(
    session: Session,
    plan: MailActionPlan,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    folder: FolderPlan | None = None,
    on_event: EventSink | None = None,
) -> MailActionResult:
    """Carry out an approved plan, re-checking the cap first.

    ``folder`` is the destination's plan; one due for IMAP creation is
    created here, after the decision, rather than while planning.

    A plan that keeps its messages is flagged where they are and then
    copied, so the copy carries the flags too -- as a rule's ``addflag``
    reaches both what it files and what it keeps. Only the matches the
    destination did not already hold are copied.
    """
    check_message_cap(plan, max_messages)

    if folder is not None:
        realize_folder(session, folder, on_event)

    if not plan.copies:
        return session.transport.apply_mail(plan)

    transport = session.transport
    flagged = 0

    if plan.flags and plan.uids:
        transport.add_flags(plan.source, plan.uids, plan.flags)
        flagged = plan.count

    uids = plan.copy_uids
    copied = (
        transport.copy_messages(plan.source, uids, plan.destination)
        if uids
        else 0
    )

    return MailActionResult(flagged=flagged, copied=copied)


# ############################################################################
# Deriving a rule from a message
# ############################################################################


@dataclass(frozen=True)
class PickedMessage:
    """The message a rule is derived from."""

    uid: int
    folder: str
    headers: Message


@dataclass(frozen=True)
class DerivedCriteria:
    """Criteria built from a message, and the headers it lacked."""

    criteria: Criteria
    skipped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LikeMessage:
    """Criteria for mail like one message, and that message.

    ``criteria`` is what was derived from the message merged with the
    criteria given outright; ``skipped`` names the headers asked for that
    the message did not have.
    """

    message: PickedMessage
    criteria: Criteria
    skipped: list[str] = field(default_factory=list)


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
def criteria_like(
    session: Session,
    folder: str,
    uid: int,
    explicit: Criteria | None = None,
    derive: str = "auto",
) -> LikeMessage:
    """Criteria matching mail like message ``uid`` in ``folder``, read-only.

    The criteria are derived from the message's headers as ``derive``
    says, then merged with ``explicit`` by ``merge_criteria``: a header
    given outright replaces what was derived for it, and ``match`` and
    ``compare`` are the explicit ones. The folder is normalized against
    the server's list; the message is not marked read. The result is not
    validated, so the caller can report ``skipped`` before
    ``require_terms`` refuses an empty set.
    """
    if uid < 1:
        raise MailctlError(f"message UIDs start at 1, not {uid}")

    explicit = explicit if explicit is not None else Criteria()

    transport = session.transport
    folder = session.dialect.normalize(folder, transport.list_folders())

    headers = transport.message_headers(folder, uid)

    derived = derive_criteria(
        headers, derive, explicit.match, explicit.compare
    )

    return LikeMessage(
        PickedMessage(uid, folder, headers),
        merge_criteria(derived.criteria, explicit),
        derived.skipped,
    )


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
