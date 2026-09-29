"""Marking messages: ``utilities.flags``, over the mxroute transport and
the conftest IMAP double.

Planning must read and never write -- the double records every SELECT's
mode and every call -- and must refuse a UID the folder does not hold
before anything is marked. Executing sends one write per sign, each only
for the messages it would change.
"""

import pytest
from imapclient.exceptions import IMAPClientError

from mailctl import MailctlError
from mailctl.utilities import flags
from mailctl.utilities.flags import FLAGGED, SEEN

WRITES = {"add_flags", "remove_flags", "move", "copy", "expunge"}


# ----------------------------------------------------------------------------
def mail(uid: int) -> bytes:
    return f"From: a@example.com\r\nSubject: note {uid}\r\n\r\n".encode()


# ----------------------------------------------------------------------------
@pytest.fixture
def mailbox(fake_imap):
    """Three messages: 1 read, 2 read and flagged, 3 neither."""
    fake_imap.messages = {uid: mail(uid) for uid in (1, 2, 3)}
    fake_imap.flags = {
        1: (b"\\Seen",),
        2: (b"\\Seen", b"\\Flagged", b"$Todo"),
    }

    return fake_imap


# ############################################################################
# What was asked for
# ############################################################################


# ----------------------------------------------------------------------------
def test_read_and_flag_are_set_or_cleared_by_name():
    assert flags.mark_flags(read=True, flagged=True) == (
        (SEEN, FLAGGED),
        (),
    )
    assert flags.mark_flags(read=False, flagged=False) == (
        (),
        (SEEN, FLAGGED),
    )
    assert flags.mark_flags(read=True, flagged=False) == ((SEEN,), (FLAGGED,))


# ----------------------------------------------------------------------------
def test_keywords_join_the_named_flags_and_repeats_collapse():
    add, remove = flags.mark_flags(
        read=True,
        add_keywords=["$Todo", "$todo", "Work-1"],
        remove_keywords=["$Junk"],
    )

    assert add == (SEEN, "$Todo", "Work-1")
    assert remove == ("$Junk",)


# ----------------------------------------------------------------------------
def test_nothing_asked_for_is_refused():
    with pytest.raises(MailctlError, match="nothing to mark"):
        flags.mark_flags()


# ----------------------------------------------------------------------------
def test_a_keyword_both_set_and_cleared_is_refused_whatever_its_case():
    with pytest.raises(
        MailctlError, match=r"cannot both set and clear \$Todo"
    ):
        flags.mark_flags(add_keywords=["$Todo"], remove_keywords=["$TODO"])


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("keyword", ["\\Seen", "\\Deleted", "\\Answered"])
def test_a_system_flag_is_not_a_keyword(keyword):
    with pytest.raises(MailctlError, match="is a system flag"):
        flags.mark_flags(add_keywords=[keyword])


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "keyword",
    ["", "two words", "(x)", "a{3}", "50%", "a*", 'say"', "x]", "Café", "\t"],
)
def test_a_keyword_that_is_not_an_imap_atom_is_refused(keyword):
    """Red if a space or an atom-special reaches STORE, where it would
    split into two flags or break the command."""
    with pytest.raises(MailctlError, match="cannot be a keyword"):
        flags.mark_flags(remove_keywords=[keyword])


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("keyword", ["$Important", "Work-1", "NonJunk", "a.b"])
def test_an_ordinary_keyword_is_accepted(keyword):
    assert flags.mark_flags(add_keywords=[keyword]) == ((keyword,), ())


# ############################################################################
# Planning -- read-only
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_plan_reads_each_messages_flags_and_writes_nothing(
    sessions, mailbox
):
    plan = flags.plan_mark(sessions, "INBOX", [1, 2, 3], (SEEN, FLAGGED), ())

    assert plan.folder == "INBOX"
    assert [(m.uid, m.add) for m in plan.messages] == [
        (1, (FLAGGED,)),
        (2, ()),
        (3, (SEEN, FLAGGED)),
    ]
    assert plan.messages[1].flags == ("\\Seen", "\\Flagged", "$Todo")
    assert [m.uid for m in plan.changing] == [1, 3]

    # Examined, never selected writable, and nothing marked read by it.
    assert ("select_folder", "INBOX", False) not in mailbox.calls
    assert ("select_folder", "INBOX", True) in mailbox.calls
    assert WRITES.isdisjoint(mailbox.names())
    assert mailbox.marked_seen == set()


# ----------------------------------------------------------------------------
def test_a_flag_is_compared_without_regard_to_case(sessions, mailbox):
    mailbox.flags[3] = (b"\\SEEN", b"$TODO")

    plan = flags.plan_mark(sessions, "INBOX", [3], (SEEN,), ("$Todo",))

    # \SEEN is the \Seen asked for, so nothing to add; $TODO is the $Todo
    # asked to go, so it is removed.
    assert plan.messages[0].add == ()
    assert plan.messages[0].remove == ("$Todo",)


# ----------------------------------------------------------------------------
def test_clearing_takes_only_what_a_message_has(sessions, mailbox):
    plan = flags.plan_mark(sessions, "INBOX", [1, 2, 3], (), (FLAGGED,))

    assert [(m.uid, m.remove) for m in plan.changing] == [(2, (FLAGGED,))]


# ----------------------------------------------------------------------------
def test_a_message_that_already_looks_as_asked_makes_an_empty_plan(
    sessions, mailbox
):
    plan = flags.plan_mark(sessions, "INBOX", [2], (SEEN, FLAGGED), ())

    assert plan.is_empty
    assert plan.changing == []


# ----------------------------------------------------------------------------
def test_a_uid_the_folder_does_not_hold_refuses_the_whole_plan(
    sessions, mailbox
):
    """Red if the missing UIDs are dropped and the rest planned, which
    would mark part of what was asked and report success."""
    with pytest.raises(MailctlError, match="no message with uid 7, 9 in"):
        flags.plan_mark(sessions, "INBOX", [1, 7, 9], (SEEN,), ())

    assert WRITES.isdisjoint(mailbox.names())


# ----------------------------------------------------------------------------
def test_the_folder_is_normalized_against_the_servers_list(sessions, mailbox):
    plan = flags.plan_mark(sessions, "Lists", [1], (FLAGGED,), ())

    assert plan.folder == "INBOX.Lists"
    assert ("select_folder", "INBOX.Lists", True) in mailbox.calls


# ----------------------------------------------------------------------------
def test_repeated_uids_are_planned_once(sessions, mailbox):
    plan = flags.plan_mark(sessions, "INBOX", [3, 1, 3], (FLAGGED,), ())

    assert [m.uid for m in plan.messages] == [3, 1]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("uids", "add", "error"),
    [
        ([0], (SEEN,), "UIDs start at 1"),
        ([], (SEEN,), "at least one message UID"),
        ([1], (), "nothing to mark"),
    ],
)
def test_bad_input_is_refused_before_connecting(
    sessions, mailbox, uids, add, error
):
    with pytest.raises(MailctlError, match=error):
        flags.plan_mark(sessions, "INBOX", uids, add, ())

    assert {"select_folder", "fetch"}.isdisjoint(mailbox.names())


# ############################################################################
# Executing
# ############################################################################


# ----------------------------------------------------------------------------
def test_execute_adds_then_removes_each_only_where_it_changes(
    sessions, mailbox
):
    plan = flags.plan_mark(sessions, "INBOX", [1, 2, 3], (SEEN,), (FLAGGED,))

    result = flags.execute_mark(sessions, plan)

    stores = [
        c for c in mailbox.calls if c[0] in ("add_flags", "remove_flags")
    ]

    assert stores == [
        ("add_flags", (3,), (b"\\Seen",)),
        ("remove_flags", (2,), (b"\\Flagged",)),
    ]
    assert ("select_folder", "INBOX", False) in mailbox.calls
    assert (result.added, result.removed) == (1, 1)


# ----------------------------------------------------------------------------
def test_executing_an_empty_plan_sends_nothing(sessions, mailbox):
    plan = flags.plan_mark(sessions, "INBOX", [2], (SEEN,), ())

    result = flags.execute_mark(sessions, plan)

    assert (result.added, result.removed) == (0, 0)
    assert WRITES.isdisjoint(mailbox.names())
    assert ("select_folder", "INBOX", False) not in mailbox.calls


# ----------------------------------------------------------------------------
def test_a_refused_store_is_raised_naming_the_folder(sessions, mailbox):
    plan = flags.plan_mark(sessions, "INBOX", [2], (), (FLAGGED,))
    mailbox.failures["remove_flags"] = IMAPClientError("NO read-only")

    with pytest.raises(MailctlError, match="could not clear flags in 'INBOX'"):
        flags.execute_mark(sessions, plan)
