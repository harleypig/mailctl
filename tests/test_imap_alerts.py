"""IMAP ALERT response codes reach the caller (#205).

RFC 9051 section 7.1 has a client present an ``[ALERT]``'s text to the
user, and RFC 2683 section 3.4.11 repeats it because so many clients do
not. The session hands each one to its ``progress`` callback as a
``ServerAlert``; the CLI shows it on stderr (``test_cli_snapshots.py``,
the ``folders-alert*`` scenarios).

Three layers are tested here: the parser on its own, the session over the
conftest double, and the session over IMAPClient and imaplib themselves
reading a server's bytes -- the last because the session wraps a private
of imaplib, and only the real library shows the wrapping still sees every
line.
"""

import imaplib

import pytest
from imapclient import IMAPClient
from imapclient.exceptions import LoginError

from mailctl.components.imap import alerts
from mailctl.components.imap.alerts import ServerAlert
from mailctl.components.imap.client import (
    ImapAuthenticationError,
    ImapSession,
)
from mailctl.config import Secret
from mailctl.providers import model
from mailctl.providers.base import MAIL
from mailctl.providers.mxroute.imap import new_imap_session
from mailctl.providers.mxroute.transport import MxrouteTransport

# ############################################################################
# The parser
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "line",
    [
        b"* OK [ALERT] Maintenance tonight",
        b"* NO [ALERT] Maintenance tonight",
        b"* BAD [ALERT] Maintenance tonight",
        b"* BYE [ALERT] Maintenance tonight",
        b"* PREAUTH [ALERT] Maintenance tonight",
        b"a12 OK [ALERT] Maintenance tonight",
        b"a12 NO [ALERT] Maintenance tonight",
        b"* ok [alert] Maintenance tonight",
    ],
)
def test_every_status_response_can_carry_an_alert(line):
    """Red if one of RFC 9051's status responses, tagged or untagged, or
    the lower-case spelling of its atoms, is not read."""
    assert alerts.alert_in_line(line) == ServerAlert("Maintenance tonight")


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "line",
    [
        b"* OK Maintenance tonight",
        b"* OK [CAPABILITY IMAP4rev1] [ALERT] not a response code",
        b"* OK [ALERT]",
        b"* OK [ALERT]   ",
        b'* LIST () "." "OK [ALERT] a folder"',
        b"* 3 EXISTS",
        b"+ [ALERT] a continuation",
    ],
)
def test_a_line_without_an_alert_code_is_none(line):
    """Red if text that merely mentions ALERT, a second bracket after the
    one response code, an empty alert, or a non-status line is read as
    one."""
    assert alerts.alert_in_line(line) is None


# ----------------------------------------------------------------------------
def test_text_that_is_not_utf8_is_still_shown():
    assert alerts.alert_in_line(b"* OK [ALERT] quota \xff") == ServerAlert(
        "quota �"
    )


# ############################################################################
# The session, over the double
# ############################################################################


# ----------------------------------------------------------------------------
def session_on(imap_config, received, port=993) -> ImapSession:
    imap_config.imap_port = port

    return new_imap_session(imap_config, received.append)


# ----------------------------------------------------------------------------
def alerts_in(received) -> list[str]:
    return [item.text for item in received if isinstance(item, ServerAlert)]


# ----------------------------------------------------------------------------
def test_an_untagged_ok_during_a_command_is_handed_over(
    fake_imap, imap_config
):
    """Red if a response line read while a command runs is not watched."""
    received = []
    fake_imap.responses["list_folders"] = [
        b"* OK [ALERT] Mailbox is 95% full",
        b"a2 OK List completed",
    ]

    session_on(imap_config, received).open()

    assert alerts_in(received) == ["Mailbox is 95% full"]


# ----------------------------------------------------------------------------
def test_a_tagged_no_is_handed_over_before_the_login_fails(
    fake_imap, imap_config
):
    """Red if a refused command's completion is not watched, or if the
    alert is lost to the failure it arrived with."""
    received = []
    fake_imap.responses["login"] = [b"a1 NO [ALERT] Account suspended"]
    fake_imap.failures["login"] = LoginError("Account suspended")

    with pytest.raises(ImapAuthenticationError):
        session_on(imap_config, received).open()

    assert alerts_in(received) == ["Account suspended"]


# ----------------------------------------------------------------------------
def test_an_alert_at_login_is_handed_over(fake_imap, imap_config):
    received = []
    fake_imap.responses["login"] = [
        b"* OK [ALERT] Password expires in 3 days",
        b"a1 OK Logged in",
    ]

    session_on(imap_config, received).open()

    assert alerts_in(received) == ["Password expires in 3 days"]


# ----------------------------------------------------------------------------
def test_the_greeting_is_read_on_implicit_tls(fake_imap, imap_config):
    received = []
    fake_imap.welcome = b"* OK [ALERT] Maintenance tonight"
    fake_imap._imap.untagged_responses["OK"] = [b"[ALERT] Read-only today"]

    session_on(imap_config, received).open()

    assert alerts_in(received) == ["Maintenance tonight", "Read-only today"]


# ----------------------------------------------------------------------------
def test_nothing_before_starttls_is_shown(fake_imap, imap_config):
    """RFC 9051 section 7.1: an alert sent before TLS is ignored. Red if
    the greeting on port 143 -- sent in the clear -- is shown."""
    received = []
    fake_imap.welcome = b"* OK [ALERT] Click here to verify your account"
    fake_imap._imap.untagged_responses["OK"] = [b"[ALERT] Also cleartext"]
    fake_imap.responses["login"] = [b"* OK [ALERT] After TLS"]

    session_on(imap_config, received, port=143).open()

    assert fake_imap.starttls_called
    assert alerts_in(received) == ["After TLS"]


# ----------------------------------------------------------------------------
def test_an_alert_with_no_progress_callback_is_harmless(
    fake_imap, imap_config
):
    fake_imap.welcome = b"* OK [ALERT] Maintenance tonight"

    new_imap_session(imap_config).open()


# ############################################################################
# The provider: into the neutral model
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_transport_hands_over_the_neutral_record(fake_imap, imap_config):
    """Red if the component's own record reaches the front-end, which may
    import no component, or if the channel is not named."""
    received = []
    fake_imap.welcome = b"* OK [ALERT] Maintenance tonight"
    transport = MxrouteTransport(
        config=imap_config,
        progress=lambda channel, message: received.append((channel, message)),
    )

    transport.connect(MAIL)
    transport.disconnect(MAIL)

    alert = [item for item in received if not isinstance(item[1], str)]

    assert alert == [
        ("mail", model.ServerAlert("Maintenance tonight", kind="alert"))
    ]
    assert type(alert[0][1]) is model.ServerAlert


# ############################################################################
# The session, over IMAPClient and imaplib reading a server's bytes
# ############################################################################


class ScriptedServer(imaplib.IMAP4):
    """imaplib reading a server's bytes: a greeting, then per command word
    the lines the server sends, its tagged completion's tag filled in."""

    # ------------------------------------------------------------------------
    def __init__(self, greeting: bytes, answers: dict[bytes, list[bytes]]):
        self.greeting = greeting
        self.answers = answers
        self._pending = b""
        super().__init__("scripted.example", 993)

    # ------------------------------------------------------------------------
    def open(self, host="", port=993, timeout=None):
        self.host = host
        self.port = port
        self.inbound = bytearray(self.greeting + b"\r\n")

    # ------------------------------------------------------------------------
    def send(self, data):
        self._pending += data

        while b"\r\n" in self._pending:
            line, self._pending = self._pending.split(b"\r\n", 1)
            tag, command = line.split(b" ", 2)[:2]
            answer = self.answers.get(command.upper(), [b"TAG OK done"])

            for reply in answer:
                self.inbound += reply.replace(b"TAG", tag) + b"\r\n"

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


# The lines Dovecot sends, with an alert on each kind of response.
GREETING = b"* OK [ALERT] Greeting alert"
ANSWERS = {
    b"CAPABILITY": [
        b"* CAPABILITY IMAP4rev1 LITERAL+",
        b"* OK [ALERT] Capability alert",
        b"TAG OK done",
    ],
    b"LOGIN": [b"TAG OK [ALERT] Login alert"],
    b"LIST": [
        b'* LIST () "." INBOX',
        b"* OK [ALERT] List alert",
        b"TAG OK done",
    ],
    b"LSUB": [b'* LSUB () "." INBOX', b"TAG NO [ALERT] Lsub alert"],
    b"LOGOUT": [b"* BYE bye", b"TAG OK done"],
}


# ----------------------------------------------------------------------------
@pytest.fixture
def scripted(monkeypatch):
    """IMAPClient itself, connected to a ScriptedServer, never a socket."""

    def connect(greeting=GREETING, answers=ANSWERS):
        server = ScriptedServer(greeting, answers)

        monkeypatch.setattr(IMAPClient, "_create_IMAP4", lambda self: server)
        monkeypatch.setattr(IMAPClient, "_set_read_timeout", lambda self: None)

        return server

    return connect


# ----------------------------------------------------------------------------
def test_imaplib_s_own_reader_hands_over_every_alert(scripted):
    """Red if the wrapping of imaplib's reader stops seeing its lines --
    a rename of the private, or IMAPClient reading another way -- or if
    the greeting or the connection's own CAPABILITY is not looked at."""
    scripted()
    received = []
    session = ImapSession(
        "scripted.example",
        993,
        "user@example.com",
        password=lambda: Secret("not-a-real-password"),
        progress=received.append,
    )

    with pytest.raises(Exception, match="LSUB failed"):
        session.open()

    assert alerts_in(received) == [
        "Greeting alert",
        "Capability alert",
        "Login alert",
        "List alert",
        "Lsub alert",
    ]


# ----------------------------------------------------------------------------
def test_imaplib_s_reader_still_returns_every_line(scripted):
    """The wrapping hands imaplib back what it read: the folder list is
    the server's, alerts or no."""
    answers = {**ANSWERS, b"LSUB": [b'* LSUB () "." INBOX', b"TAG OK done"]}
    scripted(answers=answers)
    session = ImapSession(
        "scripted.example",
        993,
        "user@example.com",
        password=lambda: Secret("not-a-real-password"),
    )

    session.open()

    assert session.folders == ["INBOX"]
    assert session.subscribed_folders == ["INBOX"]
