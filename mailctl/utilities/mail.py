"""Mail already delivered: the existing-mail pass, and rules from a message.

Sieve only ever sees new mail, so this is the half that applies a rule to
what is already in the mailbox. The ``--max-messages`` ceiling is checked
again when a plan is executed, not only when it is made.
"""

import email.utils
import re
from dataclasses import dataclass, field

from .. import MailctlError
from ..criteria import Criteria
from ..engine import Session
from ..providers.base import (
    ActionSpec,
    MailActionPlan,
    MailActionResult,
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
        raise MailctlError(
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
def source_folder(session: Session, name: str) -> str:
    """Normalize the folder the existing-mail pass reads from.

    The same normalization the target gets, so ``--folder Lists/X`` and
    ``--fileinto Lists/X`` are recognised as one folder, and the search
    selects the server's real name for it.
    """
    return session.transport.normalize(name)


# ----------------------------------------------------------------------------
def plan_mail(
    session: Session,
    criteria: Criteria,
    spec: ActionSpec,
    source: str,
    destination: str,
) -> MailActionPlan:
    """Select the matches in ``source`` read-only and plan what is done.

    Selection is the provider's: it answers which delivered messages the
    rule matches, however its host evaluates rules.
    """
    criteria.require_terms()

    return session.transport.select_mail(
        criteria, source, destination, list(spec.flags), spec.discard
    )


# ----------------------------------------------------------------------------
def check_message_cap(plan: MailActionPlan, max_messages: int) -> None:
    """Refuse a plan larger than the cap, whole.

    Processing the first N and reporting success would read as "it handled
    everything", so a partial batch is never processed.
    """
    if plan.count <= max_messages:
        return

    raise MailctlError(
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
    session: Session,
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
        realize_folder(session, folder, on_event)

    return session.transport.apply_mail(plan)


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
    session: Session,
    folder: str,
    uid: int | None = None,
    search: str | None = None,
) -> PickedMessage:
    """Fetch one message's headers, by UID or by the newest search match."""
    candidates = 1

    if uid is None:
        uids = session.transport.search_messages(folder, search or "")

        if not uids:
            raise MailctlError(f"no message in {folder!r} matched {search!r}")

        candidates = len(uids)
        uid = max(uids)

    headers = session.transport.message_headers(folder, uid)

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
