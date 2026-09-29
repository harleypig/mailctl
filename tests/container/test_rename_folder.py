"""``mailctl rename-folder`` against a real Dovecot + Pigeonhole (#5).

The issue's by-hand rename checked LIST and STATUS, both passed, and the
folder had still vanished from webmail: RENAME had left it unsubscribed.
So everything here is read back with the libraries' own clients -- LIST,
LSUB, STATUS, and the script file Pigeonhole wrote -- never with mailctl.
"""

import uuid

import pytest

pytestmark = pytest.mark.container

# Two rules filing into the folder and one under it, and one rule and a
# hand comment that the rename must leave byte for byte.
SCRIPT = """require ["fileinto"];
# rule:[lists]
if header :contains "subject" "list"
{
\tfileinto "Lists.Old";
\tstop;
}
# written by hand, keep me
# rule:[keep]
if header :contains "subject" "keep"
{
    fileinto   "Keep" ;
}
# rule:[kid]
if header :contains "subject" "kid"
{
\tfileinto "Lists.Old.Kid";
}
"""


# ----------------------------------------------------------------------------
def message(subject: str) -> bytes:
    return (
        f"From: someone@example.test\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: <{uuid.uuid4().hex}@example.test>\r\n"
        f"\r\n"
        f"Body.\r\n"
    ).encode()


# ----------------------------------------------------------------------------
def test_rename_folder_moves_subscribes_and_repoints(account):
    """Red if RENAME is not sent or does not take the subfolder along, if
    either new name is left out of LSUB or an old one left in it, if the
    message count changes, if a rule still files into an old name, or if
    any byte of the script other than the two folder names moves -- the
    hand comment and the oddly spaced rule included."""
    with account.imap() as client:
        for folder in ("Lists.Old", "Lists.Old.Kid", "Keep"):
            client.create_folder(folder)
            client.subscribe_folder(folder)

    for number in range(3):
        account.append("Lists.Old", message(f"list {number}"))

    account.seed_script("managesieve", SCRIPT)
    before = account.script_bytes("managesieve")

    result = account.run("rename-folder", "Lists/Old", "Lists/New", "--yes")

    assert result.code == 0, result.err + result.out
    assert "FAIL" not in result.out

    with account.imap() as client:
        listed = {name for _, _, name in client.list_folders()}
        subscribed = {name for _, _, name in client.list_sub_folders()}
        count = client.folder_status("Lists.New", ["MESSAGES"])[b"MESSAGES"]

    assert {"Lists.New", "Lists.New.Kid", "Keep"} <= listed
    assert not {"Lists.Old", "Lists.Old.Kid"} & listed
    assert {"Lists.New", "Lists.New.Kid", "Keep"} <= subscribed
    assert not {"Lists.Old", "Lists.Old.Kid"} & subscribed
    assert count == 3

    assert account.script_bytes("managesieve") == before.replace(
        b'"Lists.Old";', b'"Lists.New";'
    ).replace(b'"Lists.Old.Kid"', b'"Lists.New.Kid"')
    assert account.active_script() == "managesieve"


# ----------------------------------------------------------------------------
def test_rename_folder_refuses_an_existing_name_and_changes_nothing(account):
    """Red if a rename onto an existing folder is sent to the server, or
    the script is touched on the way to refusing it."""
    with account.imap() as client:
        for folder in ("Lists.Old", "Taken"):
            client.create_folder(folder)

    account.seed_script("managesieve", SCRIPT)
    before = account.script_bytes("managesieve")

    result = account.run("rename-folder", "Lists/Old", "Taken", "--yes")

    assert result.code == 1
    assert "'Taken' already exists" in result.err

    with account.imap() as client:
        listed = {name for _, _, name in client.list_folders()}

    assert {"Lists.Old", "Taken"} <= listed
    assert account.script_bytes("managesieve") == before
