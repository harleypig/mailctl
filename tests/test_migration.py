"""What the rename to mailctl left behind, and the switch that moves it.

The rename was a clean break: the old config directory and the old
``MXROUTE_*`` setting names are not read. Two things keep that from
stranding anybody silently, and both are tested here:

* the old directory is reported -- loudly, on every command -- until the
  new one exists, and ``mailctl migrate-config`` moves its contents across,
  keeping modes and overwriting nothing;
* an old setting name present without its new one is reported by name,
  and never by value, since one of them is the password.

The Sieve script created under the old name is ``test_utilities_rules.py``'s.
"""

import argparse
import os
import stat
from pathlib import Path

import pytest

from mailctl import MailctlError, cli, utilities
from mailctl.config import (
    LEGACY_ENV_NAMES,
    Config,
    load_config,
    read_env_file,
)

# A value no output may contain. Long and odd enough that it cannot turn up
# by coincidence, so its absence means it was never rendered.
SENTINEL = "s3ntinel-VALUE-never-shown-9f2c"

# ############################################################################
# Helpers
# ############################################################################


# ----------------------------------------------------------------------------
def mode(path: Path) -> int:
    """The permission bits of ``path``."""
    return stat.S_IMODE(path.lstat().st_mode)


# ----------------------------------------------------------------------------
def planned() -> utilities.migration.ConfigMigrationPlan:
    """The migration plan, which these tests expect to exist."""
    plan = utilities.migration.plan_config_migration()

    assert plan is not None

    return plan


# ----------------------------------------------------------------------------
@pytest.fixture
def dirs(tmp_path) -> tuple[Path, Path]:
    """The old and new config directories under the test's XDG home."""
    home = Path(os.environ["XDG_CONFIG_HOME"])

    return home / "mxfilter", home / "mailctl"


# ----------------------------------------------------------------------------
@pytest.fixture
def old_setup(dirs) -> Path:
    """An old-name directory as the tool left it: private, with backups."""
    old, _new = dirs

    backups = old / "backups"
    backups.mkdir(parents=True)
    old.chmod(0o700)
    backups.chmod(0o700)

    (old / "config.toml").write_text('host = "mail.example.com"\n')
    (old / "config.toml").chmod(0o644)

    for name in ("managesieve-20260101T000000Z.sieve", "spare.sieve"):
        path = backups / name
        path.write_text("# a backup\n")
        path.chmod(0o600)

    return old


# ############################################################################
# The config-directory finding
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_old_directory_is_a_finding_while_the_new_one_is_missing(
    dirs, old_setup
):
    old, new = dirs

    assert (
        utilities.migration.check_config_dir()
        == utilities.migration.ConfigDirPending(old, new)
    )


# ----------------------------------------------------------------------------
def test_there_is_no_finding_once_the_new_directory_exists(dirs, old_setup):
    _old, new = dirs
    new.mkdir()

    assert utilities.migration.check_config_dir() is None


# ----------------------------------------------------------------------------
def test_there_is_no_finding_without_an_old_directory(dirs):
    assert utilities.migration.check_config_dir() is None


# ----------------------------------------------------------------------------
def test_every_command_warns_loudly_about_the_old_directory(
    old_setup, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "cmd_list", lambda args: 0)

    assert cli.main(["list"]) == 0

    err = capsys.readouterr().err
    assert "mailctl migrate-config" in err
    assert str(old_setup) in err
    assert "NOT read" in err


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("state", ["no-old-dir", "new-dir-exists"])
def test_the_directory_warning_is_silent_otherwise(
    state, dirs, monkeypatch, capsys
):
    old, new = dirs

    if state == "new-dir-exists":
        old.mkdir(parents=True)
        new.mkdir()

    monkeypatch.setattr(cli, "cmd_list", lambda args: 0)

    assert cli.main(["list"]) == 0
    assert capsys.readouterr().err == ""


# ############################################################################
# migrate-config
# ############################################################################


# ----------------------------------------------------------------------------
def test_migrate_moves_everything_and_keeps_modes(dirs, old_setup):
    old, new = dirs
    plan = planned()

    assert plan.conflicts == ()
    assert len(plan.files) == 3

    events = []
    utilities.migration.execute_config_migration(plan, events.append)

    assert not old.exists()
    assert (new / "config.toml").read_text() == 'host = "mail.example.com"\n'
    assert mode(new) == 0o700
    assert mode(new / "backups") == 0o700
    assert mode(new / "config.toml") == 0o644
    assert mode(new / "backups" / "spare.sieve") == 0o600
    assert mode(new / "backups/managesieve-20260101T000000Z.sieve") == 0o600
    assert (
        sum(isinstance(e, utilities.migration.FileMoved) for e in events) == 3
    )
    assert utilities.migration.OldDirRemoved(old) in events


# ----------------------------------------------------------------------------
def test_migrate_merges_into_a_new_directory_that_already_exists(
    dirs, old_setup
):
    """A backup written after upgrading, before migrating, is not a clash:
    the directories merge and only a same-named file would be refused."""
    _old, new = dirs
    (new / "backups").mkdir(parents=True)
    (new / "backups" / "mailctl-later.sieve").write_text("new\n")

    plan = planned()
    utilities.migration.execute_config_migration(plan)

    assert sorted(p.name for p in (new / "backups").iterdir()) == [
        "mailctl-later.sieve",
        "managesieve-20260101T000000Z.sieve",
        "spare.sieve",
    ]


# ----------------------------------------------------------------------------
def test_migrate_refuses_to_overwrite_anything_at_the_destination(
    dirs, old_setup
):
    old, new = dirs
    new.mkdir()
    (new / "config.toml").write_text("mine\n")

    plan = planned()

    assert plan.conflicts == (new / "config.toml",)

    with pytest.raises(MailctlError, match="refusing to migrate"):
        utilities.migration.execute_config_migration(plan)

    assert (new / "config.toml").read_text() == "mine\n"
    assert (old / "config.toml").exists()
    assert (old / "backups" / "spare.sieve").exists()


# ----------------------------------------------------------------------------
def test_migrate_with_no_old_directory_has_nothing_to_plan(dirs):
    assert utilities.migration.plan_config_migration() is None


# ----------------------------------------------------------------------------
def test_migrate_points_config_paths_at_the_new_directory(dirs, old_setup):
    """The example config puts the password file in the config directory;
    moving the file without the setting would break the next login."""
    old, new = dirs
    toml = old / "config.toml"
    toml.write_text(
        "# mine\n"
        'password_file = "~/unrelated/pw"\n'
        f"backup_dir = '{old}/backups'\n"
    )

    plan = planned()

    assert [ref.key for ref in plan.references] == ["backup_dir"]

    utilities.migration.execute_config_migration(plan)

    assert (new / "config.toml").read_text() == (
        "# mine\n"
        'password_file = "~/unrelated/pw"\n'
        f'backup_dir = "{new}/backups"\n'
    )


# ----------------------------------------------------------------------------
def test_the_cli_dry_run_lists_and_moves_nothing(old_setup, capsys):
    assert cli.main(["migrate-config", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "0600  backups/spare.sieve" in out
    assert "[dry-run] nothing was moved." in out
    assert (old_setup / "backups" / "spare.sieve").exists()


# ----------------------------------------------------------------------------
def test_the_cli_moves_with_yes(dirs, old_setup, capsys):
    _old, new = dirs

    assert cli.main(["migrate-config", "--yes"]) == 0

    assert (new / "backups" / "spare.sieve").exists()
    assert "Removed the now-empty" in capsys.readouterr().out


# ----------------------------------------------------------------------------
def test_the_cli_refuses_without_a_terminal_or_yes(old_setup, capsys):
    assert cli.main(["migrate-config"]) == 1

    assert "--yes" in capsys.readouterr().err
    assert (old_setup / "config.toml").exists()


# ----------------------------------------------------------------------------
def test_the_cli_refuses_a_clash_and_names_it(dirs, old_setup, capsys):
    _old, new = dirs
    new.mkdir()
    (new / "config.toml").write_text("mine\n")

    assert cli.main(["migrate-config", "--yes"]) == 1

    err = capsys.readouterr().err
    assert str(new / "config.toml") in err
    assert "refusing to migrate" in err


# ############################################################################
# Old setting names
# ############################################################################


# ----------------------------------------------------------------------------
def test_an_old_name_is_not_read(monkeypatch):
    monkeypatch.setenv("MXROUTE_HOST", "old.example")

    assert load_config(argparse.Namespace()).host == ""


# ----------------------------------------------------------------------------
def test_an_old_password_is_not_read():
    config = Config(environ={"MXROUTE_PASSWORD": SENTINEL})

    assert config.password_sources() == []


# ----------------------------------------------------------------------------
def test_every_old_name_is_reported_with_its_new_one():
    environ = dict.fromkeys(LEGACY_ENV_NAMES, SENTINEL)

    found = Config(environ=environ).legacy_settings()

    assert [(s.old, s.new) for s in found] == list(LEGACY_ENV_NAMES.items())
    assert SENTINEL not in repr(found)


# ----------------------------------------------------------------------------
def test_an_old_name_is_quiet_when_its_new_name_is_set():
    environ = {"MXROUTE_HOST": "a", "MAILCTL_HOST": "b"}

    assert Config(environ=environ).legacy_settings() == []


# ----------------------------------------------------------------------------
def test_an_old_name_in_the_env_file_is_reported_and_its_value_dropped(
    tmp_path,
):
    """Recorded by name only: the value is not kept on the object at all,
    and a loosely-permissioned file is not refused for an old password it
    will never use."""
    path = tmp_path / ".env"
    path.write_text(f"MXROUTE_PASSWORD={SENTINEL}\nMXROUTE_USER=x\n")
    path.chmod(0o644)

    env_file = read_env_file(path)

    assert env_file.legacy_names == {"MXROUTE_PASSWORD", "MXROUTE_USER"}
    assert env_file.password is None
    assert SENTINEL not in repr(env_file.__dict__)

    found = Config(environ={}, env_file=env_file).legacy_settings()

    assert {(s.old, s.where.path) for s in found} == {
        ("MXROUTE_PASSWORD", path),
        ("MXROUTE_USER", path),
    }


# ----------------------------------------------------------------------------
def test_an_old_name_in_the_env_file_is_quiet_when_the_new_one_is_there(
    tmp_path,
):
    path = tmp_path / ".env"
    path.write_text("MXROUTE_PASSWORD=a\nMAILCTL_PASSWORD=b\n")
    path.chmod(0o600)

    config = Config(environ={}, env_file=read_env_file(path))

    assert config.legacy_settings() == []


# ----------------------------------------------------------------------------
def test_the_cli_warning_names_old_and_new_and_never_the_value(
    tmp_path, monkeypatch, capsys
):
    for old in LEGACY_ENV_NAMES:
        monkeypatch.setenv(old, SENTINEL)

    env = tmp_path / ".env"
    env.write_text(f"MXROUTE_PASSWORD_CMD=echo {SENTINEL}\n")
    env.chmod(0o600)

    # Stop right after configure(), before any connection is attempted.
    def stop(config, args, **kwargs):
        raise MailctlError("stopped")

    monkeypatch.setattr(cli, "connect", stop)

    cli.main(["list", "--env-file", str(env)])

    captured = capsys.readouterr()
    output = captured.out + captured.err

    assert SENTINEL not in output

    for old, new in LEGACY_ENV_NAMES.items():
        assert (
            f"warning: {old} (environment) is no longer read; rename it "
            f"to {new}" in output
        )

    assert f"MXROUTE_PASSWORD_CMD (env file {env})" in output
