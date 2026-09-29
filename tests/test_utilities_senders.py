"""The sender report: ``utilities.senders.count_senders``, over the
conftest IMAP double.

The double's SEARCH returns every message, so what is counted is decided
by the re-check, as it is for ``search``. Reading must never mark mail
read, and the ceiling must refuse before a single header is fetched.
"""

import pytest

from mailctl import MailctlError, utilities
from mailctl.cli import error_text
from mailctl.criteria import Criteria

SEEN = (b"\\Seen",)


# ----------------------------------------------------------------------------
def message(sender: str, *extra: str) -> bytes:
    lines = [f"From: {sender}", "Subject: hello", *extra]

    return ("\r\n".join(lines) + "\r\n\r\nbody\r\n").encode("utf-8")


# ----------------------------------------------------------------------------
def mailbox(fake_imap, *items) -> None:
    """Load (source, read) pairs into the double as UIDs 1, 2, ..."""
    fake_imap.messages = {}
    fake_imap.flags = {}

    for uid, (source, read) in enumerate(items, start=1):
        fake_imap.messages[uid] = source

        if read:
            fake_imap.flags[uid] = SEEN


GITHUB = "GitHub <noreply@github.com>"
SHOUTY = "GitHub <NoReply@GitHub.COM>"
NEWS = "News <news@example.com>"
BOSS = "Boss <boss@example.com>"
LIST = "List-Id: Python announcements <Python-Announce.python.org>"


# ----------------------------------------------------------------------------
def rows(report) -> list[tuple]:
    return [(row.key, row.total, row.unread) for row in report.senders]


# ############################################################################
# Grouping
# ############################################################################


# ----------------------------------------------------------------------------
def test_addresses_are_counted_lower_cased_with_unread(sessions, fake_imap):
    mailbox(
        fake_imap,
        (message(GITHUB), False),
        (message(SHOUTY), False),
        (message(GITHUB), True),
        (message(NEWS), True),
        (message(BOSS), False),
    )

    report = utilities.senders.count_senders(sessions, "INBOX")

    assert report.by == "address"
    assert report.folder == "INBOX"
    assert (report.messages, report.unread, report.groups) == (5, 3, 3)
    assert rows(report) == [
        ("noreply@github.com", 3, 2),
        ("boss@example.com", 1, 1),
        ("news@example.com", 1, 0),
    ]
    assert report.senders[0].name == "GitHub"


# ----------------------------------------------------------------------------
def test_by_domain_folds_every_address_at_it(sessions, fake_imap):
    mailbox(
        fake_imap,
        (message(NEWS), False),
        (message(BOSS), True),
        (message("Other <x@Example.COM>"), False),
        (message(GITHUB), False),
    )

    report = utilities.senders.count_senders(sessions, by="domain")

    assert rows(report) == [("example.com", 3, 2), ("github.com", 1, 1)]
    assert all(row.name == "" for row in report.senders)


# ----------------------------------------------------------------------------
def test_by_list_id_takes_the_bracketed_id_and_counts_the_rest_apart(
    sessions, fake_imap
):
    mailbox(
        fake_imap,
        (message(NEWS, LIST), False),
        (message(BOSS, LIST), True),
        (message(GITHUB), False),
    )

    report = utilities.senders.count_senders(sessions, by="list-id")

    assert rows(report) == [
        ("python-announce.python.org", 2, 1),
        (None, 1, 1),
    ]
    assert report.senders[0].name == "Python announcements"
    assert report.senders[1].name == ""


# ----------------------------------------------------------------------------
def test_an_encoded_display_name_is_decoded_and_cannot_split_the_address(
    sessions, fake_imap
):
    """=2C is a comma once decoded; parsing after decoding would read
    'Doe' as the address."""
    mailbox(
        fake_imap, (message("=?utf-8?q?Doe=2C_J=C3=BCrgen?= <j@x.org>"), 0)
    )

    report = utilities.senders.count_senders(sessions)

    assert rows(report) == [("j@x.org", 1, 1)]
    assert report.senders[0].name == "Doe, Jürgen"


# ----------------------------------------------------------------------------
def test_structure_inside_an_encoded_name_cannot_lose_the_address(
    sessions, fake_imap
):
    """']' and ';' are RFC 5322 specials; read as structure, the whole
    header came back as the 'address', escapes and all."""
    mailbox(
        fake_imap,
        (message("=?utf-8?q?Evil=1B]0;pwn=07?= <Evil@Example.com>"), 0),
    )

    report = utilities.senders.count_senders(sessions)

    assert rows(report) == [("evil@example.com", 1, 1)]
    assert report.senders[0].name == "Evil\x1b]0;pwn\x07"


# ----------------------------------------------------------------------------
def test_a_raw_utf8_from_is_read_as_utf8(sessions, fake_imap):
    mailbox(fake_imap, (message("Zoë <Zoë@exemple.fr>"), False))

    report = utilities.senders.count_senders(sessions)

    assert rows(report) == [("zoë@exemple.fr", 1, 1)]
    assert report.senders[0].name == "Zoë"


# ----------------------------------------------------------------------------
def test_mail_with_no_from_is_counted_under_none(sessions, fake_imap):
    mailbox(
        fake_imap,
        (b"Subject: anonymous\r\n\r\nbody\r\n", False),
        (b"From: \r\nSubject: blank\r\n\r\nbody\r\n", True),
        (message(BOSS), True),
    )

    for by in ("address", "domain"):
        report = utilities.senders.count_senders(sessions, by=by)

        assert rows(report)[0] == (None, 2, 1), by
        assert report.groups == 2


# ----------------------------------------------------------------------------
def test_the_name_kept_is_the_newest(sessions, fake_imap):
    mailbox(
        fake_imap,
        (message("Old Name <a@x.org>"), False),
        (message("New Name <a@x.org>"), False),
    )

    report = utilities.senders.count_senders(sessions)

    assert report.senders[0].name == "New Name"


# ############################################################################
# Order, share, and trimming
# ############################################################################


# ----------------------------------------------------------------------------
def test_ties_go_to_the_most_unread_then_by_name(sessions, fake_imap):
    mailbox(
        fake_imap,
        (message("b@x.org"), True),
        (message("b@x.org"), True),
        (message("c@x.org"), False),
        (message("c@x.org"), True),
        (message("a@x.org"), True),
        (message("a@x.org"), True),
    )

    report = utilities.senders.count_senders(sessions)

    assert [row.key for row in report.senders] == [
        "c@x.org",
        "a@x.org",
        "b@x.org",
    ]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("total", "unread", "percent"),
    [(3, 2, 66.7), (4, 0, 0.0), (1, 1, 100.0), (0, 0, 0.0)],
)
def test_unread_percent_is_a_share_to_one_decimal(total, unread, percent):
    row = utilities.senders.SenderCount("a@x.org", "", total, unread)

    assert row.unread_percent == percent


# ----------------------------------------------------------------------------
def test_min_and_top_trim_the_rows_but_not_the_totals(sessions, fake_imap):
    mailbox(
        fake_imap,
        *[(message(GITHUB), False)] * 3,
        *[(message(NEWS), False)] * 2,
        (message(BOSS), False),
    )

    trimmed = utilities.senders.count_senders(sessions, minimum=2)
    topped = utilities.senders.count_senders(sessions, top=1)

    assert rows(trimmed) == [
        ("noreply@github.com", 3, 3),
        ("news@example.com", 2, 2),
    ]
    assert rows(topped) == [("noreply@github.com", 3, 3)]

    for report in (trimmed, topped):
        assert (report.messages, report.groups) == (6, 3)


# ############################################################################
# Selection
# ############################################################################


# ----------------------------------------------------------------------------
def test_criteria_are_rechecked_against_the_headers(sessions, fake_imap):
    mailbox(
        fake_imap,
        (message(GITHUB), False),
        (message(NEWS), False),
        (message(SHOUTY), True),
    )
    criteria = Criteria()
    criteria.add("From", "noreply@github.com")

    report = utilities.senders.count_senders(sessions, criteria=criteria)

    assert rows(report) == [("noreply@github.com", 2, 1)]
    assert ("search", [["FROM", "noreply@github.com"]]) in fake_imap.calls


# ----------------------------------------------------------------------------
def test_no_criteria_counts_everything(sessions, fake_imap):
    mailbox(fake_imap, (message(GITHUB), False), (message(NEWS), False))

    report = utilities.senders.count_senders(sessions, criteria=Criteria())

    assert report.messages == 2
    assert ("search", "ALL") in fake_imap.calls


# ----------------------------------------------------------------------------
def test_an_empty_folder_is_an_empty_report(sessions, fake_imap):
    mailbox(fake_imap)

    report = utilities.senders.count_senders(sessions)

    assert (report.messages, report.unread, report.groups) == (0, 0, 0)
    assert report.senders == []
    assert fake_imap.fetches == []


# ############################################################################
# The work is bounded, and nothing is marked read
# ############################################################################


# ----------------------------------------------------------------------------
def test_over_the_ceiling_is_refused_before_any_header_is_read(
    sessions, fake_imap
):
    mailbox(fake_imap, *[(message(GITHUB), False)] * 3)

    with pytest.raises(MailctlError) as caught:
        utilities.senders.count_senders(sessions, max_messages=2)

    assert "found 3 message(s)" in str(caught.value)
    assert "raise the ceiling to 3 or higher" in str(caught.value)
    assert "--max-messages 3 or higher" in error_text(caught.value)
    assert fake_imap.fetches == []


# ----------------------------------------------------------------------------
def test_at_the_ceiling_is_counted(sessions, fake_imap):
    mailbox(fake_imap, *[(message(GITHUB), False)] * 3)

    report = utilities.senders.count_senders(sessions, max_messages=3)

    assert report.messages == 3


# ----------------------------------------------------------------------------
def test_headers_are_read_a_page_per_fetch(sessions, fake_imap):
    """600 messages are three FETCHes of at most 250 UIDs, never one per
    message, and every message is counted once."""
    mailbox(fake_imap, *[(message(GITHUB), False)] * 600)

    report = utilities.senders.count_senders(sessions)

    sizes = [len(uids) for uids, _ in fake_imap.fetches]

    assert sizes == [250, 250, 100]
    assert report.messages == 600
    assert sorted(uid for uids, _ in fake_imap.fetches for uid in uids) == (
        list(range(1, 601))
    )


# ----------------------------------------------------------------------------
def test_counting_never_marks_mail_read(sessions, fake_imap):
    mailbox(fake_imap, (message(GITHUB), False), (message(NEWS), False))

    utilities.senders.count_senders(sessions)

    assert fake_imap.readonly is True
    assert fake_imap.marked_seen == set()
    assert all(
        not item.upper().startswith("BODY[")
        for _, items in fake_imap.fetches
        for item in items
    )


# ############################################################################
# Refusals
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("kwargs", "text", "flag"),
    [
        ({"by": "subject"}, "cannot group senders by 'subject'", None),
        ({"top": 0}, "the number of rows must be at least 1", "--top"),
        ({"minimum": 0}, "the minimum count must be at least 1", "--min"),
        (
            {"max_messages": 0},
            "the message ceiling must be at least 1",
            "--max-messages",
        ),
    ],
)
def test_bad_arguments_are_refused_before_searching(
    sessions, fake_imap, kwargs, text, flag
):
    """#51: the utility names the setting; the CLI names its flag."""
    with pytest.raises(MailctlError, match=text) as caught:
        utilities.senders.count_senders(sessions, **kwargs)

    assert not [call for call in fake_imap.calls if call[0] == "search"]

    if flag is not None:
        assert error_text(caught.value) == f"{flag} must be at least 1, not 0"
