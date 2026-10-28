"""`make typecheck` refuses to run without the repo's .venv (#244).

[tool.pyright] resolves imports from .venv. Without one, pyright warns that
the venv was not found, falls back to whatever Python is on PATH, and exits
0 when that Python happens to satisfy the imports, so a bare `pyright` in
the target read as a pass. Each test copies the Makefile alone into a
temporary directory and runs the target there, with a stand-in `pyright` on
PATH that passes and records that it ran: the shape the bug needs.

What turns them red:

- the target's guard removed, or the target back to a bare `pyright`: the
  refusal tests see the PATH stand-in run and exit 0, or no message;
- the target running PATH's pyright instead of .venv's: the stand-in test
  sees the wrong marker;
- the target swallowing pyright's exit status: the failing stand-in test
  sees make succeed.

Not covered here: that pyright, once .venv is found, reports a dependency
missing from it as an error. That is pyright's reportMissingImports, seen
by hand on the PR, and a real pyright run would cost this tier ten seconds.
"""

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAKE = shutil.which("make")

REFUSAL = ".venv/bin/pyright not found"


# ----------------------------------------------------------------------------
def _stub(path: Path, marker: Path, status: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\necho ran > "{marker}"\nexit {status}\n')
    path.chmod(0o755)


# ----------------------------------------------------------------------------
def _typecheck(work: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    # make is not optional here: a skip would be a test that never runs.
    assert MAKE is not None, "make is not on PATH"

    shutil.copy(ROOT / "Makefile", work / "Makefile")

    # The stand-in for a pyright from some other environment, which is
    # what passed before the fix.
    path_bin = tmp_path / "path-bin"
    _stub(path_bin / "pyright", tmp_path / "path-pyright-ran", 0)

    env = {**os.environ, "PATH": f"{path_bin}{os.pathsep}{os.environ['PATH']}"}

    return subprocess.run(
        [MAKE, "--no-print-directory", "typecheck"],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


# ----------------------------------------------------------------------------
def test_refused_without_venv(tmp_path: Path) -> None:
    work = tmp_path / "repo"
    work.mkdir()

    result = _typecheck(work, tmp_path)

    assert result.returncode != 0
    assert REFUSAL in result.stderr
    assert "make install" in result.stderr
    assert not (tmp_path / "path-pyright-ran").exists()


# ----------------------------------------------------------------------------
def test_refused_with_venv_lacking_pyright(tmp_path: Path) -> None:
    # `uv venv` alone, before `make install`: the directory exists and the
    # dev tools do not.
    work = tmp_path / "repo"
    (work / ".venv" / "bin").mkdir(parents=True)

    result = _typecheck(work, tmp_path)

    assert result.returncode != 0
    assert REFUSAL in result.stderr
    assert not (tmp_path / "path-pyright-ran").exists()


# ----------------------------------------------------------------------------
def test_runs_the_venv_pyright(tmp_path: Path) -> None:
    work = tmp_path / "repo"
    _stub(work / ".venv" / "bin" / "pyright", tmp_path / "venv-pyright-ran", 0)

    result = _typecheck(work, tmp_path)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "venv-pyright-ran").exists()
    assert not (tmp_path / "path-pyright-ran").exists()


# ----------------------------------------------------------------------------
def test_pyright_failure_fails_the_target(tmp_path: Path) -> None:
    work = tmp_path / "repo"
    _stub(work / ".venv" / "bin" / "pyright", tmp_path / "venv-pyright-ran", 1)

    result = _typecheck(work, tmp_path)

    assert result.returncode != 0
    assert (tmp_path / "venv-pyright-ran").exists()
