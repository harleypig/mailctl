"""Between the neutral model and the ``imap`` component's own records.

The component keeps records of its own, and the utilities speak
:mod:`mailctl.providers.model`. The two are field for field alike today, so
each translation here is a straight copy; keeping it in one place is what
lets either side change without the other noticing. The ManageSieve
records -- a placement, a diff -- cross in the dialect (``sieve.py``).
"""

from ...components.imap import messages as imap_records
from .. import model

__all__ = [
    "fetched_message",
    "mail_result",
    "message_summary",
    "session_plan",
]


# ----------------------------------------------------------------------------
def message_summary(
    value: imap_records.MessageSummary,
) -> model.MessageSummary:
    return model.MessageSummary(
        uid=value.uid,
        date=value.date,
        sender=value.sender,
        subject=value.subject,
        folder=value.folder,
        size=value.size,
        flags=value.flags,
        has_attachments=value.has_attachments,
    )


# ----------------------------------------------------------------------------
def _session_summary(
    value: model.MessageSummary,
) -> imap_records.MessageSummary:
    return imap_records.MessageSummary(
        uid=value.uid,
        date=value.date,
        sender=value.sender,
        subject=value.subject,
        folder=value.folder,
        size=value.size,
        flags=value.flags,
        has_attachments=value.has_attachments,
    )


# ----------------------------------------------------------------------------
def fetched_message(
    value: imap_records.FetchedMessage,
) -> model.FetchedMessage:
    return model.FetchedMessage(value.headers, message_summary(value.summary))


# ----------------------------------------------------------------------------
def session_plan(value: model.MailActionPlan) -> imap_records.MailActionPlan:
    """A plan handed back by the engine, as the IMAP session executes it."""
    return imap_records.MailActionPlan(
        value.source,
        value.destination,
        list(value.flags),
        value.discard,
        [_session_summary(message) for message in value.messages],
    )


# ----------------------------------------------------------------------------
def mail_result(
    value: imap_records.MailActionResult,
) -> model.MailActionResult:
    return model.MailActionResult(value.flagged, value.moved, value.deleted)
