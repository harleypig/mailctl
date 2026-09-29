"""The steps of a change, reported to a front-end as they happen.

An execute step takes an optional ``on_event`` sink and calls it with one
of these records as each step lands, so a front-end can show progress
without the utilities writing anything themselves.

``ServerAlert`` is the other thing a front-end is told as it happens: an
alert or a warning the server sent, which arrives as a message on the session's
``progress`` callback rather than here, since any command can meet one.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..providers.base import FolderCreation, ServerAlert

__all__ = [
    "EventSink",
    "FolderCreated",
    "FolderRenamed",
    "ScriptBackedUp",
    "ScriptUploaded",
    "ServerAlert",
    "SubscriptionChanged",
]

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


@dataclass(frozen=True)
class FolderRenamed:
    """A folder was renamed over IMAP, with ``children`` folders under it;
    their subscriptions are fixed next."""

    old: str
    new: str
    children: int


@dataclass(frozen=True)
class SubscriptionChanged:
    """A folder was subscribed to, or dropped from the subscription list,
    as part of a larger change."""

    folder: str
    subscribed: bool


EventSink = Callable[[object], None]
