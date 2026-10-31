"""Shared fixtures: the TUI started against the stub API, or the real one.

``REPO_TESTS`` is mailctl's own test tree. Its ``api_child.py`` is the real
API with the two servers replaced by doubles, so a test started against it
crosses the same boundary a user's TUI does.
"""

import hashlib
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from mailctl_tui.app import MailctlApp

HERE = Path(__file__).parent
STUB = HERE / "stub_api.py"
REPO_TESTS = HERE.parents[2] / "tests"
API_CHILD = REPO_TESTS / "api_child.py"

# A terminal tall and wide enough that no row or footer key is cut off.
SIZE = (120, 40)

FILTERS = {
    "version": 2,
    "filterset": "managesieve",
    "filters": [
        {
            "position": 1,
            "name": "all-lists",
            "disabled": False,
            "stops": True,
            "combinator": "anyof",
            "tests": [
                {
                    "header": "To",
                    "match_type": "contains",
                    "keys": ["@lists.example.com"],
                    "comparator": "i;ascii-casemap",
                }
            ],
            "actions": ["fileinto", "stop"],
            "steps": [
                {"kind": "file", "argument": None, "creates": False},
                {"kind": "stop", "argument": None, "creates": False},
            ],
            "unmodelled": [],
        },
        {
            "position": 2,
            "name": "announce",
            "disabled": True,
            "stops": False,
            "combinator": "allof",
            "tests": [
                {
                    "header": "To",
                    "match_type": "contains",
                    "keys": ["announce@lists.example.com"],
                    "comparator": "i;ascii-casemap",
                },
                {
                    "header": "Subject",
                    "match_type": "is",
                    "keys": ["news", "update"],
                    "comparator": "i;ascii-casemap",
                },
            ],
            "actions": ["fileinto"],
            "steps": [{"kind": "file", "argument": None, "creates": False}],
            "unmodelled": [],
        },
    ],
    "findings": [
        {
            "certainty": "certain",
            "broad": "all-lists",
            "narrow": "announce",
            "reason": "'all-lists' carries stop and its conditions cover "
            "every case 'announce' tests for",
        }
    ],
}


# Every capability the add form gates on, declared, as mxroute does.
CAPABILITIES = {
    "ordering": True,
    "stop": True,
    "rule_sets": True,
    "folder_visibility": True,
    "actions": ["discard", "fileinto", "flag", "keep"],
}

LISTING = {
    "version": 2,
    # Typed as "Lists"; the API names the folder it searched.
    "folder": "INBOX.Lists",
    "uidvalidity": 1700,
    "more": False,
    "sort": None,
    "messages": [
        {
            "uid": 41,
            "received": "2026-10-29T08:15:02",
            "size": 2048,
            "flags": [],
            "has_attachments": True,
            "from": "GitHub <noreply@github.com>",
            "subject": "[repo] New issue",
            "folder": "INBOX.Lists",
        },
        {
            "uid": 40,
            "received": "2026-10-28T19:01:44",
            "size": 900,
            "flags": ["\\Seen", "\\Flagged"],
            "has_attachments": False,
            "from": "Boss <boss@example.com>",
            "subject": "Lunch",
            "folder": "INBOX.Lists",
        },
    ],
}

MESSAGE = {
    "version": 2,
    "message": {
        "uid": 41,
        "folder": "INBOX.Lists",
        "uidvalidity": 1700,
        "size": 2048,
        "flags": [],
        "headers": [
            {"name": "From", "value": "GitHub <noreply@github.com>"},
            {"name": "List-Id", "value": "<repo.github.com>"},
            {"name": "Subject", "value": "[repo] New issue"},
        ],
        "body": "First line\r\nSecond [bold]line[/]\x1b[2J\n\tindented",
        "body_from_html": False,
        "attachments": [
            {"name": "log.txt", "content_type": "text/plain", "size": 12}
        ],
    },
}

LIKE = {
    "version": 1,
    "criteria": {
        "match": "any",
        "compare": "contains",
        "terms": [
            {"header": "List-Id", "value": "repo.github.com"},
            {"header": "X-GitHub-Reason", "value": "subscribed"},
        ],
    },
}

PLAN = {
    "version": 2,
    "plan": {
        "command": "filter add",
        "changes": True,
        "diff": {
            "label": "sieve",
            "text": "+# rule:[github]\n+if header :contains ...\x1b[31m",
            "reformats": False,
        },
        "filter": "github",
        "criteria": LIKE["criteria"],
        "actions": {
            "fileinto": "INBOX.GitHub",
            "discard": False,
            "flags": ["\\Seen"],
            "keep": False,
            "stop": None,
        },
        "folder": {
            "requested": "GitHub",
            "folder": "INBOX.GitHub",
            "delimiter": ".",
            "delimiter_assumed": False,
            "status": "create",
            "subscribe": True,
            "case_variants": [],
            "mailbox_disabled_by": None,
        },
        "missing_extensions": [],
        "placement": {
            "dead_on_arrival": [
                {
                    "certainty": "certain",
                    "broad": "all-lists",
                    "narrow": "github",
                    "reason": "'all-lists' carries stop",
                }
            ],
            "starves": [],
        },
        "filterset": "managesieve",
        "active": "managesieve",
        "activate": True,
    },
}

EVENTS = [
    {
        "version": 2,
        "event": {
            "kind": "filterset_backed_up",
            "filterset": "managesieve",
            "path": "/backups/managesieve-1.sieve",
        },
    },
    {
        "version": 2,
        "event": {
            "kind": "folder_created",
            "folder": "INBOX.GitHub",
            "subscribed": True,
            "subscribe_error": None,
            "by_subscription": True,
        },
    },
    {
        "version": 2,
        "event": {
            "kind": "filterset_uploaded",
            "filterset": "managesieve",
            "activated": False,
        },
    },
]

RESULT = {
    "version": 2,
    "result": {
        "command": "filter add",
        "filter": "github",
        "filterset": "managesieve",
        "activated": True,
        "backup": "/backups/managesieve-1.sieve",
    },
}


class Scene:
    """A scene file for the stub API, and the app started over it."""

    # ------------------------------------------------------------------------
    def __init__(self, path: Path):
        self.path = path

    # ------------------------------------------------------------------------
    def write(self, **scene) -> None:
        self.path.write_text(json.dumps(scene), encoding="utf-8")

    # ------------------------------------------------------------------------
    def flow(self, **changes) -> None:
        """A scene for the whole first scope: every document above."""
        self.write(
            **{
                "capabilities": CAPABILITIES,
                "filters": FILTERS,
                "listing": LISTING,
                "message": MESSAGE,
                "like": LIKE,
                "plan": PLAN,
                "events": EVENTS,
                "result": RESULT,
                **changes,
            }
        )

    # ------------------------------------------------------------------------
    def params(self, method: str) -> list[dict]:
        """The parameters of every ``method`` request, in order."""
        return self.seen().get("params", {}).get(method, [])

    # ------------------------------------------------------------------------
    def seen(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8")).get(
            "seen", {}
        )

    # ------------------------------------------------------------------------
    def app(self, app_class: type[MailctlApp] = MailctlApp) -> MailctlApp:
        return app_class(
            [sys.executable, str(STUB)],
            env={**os.environ, "STUB_SCENE": str(self.path)},
        )


# ----------------------------------------------------------------------------
@pytest.fixture
def scene(tmp_path) -> Scene:
    return Scene(tmp_path / "scene.json")


# ----------------------------------------------------------------------------
def digest(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


# ----------------------------------------------------------------------------
async def settle(pilot, until: Callable[[], bool], tries: int = 200) -> None:
    """Let the app work until ``until`` holds; the API is another process,
    so its answers arrive when they arrive. Fails rather than hangs."""
    for _ in range(tries):
        if until():
            return

        await pilot.pause(0.05)

    raise AssertionError("the app never reached the expected state")


# ----------------------------------------------------------------------------
async def reach(pilot, app, screen_class: type) -> None:
    """Let the app work until a ``screen_class`` is the screen and has
    mounted (#348). A pushed screen is on the stack before it mounts, so
    ``isinstance`` alone can hold while it has no children and no focus:
    a query then finds nothing, and a key pressed then reaches no widget
    and is lost for good. Once it has mounted, the focus its ``on_mount``
    asked for is queued ahead of any key the test presses next."""
    await settle(
        pilot,
        lambda: isinstance(app.screen, screen_class) and app.screen.is_mounted,
    )
