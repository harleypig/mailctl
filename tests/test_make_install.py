"""`make install` works whether or not .venv exists (harleypig/mailctl#262).

`install` depends on `venv`, which ran a bare `uv venv`. uv refuses that
over an existing .venv ("A virtual environment already exists", exit 2,
seen with uv 0.13.6), so `make install` failed in exactly the checkout
`make typecheck` sends people to run it in: a .venv without the dev
extra. Each test copies the Makefile alone into a temporary directory and
runs the target there, with a stand-in `uv` on PATH that logs every call
and refuses `uv venv` over an existing .venv the way the real one does.

What turns them red:

- `venv` back to a bare `uv venv`: the existing-.venv test sees the
  stand-in refuse, make fail, and no `uv pip install` run;
- `venv` skipping `uv venv` unconditionally: the fresh-checkout test sees
  no `uv venv` in the log and no .venv made;
- `install` swallowing the install's exit status: the failing-install
  test sees make succeed.

Not covered here: that the real uv still refuses, which is why the
stand-in copies it. That was seen by hand on the PR; a real `uv venv`
would make this tier depend on an interpreter uv can find.
"""

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAKE = shutil.which("make")

# The stand-in's refusal mirrors uv 0.13.6's own wording and exit status.
UV_STUB = """#!/bin/sh
echo "$*" >> "{log}"
if [ "$1" = venv ]; then
  if [ -e .venv ]; then
    echo "error: A virtual environment already exists at: .venv" >&2
    exit 2
  fi
  mkdir -p .venv/bin
  exit 0
fi
exit {pip_status}
"""


# ----------------------------------------------------------------------------
def _install(
    work: Path, tmp_path: Path, pip_status: int = 0
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    # make is not optional here: a skip would be a test that never runs.
    assert MAKE is not None, "make is not on PATH"

    shutil.copy(ROOT / "Makefile", work / "Makefile")

    log = tmp_path / "uv-calls"
    path_bin = tmp_path / "path-bin"
    path_bin.mkdir()
    uv = path_bin / "uv"
    uv.write_text(UV_STUB.format(log=log, pip_status=pip_status))
    uv.chmod(0o755)

    env = {**os.environ, "PATH": f"{path_bin}{os.pathsep}{os.environ['PATH']}"}

    result = subprocess.run(
        [MAKE, "--no-print-directory", "install"],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    calls = log.read_text().splitlines() if log.exists() else []

    return result, calls


# ----------------------------------------------------------------------------
def test_install_over_an_existing_venv(tmp_path: Path) -> None:
    # A .venv without the dev extra: what `make typecheck` refuses, naming
    # `make install` as the fix.
    work = tmp_path / "repo"
    (work / ".venv" / "bin").mkdir(parents=True)

    result, calls = _install(work, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "pip install -e .[dev]" in calls
    assert not any(call.startswith("venv") for call in calls)


# ----------------------------------------------------------------------------
def test_install_in_a_fresh_checkout(tmp_path: Path) -> None:
    work = tmp_path / "repo"
    work.mkdir()

    result, calls = _install(work, tmp_path)

    assert result.returncode == 0, result.stderr
    assert calls == ["venv", "pip install -e .[dev]"]
    assert (work / ".venv").is_dir()


# ----------------------------------------------------------------------------
def test_install_failure_fails_the_target(tmp_path: Path) -> None:
    work = tmp_path / "repo"
    work.mkdir()

    result, calls = _install(work, tmp_path, pip_status=1)

    assert result.returncode != 0
    assert "pip install -e .[dev]" in calls
