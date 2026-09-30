"""UIDVALIDITY against a real Dovecot: a folder deleted and made again under
the same name is renumbered, and a UID from before is refused (#204).

The oracle is IMAPClient's own SELECT response and the flags the server
keeps, never mailctl. Every test names, in its docstring, the break that
turns it red.
"""

import json
import uuid

import pytest

pytestmark = pytest.mark.container

FOLDER = "INBOX.Renumbered"


# ----------------------------------------------------------------------------
def message(subject: str) -> bytes:
    """A minimal RFC 5322 message."""
    return (
        f"From: Someone <someone@example.org>\r\n"
        f"To: user@example.test\r\n"
        f"Subject: {subject}\r\n"
        f"Date: Tue, 3 Feb 2026 04:05:06 +0000\r\n"
        f"Message-ID: <{uuid.uuid4().hex}@example.test>\r\n"
        f"\r\n"
        f"Body of {subject}.\r\n"
    ).encode()


# ----------------------------------------------------------------------------
def server_uidvalidity(account, folder: str) -> int:
    """What the server's own EXAMINE reports for ``folder``."""
    with account.imap() as client:
        return int(client.select_folder(folder, readonly=True)[b"UIDVALIDITY"])


# ----------------------------------------------------------------------------
def flags(account, folder: str, uid: int) -> set[bytes]:
    """One message's stored flags, less the session-only \\Recent."""
    with account.imap() as client:
        client.select_folder(folder, readonly=True)
        fetched = client.fetch([uid], ["FLAGS"])

    return set(fetched[uid][b"FLAGS"]) - {b"\\Recent"}


# ----------------------------------------------------------------------------
def remake(account, folder: str) -> None:
    """Delete ``folder`` and create it again under the same name."""
    with account.imap() as client:
        client.delete_folder(folder)
        client.create_folder(folder)


# ----------------------------------------------------------------------------
@pytest.fixture
def renumbered(account):
    """A folder whose UID 1 was one message and is now another.

    Returns the UIDVALIDITY from before and the one now. The first half of
    the claim is Dovecot's, and is asserted rather than assumed: a server
    that kept the value across a delete and a create would make every test
    here pass for the wrong reason.
    """
    with account.imap() as client:
        client.create_folder(FOLDER)

    first = account.append(FOLDER, message("before"))
    before = server_uidvalidity(account, FOLDER)

    remake(account, FOLDER)

    second = account.append(FOLDER, message("after"))
    after = server_uidvalidity(account, FOLDER)

    assert first == second == 1, "the new folder did not reuse UID 1"
    assert before != after, "Dovecot kept the UIDVALIDITY across a remake"

    return before, after


# ----------------------------------------------------------------------------
def test_search_json_reports_the_servers_uidvalidity(account, renumbered):
    """Red if the listing carries no value, or one other than what the
    server's own EXAMINE reports."""
    _before, after = renumbered

    result = account.run("mail", "search", "--folder", FOLDER, "--json")

    assert result.code == 0, result.err
    assert json.loads(result.out)["uidvalidity"] == after


# ----------------------------------------------------------------------------
def test_a_stale_uid_is_refused_and_the_new_message_left_alone(
    account, renumbered
):
    """The issue's done-condition, on a real server. UID 1 under the old
    UIDVALIDITY named the deleted message; under the new one it names
    another. Red if mark stores \\Seen on the message that now has UID 1
    -- the wrong-mail write #204 is about."""
    before, after = renumbered

    result = account.run(
        "mail",
        "mark",
        "1",
        "--folder",
        FOLDER,
        "--read",
        "--uidvalidity",
        str(before),
        "--yes",
    )

    assert result.code == 1
    assert f"now {after}" in result.err
    assert flags(account, FOLDER, 1) == set()


# ----------------------------------------------------------------------------
def test_a_current_pin_marks_the_message(account, renumbered):
    """Red if a pin that matches is refused, or stops the write."""
    _before, after = renumbered

    result = account.run(
        "mail",
        "mark",
        "1",
        "--folder",
        FOLDER,
        "--read",
        "--uidvalidity",
        str(after),
        "--yes",
    )

    assert result.code == 0, result.err
    assert flags(account, FOLDER, 1) == {b"\\Seen"}


# ----------------------------------------------------------------------------
def test_view_refuses_a_stale_uid_and_leaves_it_unread(account, renumbered):
    """Red if view shows the message UID 1 names now, as though it were
    the one the pin was taken for."""
    before, _after = renumbered

    result = account.run(
        "mail", "view", "1", "--folder", FOLDER, "--uidvalidity", str(before)
    )

    assert result.code == 1
    assert "after" not in result.out
    assert flags(account, FOLDER, 1) == set()
