"""The TUI against the real API, through mailctl's own ``api_child.py``.

That child is the real ``mailctl-api`` with the two protocol clients
replaced by doubles holding their state in a file. So this is the stub's
contract checked against the thing it stands in for: the real
``initialize``, the real ``filter.list`` document, and the real
``credentials.prompt``. The TUI still imports nothing from mailctl; only
the child process does.
"""

import json
import os
import sys

from conftest import API_CHILD, SIZE, digest, settle
from mailctl_tui.app import MailctlApp, PasswordScreen
from mailctl_tui.offer import COMMANDS
from test_app import cells

PASSWORD_KEYS = list("real-api-Pa55-41d0")

ROUNDCUBE = """require ["fileinto","imap4flags"];
# rule:[keep-boss]
if header :contains "from" "boss@example.com"
{
\tfileinto "INBOX.Boss";
\tstop;
}
"""


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
                "messages": {},
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
        await settle(pilot, lambda: isinstance(app.screen, PasswordScreen))
        await pilot.press(*PASSWORD_KEYS)
        await pilot.press("enter")
        await settle(pilot, lambda: len(cells(app)) == 1)

        assert cells(app) == [
            ["1", "keep-boss", "From contains 'boss@example.com'", "stop"]
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

        assert required <= set(app.capabilities)
        assert {"ordering", "mark", "rule_sets"} <= set(app.capabilities)
