"""The CLI's own presentation of failures, apart from any one command."""

import pytest

from mailctl import MailctlError, cli
from mailctl.components.managesieve import client as sieve_client
from mailctl.config import Config
from mailctl.criteria import Criteria, load_filter


# ----------------------------------------------------------------------------
def test_a_multi_line_error_is_indented_under_the_prefix(monkeypatch, capsys):
    """Continuation lines sit two spaces in, so the error reads as one block.

    The core separates sentences with plain newlines and leaves the layout
    to the front-end; this is the CLI's half of that split.
    """

    def fail(args):
        raise MailctlError("first line;\nsecond line.\nthird line")

    monkeypatch.setattr(cli, "cmd_test", fail)

    assert cli.main(["server", "test"]) == 1

    assert capsys.readouterr().err == (
        "mailctl: first line;\n  second line.\n  third line\n"
    )


# ----------------------------------------------------------------------------
def test_a_single_line_error_is_printed_unchanged(monkeypatch, capsys):
    def fail(args):
        raise MailctlError("one line only")

    monkeypatch.setattr(cli, "cmd_test", fail)

    assert cli.main(["server", "test"]) == 1

    assert capsys.readouterr().err == "mailctl: one line only\n"


# ----------------------------------------------------------------------------
def test_an_answered_prompt_is_reported_as_set():
    """#61: the password is read before 'test' reports it, so the prompt
    path shows the outcome ("set") rather than the pre-prompt "unset"."""
    config = Config(user="u@example.com", environ={})
    config.prompter = lambda _prompt: "answered"

    assert cli.resolve_password(config) == ("set", None)


# ----------------------------------------------------------------------------
def test_an_unusable_password_is_reported_and_handed_back():
    """Nothing configured and nothing to prompt with: the failure comes back
    to be raised after the settings are shown, never swallowed."""
    config = Config(user="u@example.com", environ={})

    state, failure = cli.resolve_password(config)

    assert state == "not usable"
    assert isinstance(failure, MailctlError)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(
            ["mail", "search", "--from", "a@b.c", "--raw", "ALL"],
            id="search-criteria-and-raw",
        ),
        pytest.param(
            ["filter", "apply", "--from", "a@b.c"], id="apply-no-action"
        ),
        pytest.param(["filter", "add", "--from", "a@b.c"], id="add-no-action"),
    ],
)
def test_bad_input_fails_before_any_login(argv, fake_imap, monkeypatch):
    """#137: the CLI connects each half on first use, so input refused
    before a server is needed never logs in or asks for the password."""
    import getpass

    def no_prompt(_prompt):
        pytest.fail("the password prompt was reached")

    monkeypatch.setattr(getpass, "getpass", no_prompt)

    sieve_logins = []
    monkeypatch.setattr(
        sieve_client.SieveSession,
        "open",
        lambda *args, **kwargs: sieve_logins.append(args),
    )
    monkeypatch.setenv("MAILCTL_HOST", "mail.example.com")
    monkeypatch.setenv("MAILCTL_USER", "user@example.com")

    assert cli.main(argv) == 1

    assert fake_imap.calls == []
    assert sieve_logins == []


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("argv", "error"),
    [
        pytest.param(
            ["messages"],
            "'messages' is now 'mailctl mail search'",
            id="messages",
        ),
        pytest.param(
            ["mail", "search", "--search", "ALL"],
            "unrecognized arguments: --search",
            id="search-flag",
        ),
    ],
)
def test_the_old_names_are_gone(argv, error, capsys):
    """#147: 'messages' became 'search' and its '--search' became '--raw',
    with no alias for either."""
    with pytest.raises(SystemExit) as stopped:
        cli.main(argv)

    assert stopped.value.code == 2
    assert error in capsys.readouterr().err


# ----------------------------------------------------------------------------
def test_build_filter_json_is_what_the_filter_loader_reads(capsys):
    """The document 'search --build-filter --json' prints is the one 'add
    --filter' will read (#149), and building it needs no server."""
    code = cli.main(
        [
            "mail",
            "search",
            "--build-filter",
            "--json",
            "--from",
            "a@x.org",
            "--subject",
            "Hi",
            "--match",
            "all",
            "--compare",
            "is",
        ]
    )

    out = capsys.readouterr().out
    expected = Criteria(match="all", compare="is")
    expected.add("From", "a@x.org")
    expected.add("Subject", "Hi")

    assert code == 0
    assert load_filter(out) == expected
