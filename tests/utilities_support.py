"""Fakes and helpers the ``test_utilities_*`` files share.

Not a test module. The fixtures built on these -- ``fake_sieve`` and
``sessions`` -- are in ``conftest.py``.
"""

from mailctl import MailctlError
from mailctl.components.managesieve.capabilities import Capabilities
from mailctl.criteria import Criteria
from mailctl.engine import Session
from mailctl.providers.mxroute import MXROUTE, MxrouteTransport

FULL = ["fileinto", "imap4flags", "mailbox"]
NO_MAILBOX = ["fileinto", "imap4flags"]

# What a folder's SELECT reports in the conftest IMAP double unless a test
# says otherwise: a time-shaped value, as Dovecot assigns.
DEFAULT_UIDVALIDITY = 1727000000

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

        # CAPABILITY lines sent besides SIEVE, as (name, value) pairs.
        self.extra: list[tuple[str, str | None]] = []

    # ------------------------------------------------------------------------
    def capabilities(self):
        return list(self.caps)

    # ------------------------------------------------------------------------
    def server_capabilities(self):
        return Capabilities((*self.extra, ("SIEVE", " ".join(self.caps))))

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


# ----------------------------------------------------------------------------
def mxroute(sieve=None, imap=None) -> Session:
    """An ``mxroute`` session over two layer-1 sessions or stand-ins.

    Either may be None, for a half that was not opened.
    """
    return Session(MXROUTE, MxrouteTransport(sieve=sieve, imap=imap))


class Stopped(BaseException):
    """What :class:`Checkpoint` raises: a stop, as the API's cancel is one,
    which no handler of failures on the way up takes for a failure."""


class Checkpoint:
    """A session's ``checkpoint`` that lets ``allowed`` operations go and
    stops the next, or with ``before_write`` stops the first write instead;
    ``asked`` records each operation it was asked about, True for a
    write."""

    # ------------------------------------------------------------------------
    def __init__(self, allowed: int = 1_000, *, before_write: bool = False):
        self.allowed = allowed
        self.before_write = before_write
        self.asked: list[bool] = []

    # ------------------------------------------------------------------------
    def __call__(self, write: bool) -> None:
        self.asked.append(write)

        if len(self.asked) > self.allowed or (write and self.before_write):
            raise Stopped


# ----------------------------------------------------------------------------
def checked(session: Session, checkpoint: Checkpoint) -> Session:
    """``session``'s provider and connections, with ``checkpoint``."""
    return Session(session.provider, session.connection, checkpoint=checkpoint)
