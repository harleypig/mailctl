"""The TUI against the real API, through mailctl's own ``api_child.py``.

That child is the real ``mailctl-api`` with the two protocol clients
replaced by doubles holding their state in a file. So this is the stub's
contract checked against the thing it stands in for: the real
``initialize``, the real ``filter.list`` document, and the real
``credentials.prompt``. The TUI still imports nothing from mailctl; only
the child process does.
"""

import gc
import json
import os
import sys

from conftest import API_CHILD, SIZE, digest, reach, settle
from mailctl_tui.app import MailctlApp, PasswordScreen
from mailctl_tui.offer import COMMANDS
from mailctl_tui.screens import AddScreen, MessageScreen, PlanScreen
from test_app import _holds, cells
from test_flow import rows, search, shown, steps
from textual.widgets import Input

PASSWORD_KEYS = list("real-api-Pa55-41d0")

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


# ----------------------------------------------------------------------------
def real_api(tmp_path) -> MailctlApp:
    """The TUI over a fresh account with no password configured, so the
    API must ask for it."""
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "password_sha256": digest("".join(PASSWORD_KEYS)),
                "scripts": {"managesieve": ROUNDCUBE},
                "active": "managesieve",
                "messages": {"1": GITHUB_MAIL},
            }
        ),
        encoding="utf-8",
    )

    # Nothing of the developer's own mailctl settings may reach the child.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("MAILCTL_")
    }
    env |= {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "MAILCTL_TEST_STATE": str(state),
        "MAILCTL_HOST": "mail.example.com",
        "MAILCTL_USER": "user@example.com",
        "MAILCTL_BACKUP_DIR": str(tmp_path / "backups"),
    }

    return MailctlApp([sys.executable, str(API_CHILD)], env=env)


# ----------------------------------------------------------------------------
async def test_the_filters_come_from_the_real_api(tmp_path):
    app = real_api(tmp_path)

    async with app.run_test(size=SIZE) as pilot:
        await reach(pilot, app, PasswordScreen)
        await pilot.press(*PASSWORD_KEYS)
        await pilot.press("enter")
        await settle(pilot, lambda: len(cells(app)) == 1)

        assert cells(app) == [
            [
                "1",
                "keep-boss",
                "From contains 'boss@example.com'",
                "files the message into a folder; stops",
                "stop",
            ]
        ]

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))

    assert state["logins"] == 1


# ----------------------------------------------------------------------------
async def test_every_gated_command_names_a_real_capability(tmp_path):
    """A ``requires`` naming no capability would hide its command under
    every provider, silently. Break: give a command ``requires="marks"``
    and this goes red."""
    app = real_api(tmp_path)

    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot, lambda: bool(app.capabilities))

        required = {c.requires for c in COMMANDS if c.requires is not None}
        actions = {c.needs_action for c in COMMANDS if c.needs_action}

        assert required <= set(app.capabilities)
        assert actions <= set(app.capabilities["actions"])
        assert {"ordering", "mark", "rule_sets"} <= set(app.capabilities)


# ----------------------------------------------------------------------------
async def test_the_first_scope_against_the_real_api(tmp_path):
    """Search, read, and add a filter through plan and execute: the real
    documents, the real events, a backup on disk, and the filter stored
    beside the one the webmail wrote. The password is asked once, and
    afterwards no container the app holds has it.

    The form is filled in by the API's ``like``, never from the
    message's headers here.
    """
    app = real_api(tmp_path)
    state_file = tmp_path / "state.json"

    async with app.run_test(size=SIZE) as pilot:
        await reach(pilot, app, PasswordScreen)
        await pilot.press(*PASSWORD_KEYS)
        await pilot.press("enter")
        await search(pilot, app, folder="INBOX")

        # The double gives every message one arrival time of its own.
        uid, _received, sender, subject, _state = rows(app)[0]

        assert (uid, sender, subject) == (
            "1",
            "GitHub <noreply@github.com>",
            "Build passed",
        )

        await pilot.press("enter")
        await reach(pilot, app, MessageScreen)

        assert "All green." in shown(app, "#body")

        await pilot.press("a")
        await reach(pilot, app, AddScreen)

        # The message has no List-Id, so the API matches on its sender.
        assert shown(app, ".note") == (
            "Filled in from the message, as mailctl reads it."
        )
        assert app.screen.query_one("#term-from", Input).value == (
            "noreply@github.com"
        )

        app.screen.query_one("#move_to", Input).value = "Lists"
        await pilot.press("ctrl+s")
        await reach(pilot, app, PlanScreen)

        assert "  then:  move it to 'Lists'" in shown(app, "#summary")
        assert '+if anyof (header :contains "From" "noreply@github.com")' in (
            shown(app, "#diff")
        )

        await pilot.press("y")
        await settle(pilot, lambda: "Saved." in shown(app, "#plan-status"))

        backups = list((tmp_path / "backups").rglob("*"))
        backup = [path for path in backups if path.is_file()]

        assert len(backup) == 1
        assert backup[0].read_text(encoding="utf-8") == ROUNDCUBE
        assert steps(app)[0] == (
            f"Backed up the current filter set to {backup[0]}"
        )
        assert f"backed up at {backup[0]}" in steps(app)[-2]

        await pilot.press("enter")
        await settle(pilot, lambda: len(cells(app)) == 2)

        gc.collect()
        whole = "".join(PASSWORD_KEYS)
        holders = [
            type(obj).__name__
            for obj in gc.get_objects()
            if isinstance(obj, dict | list | tuple | set)
            and obj is not PASSWORD_KEYS
            and _holds(obj, whole)
        ]
        del whole

        assert holders == []

    state = json.loads(state_file.read_text(encoding="utf-8"))
    stored = state["scripts"]["managesieve"]

    assert "# rule:[keep-boss]" in stored
    assert '"noreply@github.com"' in stored
    assert state["logins"] == 2
