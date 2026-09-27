"""Terminal safety for 'messages' and 'view'.

Mail is written by whoever sent it. A header or body carrying ESC can
recolour the terminal, rewrite its title (OSC 0), plant a hyperlink whose
text lies about its target (OSC 8), or -- on some emulators -- push input
back into the shell; a lone carriage return rewinds the line so later text
overprints it. So every character a terminal acts on rather than draws is
escaped before printing, everywhere a message's content reaches stdout.
"""

import pytest

from mxfilter import cli
from mxfilter.imap import MessageSummary

ESC = "\x1b"

# Everything a terminal may act on that this module must never emit raw.
FORBIDDEN = [chr(code) for code in range(0x20) if code not in (0x09, 0x0A)]
FORBIDDEN += ["\x7f", *(chr(code) for code in range(0x80, 0xA0))]

# ############################################################################
# A hostile message
# ############################################################################

# Subject: an encoded word decoding to ESC, a colour sequence, and a newline
# that would forge a header line. Body: colour, an OSC 8 hyperlink, a title
# change, a C1 CSI, a lone CR, and BEL. The attachment name hides a
# sequence in an RFC 2231 parameter.
HOSTILE = (
    b"From: Mallory <mallory@example.com>\r\n"
    b"Subject: =?utf-8?q?Invoice=1B[31m_red=0AFrom:_forged?=\r\n"
    b"MIME-Version: 1.0\r\n"
    b'Content-Type: multipart/mixed; boundary="b"\r\n'
    b"\r\n"
    b"--b\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"Content-Transfer-Encoding: quoted-printable\r\n"
    b"\r\n"
    b"=1B[2J=1B]8;;http://evil.example/=07Click here=1B]8;;=07\r\n"
    b"=1B]0;owned=07 =C2=9B31m progress=0Dsafe tail\r\n"
    b"--b\r\n"
    b"Content-Type: application/octet-stream\r\n"
    b"Content-Disposition: attachment;\r\n"
    b" filename*=utf-8''bad%1B%5B1mname.bin\r\n"
    b"\r\n"
    b"payload\r\n"
    b"--b--\r\n"
)

# The same attack with no transfer encoding to hide behind: the bytes
# arrive raw, which is what '--raw' prints.
HOSTILE_8BIT = (
    b"From: Mallory <mallory@example.com>\r\n"
    b"Subject: raw \x1b[31mred\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"Content-Transfer-Encoding: 8bit\r\n"
    b"\r\n"
    b"\x1b]0;owned\x07 \xc2\x9b31m progress\rsafe tail\r\n"
)


# ----------------------------------------------------------------------------
@pytest.fixture
def run(fake_imap, monkeypatch, capsys):
    """Run ``cli.main`` against the double holding the hostile message."""
    fake_imap.messages = {1: HOSTILE, 2: HOSTILE_8BIT}
    monkeypatch.setenv("MXROUTE_HOST", "mail.example.com")
    monkeypatch.setenv("MXROUTE_USER", "user@example.com")
    monkeypatch.setenv("MXROUTE_PASSWORD", "not-a-real-password")

    def invoke(*argv: str) -> str:
        code = cli.main(list(argv))
        captured = capsys.readouterr()

        assert code == 0, captured.err

        return captured.out

    return invoke


# ----------------------------------------------------------------------------
def assert_terminal_safe(output: str) -> None:
    """No forbidden character, a CR counting only outside a CRLF pair."""
    output = output.replace("\r\n", "\n")
    found = sorted({repr(char) for char in output if char in FORBIDDEN})

    assert found == [], f"raw control characters reached stdout: {found}"


# ############################################################################
# End to end
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(("view", "1"), id="view"),
        pytest.param(("view", "1", "--headers-only"), id="headers-only"),
        pytest.param(("view", "1", "--raw"), id="raw"),
        pytest.param(("view", "2"), id="view-8bit"),
        pytest.param(("view", "2", "--raw"), id="raw-8bit"),
        pytest.param(("messages",), id="messages"),
    ],
)
def test_hostile_content_never_reaches_the_terminal_raw(run, argv):
    assert_terminal_safe(run(*argv))


# ----------------------------------------------------------------------------
def test_view_shows_the_escapes_and_keeps_the_text(run):
    output = run("view", "1")

    assert "Click here" in output
    assert "\\x1b]8;;http://evil.example/\\x07" in output
    assert "\\x9b31m" in output
    assert "progress\\x0dsafe tail" in output
    assert "bad\\x1b[1mname.bin" in output


# ----------------------------------------------------------------------------
def test_a_decoded_newline_cannot_forge_a_header_line(run):
    lines = run("view", "1").splitlines()

    assert not [line for line in lines if line.startswith("From: forged")]
    assert "  Subject: Invoice\\x1b[31m red From: forged" in lines


# ----------------------------------------------------------------------------
def test_raw_keeps_safe_characters_as_they_are(run):
    output = run("view", "1", "--raw")

    assert "Content-Transfer-Encoding: quoted-printable\r\n" in output
    assert "=1B[2J" in output


# ############################################################################
# The sanitizer
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        pytest.param("\x1b[31mred", "\\x1b[31mred", id="csi"),
        pytest.param("\x1b]0;title\x07", "\\x1b]0;title\\x07", id="osc"),
        pytest.param("\x9b31m", "\\x9b31m", id="c1-csi"),
        pytest.param("a\rb", "a\\x0db", id="lone-cr"),
        pytest.param("a\r\nb", "a\r\nb", id="crlf"),
        pytest.param("a\tb\nc", "a\tb\nc", id="tab-newline"),
        pytest.param("\x00\x7f", "\\x00\\x7f", id="nul-del"),
        pytest.param("Café ☕", "Café ☕", id="printable-unicode"),
        pytest.param("\udce9", "\ufffd", id="lone-surrogate"),
    ],
)
def test_safe_text(raw, shown):
    assert cli.safe_text(raw) == shown


# ----------------------------------------------------------------------------
def test_safe_line_collapses_line_breaks():
    assert cli.safe_line("one\r\ntwo\n\tthree") == "one two three"


# ----------------------------------------------------------------------------
def test_clip_sanitizes_before_it_truncates():
    """The from-message and apply previews show the same untrusted
    headers, through ``clip``."""
    assert cli.clip("\x1b[31mred", 40) == "\\x1b[31mred"


# ############################################################################
# Listing helpers
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("size", "shown"),
    [(0, "0B"), (1023, "1023B"), (1536, "1.5K"), (3 * 1024 * 1024, "3.0M")],
)
def test_human_size(size, shown):
    assert cli.human_size(size) == shown


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("flags", "attached", "marks"),
    [
        pytest.param((), False, "N", id="unread"),
        pytest.param(("\\Seen",), False, "", id="read"),
        pytest.param(("\\SEEN", "\\Flagged"), True, "F@", id="case"),
        pytest.param(("\\Answered", "\\Deleted"), False, "NRD", id="more"),
    ],
)
def test_status_marks(flags, attached, marks):
    message = MessageSummary(
        uid=1,
        date="",
        sender="",
        subject="",
        folder="INBOX",
        flags=flags,
        has_attachments=attached,
    )

    assert cli.status_marks(message) == marks
