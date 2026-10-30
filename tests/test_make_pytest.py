"""The test targets run .venv's pytest, or refuse (harleypig/mailctl#262).

`test`, `testlive` and `testcontainer` ran a bare `pytest`, so they used
whichever was first on PATH: another environment's, against packages that
are not mailctl's, or none at all. The same shape harleypig/mailctl#244
fixed for `typecheck`. Each test copies the Makefile alone into a
temporary directory and runs a target there, with a stand-in `pytest` on
PATH that passes and records that it ran: the shape the bug needs.

What turns them red:

- a target back to a bare `pytest`: the refusal tests see the PATH
  stand-in run and exit 0, or no message, and the venv test sees the
  wrong marker;
- a target losing what it passes: `testlive` its `MAILCTL_LIVE=1`,
  `testcontainer` its `MAILCTL_CONTAINER=1` or `tests/container`, either
  its `-v` or `TESTARGS`: the venv test sees different arguments or
  environment;
- a target swallowing pytest's exit status: the failing stand-in test
  sees make succeed.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MAKE = shutil.which("make")

REFUSAL = ".venv/bin/pytest not found"

# target -> (the arguments its pytest gets, the gate it sets). TESTARGS is
# given to every target as `-k probe`; `test` takes none, so it must not
# pass it on.
TARGETS = {
    "test": ("", ""),
    "testlive": ("-v -k probe", "MAILCTL_LIVE=1"),
    "testcontainer": ("-v tests/container -k probe", "MAILCTL_CONTAINER=1"),
}


# ----------------------------------------------------------------------------
def _stub(path: Path, marker: Path, status: int) -> None:
    # Records its arguments and the two gates, so a target that drops
    # either is seen. Only these two variables are written: nothing else
    # from the environment is copied to disk.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        f'echo "$*" > "{marker}"\n'
        f'echo "MAILCTL_LIVE=$MAILCTL_LIVE" >> "{marker}"\n'
        f'echo "MAILCTL_CONTAINER=$MAILCTL_CONTAINER" >> "{marker}"\n'
        f"exit {status}\n"
    )
    path.chmod(0o755)


# ----------------------------------------------------------------------------
def _run(
    target: str, work: Path, tmp_path: Path
) -> subprocess.CompletedProcess[str]:
    # make is not optional here: a skip would be a test that never runs.
    assert MAKE is not None, "make is not on PATH"

    shutil.copy(ROOT / "Makefile", work / "Makefile")

    # The stand-in for a pytest from some other environment, which is what
    # ran before the fix.
    path_bin = tmp_path / "path-bin"
    _stub(path_bin / "pytest", tmp_path / "path-pytest-ran", 0)

    # The gates are cleared so only the target can set them: a run of this
    # suite under `make testlive` would otherwise hand MAILCTL_LIVE down.
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"MAILCTL_LIVE", "MAILCTL_CONTAINER", "TESTARGS"}
    }
    env["PATH"] = f"{path_bin}{os.pathsep}{os.environ['PATH']}"

    return subprocess.run(
        [MAKE, "--no-print-directory", target, "TESTARGS=-k probe"],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("target", TARGETS)
def test_refused_without_venv(target: str, tmp_path: Path) -> None:
    work = tmp_path / "repo"
    work.mkdir()

    result = _run(target, work, tmp_path)

    assert result.returncode != 0
    assert REFUSAL in result.stderr
    assert "make install" in result.stderr
    assert not (tmp_path / "path-pytest-ran").exists()


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("target", TARGETS)
def test_refused_with_venv_lacking_pytest(target: str, tmp_path: Path) -> None:
    # `uv venv` alone, before `make install`: the directory exists and the
    # dev tools do not.
    work = tmp_path / "repo"
    (work / ".venv" / "bin").mkdir(parents=True)

    result = _run(target, work, tmp_path)

    assert result.returncode != 0
    assert REFUSAL in result.stderr
    assert not (tmp_path / "path-pytest-ran").exists()


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("target", TARGETS)
def test_runs_the_venv_pytest(target: str, tmp_path: Path) -> None:
    work = tmp_path / "repo"
    marker = tmp_path / "venv-pytest-ran"
    _stub(work / ".venv" / "bin" / "pytest", marker, 0)

    result = _run(target, work, tmp_path)

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "path-pytest-ran").exists()

    args, gate = TARGETS[target]
    recorded = marker.read_text().splitlines()

    assert recorded[0].strip() == args
    assert [line for line in recorded[1:] if line.endswith("=1")] == (
        [gate] if gate else []
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("target", TARGETS)
def test_pytest_failure_fails_the_target(target: str, tmp_path: Path) -> None:
    work = tmp_path / "repo"
    marker = tmp_path / "venv-pytest-ran"
    _stub(work / ".venv" / "bin" / "pytest", marker, 1)

    result = _run(target, work, tmp_path)

    assert result.returncode != 0
    assert marker.exists()
