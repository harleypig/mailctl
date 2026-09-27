"""The CLI's own presentation of failures, apart from any one command."""

from mailctl import MailctlError, cli
from mailctl.config import Config


# ----------------------------------------------------------------------------
def test_a_multi_line_error_is_indented_under_the_prefix(monkeypatch, capsys):
    """Continuation lines sit two spaces in, so the error reads as one block.

    The core separates sentences with plain newlines and leaves the layout
    to the front-end; this is the CLI's half of that split.
    """

    def fail(args):
        raise MailctlError("first line;\nsecond line.\nthird line")

    monkeypatch.setattr(cli, "cmd_test", fail)

    assert cli.main(["test"]) == 1

    assert capsys.readouterr().err == (
        "mailctl: first line;\n  second line.\n  third line\n"
    )


# ----------------------------------------------------------------------------
def test_a_single_line_error_is_printed_unchanged(monkeypatch, capsys):
    def fail(args):
        raise MailctlError("one line only")

    monkeypatch.setattr(cli, "cmd_test", fail)

    assert cli.main(["test"]) == 1

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
