"""The API against a real server (#258).

The API process is started as a client starts it, ``python -m mailctl.api``
with the account's settings in its environment, and driven over its stdin
and stdout. The password is left out of those settings, so the process
has to ask for it with ``credentials.prompt``.

The oracle is not mailctl: the script is read back off the container's
disk, the folders through IMAPClient, and the rule's effect by handing new
mail to ``dovecot-lda``.
"""

import os
import sys
from pathlib import Path

import pytest
from api_support import Client

pytestmark = pytest.mark.container

ROUNDCUBE = """require ["fileinto","imap4flags"];
# rule:[keep-boss]
if header :contains "from" "boss@example.com"
{
\tfileinto "INBOX.Boss";
\tstop;
}
"""

GITHUB_MAIL = (
    b"From: GitHub <noreply@github.com>\r\n"
    b"To: someone@example.com\r\n"
    b"Subject: Build passed\r\n"
    b"\r\n"
    b"All green.\r\n"
)

GITHUB_FILTER = {
    "version": 1,
    "criteria": {
        "match": "all",
        "compare": "contains",
        "terms": [{"header": "From", "value": "noreply@github.com"}],
    },
}


# ----------------------------------------------------------------------------
@pytest.fixture
def api(account, tmp_path):
    """The API process on this account, asking for its password."""
    env = {**os.environ}
    del env["MAILCTL_PASSWORD_FILE"]

    client = Client([sys.executable, "-m", "mailctl.api"], env, tmp_path)
    client.answer = {"result": {"password": account._password()}}

    yield client

    if client.process.poll() is None:
        client.process.kill()
        client.process.wait()


# ----------------------------------------------------------------------------
def test_a_filter_added_through_the_api_backs_up_first_and_files_mail(
    account, api
):
    """Red if the upload is announced before the backup, if the backup is
    not the script the server held, if the Roundcube rule is lost from the
    merge, or if the folder the rule files into is not made."""
    account.seed_script("managesieve", ROUNDCUBE)

    assert "result" in api.request("initialize")

    plan = api.request(
        "filter.add.plan",
        {
            "filter": GITHUB_FILTER,
            "actions": {"move_to": "Lists/GitHub"},
            "name": "github",
            "create_folder": True,
        },
    )["result"]

    assert plan["document"]["plan"]["folder"]["status"] == (
        "create-and-on-delivery"
    )

    done = api.request("filter.add.execute", {"plan_id": plan["plan_id"]})
    kinds = [
        note["params"]["document"]["event"]["kind"]
        for note in done["notes"]
        if note["method"] == "event"
    ]

    assert kinds == [
        "filterset_backed_up",
        "folder_created",
        "filterset_uploaded",
    ]

    backup = done["result"]["result"]["backup"]

    assert Path(backup).read_bytes() == ROUNDCUBE.encode()

    stored = account.script_bytes("managesieve").decode()

    assert "# rule:[keep-boss]" in stored
    assert "# rule:[github]" in stored
    assert account.active_script() == "managesieve"

    listed = api.request("filter.list")["result"]

    assert [item["name"] for item in listed["filters"]] == [
        "keep-boss",
        "github",
    ]

    account.deliver(GITHUB_MAIL, "noreply@github.com")

    with account.imap() as client:
        client.select_folder("Lists.GitHub", readonly=True)

        assert len(client.search("ALL")) == 1

    found = api.request(
        "mail.search", {"folder": "Lists/GitHub", "filter": GITHUB_FILTER}
    )["result"]

    assert [item["subject"] for item in found["messages"]] == ["Build passed"]
    assert api.request("shutdown")["result"] is None

    code, stderr = api.close()

    assert code == 0
    assert api.prompted == 1

    password = account._password().encode()

    assert password not in b"".join(api.lines)
    assert password not in stderr
