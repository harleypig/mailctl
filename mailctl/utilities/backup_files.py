"""Backup files on disk: the exact bytes, written for their owner alone.

Writing a local file is not server communication and belongs to no host,
so it is here rather than in a provider. Where a backup goes is the
dialect's (``Dialect.backup_path`` / ``backup_target``); reading the bytes
from the server is the transport's.
"""

import os
from pathlib import Path

from .. import MailctlError

__all__ = ["write_backup"]


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
