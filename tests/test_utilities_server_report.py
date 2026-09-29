"""The report for a server mailctl does not recognise (#39, item 1).

Driven as any front-end would: plain inputs over a session, asserting on
what comes back. The Sieve side is ``FakeSieveSession``; the IMAP side is
the real ``ImapSession`` over the conftest double, so the ``ID`` answer
selects a server module for real.

The account here is built from sentinels -- the address, its domain and
local part, the host, the password, a folder, a script's name and its
text -- and the servers repeat every one of them back where a careless
report would copy them. None may appear anywhere in the report.
"""

from datetime import UTC, datetime

import pytest
from utilities_support import mxroute

from mailctl import __version__
from mailctl.config import Config, Secret
from mailctl.providers.base import ServerDescription
from mailctl.providers.mxroute.imap import new_imap_session
from mailctl.utilities.server_report import (
    MAIL,
    RULES,
    build_server_report,
    render_server_report,
    unrecognised_halves,
    unrecognised_servers,
)

NOW = datetime(2026, 9, 29, 14, 30, 5, tzinfo=UTC)

LOCAL = "zqlocal"
DOMAIN = "zqdomain.test"
USER = f"{LOCAL}@{DOMAIN}"
HOST = "zqhost.test"
PASSWORD = "zq-password-sentinel"
FOLDER = "ZqFolder"
SCRIPT = "zqscript"
OTHER_SCRIPT = "zqother"
SCRIPT_TEXT = "zq-script-text-sentinel"
IP = "198.51.100.4"

SENTINELS = [LOCAL, DOMAIN, HOST, PASSWORD, FOLDER, SCRIPT, SCRIPT_TEXT, IP]


# ----------------------------------------------------------------------------
@pytest.fixture
def account(fake_sieve, fake_imap):
    """The sentinel account, on servers mailctl does not recognise, each
    echoing the account's own details back in what it sends."""
    fake_sieve.active = SCRIPT
    fake_sieve.others = [OTHER_SCRIPT]
    fake_sieve.script = f'# {SCRIPT_TEXT}\nrequire "fileinto";\n'
    fake_sieve.extra = [
        ("IMPLEMENTATION", "Acme Sieve"),
        ("OWNER", USER),
        ("X-SERVED-BY", f"{HOST.upper()} for {LOCAL}"),
        ("X-FROM", IP),
    ]
    fake_imap.caps = {"ID", "NAMESPACE", "UIDPLUS"}
    fake_imap.id_response = (
        (
            b"name",
            b"Acme IMAP",
            b"x-folder",
            f"INBOX.{FOLDER}".encode(),
            b"x-contact",
            f"postmaster@{DOMAIN}".encode(),
            b"x-script",
            SCRIPT.encode(),
        ),
    )
    fake_imap.listing.append(((), b".", f"INBOX.{FOLDER}".encode()))
    fake_imap.namespace_response = (
        (("INBOX.", "."),),
        None,
        ((f"{FOLDER}.", "."),),
    )


# ----------------------------------------------------------------------------
@pytest.fixture
def config() -> Config:
    config = Config(host=HOST, imap_host=HOST, user=USER)
    config._password = Secret(PASSWORD)

    return config


# ----------------------------------------------------------------------------
@pytest.fixture
def session(account, fake_sieve, fake_imap, config):
    """The two halves, opened with the sentinel account's settings, once
    the servers hold the sentinel account."""
    imap = new_imap_session(config)
    imap.open()

    return mxroute(sieve=fake_sieve, imap=imap)


# ############################################################################
# Recognising a server
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_known_server_has_nothing_to_report(
    sessions, imap_config, fake_sieve, fake_imap
):
    """Servers answering as MXroute's Dovecot and Pigeonhole do."""
    fake_sieve.extra = [("IMPLEMENTATION", "Dovecot Pigeonhole")]
    fake_imap.caps.add("ID")

    assert unrecognised_servers(sessions) == []
    assert build_server_report(sessions, imap_config) is None


# ----------------------------------------------------------------------------
def test_an_unknown_server_is_named_by_its_half(session, account):
    assert unrecognised_servers(session) == [RULES, MAIL]


# ----------------------------------------------------------------------------
def test_only_the_unrecognised_half_is_named(session, account, fake_imap):
    fake_imap.id_response = ((b"name", b"Dovecot"),)

    assert unrecognised_servers(session) == [RULES]

    report = build_server_report(session, config_for(), now=NOW)

    assert report.unrecognised == ("ManageSieve",)
    assert [server.software for server in report.servers] == [
        None,
        "dovecot",
    ]


# ----------------------------------------------------------------------------
def test_a_server_that_sends_no_identity_is_unrecognised(
    sessions, fake_imap, fake_sieve
):
    """No ID and no IMPLEMENTATION: the plain protocol, reported."""
    fake_imap.caps = {"UIDPLUS"}

    assert unrecognised_servers(sessions) == [RULES, MAIL]


# ----------------------------------------------------------------------------
def test_a_half_the_session_lacks_is_not_asked(imap_session, fake_imap):
    """A mail-only session never asks the rule half anything."""
    fake_imap.caps.add("ID")

    assert unrecognised_servers(mxroute(imap=imap_session)) == []


# ----------------------------------------------------------------------------
def test_unrecognised_halves_reads_the_descriptions():
    known = ServerDescription((), (), True, software="dovecot")
    unknown = ServerDescription((), (), True)

    assert unrecognised_halves(unknown, known) == [RULES]
    assert unrecognised_halves(None, unknown) == [MAIL]
    assert unrecognised_halves(known, None) == []


# ############################################################################
# What the report holds
# ############################################################################


# ----------------------------------------------------------------------------
def config_for() -> Config:
    return Config(host=HOST, imap_host=HOST, user=USER)


# ----------------------------------------------------------------------------
def test_the_report_carries_identity_capabilities_and_shape(session, account):
    report = build_server_report(session, config_for(), now=NOW)
    rules, mail = report.servers

    assert report.version == __version__
    assert report.provider == "mxroute"
    assert report.taken == "2026-09-29"
    assert report.unrecognised == ("ManageSieve", "IMAP")
    assert report.title == (
        "Unrecognised server: ManageSieve Acme Sieve; IMAP Acme IMAP"
    )
    assert dict(rules.identity) == {"implementation": "Acme Sieve"}
    assert dict(mail.identity)["name"] == "Acme IMAP"
    assert report.extensions == ("fileinto", "imap4flags", "mailbox")
    assert report.active_rule_set is True
    assert report.rule_sets == 2
    assert report.delimiter == "."
    assert report.folders == 4
    assert [space.prefix for space in report.namespaces] == [
        "INBOX.",
        "<name>.",
    ]
    assert [(fact.label, fact.text) for fact in report.endpoints] == [
        ("IMAP", "<host>:993"),
        ("Sieve", "<host>:4190 (tls=starttls)"),
    ]


# ----------------------------------------------------------------------------
def test_every_identifying_value_is_replaced(session, account):
    """The account's own details, wherever a server repeated them, come
    back as placeholders -- whatever case the server wrote them in."""
    report = build_server_report(session, config_for(), now=NOW)
    rules, mail = report.servers
    capabilities = {item.name: item.value for item in rules.capabilities}

    assert capabilities["OWNER"] == "<redacted>"
    assert capabilities["X-SERVED-BY"] == "<host> for <user>"
    assert capabilities["X-FROM"] == "<ip>"
    assert dict(mail.identity) == {
        "name": "Acme IMAP",
        "x-folder": "<name>",
        "x-contact": "<address>",
        "x-script": "<name>",
    }


# ----------------------------------------------------------------------------
def test_no_sentinel_reaches_the_report_or_its_body(
    session, account, config, fake_sieve, fake_imap, monkeypatch
):
    """The core requirement. The password is never read at all: revealing
    it while the report is built fails the test outright."""

    def refuse(self):
        raise AssertionError("the report revealed the password")

    monkeypatch.setattr(Secret, "reveal", refuse)

    report = build_server_report(session, config, now=NOW)
    body = render_server_report(report)

    for text in (body, repr(report), report.title):
        found = [value for value in SENTINELS if value.lower() in text.lower()]

        assert found == [], text

    assert "get_script" not in fake_sieve.names()
    assert set(fake_imap.names()) <= {"login", "namespace", "id_"}


# ----------------------------------------------------------------------------
def test_the_sentinel_check_can_fail(session, account, fake_sieve):
    """Known positive for the check above: an IMPLEMENTATION naming the
    script's text is not one the report knows to replace, so it shows."""
    fake_sieve.extra[0] = ("IMPLEMENTATION", SCRIPT_TEXT)

    body = render_server_report(
        build_server_report(session, config_for(), now=NOW)
    )

    assert SCRIPT_TEXT in body


# ----------------------------------------------------------------------------
def test_inbox_and_short_names_are_left_alone(session, account, fake_imap):
    """INBOX is every server's, and a two-letter folder would rewrite the
    capability names it happens to appear in."""
    fake_imap.listing.append(((), b".", b"ID"))

    report = build_server_report(session, config_for(), now=NOW)
    mail = report.servers[1]

    assert report.namespaces[0].prefix == "INBOX."
    assert [item.name for item in mail.capabilities] == [
        "ID",
        "NAMESPACE",
        "UIDPLUS",
    ]


# ############################################################################
# The issue body
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_body_lays_out_each_server(session, account):
    body = render_server_report(
        build_server_report(session, config_for(), now=NOW)
    )

    assert body.startswith("## Unrecognised server\n")
    assert body.endswith("\n")
    assert "### ManageSieve\n" in body
    assert "### IMAP\n" in body
    assert "- Recognised as: `none`" in body
    assert "  - `OWNER <redacted>`" in body
    assert "- Capabilities, read before login:" in body
    assert "- Capabilities, read after login:" in body
    assert "- Sieve scripts: 2" in body
    assert "- Folders: 4" in body
    assert "## Left out" in body


# ----------------------------------------------------------------------------
def test_a_server_value_cannot_start_a_line_or_close_its_span(
    session, account, fake_sieve
):
    """What a server sent sits in one code span on its own line."""
    fake_sieve.extra.append(("X-FORGE", "a\n## Injected `b`"))

    body = render_server_report(
        build_server_report(session, config_for(), now=NOW)
    )

    assert "\n## Injected" not in body
    assert "  - `X-FORGE a ## Injected 'b'`" in body


# ----------------------------------------------------------------------------
def test_the_report_is_read_only(session, account, fake_sieve, fake_imap):
    build_server_report(session, config_for(), now=NOW)

    assert fake_sieve.calls == []
    assert set(fake_imap.names()) <= {"login", "namespace", "id_"}
