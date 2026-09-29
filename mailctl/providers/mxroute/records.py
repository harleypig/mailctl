"""Between the neutral model and the components' own records.

The components keep records of their own, and the utilities speak
:mod:`mailctl.providers.model`. The ``imap`` records are field for field
alike today, so each translation here is a straight copy; keeping it in one
place is what lets either side change without the other noticing. What
each server says about itself crosses here too, for the transport. The
ManageSieve records a rule edit needs -- a placement, a diff -- cross in
the dialect (``sieve.py``).
"""

from ...components.imap import messages as imap_records
from ...components.imap import status as imap_status
from ...components.managesieve.capabilities import Capabilities
from .. import model

__all__ = [
    "fetched_message",
    "folder_status",
    "mail_namespaces",
    "mail_result",
    "mail_server",
    "message_summary",
    "rules_server",
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
def folder_status(value: imap_status.FolderStatus) -> model.FolderStatus:
    return model.FolderStatus(
        value.folder, value.messages, value.unseen, value.size
    )


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


# ----------------------------------------------------------------------------
def rules_server(value: Capabilities) -> model.ServerDescription:
    """The ManageSieve CAPABILITY response, as the neutral description.

    sievelib reads it on connecting and again after STARTTLS, and never
    after AUTHENTICATE, so it is the list from before login. The identity
    is the ``IMPLEMENTATION`` line, which is how ManageSieve names its
    software.
    """
    identity = (
        (("implementation", value.implementation),)
        if value.implementation is not None
        else ()
    )

    return model.ServerDescription(
        identity,
        tuple(model.Capability(name, text) for name, text in value.entries),
        after_login=False,
    )


# ----------------------------------------------------------------------------
def mail_server(
    identity: dict[str, str], capabilities: list[str]
) -> model.ServerDescription:
    """The IMAP ``ID`` and ``CAPABILITY`` answers, as the neutral one.

    The session logs in before anything asks for capabilities, so
    IMAPClient's list is the one from after login.
    """
    return model.ServerDescription(
        tuple(identity.items()),
        tuple(model.Capability(name) for name in capabilities),
        after_login=True,
    )


# ----------------------------------------------------------------------------
def mail_namespaces(
    value: tuple[tuple[tuple[str, str | None], ...], ...],
) -> list[model.Namespace]:
    """The ``NAMESPACE`` response, one record per namespace, kind by kind."""
    found = []

    for kind, pairs in zip(model.NAMESPACE_KINDS, value, strict=False):
        found.extend(
            model.Namespace(kind, prefix, delimiter)
            for prefix, delimiter in pairs
        )

    return found
