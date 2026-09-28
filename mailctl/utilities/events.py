"""The steps of a change, reported to a front-end as they happen.

An execute step takes an optional ``on_event`` sink and calls it with one
of these records as each step lands, so a front-end can show progress
without the utilities writing anything themselves.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..providers.base import FolderCreation

# ############################################################################
# Events -- the steps of a change, reported as they happen
# ############################################################################


@dataclass(frozen=True)
class ScriptBackedUp:
    """The server's copy was saved; sent before anything is uploaded."""

    script: str
    path: Path


@dataclass(frozen=True)
class ScriptUploaded:
    """The new script was validated and uploaded, and maybe activated."""

    script: str
    activated: bool


@dataclass(frozen=True)
class FolderCreated:
    """A planned folder was created over IMAP, on execute."""

    result: FolderCreation


EventSink = Callable[[object], None]
