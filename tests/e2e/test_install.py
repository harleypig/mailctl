"""#421: install from a clone, on each route, as the README says to.

The first use case, and the shape every later one takes: one test, run
once per route by the ``installed`` fixture, that walks #421's table in
order and stops at the first step that does not match.

- **pipx** and **uv**: the install finishes (the fixture), ``mailctl``,
  ``mailctl-api``, and ``mailctl-tui`` are on ``PATH`` and are the ones
  this run installed, ``mailctl --version`` names the version in
  ``pyproject.toml``, and ``mailctl-tui --help`` prints a usage message.
- **Docker**: the image builds (the fixture) and exists, and the same two
  commands answer through it.

Red under: a broken install (the fixture fails, and the test errors in
setup); a console script dropped from either ``pyproject.toml`` (that
command is not on the sandbox's ``PATH``); a version ``mailctl`` reports
that is not the one in ``pyproject.toml``; and a TUI that does not start.
Each route was seen red by breaking its install (#483).
"""

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
    "version"
]


# ----------------------------------------------------------------------------
def test_install_from_a_clone(installed):
    if installed.image is not None:
        probe = subprocess.run(
            ["docker", "image", "inspect", installed.image],
            capture_output=True,
            check=False,
        )

        assert probe.returncode == 0, f"no image {installed.image}"

    else:
        for command in ("mailctl", "mailctl-api", "mailctl-tui"):
            found = shutil.which(command, path=installed.path)

            assert found is not None, f"{command} is not on PATH"

            # The sandbox's own, not one found further along PATH.
            assert Path(found).parent == installed.bin_dir, found

    version = installed.run("mailctl", "--version")

    assert (version.code, version.out) == (0, f"mailctl {VERSION}\n"), (
        version.err
    )

    tui = installed.run("mailctl-tui", "--help")

    assert tui.code == 0, tui.err
    assert tui.out.startswith("usage: mailctl-tui"), tui.out
