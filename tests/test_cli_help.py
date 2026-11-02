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
def test_help_config_lists_config_test(capsys):
    """#440: red if ``config test`` is missing from its group's help."""
    code, out, _err = run(capsys, "help", "config")

    assert code == 0
    assert "{test,migrate}" in out
    assert "check the settings work, change nothing" in out


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
    assert "invalid choice (choose from" in helped[2]
    assert "nosuch" not in helped[2]
    assert "filter" in helped[2]


# ----------------------------------------------------------------------------
def test_an_unknown_action_is_refused_by_its_group(capsys):
    helped = run(capsys, "help", "filter", "nosuch")

    assert helped == run(capsys, "filter", "nosuch")
    assert helped[0] == 2
    assert "invalid choice (choose from" in helped[2]
    assert "nosuch" not in helped[2]
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


# ############################################################################
# Options renamed to the same words for every provider (#219)
# ############################################################################


# ----------------------------------------------------------------------------
def every_option() -> set[str]:
    """Every option string any command takes, hidden ones included."""
    return {
        option
        for parser in command_parsers().values()
        for action in parser._actions
        for option in action.option_strings
    }


# ----------------------------------------------------------------------------
def test_no_old_option_name_is_still_taken():
    """No alias: an old name the parser still took would be one, and each
    new name is one the parser does take."""
    options = every_option()

    assert set(cli.GONE_OPTIONS).isdisjoint(options)
    assert set(cli.GONE_OPTIONS.values()) <= options


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("old", sorted(cli.GONE_OPTIONS))
@pytest.mark.parametrize("spelling", ["apart", "joined"])
def test_an_old_option_is_refused_naming_the_new_one(capsys, old, spelling):
    given = [old, "X"] if spelling == "apart" else [f"{old}=X"]

    code, out, err = run(
        capsys, "filter", "add", "--from", "a@example.com", *given
    )

    assert (code, out) == (2, "")
    assert f"{old} is now {cli.GONE_OPTIONS[old]}" in err


# ----------------------------------------------------------------------------
def test_an_old_option_name_after_a_double_dash_is_not_an_option(capsys):
    """After '--' every word is an argument, so nothing is refused for it:
    argparse's own error is what the user sees."""
    code, _, err = run(capsys, "mail", "view", "--", "--script")

    assert code == 2
    assert "is now" not in err


# ############################################################################
# The connection options, on a page of their own (#458)
# ############################################################################


# ----------------------------------------------------------------------------
def connection_flags() -> set[str]:
    """Every option the connection page lists, read off the parser."""
    page = cli.build_parser().connection_page

    assert page is not None

    return {
        option for action in page._actions for option in action.option_strings
    }


# ----------------------------------------------------------------------------
def connecting_commands() -> dict:
    """The commands that read the settings, whose pages collapse the
    connection options; derived, so a command added later is covered."""
    return {
        path: parser
        for path, parser in command_parsers().items()
        if isinstance(parser, cli.Parser) and parser.collapsed is not None
    }


# ----------------------------------------------------------------------------
def test_the_connecting_commands_and_their_options_are_found():
    """Vacuous if either walk read nothing; too wide if it took a command
    that reads no settings."""
    commands = connecting_commands()

    assert {"folder list", "filter add", "config test"} <= set(commands)
    assert "config migrate" not in commands
    assert "help" not in commands
    assert {"--host", "--imap-host", "--sieve-tls", "--password-file"} <= (
        connection_flags()
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("path", list(connecting_commands()))
def test_a_command_page_names_the_connection_options_in_a_line(capsys, path):
    code, out, _err = run(capsys, *path.split(), "--help")

    assert code == 0
    assert cli.CONNECTION_POINTER in " ".join(out.split())

    for flag in ("--imap-host", "--sieve-tls", "--password-file"):
        assert flag not in out


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("path", list(connecting_commands()))
def test_a_command_still_takes_every_connection_option(path):
    """Left off the page, never off the command line."""
    taken = set(command_parsers()[path]._option_string_actions)

    assert connection_flags() <= taken


# ----------------------------------------------------------------------------
def test_a_connection_option_still_parses_after_the_command():
    args = cli.build_parser().parse_args(
        [
            "folder",
            "list",
            "--imap-host",
            "imap.example.com",
            "--sieve-tls",
            "ssl",
        ]
    )

    assert (args.imap_host, args.sieve_tls) == ("imap.example.com", "ssl")


# ----------------------------------------------------------------------------
def test_help_connection_lists_every_connection_option(capsys):
    code, out, _err = run(capsys, "help", "connection")

    assert code == 0
    assert out.startswith("usage: mailctl GROUP ACTION [connection options]")

    for flag in connection_flags():
        assert flag in out


# ----------------------------------------------------------------------------
def test_a_command_page_puts_the_connection_options_back():
    """The options are shared by every command, so a page that left them
    out and kept them out would empty the connection page as well."""
    parser = cli.build_parser()
    command_parsers(parser)["folder list"].format_help()

    assert parser.connection_page is not None
    assert "the IMAP server's hostname" in parser.connection_page.format_help()
