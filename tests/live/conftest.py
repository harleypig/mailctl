"""The gate for the live tier. Skipping is the default and the fallback.

These tests talk to a **real MXroute account**. Every path out of this
file that is not an explicit, fully-configured opt-in is a skip -- an
unset variable, a mistyped one, a missing credential, and a refusal from
mailctl's own settings loader (a ``MailctlError``) all end the same way.
Any other exception while working out the gate is not caught: it errors
the test in setup, which stops it as surely as a skip and is louder,
since it is a defect in the gate or the loader rather than a run that
was not configured.

The gate is deliberately narrow:

* ``MAILCTL_LIVE`` must equal exactly ``"1"``. Not "set", not truthy --
  ``true``, ``yes`` and ``0`` all skip. ``make testlive`` sets it to
  ``1``; nothing else should be doing so by accident.
* The account settings must all be present. A half-configured run would
  otherwise fail against *some* server, which is worse than not running.
  They are resolved by mailctl's own ``load_config``, so whatever a
  mailctl command would read counts: the environment, ``config.toml``,
  and an env file named by ``MAILCTL_LIVE_ENV_FILE`` (#380). That file is
  read exactly as ``--env-file`` is -- its mode checked where it sets
  ``MAILCTL_PASSWORD``, the password wrapped in ``Secret``, nothing
  exported -- and one that cannot be read is a skip, never a fall-through
  to the environment. ``MAILCTL_LIVE`` and ``MAILCTL_LIVE_WRITE`` are
  opt-ins for the run and are read from the environment only.

TESTS.md holds the rest of the safety contract for anything added here.
The two clauses a writing test must satisfy, neither of which the
read-only smoke tests need, are fixtures (``tests/live_write.py``, #9):

* **``guarded_scripts``** -- every Sieve script is captured before the
  test and put back afterwards, including on failure, and the restore is
  confirmed or reported. The account's real filters are not the test's to
  lose.
* **``scratch_folder``** -- never real mail in INBOX: a new folder the
  test owns, filled with messages it appends itself, and deleted after.

**Writing is a second opt-in.** Both fixtures reach the account through
``write_mailbox``, which skips unless ``MAILCTL_LIVE_WRITE`` is also
exactly ``"1"``. ``make testlive`` alone therefore stays read-only; a
writing test that forgets these fixtures has no ``write_mailbox`` and so
no gate, which is why a writing test must use them.
"""

import argparse
import os

import pytest
from live_write import (  # noqa: F401 -- fixtures, registered by import
    Mailbox,
    guarded_scripts,
    scratch_folder,
    write_gate_reason,
)

from mailctl import MailctlError
from mailctl.config import Config, load_config

LIVE_FLAG = "MAILCTL_LIVE"
LIVE_VALUE = "1"

# Not MAILCTL_ENV_FILE: no mailctl command reads a variable of that name,
# and one that looked like a setting would suggest otherwise.
ENV_FILE_VAR = "MAILCTL_LIVE_ENV_FILE"

# Config field -> the variable that sets it, for the refusal's wording.
REQUIRED_SETTINGS = {"host": "MAILCTL_HOST", "user": "MAILCTL_USER"}
# In the order mailctl's config ladder reads them; the first two keep the
# value out of the environment, so the message names them as preferred.
PASSWORD_SETTINGS = (
    "MAILCTL_PASSWORD_FILE",
    "MAILCTL_PASSWORD_CMD",
    "MAILCTL_PASSWORD",
)


# ############################################################################
# The gate
# ############################################################################


# ----------------------------------------------------------------------------
def live_settings() -> Config:
    """The account settings, resolved the way the CLI resolves them.

    ``MAILCTL_LIVE_ENV_FILE`` stands where ``--env-file`` would; empty is
    unset. Raises ``MailctlError`` where mailctl would refuse the run.
    """
    env_file = os.environ.get(ENV_FILE_VAR) or None

    return load_config(argparse.Namespace(env_file=env_file))


# ----------------------------------------------------------------------------
def gate_reason() -> str | None:
    """Return why the live tier must not run, or None if it may.

    Written as "why not" rather than "may we" on purpose: a new condition
    is added by returning a reason, so forgetting to update the caller
    cannot turn into an accidental live run.

    Only ``MailctlError`` becomes a reason. Anything else raised while
    reading the settings propagates, so the test errors rather than runs
    or skips quietly; its message is not one the loader vetted, so it is
    not rendered as a skip reason either.
    """
    if os.environ.get(LIVE_FLAG) != LIVE_VALUE:
        return (
            f"live tier is off: set {LIVE_FLAG}={LIVE_VALUE} (or run "
            f"'make testlive') to hit a real MXroute account"
        )

    # A MailctlError's message names paths, modes, and line numbers and
    # never a value -- the loader's own bar -- so it is safe as a reason.
    try:
        config = live_settings()

    except MailctlError as exc:
        return f"live tier cannot read its settings ({exc.code}): {exc}"

    missing = [
        var
        for name, var in REQUIRED_SETTINGS.items()
        if not getattr(config, name)
    ]

    if missing:
        return (
            f"live tier needs {', '.join(missing)} in the environment, "
            f"config.toml, or the file {ENV_FILE_VAR} names"
        )

    # Presence only: nothing is opened or run until a test connects.
    if not config.password_sources():
        return (
            f"live tier needs a credential: set one of "
            f"{', '.join(PASSWORD_SETTINGS[:-1])} or {PASSWORD_SETTINGS[-1]} "
            f"({PASSWORD_SETTINGS[0]} or {PASSWORD_SETTINGS[1]} is preferred "
            f"-- either keeps the value out of the environment)"
        )

    return None


# ----------------------------------------------------------------------------
def pytest_runtest_setup(item):
    """Skip every test in this directory unless the gate is fully open.

    This runs for each item under ``tests/live/`` regardless of its
    markers, so a live test whose author forgot ``@pytest.mark.live`` is
    still gated.
    """
    reason = gate_reason()

    if reason is not None:
        pytest.skip(reason)


# ############################################################################
# Fixtures
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def isolated_environment():
    """Override the offline tier's environment scrubbing.

    ``tests/conftest.py`` clears every ``MAILCTL_*`` variable so the unit
    tests cannot depend on the developer's account. The live tier is the
    one place those variables are the input, so the scrubbing is
    disabled here rather than worked around inside each test.
    """
    return None


# ----------------------------------------------------------------------------
@pytest.fixture(scope="session")
def live_config() -> Config:
    """The account settings the gate opened on (``live_settings``)."""
    return live_settings()


# ----------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def no_network():
    """Override the offline tier's socket block.

    ``tests/conftest.py`` makes ``socket.socket`` raise so a unit test
    cannot reach a server by accident. Reaching a server is this tier's
    entire job, so the block is lifted here -- behind the gate above, and
    nowhere else.
    """
    return None


# ----------------------------------------------------------------------------
@pytest.fixture
def write_mailbox(live_config) -> Mailbox:
    """The account, for a test that writes -- behind a second opt-in.

    ``MAILCTL_LIVE=1`` opens the tier for reading. Writing also needs
    ``MAILCTL_LIVE_WRITE`` set to exactly ``"1"``, so a run meant to read
    cannot write by accident.
    """
    reason = write_gate_reason(os.environ)

    if reason is not None:
        pytest.skip(reason)

    return Mailbox.from_config(live_config)
