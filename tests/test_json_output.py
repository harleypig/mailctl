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
from mailctl.providers.base import FolderCreation
from mailctl.providers.model import FILEINTO, OTHER, STOP, ActionStep
from mailctl.providers.mxroute.sieve import _ACTION_KINDS
from mailctl.rules import Rule
from mailctl.utilities import events
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
        "filter add", record, Path("/b/managesieve.sieve")
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
