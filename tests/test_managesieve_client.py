"""The ManageSieve wrapper's own gaps, against a scripted server socket.

Everything above ``SieveClient`` in the suite runs on a client double.
These tests run the real ``SieveClient`` -- sievelib's code and ours --
against a fake *socket* that replays a server's bytes, because the gaps
closed here (ADR 0006 S1, S4, S5, S8, S9) live in how the wire is read:

* **S1 / #90** -- GETSCRIPT returns the server's exact bytes. sievelib's
  own reader turns CRLF into LF and drops the final line ending, so a
  backup of its output is not a backup.
* **S4** -- the CAPABILITY response is kept whole, including what sievelib
  discards.
* **S5** -- the read timeout is the caller's, not a hard-coded 5 s.
* **S8** -- nothing can switch on sievelib's debug output, which prints
  the base64 AUTHENTICATE payload: the password.
* **S9 / #95** -- a connection that closes mid-response is an error.
  sievelib's own reader asks for the next line forever.

They also stand guard over sievelib's private names, which the wrapper
reaches into; a sievelib release that renames one fails here first.
"""

import inspect
import signal
import socket
from typing import cast

import pytest

from mailctl import MailctlError
from mailctl.components.managesieve import client as client_module
from mailctl.components.managesieve import write_backup
from mailctl.components.managesieve.client import (
    DEFAULT_TIMEOUT,
    SieveClient,
    SieveConnectionError,
    SieveSession,
)
from mailctl.components.managesieve.servers import PLAIN
from mailctl.config import Secret
from mailctl.providers.mxroute.sieve import merge_rule, parse_script

# CRLF throughout and a final CRLF: the two things sievelib's getscript
# loses, in a script shaped like the one Roundcube leaves on MXroute.
SCRIPT = (
    b'require ["fileinto"];\r\n'
    b"# rule:[keep-boss]\r\n"
    b'if header :contains "from" "boss@example.com"\r\n'
    b"{\r\n"
    b'\tfileinto "INBOX.Boss";\r\n'
    b"\tstop;\r\n"
    b"}\r\n"
)

GREETING = (
    b'"IMPLEMENTATION" "Dovecot Pigeonhole"\r\n'
    b'"SIEVE" "fileinto mailbox imap4flags"\r\n'
    b'"NOTIFY" "mailto"\r\n'
    b'"SASL" "PLAIN"\r\n'
    b'"MAXREDIRECTS" "4"\r\n'
    b'"OWNER" "user@example.com"\r\n'
    b'"UNAUTHENTICATE"\r\n'
    b'"VERSION" "1.0"\r\n'
    b'OK "Dovecot ready."\r\n'
)

AUTHENTICATED = b'OK "Logged in."\r\n'

PASSWORD = "not-a-real-password"


# ############################################################################
# The scripted server
# ############################################################################


class ScriptedSocket:
    """Replays a server's bytes, at most ``chunk`` per ``recv``."""

    # ------------------------------------------------------------------------
    def __init__(self, data: bytes = b"", chunk: int = 4096):
        self.data = data
        self.chunk = chunk
        self.sent: list[bytes] = []
        self.timeouts: list[float] = []

    # ------------------------------------------------------------------------
    def sendall(self, payload: bytes) -> None:
        self.sent.append(payload)

    # ------------------------------------------------------------------------
    def recv(self, size: int) -> bytes:
        taken = self.data[: min(size, self.chunk)]
        self.data = self.data[len(taken) :]

        return taken

    # ------------------------------------------------------------------------
    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)

    # ------------------------------------------------------------------------
    def close(self) -> None:
        pass


# ----------------------------------------------------------------------------
def literal(script: bytes, status: bytes = b'OK "Getscript completed."'):
    """Return a GETSCRIPT response carrying ``script`` as a literal."""
    return b"{%d}\r\n" % len(script) + script + b"\r\n" + status + b"\r\n"


# ----------------------------------------------------------------------------
def open_client(sock: ScriptedSocket) -> SieveClient:
    """Return an authenticated client already reading from ``sock``."""
    client = SieveClient("mail.example.com", 4190)
    client.sock = cast(socket.socket, sock)
    client.authenticated = True

    return client


# ----------------------------------------------------------------------------
def open_session(data: bytes, chunk: int = 4096) -> SieveSession:
    """Return a session over :func:`open_client`, as ``open()`` leaves it."""
    session = SieveSession(
        "mail.example.com", 4190, "user@example.com", lambda: Secret(PASSWORD)
    )
    session.client = open_client(ScriptedSocket(data, chunk))

    return session


# ----------------------------------------------------------------------------
@pytest.fixture
def server(monkeypatch):
    """Serve ``GREETING`` then accept the login, on a scripted socket."""
    sock = ScriptedSocket(GREETING + AUTHENTICATED)

    monkeypatch.setattr(
        client_module.socket, "create_connection", lambda *a, **k: sock
    )

    return sock


# ----------------------------------------------------------------------------
def connected(timeout: float = DEFAULT_TIMEOUT) -> SieveSession:
    """Open a real session against the ``server`` fixture's socket."""
    session = SieveSession(
        "mail.example.com",
        4190,
        "user@example.com",
        lambda: Secret(PASSWORD),
        tls="none",
        timeout=timeout,
    )
    session.open()

    return session


# ############################################################################
# S1 / #90: GETSCRIPT is byte-exact
# ############################################################################


# ----------------------------------------------------------------------------
def test_getscript_returns_the_servers_exact_bytes():
    """CRLF and the final line ending both survive (#90)."""
    session = open_session(literal(SCRIPT))

    assert session.get_script_bytes("managesieve") == SCRIPT


# ----------------------------------------------------------------------------
def test_the_text_is_the_same_bytes_and_so_is_the_backup(tmp_path):
    """Decoding for the merge path loses nothing a backup needs."""
    text = open_session(literal(SCRIPT)).get_script("managesieve")

    target = write_backup(text, tmp_path / "backup.sieve")

    assert text.encode("utf-8") == SCRIPT
    assert target.read_bytes() == SCRIPT


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "script",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"keep;", id="no-final-newline"),
        pytest.param(b"keep;\n", id="lf-final-newline"),
        pytest.param(b"keep;\r\n\r\n", id="blank-last-line"),
        pytest.param("# rule:[Grüße]\r\nkeep;\r\n".encode(), id="non-ascii"),
    ],
)
def test_getscript_is_exact_whatever_the_script_ends_with(script):
    """The cases a line-based reader cannot tell apart."""
    assert open_session(literal(script)).get_script_bytes("s") == script


# ----------------------------------------------------------------------------
def test_a_script_split_across_many_reads_is_reassembled():
    """A literal longer than one ``recv`` is read to its declared length."""
    session = open_session(literal(SCRIPT), chunk=7)

    assert session.get_script_bytes("managesieve") == SCRIPT


# ----------------------------------------------------------------------------
def test_the_name_is_sent_as_sievelib_sends_every_other_name():
    sock = ScriptedSocket(literal(SCRIPT))
    open_client(sock).getscript_bytes("managesieve")

    assert sock.sent == [b'GETSCRIPT "managesieve"\r\n']


# ----------------------------------------------------------------------------
def test_bytes_past_the_response_are_left_for_the_next_command():
    """The buffer is sievelib's own, so nothing read ahead is lost."""
    client = open_client(ScriptedSocket(literal(SCRIPT) + b'OK "next"\r\n'))
    client.getscript_bytes("managesieve")

    assert client._private("read_buffer") == b'OK "next"\r\n'


# ----------------------------------------------------------------------------
def test_a_merge_still_works_on_the_exact_text():
    """CRLF text from the server parses, and the rule already there stays."""
    text = open_session(literal(SCRIPT)).get_script("managesieve")

    merged = merge_rule(
        text,
        "github",
        [("from", ":contains", "noreply@github.com")],
        [("fileinto", "INBOX.GitHub")],
    )

    assert [entry["name"] for entry in parse_script(merged).filters] == [
        "keep-boss",
        "github",
    ]


# ----------------------------------------------------------------------------
def test_a_no_answer_is_a_readable_failure_with_the_reason_kept():
    session = open_session(b'NO (NONEXISTENT) "There is no such script"\r\n')

    with pytest.raises(MailctlError, match="could not download script 'x'"):
        session.get_script_bytes("x")

    assert session.client is not None
    assert session.client.errcode == b"NONEXISTENT"


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("data", "reason"),
    [
        pytest.param(b'BYE "going away"\r\n', "closed by server", id="bye"),
        pytest.param(
            b"{5}\r\nkeep;junk\r\n", "no line end after", id="no-line-end"
        ),
        pytest.param(b"{50}\r\nkeep;", "closed by server", id="short-read"),
        pytest.param(b"what\r\n", "malformed response", id="garbage"),
    ],
)
def test_a_broken_response_fails_rather_than_returning_part(data, reason):
    with pytest.raises(MailctlError, match=reason):
        open_session(data).get_script_bytes("x")


# ----------------------------------------------------------------------------
def test_a_script_that_is_not_utf8_is_refused_by_byte_offset():
    session = open_session(literal(b"keep; # \xff\r\n"))

    with pytest.raises(MailctlError, match=r"not valid UTF-8.*byte 8"):
        session.get_script("x")


# ############################################################################
# S4: the whole CAPABILITY response
# ############################################################################


# ----------------------------------------------------------------------------
def test_capabilities_sievelib_drops_are_kept(server):
    """MAXREDIRECTS, OWNER, and UNAUTHENTICATE survive the login."""
    capabilities = connected().server_capabilities()

    assert capabilities.max_redirects == 4
    assert capabilities.owner == "user@example.com"
    assert capabilities.unauthenticate is True
    assert capabilities.implementation == "Dovecot Pigeonhole"
    assert [name for name, _value in capabilities.entries] == [
        "IMPLEMENTATION",
        "SIEVE",
        "NOTIFY",
        "SASL",
        "MAXREDIRECTS",
        "OWNER",
        "UNAUTHENTICATE",
        "VERSION",
    ]


# ----------------------------------------------------------------------------
def test_the_sieve_extensions_come_from_the_same_parse(server):
    assert connected().capabilities() == ["fileinto", "mailbox", "imap4flags"]


# ----------------------------------------------------------------------------
def test_sievelib_still_sees_the_capabilities_it_relies_on(server):
    """Its SASL choice and CHECKSCRIPT gate read its own table."""
    session = connected()

    assert session.client is not None
    assert session.client.get_sasl_mechanisms() == ["PLAIN"]
    assert session.client.get_implementation() == "Dovecot Pigeonhole"


# ----------------------------------------------------------------------------
def test_the_server_is_selected_from_what_it_advertised(server):
    assert connected().server().name == "pigeonhole"


# ----------------------------------------------------------------------------
def test_a_server_advertising_no_implementation_is_plain(monkeypatch):
    sock = ScriptedSocket(b'"SASL" "PLAIN"\r\nOK\r\n' + AUTHENTICATED)
    monkeypatch.setattr(
        client_module.socket, "create_connection", lambda *a, **k: sock
    )

    assert connected().server() is PLAIN


# ############################################################################
# S5: the read timeout
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_read_timeout_is_the_callers(server):
    connected(timeout=12.5)

    assert server.timeouts == [12.5]


# ----------------------------------------------------------------------------
def test_the_default_timeout_is_sievelibs_own(server):
    """A caller that sets nothing sees the 5 s it always had."""
    connected()

    assert server.timeouts == [5.0]


# ############################################################################
# S8: debug output cannot be switched on
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_client_is_built_with_debug_off():
    assert SieveClient("h", 4190)._private("debug") is False


# ----------------------------------------------------------------------------
def test_there_is_no_way_to_ask_for_debug():
    assert "debug" not in inspect.signature(SieveClient).parameters


# ----------------------------------------------------------------------------
def test_even_a_forced_debug_flag_prints_nothing(monkeypatch, capsys):
    """The AUTHENTICATE payload is the password, base64'd; it never prints.

    The flag is forced on after construction, which is the one way left
    to set it, and a whole login is run under it.
    """
    sock = ScriptedSocket(GREETING + AUTHENTICATED)
    monkeypatch.setattr(
        client_module.socket, "create_connection", lambda *a, **k: sock
    )

    client = SieveClient("h", 4190)
    vars(client)["_Client__debug"] = True

    assert client.connect("user@example.com", PASSWORD)

    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""


# ############################################################################
# S9 / #95: a closed connection fails rather than hanging
# ############################################################################

# Seconds a truncated response may take to fail. sievelib's own reader
# never returns, so the guard is what turns a hang into a red test.
HANG_GUARD = 5


# ----------------------------------------------------------------------------
@pytest.fixture
def no_hang():
    """Fail the test, rather than the run, if a read never returns."""

    def hung(_signum, _frame):
        raise AssertionError(f"still reading after {HANG_GUARD}s: a hang")

    previous = signal.signal(signal.SIGALRM, hung)
    signal.alarm(HANG_GUARD)

    yield

    signal.alarm(0)
    signal.signal(signal.SIGALRM, previous)


# ----------------------------------------------------------------------------
def test_sievelib_still_has_the_readers_the_client_replaces():
    """A rename upstream would bypass the S9 overrides without a word."""
    assert callable(getattr(client_module.Client, "_Client__read_line", None))
    assert callable(getattr(client_module.Client, "_Client__read_block", None))


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"", id="nothing"),
        pytest.param(b'"IMPLEMENTATION" "Dove', id="mid-line"),
        pytest.param(GREETING[:-20], id="before-the-status"),
    ],
)
def test_a_greeting_cut_short_fails_to_connect(monkeypatch, no_hang, data):
    """CAPABILITY is read by sievelib's reader, at connect and STARTTLS."""
    sock = ScriptedSocket(data)
    monkeypatch.setattr(
        client_module.socket, "create_connection", lambda *a, **k: sock
    )

    with pytest.raises(SieveConnectionError, match="closed by server"):
        connected()


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b'"managesieve" ACT', id="mid-line"),
        pytest.param(b"{20}\r\nmanage", id="mid-literal"),
        pytest.param(b"{11}\r\nmanagesieve", id="after-the-literal"),
        pytest.param(b'"managesieve" ACTIVE\r\n', id="before-the-status"),
    ],
)
def test_a_script_list_cut_short_fails(no_hang, data):
    with pytest.raises(MailctlError, match=r"LISTSCRIPTS.*closed by server"):
        open_session(data, chunk=3).list_scripts()


# ----------------------------------------------------------------------------
def test_an_error_literal_cut_short_fails(no_hang):
    """A NO whose reason is a literal reads it through the same path."""
    session = open_session(b"NO {40}\r\nscript too")

    with pytest.raises(MailctlError, match=r"PUTSCRIPT.*closed by server"):
        session.put_script("s", "keep;")


# ----------------------------------------------------------------------------
def test_a_whole_response_still_reads_one_byte_at_a_time(no_hang):
    """The overrides keep sievelib's contract, literals included."""
    data = b'{5}\r\nother\r\n"managesieve" ACTIVE\r\nOK "Listed."\r\n'

    assert open_session(data, chunk=1).list_scripts() == (
        "managesieve",
        ["other"],
    )
