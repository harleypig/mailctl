"""--json's documents, apart from any one command's snapshot (#151).

The snapshots (``tests/snapshots/cli/*-json.txt``) pin each command's
shape end to end; this holds what no snapshot can show: that a credential
is refused rather than serialised, what an unmapped value does, how a date
is written, how main() reports a failure it did not expect, and which
commands offer --json at all.
"""

import argparse
import json
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import cast

import pytest
from cli_support import command_parsers

from mailctl import MailctlError, cli, engine, json_output
from mailctl.config import (
    ENV_FILE,
    ENVIRONMENT,
    Config,
    LegacySetting,
    Secret,
    Source,
)
from mailctl.providers.base import FolderCreation, MailActionResult
from mailctl.providers.model import FILEINTO, OTHER, STOP, ActionStep
from mailctl.providers.mxroute.sieve import _ACTION_KINDS
from mailctl.rules import Rule
from mailctl.utilities import events
from mailctl.utilities.folder_rename import (
    FolderRenamePlan,
    FolderRenameResult,
    PostCondition,
)
from mailctl.utilities.migration import ConfigDirPending
from mailctl.utilities.rules import RulesReport

SENTINEL = "s3ntinel-VALUE-never-shown-151"


# ############################################################################
# Serialising
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_secret_is_refused_and_its_value_is_not_in_the_error():
    with pytest.raises(TypeError) as caught:
        json_output.dumps({"password": Secret(SENTINEL)})

    assert "credential" in str(caught.value)
    assert SENTINEL not in str(caught.value)


# ----------------------------------------------------------------------------
def test_a_secret_nested_deep_is_refused_too():
    with pytest.raises(TypeError, match="credential"):
        json_output.dumps({"a": [{"b": (Secret(SENTINEL),)}]})


# ----------------------------------------------------------------------------
def test_a_value_no_mapping_converted_is_refused_by_type():
    """A record handed over unmapped is a bug, not a string to guess at."""
    with pytest.raises(TypeError, match="no JSON mapping for object"):
        json_output.dumps({"x": object()})


# ----------------------------------------------------------------------------
def test_text_from_mail_is_escaped_to_printable_ascii():
    text = json_output.dumps({"s": "a\x1b]0;x\x07b ‮ é"})

    assert text.isascii()
    assert "\x1b" not in text
    assert "\x07" not in text
    assert json.loads(text) == {"s": "a\x1b]0;x\x07b ‮ é"}


# ----------------------------------------------------------------------------
def test_an_error_line_is_one_line():
    text = json_output.dumps(json_output.error("a\nb"), indent=None)

    assert text.count("\n") == 1
    assert json.loads(text) == {
        "version": json_output.JSON_VERSION,
        "error": {"message": "a\nb"},
    }


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-02-03 04:05:06", "2026-02-03T04:05:06"),
        ("", None),
        ("3 Feb 2026", "3 Feb 2026"),
    ],
)
def test_a_received_date_is_iso_or_passed_through(value, expected):
    assert json_output._received(value) == expected


# ############################################################################
# main()
# ############################################################################


# ----------------------------------------------------------------------------
def test_an_unexpected_failure_under_json_is_a_json_line(monkeypatch, capsys):
    def fail(args):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "cmd_folders", fail)

    assert cli.main(["folder", "list", "--json"]) == 1

    captured = capsys.readouterr()
    failure = json.loads(captured.err.splitlines()[-1])

    assert captured.out == ""
    assert failure["error"]["message"].startswith("unexpected RuntimeError")


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("machine", [[], ["--json"]])
def test_an_unexpected_failure_names_its_type_and_not_its_text(
    monkeypatch, capsys, machine
):
    """An exception nothing expected may carry anything: imaplib's
    encoding error quoted a character of the password it could not send."""

    def fail(args):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(cli, "cmd_folders", fail)

    assert cli.main(["folder", "list", *machine]) == 1

    captured = capsys.readouterr()

    assert "unexpected RuntimeError" in captured.err
    assert SENTINEL not in captured.out + captured.err


# ----------------------------------------------------------------------------
def test_an_interrupt_under_json_is_a_json_line(monkeypatch, capsys):
    def interrupted(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "cmd_list", interrupted)

    assert cli.main(["filterset", "list", "--json"]) == 130

    failure = json.loads(capsys.readouterr().err.splitlines()[-1])

    assert failure == {
        "version": json_output.JSON_VERSION,
        "error": {"message": "interrupted"},
    }


# ----------------------------------------------------------------------------
def test_a_probe_failure_under_json_is_a_json_line(monkeypatch, capsys):
    """probe keeps its own document (#101); its failure is main()'s."""

    def fail(sessions, config):
        raise MailctlError("probe failed")

    monkeypatch.setattr(cli.utilities.reports, "probe_servers", fail)

    assert cli.main(["server", "probe", "--json"]) == 1

    captured = capsys.readouterr()

    assert captured.out == ""
    assert json.loads(captured.err.splitlines()[-1]) == {
        "version": json_output.JSON_VERSION,
        "error": {"message": "probe failed"},
    }


# ----------------------------------------------------------------------------
def test_a_coded_failure_under_json_carries_its_code(monkeypatch, capsys):
    """#51: a script can tell one refusal from another by its code, and
    the message is the CLI's, flags included."""

    def fail(sessions, config):
        raise MailctlError("nothing to mark", code="no_marks")

    monkeypatch.setattr(cli.utilities.reports, "probe_servers", fail)

    assert cli.main(["server", "probe", "--json"]) == 1

    failure = json.loads(capsys.readouterr().err.splitlines()[-1])

    assert failure["error"] == {
        "message": "nothing to mark -- use --read, --unread, --flag, "
        "--unflag, --keyword, or --no-keyword",
        "code": "no_marks",
    }


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["mail", "search", "--json", "--bogus"], "invalid_arguments"),
        (["mail", "search", "--bogus", "--json"], "invalid_arguments"),
        (["mail", "view", "1", "--raw", "--json"], "invalid_arguments"),
        (["--json", "rules"], "gone_command"),
        (["rules", "--json"], "gone_command"),
        (["mail", "search", "--json", "--fileinto", "x"], "gone_option"),
        (["mail", "search", "--fileinto", "x", "--json"], "gone_option"),
        (
            ["folder", "list", "--imap-port", "many", "--json"],
            "setting_invalid",
        ),
        (
            [
                "filter",
                "add",
                "--from",
                "a",
                "--move-to",
                "b",
                "--no-subscribe",
                "--dry-run",
                "--json",
            ],
            "subscribe_needs_create",
        ),
    ],
)
def test_a_rejected_command_line_under_json_ends_in_a_coded_line(
    argv, code, capsys, monkeypatch
):
    """#352: a command line refused before any command runs carries a
    code under --json, wherever --json stands. The exit status, the usage
    block, and the error line are as they are without --json: the line is
    only added after them."""
    with pytest.raises(SystemExit) as rejected:
        cli.main(argv)

    lines = capsys.readouterr().err.splitlines()

    monkeypatch.setattr(cli, "json_requested", lambda _argv: False)

    with pytest.raises(SystemExit) as unasked:
        cli.main(argv)

    failure = json.loads(lines[-1])

    assert rejected.value.code == unasked.value.code == 2
    assert lines[:-1] == capsys.readouterr().err.splitlines()
    assert failure["version"] == json_output.JSON_VERSION
    assert failure["error"]["code"] == code
    assert f": error: {failure['error']['message']}" in lines[-2]


# ----------------------------------------------------------------------------
def test_an_option_is_never_taken_by_a_prefix_of_its_name(capsys):
    """--jso is not --json: a prefix that works today breaks the day an
    option sharing it is added, and the command line is a contract."""
    with pytest.raises(SystemExit) as rejected:
        cli.main(["mail", "search", "--jso", "--json"])

    failure = json.loads(capsys.readouterr().err.splitlines()[-1])

    assert rejected.value.code == 2
    assert failure["error"] == {
        "message": "unrecognized arguments: --jso",
        "code": "invalid_arguments",
    }


# ----------------------------------------------------------------------------
def test_a_bad_setting_flag_is_a_setting_refusal_naming_it(capsys):
    """The same code a bad value from the environment or the config file
    gets; still exit 2, as a rejected command line."""
    with pytest.raises(SystemExit) as rejected:
        cli.main(["folder", "list", "--sieve-tls", "bad", "--json"])

    failure = json.loads(capsys.readouterr().err.splitlines()[-1])

    assert rejected.value.code == 2
    assert failure["error"] == {
        "message": "argument --sieve-tls: invalid choice (choose from "
        "starttls, ssl, none)",
        "code": "setting_invalid",
    }


SENTINEL = "Sentinel-Not-A-Real-Password-7f3a"


# Every shape a password can take into a refused command line: an option
# mistyped (spaced, with '=', or short and attached), a password option
# given where it is not taken, one that takes the password as the command
# word, and a value given to an option that takes none or another kind.
LEAKY_COMMAND_LINES = [
    ["filter", "list", "--pasword", SENTINEL],
    ["filter", "list", f"--pasword={SENTINEL}"],
    ["filter", "list", "--passwd", SENTINEL],
    ["filter", "list", "--pasword-cmd", SENTINEL],
    ["filter", "list", f"-P{SENTINEL}"],
    ["config", "migrate", "--password", SENTINEL],
    ["config", "migrate", f"-p{SENTINEL}"],
    ["--password", SENTINEL, "filter", "list"],
    ["help", f"--password={SENTINEL}"],
    ["filter", SENTINEL],
    ["filter", "list", "--pasword", f"--{SENTINEL}"],
    ["filter", "list", "--pasword", f"--{SENTINEL}=x"],
    ["--password", f"--{SENTINEL}", "filter", "list"],
    ["config", "migrate", "--password", f"--{SENTINEL}"],
    ["filter", "list", "--", f"--{SENTINEL}"],
    ["filter", "list", f"--debug={SENTINEL}"],
    ["mail", "view", SENTINEL],
    ["folder", "list", "--imap-port", SENTINEL],
    ["folder", "list", "--sieve-tls", SENTINEL],
]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("json_flag", [[], ["--json"]], ids=["text", "json"])
@pytest.mark.parametrize("argv", LEAKY_COMMAND_LINES)
def test_a_refused_command_line_never_quotes_a_word_argparse_could_not_use(
    argv, json_flag, capsys
):
    """#364: a word argparse refused may be a password that lost its
    option, so neither the error line nor the --json line quotes one; they
    say what kind of mistake it was and name options only. Exit 2 and the
    usage block are argparse's own. Red if Parser.error hands argparse's
    message on unfiltered."""
    # Nothing after '--' is an option, so --json goes before it.
    at = argv.index("--") if "--" in argv else len(argv)

    with pytest.raises(SystemExit) as rejected:
        cli.main([*argv[:at], *json_flag, *argv[at:]])

    out, err = capsys.readouterr()

    assert rejected.value.code == 2
    assert err.startswith("usage: mailctl")
    assert ": error: " in err
    assert SENTINEL not in out
    assert SENTINEL not in err

    if json_flag:
        assert json.loads(err.splitlines()[-1])["error"]["code"]


# Each settings option's value in every place it is taken: before the
# command, between the group and its action, and after it (#243, #265).
SETTINGS_VALUE_COMMAND_LINES = [
    (["--provider", SENTINEL, "filter", "list"], "unknown_provider"),
    (["filter", "--provider", SENTINEL, "list"], "unknown_provider"),
    (["filter", "list", "--provider", SENTINEL], "unknown_provider"),
    (["filter", "list", f"--provider={SENTINEL}"], "unknown_provider"),
    (["--env-file", SENTINEL, "filter", "list"], "env_file_unreadable"),
    (["filter", "--env-file", SENTINEL, "list"], "env_file_unreadable"),
    (["filter", "list", "--env-file", SENTINEL], "env_file_unreadable"),
    (["filter", "list", f"--env-file={SENTINEL}"], "env_file_unreadable"),
]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("json_flag", [[], ["--json"]], ids=["text", "json"])
@pytest.mark.parametrize(("argv", "code"), SETTINGS_VALUE_COMMAND_LINES)
def test_a_refused_provider_or_env_file_never_quotes_its_value(
    argv, code, json_flag, capsys, monkeypatch, tmp_path
):
    """#373: --provider and --env-file are refused below argparse, as an
    unknown provider and a file that cannot be read, so json_output.
    rejection never words them. A value given to either may be a password
    in the wrong place: the refusal names the option and what was wrong,
    and quotes nothing. Red while either refusal quotes its value."""
    monkeypatch.chdir(tmp_path)

    assert cli.main([*argv, *json_flag]) == 1

    out, err = capsys.readouterr()
    option = "--provider" if code == "unknown_provider" else "--env-file"

    assert SENTINEL not in out
    assert SENTINEL not in err
    assert option in err

    if json_flag:
        assert json.loads(err.splitlines()[-1])["error"]["code"] == code


# ----------------------------------------------------------------------------
def test_a_mutual_exclusion_names_options_only(capsys):
    """A guard, not a falsifier of the filter: argparse's message for two
    options that cannot be combined holds their names alone, so the value
    given to one is on neither line with the filter or without it."""
    with pytest.raises(SystemExit) as rejected:
        cli.main(
            [
                "filter",
                "list",
                f"--password-cmd={SENTINEL}",
                "--password-file",
                "x",
                "--json",
            ]
        )

    out, err = capsys.readouterr()

    assert rejected.value.code == 2
    assert "not allowed with argument" in err
    assert SENTINEL not in out + err


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("message", "said"),
    [
        (
            f"argument --debug: ignored explicit argument '{SENTINEL}'",
            "argument --debug: ignored explicit argument",
        ),
        (
            f"argument uid: invalid int value: '{SENTINEL}'",
            "argument uid: invalid int value",
        ),
        (
            f"argument --sort: invalid choice: '{SENTINEL}' (choose from "
            "'size', 'sent', 'received')",
            "argument --sort: invalid choice (choose from size, sent, "
            "received)",
        ),
        (
            f"argument --sort: invalid choice: '{SENTINEL}', maybe you "
            "meant 'size'? (choose from size, sent, received)",
            "argument --sort: invalid choice (choose from size, sent, "
            "received)",
        ),
        (
            f"argument --sort: invalid choice: 'x (choose from {SENTINEL})' "
            "(choose from size, sent)",
            "argument --sort: invalid choice (choose from size, sent)",
        ),
        (
            f"argument --sort: invalid choice: '{SENTINEL}' (choose from "
            "a b, c)",
            "argument --sort: invalid choice",
        ),
        (
            f"ambiguous option: --pass={SENTINEL} could match --password, "
            "--password-cmd",
            "ambiguous option: --pass could match --password, --password-cmd",
        ),
        (
            f"unrecognized arguments: -p{SENTINEL} --{SENTINEL}=x",
            "unrecognized arguments: 2 words",
        ),
        (
            f"unrecognized arguments: --pasword --{SENTINEL}",
            "unrecognized arguments: --pasword and 1 other word",
        ),
        (
            f"unrecognized arguments: --pasword --{SENTINEL}=x",
            "unrecognized arguments: --pasword and 1 other word",
        ),
        (
            "unrecognized arguments: --pasword -S",
            "unrecognized arguments: --pasword and 1 other word",
        ),
        (
            f"unrecognized arguments: -- --{SENTINEL}",
            "unrecognized arguments: 2 words",
        ),
        (
            "unrecognized arguments: --pasword=x --typo",
            "unrecognized arguments: --pasword, --typo",
        ),
    ],
)
def test_a_value_argparse_quotes_is_dropped_and_the_names_kept(message, said):
    """Each template that quotes a value keeps what names the mistake. An
    option is named up to its '=', a short one only alone, and never a
    word straight after an option or ``--`` with no '=': that word may be
    the option's value, and a password may start with '-'."""
    assert json_output.rejection(message)[0] == said


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "message",
    [
        "argument --password-file: not allowed with argument -p/--password",
        "argument --provider: expected one argument",
        "argument --env-file: expected at most one argument",
        "argument UID: expected at least one argument",
        "the following arguments are required: name, --move-to",
        "the following arguments are required: {add,list}",
        "one of the arguments --read --unread is required",
    ],
)
def test_a_message_naming_only_arguments_is_kept_as_argparse_wrote_it(
    message,
):
    assert json_output.rejection(message) == (
        message,
        "invalid_arguments",
        None,
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "message",
    [
        f"argument --password: {SENTINEL} is not a valid password",
        f"invalid value {SENTINEL!r} for argument --password",
        f"the following arguments are required: name (got '{SENTINEL}')",
        f"argument --provider: expected one argument, not '{SENTINEL}'",
        f"ambiguous option: -p{SENTINEL} could match -p, -P",
        f"argument --sort: invalid choice '{SENTINEL}'",
        "",
    ],
)
def test_a_message_of_no_known_shape_is_refused_in_general_words(message):
    """#364: a template a later Python rewords, or one never seen, may
    quote a value, so it is never shown. Red if the fallback hands
    argparse's message on."""
    assert json_output.rejection(message) == (
        json_output.REFUSED,
        "invalid_arguments",
        None,
    )


# ----------------------------------------------------------------------------
def test_a_message_of_no_known_shape_is_the_general_words_on_both_lines(
    capsys, monkeypatch
):
    """The fail-closed path through the CLI end to end: an argparse that
    worded its refusal in a way never seen prints neither its text nor the
    value, on the error line or the --json line, and still exits 2."""
    reworded = f"value {SENTINEL!r} is not one argparse knows"

    def error(self, message):
        return original(self, reworded)

    original = cli.Parser.error
    monkeypatch.setattr(cli.Parser, "error", error)

    with pytest.raises(SystemExit) as rejected:
        cli.main(["mail", "search", "--bogus", "--json"])

    err = capsys.readouterr().err
    lines = err.splitlines()

    assert rejected.value.code == 2
    assert SENTINEL not in err
    assert lines[-2] == f"mailctl: error: {json_output.REFUSED}"
    assert json.loads(lines[-1])["error"] == {
        "message": json_output.REFUSED,
        "code": "invalid_arguments",
    }


# ----------------------------------------------------------------------------
def test_an_unrecognised_word_is_counted_and_an_option_named():
    message, code, fields = json_output.rejection(
        "unrecognized arguments: --pasword=x secret other --flag"
    )

    assert message == (
        "unrecognized arguments: --pasword, --flag and 2 other words"
    )
    assert (code, fields) == ("invalid_arguments", None)
    assert json_output.rejection("unrecognized arguments: a")[0] == (
        "unrecognized arguments: 1 word"
    )


# ----------------------------------------------------------------------------
def test_a_rejected_command_line_without_json_prints_no_json(capsys):
    with pytest.raises(SystemExit):
        cli.main(["mail", "search", "--bogus"])

    assert not capsys.readouterr().err.splitlines()[-1].startswith("{")


# ----------------------------------------------------------------------------
def test_a_refusal_the_cli_makes_carries_the_core_code_in_its_own_words(
    capsys,
):
    """The CLI refuses no criteria before the core can, and in words that
    name its own flags; the code is the core's, so a script meets one
    code for the condition, and ERROR_TEXT adds nothing to the words."""
    assert cli.main(["filter", "add", "--move-to", "b", "--dry-run"]) == 1
    assert capsys.readouterr().err == f"mailctl: {cli.NO_CRITERIA}\n"

    assert (
        cli.main(["filter", "add", "--move-to", "b", "--dry-run", "--json"])
        == 1
    )

    failure = json.loads(capsys.readouterr().err.splitlines()[-1])

    assert failure["error"] == {
        "message": cli.NO_CRITERIA,
        "code": "no_criteria",
    }


# ----------------------------------------------------------------------------
def test_json_is_read_from_the_words_before_a_double_dash():
    assert cli.json_requested(["mail", "search", "--json"])
    assert not cli.json_requested(["mail", "search", "--", "--json"])
    assert not cli.json_requested(["mail", "search", "--jsonish"])


# ----------------------------------------------------------------------------
def test_what_a_command_prints_on_the_way_goes_to_stderr(monkeypatch, capsys):
    """The redirect, not each command's care, keeps stdout clean."""

    def chatty(args):
        print("a note for a person")

        return cli.emit_json(args, json_output.scripts("a", []))

    monkeypatch.setattr(cli, "cmd_list", chatty)

    assert cli.main(["filterset", "list", "--json"]) == 0

    captured = capsys.readouterr()

    assert json.loads(captured.out)["filtersets"] == [
        {"name": "a", "active": True}
    ]
    assert "a note for a person" in captured.err


# ############################################################################
# Which commands offer it
# ############################################################################


# ----------------------------------------------------------------------------
def dests(parser: argparse.ArgumentParser) -> set[str]:
    return {item.dest for item in parser._actions}


# Writes whose --dry-run is not a plan against the server: a backup and a
# saved baseline are local files, and migrating the config moves them.
LOCAL_WRITES = {"filterset backup", "config migrate", "server baseline save"}


# ----------------------------------------------------------------------------
def test_every_server_write_with_a_dry_run_offers_json():
    """Derived, so a write command added later is held to it on arrival."""
    commands = command_parsers()
    writes = {
        name
        for name, parser in commands.items()
        if "dry_run" in dests(parser) and name not in LOCAL_WRITES
    }

    assert {
        "filter add",
        "filter apply",
        "filterset restore",
        "folder subscribe",
    } <= writes

    for name in writes:
        assert "json" in dests(commands[name]), name


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name",
    [
        "mail search",
        "mail view",
        "folder list",
        "filter list",
        "filterset list",
        "server probe",
        "mail senders",
    ],
)
def test_the_data_commands_offer_json(name):
    assert "json" in dests(command_parsers()[name])


# ----------------------------------------------------------------------------
def test_a_report_for_a_person_offers_none():
    """'server test' is read by a person; its contract is its exit
    code."""
    assert "json" not in dests(command_parsers()["server test"])


# ----------------------------------------------------------------------------
def test_search_offers_uids_only():
    assert "uids_only" in dests(command_parsers()["mail search"])


# ############################################################################
# The documents the API adds
# ############################################################################


# ----------------------------------------------------------------------------
def test_an_error_carries_its_fields_as_plain_json_and_only_when_given():
    """The CLI never passes fields, so its failure line is as it was."""
    assert json_output.error("gone") == {
        "version": json_output.JSON_VERSION,
        "error": {"message": "gone"},
    }

    document = json_output.error(
        "changed",
        "filterset_changed",
        {
            "arguments": ("managesieve",),
            "where": Path("/x/y"),
            "known": frozenset({"b", "a"}),
            "count": 3,
        },
    )

    assert document["error"]["fields"] == {
        "arguments": ["managesieve"],
        "where": "/x/y",
        "known": ["a", "b"],
        "count": 3,
    }
    assert json.loads(json_output.dumps(document)) == document


# ----------------------------------------------------------------------------
def test_a_secret_in_an_error_field_is_still_refused():
    document = json_output.error("x", "y", {"value": Secret(SENTINEL)})

    with pytest.raises(TypeError, match="credential"):
        json_output.dumps(document)


# ----------------------------------------------------------------------------
def test_the_capabilities_name_every_field_the_provider_declares():
    """Red if a capability is added to the record and not to the
    document: a client would never be offered it."""
    offer = engine.capabilities_for(Config())
    document = json_output.capabilities(offer)

    assert set(document) == {item.name for item in fields(offer)}
    assert json.loads(json_output.dumps(document)) == document


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (
            events.ScriptBackedUp("managesieve", Path("/b/managesieve.sieve")),
            {
                "kind": "filterset_backed_up",
                "filterset": "managesieve",
                "path": "/b/managesieve.sieve",
            },
        ),
        (
            events.ScriptUploaded("managesieve", True),
            {
                "kind": "filterset_uploaded",
                "filterset": "managesieve",
                "activated": True,
            },
        ),
        (
            events.FolderCreated(FolderCreation("INBOX.Lists", False, "NO")),
            {
                "kind": "folder_created",
                "folder": "INBOX.Lists",
                "subscribed": False,
                "subscribe_error": "NO",
                "by_subscription": True,
            },
        ),
        (
            events.FolderCreated(FolderCreation("INBOX.Lists", True)),
            {
                "kind": "folder_created",
                "folder": "INBOX.Lists",
                "subscribed": True,
                "subscribe_error": None,
                "by_subscription": True,
            },
        ),
        (
            events.FolderRenamed("INBOX.A", "INBOX.B", 2),
            {
                "kind": "folder_renamed",
                "old": "INBOX.A",
                "new": "INBOX.B",
                "children": 2,
            },
        ),
        (
            events.SubscriptionChanged("INBOX.B", False),
            {
                "kind": "subscription_changed",
                "folder": "INBOX.B",
                "subscribed": False,
            },
        ),
    ],
)
def test_every_event_has_a_document(record, expected):
    assert json_output.event(record) == {
        "version": json_output.JSON_VERSION,
        "event": expected,
    }


# ----------------------------------------------------------------------------
def test_every_event_type_is_mapped():
    """Derived from the module, so an event added there without a mapping
    is caught here rather than by a client's first change."""
    kinds = [
        value
        for name in events.__all__
        if isinstance(value := getattr(events, name), type)
        and is_dataclass(value)
        and value is not events.ServerAlert
    ]

    assert len(kinds) == 5

    for kind in kinds:
        record = kind(*(None for _ in fields(kind)))

        try:
            json_output.event(record)

        except AttributeError:
            # Mapped: the mapping read a field the placeholder lacks.
            continue

        except TypeError as error:
            pytest.fail(f"{kind.__name__} has no event mapping: {error}")


# ----------------------------------------------------------------------------
def test_an_alert_is_tagged_with_its_channel_in_the_user_facing_noun():
    alert = events.ServerAlert("Mailbox is 95% full", "alert")

    assert json_output.alert(events.RULES, alert)["alert"] == {
        "channel": "filter",
        "kind": "alert",
        "text": "Mailbox is 95% full",
    }
    assert json_output.alert(events.MAIL, alert)["alert"]["channel"] == "mail"


# ----------------------------------------------------------------------------
def test_an_execute_result_names_what_it_did_and_where_the_backup_is():
    record = argparse.Namespace(
        name="github", script="managesieve", activate=True
    )

    assert json_output.script_change_result(
        "filter add", record.name, record, Path("/b/managesieve.sieve")
    ) == {
        "version": json_output.JSON_VERSION,
        "result": {
            "command": "filter add",
            "filter": "github",
            "filterset": "managesieve",
            "activated": True,
            "backup": "/b/managesieve.sieve",
        },
    }


# ----------------------------------------------------------------------------
def test_a_change_to_the_whole_filter_set_names_no_filter():
    record = argparse.Namespace(script="managesieve", activate=False)
    result = json_output.script_change_result(
        "filter optimize", None, record, Path("/b/managesieve.sieve")
    )["result"]

    assert result["filter"] is None
    assert result["activated"] is False


# ----------------------------------------------------------------------------
def test_an_existing_mail_result_counts_each_thing_done_and_where():
    """Red if a count is dropped or read from the wrong field of the
    result: a client reports these to the user as what happened."""
    done = MailActionResult(flagged=1, moved=2, deleted=3, copied=4)
    plan = argparse.Namespace(source="INBOX", destination="INBOX.Lists")

    assert json_output.mail_result(done, plan) == {
        "version": json_output.JSON_VERSION,
        "result": {
            "command": "filter apply",
            "source": "INBOX",
            "destination": "INBOX.Lists",
            "flagged": 1,
            "moved": 2,
            "deleted": 3,
            "copied": 4,
        },
    }


# ----------------------------------------------------------------------------
def test_a_filter_set_is_shown_as_its_text_and_null_where_it_will_not_parse():
    """Red if the text is altered, or a set whose filters cannot be read
    loses its text or reports an empty list for them."""
    shown = json_output.filterset_source("managesieve", "keep;\r\n", ["a"])
    broken = json_output.filterset_source("broken", "if {", None)

    assert shown == {
        "version": json_output.JSON_VERSION,
        "filterset": {
            "name": "managesieve",
            "source": "keep;\r\n",
            "filters": ["a"],
        },
    }
    assert broken["filterset"]["filters"] is None


# ----------------------------------------------------------------------------
def test_a_backup_result_says_where_and_whether_it_was_written():
    record = argparse.Namespace(script="managesieve")

    assert json_output.backup_result(
        record, Path("/b/managesieve.sieve"), None, False
    ) == {
        "version": json_output.JSON_VERSION,
        "result": {
            "command": "filterset backup",
            "filterset": "managesieve",
            "path": "/b/managesieve.sieve",
            "filters": None,
            "written": False,
        },
    }


# ----------------------------------------------------------------------------
def test_a_folder_creation_result_keeps_a_failed_subscription_apart():
    """Red if a subscription that failed reads as one declined: the
    folder is invisible either way, and only one needs fixing."""
    failed = FolderCreation("INBOX.New", False, "NO [LIMIT] too many")
    declined = FolderCreation("INBOX.New", False)

    assert json_output.folder_creation_result(failed)["result"] == {
        "command": "folder create",
        "folder": "INBOX.New",
        "subscribed": False,
        "subscribe_error": "NO [LIMIT] too many",
        "by_subscription": True,
    }

    result = json_output.folder_creation_result(declined)["result"]

    assert result["subscribe_error"] is None


# ----------------------------------------------------------------------------
def test_a_subscription_result_names_the_folder_as_the_server_does():
    record = argparse.Namespace(folder="INBOX.spam", subscribe=False)

    assert json_output.subscription_result("folder unsubscribe", record) == {
        "version": json_output.JSON_VERSION,
        "result": {
            "command": "folder unsubscribe",
            "folder": "INBOX.spam",
            "subscribed": False,
        },
    }


# ----------------------------------------------------------------------------
def rename_plan(rules_change: bool) -> FolderRenamePlan:
    """The four fields a rename's result reads off its plan."""
    record = argparse.Namespace(
        old="INBOX.Lists",
        new="INBOX.Feeds",
        script="managesieve",
        rules_change=rules_change,
    )

    return cast(FolderRenamePlan, record)


# ----------------------------------------------------------------------------
def test_a_folder_rename_result_carries_each_check_and_its_remedy():
    """Red if a failed check loses the operation that would fix it, or
    ``ok`` stops following the checks: a client tells the user from these
    whether the rename landed."""
    done = FolderRenameResult(
        plan=rename_plan(True),
        backup=Path("/b/managesieve.sieve"),
        subscribed=("INBOX.Feeds",),
        unsubscribed=(),
        subscription_errors=("NO unsubscribe failed",),
        checks=(
            PostCondition("'INBOX.Lists' is gone", True),
            PostCondition(
                "'INBOX.Feeds' is subscribed",
                False,
                "webmail will not show it",
                operation="folder subscribe",
                arguments=("INBOX.Feeds",),
            ),
        ),
    )

    assert json_output.folder_rename_result(done)["result"] == {
        "command": "folder rename",
        "old": "INBOX.Lists",
        "new": "INBOX.Feeds",
        "filterset": "managesieve",
        "backup": "/b/managesieve.sieve",
        "subscribed": ["INBOX.Feeds"],
        "unsubscribed": [],
        "subscription_errors": ["NO unsubscribe failed"],
        "ok": False,
        "checks": [
            {
                "label": "'INBOX.Lists' is gone",
                "ok": True,
                "detail": None,
                "operation": None,
                "arguments": [],
            },
            {
                "label": "'INBOX.Feeds' is subscribed",
                "ok": False,
                "detail": "webmail will not show it",
                "operation": "folder subscribe",
                "arguments": ["INBOX.Feeds"],
            },
        ],
    }


# ----------------------------------------------------------------------------
def test_a_folder_rename_that_repoints_no_filter_names_no_filter_set():
    done = FolderRenameResult(rename_plan(False), None, (), (), (), ())
    result = json_output.folder_rename_result(done)["result"]

    assert result["filterset"] is None
    assert result["backup"] is None
    assert result["ok"] is True


# ----------------------------------------------------------------------------
def test_old_name_carries_names_and_places_and_null_for_no_directory():
    """Red if a finding loses its new name or its place, or the directory
    is reported where there is none to report (#307)."""
    found = [
        LegacySetting(
            "MXROUTE_PASSWORD",
            "MAILCTL_PASSWORD",
            Source(ENV_FILE, "MXROUTE_PASSWORD", Path("/x/mailctl.env")),
        ),
        LegacySetting(
            "MXROUTE_HOST", "MAILCTL_HOST", Source(ENVIRONMENT, "MXROUTE_HOST")
        ),
    ]

    assert json_output.old_name(found, None) == {
        "settings": [
            {
                "old": "MXROUTE_PASSWORD",
                "new": "MAILCTL_PASSWORD",
                "where": "env file",
                "path": "/x/mailctl.env",
            },
            {
                "old": "MXROUTE_HOST",
                "new": "MAILCTL_HOST",
                "where": "environment",
                "path": None,
            },
        ],
        "config_dir": None,
    }

    pending = ConfigDirPending(Path("/c/mxfilter"), Path("/c/mailctl"))

    assert json_output.old_name([], pending)["config_dir"] == {
        "old": "/c/mxfilter",
        "new": "/c/mailctl",
        "operation": "config migrate",
    }


# ############################################################################
# A filter's steps
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_filter_carries_its_steps_in_neutral_words_beside_its_actions():
    """Red if a step loses its kind, argument, or creates, if a kind is
    written in the model's spelling rather than the document's, or if
    ``actions`` stops being the host's own names (#259)."""
    rule = Rule(0, "keep-boss", "anyof", actions=("fileinto", "foo", "stop"))
    steps = (
        ActionStep(FILEINTO, "INBOX.Boss", creates=True),
        ActionStep(OTHER, "foo"),
        ActionStep(STOP),
    )
    report = RulesReport("managesieve", [rule], [])

    (listed,) = json_output.rules_report(report, [steps])["filters"]

    assert listed["actions"] == ["fileinto", "foo", "stop"]
    assert listed["steps"] == [
        {"kind": "file", "argument": "INBOX.Boss", "creates": True},
        {"kind": "other", "argument": "foo", "creates": False},
        {"kind": "stop", "argument": None, "creates": False},
    ]


# ----------------------------------------------------------------------------
def test_every_step_kind_a_provider_reports_has_a_document_word():
    """The kinds are read off what mxroute's dialect reports, plus OTHER
    for an action it has no kind for, not listed here; red if one has no
    word, which would fail the whole document."""
    reported = set(_ACTION_KINDS.values()) | {OTHER}

    assert reported <= set(json_output.STEP_KINDS)
    assert len(set(json_output.STEP_KINDS.values())) == len(
        json_output.STEP_KINDS
    )
