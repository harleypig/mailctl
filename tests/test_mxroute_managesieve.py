"""MXroute's side of ManageSieve: the advice a failed connection gains.

The layer-1 session says only what failed; what is tested here is the
advice MXroute's undocumented port and TLS mode add on top, and the CLI's
words for it. Nothing opens a socket -- a scripted one replays the server.
"""

import argparse

import pytest

from mailctl import MailctlError
from mailctl.cli import error_text
from mailctl.components.managesieve import client as client_module
from mailctl.config import Secret, load_config
from mailctl.providers.mxroute.managesieve import sieve_session

# A server's CAPABILITY with no STARTTLS in it.
GREETING = (
    b'"IMPLEMENTATION" "Dovecot Pigeonhole"\r\n'
    b'"SIEVE" "fileinto"\r\n'
    b'"SASL" "PLAIN"\r\n'
    b'"VERSION" "1.0"\r\n'
    b'OK "Dovecot ready."\r\n'
)


class ScriptedSocket:
    """Replays ``data``, and records what is sent."""

    # ------------------------------------------------------------------------
    def __init__(self, data: bytes):
        self.data = data
        self.sent: list[bytes] = []

    # ------------------------------------------------------------------------
    def sendall(self, payload: bytes) -> None:
        self.sent.append(payload)

    # ------------------------------------------------------------------------
    def recv(self, size: int) -> bytes:
        taken, self.data = self.data[:size], self.data[size:]

        return taken

    # ------------------------------------------------------------------------
    def settimeout(self, value: float) -> None:
        pass

    # ------------------------------------------------------------------------
    def close(self) -> None:
        pass


# ----------------------------------------------------------------------------
def test_a_server_offering_no_starttls_gains_the_other_tls_mode(monkeypatch):
    """The one thing that helps a user whose server offers no STARTTLS is
    the other TLS mode, so the refusal keeps its own code and carries the
    advice to try it, which the CLI names as the flag. Red if it is coded
    as an unreachable server (#377)."""
    sock = ScriptedSocket(GREETING)
    monkeypatch.setattr(
        client_module.socket, "create_connection", lambda *a, **k: sock
    )
    config = load_config(
        argparse.Namespace(host="mail.example.com", user="user@example.com"),
        environ={},
    )
    config._password = Secret("not-a-real-password")

    with pytest.raises(MailctlError) as caught, sieve_session(config):
        pass

    error = caught.value

    assert error.code == "starttls_not_offered"
    assert error.fields["advice"] == "try_setting"
    assert error.fields["setting"] == "sieve_tls"
    assert error.fields["value"] == "ssl"
    assert "does not offer STARTTLS" in error_text(error)
    assert " Try --sieve-tls ssl (implicit TLS) as the alternative." in (
        error_text(error)
    )
    assert sock.sent == []
