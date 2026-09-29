"""MXroute's side of IMAP: ``Config`` to a session, and the advice a failure
gains.

The layer-1 session takes plain parameters and says only what failed; what
is tested here is what mailctl's configuration and MXroute's setup add on
top. Nothing opens a socket -- the ``fake_imap`` double stands in for
``IMAPClient``.
"""

import ssl

import pytest
from imapclient.exceptions import LoginError

from mailctl import MailctlError
from mailctl.cli import error_text
from mailctl.config import Config, Secret
from mailctl.providers.mxroute.imap import imap_session, new_imap_session


# ----------------------------------------------------------------------------
def open_and_close(config: Config) -> None:
    """Open and close a session for ``config``, as the engine does."""
    with imap_session(config):
        pass


# ----------------------------------------------------------------------------
def test_port_993_uses_implicit_tls_and_143_uses_starttls(
    fake_imap, imap_config
):
    """The port picks the TLS mode; 993 and 143 are different protocols."""
    open_and_close(imap_config)

    assert fake_imap.connected_to == ("mail.example.com", 993, True)
    assert fake_imap.starttls_called is False

    imap_config.imap_port = 143
    open_and_close(imap_config)

    assert fake_imap.connected_to == ("mail.example.com", 143, False)
    assert fake_imap.starttls_called is True


# ----------------------------------------------------------------------------
def test_a_missing_setting_is_named_before_anything_connects(fake_imap):
    """Failing on the settings is friendlier than failing on the socket."""
    config = Config(user="user@example.com")
    config._password = Secret("x")

    with pytest.raises(MailctlError, match="imap_host"):
        new_imap_session(config)

    assert fake_imap.connected_to is None


# ----------------------------------------------------------------------------
def test_a_login_failure_names_the_full_address_convention(
    fake_imap, imap_config
):
    """MXroute wants the whole email address; the wrong guess looks generic.

    The message also has to report the credential *state* and never the
    credential.
    """
    fake_imap.failures["login"] = LoginError("no")

    with pytest.raises(MailctlError, match="FULL email address") as caught:
        open_and_close(imap_config)

    assert str(caught.value) == (
        "IMAP authentication failed for 'user@example.com' (password set). "
        "MXRoute expects the FULL email address as the username, e.g. "
        "you@yourdomain.com. [no]"
    )
    assert "not-a-real-password" not in str(caught.value)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        pytest.param(
            ssl.SSLError(1, "bad handshake"),
            "TLS failure against mail.example.com:993 -- bad handshake. "
            "Port 993 is implicit TLS; port 143 uses STARTTLS.",
            id="tls",
        ),
        pytest.param(
            OSError("no route"),
            "cannot reach mail.example.com:993 -- no route. Check the "
            "IMAP host and port settings.",
            id="unreachable",
        ),
    ],
)
def test_a_connection_failure_gains_the_advice_that_fits_it(
    fake_imap, imap_config, failure, expected
):
    """The hint names mailctl's settings, so it is added here, not below."""
    fake_imap.failures["login"] = failure

    with pytest.raises(MailctlError) as caught:
        open_and_close(imap_config)

    assert str(caught.value) == expected


# ----------------------------------------------------------------------------
def test_the_cli_names_the_flags_that_set_the_unreachable_server(
    fake_imap, imap_config
):
    """#51: the provider names its settings; the CLI names their flags."""
    fake_imap.failures["login"] = OSError("no route")

    with pytest.raises(MailctlError) as caught:
        open_and_close(imap_config)

    assert error_text(caught.value) == (
        "cannot reach mail.example.com:993 -- no route. Check --imap-host "
        "and --imap-port."
    )
    assert "--imap" not in str(caught.value)


# ----------------------------------------------------------------------------
def test_the_session_is_logged_out_on_the_way_out(fake_imap, imap_config):
    open_and_close(imap_config)

    assert fake_imap.names()[-1] == "logout"
