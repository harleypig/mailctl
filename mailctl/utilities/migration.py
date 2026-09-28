"""What the rename from ``mxfilter`` left behind, found and moved.

The old config directory and the old setting names are never read; they
are reported, and ``plan_config_migration`` / ``execute_config_migration``
move the directory's contents. An old setting is reported by name only.
"""

import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .. import MailctlError
from ..config import (
    Config,
    LegacySetting,
    config_dir,
    expand_path,
    legacy_config_dir,
)
from .events import EventSink

# ############################################################################
# The old name -- what the rename left behind
# ############################################################################


@dataclass(frozen=True)
class ConfigDirPending:
    """The old config directory is there and the new one is not.

    Nothing in ``old`` is read, so the config and the script backups in it
    are stranded until ``plan_config_migration`` moves them.
    """

    old: Path
    new: Path


@dataclass(frozen=True)
class MigrationEntry:
    """One thing in the old directory, as a path relative to it."""

    relative: Path
    mode: int
    directory: bool


@dataclass(frozen=True)
class ConfigReference:
    """A config-file path that points inside the old directory.

    ``written`` is the value as the file spells it; ``replacement`` is the
    same place under the new directory, which is what it becomes.
    """

    key: str
    written: str
    replacement: str


@dataclass(frozen=True)
class ConfigMigrationPlan:
    """What moving the old directory's contents to the new one would do.

    ``conflicts`` are destinations that already exist. Any at all and the
    plan is refused on execute: nothing at the destination is overwritten.
    """

    old: Path
    new: Path
    entries: tuple[MigrationEntry, ...]
    conflicts: tuple[Path, ...]
    references: tuple[ConfigReference, ...]

    # ------------------------------------------------------------------------
    @property
    def files(self) -> tuple[MigrationEntry, ...]:
        return tuple(entry for entry in self.entries if not entry.directory)


@dataclass(frozen=True)
class FileMoved:
    """One file (or symlink) was moved to the new directory."""

    source: Path
    destination: Path


@dataclass(frozen=True)
class ReferenceRewritten:
    """A config-file path was pointed at the new directory -- or, where
    ``rewritten`` is False, could not be found as written to change."""

    reference: ConfigReference
    rewritten: bool


@dataclass(frozen=True)
class OldDirRemoved:
    """The old directory was empty once its contents moved, and is gone."""

    path: Path


# The config-file keys that hold a path mailctl itself opens.
PATH_KEYS = ("password_file", "backup_dir")


# ----------------------------------------------------------------------------
def check_config_dir(
    old: Path | None = None, new: Path | None = None
) -> ConfigDirPending | None:
    """Report the old config directory when the new one does not exist.

    Once the new directory exists the finding stops: what is left in the
    old one is then the user's own choice, not a setup the rename broke.
    """
    old = legacy_config_dir() if old is None else old
    new = config_dir() if new is None else new

    if old.is_dir() and not os.path.lexists(new):
        return ConfigDirPending(old, new)

    return None


# ----------------------------------------------------------------------------
def check_legacy_settings(config: Config) -> list[LegacySetting]:
    """Report old ``MXROUTE_*`` names this run ignored -- names only."""
    return config.legacy_settings()


# ----------------------------------------------------------------------------
def plan_config_migration(
    old: Path | None = None, new: Path | None = None
) -> ConfigMigrationPlan | None:
    """Work out what moving the old directory's contents would do.

    Returns None when there is no old directory. Read-only: it lists what
    would move, which destinations are already taken, and which config
    paths point inside the old directory and would stop resolving.
    """
    old = legacy_config_dir() if old is None else old
    new = config_dir() if new is None else new

    if not old.is_dir():
        return None

    entries = _migration_entries(old)
    conflicts = []

    if os.path.lexists(new) and not _real_dir(new):
        conflicts.append(new)

    for entry in entries:
        destination = new / entry.relative

        if not os.path.lexists(destination):
            continue

        # A directory already there is merged into, not overwritten.
        if entry.directory and _real_dir(destination):
            continue

        conflicts.append(destination)

    return ConfigMigrationPlan(
        old=old,
        new=new,
        entries=entries,
        conflicts=tuple(conflicts),
        references=_config_references(old, new),
    )


# ----------------------------------------------------------------------------
def execute_config_migration(
    plan: ConfigMigrationPlan, on_event: EventSink | None = None
) -> None:
    """Move the planned files, keeping their modes, and tidy up after.

    Files are renamed, not copied, so each keeps its inode and its mode --
    a ``0600`` backup stays ``0600``. A directory created here takes the
    mode of the one it stands in for. A destination that appeared since
    the plan was made stops the move rather than being overwritten.
    """
    if plan.conflicts:
        raise MailctlError(
            f"refusing to migrate: {len(plan.conflicts)} path(s) already "
            f"exist in {plan.new} and would be overwritten"
        )

    emit = on_event or (lambda _event: None)

    try:
        _ensure_dir(plan.new, _mode_of(plan.old))

        for entry in plan.entries:
            if entry.directory:
                _ensure_dir(plan.new / entry.relative, entry.mode)

        for entry in plan.files:
            source = plan.old / entry.relative
            destination = plan.new / entry.relative

            if os.path.lexists(destination):
                raise MailctlError(
                    f"{destination} appeared while migrating; it was left "
                    f"alone and {source} was not moved"
                )

            os.rename(source, destination)
            emit(FileMoved(source, destination))

    except OSError as exc:
        raise MailctlError(f"could not migrate {plan.old}: {exc}") from exc

    for reference in plan.references:
        rewritten = _rewrite_reference(plan.new / "config.toml", reference)
        emit(ReferenceRewritten(reference, rewritten))

    if _remove_empty_tree(plan.old):
        emit(OldDirRemoved(plan.old))


# ----------------------------------------------------------------------------
def _real_dir(path: Path) -> bool:
    """A directory that is not a symlink to one."""
    return path.is_dir() and not path.is_symlink()


# ----------------------------------------------------------------------------
def _mode_of(path: Path) -> int:
    """The permission bits of ``path`` itself, not of a link's target."""
    return stat.S_IMODE(path.lstat().st_mode)


# ----------------------------------------------------------------------------
def _migration_entries(old: Path) -> tuple[MigrationEntry, ...]:
    """Everything under ``old``, parents before children.

    A symlink is moved as a link, never followed, so a link to a directory
    is an entry of its own rather than a tree to descend into.
    """
    entries = []

    for root, dirnames, filenames in os.walk(old):
        base = Path(root)
        dirnames.sort()

        for name in sorted(dirnames + filenames):
            path = base / name
            directory = _real_dir(path)

            entries.append(
                MigrationEntry(
                    path.relative_to(old), _mode_of(path), directory
                )
            )

    entries.sort(key=lambda entry: entry.relative.parts)

    return tuple(entries)


# ----------------------------------------------------------------------------
def _config_references(old: Path, new: Path) -> tuple[ConfigReference, ...]:
    """Find config-file paths that name a place inside the old directory.

    Only the keys that hold a path are looked at. A file that will not
    parse is moved as it is and reported by ``load_config`` when it is
    next read, so it yields nothing here.
    """
    path = old / "config.toml"

    try:
        with path.open("rb") as handle:
            values = tomllib.load(handle)

    except (OSError, tomllib.TOMLDecodeError):
        return ()

    found = []

    for key in PATH_KEYS:
        written = values.get(key)

        if not isinstance(written, str) or not written:
            continue

        expanded = expand_path(written)

        if expanded.is_relative_to(old):
            replacement = str(new / expanded.relative_to(old))
            found.append(ConfigReference(key, written, replacement))

    return tuple(found)


# ----------------------------------------------------------------------------
def _rewrite_reference(path: Path, reference: ConfigReference) -> bool:
    """Point one config-file path at the new directory, in place.

    Only the quoted value as written is replaced, so the rest of the file
    -- comments, layout -- is untouched. Writing into the existing file
    keeps its mode. Returns False when the value cannot be found as
    written (an escaped string, say), leaving the file as it was.
    """
    try:
        text = path.read_text(encoding="utf-8")

    except OSError:
        return False

    replacement = _toml_string(reference.replacement)
    updated = text

    for quote in ('"', "'"):
        updated = updated.replace(
            f"{quote}{reference.written}{quote}", replacement
        )

    if updated == text:
        return False

    try:
        with path.open("w", encoding="utf-8", newline="") as stream:
            stream.write(updated)

    except OSError:
        return False

    return True


# ----------------------------------------------------------------------------
def _toml_string(value: str) -> str:
    """Spell ``value`` as a TOML basic string."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')

    return f'"{escaped}"'


# ----------------------------------------------------------------------------
def _ensure_dir(path: Path, mode: int) -> None:
    """Create ``path`` with ``mode`` unless it is already a directory.

    An existing directory keeps the mode the user gave it.
    """
    if _real_dir(path):
        return

    path.mkdir(parents=True)
    os.chmod(path, mode)


# ----------------------------------------------------------------------------
def _remove_empty_tree(root: Path) -> bool:
    """Remove ``root`` and the directories under it, if all are empty.

    Anything still holding a file is left alone. Returns whether ``root``
    itself is gone.
    """
    for directory, _dirnames, _filenames in os.walk(root, topdown=False):
        try:
            os.rmdir(directory)

        except OSError:
            continue

    return not os.path.lexists(root)
