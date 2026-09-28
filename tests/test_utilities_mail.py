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
def test_a_search_picks_the_newest_match_and_says_how_many(sessions, mailbox):
    picked = utilities.mail.pick_message(sessions, "INBOX", search="ALL")

    assert picked.uid == 3
    assert picked.candidates == 3
    assert picked.headers["Subject"] == "Lunch"


# ----------------------------------------------------------------------------
def test_a_search_with_no_match_is_refused(sessions, fake_imap):
    with pytest.raises(MailctlError, match="matched"):
        utilities.mail.pick_message(sessions, "INBOX", search="FROM nobody")


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
# Folder creation happens on execute
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_mail_pass_creates_its_folder_once_and_only_on_execute(
    sessions, imap_config, mailbox
):
    sessions.sieve = None
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
