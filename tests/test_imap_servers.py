"""IMAP server modules are chosen by what the server says it is.

``select_server`` reads the ``ID`` response (RFC 2971). A server no module
recognises -- or one that sends no ``ID`` -- gets the plain protocol,
because an unknown server is the normal case for a new host rather than an
error. The choice never turns on a version number: MXroute's server reports
none (mailctl #18), so a version-keyed match could never fire there
(ADR 0006).
"""

import pytest
from imapclient.exceptions import IMAPClientError

from mailctl.components.imap.servers import PLAIN, dovecot, select_server


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "identity",
    [
        pytest.param({"name": "Dovecot"}, id="as-mxroute-reports-it"),
        pytest.param(
            {"name": "Dovecot", "version": "2.4.1"}, id="with-a-version"
        ),
        pytest.param({"name": "dovecot"}, id="lower-case"),
        pytest.param({"name": "Dovecot (Ubuntu)"}, id="with-a-suffix"),
    ],
)
def test_dovecot_is_recognised_by_its_id_name(identity):
    assert select_server(identity) is dovecot.PROFILE


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "identity",
    [
        pytest.param({"name": "Cyrus IMAPd"}, id="another-server"),
        pytest.param({"vendor": "Dovecot"}, id="the-name-is-what-counts"),
        pytest.param({"name": ""}, id="empty"),
        pytest.param({}, id="no-id"),
    ],
)
def test_an_unrecognised_server_gets_the_plain_protocol(identity):
    assert select_server(identity) is PLAIN


# ############################################################################
# Probing a live session
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_session_probes_id_and_selects_dovecot(fake_imap, imap_session):
    fake_imap.caps.add("ID")

    assert imap_session.identity() == {"name": "Dovecot"}
    assert imap_session.server() is dovecot.PROFILE


# ----------------------------------------------------------------------------
def test_the_probe_runs_once_and_only_when_asked(fake_imap, imap_session):
    """Opening a session sends no ID; asking twice sends one."""
    fake_imap.caps.add("ID")

    assert "id_" not in fake_imap.names()

    imap_session.server()
    imap_session.server()

    assert fake_imap.names().count("id_") == 1


# ----------------------------------------------------------------------------
def test_a_server_without_the_id_capability_is_not_asked(
    fake_imap, imap_session
):
    assert imap_session.server() is PLAIN
    assert "id_" not in fake_imap.names()


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("response", "failure"),
    [
        pytest.param((None,), None, id="nil"),
        pytest.param((), None, id="empty"),
        pytest.param(None, IMAPClientError("BAD"), id="refused"),
    ],
)
def test_an_unanswered_id_is_the_plain_protocol(
    fake_imap, imap_session, response, failure
):
    """The response is advisory; not getting one is ordinary."""
    fake_imap.caps.add("ID")
    fake_imap.id_response = response

    if failure is not None:
        fake_imap.failures["id_"] = failure

    assert imap_session.identity() == {}
    assert imap_session.server() is PLAIN


# ----------------------------------------------------------------------------
def test_a_nil_field_value_is_left_out(fake_imap, imap_session):
    fake_imap.caps.add("ID")
    fake_imap.id_response = ((b"name", b"Dovecot", b"version", None),)

    assert imap_session.identity() == {"name": "Dovecot"}
