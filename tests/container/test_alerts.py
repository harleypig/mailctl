"""An IMAP ALERT from a real Dovecot reaches the user (#205).

The image's post-login script sends ``* OK [ALERT] ...`` to a user whose
name starts with ``alert-`` as the login completes, and nothing to anyone
else; the same account under that name is this test's.
"""

import json

import pytest

from mailctl import json_output

pytestmark = pytest.mark.container

SHOWN = (
    "mailctl: alert from the mail server: Maintenance tonight at 22:00 UTC\n"
)


# ----------------------------------------------------------------------------
@pytest.fixture
def alerted(account, monkeypatch):
    """The account, logged in under the name the server alerts."""
    monkeypatch.setenv("MAILCTL_USER", f"alert-{account.user}")

    return account


# ----------------------------------------------------------------------------
def test_the_alert_is_shown_on_stderr(alerted):
    """Red if the alert Dovecot sends after LOGIN is not read, or is shown
    only under --verbose, or is shown anywhere but stderr."""
    result = alerted.run("folder", "list")

    assert result.code == 0, result.err
    assert result.err == SHOWN
    assert "Maintenance" not in result.out


# ----------------------------------------------------------------------------
def test_json_keeps_stdout_the_document_alone(alerted):
    result = alerted.run("folder", "list", "--json")

    assert result.code == 0, result.err
    assert json.loads(result.out)["version"] == json_output.JSON_VERSION
    assert result.err == SHOWN


# ----------------------------------------------------------------------------
def test_a_user_the_server_does_not_alert_sees_nothing(account):
    """The control: without it, an alert shown to every user would pass
    the tests above while every other test in this tier read it."""
    result = account.run("folder", "list")

    assert result.code == 0, result.err
    assert result.err == ""
