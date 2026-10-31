"""The live tier's read gate: which credential settings open it (#369).

``tests/live/conftest.py`` skips every live test unless the account is
fully configured. A credential counts if any rung mailctl's own config
ladder reads is set -- ``MAILCTL_PASSWORD_FILE``, ``MAILCTL_PASSWORD_CMD``,
or ``MAILCTL_PASSWORD``. Before #369 the gate knew only the last two, so
an environment valid for every mailctl command was refused here.

Offline. The gate tests presence only, so the values below are
placeholders: the file named does not exist, and nothing here (or in the
gate) opens, reads, or prints one.

Not covered: the ``MAILCTL_LIVE`` flag's exactness and the host/user
check, which this file sets to their open state and leaves to the gate's
own docstring; and the write gate, which is ``test_live_write_gate.py``.
"""

import importlib.util
from pathlib import Path

import pytest

PASSWORD_SETTINGS = (
    "MAILCTL_PASSWORD_FILE",
    "MAILCTL_PASSWORD_CMD",
    "MAILCTL_PASSWORD",
)


# ----------------------------------------------------------------------------
@pytest.fixture
def live_gate():
    """The live tier's ``gate_reason``, loaded under a name of its own.

    Imported as plain ``conftest`` it would collide with ``tests/
    conftest.py``; loading it by path keeps the two apart and registers
    none of its fixtures here.
    """
    path = Path(__file__).parent / "live" / "conftest.py"
    spec = importlib.util.spec_from_file_location("live_gate_conftest", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module.gate_reason


# ----------------------------------------------------------------------------
@pytest.fixture
def open_but_credential(monkeypatch):
    """Everything the gate checks set open, except a credential.

    ``tests/conftest.py`` has already cleared every ``MAILCTL_*``
    variable, so the three credential settings start unset.
    """
    monkeypatch.setenv("MAILCTL_LIVE", "1")
    monkeypatch.setenv("MAILCTL_HOST", "mail.example.invalid")
    monkeypatch.setenv("MAILCTL_USER", "user@example.invalid")

    for name in PASSWORD_SETTINGS:
        monkeypatch.delenv(name, raising=False)

    return monkeypatch


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", PASSWORD_SETTINGS)
def test_each_credential_rung_alone_opens_the_gate(
    live_gate, open_but_credential, name
):
    """Red if the gate refuses a rung mailctl reads -- the #369 defect.

    ``MAILCTL_PASSWORD_FILE`` alone was refused before the fix. The path
    does not exist: the gate must not look at it.
    """
    open_but_credential.setenv(name, "/nonexistent/369-placeholder")

    assert live_gate() is None


# ----------------------------------------------------------------------------
def test_no_credential_keeps_it_shut(live_gate, open_but_credential):
    """Red if the gate opens with none of the three set.

    The refusal must name every rung, so the reader is pointed at the
    one their setup uses rather than told only two exist.
    """
    reason = live_gate()

    assert reason is not None

    for name in PASSWORD_SETTINGS:
        assert name in reason
