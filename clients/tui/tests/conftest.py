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


class Scene:
    """A scene file for the stub API, and the app started over it."""

    # ------------------------------------------------------------------------
    def __init__(self, path: Path):
        self.path = path

    # ------------------------------------------------------------------------
    def write(self, **scene) -> None:
        self.path.write_text(json.dumps(scene), encoding="utf-8")

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
