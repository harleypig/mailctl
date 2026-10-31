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

# Two filters, so one can be moved, and two the optimizer merges: the same
# test on the same header with the same actions, one after the other.
TWO_FILTERS = (
    ROUNDCUBE
    + """# rule:[github]
if header :contains "from" "noreply@github.com"
{
\tfileinto "INBOX.Lists";
\tstop;
}
"""
)

# TWO_FILTERS with github disabled, as ``filter disable`` writes it.
GITHUB_DISABLED = TWO_FILTERS.replace(
    'if header :contains "from" "noreply@github.com"',
    'if false # header :contains "from" "noreply@github.com"',
)

MERGEABLE = (
    ROUNDCUBE
    + """# rule:[keep-ceo]
if header :contains "from" "ceo@example.com"
{
\tfileinto "INBOX.Boss";
\tstop;
}
"""
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
    prompt the only way to it, or names another password for the file.
    """
    started: list[ChildClient] = []

    def launch(
        *argv: str,
        password: str = PASSWORD,
        configured: bool | str = True,
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
            secret.write_text(
                configured if isinstance(configured, str) else password,
                encoding="utf-8",
            )
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
    assert result["old_name"] == {"settings": [], "config_dir": None}
    assert client.read_state().get("logins", 0) == 0
    assert client.request("shutdown")["result"] is None
    assert client.close()[0] == 0


# ----------------------------------------------------------------------------
def assert_sentinel_absent(client: ChildClient) -> None:
    """The sentinel is on no line of stdout and nowhere on stderr."""
    code, stderr = client.close()

    assert code == 0
    assert client.lines
    assert all(SENTINEL.encode() not in line for line in client.lines)
    assert SENTINEL.encode() not in stderr


# ----------------------------------------------------------------------------
def test_initialize_names_an_old_setting_in_the_env_file_never_its_value(
    start, tmp_path
):
    """Red if initialize does not report an old name the env file sets, or
    if the old password's value reaches any output (#307)."""
    env_file = tmp_path / "mailctl.env"
    env_file.write_text(f"MXROUTE_PASSWORD={SENTINEL}\n", encoding="utf-8")
    env_file.chmod(0o600)

    client = start("--debug", "--env-file", str(env_file))
    result = client.request("initialize")["result"]

    assert result["old_name"] == {
        "settings": [
            {
                "old": "MXROUTE_PASSWORD",
                "new": "MAILCTL_PASSWORD",
                "where": "env file",
                "path": str(env_file),
            }
        ],
        "config_dir": None,
    }

    # The old name is not read: the password still comes from the
    # configured file, and logs in.
    assert "result" in client.request("filter.list")

    assert_sentinel_absent(client)


# ----------------------------------------------------------------------------
def test_initialize_names_an_old_setting_in_the_environment(
    start, monkeypatch
):
    monkeypatch.setenv("MXROUTE_PASSWORD", SENTINEL)

    client = start("--debug")
    result = client.request("initialize")["result"]

    assert result["old_name"]["settings"] == [
        {
            "old": "MXROUTE_PASSWORD",
            "new": "MAILCTL_PASSWORD",
            "where": "environment",
            "path": None,
        }
    ]

    assert_sentinel_absent(client)


# ----------------------------------------------------------------------------
def test_initialize_names_an_old_config_dir_with_no_new_one(start, tmp_path):
    """Red if initialize does not report the old directory, or if anything
    in it reaches any output (#307)."""
    home = Path(os.environ["XDG_CONFIG_HOME"])
    old = home / "mxfilter"
    old.mkdir(parents=True)
    (old / "config.toml").write_text(
        f'password_cmd = "echo {SENTINEL}"\n', encoding="utf-8"
    )

    client = start("--debug")
    result = client.request("initialize")["result"]

    assert result["old_name"] == {
        "settings": [],
        "config_dir": {
            "old": str(old),
            "new": str(home / "mailctl"),
            "operation": "config migrate",
        },
    }
    assert "result" in client.request("filter.list")

    assert_sentinel_absent(client)


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
def test_filter_list_says_what_each_filter_does_in_neutral_steps(start):
    """Red if ``steps`` is missing, or words what the filter does in the
    host's language, or if ``actions`` is no longer the host's (#259)."""
    client = initialized(start)
    (listed,) = client.request("filter.list")["result"]["filters"]

    assert listed["actions"] == ["fileinto", "stop"]
    assert listed["steps"] == [
        {"kind": "file", "argument": None, "creates": False},
        {"kind": "stop", "argument": None, "creates": False},
    ]


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
def test_mail_search_lists_every_match_for_a_null_limit_and_20_for_none(start):
    """Red if an explicit null ``limit`` is read as an absent one (#303)."""
    many = {str(uid): GITHUB_MAIL for uid in range(1, 26)}
    client = initialized(start, messages=many)

    every = client.request("mail.search", {"limit": None})["result"]
    default = client.request("mail.search", {})["result"]

    assert len(every["messages"]) == 25
    assert len(default["messages"]) == 20


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
def test_mail_search_like_finds_mail_like_a_message(start):
    """Red if ``like`` is not taken, or derives nothing from the message."""
    client = initialized(start)
    result = client.request("mail.search", {"like": {"uid": 1}})["result"]

    assert [item["uid"] for item in result["messages"]] == [1]


# ----------------------------------------------------------------------------
def test_mail_search_like_merges_with_a_filter_as_the_cli_does(start):
    """A header the filter gives replaces what was derived for it, and
    its ``match`` governs: red if ``like`` overrides the filter, or the
    two are not merged at all."""
    client = initialized(start)
    merged = client.request(
        "mail.search", {"like": {"uid": 2}, "filter": GITHUB_FILTER}
    )["result"]
    built = client.request(
        "mail.search",
        {"like": {"uid": 2}, "filter": GITHUB_FILTER, "build_filter": True},
    )["result"]

    assert [item["uid"] for item in merged["messages"]] == [1]
    assert built == GITHUB_FILTER


# ----------------------------------------------------------------------------
def test_mail_search_builds_the_filter_add_takes_and_saves_nothing(start):
    """Red if ``build_filter`` lists messages instead, returns anything
    but the filter document, or writes."""
    client = initialized(start)
    built = client.request(
        "mail.search", {"like": {"uid": 2}, "build_filter": True}
    )["result"]

    assert built == {
        "version": 1,
        "criteria": {
            "match": "any",
            "compare": "contains",
            "terms": [{"header": "From", "value": "boss@example.com"}],
        },
    }
    assert [
        item["name"]
        for item in client.request("filter.list")["result"]["filters"]
    ] == ["keep-boss"]

    plan = client.request(
        "filter.add.plan",
        {"filter": built, "actions": {"move_to": "INBOX.Boss"}},
    )["result"]

    assert plan["document"]["plan"]["criteria"] == built["criteria"]


# ----------------------------------------------------------------------------
def test_mail_search_like_refuses_a_uid_from_a_renumbered_folder(start):
    client = initialized(start, uidvalidity={"INBOX": 7})
    stale = {"like": {"uid": 2, "uidvalidity": 6}}

    assert refusal(client.request("mail.search", stale))["code"] == (
        "uidvalidity_changed"
    )
    assert refusal(
        client.request("mail.search", {**stale, "build_filter": True})
    )["code"] == ("uidvalidity_changed")


# ----------------------------------------------------------------------------
def test_mail_search_like_and_build_filter_refuse_what_the_cli_refuses(start):
    """A UID below 1, a UID the folder lacks, no criteria to build from,
    and the combinations ``mail search`` refuses: ``raw`` with ``like``
    or ``build_filter``, and ``sort`` with ``build_filter``."""
    client = initialized(start)

    def error(params: dict) -> dict:
        return client.request("mail.search", params)["error"]

    assert error({"like": {"uid": 0}})["code"] == -32602
    assert error({"like": {}})["code"] == -32602
    assert error({"like": {"uid": 1, "folder": "x"}})["code"] == -32602
    assert (
        "uid 9"
        in refusal(client.request("mail.search", {"like": {"uid": 9}}))[
            "message"
        ]
    )
    assert refusal(client.request("mail.search", {"build_filter": True}))[
        "code"
    ] == ("no_criteria")
    assert error({"raw": "ALL", "like": {"uid": 1}})["code"] == -32602
    assert error({"raw": "ALL", "build_filter": True})["code"] == -32602
    assert (
        error({"sort": {"key": "size"}, "build_filter": True})["code"]
        == -32602
    )


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
    """The client cannot alter what runs by sending the plan back. Red if
    the refused request uses the plan up."""
    client = initialized(start)
    plan_id = add_plan(client)["result"]["plan_id"]
    response = client.request(
        "filter.add.execute", {"plan_id": plan_id, "name": "other"}
    )

    assert response["error"]["code"] == -32602
    assert "name" in response["error"]["message"]

    # Refused as a request, so the plan is not used up by it.
    assert "result" in client.request(
        "filter.add.execute", {"plan_id": plan_id}
    )


# ############################################################################
# The rest of the filter group
# ############################################################################

# Each command that changes the filter set: its method's name, what its
# plan is given, the filter set it starts from, the command its plan
# document names, the filter its result names, and a line only the
# stored result holds.
SCRIPT_WRITES = {
    "remove": (
        "filter.remove",
        {"name": "github"},
        TWO_FILTERS,
        "filter remove",
        "github",
        None,
    ),
    "move": (
        "filter.move",
        {"name": "github", "placement": {"where": "first"}},
        TWO_FILTERS,
        "filter move",
        "github",
        "github",
    ),
    "rename": (
        "filter.rename",
        {"name": "github", "new_name": "gh"},
        TWO_FILTERS,
        "filter rename",
        "gh",
        "# rule:[gh]",
    ),
    "disable": (
        "filter.disable",
        {"name": "github"},
        TWO_FILTERS,
        "filter disable",
        "github",
        'if false # header :contains "from" "noreply@github.com"',
    ),
    "enable": (
        "filter.enable",
        {"name": "github"},
        GITHUB_DISABLED,
        "filter enable",
        "github",
        'if header :contains "from" "noreply@github.com"',
    ),
    "optimize": (
        "filter.optimize",
        {},
        MERGEABLE,
        "filter optimize",
        None,
        '["boss@example.com", "ceo@example.com"]',
    ),
}


# ----------------------------------------------------------------------------
def script_plan(client: ChildClient, case: str, **extra) -> dict:
    method, params, *_rest = SCRIPT_WRITES[case]

    return client.request(f"{method}.plan", {**params, **extra})


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("case", sorted(SCRIPT_WRITES))
def test_a_filter_set_write_plans_then_stores_once_with_a_backup(start, case):
    """Red if a plan writes, if execute stores anything but the plan's
    result, if its events do not come first, backup before upload, or if
    the id can be used twice."""
    method, _params, scripts, command, name, only_after = SCRIPT_WRITES[case]
    client = initialized(start, scripts={"managesieve": scripts})
    result = script_plan(client, case)["result"]
    plan = result["document"]["plan"]

    assert plan["command"] == command
    assert plan["changes"] is True
    assert plan["filterset"] == "managesieve"
    assert "stored" not in client.read_state()

    response = client.request(
        f"{method}.execute", {"plan_id": result["plan_id"]}
    )
    done = response["result"]["result"]
    stored = client.read_state()["scripts"]["managesieve"]

    assert [event["kind"] for event in events(response)] == [
        "filterset_backed_up",
        "filterset_uploaded",
    ]
    assert done["command"] == command
    assert done["filter"] == name
    assert Path(done["backup"]).read_text(encoding="utf-8") == scripts
    assert client.read_state()["stored"] == ["managesieve"]

    if case == "remove":
        assert "# rule:[github]" not in stored
        assert "# rule:[keep-boss]" in stored

    elif case == "move":
        assert stored.index("rule:[github]") < stored.index("rule:[keep-boss]")

    else:
        assert only_after in stored

    again = client.request(f"{method}.execute", {"plan_id": result["plan_id"]})

    assert refusal(again)["code"] == "plan_unknown"
    assert client.read_state()["stored"] == ["managesieve"]


# ----------------------------------------------------------------------------
def test_a_disabled_filter_is_enabled_again(start):
    """Red if ``filter.enable`` switches the filter the wrong way, or
    names another command."""
    client = initialized(start, scripts={"managesieve": TWO_FILTERS})

    for method in ("filter.disable", "filter.enable"):
        result = client.request(f"{method}.plan", {"name": "github"})["result"]

        assert result["document"]["plan"]["enable"] is (
            method.endswith("enable")
        )
        assert "result" in client.request(
            f"{method}.execute", {"plan_id": result["plan_id"]}
        )

    stored = client.read_state()["scripts"]["managesieve"]

    assert "if false" not in stored
    assert "# rule:[github]" in stored


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("case", sorted(SCRIPT_WRITES))
def test_a_filter_set_write_refuses_a_set_changed_since_the_plan(start, case):
    """Red if execute uploads over another client's edit (ADR 0002)."""
    method, _params, scripts, *_rest = SCRIPT_WRITES[case]
    client = initialized(start, scripts={"managesieve": scripts})
    plan_id = script_plan(client, case)["result"]["plan_id"]

    state = client.read_state()
    edited = scripts + "# rule:[made-elsewhere]\nkeep;\n"
    state["scripts"]["managesieve"] = edited
    client.write_state(state)

    error = refusal(client.request(f"{method}.execute", {"plan_id": plan_id}))

    assert error["code"] == "filterset_changed"
    assert client.read_state()["scripts"]["managesieve"] == edited
    assert "stored" not in client.read_state()

    retry = client.request(f"{method}.execute", {"plan_id": plan_id})

    assert refusal(retry)["code"] == "plan_unknown"


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["add", *sorted(SCRIPT_WRITES)])
def test_a_filter_set_write_refuses_an_active_set_switched_since_the_plan(
    start, case
):
    """Red if execute uploads once another client has made another filter
    set active: the plan decided from the old one whether to activate its
    own, so the switch would be undone, unseen (#308)."""
    if case == "add":
        method, scripts = "filter.add", ROUNDCUBE

    else:
        method, _params, scripts, *_rest = SCRIPT_WRITES[case]

    client = initialized(start, scripts={"managesieve": scripts})
    plan = add_plan(client) if case == "add" else script_plan(client, case)
    plan_id = plan["result"]["plan_id"]

    state = client.read_state()
    state["scripts"]["elsewhere"] = "keep;\n"
    state["active"] = "elsewhere"
    client.write_state(state)

    error = refusal(client.request(f"{method}.execute", {"plan_id": plan_id}))
    after = client.read_state()

    assert error["code"] == "filterset_changed"
    assert error["fields"] == {"operation": "filterset list", "arguments": []}
    assert after["active"] == "elsewhere"
    assert after["scripts"]["managesieve"] == scripts
    assert "stored" not in after
    assert not (client.state.parent / "backups").exists()


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("case", sorted(SCRIPT_WRITES))
def test_a_filter_set_write_runs_only_its_own_plan_by_its_id(start, case):
    """Red if execute takes anything the plan holds, if another command's
    execute runs this plan, or if a discarded plan still runs."""
    method, _params, scripts, *_rest = SCRIPT_WRITES[case]
    client = initialized(start, scripts={"managesieve": scripts})
    plan_id = script_plan(client, case)["result"]["plan_id"]

    sent_back = client.request(
        f"{method}.execute", {"plan_id": plan_id, "name": "other"}
    )
    other = "filter.add" if case != "remove" else "filter.move"
    elsewhere = client.request(f"{other}.execute", {"plan_id": plan_id})

    assert sent_back["error"]["code"] == -32602
    assert refusal(elsewhere)["code"] == "plan_unknown"

    # Neither used the plan up: it is still there to discard.
    assert client.request("plan.discard", {"plan_id": plan_id})["result"] is (
        None
    )

    discarded = client.request(f"{method}.execute", {"plan_id": plan_id})

    assert refusal(discarded)["code"] == "plan_unknown"
    assert "stored" not in client.read_state()


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("method", "params", "scripts"),
    [
        (
            "filter.move",
            {"name": "github", "placement": {"where": "last"}},
            TWO_FILTERS,
        ),
        ("filter.enable", {"name": "github"}, TWO_FILTERS),
        (
            "filter.rename",
            {"name": "github", "new_name": "github"},
            TWO_FILTERS,
        ),
        ("filter.optimize", {}, TWO_FILTERS),
    ],
)
def test_a_plan_that_changes_nothing_is_refused_at_execute(
    start, method, params, scripts
):
    """Red if a plan the CLI would stop after showing is carried out: a
    backup and an upload of the same bytes, and an activation nobody saw
    asked for."""
    client = initialized(start, scripts={"managesieve": scripts})
    result = client.request(f"{method}.plan", params)["result"]

    assert result["document"]["plan"]["changes"] is False
    assert result["document"]["plan"]["diff"] is None

    error = refusal(
        client.request(f"{method}.execute", {"plan_id": result["plan_id"]})
    )

    assert error["code"] == "nothing_to_change"
    assert "stored" not in client.read_state()


# ----------------------------------------------------------------------------
def test_a_filter_add_that_changes_nothing_is_still_carried_out(start):
    """``filter add`` on the CLI uploads a filter replaced by itself, and
    the API does the same. Red if ``filter.add.execute`` starts refusing
    a plan that changes nothing without the CLI doing so too."""
    client = initialized(start)
    first = add_plan(client)["result"]["plan_id"]

    assert "result" in client.request("filter.add.execute", {"plan_id": first})

    stored = client.read_state()["scripts"]["managesieve"]
    result = add_plan(client, replace=True)["result"]

    assert result["document"]["plan"]["changes"] is False

    response = client.request(
        "filter.add.execute", {"plan_id": result["plan_id"]}
    )

    assert [event["kind"] for event in events(response)] == [
        "filterset_backed_up",
        "filterset_uploaded",
    ]
    assert client.read_state()["stored"] == ["managesieve", "managesieve"]
    assert client.read_state()["scripts"]["managesieve"] == stored


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("capability", "method", "params"),
    [
        (
            "ordering",
            "filter.move",
            {"name": "github", "placement": {"where": "first"}},
        ),
        ("ordering", "filter.optimize", {}),
        ("disable", "filter.disable", {"name": "github"}),
        ("disable", "filter.enable", {"name": "github"}),
        ("rename", "filter.rename", {"name": "github", "new_name": "gh"}),
    ],
)
def test_a_command_the_provider_does_not_declare_is_refused_unread(
    start, monkeypatch, capability, method, params
):
    """The CLI hides these and refuses them below the front-end; the API
    meets the same refusal, before anything connects. Red if the method
    skips the check, or the check reads the filter set first."""
    monkeypatch.setenv("MAILCTL_TEST_DECLINE", capability)
    client = initialized(start, scripts={"managesieve": TWO_FILTERS})
    error = refusal(client.request(f"{method}.plan", params))

    assert (
        f"does not declare the {capability!r} capability" in error["message"]
    )
    assert client.read_state().get("logins", 0) == 0


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("method", "params", "words"),
    [
        ("filter.remove", {"name": "nosuch"}, "nosuch"),
        ("filter.disable", {"name": "nosuch"}, "nosuch"),
        (
            "filter.move",
            {"name": "github", "placement": {"where": "after", "filter": "x"}},
            "x",
        ),
        ("filter.rename", {"name": "nosuch", "new_name": "gh"}, "nosuch"),
        ("filter.rename", {"name": "github", "new_name": " "}, "empty"),
    ],
)
def test_a_filter_set_write_refuses_what_the_utility_refuses(
    start, method, params, words
):
    client = initialized(start, scripts={"managesieve": TWO_FILTERS})

    assert (
        words in refusal(client.request(f"{method}.plan", params))["message"]
    )
    assert "stored" not in client.read_state()


# ----------------------------------------------------------------------------
def test_a_rename_to_a_name_already_taken_is_refused_with_its_code(start):
    client = initialized(start, scripts={"managesieve": TWO_FILTERS})
    response = client.request(
        "filter.rename.plan", {"name": "github", "new_name": "keep-boss"}
    )

    assert refusal(response)["code"] == "rule_name_taken"


# ----------------------------------------------------------------------------
def test_filter_set_write_parameters_are_refused_as_parameters(start):
    """Red if a missing name, a missing or unknown placement, or an
    unknown kind to skip reaches the utilities instead."""
    client = initialized(start, scripts={"managesieve": TWO_FILTERS})
    asks = [
        ("filter.remove.plan", {}),
        ("filter.move.plan", {"name": "github"}),
        ("filter.move.plan", {"name": "github", "placement": {"where": "up"}}),
        ("filter.rename.plan", {"name": "github"}),
        ("filter.optimize.plan", {"skip": ["colour"]}),
        ("filter.disable.plan", {"name": "github", "yes": True}),
    ]

    for method, params in asks:
        assert client.request(method, params)["error"]["code"] == -32602, (
            method,
            params,
        )


# ----------------------------------------------------------------------------
def test_filter_optimize_skips_the_kinds_it_is_told_to(start):
    client = initialized(start, scripts={"managesieve": MERGEABLE})
    every = client.request("filter.optimize.plan", {})["result"]["document"]
    skipped = client.request("filter.optimize.plan", {"skip": ["merge"]})

    assert len(every["plan"]["merges"]) == 1
    assert skipped["result"]["document"]["plan"]["considered"] == [
        "redundant",
        "reorder",
    ]
    assert skipped["result"]["document"]["plan"]["merges"] == []


# ----------------------------------------------------------------------------
def apply_plan(client: ChildClient, **extra) -> dict:
    return client.request(
        "filter.apply.plan",
        {
            "filter": GITHUB_FILTER,
            "actions": {"move_to": "INBOX.Lists"},
            **extra,
        },
    )


# ----------------------------------------------------------------------------
def test_filter_apply_plans_the_pass_then_moves_the_mail_once(start):
    """Red if the plan touches mail, if execute does anything but the
    plan's move, or if the id can be used twice."""
    client = initialized(start)
    result = apply_plan(client)["result"]
    plan = result["document"]["plan"]

    assert plan["command"] == "filter apply"
    assert plan["changes"] is True
    assert plan["over_limit"] is False
    assert [item["uid"] for item in plan["mail"]["messages"]] == [1]
    assert plan["mail"]["destination"] == "INBOX.Lists"
    assert "imap_writes" not in client.read_state()

    response = client.request(
        "filter.apply.execute", {"plan_id": result["plan_id"]}
    )

    assert response["result"]["result"] == {
        "command": "filter apply",
        "source": "INBOX",
        "destination": "INBOX.Lists",
        "flagged": 0,
        "moved": 1,
        "deleted": 0,
        "copied": 0,
    }
    assert client.read_state()["imap_writes"] == [["move", [1], "INBOX.Lists"]]

    again = client.request(
        "filter.apply.execute", {"plan_id": result["plan_id"]}
    )

    assert refusal(again)["code"] == "plan_unknown"
    assert len(client.read_state()["imap_writes"]) == 1


# ----------------------------------------------------------------------------
def test_filter_apply_creates_its_folder_at_execute_and_says_so_first(start):
    """Red if the plan creates the folder, or execute moves mail into a
    folder it has not made; or if the plan asks the rules half how a
    filter would create it, since this pass saves no filter (the CLI's
    ``apply-create-dry-json`` snapshot is the same plan)."""
    client = initialized(start)
    result = apply_plan(
        client, actions={"move_to": "INBOX.New"}, create_folder=True
    )["result"]

    # Created over IMAP, and only so: the server advertises the mailbox
    # extension, but no filter is saved to create it on delivery.
    assert result["document"]["plan"]["folder"]["status"] == "create"
    assert result["document"]["plan"]["folder"]["mailbox_disabled_by"] is None
    assert "imap_writes" not in client.read_state()
    assert "sieve_connects" not in client.read_state()

    response = client.request(
        "filter.apply.execute", {"plan_id": result["plan_id"]}
    )
    writes = [write[0] for write in client.read_state()["imap_writes"]]

    assert [event["kind"] for event in events(response)] == ["folder_created"]
    assert writes == ["create_folder", "subscribe_folder", "move"]


# ----------------------------------------------------------------------------
def test_filter_apply_refuses_a_missing_folder_it_was_not_asked_to_make(
    start,
):
    client = initialized(start)
    error = refusal(apply_plan(client, actions={"move_to": "INBOX.New"}))

    assert error["code"] == "folder_missing"
    assert error["fields"] == {"folder": "INBOX.New"}


# ----------------------------------------------------------------------------
def test_filter_apply_over_its_ceiling_is_planned_and_refused_whole(start):
    """Red if the execute processes a partial batch, or reads the
    ceiling from anywhere but the plan."""
    every = {
        **GITHUB_FILTER,
        "criteria": {
            **GITHUB_FILTER["criteria"],
            "terms": [{"header": "To", "value": "user@example.com"}],
        },
    }
    client = initialized(start)
    result = apply_plan(client, filter=every, max_messages=1)["result"]

    assert result["document"]["plan"]["over_limit"] is True
    assert result["document"]["plan"]["mail"]["count"] == 2

    error = refusal(
        client.request("filter.apply.execute", {"plan_id": result["plan_id"]})
    )

    assert error["code"] == "max_messages"
    assert "imap_writes" not in client.read_state()


# ----------------------------------------------------------------------------
def test_filter_apply_refuses_a_folder_renumbered_since_the_plan(start):
    """Red if the execute moves UIDs that now name other messages."""
    client = initialized(start)
    plan_id = apply_plan(client)["result"]["plan_id"]

    state = client.read_state()
    state["uidvalidity"] = {"INBOX": 999}
    client.write_state(state)

    # A server cannot renumber the folder this connection holds selected
    # (RFC 9051 section 2.3.1.1), so another is selected in between, as a
    # long-lived client's next read would.
    assert "result" in client.request("mail.search", {"folder": "INBOX.Lists"})

    error = refusal(
        client.request("filter.apply.execute", {"plan_id": plan_id})
    )

    assert error["code"] == "uidvalidity_changed"
    assert "imap_writes" not in client.read_state()


# ----------------------------------------------------------------------------
def test_filter_apply_that_changes_nothing_is_planned_and_not_carried_out(
    start,
):
    """Filing mail into the folder it is in searches nothing."""
    client = initialized(start)
    result = apply_plan(client, actions={"move_to": "INBOX"})["result"]

    assert result["document"]["plan"]["changes"] is False
    assert result["document"]["plan"]["mail"] is None

    error = refusal(
        client.request("filter.apply.execute", {"plan_id": result["plan_id"]})
    )

    assert error["code"] == "nothing_to_change"
    assert "imap_writes" not in client.read_state()


# ----------------------------------------------------------------------------
def test_filter_apply_refuses_what_the_cli_refuses(start):
    """``stop`` means nothing to delivered mail, so it is not taken; the
    rest are the CLI's own refusals."""
    client = initialized(start)

    nothing = apply_plan(client, actions={})
    redirect = apply_plan(client, actions={"redirect": "a@example.com"})
    stop = apply_plan(
        client, actions={"move_to": "INBOX.Lists", "stop": False}
    )
    ceiling = apply_plan(client, max_messages=-1)
    unsubscribed = apply_plan(client, subscribe=False)

    assert refusal(nothing)["code"] == "no_mail_action"
    assert "redirect" in refusal(redirect)["message"]
    assert stop["error"]["code"] == -32602
    assert ceiling["error"]["code"] == -32602
    assert refusal(unsubscribed)["code"] == "subscribe_needs_create"
    assert "imap_writes" not in client.read_state()


# ############################################################################
# The filterset group
# ############################################################################


# ----------------------------------------------------------------------------
def test_filterset_list_and_show_read_the_filter_sets_and_no_mail(start):
    """Red if the active set is not listed first, if ``show`` changes a
    byte of the text or reads the wrong set, or if either opens IMAP,
    which ``filterset list`` and ``filterset show`` never do."""
    client = initialized(
        start,
        scripts={"managesieve": TWO_FILTERS, "other": "keep;\n"},
    )
    listed = client.request("filterset.list")["result"]
    shown = client.request("filterset.show")["result"]
    other = client.request("filterset.show", {"name": "other"})["result"]

    assert listed["filtersets"] == [
        {"name": "managesieve", "active": True},
        {"name": "other", "active": False},
    ]
    assert shown["filterset"] == {
        "name": "managesieve",
        "source": TWO_FILTERS,
        "filters": ["keep-boss", "github"],
    }
    assert other["filterset"]["name"] == "other"
    assert other["filterset"]["source"] == "keep;\n"
    assert "imap_connects" not in client.read_state()


# ----------------------------------------------------------------------------
def test_filterset_show_returns_text_that_does_not_parse(start):
    """The text is the point; red if a filter set too broken to parse is
    refused instead of shown, with null for its filters."""
    broken = "if header :contains {\n"
    client = initialized(start, scripts={"managesieve": broken})
    shown = client.request("filterset.show")["result"]["filterset"]

    assert shown == {"name": "managesieve", "source": broken, "filters": None}


# ----------------------------------------------------------------------------
def test_filterset_show_refuses_with_no_active_set_and_none_named(start):
    client = initialized(start, active=None)

    assert (
        "no active filter set"
        in (refusal(client.request("filterset.show"))["message"])
    )


# ----------------------------------------------------------------------------
def test_filterset_backup_writes_the_servers_bytes_and_dry_run_writes_none(
    start,
):
    """Red if a dry run writes, if the copy is not the server's exact
    bytes, if it is readable by anyone else, or if IMAP is opened."""
    client = initialized(start, scripts={"managesieve": TWO_FILTERS})
    target = client.state.parent / "kept" / "copy.sieve"

    planned = client.request(
        "filterset.backup", {"output": str(target), "dry_run": True}
    )["result"]["result"]

    assert planned == {
        "command": "filterset backup",
        "filterset": "managesieve",
        "path": str(target),
        "filters": 2,
        "written": False,
    }
    assert not target.parent.exists()

    done = client.request("filterset.backup", {"output": str(target)})

    assert done["result"]["result"] == {**planned, "written": True}
    assert target.read_bytes() == TWO_FILTERS.encode()
    assert target.stat().st_mode & 0o777 == 0o600
    assert target.parent.stat().st_mode & 0o777 == 0o700
    assert "imap_connects" not in client.read_state()


# ----------------------------------------------------------------------------
def test_filterset_backup_goes_to_the_backup_directory_by_default(start):
    client = initialized(start)
    done = client.request("filterset.backup")["result"]["result"]
    path = Path(done["path"])

    assert path.parent == client.state.parent / "backups"
    assert path.read_text(encoding="utf-8") == ROUNDCUBE


# ----------------------------------------------------------------------------
def test_filterset_backup_refuses_an_output_outside_the_backup_root(
    start, monkeypatch, tmp_path
):
    """``MAILCTL_BACKUP_ROOT`` holds on the API's host as on the CLI's.
    Red if the copy is written outside it, or the server is asked for
    the filter set first."""
    monkeypatch.setenv("MAILCTL_BACKUP_ROOT", str(tmp_path))
    client = initialized(start)
    outside = tmp_path.parent / f"{tmp_path.name}-outside" / "copy.sieve"

    error = refusal(
        client.request("filterset.backup", {"output": str(outside)})
    )

    assert error["code"] == "backup_outside_root"
    assert not outside.parent.exists()
    assert client.read_state().get("logins", 0) == 0


# ----------------------------------------------------------------------------
def restore_file(client: ChildClient, text: str = TWO_FILTERS) -> str:
    """A backup file in the API process's working directory, by the
    relative name a client would give it."""
    (client.state.parent / "restore.sieve").write_text(text, encoding="utf-8")

    return "restore.sieve"


# ----------------------------------------------------------------------------
def test_filterset_restore_plans_then_replaces_the_set_backing_it_up_first(
    start,
):
    """Red if the plan writes, if execute stores anything but the file's
    bytes, if the set it replaces is not backed up before the upload, if
    IMAP is opened, or if the id can be used twice."""
    client = initialized(start)
    result = client.request(
        "filterset.restore.plan", {"file": restore_file(client)}
    )["result"]
    plan = result["document"]["plan"]

    assert plan["command"] == "filterset restore"
    assert plan["changes"] is True
    assert plan["source"] == "restore.sieve"
    assert (plan["filters_before"], plan["filters_after"]) == (1, 2)
    assert plan["filterset"] == "managesieve"
    assert "stored" not in client.read_state()

    # The file changing after the plan changes nothing: what was shown is
    # what runs.
    restore_file(client, "keep;\n")

    response = client.request(
        "filterset.restore.execute", {"plan_id": result["plan_id"]}
    )
    done = response["result"]["result"]

    assert [event["kind"] for event in events(response)] == [
        "filterset_backed_up",
        "filterset_uploaded",
    ]
    assert done["command"] == "filterset restore"
    assert done["filter"] is None
    assert Path(done["backup"]).read_text(encoding="utf-8") == ROUNDCUBE
    assert client.read_state()["scripts"]["managesieve"] == TWO_FILTERS
    assert "imap_connects" not in client.read_state()

    again = client.request(
        "filterset.restore.execute", {"plan_id": result["plan_id"]}
    )

    assert refusal(again)["code"] == "plan_unknown"
    assert client.read_state()["stored"] == ["managesieve"]


# ----------------------------------------------------------------------------
def test_filterset_restore_the_server_rejects_is_backed_up_and_not_stored(
    start,
):
    """CHECKSCRIPT comes before the upload, and the backup before both.
    Red if a file the server rejects is stored, or no copy of what it
    would have replaced is left."""
    client = initialized(start, checkscript=False)
    plan_id = client.request(
        "filterset.restore.plan", {"file": restore_file(client)}
    )["result"]["plan_id"]

    error = refusal(
        client.request("filterset.restore.execute", {"plan_id": plan_id})
    )
    kept = list((client.state.parent / "backups").iterdir())

    assert "rejected" in error["message"]
    assert "stored" not in client.read_state()
    assert [path.read_text(encoding="utf-8") for path in kept] == [ROUNDCUBE]


# ----------------------------------------------------------------------------
def test_filterset_restore_refuses_what_the_cli_refuses(start):
    """An empty file, unless allowed; a file that is not there; and, with
    nothing active, a restore that names no filter set."""
    client = initialized(start)
    empty = restore_file(client, "\n")

    assert refusal(client.request("filterset.restore.plan", {"file": empty}))[
        "code"
    ] == ("empty_backup")
    assert (
        "could not read"
        in (
            refusal(
                client.request("filterset.restore.plan", {"file": "missing"})
            )["message"]
        )
    )

    allowed = client.request(
        "filterset.restore.plan", {"file": empty, "allow_empty": True}
    )

    assert allowed["result"]["document"]["plan"]["filters_after"] == 0

    nothing_active = initialized(start, active=None)
    restore_file(nothing_active)

    assert refusal(
        nothing_active.request(
            "filterset.restore.plan", {"file": "restore.sieve"}
        )
    )["code"] == ("restore_needs_script")
    assert "stored" not in client.read_state()


# ----------------------------------------------------------------------------
def test_filterset_restore_expands_nothing_in_the_file_it_is_given(
    start, monkeypatch
):
    """A client has no shell, so its path is taken as written. Red if
    ``$VAR``, ``${VAR}``, or ``~`` is expanded in the API's environment,
    or if a refusal quotes the expanded path: the variable here holds
    the sentinel, and it must reach no output, ``--debug`` on."""
    monkeypatch.setenv("MAILCTL_PASSWORD", SENTINEL)
    client = start("--debug")
    client.request("initialize")

    for spelled in ("$MAILCTL_PASSWORD", "${MAILCTL_PASSWORD}", "~"):
        error = refusal(
            client.request("filterset.restore.plan", {"file": spelled})
        )

        assert error["message"].startswith(
            f"could not read backup {spelled} --"
        )

    assert client.read_state().get("logins", 0) == 0

    assert_sentinel_absent(client)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["password", "password-link", "mailctl.env"])
def test_filterset_restore_refuses_a_credential_file_unread(
    start, tmp_path, name
):
    """Red if the password file, a link to it, or the env file in use is
    read as a backup: its contents would come back as the diff. Each is
    unreadable once the API has started, so a read would fail as
    ``could not read`` and not with this code."""
    secret = tmp_path / "password"
    env_file = tmp_path / "mailctl.env"
    env_file.write_text(f"MAILCTL_PASSWORD={SENTINEL}\n", encoding="utf-8")
    env_file.chmod(0o600)
    (tmp_path / "password-link").symlink_to(secret)

    client = start("--debug", "--env-file", str(env_file), password=SENTINEL)
    client.request("initialize")
    secret.chmod(0)
    env_file.chmod(0)

    response = client.request(
        "filterset.restore.plan", {"file": name, "allow_empty": True}
    )

    assert "result" not in response
    assert refusal(response)["code"] == "credential_file"
    assert client.read_state().get("logins", 0) == 0

    assert_sentinel_absent(client)


# ----------------------------------------------------------------------------
def test_filterset_restore_of_what_the_server_holds_is_not_carried_out(
    start,
):
    client = initialized(start)
    result = client.request(
        "filterset.restore.plan", {"file": restore_file(client, ROUNDCUBE)}
    )["result"]

    assert result["document"]["plan"]["changes"] is False
    assert result["document"]["plan"]["diff"] is None

    error = refusal(
        client.request(
            "filterset.restore.execute", {"plan_id": result["plan_id"]}
        )
    )

    assert error["code"] == "nothing_to_change"
    assert "stored" not in client.read_state()


# ############################################################################
# The folder group
# ############################################################################


# ----------------------------------------------------------------------------
def test_folder_list_lists_every_folder_and_opens_no_filter_set(start):
    """Red if a folder is dropped or its subscription misread, or if the
    rules half is opened, which ``folder list`` never does."""
    client = initialized(start)
    listed = client.request("folder.list")["result"]

    assert listed["delimiter"] == "."
    assert listed["folders"] == [
        {"name": "INBOX", "subscribed": True},
        {"name": "INBOX.Lists", "subscribed": True},
        {"name": "INBOX.spam", "subscribed": False},
    ]
    assert "sieve_connects" not in client.read_state()


# ----------------------------------------------------------------------------
def test_folder_list_counts_each_folder_from_one_request(start):
    client = initialized(start, imap_caps=["LIST-STATUS", "STATUS=SIZE"])
    listed = client.request("folder.list", {"counts": True})["result"]
    spam = listed["folders"][2]

    assert spam == {
        "name": "INBOX.spam",
        "subscribed": False,
        "messages": 12,
        "unseen": 12,
        "size": 30822,
    }
    assert "sieve_connects" not in client.read_state()


# ----------------------------------------------------------------------------
def test_folder_list_counts_are_refused_without_one_request_for_them(start):
    """A server without LIST-STATUS would need a request per folder."""
    client = initialized(start)
    error = refusal(client.request("folder.list", {"counts": True}))

    assert "LIST-STATUS" in error["message"]


# ----------------------------------------------------------------------------
def test_folder_create_plans_then_creates_and_subscribes_once(start):
    """Red if the plan creates, if execute does more than create and
    subscribe, if the rules half is opened, or if the id runs twice."""
    client = initialized(start)
    result = client.request(
        "folder.create.plan", {"folder": "INBOX.Projects"}
    )["result"]
    plan = result["document"]["plan"]

    assert plan["command"] == "folder create"
    assert plan["changes"] is True
    assert plan["folder"]["status"] == "create"
    assert plan["folder"]["subscribe"] is True
    assert "imap_writes" not in client.read_state()

    response = client.request(
        "folder.create.execute", {"plan_id": result["plan_id"]}
    )

    assert response["result"]["result"] == {
        "command": "folder create",
        "folder": "INBOX.Projects",
        "subscribed": True,
        "subscribe_error": None,
        "by_subscription": True,
    }
    assert client.read_state()["imap_writes"] == [
        ["create_folder", "INBOX.Projects"],
        ["subscribe_folder", "INBOX.Projects"],
    ]
    assert "sieve_connects" not in client.read_state()

    again = client.request(
        "folder.create.execute", {"plan_id": result["plan_id"]}
    )

    assert refusal(again)["code"] == "plan_unknown"
    assert len(client.read_state()["imap_writes"]) == 2


# ----------------------------------------------------------------------------
def test_folder_create_unsubscribed_creates_and_subscribes_nothing(start):
    client = initialized(start)
    result = client.request(
        "folder.create.plan", {"folder": "INBOX.Quiet", "subscribe": False}
    )["result"]
    response = client.request(
        "folder.create.execute", {"plan_id": result["plan_id"]}
    )

    assert response["result"]["result"]["subscribed"] is False
    assert client.read_state()["imap_writes"] == [
        ["create_folder", "INBOX.Quiet"]
    ]


# ----------------------------------------------------------------------------
def test_folder_create_of_a_folder_already_there_is_not_carried_out(start):
    client = initialized(start)
    result = client.request("folder.create.plan", {"folder": "INBOX.Lists"})[
        "result"
    ]

    assert result["document"]["plan"]["changes"] is False

    error = refusal(
        client.request("folder.create.execute", {"plan_id": result["plan_id"]})
    )

    assert error["code"] == "nothing_to_change"
    assert "imap_writes" not in client.read_state()


# ----------------------------------------------------------------------------
def test_folder_create_refuses_a_case_variant_of_a_folder(start):
    client = initialized(start)
    error = refusal(
        client.request("folder.create.plan", {"folder": "INBOX.lists"})
    )

    assert "case-sensitive" in error["message"]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("command", "folder", "write"),
    [
        ("subscribe", "INBOX.spam", "subscribe_folder"),
        ("unsubscribe", "INBOX.Lists", "unsubscribe_folder"),
    ],
)
def test_a_subscription_plans_then_changes_once(start, command, folder, write):
    """Red if the plan writes, if execute writes the other way, if the
    rules half is opened, or if the id runs twice."""
    client = initialized(start)
    result = client.request(f"folder.{command}.plan", {"folder": folder})[
        "result"
    ]
    plan = result["document"]["plan"]

    assert plan["command"] == f"folder {command}"
    assert plan["changes"] is True
    assert plan["subscribed_now"] is (command == "unsubscribe")
    assert "imap_writes" not in client.read_state()

    response = client.request(
        f"folder.{command}.execute", {"plan_id": result["plan_id"]}
    )

    assert response["result"]["result"] == {
        "command": f"folder {command}",
        "folder": folder,
        "subscribed": command == "subscribe",
    }
    assert client.read_state()["imap_writes"] == [[write, folder]]
    assert "sieve_connects" not in client.read_state()

    again = client.request(
        f"folder.{command}.execute", {"plan_id": result["plan_id"]}
    )

    assert refusal(again)["code"] == "plan_unknown"


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("command", "folder"),
    [("subscribe", "INBOX.Lists"), ("unsubscribe", "INBOX.spam")],
)
def test_a_subscription_already_as_asked_is_not_carried_out(
    start, command, folder
):
    client = initialized(start)
    result = client.request(f"folder.{command}.plan", {"folder": folder})[
        "result"
    ]

    assert result["document"]["plan"]["changes"] is False

    error = refusal(
        client.request(
            f"folder.{command}.execute", {"plan_id": result["plan_id"]}
        )
    )

    assert error["code"] == "nothing_to_change"
    assert "imap_writes" not in client.read_state()


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("command", ["subscribe", "unsubscribe"])
def test_a_subscription_to_no_folder_is_refused(start, command):
    client = initialized(start)
    error = refusal(
        client.request(f"folder.{command}.plan", {"folder": "INBOX.Nope"})
    )

    assert error["code"] == "no_such_folder"


# ----------------------------------------------------------------------------
def test_folder_rename_repoints_its_filters_backing_the_set_up_first(start):
    """Red if the plan writes, if the folder is renamed before the filter
    set is backed up and checked, if a filter is left filing into the old
    name, or if the subscriptions are not carried."""
    client = initialized(start, scripts={"managesieve": TWO_FILTERS})
    result = client.request(
        "folder.rename.plan", {"old": "INBOX.Lists", "new": "INBOX.Feeds"}
    )["result"]
    plan = result["document"]["plan"]

    assert plan["command"] == "folder rename"
    assert plan["filters"] == [
        {"filter": "github", "old": "INBOX.Lists", "new": "INBOX.Feeds"}
    ]
    assert plan["diff"] is not None
    assert "imap_writes" not in client.read_state()
    assert "stored" not in client.read_state()

    response = client.request(
        "folder.rename.execute", {"plan_id": result["plan_id"]}
    )
    done = response["result"]["result"]
    stored = client.read_state()["scripts"]["managesieve"]

    assert [event["kind"] for event in events(response)] == [
        "filterset_backed_up",
        "folder_renamed",
        "subscription_changed",
        "subscription_changed",
        "filterset_uploaded",
    ]
    assert done["ok"] is True
    assert done["filterset"] == "managesieve"
    assert done["subscribed"] == ["INBOX.Feeds"]
    assert done["unsubscribed"] == ["INBOX.Lists"]
    assert Path(done["backup"]).read_text(encoding="utf-8") == TWO_FILTERS
    assert 'fileinto "INBOX.Feeds"' in stored
    assert 'fileinto "INBOX.Lists"' not in stored
    assert [write[0] for write in client.read_state()["imap_writes"]] == [
        "rename_folder",
        "subscribe_folder",
        "unsubscribe_folder",
    ]

    again = client.request(
        "folder.rename.execute", {"plan_id": result["plan_id"]}
    )

    assert refusal(again)["code"] == "plan_unknown"


# ----------------------------------------------------------------------------
def test_folder_rename_with_no_filter_into_it_leaves_the_set_alone(start):
    client = initialized(start)
    result = client.request(
        "folder.rename.plan", {"old": "INBOX.spam", "new": "INBOX.junk"}
    )["result"]

    assert result["document"]["plan"]["diff"] is None

    response = client.request(
        "folder.rename.execute", {"plan_id": result["plan_id"]}
    )
    done = response["result"]["result"]

    assert done["backup"] is None
    assert done["filterset"] is None
    assert done["ok"] is True
    assert "stored" not in client.read_state()
    assert not (client.state.parent / "backups").exists()


# ----------------------------------------------------------------------------
def test_folder_rename_refuses_what_the_cli_refuses(start):
    client = initialized(start)

    inbox = client.request(
        "folder.rename.plan", {"old": "INBOX", "new": "INBOX.Old"}
    )
    missing = client.request(
        "folder.rename.plan", {"old": "INBOX.Nope", "new": "INBOX.Other"}
    )

    assert refusal(inbox)["code"] == "rename_inbox"
    assert refusal(missing)["code"] == "no_such_folder"
    assert "imap_writes" not in client.read_state()


# ############################################################################
# The two new filter set writes, held to the stale-plan refusals
# ############################################################################

# A filter set mailctl cannot parse, which only a restore may replace
# (ADR 0005).
UNPARSEABLE = "if header :contains {{{ not sieve\n"

# Each command in these groups that uploads the filter set: its method,
# what its plan is given, and the filter set it starts from. The file a
# restore reads differs from each.
STALE_WRITES = {
    "filterset restore": (
        "filterset.restore",
        {"file": "restore.sieve"},
        TWO_FILTERS,
    ),
    "filterset restore over an unparseable set": (
        "filterset.restore",
        {"file": "restore.sieve"},
        UNPARSEABLE,
    ),
    "folder rename": (
        "folder.rename",
        {"old": "INBOX.Lists", "new": "INBOX.Feeds"},
        TWO_FILTERS,
    ),
    # No filter files into INBOX.spam, so nothing is uploaded; the set is
    # still read again before the folder moves (#332).
    "folder rename with no filter to repoint": (
        "folder.rename",
        {"old": "INBOX.spam", "new": "INBOX.junk"},
        TWO_FILTERS,
    ),
}


# ----------------------------------------------------------------------------
def stale_plan(start, case: str) -> tuple[ChildClient, str, str, str]:
    method, params, before = STALE_WRITES[case]
    client = initialized(start, scripts={"managesieve": before})
    restore_file(client, MERGEABLE)
    result = client.request(f"{method}.plan", params)["result"]
    plan = result["document"]["plan"]

    assert plan["changes"] is True

    if before is UNPARSEABLE:
        assert plan["filters_before"] is None

    return client, method, result["plan_id"], before


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("case", sorted(STALE_WRITES))
def test_a_group_write_refuses_a_filter_set_changed_since_the_plan(
    start, case
):
    """Red if execute uploads over another client's edit (#257), or a
    rename moves the folder before finding the set changed."""
    client, method, plan_id, before = stale_plan(start, case)

    state = client.read_state()
    edited = before + "# rule:[made-elsewhere]\nkeep;\n"
    state["scripts"]["managesieve"] = edited
    client.write_state(state)

    error = refusal(client.request(f"{method}.execute", {"plan_id": plan_id}))
    after = client.read_state()

    assert error["code"] == "filterset_changed"
    assert after["scripts"]["managesieve"] == edited
    assert "stored" not in after
    assert "imap_writes" not in after
    assert not (client.state.parent / "backups").exists()

    retry = client.request(f"{method}.execute", {"plan_id": plan_id})

    assert refusal(retry)["code"] == "plan_unknown"


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("case", sorted(STALE_WRITES))
def test_a_group_write_refuses_an_active_set_switched_since_the_plan(
    start, case
):
    """Red if execute uploads once another client has made another filter
    set active (#308)."""
    client, method, plan_id, before = stale_plan(start, case)

    state = client.read_state()
    state["scripts"]["elsewhere"] = "keep;\n"
    state["active"] = "elsewhere"
    client.write_state(state)

    error = refusal(client.request(f"{method}.execute", {"plan_id": plan_id}))
    after = client.read_state()

    assert error["code"] == "filterset_changed"
    assert error["fields"] == {"operation": "filterset list", "arguments": []}
    assert after["active"] == "elsewhere"
    assert after["scripts"]["managesieve"] == before
    assert "stored" not in after
    assert "imap_writes" not in after
    assert not (client.state.parent / "backups").exists()


# ############################################################################
# What the provider does not declare, and parameters
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("capability", "method", "params"),
    [
        ("rule_sets", "filterset.list", {}),
        ("rule_sets", "filterset.show", {}),
        ("rule_sets", "filterset.backup", {}),
        ("rule_sets", "filterset.restore.plan", {"file": "restore.sieve"}),
        ("folder_visibility", "folder.subscribe.plan", {"folder": "INBOX"}),
        ("folder_visibility", "folder.unsubscribe.plan", {"folder": "INBOX"}),
        (
            "folder_visibility",
            "folder.create.plan",
            {"folder": "INBOX.Quiet", "subscribe": False},
        ),
        ("folder_counts", "folder.list", {"counts": True}),
        ("mark", "mail.mark.plan", {"uids": [1], "read": True}),
        (
            "uidvalidity",
            "mail.mark.plan",
            {"uids": [1], "read": True, "uidvalidity": 1},
        ),
    ],
)
def test_a_group_method_the_provider_does_not_declare_is_refused_unread(
    start, monkeypatch, capability, method, params
):
    """The CLI hides these and refuses them below the front-end; the API
    meets the same refusal, before anything connects."""
    monkeypatch.setenv("MAILCTL_TEST_DECLINE", capability)
    client = initialized(start)
    restore_file(client)
    error = refusal(client.request(method, params))

    assert (
        f"does not declare the {capability!r} capability" in error["message"]
    )
    assert client.read_state().get("logins", 0) == 0


# ----------------------------------------------------------------------------
def test_group_parameters_are_refused_as_parameters(start):
    """Red if a missing name, a wrong type, or a parameter the command
    does not take reaches the utilities, or an execute takes more than
    its plan's id."""
    client = initialized(start)
    asks = [
        ("filterset.list", {"name": "x"}),
        ("filterset.show", {"name": 1}),
        ("filterset.backup", {"dry_run": "yes"}),
        ("filterset.restore.plan", {}),
        ("folder.list", {"counts": 1}),
        ("folder.create.plan", {}),
        ("folder.rename.plan", {"old": "INBOX.Lists"}),
        ("folder.subscribe.plan", {"folder": "INBOX.spam", "yes": True}),
        ("mail.mark.plan", {"read": True}),
        ("mail.mark.plan", {"uids": ["1"], "read": True}),
        ("mail.mark.plan", {"uids": [True], "read": True}),
        ("mail.mark.plan", {"uids": [1], "read": "yes"}),
        ("mail.mark.plan", {"uids": [1], "keywords": [1]}),
        ("mail.senders", {"top": "all"}),
        ("mail.senders", {"by": 1}),
        ("server.test", {"verbose": True}),
        ("server.probe", {"report": "yes"}),
        ("server.probe", {"json": True}),
        ("server.baseline.save.plan", {"yes": True}),
        ("server.baseline.show", {"json": True}),
        ("server.baseline.check", {"host": "elsewhere"}),
        ("config.migrate.plan", {"dry_run": True}),
    ]

    for method, params in asks:
        assert client.request(method, params)["error"]["code"] == -32602, (
            method,
            params,
        )


# ----------------------------------------------------------------------------
# Each execute of these groups, and a plan for it that changes something.
GROUP_EXECUTES = {
    "folder.create": {"folder": "INBOX.New"},
    "folder.subscribe": {"folder": "INBOX.spam"},
    "folder.unsubscribe": {"folder": "INBOX.Lists"},
    "folder.rename": {"old": "INBOX.Lists", "new": "INBOX.Feeds"},
    "filterset.restore": {"file": "restore.sieve"},
    "mail.mark": {"uids": [1], "read": True},
    "server.baseline.save": {},
}


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("method", sorted(GROUP_EXECUTES))
def test_a_bad_execute_parameter_does_not_use_up_the_plan(start, method):
    """Red if an execute takes its plan before refusing a parameter it
    does not take: refused as a request, the plan must still run."""
    client = initialized(start)
    restore_file(client)
    plan_id = client.request(f"{method}.plan", GROUP_EXECUTES[method])[
        "result"
    ]["plan_id"]

    bad = client.request(f"{method}.execute", {"plan_id": plan_id, "x": 1})

    assert bad["error"]["code"] == -32602
    assert "result" in client.request(
        f"{method}.execute", {"plan_id": plan_id}
    )


# ############################################################################
# The password
# ############################################################################


# ----------------------------------------------------------------------------
def nowhere(client: ChildClient, stderr: bytes, *texts: str) -> None:
    """None of ``texts`` in any line on stdout or anywhere on stderr: as
    written, as JSON escapes it, or as Python's repr escapes it."""
    out = b"".join(client.lines)

    for text in texts:
        for form in {
            text,
            json.dumps(text)[1:-1],
            repr(text)[1:-1],
        }:
            assert form.encode("utf-8") not in out, form
            assert form.encode("utf-8") not in stderr, form


# ----------------------------------------------------------------------------
def test_a_password_given_through_the_prompt_is_used_and_never_echoed(start):
    """The sentinel logs in on both halves -- each double logs in only on
    its hash -- and appears in no line on stdout and nothing on stderr,
    through reads on both halves with the protocol chatter on, refusals,
    and ``--debug``'s tracebacks. Red under a progress line that names the
    password after either login."""
    client = start("--debug", password=SENTINEL, configured=False)
    client.answer = {"result": {"password": SENTINEL}}
    client.request("initialize", {"log": True})

    listed = client.request("filter.list")
    found = client.request("mail.search", {"filter": GITHUB_FILTER})
    viewed = client.request("mail.view", {"uid": 1})
    refused = client.request("mail.search", {"limit": 0})
    client.request("mail.search", {"filter": {"version": 99}})
    client.request("mail.view", {"uid": 2, "password": SENTINEL})
    client.request("shutdown")
    code, stderr = client.close()

    assert "result" in listed
    assert [item["uid"] for item in found["result"]["messages"]] == [1]
    assert viewed["result"]["message"]["uid"] == 1
    assert any(note["method"] == "log" for note in found["notes"])
    assert refused["error"]["code"] == 1
    assert client.prompted == 1
    assert client.read_state()["logins"] == 2
    assert code == 0
    assert stderr, "--debug wrote no traceback, so stderr proves nothing"
    nowhere(client, stderr, SENTINEL)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("method", ["filter.list", "mail.search"])
def test_a_login_the_server_refuses_never_echoes_the_password(start, method):
    """The sentinel as the wrong password, on each half: refused, coded
    ``auth_failed`` so a client can tell it from the rest, and quoted
    nowhere, ``--debug``'s tracebacks included."""
    client = start("--debug", password=PASSWORD, configured=False)
    client.answer = {"result": {"password": SENTINEL}}
    client.request("initialize", {"log": True})

    error = refusal(client.request(method))
    client.request("shutdown")
    _code, stderr = client.close()

    assert error["code"] == "auth_failed"
    nowhere(client, stderr, SENTINEL)


# ----------------------------------------------------------------------------
def test_a_password_imap_cannot_send_is_refused_without_quoting_it(start):
    """imaplib sends LOGIN as ASCII, and its encoding error names the
    character it could not send and where. ManageSieve sends UTF-8, so the
    same password logs in there. Red if the encoding error reaches any
    output, by its text or by a traceback's chained cause."""
    secret = "sentinel-p\u00e4ss-\u00f8rd-41c2"
    client = start("--debug", password=secret, configured=False)
    client.answer = {"result": {"password": secret}}
    client.request("initialize", {"log": True})

    listed = client.request("filter.list")
    error = refusal(client.request("mail.search"))
    client.request("shutdown")
    _code, stderr = client.close()

    assert "result" in listed
    assert error["code"] == "credentials_not_ascii"
    assert error["fields"] == {"which": "password"}
    nowhere(
        client,
        stderr,
        secret,
        "\u00e4",
        "\u00f8",
        "can't encode",
        "ordinal not in range",
    )


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
def test_a_refused_prompted_password_is_asked_for_again(start):
    """A server that refused the answer refuses it on every retry, so it
    is forgotten: wrong, refused and coded, asked again, right, done."""
    client = start(configured=False)
    client.answer = {"result": {"password": "not-this-one"}}
    client.request("initialize")

    assert refusal(client.request("filter.list"))["code"] == "auth_failed"
    assert client.prompted == 1

    client.answer = {"result": {"password": PASSWORD}}

    assert "result" in client.request("filter.list")
    assert client.prompted == 2
    assert "result" in client.request("mail.search")
    assert client.prompted == 2


# ----------------------------------------------------------------------------
def test_a_refused_password_from_a_file_is_not_asked_for(start):
    """Only a prompted password is forgotten: asking cannot change one the
    configuration names, so there is no prompt and the refusal stands."""
    client = start(configured="not-the-account-password")
    client.request("initialize")

    with_file = client.request("filter.list")
    again = client.request("filter.list")

    assert refusal(with_file)["code"] == "auth_failed"
    assert refusal(again)["code"] == "auth_failed"
    assert client.prompted == 0


# ----------------------------------------------------------------------------
def test_a_reply_to_the_prompt_that_does_not_parse_is_declined_at_once(start):
    """Held, it would leave both sides waiting on each other. It is
    answered as a bad line, the request is refused as declined, and the
    next request asks again."""
    client = start(configured=False)
    client.request("initialize")
    client.send({"jsonrpc": "2.0", "id": "list", "method": "filter.list"})
    prompt = client.receive()

    client.send_line(
        b'{"jsonrpc": "2.0", "id": "%s", "result": {"password": "x"\n'
        % prompt["id"].encode()
    )

    assert client.receive()["error"]["code"] == -32700
    assert refusal(client.until("list"))["code"] == "password_declined"

    client.answer = {"result": {"password": PASSWORD}}

    assert "result" in client.request("filter.list")


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


# The CLI's commands the API offers no method for, each with its reason.
# Every other command has one: a command added to the CLI later fails the
# test below until it gets a method or a line here.
NOT_IN_THE_API = {
    "help": "the CLI's help for its own command line; a client learns what "
    "it can call from initialize's capabilities and this method set",
}


# ----------------------------------------------------------------------------
def test_every_command_method_is_a_command_of_the_cli():
    """A method's name is its CLI command path, dotted, so the API and the
    help name an operation alike (ADR 0008); a write command is a plan and
    an execute. Every command has a method unless ``NOT_IN_THE_API`` says
    why not; red for a command with neither, or one with both."""
    commands = set(command_parsers())
    named = set(server.METHODS) - server.PROTOCOL_METHODS
    paths = {
        name.removesuffix(".plan").removesuffix(".execute").replace(".", " ")
        for name in named
    }

    assert named
    assert paths <= commands
    assert commands - paths == set(NOT_IN_THE_API)
    assert not paths & set(NOT_IN_THE_API)

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


# ############################################################################
# Parameters the API refuses as parameters
# ############################################################################


# ----------------------------------------------------------------------------
def test_malformed_parameters_are_refused_as_parameters(start):
    """Each was an internal error once: an action the CLI refuses by name
    given as a list, and a filter document holding a password, which the
    parser had wrapped."""
    client = initialized(start)

    as_list = add_plan(client, actions={"redirect": [1]})["error"]
    with_password = client.request(
        "mail.search", {"filter": {**GITHUB_FILTER, "password": SENTINEL}}
    )["error"]

    assert as_list == {
        "code": -32602,
        "message": "'redirect' must be a string",
    }
    assert with_password["code"] == -32602
    assert SENTINEL not in with_password["message"]


# ----------------------------------------------------------------------------
def test_every_refusal_of_a_parameter_value_carries_a_code(start):
    client = initialized(start)

    unsubscribed = add_plan(client, subscribe=False)
    unsorted = client.request("mail.search", {"sort": {"key": "colour"}})

    assert refusal(unsubscribed)["code"] == "subscribe_needs_create"
    assert refusal(unsorted)["code"] == "unknown_sort_key"


# ############################################################################
# The mail group's last two
# ############################################################################


# ----------------------------------------------------------------------------
def test_mail_mark_plans_then_marks_once_on_the_mail_half_only(start):
    """Red if the plan writes, if execute sends more than the one write
    the plan shows, if the rules half is opened, which ``mail mark``
    never does, or if the id runs twice."""
    client = initialized(start)
    planned = client.request(
        "mail.mark.plan", {"uids": [1, 2], "read": True, "flagged": False}
    )["result"]

    assert planned["document"]["plan"] == {
        "command": "mail mark",
        "changes": True,
        "folder": "INBOX",
        "uidvalidity": planned["document"]["plan"]["uidvalidity"],
        "set": ["\\Seen"],
        "clear": ["\\Flagged"],
        "messages": [
            {"uid": 1, "flags": [], "set": ["\\Seen"], "clear": []},
            {"uid": 2, "flags": [], "set": ["\\Seen"], "clear": []},
        ],
    }
    assert "imap_writes" not in client.read_state()

    done = client.request(
        "mail.mark.execute", {"plan_id": planned["plan_id"]}
    )["result"]["result"]
    again = client.request(
        "mail.mark.execute", {"plan_id": planned["plan_id"]}
    )

    assert done == {
        "command": "mail mark",
        "folder": "INBOX",
        "added": 2,
        "removed": 0,
    }
    # The double records what IMAPClient is handed: the flag as bytes.
    assert client.read_state()["imap_writes"] == [
        ["add_flags", [1, 2], [repr(b"\\Seen")]]
    ]
    assert refusal(again)["code"] == "plan_unknown"
    assert "sieve_connects" not in client.read_state()


# ----------------------------------------------------------------------------
def test_mail_mark_refuses_a_folder_renumbered_since_the_plan(start):
    """#204 at execute, through the API: red if the UIDs are marked after
    the folder's UIDVALIDITY changed, whatever the plan read."""
    client = initialized(start)
    plan_id = client.request("mail.mark.plan", {"uids": [1], "read": True})[
        "result"
    ]["plan_id"]

    state = client.read_state()
    state["uidvalidity"] = {"INBOX": 7}
    client.write_state(state)

    # As in the filter.apply case: a folder is renumbered only while this
    # connection holds another selected.
    assert "result" in client.request("mail.search", {"folder": "INBOX.Lists"})

    error = refusal(client.request("mail.mark.execute", {"plan_id": plan_id}))

    assert error["code"] == "uidvalidity_changed"
    assert "imap_writes" not in client.read_state()


# ----------------------------------------------------------------------------
def test_mail_mark_refuses_a_pin_the_folder_no_longer_has(start):
    client = initialized(start)
    error = refusal(
        client.request(
            "mail.mark.plan", {"uids": [1], "read": True, "uidvalidity": 9}
        )
    )

    assert error["code"] == "uidvalidity_changed"
    assert "imap_writes" not in client.read_state()


# ----------------------------------------------------------------------------
def test_mail_mark_that_changes_nothing_is_planned_and_not_carried_out(start):
    client = initialized(start)
    planned = client.request("mail.mark.plan", {"uids": [1], "read": False})[
        "result"
    ]

    assert planned["document"]["plan"]["changes"] is False

    error = refusal(
        client.request("mail.mark.execute", {"plan_id": planned["plan_id"]})
    )

    assert error["code"] == "nothing_to_change"
    assert "imap_writes" not in client.read_state()


# ----------------------------------------------------------------------------
def test_mail_mark_refuses_what_the_cli_refuses(start):
    """Red if a mark with nothing to set reaches the server, if a UID the
    folder does not hold marks the rest, or if a keyword that is a system
    flag or two words is taken."""
    client = initialized(start)
    asks = [
        ({"uids": [1]}, "nothing to mark"),
        ({"uids": [1, 99], "read": True}, "no message with uid 99"),
        ({"uids": [1], "keywords": ["\\Deleted"]}, "is a system flag"),
        ({"uids": [1], "keywords": ["two words"]}, "cannot be a keyword"),
        (
            {"uids": [1], "keywords": ["$X"], "no_keywords": ["$X"]},
            "cannot both set and clear",
        ),
        ({"uids": [], "read": True}, "at least one message UID"),
        ({"uids": [0], "read": True}, "start at 1"),
    ]

    for params, words in asks:
        assert (
            words
            in refusal(client.request("mail.mark.plan", params))["message"]
        ), params

    assert refusal(client.request("mail.mark.plan", {"uids": [1]}))[
        "code"
    ] == ("no_marks")
    assert "imap_writes" not in client.read_state()


# ----------------------------------------------------------------------------
def test_mail_senders_counts_the_mail_by_sender_on_the_mail_half_only(start):
    """Red if a sender is miscounted or its unread lost, if a filter does
    not narrow what is counted, if ``top`` keeps other than that many
    rows, or if the rules half is opened."""
    client = initialized(start)
    counted = client.request("mail.senders", {"top": None})["result"]
    busiest = client.request("mail.senders", {"top": 1})["result"]
    domains = client.request("mail.senders", {"by": "domain"})["result"]
    narrowed = client.request("mail.senders", {"filter": GITHUB_FILTER})[
        "result"
    ]

    assert {
        key: counted[key] for key in ("folder", "by", "messages", "unread")
    } == {"folder": "INBOX", "by": "address", "messages": 2, "unread": 2}
    assert counted["groups"] == len(counted["senders"]) == 2
    assert {row["key"] for row in counted["senders"]} == {
        "noreply@github.com",
        "boss@example.com",
    }
    assert all(
        row["unread"] == row["total"] == 1 for row in counted["senders"]
    )
    assert [row["key"] for row in domains["senders"]] == [
        "example.com",
        "github.com",
    ]
    assert len(busiest["senders"]) == 1
    assert busiest["groups"] == 2
    assert [row["key"] for row in narrowed["senders"]] == [
        "noreply@github.com"
    ]
    assert "sieve_connects" not in client.read_state()


# ----------------------------------------------------------------------------
def test_mail_senders_refuses_what_the_cli_refuses(start):
    """Red if a count over its ceiling reads any header, if a top of 0 is
    taken (the CLI's 0 for every row is the API's null), or an unknown
    grouping."""
    client = initialized(start)

    over = refusal(client.request("mail.senders", {"max_messages": 1}))
    zero = refusal(client.request("mail.senders", {"top": 0}))
    grouping = refusal(client.request("mail.senders", {"by": "colour"}))

    assert "1" in over["message"]
    assert zero["code"] == "at_least_one"
    assert zero["fields"] == {"setting": "top", "value": 0}
    assert "cannot group senders by 'colour'" in grouping["message"]


# ############################################################################
# The server group
# ############################################################################


# ----------------------------------------------------------------------------
def test_server_test_reports_both_halves_and_where_each_setting_came_from(
    start,
):
    """Red if a half is not opened, as ``server test`` opens both, if the
    report says other than what each double holds, or if a setting's
    source is lost."""
    client = initialized(
        start, scripts={"managesieve": ROUNDCUBE, "other": "keep;\n"}
    )
    report = client.request("server.test")["result"]["test"]

    assert report["provider"] == "mxroute"
    assert [item["name"] for item in report["settings"]] == [
        "provider",
        "host",
        "user",
        "source_folder",
    ]
    assert report["settings"][2] == {
        "name": "user",
        "value": USER,
        "source": {
            "kind": "environment",
            "name": "MAILCTL_USER",
            "path": None,
        },
    }
    assert report["password"] == {
        "state": "set",
        "source": {
            "kind": "environment",
            "name": "MAILCTL_PASSWORD_FILE",
            "path": None,
        },
    }
    assert report["rules"]["active"] == "managesieve"
    assert report["rules"]["others"] == ["other"]
    assert {item["name"] for item in report["rules"]["extensions"]} >= {
        "fileinto",
        "imap4flags",
    }
    assert report["mail"]["delimiter"] == "."
    assert report["mail"]["folders"] == 3
    assert report["mail"]["unsubscribed"] == ["INBOX.spam"]
    # Neither double names its software as a server module knows it.
    assert report["unrecognised"] == ["filter", "mail"]
    assert report["baseline"] is None
    assert report["notes"]

    state = client.read_state()

    assert state["sieve_connects"] >= 1
    assert state["imap_connects"] >= 1


# ----------------------------------------------------------------------------
def test_server_test_reports_a_baseline_it_cannot_compare_and_goes_on(
    start,
):
    """The CLI's test neither fails nor stops over a baseline; red if the
    API's refuses, or says nothing of why it could not compare."""
    client = initialized(start)
    path = baseline_file()
    path.parent.mkdir(parents=True)
    path.write_text("not json\n", encoding="utf-8")

    report = client.request("server.test")["result"]["test"]

    assert report["baseline"]["checked"] is False
    assert "not JSON" in report["baseline"]["error"]["message"]


# ----------------------------------------------------------------------------
def test_server_probe_returns_the_probe_document_from_both_halves(start):
    """The document ``server probe --json`` prints. Red if a half is
    missing from it or left unopened."""
    client = initialized(start)
    probe = client.request("server.probe")["result"]

    assert probe["version"] == reports.PROBE_VERSION
    assert probe["provider"] == "mxroute"
    assert probe["rules"]["active_rule_set"] == "managesieve"
    assert probe["mail"]["delimiter"] == "."
    assert probe["mail"]["capabilities"]

    state = client.read_state()

    assert state["sieve_connects"] >= 1
    assert state["imap_connects"] >= 1


# ----------------------------------------------------------------------------
def test_server_probe_report_is_redacted_for_filing(start):
    """Neither double names software a server module knows, so mailctl
    recognises neither. Red if the report is missing, or if the address,
    its parts, the host, a folder, or a filter set's name reaches it."""
    client = initialized(start)
    report = client.request("server.probe", {"report": True})["result"][
        "report"
    ]

    assert report["unrecognised"] == ["ManageSieve", "IMAP"]
    assert report["title"].startswith("Unrecognised server:")

    for secret in (USER, "example.com", "mail.example.com", "Lists"):
        assert secret not in report["body"], secret

    assert "managesieve" not in report["body"].replace("ManageSieve", "")


# ----------------------------------------------------------------------------
def baseline_file() -> Path:
    """Where the baseline for the test's host is written."""
    home = Path(os.environ["XDG_CONFIG_HOME"])

    return home / "mailctl" / "baselines" / "mail.example.com.json"


# ----------------------------------------------------------------------------
def test_server_baseline_save_plans_then_writes_the_file_once(start):
    """Red if the plan writes the file, if execute writes other than what
    was planned or readably by anyone else, if a half is left unopened,
    or if the id runs twice. The second plan shows the first save."""
    client = initialized(start)
    path = baseline_file()
    planned = client.request("server.baseline.save.plan")["result"]
    plan = planned["document"]["plan"]

    assert plan["command"] == "server baseline save"
    assert plan["path"] == str(path)
    assert plan["previous_taken"] is None
    assert plan["diff"] is None
    assert not path.exists()

    done = client.request(
        "server.baseline.save.execute", {"plan_id": planned["plan_id"]}
    )["result"]["result"]
    again = client.request(
        "server.baseline.save.execute", {"plan_id": planned["plan_id"]}
    )

    assert done == {
        "command": "server baseline save",
        "host": "mail.example.com",
        "path": str(path),
        "taken": plan["taken"],
    }
    assert path.stat().st_mode & 0o777 == 0o600
    assert refusal(again)["code"] == "plan_unknown"

    state = client.read_state()

    assert state["sieve_connects"] >= 1
    assert state["imap_connects"] >= 1

    second = client.request("server.baseline.save.plan")["result"]["document"]

    assert second["plan"]["previous_taken"] == plan["taken"]
    assert second["plan"]["drift"] == []
    assert isinstance(second["plan"]["diff"], str)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("saved_first", [False, True])
def test_server_baseline_save_refuses_a_file_changed_since_the_plan(
    start, saved_first
):
    """A plan can wait; a save made meanwhile, by another client or for
    another account, must not be overwritten by one that never saw it.
    Red if execute writes, whether the file was new or not."""
    client = initialized(start)

    if saved_first:
        first = client.request("server.baseline.save.plan")["result"]
        client.request(
            "server.baseline.save.execute", {"plan_id": first["plan_id"]}
        )

    plan_id = client.request("server.baseline.save.plan")["result"]["plan_id"]
    path = baseline_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("someone else's save\n", encoding="utf-8")

    error = refusal(
        client.request("server.baseline.save.execute", {"plan_id": plan_id})
    )

    assert error["code"] == "baseline_changed"
    assert error["fields"] == {"operation": "server baseline save"}
    assert path.read_text(encoding="utf-8") == "someone else's save\n"


# ----------------------------------------------------------------------------
def test_server_baseline_show_and_check_read_the_saved_baseline(start):
    """Red if ``show`` returns other than the file as stored or opens a
    connection, which it never does, or if ``check`` finds drift in an
    unchanged account."""
    client = initialized(start)
    plan_id = client.request("server.baseline.save.plan")["result"]["plan_id"]
    client.request("server.baseline.save.execute", {"plan_id": plan_id})

    # A second process on a fresh state file, so its logins are its own.
    other = initialized(start)
    shown = other.request("server.baseline.show")["result"]

    assert shown == json.loads(baseline_file().read_text(encoding="utf-8"))
    assert USER in shown["accounts"]
    assert other.read_state().get("logins", 0) == 0

    checked = other.request("server.baseline.check")["result"]

    assert checked["version"] == json_output.DRIFT_VERSION
    assert checked["host"] == "mail.example.com"
    assert checked["drift"] == []
    assert checked["serious"] == checked["informational"] == 0


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "method", ["server.baseline.show", "server.baseline.check"]
)
def test_server_baseline_read_with_none_saved_is_refused_with_its_code(
    start, method
):
    client = initialized(start)
    error = refusal(client.request(method))

    assert error["code"] == "no_baseline"
    assert error["fields"] == {"operation": "server baseline save"}

    if method == "server.baseline.show":
        assert client.read_state().get("logins", 0) == 0


# ----------------------------------------------------------------------------
def test_the_server_group_never_sends_the_password(start):
    """Each describes the servers and the account; red if the password,
    given through the prompt, reaches any of their documents, events,
    or ``--debug``'s output."""
    client = start("--debug", password=SENTINEL, configured=False)
    client.answer = {"result": {"password": SENTINEL}}
    client.request("initialize", {"log": True})

    for method, params in [
        ("server.test", None),
        ("server.probe", None),
        ("server.probe", {"report": True}),
        ("server.baseline.check", None),
    ]:
        client.request(method, params)

    plan_id = client.request("server.baseline.save.plan")["result"]["plan_id"]
    client.request("server.baseline.save.execute", {"plan_id": plan_id})

    for method in ("server.baseline.show", "server.baseline.check"):
        assert "result" in client.request(method), method

    client.request("shutdown")
    code, stderr = client.close()

    assert code == 0
    assert client.prompted == 1
    assert stderr, "--debug wrote no traceback, so stderr proves nothing"
    assert SENTINEL not in baseline_file().read_text(encoding="utf-8")
    nowhere(client, stderr, SENTINEL)


# ############################################################################
# The config group
# ############################################################################


# ----------------------------------------------------------------------------
def old_config_dir() -> tuple[Path, Path]:
    """The old directory, holding a config file and a backup, and the new
    one it moves to."""
    home = Path(os.environ["XDG_CONFIG_HOME"])
    old = home / "mxfilter"
    (old / "backups").mkdir(parents=True)
    (old / "config.toml").write_text(
        f'backup_dir = "{old / "backups"}"\n'
        f'password_cmd = "echo {SENTINEL}"\n',
        encoding="utf-8",
    )
    (old / "backups" / "one.sieve").write_text("keep;\n", encoding="utf-8")
    (old / "backups" / "one.sieve").chmod(0o600)

    return old, home / "mailctl"


# ----------------------------------------------------------------------------
def test_config_migrate_plans_then_moves_the_old_directory_once(start):
    """Red if the plan moves anything, if execute loses a file's mode or
    leaves the old directory, if a step is not sent as it lands, if a
    connection is opened, which ``config migrate`` never needs, if the id
    runs twice, or if the result does not say that what moved is read by
    a process started after it. The old config.toml's password command
    holds the sentinel, and no plan, event, or result may carry it."""
    old, new = old_config_dir()
    client = initialized(start, "--debug")
    planned = client.request("config.migrate.plan")["result"]
    plan = planned["document"]["plan"]

    assert plan["changes"] is True
    assert plan["old"] == str(old)
    assert plan["new"] == str(new)
    assert {entry["path"] for entry in plan["entries"]} == {
        "backups",
        "backups/one.sieve",
        "config.toml",
    }
    assert plan["references"] == [
        {
            "key": "backup_dir",
            "old": str(old / "backups"),
            "new": str(new / "backups"),
        }
    ]
    assert old.is_dir()

    response = client.request(
        "config.migrate.execute", {"plan_id": planned["plan_id"]}
    )
    again = client.request(
        "config.migrate.execute", {"plan_id": planned["plan_id"]}
    )
    kinds = [event["kind"] for event in events(response)]

    assert response["result"]["result"] == {
        "command": "config migrate",
        "old": str(old),
        "new": str(new),
        "restart": True,
    }
    assert kinds.count("file_moved") == 2
    assert kinds[-2:] == ["reference_rewritten", "old_dir_removed"]
    assert not old.exists()
    assert (new / "backups" / "one.sieve").stat().st_mode & 0o777 == 0o600
    assert str(new / "backups") in (new / "config.toml").read_text(
        encoding="utf-8"
    )
    assert refusal(again)["code"] == "plan_unknown"
    assert client.read_state().get("logins", 0) == 0

    assert_sentinel_absent(client)


# ----------------------------------------------------------------------------
def test_config_migrate_with_no_old_directory_is_not_carried_out(start):
    client = initialized(start)
    planned = client.request("config.migrate.plan")["result"]

    assert planned["document"]["plan"]["changes"] is False
    assert planned["document"]["plan"]["old"] is None

    error = refusal(
        client.request(
            "config.migrate.execute", {"plan_id": planned["plan_id"]}
        )
    )

    assert error["code"] == "nothing_to_change"


# ----------------------------------------------------------------------------
def test_config_migrate_refuses_to_overwrite_what_is_at_the_destination(
    start,
):
    """Red if a plan that would overwrite a file is offered at all: the
    CLI refuses it before asking, and so does the API, naming each."""
    old, new = old_config_dir()
    (new / "backups").mkdir(parents=True)
    (new / "backups" / "one.sieve").write_text("mine\n", encoding="utf-8")

    client = initialized(start)
    error = refusal(client.request("config.migrate.plan"))

    assert error["code"] == "migration_conflict"
    assert error["fields"] == {
        "conflicts": [str(new / "backups" / "one.sieve")]
    }
    assert (old / "backups" / "one.sieve").exists()
    assert (new / "backups" / "one.sieve").read_text(encoding="utf-8") == (
        "mine\n"
    )


# ----------------------------------------------------------------------------
def tree(root: Path) -> list[str]:
    """Every path under ``root``, relative to it, sorted."""
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


# ----------------------------------------------------------------------------
def test_config_migrate_refuses_a_plan_a_new_file_now_clashes_with(start):
    """Red if execute starts moving before it finds that a destination
    appeared after the plan: it would make the new directory's
    subdirectories, move the files sorted before the clash, refuse
    uncoded at it, and leave the old directory half-moved."""
    home = Path(os.environ["XDG_CONFIG_HOME"])
    old, new = home / "mxfilter", home / "mailctl"
    (old / "backups").mkdir(parents=True)

    for name in ("a.txt", "backups/one.sieve", "config.toml", "z.txt"):
        (old / name).write_text(f"{name}\n", encoding="utf-8")

    before = tree(old)
    client = initialized(start)
    planned = client.request("config.migrate.plan")["result"]

    new.mkdir()
    (new / "z.txt").write_text("mine\n", encoding="utf-8")

    response = client.request(
        "config.migrate.execute", {"plan_id": planned["plan_id"]}
    )
    error = refusal(response)

    assert error["code"] == "migration_changed"
    assert error["fields"] == {"operation": "config migrate"}
    assert events(response) == []
    assert tree(old) == before
    assert tree(new) == ["z.txt"]
    assert (new / "z.txt").read_text(encoding="utf-8") == "mine\n"


# ----------------------------------------------------------------------------
def test_config_migrate_refuses_a_plan_a_baseline_saved_since_clashes_with(
    start,
):
    """Red if a baseline saved in the same session, between the plan and
    its execute, is met only by the move: it is refused uncoded, and the
    baseline has made the new directory, so the old one's config.toml is
    stranded with nothing left to report it. Refused coded, before
    anything moves, it can be planned again and the clash shown."""
    home = Path(os.environ["XDG_CONFIG_HOME"])
    old = home / "mxfilter"
    (old / "baselines").mkdir(parents=True)
    (old / "baselines" / "mail.example.com.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (old / "config.toml").write_text('host = "x"\n', encoding="utf-8")

    before = tree(old)
    client = initialized(start)
    planned = client.request("config.migrate.plan")["result"]
    save = client.request("server.baseline.save.plan")["result"]
    client.request(
        "server.baseline.save.execute", {"plan_id": save["plan_id"]}
    )

    response = client.request(
        "config.migrate.execute", {"plan_id": planned["plan_id"]}
    )
    error = refusal(response)

    assert error["code"] == "migration_changed"
    assert events(response) == []
    assert tree(old) == before
    assert tree(home / "mailctl") == [
        "baselines",
        "baselines/mail.example.com.json",
    ]


# ----------------------------------------------------------------------------
def test_config_migrate_never_expands_a_variable_into_a_config_path(
    start, monkeypatch
):
    """Red if a config.toml path that reaches the old directory through a
    variable is rewritten by expanding it: the variable's value -- here
    the sentinel -- would be sent as the reference's new value and
    written into the file. It is refused, naming only the key."""
    old, _new = old_config_dir()
    monkeypatch.setenv("MAILCTL_TEST_PW", f"{old}/{SENTINEL}")
    toml = old / "config.toml"
    toml.write_text('password_file = "$MAILCTL_TEST_PW"\n', encoding="utf-8")

    client = initialized(start, "--debug")
    error = refusal(client.request("config.migrate.plan"))

    assert error["code"] == "migration_reference_unrewritable"
    assert error["fields"] == {
        "key": "password_file",
        "operation": "config migrate",
    }
    assert toml.read_text(encoding="utf-8") == (
        'password_file = "$MAILCTL_TEST_PW"\n'
    )

    assert_sentinel_absent(client)
