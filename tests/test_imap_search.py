"""Non-ASCII search values reach the server intact (mailctl #89; ADR 0006 I1).

IMAPClient cannot encode a non-ASCII ``str`` without a charset, and even
with ``charset='UTF-8'`` it does not carry the charset into nested
criteria (upstream mjs/imapclient#645). Every key ``Criteria`` builds is
nested -- each term is its own list -- so a flat search fails as surely as
an ``OR``.

These tests therefore run IMAPClient's **real** ``search`` serializer over
a recording transport and read the bytes that would go on the wire. The
``fake_imap`` double alone would pass them vacuously: it records the key it
was handed and never serializes it. The transport touches IMAPClient's
private ``_imap``, ``_cached_capabilities``, and ``_starttls_done``, which
is what ``imapclient<5`` in ``pyproject.toml`` bounds.
"""

import re

import pytest
from imapclient import IMAPClient

from mailctl import MailctlError
from mailctl.criteria import Criteria

# ############################################################################
# The wire
# ############################################################################


class RecordingTransport:
    """IMAPClient's ``_imap``, reduced to what ``search`` touches."""

    # ------------------------------------------------------------------------
    def __init__(self):
        self.sent: list[bytes] = []
        self.tags = 0

    # ------------------------------------------------------------------------
    def _new_tag(self) -> str:
        self.tags += 1

        return f"A{self.tags}"

    # ------------------------------------------------------------------------
    def send(self, data: bytes) -> None:
        self.sent.append(data)

    # ------------------------------------------------------------------------
    def _command_complete(self, command, tag):
        return "OK", [b"SEARCH completed"]

    # ------------------------------------------------------------------------
    def _untagged_response(self, typ, data, name):
        return "OK", [b""]


# ----------------------------------------------------------------------------
def serialize(criteria, charset=None) -> bytes:
    """Return the SEARCH command IMAPClient would send for ``criteria``.

    LITERAL+ is advertised, as Dovecot does, so a literal goes inline as
    ``{n+}`` rather than waiting on a continuation.
    """
    client = IMAPClient.__new__(IMAPClient)
    # IMAPClient types _imap as imaplib's IMAP4; this stands in for it.
    client._imap = RecordingTransport()  # pyright: ignore[reportAttributeAccessIssue]
    client.use_uid = True
    client._starttls_done = False
    client._cached_capabilities = (b"IMAP4REV1", b"LITERAL+")

    client.search(criteria, charset=charset)

    return b"".join(client._imap.sent)


_LITERAL = re.compile(rb"\{(\d+)\+\}\r\n")


# ----------------------------------------------------------------------------
def parse_wire(wire: bytes) -> tuple[bytes, list[bytes]]:
    """Split a command into its skeleton and its literals.

    The skeleton is the command with each literal replaced by ``<L>``, so
    parentheses inside a literal are data and never counted as structure.
    """
    skeleton, literals, position = b"", [], 0

    while match := _LITERAL.search(wire, position):
        size = int(match[1])
        skeleton += wire[position : match.start()] + b" <L>"
        literals.append(wire[match.end() : match.end() + size])
        position = match.end() + size

    return skeleton + wire[position:], literals


# ----------------------------------------------------------------------------
def balanced(skeleton: bytes) -> bool:
    """Whether every parenthesis in the skeleton closes, in order."""
    depth = 0

    for byte in skeleton:
        depth += {ord("("): 1, ord(")"): -1}.get(byte, 0)

        if depth < 0:
            return False

    return depth == 0


# ----------------------------------------------------------------------------
@pytest.fixture
def wire(fake_imap, monkeypatch) -> list[bytes]:
    """Route the double's SEARCH through IMAPClient's real serializer."""
    commands: list[bytes] = []

    def search(criteria, charset=None):
        fake_imap.calls.append(("search", criteria, charset))
        commands.append(serialize(criteria, charset))

        return sorted(fake_imap.messages)

    monkeypatch.setattr(fake_imap, "search", search)

    return commands


# ############################################################################
# Non-ASCII values, flat and inside an OR
# ############################################################################

# The header each value is searched in, and the raw header line a message
# carries it in. An address cannot be an RFC 2047 encoded word, so the
# address arrives as raw UTF-8 (RFC 6532); the others are encoded words.
NON_ASCII = {
    "subject": ("Café", b"Subject: =?utf-8?q?Caf=C3=A9_menu?="),
    "from": ("zoë@exemple.fr", "From: zoë@exemple.fr".encode()),
    "list-id": (
        "équipe",
        b"List-Id: =?utf-8?q?=C3=A9quipe?= <equipe.lists.example.com>",
    ),
}


# ----------------------------------------------------------------------------
def criteria_for(header: str, value: str, shape: str) -> Criteria:
    """One non-ASCII term, alone or ORed with an ASCII one."""
    if shape == "flat":
        criteria = Criteria(match="all")
        criteria.add(header, value)

        return criteria

    criteria = Criteria(match="any")
    criteria.add("to", "nobody@example.com")
    criteria.add(header, value)

    return criteria


# ----------------------------------------------------------------------------
def carrying(line: bytes) -> bytes:
    """A message whose only interesting header is ``line``."""
    return b"To: me@example.com\r\n" + line + b"\r\n\r\n"


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("shape", ["flat", "or"])
@pytest.mark.parametrize("header", sorted(NON_ASCII))
def test_a_non_ascii_value_is_sent_as_one_utf8_literal(
    imap_session, fake_imap, wire, header, shape
):
    """The value arrives whole, under ``CHARSET UTF-8``, and nothing else
    is swallowed into its literal."""
    value, _line = NON_ASCII[header]
    fake_imap.messages = {}

    imap_session.search(criteria_for(header, value, shape), "INBOX")

    skeleton, literals = parse_wire(wire[-1])

    assert skeleton.startswith(b"A1 UID SEARCH CHARSET UTF-8 "), skeleton
    assert literals == [value.encode("utf-8")], wire[-1]
    assert balanced(skeleton), wire[-1]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("shape", ["flat", "or"])
@pytest.mark.parametrize("header", sorted(NON_ASCII))
def test_the_recheck_compares_decoded_text(
    imap_session, fake_imap, wire, header, shape
):
    """The server's candidates are re-checked as text, not as the bytes
    sent: a match is kept and a near miss is dropped."""
    value, line = NON_ASCII[header]
    fake_imap.messages = {
        1: carrying(line),
        2: carrying(b"Subject: nothing to see"),
    }

    found = imap_session.search(criteria_for(header, value, shape), "INBOX")

    assert [message.uid for message in found] == [1]


# ----------------------------------------------------------------------------
def test_an_ascii_search_is_sent_exactly_as_before(imap_session, wire):
    """No CHARSET, no literal: a server that knows only US-ASCII -- the
    one charset RFC 3501 requires -- is still searched the way it was."""
    criteria = Criteria(match="any")
    criteria.add("from", "a@example.com")
    criteria.add("subject", "report")

    imap_session.search(criteria, "INBOX")

    assert wire[-1] == (
        b"A1 UID SEARCH (OR (FROM a@example.com) (SUBJECT report))\r\n"
    )


# ----------------------------------------------------------------------------
def test_a_value_that_is_not_text_is_refused_by_name(imap_session, wire):
    """A command-line argument that was not valid UTF-8 reaches Python as
    a lone surrogate, which no charset can encode. That is the user's to
    fix, so it is a MailctlError, not a traceback."""
    criteria = Criteria(match="all")
    criteria.add("subject", "caf\udce9")

    with pytest.raises(MailctlError, match="not valid text"):
        imap_session.search(criteria, "INBOX")

    assert wire == []


# ----------------------------------------------------------------------------
def test_a_non_ascii_raw_expression_is_refused_with_a_pointer(imap_session):
    """A raw SEARCH expression goes to the server unquoted, so a non-ASCII
    one cannot be sent correctly at all; the criteria flags can."""
    with pytest.raises(MailctlError, match="criteria flags"):
        imap_session.raw_search("INBOX", "SUBJECT café")
