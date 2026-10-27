"""A built wheel holds the whole package and runs (#229).

An editable install, which the suite and CI both run under, reads the
package from the checkout, so a wheel missing a subpackage passes every
other test. This builds a wheel from a copy of the source with the declared
build backend, unpacks it, and runs it with nothing from the checkout on
the path.
"""

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import imapclient
import sievelib

import mailctl


# ----------------------------------------------------------------------------
def _directory(module: ModuleType) -> Path:
    assert module.__file__ is not None
    return Path(module.__file__).resolve().parent


ROOT = Path(__file__).resolve().parent.parent
PACKAGE = _directory(mailctl)


# ----------------------------------------------------------------------------
def _packages(top: Path) -> set[str]:
    return {
        ".".join(init.parent.relative_to(top.parent).parts)
        for init in top.rglob("__init__.py")
    }


# ----------------------------------------------------------------------------
def _build_wheel(tmp_path: Path) -> Path:
    # A copy, because the backend writes build/ and *.egg-info beside the
    # source it is given.
    source = tmp_path / "source"
    source.mkdir()
    shutil.copy(ROOT / "pyproject.toml", source)
    shutil.copy(ROOT / "README.md", source)
    shutil.copytree(
        PACKAGE,
        source / "mailctl",
        ignore=shutil.ignore_patterns("__pycache__"),
    )

    dist = tmp_path / "dist"
    dist.mkdir()

    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from setuptools import build_meta;"
            " build_meta.build_wheel(sys.argv[1])",
            str(dist),
        ],
        cwd=source,
        check=True,
        capture_output=True,
    )

    (wheel,) = dist.glob("mailctl-*.whl")
    return wheel


# ----------------------------------------------------------------------------
def test_wheel_holds_every_package_and_runs(tmp_path):
    wheel = _build_wheel(tmp_path)

    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(tmp_path / "installed")

    installed = tmp_path / "installed"

    assert _packages(installed / "mailctl") == _packages(PACKAGE)

    # -S skips site-packages and the .pth file the editable install left
    # there, so mailctl can come only from the wheel. The two runtime
    # dependencies are put back by hand, after it.
    dependencies = {
        str(_directory(module).parent) for module in (imapclient, sievelib)
    }
    path = os.pathsep.join([str(installed), *sorted(dependencies)])

    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import sys, mailctl, mailctl.cli;"
            " print(mailctl.__file__);"
            " sys.exit(mailctl.cli.main(['--version']))",
        ],
        cwd=tmp_path,
        env={"PYTHONPATH": path, "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    where, version = result.stdout.splitlines()

    assert Path(where).is_relative_to(installed)
    assert version.endswith(mailctl.__version__)
