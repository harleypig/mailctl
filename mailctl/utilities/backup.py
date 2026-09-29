"""Backing up the active script, and restoring one from a backup file.

A backup is the server's exact bytes. A restore is the one write path that
replaces rather than merges (ADR 0005), and it still backs up what it
replaces first.
"""

from dataclasses import dataclass
from pathlib import Path

from .. import MailctlError
from ..config import Config, expand_path
from ..engine import Session
from ..providers.base import DisplayDiff, Provider
from .backup_files import write_backup
from .events import EventSink
from .scripts import activates, fetch_active, upload_script

# ############################################################################
# Backups
# ############################################################################


@dataclass(frozen=True)
class BackupPlan:
    """The active script, and where a copy of it would be written."""

    script: str
    source: str
    target: Path


# ----------------------------------------------------------------------------
def plan_backup(
    session: Session, config: Config, output: str | None = None
) -> BackupPlan:
    """Fetch the active script and resolve where its copy goes."""
    name = session.transport.active_rule_set()

    if not name:
        raise MailctlError(
            "no active script on the server, so there is nothing to back up.",
            code="no_active_script",
            fields={"operation": "list"},
        )

    source = session.transport.read_rule_set(name)
    target = session.dialect.backup_target(output, name, config.backup_dir)

    return BackupPlan(name, source, target)


# ----------------------------------------------------------------------------
def execute_backup(plan: BackupPlan) -> Path:
    """Write the server's exact bytes to the planned target."""
    return write_backup(plan.source, plan.target)


# ----------------------------------------------------------------------------
def count_rules(provider: Provider | Session, source: str) -> int | None:
    """Return how many rules a script holds, or None if it will not parse.

    A script too broken to parse is the one most worth backing up, so this
    reports rather than raises.
    """
    try:
        return len(provider.dialect.rule_names(source))

    except MailctlError:
        return None


# ############################################################################
# Restore
# ############################################################################


@dataclass(frozen=True)
class RestorePlan:
    """A backup file to be uploaded, whole, over a stored script.

    ``diff`` is a raw diff, not the normalized one a merge shows: restore
    uploads the file's exact bytes, so any difference -- formatting
    included -- is a real change and is shown as one.
    """

    source: Path
    script: str
    before: str
    after: str
    diff: DisplayDiff
    active: str | None
    activate: bool

    # ------------------------------------------------------------------------
    @property
    def changes(self) -> bool:
        return self.before != self.after


@dataclass(frozen=True)
class BackupFile:
    """A backup read from disk, exactly as written, ready to restore."""

    path: Path
    text: str


# ----------------------------------------------------------------------------
def read_backup_file(
    path: str | Path, allow_empty: bool = False
) -> BackupFile:
    """Read a backup file exactly as written, newline translation off.

    Needs no server, so a front-end calls it before connecting: a mistyped
    path or an empty file is reported without costing a login. ``~`` and
    ``$VAR`` / ``${VAR}`` are expanded, as for every other path setting.

    A file holding nothing but whitespace is refused unless
    ``allow_empty``: uploading it removes every rule, and an empty file is
    as likely to be a truncated copy or the wrong path as a deliberate
    wipe.
    """
    source = expand_path(str(path))

    try:
        with source.open(encoding="utf-8", newline="") as handle:
            text = handle.read()

    except (OSError, UnicodeDecodeError) as exc:
        raise MailctlError(f"could not read backup {source} -- {exc}") from exc

    if not text.strip() and not allow_empty:
        raise MailctlError(
            f"backup {source} is empty; restoring it would remove every "
            f"rule from the script.",
            code="empty_backup",
        )

    return BackupFile(source, text)


# ----------------------------------------------------------------------------
def plan_restore(
    session: Session,
    backup: BackupFile,
    script: str | None = None,
    activate: bool = False,
) -> RestorePlan:
    """Work out replacing a script with a backup, without doing it.

    The target is ``script``, or the active script when none is named; no
    other stored script is touched, and whether the target ends up active
    follows the same rule as every other upload (``activates``). The
    current script may be one mailctl cannot parse: restore does not
    merge, and the current bytes are backed up before anything is sent,
    so overwriting it loses nothing (ADR 0005).
    """
    source, after = backup.path, backup.text
    name, before, active = fetch_active(session, script)

    # Not a guess at a name: with nothing active there is no "the script"
    # to mean, and the recovery case is served by naming one, which is
    # then activated because nothing else runs.
    if script is None and active is None:
        raise MailctlError(
            "no active script on the server to restore over. Name the "
            "script to restore; with nothing active it is activated.",
            code="restore_needs_script",
        )

    return RestorePlan(
        source=source,
        script=name,
        before=before,
        after=after,
        diff=session.dialect.raw_diff(before, after, name),
        active=active,
        activate=activates(name, active, activate),
    )


# ----------------------------------------------------------------------------
def execute_restore(
    session: Session,
    config: Config,
    plan: RestorePlan,
    on_event: EventSink | None = None,
) -> Path | None:
    """Back up the current script, then upload the backup file over it.

    Returns the path of the backup taken, or None when the file already
    matches the server and nothing was sent.
    """
    if not plan.changes:
        return None

    return upload_script(
        session,
        config,
        plan.script,
        plan.before,
        plan.after,
        on_event,
        activate=plan.activate,
    )
