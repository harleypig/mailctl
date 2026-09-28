"""Between the neutral model and the layer-1 libraries' own records.

The ``imap`` and ``managesieve`` components keep records of their own, and
the engine speaks :mod:`mailctl.providers.model`. The two are field for
field alike today, so each translation here is a straight copy; keeping it
in one place is what lets either side change without the other noticing.
"""

from typing import overload

from ...components import imap as imap_records
from ...components import managesieve as sieve_records
from .. import model

__all__ = [
    "DIFF_LABEL",
    "display_diff",
    "folder_creation",
    "mail_plan",
    "mail_result",
    "message_summary",
    "placement",
    "session_plan",
]

# What a diff of this host's rule set is called: a Sieve script.
DIFF_LABEL = "sieve"


# ----------------------------------------------------------------------------
@overload
def placement(value: model.Placement) -> sieve_records.Placement: ...


@overload
def placement(value: None) -> None: ...


def placement(
    value: model.Placement | None,
) -> sieve_records.Placement | None:
    """A neutral placement, as the ManageSieve component takes it."""
    if value is None:
        return None

    return sieve_records.Placement(value.where, value.anchor)


# ----------------------------------------------------------------------------
def display_diff(value: sieve_records.DisplayDiff) -> model.DisplayDiff:
    """A script diff, labelled as one."""
    return model.DisplayDiff(value.text, value.reformats, DIFF_LABEL)


# ----------------------------------------------------------------------------
def folder_creation(
    value: imap_records.FolderCreation,
) -> model.FolderCreation:
    return model.FolderCreation(
        value.folder, value.subscribed, value.subscribe_error
    )


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
def mail_plan(value: imap_records.MailActionPlan) -> model.MailActionPlan:
    """The IMAP session's plan, as the engine carries it."""
    return model.MailActionPlan(
        value.source,
        value.destination,
        list(value.flags),
        value.discard,
        [message_summary(message) for message in value.messages],
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
