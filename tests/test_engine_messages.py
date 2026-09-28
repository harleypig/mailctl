"""Finding and reading messages: ``engine.list_messages`` and
``engine.read_message``, over the conftest IMAP double.

Two properties matter beyond "it returns the right data". Reading must
never mark a message read -- the double records every SELECT's mode and
every FETCH's items, and emulates the \\Seen a real server would set, so
each of the two guards is checked on its own. And decoding must never
raise on the malformed, mislabelled mail that real mailboxes hold.
"""

import pytest
from imapclient.response_parser import parse_fetch_response

from mailctl import MailctlError, engine
from mailctl.components.imap import structure_has_attachment
from mailctl.criteria import Criteria
from mailctl.providers.mxroute import MxrouteProvider

# ############################################################################
# Messages
# ############################################################################


# ----------------------------------------------------------------------------
def rfc822(*lines: str, body: str = "") -> bytes:
    """Join header lines and a body into CRLF message bytes."""
    return ("\r\n".join(lines) + "\r\n\r\n" + body).encode("latin-1")


PLAIN = rfc822(
    "From: Boss <boss@example.com>",
    "To: user@example.com",
    "Subject: =?utf-8?q?Caf=C3=A9_plans?=",
    "Date: Tue, 3 Feb 2026 04:05:06 +0000",
    "Content-Type: text/plain; charset=utf-8",
    body="Lunch at noon.\r\nBring the report.\r\n",
)

HTML_ONLY = rfc822(
    "From: News <news@example.com>",
    "Subject: Weekly",
    "Content-Type: text/html; charset=utf-8",
    body="<html><head><style>p {color: red}</style></head><body>"
    "<h1>Headline</h1><p>First &amp; best</p>"
    "<script>alert('x')</script><p>Second</p></body></html>",
)

MULTIPART = rfc822(
    "From: Sender <sender@example.com>",
    "Subject: Report attached",
    "MIME-Version: 1.0",
    'Content-Type: multipart/mixed; boundary="outer"',
    body="--outer\r\n"
    'Content-Type: multipart/alternative; boundary="alt"\r\n'
    "\r\n"
    "--alt\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "\r\n"
    "Plain version.\r\n"
    "--alt\r\n"
    "Content-Type: text/html; charset=utf-8\r\n"
    "\r\n"
    "<p>HTML version.</p>\r\n"
    "--alt--\r\n"
    "--outer\r\n"
    "Content-Type: application/pdf\r\n"
    'Content-Disposition: attachment; filename="report.pdf"\r\n'
    "Content-Transfer-Encoding: base64\r\n"
    "\r\n"
    "JVBERi0xLjQK\r\n"
    "--outer\r\n"
    "Content-Type: message/rfc822\r\n"
    "\r\n"
    "From: inner@example.com\r\n"
    "Subject: Forwarded\r\n"
    "\r\n"
    "Inner body that is not this message's text.\r\n"
    "--outer--\r\n",
)

LATIN1 = rfc822(
    "From: pierre@example.fr",
    "Subject: Menu",
    "Content-Type: text/plain; charset=iso-8859-1",
    body="Caf\xe9 cr\xe8me\r\n",
)

BOGUS_CHARSET = rfc822(
    "From: odd@example.com",
    "Subject: Odd",
    "Content-Type: text/plain; charset=x-no-such-charset",
    body="Caf\xe9\r\n",
)

# A charset name no codec lookup can even attempt: str.decode raises
# ValueError for the NUL, not LookupError.
NUL_CHARSET = rfc822(
    "From: odd@example.com",
    "Subject: nul",
    'Content-Type: text/plain; charset="utf\x00x"',
    body="body\r\n",
)

# RFC 2231 continuations mixed with a whole value: the stdlib's
# decode_params sorts None against int and raises TypeError.
MIXED_2231 = rfc822(
    "From: a@example.com",
    "Subject: s",
    "MIME-Version: 1.0",
    'Content-Type: multipart/mixed; boundary="b"',
    body="--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n"
    "--b\r\nContent-Type: application/octet-stream\r\n"
    "Content-Disposition: attachment; filename*0*=utf-8''a; "
    "filename*=utf-8''b\r\n\r\nzz\r\n--b--\r\n",
)

EIGHT_BIT_HEADER = rfc822(
    "From: Jos\xe9 <jose@example.com>",
    "Subject: raw 8-bit",
    body="hi\r\n",
)

# ############################################################################
# Fixtures and helpers
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.fixture
def sessions(imap_session) -> MxrouteProvider:
    return MxrouteProvider(imap=imap_session)


# ----------------------------------------------------------------------------
def seen_setting(item: str) -> bool:
    """Whether a FETCH item sets \\Seen outside a read-only folder."""
    item = item.upper()

    return item.startswith("BODY[") or item in ("RFC822", "RFC822.TEXT")


# ----------------------------------------------------------------------------
def assert_left_unread(fake) -> None:
    """Both guards against marking mail read, checked independently."""
    selects = [call for call in fake.calls if call[0] == "select_folder"]

    assert selects, "nothing was selected -- the check read nothing"
    assert all(readonly for _, _, readonly in selects)

    items = [item for _, parts in fake.fetches for item in parts]

    assert items, "nothing was fetched -- the check read nothing"
    assert not [item for item in items if seen_setting(item)]

    assert fake.marked_seen == set()


# ----------------------------------------------------------------------------
def structure(text: bytes):
    """Parse a BODYSTRUCTURE the way IMAPClient hands it over."""
    parsed = parse_fetch_response([b"1 (UID 1 BODYSTRUCTURE " + text + b")"])

    return parsed[1][b"BODYSTRUCTURE"]


PLAIN_STRUCTURE = (
    b'("text" "plain" ("charset" "utf-8") NIL NIL "7bit" 12 1 NIL NIL NIL NIL)'
)
PDF_PART = (
    b'("application" "pdf" ("name" "a.pdf") NIL NIL "base64" 100 NIL '
    b'("attachment" ("filename" "a.pdf")) NIL NIL)'
)

# ############################################################################
# list_messages
# ############################################################################


# ----------------------------------------------------------------------------
def test_listing_is_newest_first_and_capped(sessions, fake_imap):
    fake_imap.messages = dict.fromkeys(range(1, 6), PLAIN)

    listing = engine.list_messages(sessions, "INBOX", limit=3)

    assert [message.uid for message in listing.messages] == [5, 4, 3]
    assert listing.more is True
    assert listing.folder == "INBOX"


# ----------------------------------------------------------------------------
def test_a_listing_that_fits_the_limit_has_no_more(sessions, fake_imap):
    fake_imap.messages = {1: PLAIN, 2: PLAIN}

    listing = engine.list_messages(sessions, "INBOX", limit=2)

    assert [message.uid for message in listing.messages] == [2, 1]
    assert listing.more is False


# ----------------------------------------------------------------------------
def test_limit_none_lists_everything(sessions, fake_imap):
    fake_imap.messages = dict.fromkeys(range(1, 31), PLAIN)

    listing = engine.list_messages(sessions, "INBOX", limit=None)

    assert len(listing.messages) == 30
    assert listing.more is False


# ----------------------------------------------------------------------------
def test_summaries_carry_size_flags_and_attachments(sessions, fake_imap):
    fake_imap.messages = {1: PLAIN, 2: MULTIPART}
    fake_imap.flags = {1: (b"\\Seen", b"\\Flagged")}
    fake_imap.structures = {
        1: structure(PLAIN_STRUCTURE),
        2: structure(b"(" + PLAIN_STRUCTURE + PDF_PART + b' "mixed")'),
    }

    listing = engine.list_messages(sessions, "INBOX")
    by_uid = {message.uid: message for message in listing.messages}

    assert by_uid[1].flags == ("\\Seen", "\\Flagged")
    assert by_uid[1].size == len(PLAIN)
    assert by_uid[1].has_attachments is False
    assert by_uid[1].subject == "Café plans"
    assert by_uid[1].sender == "Boss <boss@example.com>"
    assert by_uid[2].has_attachments is True
    assert by_uid[2].flags == ()


# ----------------------------------------------------------------------------
def test_criteria_are_rechecked_against_the_headers(sessions, fake_imap):
    """The double's SEARCH returns everything; only the re-check narrows."""
    fake_imap.messages = {1: PLAIN, 2: HTML_ONLY, 3: LATIN1}
    criteria = Criteria()
    criteria.add("From", "news@example.com")

    listing = engine.list_messages(sessions, "INBOX", criteria=criteria)

    assert [message.uid for message in listing.messages] == [2]
    assert ("search", [["FROM", "news@example.com"]]) in fake_imap.calls


# ----------------------------------------------------------------------------
def test_one_undecodable_subject_does_not_break_the_listing(
    sessions, fake_imap
):
    fake_imap.messages = {
        1: PLAIN,
        2: rfc822("From: a@example.com", "Subject: =?utf-8?b?G=?=", body="x"),
    }

    listing = engine.list_messages(sessions, "INBOX")

    assert [message.subject for message in listing.messages] == [
        "=?utf-8?b?G=?=",
        "Café plans",
    ]


# ----------------------------------------------------------------------------
def test_a_raw_search_is_passed_through(sessions, fake_imap):
    fake_imap.messages = {1: PLAIN}

    engine.list_messages(sessions, "INBOX", search="UNSEEN")

    assert ("search", "UNSEEN") in fake_imap.calls


# ----------------------------------------------------------------------------
def test_empty_criteria_list_everything(sessions, fake_imap):
    fake_imap.messages = {1: PLAIN, 2: PLAIN}

    listing = engine.list_messages(sessions, "INBOX", criteria=Criteria())

    assert len(listing.messages) == 2
    assert ("search", "ALL") in fake_imap.calls


# ----------------------------------------------------------------------------
def test_criteria_and_a_raw_search_are_refused_together(sessions):
    criteria = Criteria()
    criteria.add("From", "x@example.com")

    with pytest.raises(MailctlError, match="not both"):
        engine.list_messages(sessions, criteria=criteria, search="ALL")


# ----------------------------------------------------------------------------
def test_a_limit_below_one_is_refused(sessions):
    with pytest.raises(MailctlError, match="at least 1"):
        engine.list_messages(sessions, limit=0)


# ----------------------------------------------------------------------------
def test_the_folder_is_normalized(sessions, fake_imap):
    fake_imap.messages = {1: PLAIN}

    listing = engine.list_messages(sessions, "Lists")

    assert listing.folder == "INBOX.Lists"
    assert ("select_folder", "INBOX.Lists", True) in fake_imap.calls


# ----------------------------------------------------------------------------
def test_listing_leaves_mail_unread(sessions, fake_imap):
    fake_imap.messages = {1: PLAIN, 2: MULTIPART}

    engine.list_messages(sessions, "INBOX")

    assert_left_unread(fake_imap)


# ----------------------------------------------------------------------------
def test_a_uid_expunged_between_search_and_fetch_is_skipped(
    sessions, fake_imap, monkeypatch
):
    fake_imap.messages = {1: PLAIN, 2: PLAIN}
    monkeypatch.setattr(fake_imap, "search", lambda key: [1, 2, 3])

    listing = engine.list_messages(sessions, "INBOX")

    assert [message.uid for message in listing.messages] == [2, 1]


# ############################################################################
# read_message
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_plain_message_is_decoded(sessions, fake_imap):
    fake_imap.messages = {7: PLAIN}
    fake_imap.flags = {7: (b"\\Answered",)}

    content = engine.read_message(sessions, "INBOX", 7)

    assert content.uid == 7
    assert content.header("subject") == "Café plans"
    assert content.header("From") == "Boss <boss@example.com>"
    assert content.header("Cc") == ""
    assert content.body == "Lunch at noon.\nBring the report.\n"
    assert content.body_from_html is False
    assert content.attachments == []
    assert content.flags == ("\\Answered",)
    assert content.source == PLAIN
    assert content.size == len(PLAIN)


# ----------------------------------------------------------------------------
def test_a_missing_uid_names_the_uid_and_folder(sessions, fake_imap):
    fake_imap.messages = {1: PLAIN}

    with pytest.raises(MailctlError, match="no message with uid 99 in"):
        engine.read_message(sessions, "INBOX", 99)


# ----------------------------------------------------------------------------
def test_a_uid_below_one_is_refused(sessions):
    with pytest.raises(MailctlError, match="start at 1"):
        engine.read_message(sessions, "INBOX", 0)


# ----------------------------------------------------------------------------
def test_reading_leaves_the_message_unread(sessions, fake_imap):
    fake_imap.messages = {1: PLAIN}

    engine.read_message(sessions, "INBOX", 1)

    assert_left_unread(fake_imap)


# ----------------------------------------------------------------------------
def test_html_only_mail_is_converted_and_flagged(sessions, fake_imap):
    fake_imap.messages = {1: HTML_ONLY}

    content = engine.read_message(sessions, "INBOX", 1)

    assert content.body_from_html is True
    assert content.body == "Headline\n\nFirst & best\n\nSecond"
    assert "<" not in content.body
    assert "alert" not in content.body
    assert "color" not in content.body


# ----------------------------------------------------------------------------
def test_multipart_prefers_plain_and_lists_attachments(sessions, fake_imap):
    fake_imap.messages = {1: MULTIPART}

    content = engine.read_message(sessions, "INBOX", 1)

    assert content.body == "Plain version."
    assert content.body_from_html is False
    assert "Inner body" not in content.body

    kinds = [(item.name, item.content_type) for item in content.attachments]

    assert kinds == [
        ("report.pdf", "application/pdf"),
        ("", "message/rfc822"),
    ]
    assert content.attachments[0].size == len(b"%PDF-1.4\n")
    assert content.attachments[1].size > 0


# ----------------------------------------------------------------------------
def test_a_declared_legacy_charset_decodes(sessions, fake_imap):
    fake_imap.messages = {1: LATIN1}

    content = engine.read_message(sessions, "INBOX", 1)

    assert content.body == "Café crème\n"


# ----------------------------------------------------------------------------
def test_an_unknown_charset_falls_back_without_raising(sessions, fake_imap):
    fake_imap.messages = {1: BOGUS_CHARSET}

    content = engine.read_message(sessions, "INBOX", 1)

    assert content.body == "Caf�\n"


# ----------------------------------------------------------------------------
def test_a_charset_with_a_nul_falls_back_without_raising(sessions, fake_imap):
    fake_imap.messages = {1: NUL_CHARSET}

    content = engine.read_message(sessions, "INBOX", 1)

    assert content.body == "body\n"


# ----------------------------------------------------------------------------
def test_an_unreadable_filename_leaves_the_attachment_unnamed(
    sessions, fake_imap
):
    fake_imap.messages = {1: MIXED_2231}

    content = engine.read_message(sessions, "INBOX", 1)

    assert content.body == "hi"
    assert [(a.name, a.content_type) for a in content.attachments] == [
        ("", "application/octet-stream")
    ]


# ----------------------------------------------------------------------------
def test_an_undecodable_8bit_header_is_still_printable(sessions, fake_imap):
    """Lone surrogates would raise in whichever front-end printed them."""
    fake_imap.messages = {1: EIGHT_BIT_HEADER}

    content = engine.read_message(sessions, "INBOX", 1)

    sender = content.header("From")
    sender.encode("utf-8")

    assert sender == "Jos� <jose@example.com>"


# ----------------------------------------------------------------------------
def test_a_folded_header_is_unfolded():
    source = rfc822("Subject: one\r\n two", body="x")

    assert engine.parse_message(source).header("Subject") == "one two"


# ----------------------------------------------------------------------------
def test_the_message_folder_is_normalized(sessions, fake_imap):
    fake_imap.messages = {1: PLAIN}

    content = engine.read_message(sessions, "Lists", 1)

    assert content.folder == "INBOX.Lists"


# ############################################################################
# structure_has_attachment
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(PLAIN_STRUCTURE, False, id="plain"),
        pytest.param(
            b"(" + PLAIN_STRUCTURE + PDF_PART + b' "mixed")',
            True,
            id="mixed-with-pdf",
        ),
        pytest.param(
            b"("
            + PLAIN_STRUCTURE
            + b'("text" "html" ("charset" "utf-8") NIL NIL "7bit" 20 1 '
            b'NIL NIL NIL NIL) "alternative")',
            False,
            id="alternative",
        ),
        pytest.param(
            b'("text" "plain" ("charset" "utf-8") NIL NIL "7bit" 12 1 '
            b'NIL ("inline" ("filename" "notes.txt")) NIL NIL)',
            True,
            id="text-with-filename",
        ),
        pytest.param(
            b'("image" "png" NIL "<id>" NIL "base64" 40 NIL NIL NIL NIL)',
            True,
            id="image",
        ),
    ],
)
def test_attachments_are_read_from_the_structure(text, expected):
    assert structure_has_attachment(structure(text)) is expected


# ----------------------------------------------------------------------------
def test_no_structure_means_no_attachment():
    assert structure_has_attachment(None) is False
