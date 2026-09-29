"""The second opt-in a live test needs before it may write (#9).

``make testlive`` opens the live tier for reading. A test that writes to
the real account also needs ``MAILCTL_LIVE_WRITE`` set to exactly ``1``;
this pins that nothing else opens it. Offline: it reads a mapping, never
the environment.
"""

import pytest
from live_write import WRITE_FLAG, write_gate_reason


# ----------------------------------------------------------------------------
def test_exactly_one_opens_the_write_gate():
    """Red if the gate opens on anything but the literal ``1``."""
    assert write_gate_reason({WRITE_FLAG: "1"}) is None


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", " 1", "11"])
def test_anything_else_keeps_it_shut(value):
    """Red if the gate is a truthiness or prefix test rather than equality.

    Unset, empty, and every near miss must refuse, and the refusal must
    name the variable that opens it.
    """
    environ = {} if value is None else {WRITE_FLAG: value}

    reason = write_gate_reason(environ)

    assert reason is not None
    assert WRITE_FLAG in reason
