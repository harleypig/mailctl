"""--json's documents, apart from any one command's snapshot (#151).

The snapshots (``tests/snapshots/cli/*-json.txt``) pin each command's
shape end to end; this holds what no snapshot can show: that a credential
is refused rather than serialised, what an unmapped value does, how a date
is written, how main() reports a failure it did not expect, and which
commands offer --json at all.
"""

import argparse
import json

import pytest

from mailctl import MailctlError, cli, json_output
from mailctl.config import Secret

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
    assert json.loads(text) == {"version": 1, "error": {"message": "a\nb"}}


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

    assert cli.main(["folders", "--json"]) == 1

    captured = capsys.readouterr()
    failure = json.loads(captured.err.splitlines()[-1])

    assert captured.out == ""
    assert failure["error"]["message"].startswith("unexpected RuntimeError")


# ----------------------------------------------------------------------------
def test_an_interrupt_under_json_is_a_json_line(monkeypatch, capsys):
    def interrupted(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "cmd_list", interrupted)

    assert cli.main(["list", "--json"]) == 130

    failure = json.loads(capsys.readouterr().err.splitlines()[-1])

    assert failure == {"version": 1, "error": {"message": "interrupted"}}


# ----------------------------------------------------------------------------
def test_a_probe_failure_under_json_is_a_json_line(monkeypatch, capsys):
    """probe keeps its own document (#101); its failure is main()'s."""

    def fail(sessions, config):
        raise MailctlError("probe failed")

    monkeypatch.setattr(cli.utilities.reports, "probe_servers", fail)

    assert cli.main(["probe", "--json"]) == 1

    captured = capsys.readouterr()

    assert captured.out == ""
    assert json.loads(captured.err.splitlines()[-1]) == {
        "version": 1,
        "error": {"message": "probe failed"},
    }


# ----------------------------------------------------------------------------
def test_what_a_command_prints_on_the_way_goes_to_stderr(monkeypatch, capsys):
    """The redirect, not each command's care, keeps stdout clean."""

    def chatty(args):
        print("a note for a person")

        return cli.emit_json(args, json_output.scripts("a", []))

    monkeypatch.setattr(cli, "cmd_list", chatty)

    assert cli.main(["list", "--json"]) == 0

    captured = capsys.readouterr()

    assert json.loads(captured.out)["scripts"] == [
        {"name": "a", "active": True}
    ]
    assert "a note for a person" in captured.err


# ############################################################################
# Which commands offer it
# ############################################################################


# ----------------------------------------------------------------------------
def subcommands() -> dict[str, argparse.ArgumentParser]:
    parser = cli.build_parser()
    action = next(
        item
        for item in parser._actions
        if isinstance(item, argparse._SubParsersAction)
    )

    return dict(action.choices)


# ----------------------------------------------------------------------------
def dests(parser: argparse.ArgumentParser) -> set[str]:
    return {item.dest for item in parser._actions}


# Writes whose --dry-run is not a plan against the server: backup and
# save-baseline write a local file, migrate-config moves local files.
LOCAL_WRITES = {"backup", "migrate-config", "save-baseline"}


# ----------------------------------------------------------------------------
def test_every_server_write_with_a_dry_run_offers_json():
    """Derived, so a write command added later is held to it on arrival."""
    commands = subcommands()
    writes = {
        name
        for name, parser in commands.items()
        if "dry_run" in dests(parser) and name not in LOCAL_WRITES
    }

    assert {"add", "apply", "restore", "subscribe"} <= writes

    for name in writes:
        assert "json" in dests(commands[name]), name


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name",
    ["search", "view", "folders", "rules", "list", "probe", "senders"],
)
def test_the_data_commands_offer_json(name):
    assert "json" in dests(subcommands()[name])


# ----------------------------------------------------------------------------
def test_a_report_for_a_person_offers_none():
    """'test' is read by a person; its contract is its exit code."""
    assert "json" not in dests(subcommands()["test"])


# ----------------------------------------------------------------------------
def test_search_offers_uids_only():
    assert "uids_only" in dests(subcommands()["search"])
