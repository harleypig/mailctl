"""Backup files on disk: the exact bytes, written for their owner alone.

Writing a local file is not server communication and belongs to no host,
so it is here rather than in a provider. A saved baseline is written the
same way (``write_private``). Where a backup goes is the
dialect's (``Dialect.backup_path`` / ``backup_target``); reading the bytes
from the server is the transport's.
"""

import os
from pathlib import Path

from .. import MailctlError

__all__ = ["FileTaken", "write_backup", "write_private"]


class FileTaken(Exception):
    """An exclusive write found a file already at its target."""


# ----------------------------------------------------------------------------
def write_backup(text: str, target: Path, numbered: bool = False) -> Path:
    """Write a script to a new file at ``target`` for the owner's eyes only.

    Returns the file written. A backup never replaces a file: one already
    at ``target`` is refused, or, with ``numbered``, the first free name of
    ``<stem>-2<suffix>``, ``<stem>-3<suffix>``, ... is taken instead. Pass
    ``numbered`` for a name mailctl chose, never for one the user named.
    Each name is created exclusively, so a file that appears between a
    check and the write is never truncated.

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
    candidate, number = target, 1

    while True:
        try:
            return write_private(text, candidate, "backup", exclusive=True)

        except FileTaken:
            if not numbered:
                raise MailctlError(
                    f"{candidate} already exists, and a backup never "
                    f"replaces a file."
                ) from None

        number += 1
        candidate = target.with_name(f"{target.stem}-{number}{target.suffix}")


# ----------------------------------------------------------------------------
def write_private(
    text: str, target: Path, what: str, exclusive: bool = False
) -> Path:
    """Write ``text`` to ``target`` exactly, mode ``0600``, in ``0700``.

    ``what`` names the file in the error a failed write raises. Without
    ``exclusive`` a file already at ``target`` is replaced; with it,
    ``FileTaken`` is raised and the file is left as it was.
    """
    target = Path(target)
    mode = "x" if exclusive else "w"

    try:
        _make_private_dir(target.parent)

        # The opener sets the mode as the file is created, so it is never
        # briefly world-readable; the chmod afterwards covers the case
        # where the file already existed, when the create mode is ignored.
        def private(path, flags):
            return os.open(path, flags, 0o600)

        with open(
            target, mode, encoding="utf-8", newline="", opener=private
        ) as stream:
            stream.write(text)

        os.chmod(target, 0o600)

    except OSError as exc:
        # The directory step raises FileExistsError too, for a parent that
        # is a file: a failed write, not a taken name.
        taken = (
            isinstance(exc, FileExistsError)
            and exc.filename is not None
            and Path(exc.filename) == target
        )

        if exclusive and taken:
            raise FileTaken(target) from None

        raise MailctlError(
            f"could not write {what} to {target}: {exc}"
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
