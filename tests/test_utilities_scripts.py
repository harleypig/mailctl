"""The utilities for reading scripts, driven as any front-end would.

No argparse and no stdout here: every test builds plain inputs, calls
the utility, and asserts on what comes back and on what the fakes were
asked to do.

The Sieve side is a session-level fake (``FakeSieveSession``); the IMAP
side is the real ``ImapSession`` over the ``FakeIMAPClient`` double from
conftest, so folder normalization and planning run for real.
"""

import pytest
from utilities_support import FakeSieveSession, mxroute

from mailctl import MailctlError, utilities
from mailctl.providers.mxroute import MXROUTE

# ############################################################################
# Reading the account
# ############################################################################


# ----------------------------------------------------------------------------
def test_read_script_takes_the_active_one_and_parses_only_on_demand(
    sessions,
):
    script = utilities.scripts.read_script(sessions)

    assert script.name == "managesieve"
    assert script.rule_names() == ["keep-boss", "bin-the-noise"]

    # Showing an unparseable script must still be possible.
    broken = utilities.scripts.ScriptText("broken", "if {{{", MXROUTE)

    with pytest.raises(MailctlError):
        broken.rule_names()


# ----------------------------------------------------------------------------
def test_reading_with_no_active_script_says_so():
    empty = mxroute(sieve=FakeSieveSession(active=None))

    with pytest.raises(MailctlError, match="name one explicitly"):
        utilities.scripts.read_script(empty)

    with pytest.raises(MailctlError, match="no filters to show"):
        utilities.rules.read_rules(empty)
