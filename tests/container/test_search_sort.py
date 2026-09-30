"""``search --sort`` (#159) against a real Dovecot, which sorts itself.

The offline tier pins what mailctl sends and how it orders what a double
returns; only a server can say that the ``UID SORT`` it sends is one the
server accepts, that SIZE, DATE, and ARRIVAL are the keys mailctl's names
say they are, and that the order survives the re-check and the limit.
The oracle for sizes is IMAPClient's own FETCH, never mailctl.

Every test names, in its docstring, the break that turns it red.
"""

import datetime
import json
import uuid

import pytest

pytestmark = pytest.mark.container


# ----------------------------------------------------------------------------
def message(
    subject: str, body_size: int = 0, date: str | None = None
) -> bytes:
    """A message whose body pads it out by ``body_size`` bytes."""
    date = date or "Tue, 3 Feb 2026 04:05:06 +0000"

    return (
        f"From: Someone <someone@example.org>\r\n"
        f"To: user@example.test\r\n"
        f"Subject: {subject}\r\n"
        f"Date: {date}\r\n"
        f"Message-ID: <{uuid.uuid4().hex}@example.test>\r\n"
        f"MIME-Version: 1.0\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Transfer-Encoding: 8bit\r\n"
        f"\r\n"
        f"{'x' * body_size}\r\n"
    ).encode()


# ----------------------------------------------------------------------------
def uids(result) -> list[int]:
    assert result.code == 0, result.err

    return [int(line) for line in result.out.split()]


# ----------------------------------------------------------------------------
def sizes(account, folder="INBOX") -> dict[int, int]:
    """Each message's RFC822.SIZE, read with IMAPClient, not mailctl."""
    with account.imap() as client:
        client.select_folder(folder, readonly=True)
        fetched = client.fetch(client.search("ALL"), ["RFC822.SIZE"])

    return {uid: data[b"RFC822.SIZE"] for uid, data in fetched.items()}


# ----------------------------------------------------------------------------
def test_the_server_advertises_sort(account):
    """The rest of this file tests the server-side path only because
    Dovecot advertises SORT; red if an image change drops it."""
    with account.imap() as client:
        assert client.has_capability("SORT")


# ----------------------------------------------------------------------------
def test_sort_size_reverse_limit_lists_the_biggest_in_order(account):
    """The two biggest, biggest first, whatever order they arrived in.
    Red if the limit were taken before sorting (the two newest), if
    --reverse were dropped (the two smallest), or if the size key were
    the wrong one."""
    small = account.append("INBOX", message("small", 10))
    biggest = account.append("INBOX", message("biggest", 5000))
    middle = account.append("INBOX", message("middle", 800))
    big = account.append("INBOX", message("big", 2000))
    newest = account.append("INBOX", message("newest", 50))

    result = account.run(
        "mail",
        "search",
        "--sort",
        "size",
        "--reverse",
        "--limit",
        "2",
        "--uids-only",
    )
    by_size = sorted(sizes(account).items(), key=lambda item: -item[1])

    assert uids(result) == [biggest, big]
    assert [uid for uid, _ in by_size[:2]] == [biggest, big]
    assert {small, middle, newest}.isdisjoint(uids(result))


# ----------------------------------------------------------------------------
def test_sent_is_the_date_header_and_received_the_arrival(account):
    """Two messages whose Date headers and arrival times disagree, so the
    two orders are opposite. Red if ``sent`` and ``received`` were mapped
    to the wrong RFC 5256 key."""
    now = datetime.datetime.now().replace(microsecond=0)

    early_sent = account.append(
        "INBOX",
        message("written first", date="Wed, 1 Jan 2025 00:00:00 +0000"),
        (),
        now,
    )
    late_sent = account.append(
        "INBOX",
        message("written last", date="Fri, 1 Jan 2027 00:00:00 +0000"),
        (),
        now - datetime.timedelta(days=30),
    )

    sent = account.run("mail", "search", "--sort", "sent", "--uids-only")
    received = account.run(
        "mail", "search", "--sort", "received", "--uids-only"
    )

    assert uids(sent) == [early_sent, late_sent]
    assert uids(received) == [late_sent, early_sent]


# ----------------------------------------------------------------------------
def test_a_non_ascii_criterion_is_sorted_and_rechecked(account):
    """#89's path under SORT: Dovecot takes the UTF-8 literal after the
    sort criteria, and only the matching messages come back, in size
    order. Red if the key reached the server mangled (no match, or an
    error) or the re-check reordered what the server sorted."""
    large = account.append("INBOX", message("un café crème", 3000))
    account.append("INBOX", message("un cafe sans accent", 9000))
    small = account.append("INBOX", message("café noir", 100))

    result = account.run(
        "mail", "search", "--subject", "café", "--sort", "size", "--uids-only"
    )

    assert uids(result) == [small, large]


# ----------------------------------------------------------------------------
def test_json_lists_sizes_in_order_and_names_the_sort(account):
    for size in (300, 4000, 100, 2500):
        account.append("INBOX", message(f"{size} bytes", size))

    result = account.run(
        "mail",
        "search",
        "--sort",
        "size",
        "--reverse",
        "--limit",
        "3",
        "--json",
    )

    assert result.code == 0, result.err

    document = json.loads(result.out)
    listed = [item["size"] for item in document["messages"]]

    assert document["sort"] == {"key": "size", "reverse": True}
    assert document["more"] is True
    assert len(listed) == 3
    assert listed == sorted(listed, reverse=True)
    assert listed == sorted(sizes(account).values(), reverse=True)[:3]


# ----------------------------------------------------------------------------
def test_a_sort_that_matches_nothing_lists_nothing(account):
    """An empty SORT response parses as no UIDs, not as an error."""
    account.append("INBOX", message("anything", 10))

    result = account.run(
        "mail",
        "search",
        "--from",
        "nobody@x.test",
        "--sort",
        "size",
        "--uids-only",
    )

    assert uids(result) == []
