"""`mailctl help [GROUP [ACTION]]`: the same text and exit as `--help`
(#153), for every group and command (#219)."""

import pytest
from cli_support import command_parsers, group_paths

from mailctl import MailctlError, cli, engine


# ----------------------------------------------------------------------------
def run(capsys, *argv: str) -> tuple[int | str | None, str, str]:
    """Exit code, stdout, and stderr of ``mailctl ARGV``."""
    with pytest.raises(SystemExit) as stopped:
        cli.main(list(argv))

    out, err = capsys.readouterr()

    return stopped.value.code, out, err


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
    commands = command_parsers()

    assert "help" in commands
    assert {"filter add", "mail search", "server baseline save"} <= set(
        commands
    )
    assert {"filter", "server", "server baseline"} <= set(group_paths())


# ----------------------------------------------------------------------------
def test_help_alone_is_mailctl_help(capsys):
    assert run(capsys, "help") == run(capsys, "--help")


# ----------------------------------------------------------------------------
def test_help_is_listed_with_its_one_line_help(capsys):
    code, out, _err = run(capsys, "help")

    assert code == 0
    assert "show help for mailctl, a group, or a command" in out
    assert ",help}" in out.split("\n\n")[0]


# ----------------------------------------------------------------------------
def test_the_groups_are_listed_in_the_decided_order(capsys):
    """#219's order, which is not the alphabet's: red if a group moves."""
    _code, out, _err = run(capsys, "--help")

    assert (
        "{mail,folder,filter,filterset,server,config,help}"
        in out.split("\n\n")[0]
    )
    assert "Read, sort, and filter the mail on your account." in out


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("path", group_paths())
def test_help_group_is_group_help(capsys, path):
    helped = run(capsys, "help", *path.split())

    assert helped == run(capsys, *path.split(), "--help")
    assert helped[0] == 0
    assert f"usage: mailctl {path}" in helped[1]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("path", list(command_parsers()))
def test_help_command_is_command_help(capsys, path):
    helped = run(capsys, "help", *path.split())

    assert helped == run(capsys, *path.split(), "--help")
    assert helped[0] == 0
    assert f"usage: mailctl {path}" in helped[1]


# ----------------------------------------------------------------------------
def test_an_unknown_command_is_refused_as_an_unknown_command(capsys):
    helped = run(capsys, "help", "nosuch")

    assert helped == run(capsys, "nosuch")
    assert helped[0] == 2
    assert "invalid choice: 'nosuch'" in helped[2]
    assert "filter" in helped[2]


# ----------------------------------------------------------------------------
def test_an_unknown_action_is_refused_by_its_group(capsys):
    helped = run(capsys, "help", "filter", "nosuch")

    assert helped == run(capsys, "filter", "nosuch")
    assert helped[0] == 2
    assert "invalid choice: 'nosuch'" in helped[2]
    assert "optimize" in helped[2]


# ----------------------------------------------------------------------------
def test_the_global_flags_work_on_either_side_of_help(capsys):
    expected = run(capsys, "filter", "add", "--help")

    assert run(capsys, "--verbose", "help", "filter", "add") == expected
    assert run(capsys, "help", "--debug", "filter", "add") == expected


# ----------------------------------------------------------------------------
def test_a_global_flag_is_taken_between_group_and_action(capsys):
    """'mailctl filter --verbose add' is as good as either end."""
    expected = run(capsys, "filter", "add", "--help")

    assert run(capsys, "filter", "--verbose", "add", "--help") == expected


# ############################################################################
# The old names (#219): refused, naming the command each is now
# ############################################################################


# ----------------------------------------------------------------------------
def test_every_old_name_is_gone_from_the_top_level():
    """No alias: an old name the parser still took would be one."""
    top = {path.split()[0] for path in command_parsers()}

    assert set(cli.GONE_COMMANDS).isdisjoint(top)


# ----------------------------------------------------------------------------
def test_every_old_name_points_at_a_command_that_exists():
    """What an old name points at is a command, with any flag it needs."""
    named = {new.split(" --")[0] for new in cli.GONE_COMMANDS.values()}

    assert named <= set(command_parsers())


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("old", sorted(cli.GONE_COMMANDS))
def test_an_old_name_is_refused_naming_the_new_command(capsys, old):
    code, out, err = run(capsys, old, "--dry-run")

    assert (code, out) == (2, "")
    assert f"'{old}' is now 'mailctl {cli.GONE_COMMANDS[old]}'" in err


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "argv",
    [["--verbose", "rules"], ["help", "rules"], ["help", "--debug", "rules"]],
    ids=["after-a-flag", "help", "help-after-a-flag"],
)
def test_an_old_name_is_refused_wherever_it_is_the_command(capsys, argv):
    code, _out, err = run(capsys, *argv)

    assert code == 2
    assert "'rules' is now 'mailctl filter list'" in err


# ----------------------------------------------------------------------------
def test_an_old_name_as_an_argument_is_only_an_argument(capsys, monkeypatch):
    """Only the command word is checked: a folder called 'list' is fine."""

    def stop(_args):
        raise MailctlError("stopped before connecting")

    monkeypatch.setattr(cli, "configure", stop)

    assert cli.main(["folder", "create", "list"]) == 1
    assert "stopped before connecting" in capsys.readouterr().err
