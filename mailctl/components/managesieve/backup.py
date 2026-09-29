"""Script backups: where a copy of a script goes.

A backup is the server's exact bytes, written as they came; writing the
file is the caller's, since it is local work and not the protocol's.
"""

import datetime
import os
from pathlib import Path

__all__ = [
    "backup_path",
    "resolve_backup_target",
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
