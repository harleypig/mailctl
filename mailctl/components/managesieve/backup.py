"""Script backups: where they go, and writing the exact bytes privately.

A backup is the server's exact bytes. Nothing here decorates, re-renders,
or translates line endings; the file is created for its owner alone.
"""

import datetime
import os
from pathlib import Path

from ... import MailctlError

__all__ = [
    "backup_path",
    "backup_script",
    "resolve_backup_target",
    "write_backup",
]


# ----------------------------------------------------------------------------
def backup_path(name: str, backup_dir: Path) -> Path:
    """Return the timestamped file a backup of ``name`` would be written to.

    Separate from writing it so that ``--dry-run`` can report the path
    without creating anything, and so both callers -- the pre-upload
    backup and the ``backup`` subcommand -- derive the same name.

    The script name comes from the server, so every character that is not
    alphanumeric, ``-``, ``_``, or ``.`` becomes an underscore. That is
    what stops a name containing ``/`` or ``..`` from writing outside the
    backup directory.
    """
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")

    safe_name = "".join(
        char if char.isalnum() or char in "-_." else "_" for char in name
    )

    return Path(backup_dir) / f"{safe_name}-{stamp}.sieve"


# ----------------------------------------------------------------------------
def resolve_backup_target(
    output: str | None, name: str, backup_dir: Path
) -> Path:
    """Decide which file a backup of ``name`` is written to.

    ``output`` is the user's ``--output``; None means the default location.
    Two shapes are accepted, and which one applies is decided in this
    order:

    1. a **directory** -- a path ending in a separator, or one that already
       exists as a directory. The timestamped default filename is written
       inside it.
    2. a **full path** -- anything else. It is written exactly as given.

    The trailing separator is what lets a caller name a directory that does
    not exist yet without it being mistaken for a filename, which is the
    only case the two rules disagree about.
    """
    if output is None:
        return backup_path(name, backup_dir)

    if output.endswith(("/", os.sep)) or Path(output).is_dir():
        return backup_path(name, Path(output))

    return Path(output)


# ----------------------------------------------------------------------------
def write_backup(text: str, target: Path) -> Path:
    """Write a script to ``target`` for the owner's eyes only.

    The file is created ``0600`` and any directory made for it ``0700``. A
    Sieve script is not a credential, but it does say who the user
    corresponds with and how they sort it, so it is not world-readable
    material either.

    The bytes are the ones handed in, exactly: ``newline=""`` turns off the
    newline translation a text-mode write would otherwise apply, so a
    script the server sent with CRLF line endings comes back byte for byte.
    A backup that is not byte-identical is not a backup.
    """
    target = Path(target)

    try:
        _make_private_dir(target.parent)

        # The opener sets the mode as the file is created, so it is never
        # briefly world-readable; the chmod afterwards covers the case
        # where the file already existed, when the create mode is ignored.
        def private(path, flags):
            return os.open(path, flags, 0o600)

        with open(
            target, "w", encoding="utf-8", newline="", opener=private
        ) as stream:
            stream.write(text)

        os.chmod(target, 0o600)

    except OSError as exc:
        raise MailctlError(
            f"could not write backup to {target}: {exc}"
        ) from exc

    return target


# ----------------------------------------------------------------------------
def _make_private_dir(directory: Path) -> None:
    """Create ``directory`` and any missing parent, mode ``0700``.

    ``mkdir(parents=True)`` applies its mode to the leaf only -- every
    parent it creates gets the process umask instead -- so the directories
    that did not exist are collected first and chmod'ed afterwards.
    Directories that were already there are left exactly as the user set
    them; this only decides the mode of what mailctl itself creates.
    """
    created = []
    probe = directory

    while not probe.exists():
        created.append(probe)
        probe = probe.parent

    directory.mkdir(parents=True, exist_ok=True)

    for made in created:
        made.chmod(0o700)


# ----------------------------------------------------------------------------
def backup_script(text: str, name: str, backup_dir: Path) -> Path:
    """Write the current script to a timestamped file and return its path.

    Taken before every upload, and by ``mailctl backup`` when no
    ``--output`` says otherwise. Restoring is then a plain ``mailctl``
    -free operation: the file is the exact bytes the server had.
    """
    return write_backup(text, backup_path(name, backup_dir))
