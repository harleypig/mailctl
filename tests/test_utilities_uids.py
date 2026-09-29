"""UIDs kept between runs: ``utilities.uids``, and every utility taking a
UID, over the mxroute transport and the conftest IMAP double.

A UID names one message only under the folder's UIDVALIDITY (RFC 9051
section 2.3.1.1). A UID pinned to a UIDVALIDITY the folder no longer has
is refused before it is used -- before any write, and before a read shows
or builds a rule from what may be another message. A plan made in this
run records the value, and executing it checks it again.
"""

import pytest
from utilities_support import DEFAULT_UIDVALIDITY

from mailctl import MailctlError
from mailctl.criteria import Criteria
from mailctl.providers.base import ActionSpec
from mailctl.utilities import flags, mail, messages, uids

WRITES = {"add_flags", "remove_flags", "move", "copy", "expunge"}
STALE = DEFAULT_UIDVALIDITY - 1


# ----------------------------------------------------------------------------
def note(uid: int) -> bytes:
    return (
        f"From: a@example.com\r\nSubject: note {uid}\r\n"
        f"Message-ID: <{uid}@example.com>\r\n\r\n"
    ).encode()


# ----------------------------------------------------------------------------
@pytest.fixture
def mailbox(fake_imap):
    """Three unread messages in every folder, each reporting the default
    UIDVALIDITY."""
    fake_imap.messages = {uid: note(uid) for uid in (1, 2, 3)}

    return fake_imap


# ----------------------------------------------------------------------------
def renumber(fake_imap, sessions, folder: str = "INBOX") -> int:
    """Give ``folder`` a new UIDVALIDITY and reconnect, as a server that
    rebuilt the folder between two runs would be met by the next one."""
    fake_imap.uidvalidity[folder] = DEFAULT_UIDVALIDITY + 1

    imap = sessions.transport.imap
    imap.close()
    imap.open()

    return DEFAULT_UIDVALIDITY + 1


# ----------------------------------------------------------------------------
def writes(fake_imap) -> set[str]:
    return WRITES & set(fake_imap.names())


# ############################################################################
# Marking -- the write the issue is about
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_stale_uid_given_to_mark_is_refused_before_any_write(
    mailbox, sessions
):
    """The regression (#204). Red if plan_mark ignores the pin: the plan
    is made, and executing it sends STORE for UIDs that may now name
    other messages."""
    with pytest.raises(MailctlError) as caught:
        plan = flags.plan_mark(
            sessions, "INBOX", [1, 2], (flags.SEEN,), (), STALE
        )
        flags.execute_mark(sessions, plan)

    assert caught.value.code == "uidvalidity_changed"
    assert caught.value.fields["given"] == STALE
    assert caught.value.fields["current"] == DEFAULT_UIDVALIDITY
    assert "None was used" in str(caught.value)
    assert writes(mailbox) == set()


# ----------------------------------------------------------------------------
def test_the_stale_uids_are_not_even_read(mailbox, sessions):
    """Checked before the flags are fetched, and at no cost: the EXAMINE
    that reports the value is the one the fetch would have made. Red if
    the check follows the fetch."""
    with pytest.raises(MailctlError, match="UIDVALIDITY"):
        flags.plan_mark(sessions, "INBOX", [1], (flags.SEEN,), (), STALE)

    assert "fetch" not in mailbox.names()
    assert mailbox.names().count("select_folder") == 1


# ----------------------------------------------------------------------------
def test_a_current_pin_plans_and_marks_as_before(mailbox, sessions):
    plan = flags.plan_mark(
        sessions, "INBOX", [3], (flags.SEEN,), (), DEFAULT_UIDVALIDITY
    )
    flags.execute_mark(sessions, plan)

    assert plan.uidvalidity == DEFAULT_UIDVALIDITY
    assert ("add_flags", (3,), (b"\\Seen",)) in mailbox.calls


# ----------------------------------------------------------------------------
def test_an_unpinned_plan_records_the_value_at_no_cost(mailbox, sessions):
    """A bare UID still works, and the one EXAMINE is all it sends."""
    plan = flags.plan_mark(sessions, "INBOX", [3], (flags.SEEN,), ())

    assert plan.uidvalidity == DEFAULT_UIDVALIDITY
    assert mailbox.names().count("select_folder") == 1


# ----------------------------------------------------------------------------
def test_a_folder_renumbered_after_planning_is_refused_at_execute(
    mailbox, sessions
):
    """The plan's own UIDs, checked again: a reconnect between the plan
    and the write finds the new value. Red if execute_mark writes on the
    plan's word alone."""
    plan = flags.plan_mark(sessions, "INBOX", [3], (flags.SEEN,), ())
    renumber(mailbox, sessions)

    with pytest.raises(MailctlError) as caught:
        flags.execute_mark(sessions, plan)

    assert caught.value.code == "uidvalidity_changed"
    assert writes(mailbox) == set()


# ############################################################################
# Reading one message
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_stale_uid_is_not_shown_as_though_it_were_the_message(
    mailbox, sessions
):
    """Red if read_message shows whatever UID 2 names now."""
    with pytest.raises(MailctlError) as caught:
        messages.read_message(sessions, "INBOX", 2, STALE)

    assert caught.value.code == "uidvalidity_changed"


# ----------------------------------------------------------------------------
def test_a_stale_uid_gone_in_the_renumbering_is_refused_as_stale(
    mailbox, sessions
):
    """Not as a missing message: the numbering is what changed. Red if the
    read's own refusal is let through when the pin disagrees."""
    with pytest.raises(MailctlError) as caught:
        messages.read_message(sessions, "INBOX", 99, STALE)

    assert caught.value.code == "uidvalidity_changed"


# ----------------------------------------------------------------------------
def test_a_missing_uid_under_a_current_pin_is_still_missing(mailbox, sessions):
    with pytest.raises(MailctlError, match="no message with uid 99"):
        messages.read_message(sessions, "INBOX", 99, DEFAULT_UIDVALIDITY)


# ----------------------------------------------------------------------------
def test_a_message_read_carries_its_uidvalidity_at_no_cost(mailbox, sessions):
    content = messages.read_message(sessions, "INBOX", 2)
    pinned = messages.read_message(sessions, "INBOX", 2, DEFAULT_UIDVALIDITY)

    assert content.uidvalidity == pinned.uidvalidity == DEFAULT_UIDVALIDITY
    assert mailbox.names().count("select_folder") == 2


# ----------------------------------------------------------------------------
def test_a_listing_reports_what_its_uids_are_valid_under(mailbox, sessions):
    listing = messages.list_messages(sessions, "INBOX")

    assert listing.uidvalidity == DEFAULT_UIDVALIDITY
    assert mailbox.names().count("select_folder") == 1


# ----------------------------------------------------------------------------
def test_a_host_reporting_none_lists_none_and_refuses_a_pin(mailbox, sessions):
    """A server that sends no UIDVALIDITY cannot vouch for a UID, so a pin
    is refused rather than taken as matching."""
    mailbox.uidvalidity["INBOX"] = None

    assert messages.list_messages(sessions, "INBOX").uidvalidity is None

    with pytest.raises(MailctlError, match="reports no UIDVALIDITY"):
        messages.read_message(sessions, "INBOX", 2, DEFAULT_UIDVALIDITY)


# ############################################################################
# --like: a rule built from a message
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_stale_like_uid_builds_no_criteria(mailbox, sessions):
    """Red if criteria_like derives a rule from whatever the UID names
    now; add and apply would then write it."""
    with pytest.raises(MailctlError) as caught:
        mail.criteria_like(sessions, "INBOX", 2, uidvalidity=STALE)

    assert caught.value.code == "uidvalidity_changed"


# ----------------------------------------------------------------------------
def test_a_current_like_pin_derives_as_before(mailbox, sessions):
    like = mail.criteria_like(
        sessions, "INBOX", 2, uidvalidity=DEFAULT_UIDVALIDITY
    )

    assert like.criteria.describe() == "From contains 'a@example.com'"


# ############################################################################
# The existing-mail pass
# ############################################################################


# ----------------------------------------------------------------------------
def pass_plan(sessions, spec: ActionSpec):
    criteria = Criteria()
    criteria.add("From", "a@example.com")

    return mail.plan_mail(sessions, criteria, spec, "INBOX", "INBOX.Lists")


# ----------------------------------------------------------------------------
def test_a_mail_plan_records_the_source_value_at_no_cost(mailbox, sessions):
    plan = pass_plan(sessions, ActionSpec(fileinto="INBOX.Lists"))

    assert plan.uidvalidity == DEFAULT_UIDVALIDITY
    assert mailbox.names().count("select_folder") == 1


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "spec",
    [ActionSpec(fileinto="INBOX.Lists"), ActionSpec(flags=("\\Flagged",))],
    ids=["move", "flag"],
)
def test_a_source_renumbered_after_planning_is_refused_at_execute(
    mailbox, sessions, spec
):
    """Red if execute_mail moves or flags on the plan's UIDs without
    checking the source was not renumbered in between."""
    plan = pass_plan(sessions, spec)
    renumber(mailbox, sessions)

    with pytest.raises(MailctlError) as caught:
        mail.execute_mail(sessions, plan)

    assert caught.value.code == "uidvalidity_changed"
    assert writes(mailbox) == set()


# ----------------------------------------------------------------------------
def test_a_copying_plan_checks_its_source_not_the_destination(
    mailbox, sessions
):
    """A keep plan searches the destination last; the check re-reads the
    source, whose UIDs it acts on."""
    mailbox.folder_messages["INBOX.Lists"] = {}
    plan = pass_plan(sessions, ActionSpec(fileinto="INBOX.Lists", keep=True))
    mailbox.uidvalidity["INBOX.Lists"] = STALE
    before = len(mailbox.calls)

    mail.execute_mail(sessions, plan)

    assert mailbox.calls[before] == ("select_folder", "INBOX", True)
    assert "copy" in mailbox.names()


# ############################################################################
# The pin itself
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("value", [0, -1, 2**32])
def test_a_pin_no_folder_could_have_is_refused_before_connecting(
    mailbox, sessions, value
):
    """RFC 9051's nz-number32: 1 to 4294967295."""
    calls = len(mailbox.calls)

    with pytest.raises(MailctlError, match="from 1 to 4294967295"):
        flags.plan_mark(sessions, "INBOX", [1], (flags.SEEN,), (), value)

    assert len(mailbox.calls) == calls


# ----------------------------------------------------------------------------
def test_the_largest_pin_is_taken(mailbox, sessions):
    mailbox.uidvalidity["INBOX"] = uids.MAX_UIDVALIDITY

    plan = flags.plan_mark(
        sessions, "INBOX", [1], (flags.SEEN,), (), uids.MAX_UIDVALIDITY
    )

    assert plan.uidvalidity == uids.MAX_UIDVALIDITY
