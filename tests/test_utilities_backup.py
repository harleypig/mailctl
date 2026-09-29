"""The utilities for backups and restores, driven as any front-end would.

No argparse and no stdout here: every test builds plain inputs, calls
the utility, and asserts on what comes back and on what the fakes were
asked to do.

The Sieve side is a session-level fake (``FakeSieveSession``); the IMAP
side is the real ``ImapSession`` over the ``FakeIMAPClient`` double from
conftest, so folder normalization and planning run for real.
"""

import pytest
from utilities_support import FakeSieveSession, mxroute

from mailctl import MailctlError, utilities
from mailctl.providers.mxroute import MXROUTE

# ############################################################################
# Backups
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_backup_is_planned_then_written_byte_for_byte(
    sessions, imap_config, tmp_path
):
    plan = utilities.backup.plan_backup(
        sessions, imap_config, str(tmp_path / "c.sieve")
    )

    assert plan.target == tmp_path / "c.sieve"
    assert not plan.target.exists()

    written = utilities.backup.execute_backup(plan)

    assert written.read_bytes() == plan.source.encode()


# ----------------------------------------------------------------------------
def test_a_backup_with_no_active_script_is_refused(imap_config):
    with pytest.raises(MailctlError, match="nothing to back up"):
        utilities.backup.plan_backup(
            mxroute(sieve=FakeSieveSession(active=None)), imap_config
        )


# ----------------------------------------------------------------------------
def test_count_rules_reports_rather_than_raises_on_a_broken_script(
    roundcube_script,
):
    assert utilities.backup.count_rules(MXROUTE, roundcube_script) == 2
    assert utilities.backup.count_rules(MXROUTE, "if {{{") is None


# ############################################################################
# Restore
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_restore_is_planned_then_backs_up_and_uploads_exact_bytes(
    imap_config, tmp_path
):
    imap_config.backup_dir = tmp_path / "backups"
    backup = tmp_path / "old.sieve"
    backup.write_bytes(b'require "fileinto";\r\n# rule:[a]\r\n')
    fake = FakeSieveSession(script="current\n")
    live = mxroute(sieve=fake)

    plan = utilities.backup.plan_restore(
        live, utilities.backup.read_backup_file(backup)
    )

    assert plan.changes
    assert plan.after == 'require "fileinto";\r\n# rule:[a]\r\n'
    assert "-current" in plan.diff.text
    assert fake.names() == ["get_script"]

    events = []
    path = utilities.backup.execute_restore(
        live, imap_config, plan, events.append
    )

    assert path.read_text() == "current\n"
    assert fake.calls[-2] == ("put_script", "managesieve", plan.after)
    assert [type(event) for event in events] == [
        utilities.events.ScriptBackedUp,
        utilities.events.ScriptUploaded,
    ]


# ----------------------------------------------------------------------------
def test_a_restore_over_an_unparseable_script_is_allowed(
    imap_config, tmp_path, roundcube_script
):
    """ADR 0005: the backup, not a refusal, is what protects it."""
    imap_config.backup_dir = tmp_path / "backups"
    backup = tmp_path / "good.sieve"
    backup.write_text(roundcube_script)
    live = mxroute(sieve=FakeSieveSession(script="if {{{ broken"))

    utilities.backup.execute_restore(
        live,
        imap_config,
        utilities.backup.plan_restore(
            live, utilities.backup.read_backup_file(backup)
        ),
    )

    assert "put_script" in live.transport.sieve.names()


# ----------------------------------------------------------------------------
def test_restoring_an_identical_file_sends_nothing(imap_config, tmp_path):
    backup = tmp_path / "same.sieve"
    backup.write_text("same\n")
    live = mxroute(sieve=FakeSieveSession(script="same\n"))

    plan = utilities.backup.plan_restore(
        live, utilities.backup.read_backup_file(backup)
    )

    assert not plan.changes
    assert utilities.backup.execute_restore(live, imap_config, plan) is None
    assert "put_script" not in live.transport.sieve.names()


# ----------------------------------------------------------------------------
def test_a_restore_needs_a_readable_file(tmp_path):
    with pytest.raises(MailctlError, match="could not read backup"):
        utilities.backup.read_backup_file(tmp_path / "no")


# ----------------------------------------------------------------------------
def test_with_nothing_active_a_restore_asks_for_script(tmp_path):
    backup = tmp_path / "b.sieve"
    backup.write_text("x")
    live = mxroute(sieve=FakeSieveSession(active=None))

    with pytest.raises(MailctlError, match=r"no active script.*Name the"):
        utilities.backup.plan_restore(
            live, utilities.backup.read_backup_file(backup)
        )


# ----------------------------------------------------------------------------
def test_with_nothing_active_a_named_restore_uploads_and_activates(
    imap_config, tmp_path
):
    """The recovery case (#54): the account's script was deactivated."""
    imap_config.backup_dir = tmp_path / "backups"
    backup = tmp_path / "b.sieve"
    backup.write_text("new\n")
    fake = FakeSieveSession(script="old\n", active=None, others=["spare"])
    live = mxroute(sieve=fake)

    plan = utilities.backup.plan_restore(
        live, utilities.backup.read_backup_file(backup), script="spare"
    )

    assert (plan.active, plan.activate) == (None, True)

    utilities.backup.execute_restore(live, imap_config, plan)

    assert fake.calls[-2:] == [
        ("put_script", "spare", "new\n"),
        ("set_active", "spare"),
    ]


# ----------------------------------------------------------------------------
def test_a_backup_path_expands_home_and_variables(monkeypatch, tmp_path):
    (tmp_path / "b.sieve").write_text("x")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAILCTL_TEST_DIR", str(tmp_path))

    for spelled in ("~/b.sieve", "$MAILCTL_TEST_DIR/b.sieve"):
        assert (
            utilities.backup.read_backup_file(spelled).path
            == tmp_path / "b.sieve"
        )

    assert (
        utilities.backup.read_backup_file("${MAILCTL_TEST_DIR}/b.sieve").text
        == "x"
    )


# ----------------------------------------------------------------------------
def test_a_restore_targets_the_named_script_and_leaves_it_inactive(
    imap_config, tmp_path
):
    imap_config.backup_dir = tmp_path / "backups"
    backup = tmp_path / "b.sieve"
    backup.write_text("new\n")
    fake = FakeSieveSession(script="old\n", others=["spare"])
    live = mxroute(sieve=fake)

    plan = utilities.backup.plan_restore(
        live, utilities.backup.read_backup_file(backup), script="spare"
    )

    assert (plan.script, plan.activate) == ("spare", False)
    assert fake.calls == [("get_script", "spare")]

    utilities.backup.execute_restore(live, imap_config, plan)

    assert fake.calls[-1] == ("put_script", "spare", "new\n")
    assert "set_active" not in fake.names()


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("content", ["", "\n", "  \r\n\t"])
def test_an_empty_backup_is_refused_unless_allowed(content, tmp_path):
    backup = tmp_path / "empty.sieve"
    backup.write_bytes(content.encode())
    live = mxroute(sieve=FakeSieveSession(script="old\n"))

    with pytest.raises(MailctlError, match="would remove every rule"):
        utilities.backup.read_backup_file(backup)

    plan = utilities.backup.plan_restore(
        live, utilities.backup.read_backup_file(backup, allow_empty=True)
    )

    assert plan.after == content
    assert plan.changes


# ----------------------------------------------------------------------------
def test_a_rejected_restore_leaves_the_backup_and_stores_nothing(
    imap_config, tmp_path
):
    imap_config.backup_dir = tmp_path / "backups"
    backup = tmp_path / "b.sieve"
    backup.write_text("new\n")
    fake = FakeSieveSession(script="old\n")
    fake.reject = True
    live = mxroute(sieve=fake)

    with pytest.raises(MailctlError, match="rejected"):
        utilities.backup.execute_restore(
            live,
            imap_config,
            utilities.backup.plan_restore(
                live, utilities.backup.read_backup_file(backup)
            ),
        )

    assert "put_script" not in fake.names()
    assert len(list((tmp_path / "backups").iterdir())) == 1
