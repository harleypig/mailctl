"""Baselines (#18) and drift from them (#19), driven as a front-end would.

The comparison is a pure function over two probe records, so most of it is
checked on records built here. Saving and checking run over the same
``sessions`` fixture the other utility tests use: the Sieve side a
session-level fake, the IMAP side the real ``ImapSession`` over the
conftest double.
"""

import json
import stat
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mailctl import MailctlError, utilities
from mailctl.cli import error_text
from mailctl.components.imap.capabilities import CHECKED_CAPABILITIES
from mailctl.config import Config, Secret
from mailctl.providers.base import (
    Capability,
    DriftTerms,
    Fact,
    Namespace,
    ProbeRecord,
    ServerDescription,
)
from mailctl.providers.mxroute import MxrouteDialect

baseline = utilities.baseline

THEN = datetime(2026, 9, 1, 8, 0, 0, tzinfo=UTC)
NOW = datetime(2026, 9, 29, 14, 30, 5, tzinfo=UTC)

TERMS = DriftTerms(
    relied=frozenset({"MOVE", "UIDPLUS"}),
    account=frozenset({"OWNER"}),
    carriers=frozenset({"SIEVE"}),
)


# The two halves of an unchanged Dovecot, as record() describes them.
RULES_HALF = ServerDescription(
    (("implementation", "Dovecot Pigeonhole"),),
    (
        Capability("SASL", "PLAIN"),
        Capability("SIEVE", "fileinto imap4flags regex"),
    ),
    after_login=False,
)
MAIL_HALF = ServerDescription(
    (("name", "Dovecot"),),
    (Capability("IMAP4REV1"), Capability("MOVE"), Capability("QUOTA")),
    after_login=True,
)


# ----------------------------------------------------------------------------
def record(**changes) -> ProbeRecord:
    """A probe of an unchanged Dovecot, with ``changes`` applied."""
    value = ProbeRecord(
        taken=THEN,
        provider="mxroute",
        endpoints=(
            Fact("IMAP", "mail.example.com:993"),
            Fact("Sieve", "mail.example.com:4190 (tls=starttls)"),
        ),
        rules=RULES_HALF,
        extensions=("fileinto", "imap4flags", "regex"),
        active_rule_set="managesieve",
        mail=MAIL_HALF,
        delimiter=".",
        namespaces=(Namespace("personal", "INBOX.", "."),),
    )

    return replace(value, **changes)


# ----------------------------------------------------------------------------
def probe(value: ProbeRecord) -> dict:
    """A record as its document: what a baseline stores of it."""
    return utilities.reports.probe_document(value)


# ----------------------------------------------------------------------------
def saved(path: Path) -> baseline.Baseline:
    """The baseline a test has just written to ``path``."""
    read = baseline.read_baseline(path)

    assert read is not None

    return read


# ----------------------------------------------------------------------------
def kinds(drift) -> list[tuple[str, str, str]]:
    return [(item.severity, item.kind, item.name) for item in drift]


# ############################################################################
# Comparing two probes
# ############################################################################


# ----------------------------------------------------------------------------
def test_an_unchanged_server_has_no_drift_whenever_it_was_probed():
    """Red if the time a probe was taken is counted as a change."""
    assert (
        baseline.compare_probes(
            record(), record(taken=NOW), frozenset(), TERMS
        )
        == []
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("requires", "severity"),
    [
        (frozenset({"fileinto"}), baseline.SERIOUS),
        (frozenset({"imap4flags"}), baseline.INFO),
        (None, baseline.SERIOUS),
    ],
    ids=["required", "not-required", "script-unreadable"],
)
def test_a_lost_extension_is_serious_only_where_the_script_needs_it(
    requires, severity
):
    """Red if a lost extension the active script requires is not serious,
    if one it does not use is, or if an unreadable script is trusted."""
    after = record(extensions=("imap4flags", "regex"))

    drift = baseline.compare_probes(record(), after, requires, TERMS)

    assert kinds(drift) == [(severity, baseline.EXTENSION_REMOVED, "fileinto")]


# ----------------------------------------------------------------------------
def test_an_extension_name_compares_without_regard_to_case():
    """Red if 'FileInto' and 'fileinto' are read as one lost, one new."""
    after = record(extensions=("FileInto", "imap4flags", "regex"))

    assert baseline.compare_probes(record(), after, None, TERMS) == []


# ----------------------------------------------------------------------------
def test_a_new_extension_is_informational():
    after = record(extensions=("copy", "fileinto", "imap4flags", "regex"))

    drift = baseline.compare_probes(record(), after, frozenset(), TERMS)

    assert kinds(drift) == [(baseline.INFO, baseline.EXTENSION_ADDED, "copy")]
    assert drift[0].after == "copy"


# ----------------------------------------------------------------------------
def test_a_new_delimiter_is_serious():
    drift = baseline.compare_probes(
        record(), record(delimiter="/"), frozenset(), TERMS
    )

    assert kinds(drift) == [(baseline.SERIOUS, baseline.DELIMITER, "")]
    assert (drift[0].before, drift[0].after) == (".", "/")


# ----------------------------------------------------------------------------
def test_another_active_rule_set_is_serious_where_the_account_was_saved():
    """Red if a changed active script passes unreported, or is reported
    against a baseline that never held this account's."""
    after = record(active_rule_set="roundcube")

    drift = baseline.compare_probes(record(), after, frozenset(), TERMS)
    unrecorded = baseline.compare_probes(
        record(active_rule_set=None),
        after,
        frozenset(),
        TERMS,
        account_recorded=False,
    )

    assert kinds(drift) == [(baseline.SERIOUS, baseline.ACTIVE_RULE_SET, "")]
    assert unrecorded == []


# ----------------------------------------------------------------------------
def test_an_identity_change_is_informational():
    """The clearest migration signal, but nothing mailctl acts on."""
    rules = replace(
        RULES_HALF, identity=(("implementation", "Pigeonhole 2.4"),)
    )

    drift = baseline.compare_probes(
        record(), record(rules=rules), frozenset(), TERMS
    )

    assert kinds(drift) == [
        (baseline.INFO, baseline.IDENTITY, "implementation")
    ]


# ----------------------------------------------------------------------------
def test_a_mail_capability_mailctl_relies_on_is_serious_either_way():
    """Red if MOVE coming or going is informational, or if a capability
    mailctl never checks (QUOTA) is serious."""
    lost = replace(MAIL_HALF, capabilities=(Capability("IMAP4REV1"),))
    gained = replace(
        MAIL_HALF,
        capabilities=(*MAIL_HALF.capabilities, Capability("UIDPLUS")),
    )

    assert kinds(
        baseline.compare_probes(record(), record(mail=lost), None, TERMS)
    ) == [
        (baseline.SERIOUS, baseline.CAPABILITY_REMOVED, "MOVE"),
        (baseline.INFO, baseline.CAPABILITY_REMOVED, "QUOTA"),
    ]
    assert kinds(
        baseline.compare_probes(record(), record(mail=gained), None, TERMS)
    ) == [(baseline.SERIOUS, baseline.CAPABILITY_ADDED, "UIDPLUS")]


# ----------------------------------------------------------------------------
def test_a_rule_half_capability_is_informational_and_carriers_are_skipped():
    """Red if the SIEVE line is reported as well as its extensions, if
    OWNER (the account, not the server) is compared, or if a SASL change
    is missed."""
    rules = ServerDescription(
        RULES_HALF.identity,
        (
            Capability("OWNER", "someone@example.com"),
            Capability("SASL", "PLAIN LOGIN"),
            Capability("SIEVE", "fileinto imap4flags"),
        ),
        after_login=False,
    )
    after = record(rules=rules, extensions=("fileinto", "imap4flags"))

    drift = baseline.compare_probes(record(), after, frozenset(), TERMS)

    assert kinds(drift) == [
        (baseline.INFO, baseline.EXTENSION_REMOVED, "regex"),
        (baseline.INFO, baseline.CAPABILITY_CHANGED, "SASL"),
    ]


# ----------------------------------------------------------------------------
def test_lists_read_at_different_stages_are_not_compared():
    """Red if a list from before login is diffed against one from after,
    which differ with the server unchanged."""
    mail = ServerDescription(
        MAIL_HALF.identity, (Capability("IMAP4REV1"),), after_login=False
    )

    drift = baseline.compare_probes(record(), record(mail=mail), None, TERMS)

    assert kinds(drift) == [(baseline.INFO, baseline.STAGE, "")]


# ----------------------------------------------------------------------------
def test_the_personal_namespace_is_serious_and_the_others_are_not():
    after = record(
        namespaces=(
            Namespace("personal", "", "/"),
            Namespace("shared", "#shared.", "."),
        )
    )

    drift = baseline.compare_probes(record(), after, frozenset(), TERMS)

    assert kinds(drift) == [
        (baseline.SERIOUS, baseline.NAMESPACE, "personal"),
        (baseline.INFO, baseline.NAMESPACE, "shared"),
    ]
    assert (drift[0].before, drift[0].after) == (
        [["INBOX.", "."]],
        [["", "/"]],
    )


# ----------------------------------------------------------------------------
def test_an_endpoint_and_a_missing_half_are_informational():
    after = record(
        endpoints=(Fact("IMAP", "other.example.com:993"),),
        mail=None,
        delimiter=None,
        namespaces=(),
    )

    drift = baseline.compare_probes(record(), after, frozenset(), TERMS)

    assert kinds(drift) == [
        (baseline.INFO, baseline.HALF, ""),
        (baseline.INFO, baseline.ENDPOINT, "IMAP"),
        (baseline.INFO, baseline.ENDPOINT, "Sieve"),
    ]


# ----------------------------------------------------------------------------
def test_serious_drift_is_listed_first():
    after = record(extensions=("copy", "fileinto", "imap4flags", "regex"))
    after = replace(after, delimiter="/")

    drift = baseline.compare_probes(record(), after, frozenset(), TERMS)

    assert [item.severity for item in drift] == [
        baseline.SERIOUS,
        baseline.INFO,
    ]


# ############################################################################
# What the provider says a change means
# ############################################################################


# ----------------------------------------------------------------------------
def test_mxroute_relies_on_what_the_imap_component_checks():
    """Red if the provider's relied-on set drifts from what the component
    checks and what its own folder-count (#157) and sort (#159) support
    read."""
    terms = MxrouteDialect.drift_terms()

    assert terms.relied == CHECKED_CAPABILITIES | {
        "LIST-STATUS",
        "STATUS=SIZE",
        "SORT",
    }
    assert terms.carriers == {"SIEVE"}
    assert terms.account == {"OWNER"}


# ----------------------------------------------------------------------------
def test_mxroute_reads_a_scripts_require_line(roundcube_script):
    assert MxrouteDialect.rule_set_requires(roundcube_script) == [
        "fileinto",
        "imap4flags",
    ]
    assert MxrouteDialect.rule_set_requires("") == []

    with pytest.raises(MailctlError):
        MxrouteDialect.rule_set_requires("this is not sieve {")


# ############################################################################
# Saving, reading, and checking against the session
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.fixture
def where(tmp_path):
    return tmp_path / "baselines"


# ----------------------------------------------------------------------------
def save(sessions, config, where, now=NOW):
    plan = baseline.plan_save_baseline(sessions, config, where, now=now)
    baseline.execute_save_baseline(plan)

    return plan


# ----------------------------------------------------------------------------
def test_the_first_save_writes_one_private_file_per_host(
    sessions, imap_config, where, fake_sieve
):
    """Red if the file or its directory can be read by anyone else, if a
    first save pretends there was something to diff, or if saving writes
    to the server."""
    plan = baseline.plan_save_baseline(sessions, imap_config, where, now=NOW)

    assert plan.previous is None
    assert (plan.diff, plan.drift) == ([], [])
    assert not where.exists()

    path = baseline.execute_save_baseline(plan)

    assert path == where / "mail.example.com.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(where.stat().st_mode) == 0o700
    assert "put_script" not in fake_sieve.names()
    assert "set_active" not in fake_sieve.names()

    document = json.loads(path.read_text())

    assert document["version"] == baseline.BASELINE_VERSION
    assert document["host"] == "mail.example.com"
    assert "active_rule_set" not in document["server"]["rules"]
    assert document["accounts"]["user@example.com"]["active_rule_set"] == (
        "managesieve"
    )


# ----------------------------------------------------------------------------
def test_a_saved_baseline_reads_back_as_the_probe_it_was(
    sessions, imap_config, where
):
    """Red if anything is lost or altered between saving and reading."""
    plan = save(sessions, imap_config, where)
    read = saved(plan.path)

    assert probe(read.record_for("user@example.com")) == probe(plan.record)
    assert read.record_for("someone@else.example").active_rule_set is None


# ----------------------------------------------------------------------------
def test_an_account_capability_is_kept_with_the_account(
    sessions, imap_config, where, fake_sieve
):
    """OWNER names the account; it must not become a fact about the host."""
    fake_sieve.extra = [("OWNER", "user@example.com")]

    plan = save(sessions, imap_config, where)
    document = json.loads(plan.path.read_text())
    account = document["accounts"]["user@example.com"]

    assert account["rules_capabilities"] == [
        {"name": "OWNER", "value": "user@example.com"}
    ]
    assert "OWNER" not in {
        item["name"] for item in document["server"]["rules"]["capabilities"]
    }
    read = saved(plan.path)

    assert probe(read.record_for("user@example.com")) == probe(plan.record)


# ----------------------------------------------------------------------------
def test_saving_again_diffs_and_keeps_the_other_accounts(
    sessions, imap_config, where, fake_sieve
):
    first = save(sessions, imap_config, where, now=THEN)
    document = json.loads(first.path.read_text())
    document["accounts"]["other@example.com"] = document["accounts"][
        "user@example.com"
    ]
    first.path.write_text(json.dumps(document, indent=2) + "\n")
    fake_sieve.caps = ["fileinto", "imap4flags", "mailbox", "regex"]

    plan = baseline.plan_save_baseline(sessions, imap_config, where, now=NOW)

    assert plan.previous is not None
    assert '+        "regex"' in plan.diff
    assert kinds(plan.drift) == [
        (baseline.INFO, baseline.EXTENSION_ADDED, "regex")
    ]

    baseline.execute_save_baseline(plan)

    assert set(saved(plan.path).accounts) == {
        "other@example.com",
        "user@example.com",
    }


# ----------------------------------------------------------------------------
def test_the_baseline_never_holds_the_password(sessions, where, fake_imap):
    """The credential is handed to the login, and nowhere near the file."""
    sentinel = "s3ntinel-BASELINE-never-stored-9c2e"
    config = Config(
        host="mail.example.com",
        imap_host="mail.example.com",
        user="user@example.com",
    )
    config._password = Secret(sentinel)

    plan = save(sessions, config, where)

    assert sentinel not in plan.path.read_text()
    assert sentinel not in plan.text


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("{not json", "it is not JSON"),
        ("[]", "it is not a JSON object"),
        ('{"version": 2}', "it is version 2"),
        ('{"version": 1, "host": "mail.example.com"}', "KeyError: 'server'"),
        (
            '{"version": 1, "host": "other.example.com", "server": {}}',
            "is not the one it is for",
        ),
    ],
    ids=["not-json", "not-object", "version", "missing-key", "wrong-host"],
)
def test_a_file_that_is_not_a_baseline_is_refused_by_name(where, text, why):
    """Red if a damaged file is read as something it is not, or refused
    without saying which file."""
    where.mkdir()
    path = where / "mail.example.com.json"
    path.write_text(text)

    with pytest.raises(MailctlError) as caught:
        baseline.read_baseline(path)

    assert str(path) in str(caught.value)
    assert why in str(caught.value)


# ----------------------------------------------------------------------------
def test_a_damaged_file_is_never_replaced_by_a_save(
    sessions, imap_config, where
):
    where.mkdir()
    path = where / "mail.example.com.json"
    path.write_text("{not json")

    with pytest.raises(MailctlError, match="cannot be read"):
        baseline.plan_save_baseline(sessions, imap_config, where, now=NOW)

    assert path.read_text() == "{not json"


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "host", ["", "../etc/passwd", "a/b", "..", "[::1]", "mail..example"]
)
def test_a_host_that_cannot_name_a_file_is_refused(where, host):
    """Red if a host setting can steer the file out of the directory."""
    with pytest.raises(MailctlError):
        baseline.baseline_path(Config(host=host), where)


# ----------------------------------------------------------------------------
def test_no_host_is_refused_and_the_cli_names_its_flag(where):
    """#51: the utility states the condition; the CLI adds --host."""
    with pytest.raises(MailctlError) as caught:
        baseline.baseline_path(Config(), where)

    assert "--host" not in str(caught.value)
    assert error_text(caught.value) == (
        "no host is configured, so there is no baseline to name; set "
        "--host or MAILCTL_HOST"
    )


# ----------------------------------------------------------------------------
def test_the_host_is_folded_to_lower_case(where):
    path = baseline.baseline_path(Config(host="Mail.Example.COM"), where)

    assert path == where / "mail.example.com.json"


# ----------------------------------------------------------------------------
def test_a_check_with_no_baseline_says_how_to_make_one(
    sessions, imap_config, where, fake_sieve
):
    with pytest.raises(MailctlError, match="no baseline") as caught:
        baseline.check_baseline(sessions, imap_config, where)

    assert "mailctl save-baseline" not in str(caught.value)
    assert caught.value.code == "no_baseline"
    assert error_text(caught.value).endswith(
        "'mailctl save-baseline' records one"
    )
    assert fake_sieve.calls == []


# ----------------------------------------------------------------------------
def test_a_check_reads_what_the_active_script_requires(
    sessions, imap_config, where, fake_sieve
):
    """The lost 'fileinto' is serious because the script requires it; the
    lost 'mailbox' is not, because it does not."""
    save(sessions, imap_config, where, now=THEN)
    fake_sieve.caps = ["imap4flags"]

    check = baseline.check_baseline(sessions, imap_config, where, now=NOW)

    assert check.account_recorded
    assert check.requires_known
    assert check.stored.taken == THEN
    assert kinds(check.drift) == [
        (baseline.SERIOUS, baseline.EXTENSION_REMOVED, "fileinto"),
        (baseline.INFO, baseline.EXTENSION_REMOVED, "mailbox"),
    ]
    assert ("get_script", "managesieve") in fake_sieve.calls


# ----------------------------------------------------------------------------
def test_a_check_under_another_provider_is_refused(
    sessions, imap_config, where
):
    plan = save(sessions, imap_config, where)
    document = json.loads(plan.path.read_text())
    document["server"]["provider"] = "gmail"
    plan.path.write_text(json.dumps(document))

    with pytest.raises(MailctlError, match="provider 'gmail'"):
        baseline.check_baseline(sessions, imap_config, where)
