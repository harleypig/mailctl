"""Terminal safety for 'messages' and 'view'.

Mail is written by whoever sent it. A header or body carrying ESC can
recolour the terminal, rewrite its title (OSC 0), plant a hyperlink whose
text lies about its target (OSC 8), or -- on some emulators -- push input
back into the shell; a lone carriage return rewinds the line so later text
overprints it. So every character a terminal acts on rather than draws is
escaped before printing, everywhere a message's content reaches stdout.

Bidirectional overrides and isolates are escaped the same way: they are
not terminal controls, but they reorder what is drawn, so an attachment
named ``invoice_<RLO>fdp.exe`` would display as ``invoice_exe.pdf``.

``view --raw`` into a pipe or a file is the one exception: nothing is
drawn there, so it writes the message's exact bytes instead.
"""

import sys

import pytest

from mailctl import cli
from mailctl.utilities.messages import MessageSummary

ESC = "\x1b"

# Everything a terminal may act on that this module must never emit raw.
FORBIDDEN = [chr(code) for code in range(0x20) if code not in (0x09, 0x0A)]
FORBIDDEN += ["\x7f", *(chr(code) for code in range(0x80, 0xA0))]

# Embeddings and overrides (U+202A-U+202E) and isolates (U+2066-U+2069):
# each changes the order of what follows until its terminator.
BIDI_CONTROLS = [
    chr(code) for code in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))
]
FORBIDDEN += BIDI_CONTROLS

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


# A sender reversing the tail of an attachment name and a subject, with
# every override and isolate present somewhere in the message.
SPOOFED = (
    "From: Mallory <mallory@example.com>\r\n"
    "Subject: Pay =?utf-8?q?=E2=80=AEtoday?= now\r\n"
    "MIME-Version: 1.0\r\n"
    'Content-Type: multipart/mixed; boundary="b"\r\n'
    "\r\n"
    "--b\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "Content-Transfer-Encoding: 8bit\r\n"
    "\r\n"
    f"{''.join(BIDI_CONTROLS)}\r\n"
    "--b\r\n"
    "Content-Type: application/octet-stream\r\n"
    "Content-Disposition: attachment;\r\n"
    " filename*=utf-8''invoice_%E2%80%AEfdp.exe\r\n"
    "\r\n"
    "payload\r\n"
    "--b--\r\n"
).encode()

# 8-bit mail that is not UTF-8: Latin-1 bytes, and a lone 0xFF that no
# text codec round-trips. A byte-exact '--raw' must hand back all of it.
LATIN1_8BIT = (
    b"From: Ren\xe9 <rene@example.com>\r\n"
    b"Subject: caf\xe9\r\n"
    b"Content-Type: text/plain; charset=iso-8859-1\r\n"
    b"Content-Transfer-Encoding: 8bit\r\n"
    b"\r\n"
    b"cr\xe8me br\xfbl\xe9e \xff\r\n"
    b"no final newline"
)


# A raw UTF-8 header (RFC 6532), carrying a C1 control once decoded.
UTF8_HEADER = (
    "From: zoë@exemple.fr\r\nSubject: café \u009b31m\r\n\r\nhi\r\n"
).encode()


# ----------------------------------------------------------------------------
@pytest.fixture
def mailbox(fake_imap, monkeypatch):
    """Load the double with the hostile messages and dummy credentials."""
    fake_imap.messages = {
        1: HOSTILE,
        2: HOSTILE_8BIT,
        3: SPOOFED,
        4: LATIN1_8BIT,
        5: UTF8_HEADER,
    }
    monkeypatch.setenv("MAILCTL_HOST", "mail.example.com")
    monkeypatch.setenv("MAILCTL_USER", "user@example.com")
    monkeypatch.setenv("MAILCTL_PASSWORD", "not-a-real-password")

    return fake_imap


# ----------------------------------------------------------------------------
@pytest.fixture
def run(mailbox, monkeypatch, capsys):
    """Run ``cli.main`` with stdout a terminal, returning what it printed.

    A terminal is the case the escaping exists for, and the only one
    where '--raw' escapes too.
    """

    def invoke(*argv: str) -> str:
        # Patched per call: the capture stream is only in place on
        # sys.stdout once the test itself is running.
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        code = cli.main(list(argv))
        captured = capsys.readouterr()

        assert code == 0, captured.err

        return captured.out

    return invoke


# ----------------------------------------------------------------------------
@pytest.fixture
def run_piped(mailbox, capsysbinary):
    """Run ``cli.main`` with stdout a pipe, returning the bytes written."""

    def invoke(*argv: str) -> bytes:
        code = cli.main(list(argv))
        captured = capsysbinary.readouterr()

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
        pytest.param(("view", "3"), id="view-bidi"),
        pytest.param(("view", "3", "--headers-only"), id="headers-bidi"),
        pytest.param(("view", "3", "--raw"), id="raw-bidi"),
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
@pytest.mark.parametrize(
    "argv", [("view", "5"), ("messages",)], ids=["view", "messages"]
)
def test_a_raw_utf8_header_is_shown_decoded_and_still_escaped(run, argv):
    """Decoding raw UTF-8 (#97) turns bytes into a real C1 control too."""
    output = run(*argv)

    assert "zoë@exemple.fr" in output
    assert "\\x9b" in output
    assert_terminal_safe(output)


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


# ----------------------------------------------------------------------------
def test_a_bidi_override_cannot_disguise_an_attachment(run):
    output = run("view", "3")

    assert "invoice_\\u202efdp.exe" in output
    assert "Subject: Pay \\u202etoday now" in output


# ############################################################################
# Byte-exact --raw into a pipe
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("uid", "source"),
    [
        pytest.param(4, LATIN1_8BIT, id="latin1-8bit"),
        pytest.param(2, HOSTILE_8BIT, id="controls-8bit"),
        pytest.param(3, SPOOFED, id="bidi"),
        pytest.param(1, HOSTILE, id="quoted-printable"),
    ],
)
def test_raw_into_a_pipe_is_the_exact_message(run_piped, uid, source):
    """'mailctl view N --raw > msg.eml' saves the message itself: no
    decoding, no escaping, no newline added or translated."""
    assert run_piped("view", str(uid), "--raw") == source


# ----------------------------------------------------------------------------
def test_raw_on_a_terminal_is_still_escaped(run):
    output = run("view", "4", "--raw")

    assert "caf\ufffd" in output
    assert output.endswith("no final newline\n")


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(("view", "2"), id="view"),
        pytest.param(("view", "2", "--headers-only"), id="headers-only"),
        pytest.param(("messages",), id="messages"),
    ],
)
def test_only_raw_skips_escaping_into_a_pipe(run_piped, argv):
    """The rendered forms are text for a reader wherever they go; a pipe
    into 'less' is still a terminal in the end."""
    assert_terminal_safe(run_piped(*argv).decode("utf-8"))


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
        pytest.param("a\x0bb\x0cc", "a\\x0bb\\x0cc", id="vt-ff"),
        pytest.param("Café ☕", "Café ☕", id="printable-unicode"),
        pytest.param("\udce9", "\ufffd", id="lone-surrogate"),
        pytest.param("a\u202eb", "a\\u202eb", id="rlo"),
        pytest.param("\u2066x\u2069", "\\u2066x\\u2069", id="isolate"),
        pytest.param("\u200e\u200f\u061c", "\u200e\u200f\u061c", id="marks"),
        pytest.param("שלום עולם", "שלום עולם", id="rtl-text"),
    ],
)
def test_safe_text(raw, shown):
    assert cli.safe_text(raw) == shown


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("char", BIDI_CONTROLS)
def test_every_bidi_control_is_escaped(char):
    assert cli.safe_line(char) == f"\\u{ord(char):04x}"


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
