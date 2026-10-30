"""The API, driven across a process boundary by a plain line-reading client.

Each test starts ``tests/api_child.py`` -- the real API, with the two
protocol clients replaced by doubles that keep their state in a file -- and
talks to it as a client in any language would: write one JSON line to its
stdin, read JSON lines off its stdout. Nothing here imports the server, so
what is tested is what crosses the boundary.

The password is held to the bar it is held to everywhere: the sentinel a
test gives through ``credentials.prompt`` must appear in no line on stdout
and nowhere on stderr.
"""

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from api_support import TIMEOUT, Client
from cli_support import command_parsers

from mailctl import cli, engine, json_output
from mailctl.api import server
from mailctl.config import Config
from mailctl.utilities import reports

CHILD = Path(__file__).with_name("api_child.py")

USER = "user@example.com"
PASSWORD = "api-test-account-password"
SENTINEL = "sentinel-Pa55word-7c1e9f"

ROUNDCUBE = """require ["fileinto","imap4flags"];
# rule:[keep-boss]
if header :contains "from" "boss@example.com"
{
\tfileinto "INBOX.Boss";
\tstop;
}
"""

GITHUB_MAIL = (
    "From: GitHub <noreply@github.com>\r\n"
    "To: user@example.com\r\n"
    "Subject: Build passed\r\n"
    "Date: Mon, 01 Sep 2025 10:00:00 +0000\r\n"
    "\r\n"
    "All green.\r\n"
)

BOSS_MAIL = (
    "From: The Boss <boss@example.com>\r\n"
    "To: user@example.com\r\n"
    "Subject: Quarterly numbers\r\n"
    "Date: Tue, 02 Sep 2025 10:00:00 +0000\r\n"
    "\r\n"
    "See attached.\r\n"
)

GITHUB_FILTER = {
    "version": 1,
    "criteria": {
        "match": "all",
        "compare": "contains",
        "terms": [{"header": "From", "value": "noreply@github.com"}],
    },
}


# ############################################################################
# The client
# ############################################################################


class ChildClient(Client):
    """A client of ``api_child.py``, which also reads and writes the
    doubles' state file."""

    # ------------------------------------------------------------------------
    def __init__(self, path: Path, argv: list[str], env: dict[str, str]):
        self.state = path / "state.json"

        super().__init__([sys.executable, str(CHILD), *argv], env, path)

    # ------------------------------------------------------------------------
    def read_state(self) -> dict:
        return json.loads(self.state.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------------
    def write_state(self, state: dict) -> None:
        self.state.write_text(json.dumps(state), encoding="utf-8")


# ----------------------------------------------------------------------------
@pytest.fixture
def start(tmp_path):
    """Start the API process over a fresh account; each test gets its own.

    ``password`` is the one the account takes, and the one configured
    through a password file unless ``configured`` is false, which leaves a
    prompt the only way to it.
    """
    started: list[ChildClient] = []

    def launch(
        *argv: str,
        password: str = PASSWORD,
        configured: bool = True,
        scripts: dict | None = None,
        active: str | None = "managesieve",
        **state,
    ) -> ChildClient:
        (tmp_path / "state.json").write_text(
            json.dumps(
                {
                    "password_sha256": hashlib.sha256(
                        password.encode()
                    ).hexdigest(),
                    "scripts": {"managesieve": ROUNDCUBE}
                    if scripts is None
                    else scripts,
                    "active": active,
                    "messages": {"1": GITHUB_MAIL, "2": BOSS_MAIL},
                    **state,
                }
            ),
            encoding="utf-8",
        )

        env = {
            **os.environ,
            "MAILCTL_TEST_STATE": str(tmp_path / "state.json"),
            "MAILCTL_HOST": "mail.example.com",
            "MAILCTL_USER": USER,
            "MAILCTL_BACKUP_DIR": str(tmp_path / "backups"),
        }

        if configured:
            secret = tmp_path / "password"
            secret.write_text(password, encoding="utf-8")
            secret.chmod(0o600)
            env["MAILCTL_PASSWORD_FILE"] = str(secret)

        client = ChildClient(tmp_path, list(argv), env)
        started.append(client)

        return client

    yield launch

    for client in started:
        if client.process.poll() is None:
            client.process.kill()
            client.process.wait(timeout=TIMEOUT)


# ----------------------------------------------------------------------------
def initialized(start, *argv: str, log: bool = False, **state) -> ChildClient:
    client = start(*argv, **state)
    response = client.request("initialize", {"log": log} if log else None)

    assert "result" in response, response

    return client


# ----------------------------------------------------------------------------
def refusal(response: dict) -> dict:
    """The error document of a refused request; fails on any other."""
    error = response["error"]

    assert error["code"] == 1, error
    assert error["data"]["version"] == json_output.JSON_VERSION

    return error["data"]["error"]


# ----------------------------------------------------------------------------
def events(response: dict) -> list[dict]:
    return [
        note["params"]["document"]["event"]
        for note in response["notes"]
        if note["method"] == "event"
    ]


# ----------------------------------------------------------------------------
def add_plan(client: ChildClient, **extra) -> dict:
    return client.request(
        "filter.add.plan",
        {
            "filter": GITHUB_FILTER,
            "actions": {"move_to": "INBOX.Lists"},
            "name": "github",
            **extra,
        },
    )


# ############################################################################
# Lifecycle
# ############################################################################


# ----------------------------------------------------------------------------
def test_initialize_reports_the_versions_and_the_offer_and_connects_nothing(
    start,
):
    """Red if initialize logs in: a bad setting must be reported before
    any connection, as the CLI's lazy open has it."""
    client = start()
    result = client.request("initialize")["result"]

    assert result["protocol"] == server.PROTOCOL_VERSION
    assert result["json_version"] == json_output.JSON_VERSION
    assert result["provider"] == "mxroute"
    assert result["capabilities"] == json_output.capabilities(
        engine.capabilities_for(Config())
    )
    assert client.read_state().get("logins", 0) == 0
    assert client.request("shutdown")["result"] is None
    assert client.close()[0] == 0


# ----------------------------------------------------------------------------
def test_initialize_refuses_a_bad_setting_and_a_second_initialize(start):
    bad = start("--provider", "nosuch")

    assert "nosuch" in refusal(bad.request("initialize"))["message"]

    good = initialized(start)

    assert refusal(good.request("initialize"))["code"] == (
        "already_initialized"
    )


# ----------------------------------------------------------------------------
def test_a_method_before_initialize_is_refused(start):
    client = start()

    assert refusal(client.request("filter.list"))["code"] == "not_initialized"


# ----------------------------------------------------------------------------
def test_shutdown_ends_the_process_and_so_does_the_end_of_input(start):
    client = initialized(start)
    client.request("filter.list")
    client.request("shutdown")

    assert client.process.wait(timeout=TIMEOUT) == 0

    other = initialized(start)

    assert other.close()[0] == 0


# ----------------------------------------------------------------------------
def test_what_is_not_a_request_is_answered_as_the_specification_says(start):
    client = start()

    client.send_line(b"not json\n")
    assert client.receive()["error"]["code"] == -32700

    client.send_line(b"[]\n")
    assert client.receive()["error"]["code"] == -32600

    client.send({"jsonrpc": "2.0", "id": 9, "method": "no.such"})
    assert client.receive() == {
        "jsonrpc": "2.0",
        "id": 9,
        "error": {"code": -32601, "message": "no method 'no.such'"},
    }

    # A notification is never answered, even for a method that is not
    # there; the next response is to the request after it.
    client.send({"jsonrpc": "2.0", "method": "no.such"})
    assert client.request("shutdown")["result"] is None


# ############################################################################
# Reading
# ############################################################################


# ----------------------------------------------------------------------------
def test_filter_list_returns_the_filter_list_document(start):
    client = initialized(start)
    result = client.request("filter.list")["result"]

    assert result["version"] == json_output.JSON_VERSION
    assert result["filterset"] == "managesieve"
    assert [item["name"] for item in result["filters"]] == ["keep-boss"]


# ----------------------------------------------------------------------------
def test_filter_list_refuses_a_filter_set_with_no_active_one(start):
    client = initialized(start, active=None, scripts={})
    error = refusal(client.request("filter.list"))

    assert error["code"] == "no_active_script"
    assert error["fields"] == {"operation": "filterset list"}


# ----------------------------------------------------------------------------
def test_mail_search_finds_what_a_filter_document_selects(start):
    client = initialized(start)
    result = client.request("mail.search", {"filter": GITHUB_FILTER})["result"]

    assert result["folder"] == "INBOX"
    assert [item["uid"] for item in result["messages"]] == [1]
    assert result["messages"][0]["from"] == "GitHub <noreply@github.com>"


# ----------------------------------------------------------------------------
def test_mail_search_refuses_what_the_utility_refuses_and_a_wrong_type(start):
    client = initialized(start)

    assert (
        "at least 1"
        in refusal(client.request("mail.search", {"limit": 0}))["message"]
    )

    wrong = client.request("mail.search", {"limit": "many"})["error"]

    assert wrong == {
        "code": -32602,
        "message": "'limit' must be a whole number",
    }

    bad_filter = refusal(
        client.request("mail.search", {"filter": {"version": 99}})
    )

    assert bad_filter["message"].startswith("filter:")


# ----------------------------------------------------------------------------
def test_mail_view_returns_one_message(start):
    client = initialized(start)
    message = client.request("mail.view", {"uid": 2})["result"]["message"]

    assert message["uid"] == 2
    assert "See attached." in message["body"]
    assert {"name": "Subject", "value": "Quarterly numbers"} in message[
        "headers"
    ]


# ----------------------------------------------------------------------------
def test_mail_view_refuses_a_uid_from_a_renumbered_folder(start):
    client = initialized(start, uidvalidity={"INBOX": 7})
    error = refusal(client.request("mail.view", {"uid": 2, "uidvalidity": 6}))

    assert error["code"] == "uidvalidity_changed"


# ############################################################################
# Plan, decide, execute
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_plan_is_the_dry_run_document_and_writes_nothing(start):
    client = initialized(start)
    result = add_plan(client)["result"]
    plan = result["document"]["plan"]

    assert set(result) == {"plan_id", "document"}
    assert plan["command"] == "filter add"
    assert plan["filter"] == "github"
    assert plan["changes"] is True
    assert "github" in plan["diff"]["text"]
    assert client.read_state()["scripts"]["managesieve"] == ROUNDCUBE
    assert "stored" not in client.read_state()


# ----------------------------------------------------------------------------
def test_a_plan_refuses_an_action_the_provider_will_not_emit(start):
    """The provider's own refusal, with its pointer, as the CLI's
    ``--redirect`` meets."""
    client = initialized(start)
    error = refusal(
        add_plan(client, actions={"redirect": "someone@example.com"})
    )

    assert "redirect" in error["message"]


# ----------------------------------------------------------------------------
def test_a_plan_refuses_criteria_a_delivered_message_cannot_meet(start):
    unread = {**GITHUB_FILTER, "criteria": {**GITHUB_FILTER["criteria"]}}
    unread["criteria"]["unread"] = True
    client = initialized(start)

    assert refusal(add_plan(client, filter=unread))["message"]


# ----------------------------------------------------------------------------
def test_a_plan_runs_once_and_its_events_come_before_its_answer(start):
    """The backup is announced before the upload, and both before the
    response; a second execute of the same id is refused, and stores
    nothing."""
    client = initialized(start)
    plan_id = add_plan(client)["result"]["plan_id"]
    response = client.request("filter.add.execute", {"plan_id": plan_id})
    result = response["result"]["result"]

    assert [event["kind"] for event in events(response)] == [
        "filterset_backed_up",
        "filterset_uploaded",
    ]
    assert result["filter"] == "github"
    assert result["filterset"] == "managesieve"
    assert result["backup"] == events(response)[0]["path"]
    assert Path(result["backup"]).read_text(encoding="utf-8") == ROUNDCUBE
    assert all(
        note["params"]["request"] == response["id"]
        for note in response["notes"]
    )

    state = client.read_state()

    assert "# rule:[github]" in state["scripts"]["managesieve"]
    assert state["stored"] == ["managesieve"]

    again = client.request("filter.add.execute", {"plan_id": plan_id})

    assert refusal(again)["code"] == "plan_unknown"
    assert client.read_state()["stored"] == ["managesieve"]


# ----------------------------------------------------------------------------
def test_a_discarded_plan_cannot_be_executed(start):
    client = initialized(start)
    plan_id = add_plan(client)["result"]["plan_id"]

    assert client.request("plan.discard", {"plan_id": plan_id})["result"] is (
        None
    )

    executed = client.request("filter.add.execute", {"plan_id": plan_id})
    discarded = client.request("plan.discard", {"plan_id": plan_id})

    assert refusal(executed)["code"] == "plan_unknown"
    assert refusal(discarded)["code"] == "plan_unknown"
    assert "stored" not in client.read_state()


# ----------------------------------------------------------------------------
def test_a_filter_set_changed_after_the_plan_is_refused_at_execute(start):
    """Red if execute uploads the plan's merge over the other client's
    edit: ADR 0002's failure, across the decide step."""
    client = initialized(start)
    plan_id = add_plan(client)["result"]["plan_id"]

    state = client.read_state()
    edited = ROUNDCUBE + "# rule:[made-elsewhere]\nkeep;\n"
    state["scripts"]["managesieve"] = edited
    client.write_state(state)

    error = refusal(client.request("filter.add.execute", {"plan_id": plan_id}))

    assert error["code"] == "filterset_changed"
    assert error["fields"] == {
        "operation": "filterset show",
        "arguments": ["managesieve"],
    }
    assert client.read_state()["scripts"]["managesieve"] == edited

    # Failed is gone too.
    retry = client.request("filter.add.execute", {"plan_id": plan_id})

    assert refusal(retry)["code"] == "plan_unknown"


# ----------------------------------------------------------------------------
def test_a_plan_id_is_the_only_thing_execute_takes(start):
    """The client cannot alter what runs by sending the plan back."""
    client = initialized(start)
    plan_id = add_plan(client)["result"]["plan_id"]
    response = client.request(
        "filter.add.execute", {"plan_id": plan_id, "name": "other"}
    )

    assert response["error"]["code"] == -32602
    assert "name" in response["error"]["message"]


# ############################################################################
# The password
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_password_given_through_the_prompt_is_used_and_never_echoed(start):
    """The sentinel reaches the connection -- the double logs in only on
    its hash -- and appears in no line on stdout and nothing on stderr,
    through a success, a refusal, and ``--debug``'s tracebacks."""
    client = start("--debug", password=SENTINEL, configured=False)
    client.answer = {"result": {"password": SENTINEL}}
    client.request("initialize", {"log": True})

    listed = client.request("filter.list")
    refused = client.request("mail.search", {"limit": 0})
    client.request("mail.search", {"filter": {"version": 99}})
    client.request("mail.view", {"uid": 2, "password": SENTINEL})
    client.request("shutdown")
    code, stderr = client.close()

    assert "result" in listed
    assert refused["error"]["code"] == 1
    assert client.prompted == 1
    assert code == 0
    assert stderr, "--debug wrote no traceback, so stderr proves nothing"
    assert SENTINEL.encode() not in b"".join(client.lines)
    assert SENTINEL.encode() not in stderr


# ----------------------------------------------------------------------------
def test_a_declined_prompt_is_refused_and_asked_again_next_time(start):
    client = start(configured=False)
    client.request("initialize")

    error = refusal(client.request("filter.list"))

    assert error["code"] == "password_declined"

    client.answer = {"result": {"password": PASSWORD}}

    assert "result" in client.request("filter.list")
    assert client.prompted == 2


# ----------------------------------------------------------------------------
def test_an_answer_with_no_password_in_it_is_refused(start):
    client = start(configured=False)
    client.answer = {"result": {"secret": PASSWORD}}
    client.request("initialize")

    assert refusal(client.request("filter.list"))["code"] == (
        "password_declined"
    )


# ----------------------------------------------------------------------------
def test_a_request_sent_while_the_prompt_waits_is_answered_after(start):
    """The client may send before answering; that request is taken in
    turn, once the one that asked has been answered."""
    client = start(configured=False)
    client.request("initialize")
    client.send({"jsonrpc": "2.0", "id": "list", "method": "filter.list"})

    prompt = client.receive()

    assert prompt["method"] == "credentials.prompt"
    assert prompt["params"]["prompt"] == f"Password for {USER}: "

    client.send({"jsonrpc": "2.0", "id": "second", "method": "filter.list"})
    client.send(
        {
            "jsonrpc": "2.0",
            "id": prompt["id"],
            "result": {"password": PASSWORD},
        }
    )

    assert "result" in client.until("list")
    assert "result" in client.until("second")


# ############################################################################
# Notifications
# ############################################################################


# ----------------------------------------------------------------------------
def test_protocol_chatter_is_sent_only_when_initialize_asked(start):
    quiet = initialized(start)
    chatty = initialized(start, log=True)

    assert quiet.request("filter.list")["notes"] == []

    notes = chatty.request("filter.list")["notes"]

    assert notes
    assert {note["method"] for note in notes} == {"log"}
    assert {note["params"]["channel"] for note in notes} == {"filter"}


# ----------------------------------------------------------------------------
def test_a_server_alert_is_sent_always_and_once_per_session(start):
    client = initialized(start, alert="Mailbox is 95% full")

    first = client.request("mail.search")
    second = client.request("mail.search")
    alerts = [n for n in first["notes"] if n["method"] == "server.alert"]

    assert [note["params"]["document"]["alert"] for note in alerts] == [
        {"channel": "mail", "kind": "alert", "text": "Mailbox is 95% full"}
    ]
    assert alerts[0]["params"]["request"] == first["id"]
    assert second["notes"] == []


# ############################################################################
# The method set
# ############################################################################


# ----------------------------------------------------------------------------
def test_every_command_method_is_a_command_of_the_cli():
    """A method's name is its CLI command path, dotted, so the API and the
    help name an operation alike (ADR 0008); a write command is a plan and
    an execute."""
    commands = set(command_parsers())
    named = set(server.METHODS) - server.PROTOCOL_METHODS
    paths = {
        name.removesuffix(".plan").removesuffix(".execute").replace(".", " ")
        for name in named
    }

    assert named
    assert paths <= commands

    for name in named:
        if name.endswith(".plan"):
            assert name.removesuffix(".plan") + ".execute" in named

        if name.endswith(".execute"):
            assert name.removesuffix(".execute") + ".plan" in named


# ----------------------------------------------------------------------------
def test_the_api_takes_the_cli_connection_settings_but_the_inline_password():
    """Started with what a user would give ``mailctl``; ``--password`` is
    the one left out, as visible in the process list."""

    def options(parser) -> set[str]:
        return {
            option
            for action in parser._actions
            for option in action.option_strings
        }

    cli_side = options(
        cli.connection_parser(
            engine.capabilities_for(Config()), reports.wording(Config())
        )
    )
    api_side = {
        option
        for group in server.build_parser()._action_groups
        if group.title == "connection"
        for action in group._group_actions
        for option in action.option_strings
    }

    assert api_side == cli_side - {"-p", "--password"}
