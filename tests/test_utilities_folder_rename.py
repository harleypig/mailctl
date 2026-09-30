"""A folder renamed and its rules repointed (#5), driven as a front-end
would drive it.

The Sieve side is a session-level fake that keeps what it is sent, so the
read-back after a rename sees the stored script; the IMAP side is the real
``ImapSession`` over the conftest double, whose RENAME leaves the
subscription list alone as RFC 3501 section 6.3.5 says a server's does.
"""

import pytest
from imapclient.exceptions import IMAPClientError
from utilities_support import FakeSieveSession, mxroute

from mailctl import MailctlError, utilities
from mailctl.cli import error_text
from mailctl.utilities.folder_rename import (
    FolderRenamePlan,
    execute_folder_rename,
    plan_folder_rename,
    verify_folder_rename,
)

# A script as it might really look: CRLF line ends, a hand comment, a
# trailing comment, tags before the folder, a disabled rule filing into a
# folder under the renamed one, and a folder whose name only starts the
# same way. Every byte not naming a moved folder must survive.
SCRIPT = (
    'require ["fileinto","imap4flags","mailbox"];\r\n'
    "# my own note, keep me\r\n"
    "# rule:[lists]\r\n"
    'if header :contains "list-id" "INBOX.Lists"\r\n'
    "{\r\n"
    '\tfileinto :create "INBOX.Lists"; # into the lists\r\n'
    "\tstop;\r\n"
    "}\r\n"
    "# rule:[github]\r\n"
    'if false # header :contains "from" "noreply@github.com"\r\n'
    "{\r\n"
    '\tfileinto   "INBOX.Lists.GitHub";\r\n'
    "}\r\n"
    "# rule:[listsy]\r\n"
    'if header :contains "subject" "x"\r\n'
    "{\r\n"
    '    fileinto "INBOX.Listsy";\r\n'
    "}\r\n"
)


class StoringSieve(FakeSieveSession):
    """The session-level Sieve fake, keeping what is stored.

    ``fail_put`` makes PUTSCRIPT fail after it has been asked, as a server
    that drops the connection mid-upload would.
    """

    def __init__(self, script):
        super().__init__(script=script)
        self.fail_put = False

    def put_script(self, name, content):
        super().put_script(name, content)

        if self.fail_put:
            raise MailctlError("PUTSCRIPT failed -- connection reset")

        self.script = content


# ----------------------------------------------------------------------------
@pytest.fixture
def sieve() -> StoringSieve:
    return StoringSieve(SCRIPT)


# ----------------------------------------------------------------------------
@pytest.fixture
def mailbox(fake_imap):
    """INBOX.Lists with a subscribed child, and a look-alike beside it."""
    fake_imap.listing += [
        ((), b".", b"INBOX.Lists.GitHub"),
        ((), b".", b"INBOX.Listsy"),
    ]
    fake_imap.subscriptions += [((), b".", b"INBOX.Lists.GitHub")]
    fake_imap.counts["INBOX.Lists"] = (360, 0, 0)

    return fake_imap


# ----------------------------------------------------------------------------
@pytest.fixture
def live(sieve, mailbox, imap_session):
    return mxroute(sieve=sieve, imap=imap_session)


# ----------------------------------------------------------------------------
def plan(live, old="Lists", new="Archive") -> FolderRenamePlan:
    return plan_folder_rename(live, old, new)


# ############################################################################
# Planning -- read-only
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_plan_names_every_folder_that_moves(live):
    result = plan(live)

    assert (result.old, result.new) == ("INBOX.Lists", "INBOX.Archive")
    assert [(m.old, m.new, m.subscribed) for m in result.moves] == [
        ("INBOX.Lists", "INBOX.Archive", True),
        ("INBOX.Lists.GitHub", "INBOX.Archive.GitHub", True),
    ]
    assert result.messages == 360


# ----------------------------------------------------------------------------
def test_the_plan_finds_the_rules_filing_into_it_and_its_children(live):
    """The look-alike INBOX.Listsy is not under INBOX.Lists."""
    result = plan(live)

    assert [(r.rule, r.old, r.new) for r in result.retargets] == [
        ("lists", "INBOX.Lists", "INBOX.Archive"),
        ("github", "INBOX.Lists.GitHub", "INBOX.Archive.GitHub"),
    ]
    assert result.rules_change


# ----------------------------------------------------------------------------
def test_only_the_folder_strings_change(live):
    """Byte-identity of everything else, the rule not touched included:
    the header test naming "INBOX.Lists", both comments, the odd spacing,
    and every CRLF."""
    result = plan(live)

    expected = SCRIPT.replace(
        'fileinto :create "INBOX.Lists";',
        'fileinto :create "INBOX.Archive";',
    ).replace('"INBOX.Lists.GitHub"', '"INBOX.Archive.GitHub"')

    assert result.after == expected
    assert (
        result.after.split("# rule:[listsy]")[1]
        == (SCRIPT.split("# rule:[listsy]")[1])
    )
    assert not result.diff.reformats


# ----------------------------------------------------------------------------
def test_planning_writes_nothing(live, sieve, mailbox):
    plan(live)

    assert not {
        "rename_folder",
        "subscribe_folder",
        "unsubscribe_folder",
        "create_folder",
    } & set(mailbox.names())
    assert sieve.names() == ["get_script"]


# ----------------------------------------------------------------------------
def test_a_case_only_rename_is_allowed(live):
    result = plan(live, "Lists", "INBOX.lists")

    assert result.new == "INBOX.lists"


# ----------------------------------------------------------------------------
def test_a_missing_parent_is_named(live):
    result = plan(live, "Lists", "Old/Lists")

    assert result.new == "INBOX.Old.Lists"
    assert result.missing_parents == ("INBOX.Old",)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("INBOX", "Archive", "INBOX cannot be renamed"),
        ("inbox", "Archive", "INBOX cannot be renamed"),
        ("Nope", "Archive", "no folder named 'INBOX.Nope'"),
        ("lists", "Archive", "'INBOX.Lists' exists, but folder names are"),
        ("Lists", "spam", "'INBOX.spam' already exists"),
        ("Lists", "Spam", "'INBOX.spam' exists, but folder names are"),
        ("Lists", "INBOX.Lists", "is already named"),
        ("Lists", "Lists/Inner", "inside itself"),
    ],
)
def test_a_rename_that_cannot_or_should_not_happen_is_refused(
    live, mailbox, old, new, message
):
    with pytest.raises(MailctlError, match=message):
        plan(live, old, new)

    assert "rename_folder" not in mailbox.names()


# ----------------------------------------------------------------------------
def test_a_child_landing_on_an_existing_folder_is_refused(
    live, mailbox, imap_session
):
    mailbox.listing.append(((), b".", b"INBOX.Archive.GitHub"))
    imap_session.create_folder("INBOX.Unrelated")

    with pytest.raises(MailctlError, match=r"'INBOX\.Archive\.GitHub'"):
        plan(live)


# ----------------------------------------------------------------------------
def test_no_referencing_rule_leaves_the_script_alone(live):
    result = plan(live, "spam", "Junk")

    assert result.retargets == ()
    assert result.after == result.before
    assert not result.rules_change
    assert not result.subscribed


# ############################################################################
# Carrying it out
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_script_is_validated_before_the_folder_moves(
    live, sieve, mailbox, imap_config
):
    """CHECKSCRIPT, then RENAME, then PUTSCRIPT: the order that keeps the
    rules pointing at a missing folder for the shortest time."""
    order = []
    sieve_check, imap_rename = sieve.check_script, mailbox.rename_folder

    sieve.check_script = lambda c: (order.append("check"), sieve_check(c))
    mailbox.rename_folder = lambda o, n: (
        order.append("rename"),
        imap_rename(o, n),
    )
    stored = sieve.put_script
    sieve.put_script = lambda n, c: (order.append("put"), stored(n, c))

    execute_folder_rename(live, imap_config, plan(live))

    assert order == ["check", "rename", "put"]


# ----------------------------------------------------------------------------
def test_a_rename_carries_subscriptions_and_lands_whole(
    live, sieve, mailbox, imap_config
):
    events = []
    before = plan(live)

    result = execute_folder_rename(live, imap_config, before, events.append)

    assert ("rename_folder", "INBOX.Lists", "INBOX.Archive") in mailbox.calls
    assert result.subscribed == ("INBOX.Archive", "INBOX.Archive.GitHub")
    assert result.unsubscribed == ("INBOX.Lists", "INBOX.Lists.GitHub")
    assert result.subscription_errors == ()
    assert sieve.script == before.after
    assert result.backup is not None
    assert result.backup.read_bytes() == SCRIPT.encode()
    assert [type(event).__name__ for event in events] == [
        "ScriptBackedUp",
        "FolderRenamed",
        "SubscriptionChanged",
        "SubscriptionChanged",
        "SubscriptionChanged",
        "SubscriptionChanged",
        "ScriptUploaded",
    ]
    assert result.ok, result.checks


# ----------------------------------------------------------------------------
def test_an_unsubscribed_folder_stays_unsubscribed(
    live, sieve, mailbox, imap_config
):
    result = execute_folder_rename(live, imap_config, plan(live, "spam", "J"))

    assert result.subscribed == ()
    assert "subscribe_folder" not in mailbox.names()
    assert sieve.names() == ["get_script", "get_script"]
    assert result.backup is None
    assert result.ok, result.checks


# ----------------------------------------------------------------------------
def test_a_checkscript_refusal_stops_before_any_write(
    live, sieve, mailbox, imap_config
):
    sieve.reject = True

    with pytest.raises(MailctlError, match="Nothing was renamed, and the"):
        execute_folder_rename(live, imap_config, plan(live))

    assert "rename_folder" not in mailbox.names()
    assert "put_script" not in sieve.names()


# ----------------------------------------------------------------------------
def test_a_rename_refused_by_the_server_uploads_nothing(
    live, sieve, mailbox, imap_config
):
    mailbox.failures["rename_folder"] = IMAPClientError("NO [INUSE]")

    with pytest.raises(MailctlError) as caught:
        execute_folder_rename(live, imap_config, plan(live))

    message = str(caught.value)

    assert "could not rename folder 'INBOX.Lists'" in message
    assert (
        "Nothing was renamed, and the filter set on the server is as"
        in message
    )
    assert "backed up to" in message
    assert "put_script" not in sieve.names()
    assert sieve.names().count("check_script") == 1


# ----------------------------------------------------------------------------
def test_a_failed_subscribe_is_reported_and_the_rules_still_follow(
    live, sieve, mailbox, imap_config
):
    """The folder has moved; leaving the rules behind would be worse."""
    mailbox.failures["subscribe_folder"] = IMAPClientError("NO quota")

    result = execute_folder_rename(live, imap_config, plan(live))

    assert "put_script" in sieve.names()
    assert len(result.subscription_errors) == 2
    assert (
        "could not subscribe to folder 'INBOX.Archive'"
        in (result.subscription_errors[0])
    )
    assert not result.ok

    failed = [check.label for check in result.checks if not check.ok]

    assert failed == [
        "'INBOX.Archive' is subscribed, as 'INBOX.Lists' was",
        "those subscribed before are subscribed now",
    ]


# ----------------------------------------------------------------------------
def test_an_upload_failing_after_the_rename_says_how_to_undo(
    live, sieve, mailbox, imap_config
):
    sieve.fail_put = True

    with pytest.raises(MailctlError) as caught:
        execute_folder_rename(live, imap_config, plan(live))

    assert "rename 'INBOX.Archive' back to 'INBOX.Lists'" in str(caught.value)
    assert caught.value.code == "rename_interrupted"

    message = error_text(caught.value)

    assert "'INBOX.Lists' was renamed to 'INBOX.Archive'" in message
    assert "is still the old one" in message
    assert "2 filters still file into the old name" in message
    assert "'mailctl folder rename INBOX.Archive INBOX.Lists'" in message
    assert sieve.names().count("put_script") == 1


# ----------------------------------------------------------------------------
def test_an_activation_failure_after_the_store_says_it_was_stored(
    live, sieve, mailbox, imap_config
):
    def refuse(name):
        raise MailctlError("SETACTIVE failed")

    sieve.set_active = refuse

    with pytest.raises(MailctlError, match="stored as 'managesieve'"):
        execute_folder_rename(live, imap_config, plan(live))


# ############################################################################
# Reading it back
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_rule_left_on_the_old_name_fails_the_check(live, imap_session):
    """What the issue's by-hand run could not see: checking the folder
    list alone reports success while a rule still points at nothing."""
    before = plan(live)
    imap_session.rename_folder("INBOX.Lists", "INBOX.Archive")
    imap_session.subscribe("INBOX.Archive")

    checks = {c.label: c for c in verify_folder_rename(live, before)}
    rules = checks[
        "no filter in 'managesieve' files into 'INBOX.Lists' or a "
        "folder under it"
    ]

    assert not rules.ok
    assert rules.detail == "'github' still does, 'lists' still does"


# ----------------------------------------------------------------------------
def test_an_unsubscribed_new_name_fails_the_check(live, imap_session):
    """The #5 comment's failure: LIST and STATUS pass, LSUB does not."""
    before = plan(live)
    imap_session.rename_folder("INBOX.Lists", "INBOX.Archive")

    failed = [c.label for c in verify_folder_rename(live, before) if not c.ok]

    assert "'INBOX.Archive' is subscribed, as 'INBOX.Lists' was" in failed
    assert "'INBOX.Lists' is off the subscription list" in failed


# ----------------------------------------------------------------------------
def test_fewer_messages_after_fails_the_check(live, mailbox, imap_session):
    before = plan(live)
    imap_session.rename_folder("INBOX.Lists", "INBOX.Archive")
    mailbox.counts["INBOX.Archive"] = (359, 0, 0)

    checks = verify_folder_rename(live, before)
    count = next(c for c in checks if "message" in c.label)

    assert not count.ok
    assert count.label == (
        "'INBOX.Archive' holds 359 messages; 'INBOX.Lists' held 360"
    )


# ----------------------------------------------------------------------------
def test_a_rename_that_never_happened_fails_every_folder_check(live):
    checks = verify_folder_rename(live, plan(live))
    failed = [c.label for c in checks if not c.ok]

    assert "'INBOX.Lists' is gone" in failed
    assert "'INBOX.Archive' exists" in failed
    assert "the 1 folder under it moved with it" in failed


# ----------------------------------------------------------------------------
def test_the_utility_is_exported():
    assert utilities.folder_rename.plan_folder_rename is plan_folder_rename
