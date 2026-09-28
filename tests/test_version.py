"""``mailctl.__version__`` is read from the installed metadata (#106).

``pyproject.toml`` is the only place the version is written, so the package
attribute must agree with the distribution's metadata rather than carry a
second copy that a release could forget to bump.
"""

import importlib.metadata
import importlib.util

import mailctl


def test_version_matches_installed_metadata():
    assert mailctl.__version__ == importlib.metadata.version("mailctl")


def test_version_falls_back_when_not_installed(monkeypatch):
    def not_installed(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", not_installed)

    # A separate copy of the module, so the real `mailctl` -- and the
    # MailctlError class every other module already holds -- is untouched.
    spec = importlib.util.spec_from_file_location(
        "_mailctl_uninstalled", mailctl.__file__
    )
    assert spec is not None
    assert spec.loader is not None

    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)

    assert probe.__version__ == "0+unknown"
