"""``mailctl senders`` against a real Dovecot (#160).

Read-only: the oracle APPENDs mail from several senders, some of it
already read, and mailctl only counts it. The expected counts are the
ones the test wrote, so a sender merged wrongly, a read message counted
as unread, or a message counted twice shows as a wrong number.
"""

import json

import pytest

pytestmark = pytest.mark.container

SEEN = [b"\\Seen"]

# (From header, read) in the order appended. GitHub writes its address in
# two cases; the two example.org senders share a domain.
MAIL = [
    ("GitHub <noreply@github.com>", False),
    ("GitHub <NoReply@GitHub.com>", False),
    ("GitHub <noreply@github.com>", True),
    ("GitHub <noreply@github.com>", False),
    ("=?utf-8?q?J=C3=BCrgen?= <jurgen@example.org>", True),
    ("=?utf-8?q?J=C3=BCrgen?= <jurgen@example.org>", False),
    ("Friend <friend@example.org>", True),
    ("News <news@lists.example.net>", False),
]


# ----------------------------------------------------------------------------
def message(sender: str, number: int, list_id: str | None = None) -> bytes:
    lines = [f"From: {sender}", f"Subject: note {number}"]

    if list_id:
        lines.append(f"List-Id: Announcements <{list_id}>")

    return ("\r\n".join(lines) + "\r\n\r\nbody\r\n").encode()


# ----------------------------------------------------------------------------
def fill(account) -> None:
    for number, (sender, read) in enumerate(MAIL):
        list_id = "news.lists.example.net" if "news@" in sender else None

        account.append(
            "INBOX",
            message(sender, number, list_id),
            flags=SEEN if read else (),
        )


# ----------------------------------------------------------------------------
def counted(result) -> list[tuple]:
    assert result.code == 0, result.err

    document = json.loads(result.out)

    return [
        (row["key"], row["total"], row["unread"])
        for row in document["senders"]
    ]


# ----------------------------------------------------------------------------
def test_senders_counts_each_address_and_its_unread_exactly(account):
    """Red if an address in another case is counted apart, a \\Seen
    message is counted unread, or a name is not decoded."""
    fill(account)

    result = account.run("senders", "--json")
    document = json.loads(result.out)

    assert counted(result) == [
        ("noreply@github.com", 4, 3),
        ("jurgen@example.org", 2, 1),
        ("news@lists.example.net", 1, 1),
        ("friend@example.org", 1, 0),
    ]
    assert (document["messages"], document["unread"]) == (8, 5)
    assert document["senders"][1]["name"] == "Jürgen"


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("by", "expected"),
    [
        (
            "domain",
            [
                ("github.com", 4, 3),
                ("example.org", 3, 1),
                ("lists.example.net", 1, 1),
            ],
        ),
        (
            "list-id",
            [(None, 7, 4), ("news.lists.example.net", 1, 1)],
        ),
    ],
)
def test_senders_groups_by_domain_and_list_id(account, by, expected):
    fill(account)

    assert counted(account.run("senders", "--by", by, "--json")) == expected


# ----------------------------------------------------------------------------
def test_senders_criteria_reach_the_server(account):
    """--unread is answered by the server's UNSEEN: every row is unread."""
    fill(account)

    assert counted(account.run("senders", "--unread", "--json")) == [
        ("noreply@github.com", 3, 3),
        ("jurgen@example.org", 1, 1),
        ("news@lists.example.net", 1, 1),
    ]


# ----------------------------------------------------------------------------
def test_senders_refuses_over_the_ceiling(account):
    fill(account)

    result = account.run("senders", "--max-messages", "7")

    assert result.code == 1
    assert "found 8 message(s)" in result.err
    assert result.out == ""


# ----------------------------------------------------------------------------
def test_counting_leaves_every_message_as_it_was(account):
    fill(account)

    with account.imap() as client:
        client.select_folder("INBOX", readonly=True)
        before = client.get_flags(client.search("ALL"))

    assert account.run("senders").code == 0

    with account.imap() as client:
        client.select_folder("INBOX", readonly=True)
        after = client.get_flags(client.search("ALL"))

    assert after == before
