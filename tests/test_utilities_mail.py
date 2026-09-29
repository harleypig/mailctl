"""The existing-mail pass and deriving a rule from a message, driven as a
front-end would drive them.

No argparse and no stdout here: every test builds plain inputs, calls
the utility, and asserts on what comes back and on what the fakes were
asked to do.

The Sieve side is a session-level fake (``FakeSieveSession``); the IMAP
side is the real ``ImapSession`` over the ``FakeIMAPClient`` double from
conftest, so folder normalization and planning run for real.
"""

import email
import email.message

import pytest
from utilities_support import criteria

from mailctl import MailctlError, utilities
from mailctl.criteria import Criteria
from mailctl.providers.base import FetchedMessage, MessageSummary
from mailctl.utilities.rules import (
    ActionSpec,
)

# ############################################################################
# Fakes and helpers
# ############################################################################


# ----------------------------------------------------------------------------
def headers(**fields) -> email.message.Message:
    text = "".join(
        f"{name.replace('_', '-')}: {value}\r\n"
        for name, value in fields.items()
    )

    return email.message_from_string(text + "\r\n")


# ----------------------------------------------------------------------------
def raw_message(sender: str, subject: str) -> bytes:
    return (
        f"From: Someone <{sender}>\r\nSubject: {subject}\r\n"
        f"Date: Tue, 3 Feb 2026 04:05:06 +0000\r\n\r\n"
    ).encode()


# ############################################################################
# The existing-mail pass
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.fixture
def mailbox(fake_imap):
    fake_imap.messages = {
        1: raw_message("noreply@github.com", "PR opened"),
        2: raw_message("noreply@github.com", "Issue closed"),
        3: raw_message("friend@example.com", "Lunch"),
    }

    return fake_imap


# ----------------------------------------------------------------------------
def test_planning_the_mail_pass_is_read_only(sessions, mailbox):
    plan = utilities.mail.plan_mail(
        sessions, criteria(), ActionSpec(), "INBOX", "INBOX.Lists"
    )

    assert plan.uids == [1, 2]
    assert plan.moves
    assert ("select_folder", "INBOX", True) in mailbox.calls
    assert "move" not in mailbox.names()


# ----------------------------------------------------------------------------
def test_the_recheck_keeps_only_the_candidates_the_rule_matches():
    """The host's search is coarse; the utility decides (ADR 0007).

    Both candidates contain the address, as a substring search would find
    them, but only one is it exactly under ``--compare is``.
    """
    rule = Criteria(compare="is")
    rule.add("from", "a@example.com")
    candidates = [
        FetchedMessage(
            headers(From=sender),
            MessageSummary(uid, "", sender, "", "INBOX"),
        )
        for uid, sender in ((1, "xa@example.com"), (2, "a@example.com"))
    ]

    kept = utilities.mail.recheck(rule, candidates)

    assert [summary.uid for summary in kept] == [2]


# ----------------------------------------------------------------------------
def test_the_recheck_reads_decoded_and_raw_header_forms():
    """Sieve compares the decoded value; the literal encoded text still
    finds its message."""
    values = utilities.mail.header_values(
        headers(Subject="=?utf-8?q?caf=C3=A9?=")
    )

    assert values["SUBJECT"] == ["café", "=?utf-8?q?caf=C3=A9?="]


# ----------------------------------------------------------------------------
def test_an_approved_plan_is_executed(sessions, mailbox):
    plan = utilities.mail.plan_mail(
        sessions, criteria(), ActionSpec(), "INBOX", "INBOX.Lists"
    )

    result = utilities.mail.execute_mail(sessions, plan, max_messages=2)

    assert result.moved == 2
    assert ("move", (1, 2), "INBOX.Lists") in mailbox.calls


# ----------------------------------------------------------------------------
def test_keep_with_a_folder_copies_rather_than_moves(sessions, mailbox):
    """``fileinto "X"; keep;`` files a copy and leaves the original (#188).

    Red if the pass ignores ``keep`` and moves, or expunges after copying.
    """
    plan = utilities.mail.plan_mail(
        sessions,
        criteria(),
        ActionSpec(fileinto="Lists", keep=True),
        "INBOX",
        "INBOX.Lists",
    )

    assert plan.copies
    assert not plan.moves

    result = utilities.mail.execute_mail(sessions, plan, max_messages=2)

    assert result.copied == 2
    assert result.moved == 0
    assert ("copy", (1, 2), "INBOX.Lists") in mailbox.calls
    assert not {"move", "expunge", "uid_expunge"} & set(mailbox.names())
    assert not any(
        call[0] == "add_flags" and b"\\Deleted" in call[2]
        for call in mailbox.calls
    )


# ----------------------------------------------------------------------------
def test_a_kept_copy_carries_the_flags_added_first(sessions, mailbox):
    """``addflag`` reaches what a rule files and what it keeps, so the
    originals are flagged before the copy is made. Red if the copy goes
    first, and the copies lack the flag."""
    plan = utilities.mail.plan_mail(
        sessions,
        criteria(),
        ActionSpec(fileinto="Lists", keep=True, flags=("\\Seen",)),
        "INBOX",
        "INBOX.Lists",
    )

    result = utilities.mail.execute_mail(sessions, plan, max_messages=2)

    assert (result.flagged, result.copied, result.moved) == (2, 2, 0)
    assert [
        name for name in mailbox.names() if name in ("add_flags", "copy")
    ] == [
        "add_flags",
        "copy",
    ]


# ----------------------------------------------------------------------------
def test_discard_with_keep_neither_deletes_nor_files(sessions, mailbox):
    """``discard; keep;`` leaves the message where it is (RFC 5228 section
    4.4), and a discarding rule writes no ``fileinto``. Red if keep is lost
    under discard, which deletes, or if the folder survives, which files."""
    plan = utilities.mail.plan_mail(
        sessions,
        criteria(),
        ActionSpec(fileinto="Lists", discard=True, keep=True, flags=("x",)),
        "INBOX",
        "INBOX.Lists",
    )

    assert not plan.discard
    assert not plan.moves
    assert not plan.copies

    result = utilities.mail.execute_mail(sessions, plan, max_messages=2)

    assert (result.flagged, result.deleted, result.copied) == (2, 0, 0)
    assert not {"copy", "move", "expunge", "uid_expunge"} & set(
        mailbox.names()
    )


# ----------------------------------------------------------------------------
def test_discard_without_keep_still_deletes(sessions, mailbox):
    plan = utilities.mail.plan_mail(
        sessions, criteria(), ActionSpec(discard=True), "INBOX", ""
    )

    assert plan.discard
    assert utilities.mail.execute_mail(sessions, plan, 2).deleted == 2


# ----------------------------------------------------------------------------
def identified(sender: str, subject: str, message_id: str) -> bytes:
    return raw_message(sender, subject).replace(
        b"\r\n\r\n", f"\r\nMessage-ID: <{message_id}>\r\n\r\n".encode()
    )


# ----------------------------------------------------------------------------
@pytest.fixture
def rerun(fake_imap):
    """The inbox a second ``apply --fileinto Lists --keep`` finds: the
    Lists folder already holds a copy of the first match."""
    fake_imap.messages = {
        1: identified("noreply@github.com", "PR opened", "1@gh"),
        2: identified("noreply@github.com", "Issue closed", "2@gh"),
        3: raw_message("friend@example.com", "Lunch"),
    }
    fake_imap.folder_messages = {
        "INBOX.Lists": {
            9: identified("noreply@github.com", "PR opened", "1@gh"),
        },
    }

    return fake_imap


# ----------------------------------------------------------------------------
def plan_copy(sessions, spec=None, rule=None):
    return utilities.mail.plan_mail(
        sessions,
        rule or criteria(),
        spec or ActionSpec(fileinto="Lists", keep=True),
        "INBOX",
        "INBOX.Lists",
    )


# ----------------------------------------------------------------------------
def test_a_second_copying_run_copies_only_what_the_folder_lacks(
    sessions, rerun
):
    """Re-running ``apply --keep`` duplicated every copy (#192). Red if
    the plan ignores what the destination holds: the copy then sends both
    UIDs again."""
    plan = plan_copy(sessions)

    assert plan.held == (1,)
    assert plan.copy_uids == [2]

    result = utilities.mail.execute_mail(sessions, plan, max_messages=2)

    assert result.copied == 1
    assert [call for call in rerun.calls if call[0] == "copy"] == [
        ("copy", (2,), "INBOX.Lists")
    ]


# ----------------------------------------------------------------------------
def test_the_destination_is_only_read_while_planning(sessions, rerun):
    """The check is part of the read-only plan. Red if it selects the
    destination read-write or writes anything."""
    plan_copy(sessions)

    assert ("select_folder", "INBOX.Lists", True) in rerun.calls
    assert ("select_folder", "INBOX.Lists", False) not in rerun.calls
    assert not {"copy", "move", "add_flags", "expunge"} & set(rerun.names())


# ----------------------------------------------------------------------------
def test_a_message_with_no_message_id_is_copied_and_said_so(sessions, rerun):
    """Nothing identifies it, so it cannot be looked for; it is copied,
    and the plan names it."""
    rerun.messages[4] = raw_message("noreply@github.com", "No id")

    plan = plan_copy(sessions)

    assert plan.unidentified == (4,)
    assert plan.copy_uids == [2, 4]


# ----------------------------------------------------------------------------
def test_the_destination_is_not_searched_when_nothing_can_be_looked_for(
    sessions, mailbox
):
    plan = plan_copy(sessions)

    assert plan.unidentified == (1, 2)
    assert ("select_folder", "INBOX.Lists", True) not in mailbox.calls


# ----------------------------------------------------------------------------
def test_a_destination_still_to_be_created_holds_nothing(sessions, rerun):
    rerun.folder_messages = {"INBOX.New": {}}

    plan = utilities.mail.plan_mail(
        sessions,
        criteria(),
        ActionSpec(fileinto="New", keep=True),
        "INBOX",
        "INBOX.New",
    )

    assert plan.held == ()
    assert plan.copy_uids == [1, 2]
    assert not any(
        call[:2] == ("select_folder", "INBOX.New") for call in rerun.calls
    )


# ----------------------------------------------------------------------------
def test_the_destination_search_drops_the_date_and_state_filters(
    sessions, rerun
):
    """A copy is read or flagged on its own, so ``--unread`` would miss a
    copy somebody has read. Red if the filters reach the second search."""
    rule = criteria()
    rule.unread = True

    plan_copy(sessions, rule=rule)

    searches = [call[1] for call in rerun.calls if call[0] == "search"]

    assert searches == [
        [["FROM", "noreply@github.com"], "UNSEEN"],
        [["FROM", "noreply@github.com"]],
    ]


# ----------------------------------------------------------------------------
def test_a_rule_of_state_filters_alone_looks_for_every_message_id(
    sessions, rerun
):
    """With no header or body test there is nothing to narrow the
    destination by, so every message there with a Message-ID is read."""
    plan = plan_copy(sessions, rule=Criteria(unread=True))

    searches = [call[1] for call in rerun.calls if call[0] == "search"]

    assert searches[-1] == [["HEADER", "Message-ID", ""]]
    assert plan.held == (1,)


# ----------------------------------------------------------------------------
def test_a_move_does_not_look_in_the_destination(sessions, rerun):
    """A move takes the message out of the source, so a second run never
    finds it again; nothing needs checking."""
    plan = plan_copy(sessions, spec=ActionSpec(fileinto="Lists"))

    assert plan.moves
    assert plan.held == ()
    assert ("select_folder", "INBOX.Lists", True) not in rerun.calls


# ----------------------------------------------------------------------------
def test_a_copy_the_folder_already_holds_whole_changes_nothing(
    sessions, rerun
):
    rerun.folder_messages["INBOX.Lists"][10] = identified(
        "noreply@github.com", "Issue closed", "2@gh"
    )

    plan = plan_copy(sessions)

    assert not plan.changes

    result = utilities.mail.execute_mail(sessions, plan, max_messages=2)

    assert result.copied == 0
    assert "copy" not in rerun.names()


# ----------------------------------------------------------------------------
def test_flags_still_reach_originals_the_folder_already_holds(sessions, rerun):
    """``addflag`` reaches the kept original whether or not it is copied
    again, so a held match is still flagged."""
    plan = plan_copy(
        sessions, spec=ActionSpec(fileinto="Lists", keep=True, flags=("x",))
    )

    assert plan.changes

    result = utilities.mail.execute_mail(sessions, plan, max_messages=2)

    assert (result.flagged, result.copied) == (2, 1)
    assert ("add_flags", (1, 2), (b"x",)) in rerun.calls


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("<a@b>", "<a@b>"),
        (" <a@b>\r\n", "<a@b>"),
        ("<long-id\r\n @example.com>", "<long-id@example.com>"),
        (None, None),
        ("  ", None),
    ],
)
def test_a_message_id_is_read_without_its_folding(header, expected):
    message = email.message.Message()

    if header is not None:
        message["Message-ID"] = header

    assert utilities.mail.message_id(message) == expected


# ----------------------------------------------------------------------------
def test_a_plan_over_the_cap_is_refused_whole(sessions, mailbox):
    plan = utilities.mail.plan_mail(
        sessions, criteria(), ActionSpec(), "INBOX", "INBOX.Lists"
    )

    with pytest.raises(MailctlError, match="NO existing message"):
        utilities.mail.check_message_cap(plan, 1)

    with pytest.raises(MailctlError, match="NO existing message"):
        utilities.mail.execute_mail(sessions, plan, max_messages=1)

    assert "move" not in mailbox.names()


# ----------------------------------------------------------------------------
def test_a_mail_pass_that_would_do_nothing_is_refused(sessions, imap_config):
    folder = utilities.folders.plan_folder(sessions, imap_config, None)

    with pytest.raises(MailctlError, match="nothing to do"):
        utilities.mail.require_mail_action(folder, ActionSpec())

    utilities.mail.require_mail_action(folder, ActionSpec(discard=True))


# ############################################################################
# Deriving a rule from a message
# ############################################################################


# ----------------------------------------------------------------------------
def test_auto_derivation_prefers_list_id_over_from():
    listed = headers(From="A <a@x.org>", List_Id="Dev <dev.x.org>")
    plain = headers(From="A <a@x.org>")

    assert utilities.mail.derive_criteria(listed).criteria.terms[0].value == (
        "dev.x.org"
    )
    assert (
        utilities.mail.derive_criteria(plain).criteria.terms[0].value
        == "a@x.org"
    )


# ----------------------------------------------------------------------------
def test_a_missing_header_is_reported_as_skipped_not_raised():
    derived = utilities.mail.derive_criteria(
        headers(From="a@x.org"), "cc,from"
    )

    assert derived.skipped == ["cc"]
    assert [term.value for term in derived.criteria.terms] == ["a@x.org"]

    empty = utilities.mail.derive_criteria(headers(From="a@x.org"), "cc")

    with pytest.raises(MailctlError):
        empty.criteria.require_terms()


# ############################################################################
# Criteria like a message (search --like)
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.fixture
def listed(fake_imap):
    fake_imap.messages = {
        1: raw_message("noreply@github.com", "PR opened"),
        7: (
            b"From: Dev <dev@x.org>\r\nSubject: Digest\r\n"
            b"List-Id: Dev list <dev.x.org>\r\n\r\n"
        ),
    }

    return fake_imap


# ----------------------------------------------------------------------------
def test_criteria_like_a_message_are_derived_from_it(sessions, listed):
    like = utilities.mail.criteria_like(sessions, "INBOX", 7)

    assert like.message.uid == 7
    assert like.message.folder == "INBOX"
    assert like.message.headers["Subject"] == "Digest"
    assert like.criteria == criteria_of(("List-Id", "dev.x.org"))
    assert like.skipped == []


# ----------------------------------------------------------------------------
def test_the_folder_is_normalized_before_the_message_is_read(sessions, listed):
    like = utilities.mail.criteria_like(sessions, "Lists", 7)

    assert like.message.folder == "INBOX.Lists"
    assert ("select_folder", "INBOX.Lists", True) in listed.calls


# ----------------------------------------------------------------------------
def test_derive_chooses_the_headers(sessions, listed):
    like = utilities.mail.criteria_like(
        sessions, "INBOX", 7, derive="from,cc,subject"
    )

    assert like.criteria == criteria_of(
        ("From", "dev@x.org"), ("Subject", "Digest")
    )
    assert like.skipped == ["cc"]


# ----------------------------------------------------------------------------
def test_explicit_criteria_override_and_extend_what_is_derived(
    sessions, listed
):
    explicit = Criteria(match="all", compare="is")
    explicit.add("list-id", "other.x.org")
    explicit.add("subject", "Digest")

    like = utilities.mail.criteria_like(
        sessions, "INBOX", 7, explicit, derive="list-id,from"
    )

    assert like.criteria == criteria_of(
        ("From", "dev@x.org"),
        ("List-Id", "other.x.org"),
        ("Subject", "Digest"),
        match="all",
        compare="is",
    )


# ----------------------------------------------------------------------------
def test_a_message_lacking_every_header_asked_for_derives_nothing(
    sessions, listed
):
    like = utilities.mail.criteria_like(sessions, "INBOX", 1, derive="cc")

    assert not like.criteria
    assert like.skipped == ["cc"]


# ----------------------------------------------------------------------------
def test_a_uid_that_is_not_there_is_refused_naming_folder_and_uid(
    sessions, listed
):
    with pytest.raises(MailctlError, match=r"uid 99 in 'INBOX.Lists'"):
        utilities.mail.criteria_like(sessions, "Lists", 99)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("uid", [0, -3])
def test_a_uid_below_one_is_refused_before_any_fetch(sessions, listed, uid):
    with pytest.raises(MailctlError, match="start at 1"):
        utilities.mail.criteria_like(sessions, "INBOX", uid)

    assert not any(call[0] == "fetch" for call in listed.calls)


# ----------------------------------------------------------------------------
def test_criteria_like_a_message_reads_and_marks_nothing(sessions, listed):
    utilities.mail.criteria_like(sessions, "INBOX", 7)

    assert listed.marked_seen == set()
    assert {call[0] for call in listed.calls} <= {
        "login",
        "select_folder",
        "fetch",
    }


# ----------------------------------------------------------------------------
def criteria_of(*pairs, match="any", compare="contains") -> Criteria:
    result = Criteria(match=match, compare=compare)

    for header, value in pairs:
        result.add(header, value)

    return result


# ############################################################################
# Folder creation happens on execute
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_mail_pass_creates_its_folder_once_and_only_on_execute(
    sessions, imap_config, mailbox
):
    sessions.transport.sieve = None
    folder = utilities.folders.plan_folder(
        sessions, imap_config, "New", create=True
    )
    plan = utilities.mail.plan_mail(
        sessions, criteria(), ActionSpec(), "INBOX", "INBOX.New"
    )

    assert utilities.folders.folder_pending(sessions, folder)
    assert "create_folder" not in mailbox.names()

    utilities.mail.execute_mail(sessions, plan, 10, folder)
    utilities.folders.realize_folder(sessions, folder)

    assert mailbox.names().count("create_folder") == 1
    assert not utilities.folders.folder_pending(sessions, folder)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("spec", "destination", "noop"),
    [
        (ActionSpec(keep=True), "", True),
        (ActionSpec(fileinto="INBOX"), "inbox", True),
        (ActionSpec(fileinto="Lists"), "INBOX.Lists", False),
        (ActionSpec(keep=True, flags=("\\Seen",)), "", False),
        (ActionSpec(discard=True), "", False),
        (ActionSpec(fileinto="Lists", keep=True), "INBOX.Lists", False),
        (ActionSpec(fileinto="INBOX", keep=True), "inbox", True),
        (ActionSpec(discard=True, keep=True), "", True),
        (ActionSpec(discard=True, keep=True), "INBOX.Lists", True),
        (ActionSpec(discard=True, keep=True, flags=("\\Seen",)), "", False),
    ],
)
def test_a_mail_pass_that_changes_nothing_is_recognised(
    spec, destination, noop
):
    assert utilities.mail.mail_pass_is_noop(spec, "INBOX", destination) is noop
