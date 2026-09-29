"""Every folder's counts from one LIST-STATUS (RFC 5819; mailctl #157).

IMAPClient has no LIST-STATUS, so ``ImapSession.list_status`` sends the
command through imaplib and reads the STATUS lines imaplib collected. The
parser is tested twice: on imaplib's own shapes, and end to end from the
bytes a server sends, read by imaplib's real reader -- which is what turns
a literal mailbox name into the ``(line, literal)`` pair the parser has to
cope with. A double that handed the parser ready-made shapes would pass
the literal case whether or not imaplib produces that shape.
"""

import imaplib
from types import SimpleNamespace

import pytest
from imapclient.exceptions import IMAPClientAbortError

from mailctl import MailctlError
from mailctl.components.imap import connection_lost
from mailctl.components.imap.client import ImapSession
from mailctl.components.imap.status import (
    FolderStatus,
    list_status_arguments,
    parse_status,
)
from mailctl.config import Secret

# ############################################################################
# The parser, on imaplib's shapes
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_quoted_and_an_atom_name_are_read_with_their_counts():
    assert parse_status(
        [b'"INBOX.Lists" (MESSAGES 17 UNSEEN 16)', b"INBOX (MESSAGES 3)"]
    ) == [
        FolderStatus("INBOX.Lists", 17, 16),
        FolderStatus("INBOX", 3),
    ]


# ----------------------------------------------------------------------------
def test_a_literal_name_is_read_from_its_pair():
    assert parse_status(
        [(b"{7}", b"Foo Bar"), b" (MESSAGES 1 UNSEEN 0 SIZE 900)"]
    ) == [FolderStatus("Foo Bar", 1, 0, 900)]


# ----------------------------------------------------------------------------
def test_a_modified_utf7_name_is_decoded_as_list_decodes_it():
    assert parse_status([b'"INBOX.&AMk-t&AOk-" (MESSAGES 2 UNSEEN 1)']) == [
        FolderStatus("INBOX.Été", 2, 1)
    ]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("line", "name"),
    [
        (b"2026 (MESSAGES 1)", "2026"),
        (b'"2026" (MESSAGES 1)', "2026"),
        (b'"a \\"q\\" b" (MESSAGES 1)', 'a "q" b'),
    ],
)
def test_a_name_the_parser_reads_as_another_type_is_still_a_name(line, name):
    assert parse_status([line]) == [FolderStatus(name, 1)]


# ----------------------------------------------------------------------------
def test_items_are_matched_by_name_whatever_their_order_or_case():
    assert parse_status([b'"X" (size 5 unseen 1 MESSAGES 2 UIDNEXT 9)']) == [
        FolderStatus("X", 2, 1, 5)
    ]


# ----------------------------------------------------------------------------
def test_no_status_lines_is_no_folders():
    assert parse_status([]) == []
    assert parse_status([None]) == []


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "data",
    [
        [b'"INBOX" (MESSAGES 3'],
        [b'"INBOX"'],
        [b'"INBOX" (MESSAGES)'],
        [b'"INBOX" MESSAGES'],
        [(b"{9}", b"Foo"), b" (MESSAGES 1)"],
    ],
)
def test_a_line_that_does_not_parse_is_an_error_not_a_guess(data):
    with pytest.raises(MailctlError, match="STATUS"):
        parse_status(data)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("sizes", "items"),
    [
        (False, "(STATUS (MESSAGES UNSEEN))"),
        (True, "(STATUS (MESSAGES UNSEEN SIZE))"),
    ],
)
def test_size_is_asked_for_only_when_wanted(sizes, items):
    assert list_status_arguments(sizes) == ('""', '"*"', "RETURN", items)


# ############################################################################
# From the server's bytes, through imaplib's own reader
# ############################################################################


class ScriptedIMAP4(imaplib.IMAP4):
    """imaplib reading a server's bytes, one scripted answer per command.

    ``answers`` maps a command word to the untagged bytes the server sends
    before its tagged completion, and ``completion`` is that completion's
    status and text (default ``OK``). CAPABILITY, which imaplib sends as
    it connects, is always answered OK. What the client sent after that
    is kept in ``sent``.
    """

    # ------------------------------------------------------------------------
    def __init__(self, answers: dict[bytes, bytes], completion=b"OK done"):
        self.answers = answers
        self.completion = completion
        self.sent: list[bytes] = []
        self._pending = b""
        super().__init__("scripted.example", 143)
        # What imaplib sent while connecting is not the test's.
        self.sent.clear()

    # ------------------------------------------------------------------------
    def open(self, host="", port=143, timeout=None):
        self.host = host
        self.port = port
        # PREAUTH: the connection starts logged in, as the session's is.
        self.inbound = bytearray(b"* PREAUTH ready\r\n")

    # ------------------------------------------------------------------------
    def send(self, data):
        self._pending += data

        while b"\r\n" in self._pending:
            line, self._pending = self._pending.split(b"\r\n", 1)
            self.sent.append(line)
            tag, command = line.split(b" ", 2)[:2]

            if command.upper() == b"CAPABILITY":
                self.inbound += b"* CAPABILITY IMAP4rev1 LIST-STATUS\r\n"
                self.inbound += tag + b" OK done\r\n"

                continue

            self.inbound += self.answers.get(command.upper(), b"")
            self.inbound += tag + b" " + self.completion + b"\r\n"

    # ------------------------------------------------------------------------
    def readline(self):
        end = self.inbound.index(b"\n") + 1
        line = bytes(self.inbound[:end])
        del self.inbound[:end]

        return line

    # ------------------------------------------------------------------------
    def read(self, size):
        chunk = bytes(self.inbound[:size])
        del self.inbound[:size]

        return chunk

    # ------------------------------------------------------------------------
    def shutdown(self):
        pass


# ----------------------------------------------------------------------------
def scripted_session(server: ScriptedIMAP4) -> ImapSession:
    """An ImapSession whose client's imaplib connection is ``server``."""
    session = ImapSession(
        "scripted.example",
        143,
        "user",
        password=lambda: Secret("not-a-real-password"),
    )
    session.client = SimpleNamespace(_imap=server)  # type: ignore[assignment]

    return session


# What Dovecot sends for LIST-STATUS: a LIST line per folder, and a STATUS
# line after each selectable one. The \Noselect parent gets none (RFC 5819
# section 2); one name is a literal, one is modified UTF-7.
WIRE = (
    b'* LIST (\\HasChildren) "." INBOX\r\n'
    b"* STATUS INBOX (MESSAGES 17 UNSEEN 16 SIZE 81234)\r\n"
    b'* LIST (\\Noselect \\HasChildren) "." "Archive"\r\n'
    b'* LIST (\\HasNoChildren) "." {15}\r\nArchive.Foo Bar\r\n'
    b"* STATUS {15}\r\nArchive.Foo Bar (MESSAGES 0 UNSEEN 0 SIZE 0)\r\n"
    b'* LIST (\\HasNoChildren) "." "INBOX.&AMk-t&AOk-"\r\n'
    b'* STATUS "INBOX.&AMk-t&AOk-" (MESSAGES 4 UNSEEN 1 SIZE 5120)\r\n'
)


# ----------------------------------------------------------------------------
def test_one_list_reads_every_selectable_folders_counts():
    server = ScriptedIMAP4({b"LIST": WIRE})

    assert scripted_session(server).list_status(sizes=True) == [
        FolderStatus("INBOX", 17, 16, 81234),
        FolderStatus("Archive.Foo Bar", 0, 0, 0),
        FolderStatus("INBOX.Été", 4, 1, 5120),
    ]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("sizes", "items"),
    [(False, b"(MESSAGES UNSEEN)"), (True, b"(MESSAGES UNSEEN SIZE)")],
)
def test_the_command_on_the_wire_is_one_list_returning_status(sizes, items):
    server = ScriptedIMAP4({b"LIST": b""})

    scripted_session(server).list_status(sizes=sizes)

    assert len(server.sent) == 1
    assert server.sent[0].split(b" ", 1)[1] == (
        b'LIST "" "*" RETURN (STATUS ' + items + b")"
    )


# ----------------------------------------------------------------------------
def test_no_untagged_line_is_left_for_the_next_command():
    server = ScriptedIMAP4({b"LIST": WIRE})

    scripted_session(server).list_status(sizes=True)

    assert "LIST" not in server.untagged_responses
    assert "STATUS" not in server.untagged_responses


# ----------------------------------------------------------------------------
def test_a_refusal_is_an_error_naming_the_command():
    server = ScriptedIMAP4(
        {b"LIST": WIRE}, completion=b"NO [SERVERBUG] not today"
    )

    with pytest.raises(MailctlError, match="LIST-STATUS failed -- NO"):
        scripted_session(server).list_status()

    assert "STATUS" not in server.untagged_responses


# ----------------------------------------------------------------------------
def test_a_bad_command_is_an_error_naming_the_command():
    server = ScriptedIMAP4({}, completion=b"BAD unknown return option")

    with pytest.raises(MailctlError, match="LIST-STATUS failed"):
        scripted_session(server).list_status()


# ----------------------------------------------------------------------------
def test_a_dropped_connection_is_told_apart_from_a_refusal():
    """The session sends a read once more only when the connection went."""

    class Dropping:
        def __init__(self):
            self.untagged_responses: dict = {}

        def _simple_command(self, *args):
            raise IMAPClientAbortError("socket closed")

    session = scripted_session(Dropping())  # type: ignore[arg-type]

    with pytest.raises(MailctlError) as raised:
        session.list_status()

    assert connection_lost(raised.value)
