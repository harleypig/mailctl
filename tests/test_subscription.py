"""Folder subscription: existing and visible are different questions.

Issue #38. A folder created over IMAP exists and receives mail, but a
webmail client draws its folder tree from ``LSUB`` rather than ``LIST`` --
so a folder that was never subscribed to is invisible to the person who
asked for it, while every check mailctl ran said the rule was fine. The
tool could not even see the condition it was creating, because it cached
``LIST`` alone.

Two properties carry this file:

* **The subscribed set is read from the server, separately.** Nothing about
  ``LIST`` implies it, and no server advertises whether its ``CREATE``
  subscribes on its own, so the answer is observed rather than assumed.
* **Anything invisible is said out loud** -- a failed subscription, and a
  deliberately declined one alike. An opt-out that printed nothing would
  reproduce the original bug for whoever passes the flag without knowing
  what it costs.
"""

from types import SimpleNamespace

import pytest
from imapclient.exceptions import IMAPClientError

from mailctl import MailctlError, engine
from mailctl.cli import (
    build_parser,
    prepare_folder,
    render_event,
    report_folder_creation,
)
from mailctl.components.imap import FolderCreation
from mailctl.engine import Sessions
from mailctl.providers.mxroute.imap import new_imap_session

NEW_FOLDER = "INBOX.Lists.GitHub"


# ############################################################################
# Helpers
# ############################################################################


# ----------------------------------------------------------------------------
def add_args(*extra: str):
    """Parse an ``add`` that would create its target folder."""
    return build_parser().parse_args(
        [
            "add",
            "--from",
            "noreply@github.com",
            "--fileinto",
            "Lists/GitHub",
            "--create-folder",
            *extra,
        ]
    )


# ----------------------------------------------------------------------------
def sieve_without_mailbox():
    """A Sieve session double whose server lacks the mailbox extension.

    That is the branch where the folder is made over IMAP now, which is the
    one subscription applies to -- ``fileinto :create`` leaves the creating
    to the server at delivery time.
    """
    return SimpleNamespace(missing_extensions=lambda needed: set(needed))


# ############################################################################
# The two folder views
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_subscribed_set_is_read_and_is_not_the_folder_list(imap_session):
    """LSUB is what a mail client draws; LIST is only what exists.

    The double starts with ``INBOX.spam`` present and unsubscribed, which is
    the state observed on a real account. Deriving one view from the other
    would make that state unrepresentable -- and it is the whole bug.
    """
    assert "INBOX.spam" in imap_session.folders
    assert "INBOX.spam" not in imap_session.subscribed_folders

    assert imap_session.exists("INBOX.spam") is True
    assert imap_session.is_subscribed("INBOX.spam") is False


# ----------------------------------------------------------------------------
def test_the_subscription_check_is_exact_except_for_inbox(imap_session):
    """Folder names are matched the way ``exists`` matches them (#56)."""
    assert imap_session.is_subscribed("INBOX.Lists") is True
    assert imap_session.is_subscribed("INBOX.lists") is False
    assert imap_session.is_subscribed("inbox") is True


# ----------------------------------------------------------------------------
def test_the_listing_subscription_check_is_exact_except_for_inbox():
    """``folders`` and ``test`` report through FolderListing, not the
    session, and must not merge two differently cased folders (#56)."""
    listing = engine.FolderListing(
        ".", ["INBOX", "INBOX.Lists", "INBOX.lists"], ["inbox", "INBOX.Lists"]
    )

    assert listing.unsubscribed == ["INBOX.lists"]


# ----------------------------------------------------------------------------
def test_an_lsub_failure_is_named_rather_than_treated_as_empty(
    fake_imap, imap_config
):
    """An empty subscription list and an unanswered one look identical.

    Shrugging LSUB off would leave the tool reporting the exact condition
    it exists to detect -- confidently, and wrongly.
    """
    fake_imap.failures["list_sub_folders"] = IMAPClientError("nope")

    with pytest.raises(MailctlError, match="LSUB failed"):
        new_imap_session(imap_config).open()


# ----------------------------------------------------------------------------
def test_the_delimiter_still_comes_from_the_list_response(imap_session):
    """Reading a second listing must not disturb the discovered delimiter."""
    assert imap_session.delimiter == "."


# ############################################################################
# Creating a folder
# ############################################################################


# ----------------------------------------------------------------------------
def test_creating_a_folder_subscribes_to_it(imap_session, fake_imap):
    """The fix. A fileinto target is by definition one the user should see."""
    result = imap_session.create_folder(NEW_FOLDER)

    assert result == FolderCreation(folder=NEW_FOLDER, subscribed=True)
    assert ("subscribe_folder", NEW_FOLDER) in fake_imap.calls
    assert imap_session.is_subscribed(NEW_FOLDER) is True


# ----------------------------------------------------------------------------
def test_the_folder_is_created_before_it_is_subscribed(
    imap_session, fake_imap
):
    """Order is not cosmetic: the folder has to exist to be subscribed."""
    imap_session.create_folder(NEW_FOLDER)
    names = fake_imap.names()

    assert names.index("create_folder") < names.index("subscribe_folder")


# ----------------------------------------------------------------------------
def test_creating_a_folder_can_decline_to_subscribe(imap_session, fake_imap):
    """A deliberately hidden folder is a real want, not a debug switch.

    Somewhere to file a high-volume list that should leave the inbox
    without cluttering the sidebar.
    """
    result = imap_session.create_folder(NEW_FOLDER, subscribe=False)

    assert result == FolderCreation(folder=NEW_FOLDER, subscribed=False)
    assert result.subscribe_error == ""
    assert "subscribe_folder" not in fake_imap.names()

    assert imap_session.exists(NEW_FOLDER) is True
    assert imap_session.is_subscribed(NEW_FOLDER) is False


# ----------------------------------------------------------------------------
def test_a_failed_subscription_is_not_a_failed_creation(
    imap_session, fake_imap
):
    """The folder exists and mail filed there will arrive -- say that.

    Raising would abandon the command with the folder already made and the
    rule unwritten, and rolling the folder back would trade a visibility
    problem for a data one.
    """
    fake_imap.failures["subscribe_folder"] = IMAPClientError("denied")

    result = imap_session.create_folder(NEW_FOLDER)

    assert result.subscribed is False
    assert NEW_FOLDER in result.subscribe_error
    assert imap_session.exists(NEW_FOLDER) is True


# ----------------------------------------------------------------------------
def test_a_failed_subscription_does_not_undo_the_folder(
    imap_session, fake_imap
):
    """Nothing walks the creation back; deletion is the irreversible one."""
    fake_imap.failures["subscribe_folder"] = IMAPClientError("denied")

    imap_session.create_folder(NEW_FOLDER)
    names = fake_imap.names()

    assert names.count("create_folder") == 1
    assert "delete_folder" not in names
    assert "unsubscribe_folder" not in names


# ----------------------------------------------------------------------------
def test_a_subscribe_the_server_ignored_is_caught_by_rereading_lsub(
    imap_session, fake_imap
):
    """The post-condition check, and the reason it is not ceremony.

    A server that answers OK and does nothing is indistinguishable from one
    that worked, unless the subscribed set is read back. Trusting the OK is
    the same mistake as trusting that CREATE subscribes.
    """
    fake_imap.subscribe_takes_effect = False

    result = imap_session.create_folder(NEW_FOLDER)

    assert result.subscribed is False
    assert "LSUB" in result.subscribe_error
    assert imap_session.exists(NEW_FOLDER) is True


# ----------------------------------------------------------------------------
def test_a_create_failure_still_raises(imap_session, fake_imap):
    """A folder that does not exist is a different failure entirely."""
    fake_imap.failures["create_folder"] = IMAPClientError("no room")

    with pytest.raises(MailctlError, match="could not create folder"):
        imap_session.create_folder(NEW_FOLDER)

    assert "subscribe_folder" not in fake_imap.names()


# ############################################################################
# Subscribing on its own
# ############################################################################


# ----------------------------------------------------------------------------
def test_subscribe_and_unsubscribe_move_the_folder_in_and_out_of_lsub(
    imap_session,
):
    """The primitives are a pair; hiding a folder is as real as showing it."""
    imap_session.subscribe("INBOX.spam")

    assert imap_session.is_subscribed("INBOX.spam") is True

    imap_session.unsubscribe("INBOX.spam")

    assert imap_session.is_subscribed("INBOX.spam") is False
    assert imap_session.exists("INBOX.spam") is True


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("method", "failure", "message"),
    [
        pytest.param(
            "subscribe",
            "subscribe_folder",
            "could not subscribe",
            id="subscribe",
        ),
        pytest.param(
            "unsubscribe",
            "unsubscribe_folder",
            "could not unsubscribe",
            id="unsubscribe",
        ),
    ],
)
def test_the_primitives_raise_and_name_the_folder(
    imap_session, fake_imap, method, failure, message
):
    """Called directly they are loud; only ``create_folder`` softens it."""
    fake_imap.failures[failure] = IMAPClientError("denied")

    with pytest.raises(MailctlError, match=message) as caught:
        getattr(imap_session, method)("INBOX.spam")

    assert "INBOX.spam" in str(caught.value)


# ############################################################################
# The CLI says so out loud
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_cli_reports_a_subscribed_folder(capsys):
    report_folder_creation(FolderCreation(folder=NEW_FOLDER, subscribed=True))

    assert "subscribed" in capsys.readouterr().out


# ----------------------------------------------------------------------------
def test_the_cli_says_when_a_folder_was_deliberately_left_hidden(capsys):
    """A silent opt-out reproduces the bug for whoever passes the flag.

    Months later that flag is a line in a saved command nobody remembers
    choosing, and the folder is invisible for a reason nobody can see.
    """
    report_folder_creation(FolderCreation(folder=NEW_FOLDER, subscribed=False))

    captured = capsys.readouterr()

    assert "--no-subscribe" in captured.out
    assert "will not appear in webmail" in captured.out


# ----------------------------------------------------------------------------
def test_the_cli_warns_on_a_failed_subscription_without_calling_it_a_failure(
    capsys,
):
    """It has to say both halves: the folder works, and it is invisible."""
    report_folder_creation(
        FolderCreation(
            folder=NEW_FOLDER,
            subscribed=False,
            subscribe_error="could not subscribe to folder -- denied",
        )
    )

    captured = capsys.readouterr()

    assert "warning:" in captured.err
    assert "mail filed there will arrive" in captured.err
    assert "not appear in webmail" in captured.err


# ############################################################################
# The flag, end to end through prepare_folder
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "command",
    [
        pytest.param(["add", "--from", "a@b.c"], id="add"),
        pytest.param(["from-message", "--uid", "1"], id="from-message"),
        pytest.param(["apply", "--from", "a@b.c"], id="apply"),
    ],
)
def test_every_folder_creating_command_takes_the_flag(command):
    """``--create-folder`` is shared, so its companion has to be too."""
    args = build_parser().parse_args([*command, "--no-subscribe"])

    assert args.no_subscribe is True
    assert build_parser().parse_args(command).no_subscribe is False


# ----------------------------------------------------------------------------
def test_ensure_folder_subscribes_and_reports_it(
    imap_session, imap_config, fake_imap, capsys
):
    """The default path, from the flags a user actually types."""
    live = Sessions(sieve_without_mailbox(), imap_session)
    plan = prepare_folder(live, imap_config, add_args())

    engine.realize_folder(live, plan, render_event)

    assert plan.folder == NEW_FOLDER
    assert plan.use_create is False
    assert ("subscribe_folder", NEW_FOLDER) in fake_imap.calls
    assert "subscribed" in capsys.readouterr().out


# ----------------------------------------------------------------------------
def test_ensure_folder_honours_no_subscribe_and_says_what_it_cost(
    imap_session, imap_config, fake_imap, capsys
):
    live = Sessions(sieve_without_mailbox(), imap_session)
    plan = prepare_folder(live, imap_config, add_args("--no-subscribe"))

    engine.realize_folder(live, plan, render_event)

    assert "subscribe_folder" not in fake_imap.names()
    assert "will not appear in webmail" in capsys.readouterr().out


# ----------------------------------------------------------------------------
def test_ensure_folder_creates_nothing_on_a_dry_run(
    imap_session, imap_config, fake_imap
):
    """Showing before changing: the same rule the rest of the tool follows."""
    prepare_folder(
        Sessions(sieve_without_mailbox(), imap_session),
        imap_config,
        add_args("--dry-run"),
    )

    assert "create_folder" not in fake_imap.names()
    assert "subscribe_folder" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_planning_a_real_run_creates_nothing_before_the_decision(
    imap_session, imap_config, fake_imap, capsys
):
    """Show, then change: the folder is announced, not made, at plan time.

    It used to be created here -- before the diff was shown, and so before
    an abort, a rejected upload, or a failed merge could stop it -- which
    left a stray folder behind whenever the change went no further.
    """
    live = Sessions(sieve_without_mailbox(), imap_session)

    prepare_folder(live, imap_config, add_args())

    assert "create_folder" not in fake_imap.names()
    assert "will be created over IMAP" in capsys.readouterr().out


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("command", ["add", "apply", "from-message"])
def test_no_subscribe_without_create_folder_is_refused(command, capsys):
    """#43: a flag that does nothing must not be accepted silently."""
    from mailctl.cli import main

    extra = (
        ["--uid", "1"] if command == "from-message" else ["--from", "a@b.c"]
    )

    with pytest.raises(SystemExit) as exited:
        main([command, *extra, "--fileinto", "Lists", "--no-subscribe"])

    assert exited.value.code == 2
    assert "only applies with --create-folder" in capsys.readouterr().err
