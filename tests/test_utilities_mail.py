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
    ],
)
def test_a_mail_pass_that_changes_nothing_is_recognised(
    spec, destination, noop
):
    assert utilities.mail.mail_pass_is_noop(spec, "INBOX", destination) is noop
