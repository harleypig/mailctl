"""``mailctl folder list --counts`` against a real Dovecot (#157).

Read-only: the mail is put there by the oracle, and mailctl only counts
it. What the counts must be is read through IMAPClient's own ``STATUS``,
one folder at a time -- never through mailctl -- so a count mailctl reads
wrongly, or attributes to the wrong folder, still shows.
"""

import json

import pytest

pytestmark = pytest.mark.container

GITHUB = "noreply@github.com"


# ----------------------------------------------------------------------------
def message(subject: str) -> bytes:
    return (
        f"From: GitHub <{GITHUB}>\r\nTo: someone@example.test\r\n"
        f"Subject: {subject}\r\n\r\nbody of {subject}\r\n"
    ).encode()


# ----------------------------------------------------------------------------
def test_counts_json_is_what_status_says_for_every_folder(account):
    """Red if a folder's total or unread is wrong, if counts land on the
    wrong folder (the non-ASCII and spaced names are there for that), if
    a folder is dropped, or if the size is missing where the server
    reports sizes."""
    with account.imap() as client:
        advertised = {item.decode() for item in client.capabilities()}
        delimiter = client.list_folders()[0][1].decode()
        lists = f"Lists{delimiter}Git Hub"
        accented = "Été"

        for name in ("Lists", lists, accented):
            client.create_folder(name)

    assert "LIST-STATUS" in advertised

    account.append("INBOX", message("one"))
    account.append("INBOX", message("two"), flags=[b"\\Seen"])
    account.append("INBOX", message("three"))

    for subject in ("four", "five"):
        account.append(lists, message(subject), flags=[b"\\Seen"])

    account.append(accented, message("six"))

    result = account.run("folder", "list", "--counts", "--json")

    assert result.code == 0, result.err

    reported = {
        entry["name"]: entry for entry in json.loads(result.out)["folders"]
    }
    sizes = "STATUS=SIZE" in advertised
    items = ["MESSAGES", "UNSEEN", *(["SIZE"] if sizes else [])]

    with account.imap() as client:
        names = [name for _, _, name in client.list_folders()]
        expected = {name: client.folder_status(name, items) for name in names}

    assert set(reported) == set(names)
    assert (
        reported["INBOX"]["messages"],
        reported["INBOX"]["unseen"],
    ) == (3, 2)
    assert (reported[lists]["messages"], reported[lists]["unseen"]) == (2, 0)
    assert (
        reported[accented]["messages"],
        reported[accented]["unseen"],
    ) == (1, 1)

    for name, status in expected.items():
        entry = reported[name]

        assert entry["messages"] == status[b"MESSAGES"], name
        assert entry["unseen"] == status[b"UNSEEN"], name

        if sizes:
            assert entry["size"] == status[b"SIZE"], name
            assert entry["size"] > 0 or entry["messages"] == 0, name

        else:
            assert entry["size"] is None, name


# ----------------------------------------------------------------------------
def test_counting_leaves_every_message_unread(account):
    """LIST-STATUS reads no message, so nothing it does can set \\Seen."""
    account.append("INBOX", message("unread"))

    result = account.run("folder", "list", "--counts")

    assert result.code == 0, result.err

    with account.imap() as client:
        client.select_folder("INBOX", readonly=True)
        flags = client.get_flags(client.search("ALL"))

    assert all(b"\\Seen" not in value for value in flags.values())
