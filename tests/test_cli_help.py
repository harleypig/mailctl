"""`mailctl help [COMMAND]`: the same text and exit as `--help` (#153)."""

import argparse

import pytest

from mailctl import cli, engine


# ----------------------------------------------------------------------------
def run(capsys, *argv: str) -> tuple[int | str | None, str, str]:
    """Exit code, stdout, and stderr of ``mailctl ARGV``."""
    with pytest.raises(SystemExit) as stopped:
        cli.main(list(argv))

    out, err = capsys.readouterr()

    return stopped.value.code, out, err


# ----------------------------------------------------------------------------
def every_command() -> list[str]:
    """Every subcommand the parser knows, hidden ones included."""
    subparsers = next(
        action
        for action in cli.build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    )

    return list(subparsers.choices)


# ----------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def no_connection(monkeypatch):
    """Help never opens a session or asks for the password."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("help opened a connection")

    monkeypatch.setattr(engine, "connect", refuse)
    monkeypatch.setattr("getpass.getpass", refuse)


# ----------------------------------------------------------------------------
def test_the_command_list_is_derived_and_includes_help():
    """A hand-written list would stop covering a command added later."""
    commands = every_command()

    assert "help" in commands
    assert {"add", "search", "migrate-config"} <= set(commands)


# ----------------------------------------------------------------------------
def test_help_alone_is_mailctl_help(capsys):
    assert run(capsys, "help") == run(capsys, "--help")


# ----------------------------------------------------------------------------
def test_help_is_listed_with_its_one_line_help(capsys):
    code, out, _err = run(capsys, "help")

    assert code == 0
    assert "show help for mailctl or a command" in out
    assert ",help}" in out.split("\n\n")[0]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("command", every_command())
def test_help_command_is_command_help(capsys, command):
    helped = run(capsys, "help", command)

    assert helped == run(capsys, command, "--help")
    assert helped[0] == 0
    assert f"usage: mailctl {command}" in helped[1]


# ----------------------------------------------------------------------------
def test_an_unknown_command_is_refused_as_an_unknown_command(capsys):
    helped = run(capsys, "help", "nosuch")

    assert helped == run(capsys, "nosuch")
    assert helped[0] == 2
    assert "invalid choice: 'nosuch'" in helped[2]
    assert "add" in helped[2]


# ----------------------------------------------------------------------------
def test_the_global_flags_work_on_either_side_of_help(capsys):
    expected = run(capsys, "add", "--help")

    assert run(capsys, "--verbose", "help", "add") == expected
    assert run(capsys, "help", "--debug", "add") == expected
