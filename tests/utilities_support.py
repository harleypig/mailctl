"""Fakes and helpers the ``test_utilities_*`` files share.

Not a test module. The fixtures built on these -- ``fake_sieve`` and
``sessions`` -- are in ``conftest.py``.
"""

from mailctl import MailctlError
from mailctl.criteria import Criteria

FULL = ["fileinto", "imap4flags", "mailbox"]
NO_MAILBOX = ["fileinto", "imap4flags"]

# ############################################################################
# Fakes and helpers
# ############################################################################


class FakeSieveSession:
    """Stands in for ``SieveSession``, recording what it was asked to do."""

    # ------------------------------------------------------------------------
    def __init__(
        self,
        script="",
        active: str | None = "managesieve",
        caps=FULL,
        others=(),
    ):
        self.script = script
        self.active = active
        self.others = list(others)
        self.caps = list(caps)
        self.reject = False
        self.calls: list[tuple] = []

    # ------------------------------------------------------------------------
    def capabilities(self):
        return list(self.caps)

    # ------------------------------------------------------------------------
    def missing_extensions(self, required):
        return sorted(name for name in required if name not in self.caps)

    # ------------------------------------------------------------------------
    def list_scripts(self):
        return (self.active, list(self.others))

    # ------------------------------------------------------------------------
    def active_script_name(self):
        return self.active

    # ------------------------------------------------------------------------
    def get_script(self, name):
        self.calls.append(("get_script", name))

        return self.script

    # ------------------------------------------------------------------------
    def check_script(self, content):
        self.calls.append(("check_script",))

        if self.reject:
            raise MailctlError("the server rejected the script")

    # ------------------------------------------------------------------------
    def put_script(self, name, content):
        self.calls.append(("put_script", name, content))

    # ------------------------------------------------------------------------
    def set_active(self, name):
        self.calls.append(("set_active", name))

    # ------------------------------------------------------------------------
    def names(self):
        return [call[0] for call in self.calls]


# ----------------------------------------------------------------------------
def criteria(header="From", value="noreply@github.com") -> Criteria:
    built = Criteria()
    built.add(header, value)

    return built
