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
from mailctl.config import Config, read_env_file
from mailctl.providers.mxroute import MXROUTE

# A run with no password file and no env file configured, so no backup
# file is refused as a credential.
NO_CREDENTIALS = Config(environ={})

SENTINEL = "sentinel-Pa55word-backup-41d"

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
        live, utilities.backup.read_backup_file(backup, NO_CREDENTIALS)
    )

    assert plan.changes
    assert plan.after == 'require "fileinto";\r\n# rule:[a]\r\n'
    assert "-current" in plan.diff.text
    assert fake.names() == ["get_script"]

    events = []
    path = utilities.backup.execute_restore(
        live, imap_config, plan, events.append
    )

    assert path is not None
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
    fake = FakeSieveSession(script="if {{{ broken")
    live = mxroute(sieve=fake)

    utilities.backup.execute_restore(
        live,
        imap_config,
        utilities.backup.plan_restore(
            live, utilities.backup.read_backup_file(backup, NO_CREDENTIALS)
        ),
    )

    assert "put_script" in fake.names()


# ----------------------------------------------------------------------------
def test_restoring_an_identical_file_sends_nothing(imap_config, tmp_path):
    backup = tmp_path / "same.sieve"
    backup.write_text("same\n")
    fake = FakeSieveSession(script="same\n")
    live = mxroute(sieve=fake)

    plan = utilities.backup.plan_restore(
        live, utilities.backup.read_backup_file(backup, NO_CREDENTIALS)
    )

    assert not plan.changes
    assert utilities.backup.execute_restore(live, imap_config, plan) is None
    assert "put_script" not in fake.names()


# ----------------------------------------------------------------------------
def test_a_restore_needs_a_readable_file(tmp_path):
    with pytest.raises(MailctlError, match="could not read backup"):
        utilities.backup.read_backup_file(tmp_path / "no", NO_CREDENTIALS)


# ----------------------------------------------------------------------------
def test_with_nothing_active_a_restore_asks_for_script(tmp_path):
    backup = tmp_path / "b.sieve"
    backup.write_text("x")
    live = mxroute(sieve=FakeSieveSession(active=None))

    with pytest.raises(MailctlError, match=r"no active filter set.*Name the"):
        utilities.backup.plan_restore(
            live, utilities.backup.read_backup_file(backup, NO_CREDENTIALS)
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
        live,
        utilities.backup.read_backup_file(backup, NO_CREDENTIALS),
        script="spare",
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
            utilities.backup.read_backup_file(spelled, NO_CREDENTIALS).path
            == tmp_path / "b.sieve"
        )

    assert (
        utilities.backup.read_backup_file(
            "${MAILCTL_TEST_DIR}/b.sieve", NO_CREDENTIALS
        ).text
        == "x"
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("expand", [True, False])
@pytest.mark.parametrize(
    "spelled", ["$MAILCTL_TEST_SECRET", "${MAILCTL_TEST_SECRET}/b.sieve"]
)
def test_a_backup_that_cannot_be_read_is_named_as_given(
    monkeypatch, tmp_path, spelled, expand
):
    """Red if the message quotes the path expanded: the variable's value
    is the password's stand-in, and with ``expand`` False, as the API
    calls it, nothing is expanded at all."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAILCTL_TEST_SECRET", SENTINEL)

    with pytest.raises(MailctlError) as caught:
        utilities.backup.read_backup_file(
            spelled, NO_CREDENTIALS, expand=expand
        )

    assert str(caught.value).startswith(f"could not read backup {spelled} --")
    assert SENTINEL not in str(caught.value)

    # Where the variable names a real file, only an expanding caller
    # reads it.
    (tmp_path / SENTINEL).mkdir()
    (tmp_path / SENTINEL / "b.sieve").write_text("x")
    named = "${MAILCTL_TEST_SECRET}/b.sieve"

    if expand:
        assert (
            utilities.backup.read_backup_file(named, NO_CREDENTIALS).text
            == "x"
        )

    else:
        with pytest.raises(MailctlError, match="could not read backup"):
            utilities.backup.read_backup_file(
                named, NO_CREDENTIALS, expand=False
            )


# ----------------------------------------------------------------------------
def credentials(tmp_path):
    """A run whose password file and env file both hold the sentinel,
    each made unreadable so that opening one fails differently."""
    secret = tmp_path / "password"
    secret.write_text(SENTINEL)
    env_path = tmp_path / "mailctl.env"
    env_path.write_text(f"MAILCTL_PASSWORD={SENTINEL}\n")
    env_path.chmod(0o600)
    config = Config(
        environ={"MAILCTL_PASSWORD_FILE": str(secret)},
        env_file=read_env_file(env_path),
    )

    secret.chmod(0)
    env_path.chmod(0)
    (tmp_path / "link").symlink_to(secret)

    return config


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["password", "link", "mailctl.env"])
def test_a_credential_file_is_refused_unopened_as_a_backup(tmp_path, name):
    """Red if a restore reads a file the password comes from: its bytes
    would come back as the diff. The file is unreadable, so a read would
    fail as ``could not read`` rather than with this code."""
    config = credentials(tmp_path)

    for expand in (True, False):
        with pytest.raises(MailctlError) as caught:
            utilities.backup.read_backup_file(
                tmp_path / name, config, allow_empty=True, expand=expand
            )

        assert caught.value.code == "credential_file"
        assert SENTINEL not in str(caught.value)


# ----------------------------------------------------------------------------
def test_a_password_file_from_any_rung_is_refused(tmp_path):
    """Every configured password file is one, not only the winning rung:
    the flag's file here outranks the environment's, and both refuse."""
    flag, ambient = tmp_path / "flag", tmp_path / "ambient"
    config = Config(
        password_file=str(flag),
        environ={"MAILCTL_PASSWORD_FILE": str(ambient)},
    )

    for path in (flag, ambient):
        with pytest.raises(MailctlError) as caught:
            utilities.backup.read_backup_file(path, config)

        assert caught.value.code == "credential_file"


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
        live,
        utilities.backup.read_backup_file(backup, NO_CREDENTIALS),
        script="spare",
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

    with pytest.raises(MailctlError, match="would remove every filter"):
        utilities.backup.read_backup_file(backup, NO_CREDENTIALS)

    plan = utilities.backup.plan_restore(
        live,
        utilities.backup.read_backup_file(
            backup, NO_CREDENTIALS, allow_empty=True
        ),
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
                live, utilities.backup.read_backup_file(backup, NO_CREDENTIALS)
            ),
        )

    assert "put_script" not in fake.names()
    assert len(list((tmp_path / "backups").iterdir())) == 1


# ############################################################################
# The backup root (#230)
# ############################################################################


# ----------------------------------------------------------------------------
def test_an_output_outside_the_root_is_refused_before_the_server_is_asked(
    imap_config, tmp_path
):
    imap_config.backup_root = tmp_path / "root"
    fake = FakeSieveSession(script="current\n")

    with pytest.raises(MailctlError) as caught:
        utilities.backup.plan_backup(
            mxroute(sieve=fake), imap_config, str(tmp_path / "out.sieve")
        )

    assert caught.value.code == "backup_outside_root"
    assert caught.value.fields["setting"] == "output"
    assert fake.calls == []


# ----------------------------------------------------------------------------
def test_an_output_inside_the_root_is_written(imap_config, tmp_path):
    imap_config.backup_root = tmp_path
    live = mxroute(sieve=FakeSieveSession(script="current\n"))

    plan = utilities.backup.plan_backup(live, imap_config, f"{tmp_path}/")

    assert utilities.backup.execute_backup(plan).parent == tmp_path


# ----------------------------------------------------------------------------
def test_an_upload_refuses_a_backup_outside_the_root_and_sends_nothing(
    imap_config, tmp_path
):
    """The utilities' own check, for a Config built without load_config."""
    imap_config.backup_root = tmp_path / "root"
    imap_config.backup_dir = tmp_path / "elsewhere"
    backup = tmp_path / "b.sieve"
    backup.write_text("new\n")
    fake = FakeSieveSession(script="old\n")
    live = mxroute(sieve=fake)

    plan = utilities.backup.plan_restore(
        live, utilities.backup.read_backup_file(backup, NO_CREDENTIALS)
    )

    with pytest.raises(MailctlError) as caught:
        utilities.backup.execute_restore(live, imap_config, plan)

    assert caught.value.fields["setting"] == "backup_dir"
    assert fake.names() == ["get_script"]
    assert not (tmp_path / "elsewhere").exists()
