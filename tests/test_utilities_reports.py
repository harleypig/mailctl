"""The utilities for probing the account, driven as any front-end would.

No argparse and no stdout here: every test builds plain inputs, calls
the utility, and asserts on what comes back and on what the fakes were
asked to do.

The Sieve side is a session-level fake (``FakeSieveSession``); the IMAP
side is the real ``ImapSession`` over the ``FakeIMAPClient`` double from
conftest, so folder normalization and planning run for real.
"""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from utilities_support import mxroute

from mailctl import utilities
from mailctl.providers.base import Capability, Namespace, ServerDescription

# ############################################################################
# Reading the account
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_mail_probe_reads_capabilities_off_the_server(sessions, fake_imap):
    """What each capability means is the provider's to say, as facts."""
    fake_imap.caps = {"UIDPLUS", "FILTER=SIEVE"}

    probe = utilities.reports.probe_mail(sessions)
    facts = {fact.label: fact.text for fact in probe.facts}

    assert list(facts) == ["MOVE", "UIDPLUS", "FILTER=SIEVE"]
    assert facts["FILTER=SIEVE"].startswith("yes -- ")
    assert facts["UIDPLUS"] == "yes"
    assert facts["MOVE"] == "no (COPY+EXPUNGE)"
    assert probe.delimiter == "."


# ############################################################################
# The probe -- everything a provider's record observes (#101)
# ############################################################################

NOW = datetime(2026, 9, 29, 14, 30, 5, 123456, tzinfo=UTC)


# ----------------------------------------------------------------------------
def timedelta_zone(hours: int) -> timezone:
    """A fixed offset from UTC, ``hours`` ahead."""
    return timezone(timedelta(hours=hours))


# ----------------------------------------------------------------------------
@pytest.fixture
def dovecot(fake_sieve, fake_imap):
    """Both halves answering as a Dovecot does, each list out of order."""
    fake_sieve.caps = ["regex", "fileinto", "mailbox"]
    fake_sieve.extra = [
        ("SASL", "PLAIN"),
        ("IMPLEMENTATION", "Dovecot Pigeonhole"),
        ("STARTTLS", None),
    ]
    fake_imap.caps = {"UIDPLUS", "NAMESPACE", "ID", "MOVE"}
    fake_imap.id_response = ((b"vendor", b"Open", b"name", b"Dovecot"),)
    fake_imap.namespace_response = (
        (("b.", "."), ("", ".")),
        None,
        (("#shared.", "."),),
    )


# ----------------------------------------------------------------------------
def test_the_probe_reads_both_halves_and_sorts_every_list(
    sessions, imap_config, dovecot
):
    record = utilities.reports.probe_servers(sessions, imap_config, now=NOW)

    assert record.provider == "mxroute"
    assert [fact.label for fact in record.endpoints] == ["IMAP", "Sieve"]
    assert record.rules == ServerDescription(
        (("implementation", "Dovecot Pigeonhole"),),
        (
            Capability("IMPLEMENTATION", "Dovecot Pigeonhole"),
            Capability("SASL", "PLAIN"),
            Capability("SIEVE", "regex fileinto mailbox"),
            Capability("STARTTLS"),
        ),
        after_login=False,
        software="pigeonhole",
    )
    assert record.extensions == ("fileinto", "mailbox", "regex")
    assert record.active_rule_set == "managesieve"
    assert record.mail == ServerDescription(
        (("name", "Dovecot"), ("vendor", "Open")),
        tuple(
            Capability(name) for name in ("ID", "MOVE", "NAMESPACE", "UIDPLUS")
        ),
        after_login=True,
        software="dovecot",
    )
    assert record.delimiter == "."
    assert record.namespaces == (
        Namespace("personal", "", "."),
        Namespace("personal", "b.", "."),
        Namespace("shared", "#shared.", "."),
    )


# ----------------------------------------------------------------------------
def test_the_probe_changes_nothing(
    sessions, imap_config, dovecot, fake_sieve, fake_imap
):
    """Only reads reach either server: no upload, activation, folder, or
    subscription -- and the mail half never selects a folder."""
    utilities.reports.probe_servers(sessions, imap_config)

    assert fake_sieve.calls == []
    assert set(fake_imap.names()) <= {"login", "namespace", "id_"}


# ----------------------------------------------------------------------------
def test_the_probe_is_dated_in_utc_to_the_second(sessions, imap_config):
    elsewhere = datetime(2026, 9, 29, 16, 30, 5, 999, tzinfo=timedelta_zone(2))

    record = utilities.reports.probe_servers(
        sessions, imap_config, now=elsewhere
    )

    assert record.taken == datetime(2026, 9, 29, 14, 30, 5, tzinfo=UTC)

    before = datetime.now(UTC).replace(microsecond=0)
    fresh = utilities.reports.probe_servers(sessions, imap_config)

    assert fresh.taken.tzinfo == UTC
    assert before <= fresh.taken <= datetime.now(UTC)


# ----------------------------------------------------------------------------
def test_a_half_the_session_lacks_is_left_empty(imap_session, imap_config):
    """A session with no rule half reports none, and asks it nothing."""
    record = utilities.reports.probe_servers(
        mxroute(imap=imap_session), imap_config, now=NOW
    )

    assert record.rules is None
    assert record.extensions == ()
    assert record.active_rule_set is None
    assert record.mail is not None

    document = json.loads(utilities.reports.dump_probe(record))

    assert document["rules"] is None


# ----------------------------------------------------------------------------
def test_the_probe_document_is_versioned_and_keyed_for_diffing(
    sessions, imap_config, dovecot
):
    record = utilities.reports.probe_servers(sessions, imap_config, now=NOW)
    document = json.loads(utilities.reports.dump_probe(record))

    assert list(document) == [
        "version",
        "taken",
        "provider",
        "endpoints",
        "rules",
        "mail",
    ]
    assert document["version"] == utilities.reports.PROBE_VERSION == 1
    assert document["taken"] == "2026-09-29T14:30:05Z"
    assert list(document["rules"]) == [
        "identity",
        "capabilities_after_login",
        "capabilities",
        "extensions",
        "active_rule_set",
    ]
    assert list(document["mail"]) == [
        "identity",
        "capabilities_after_login",
        "capabilities",
        "delimiter",
        "namespaces",
    ]
    assert document["rules"]["capabilities"][-1] == {
        "name": "STARTTLS",
        "value": None,
    }
    assert document["mail"]["identity"] == {
        "name": "Dovecot",
        "vendor": "Open",
    }
    assert document["mail"]["namespaces"][0] == {
        "kind": "personal",
        "prefix": "",
        "delimiter": ".",
    }


# ----------------------------------------------------------------------------
def test_two_probes_of_an_unchanged_server_differ_only_in_the_time(
    sessions, imap_config, dovecot
):
    """What makes the document a baseline #18 can store and #19 diff."""
    first = utilities.reports.dump_probe(
        utilities.reports.probe_servers(sessions, imap_config, now=NOW)
    )
    later = utilities.reports.dump_probe(
        utilities.reports.probe_servers(
            sessions, imap_config, now=NOW + timedelta(days=90)
        )
    )

    changed = [
        (old, new)
        for old, new in zip(
            first.splitlines(), later.splitlines(), strict=True
        )
        if old != new
    ]

    assert changed == [
        (
            '  "taken": "2026-09-29T14:30:05Z",',
            '  "taken": "2026-12-28T14:30:05Z",',
        )
    ]
