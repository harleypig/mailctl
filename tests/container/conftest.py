"""The container tier: mailctl's write paths against a local Dovecot.

These tests start a throwaway Dovecot + Pigeonhole server in Docker, built
from ``tests/container/image/``, and run mailctl against it the way a user
would -- through ``cli.main`` -- so that PUTSCRIPT, SETACTIVE, folder
creation, subscription, and the retroactive move/flag/discard pass are
exercised against a real server rather than a double. Nothing here can
reach a real account: the only host is ``127.0.0.1`` on ports Docker
chose, and every setting comes from the fixtures below, never from the
developer's own ``MAILCTL_*`` variables or config file.

The gate is as narrow as the live tier's, and deliberately separate from it
(#49): ``MAILCTL_CONTAINER`` must equal exactly ``"1"``, and Docker must
answer. Everything else is a skip. ``make testcontainer`` sets the
variable; a plain ``pytest`` never starts a container.

Every test gets its **own mailbox** on the one server (``account``), so no
test sees another's scripts, folders, or mail, and order does not matter.

The oracle is independent of mailctl. What a test asserts about the server
-- the stored script's bytes, the folder and subscription lists, a
message's flags -- is read with sievelib's and IMAPClient's own clients
(``Account.sieve`` / ``Account.imap``), not through the code under test,
so a mailctl bug that corrupts a write and then reads it back the same
wrong way still shows.

The password is a throwaway, but it is held to the ``Secret`` bar anyway
(CONVENTIONS.md > *Credentials*): it is generated here, written to one
0600 file that mailctl reads through ``MAILCTL_PASSWORD_FILE``, handed to
the container on stdin, and never printed, formatted into an assertion, or
passed on a command line.
"""

import os
import ssl
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from dovecot_support import (
    HOST,
    Server,
    docker_reason,
    new_user,
    running_dovecot,
)
from imapclient import IMAPClient
from live_write import (  # noqa: F401 -- fixtures, registered by import
    Mailbox,
    guarded_scripts,
    scratch_folder,
)
from live_write import SievelibClient as SieveClient

from mailctl import cli
from mailctl.config import Secret

GATE_FLAG = "MAILCTL_CONTAINER"
GATE_VALUE = "1"


# ############################################################################
# The gate
# ############################################################################


# ----------------------------------------------------------------------------
def gate_reason() -> str | None:
    """Return why the container tier must not run, or None if it may.

    "Why not" rather than "may we", as in ``tests/live/conftest.py``: a new
    condition is added by returning a reason, so forgetting the caller
    cannot turn into an accidental run.
    """
    if os.environ.get(GATE_FLAG) != GATE_VALUE:
        return (
            f"container tier is off: set {GATE_FLAG}={GATE_VALUE} (or run "
            f"'make testcontainer') to start a local Dovecot in Docker"
        )

    return docker_reason()


# ----------------------------------------------------------------------------
def pytest_runtest_setup(item):
    """Skip every test in this directory unless the gate is open.

    Runs for each item under ``tests/container/`` whatever its markers, so
    a test whose author forgot the marker is still gated.
    """
    reason = gate_reason()

    if reason is not None:
        pytest.skip(reason)


# ############################################################################
# Overrides of the offline tier's guards
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def no_network():
    """Lift the offline tier's socket block, here and nowhere else.

    Reaching the container is this tier's whole job. The environment
    scrubbing in ``tests/conftest.py`` is **not** lifted: it is what keeps
    the developer's real ``MAILCTL_*`` settings -- and so their real
    account -- out of these tests. The ``account`` fixture then sets every
    setting mailctl reads.
    """
    return None


# ############################################################################
# The server: dovecot_support's, shared with the e2e tier
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.fixture(scope="session")
def dovecot(tmp_path_factory) -> Iterator[Server]:
    """One container for the run, removed after it."""
    with running_dovecot(tmp_path_factory.mktemp("dovecot")) as server:
        yield server


# ############################################################################
# One mailbox per test
# ############################################################################


@dataclass
class Result:
    """What one ``mailctl`` invocation did."""

    code: int
    out: str
    err: str


@dataclass
class Account:
    """A fresh mailbox on the container, and ways to drive and inspect it.

    ``run`` is mailctl, the code under test. ``sieve`` and ``imap`` are the
    libraries' own clients, logged in as the same user, and are the
    independent oracle for what the server now holds.
    """

    server: Server
    user: str
    backup_dir: Path
    capsys: pytest.CaptureFixture

    # ------------------------------------------------------------------------
    def run(self, *argv: str) -> Result:
        """Run ``mailctl ARGV`` in-process, as the CLI snapshots do."""
        self.capsys.readouterr()

        code = cli.main(list(argv))
        captured = self.capsys.readouterr()

        return Result(code, captured.out, captured.err)

    # ------------------------------------------------------------------------
    def _password(self) -> str:
        return self.server.password_file.read_text()

    # ------------------------------------------------------------------------
    def mailbox(self) -> Mailbox:
        """This mailbox as the write guards take one (``live_write``)."""
        return Mailbox(
            sieve_host=HOST,
            sieve_port=self.server.sieve_port,
            sieve_tls="starttls",
            imap_host=HOST,
            imap_port=self.server.imap_port,
            imap_tls="ssl",
            user=self.user,
            password=lambda: Secret(self._password()),
        )

    # ------------------------------------------------------------------------
    @contextmanager
    def sieve(self) -> Iterator[SieveClient]:
        """sievelib's own client, logged in over STARTTLS."""
        client = SieveClient(HOST, self.server.sieve_port)

        if not client.connect(self.user, self._password(), starttls=True):
            pytest.fail("the oracle's ManageSieve login failed")

        try:
            yield client

        finally:
            client.logout()

    # ------------------------------------------------------------------------
    @contextmanager
    def imap(self) -> Iterator[IMAPClient]:
        """IMAPClient itself, logged in over implicit TLS."""
        client = IMAPClient(
            HOST,
            port=self.server.imap_port,
            ssl=True,
            ssl_context=ssl.create_default_context(),
        )

        client.login(self.user, self._password())

        try:
            yield client

        finally:
            client.logout()

    # ------------------------------------------------------------------------
    def seed_script(self, name: str, text: str, activate=True) -> None:
        """Store a script as another client (Roundcube, say) would have."""
        with self.sieve() as client:
            assert client.putscript(name, text), f"PUTSCRIPT {name} refused"

            if activate:
                assert client.setactive(name), f"SETACTIVE {name} refused"

    # ------------------------------------------------------------------------
    def script(self, name: str) -> str | None:
        """A stored script's text, as the server returns it."""
        with self.sieve() as client:
            return client.getscript(name)

    # ------------------------------------------------------------------------
    def script_bytes(self, name: str) -> bytes:
        """A stored script's exact bytes, read off the container's disk.

        sievelib's GETSCRIPT is not byte-exact (ADR 0006, gap S1), so the
        file Pigeonhole wrote is the only oracle for a byte-exact claim.
        """
        path = f"/srv/vmail/{self.user}/sieve/{name}.sieve"
        result = subprocess.run(
            ["docker", "exec", self.server.container, "cat", path],
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0, f"no script file at {path}"

        return result.stdout

    # ------------------------------------------------------------------------
    def active_script(self) -> str | None:
        with self.sieve() as client:
            active, _ = client.listscripts()

        return active

    # ------------------------------------------------------------------------
    def append(self, folder: str, message: bytes, flags=(), when=None) -> int:
        """APPEND ``message`` to ``folder`` and return its UID.

        ``when`` is the internal date to give it -- the arrival date IMAP's
        SINCE and BEFORE compare -- or None for now.
        """
        # IMAPClient is unannotated; with unpack=True, append() returns
        # the server's one response line.
        with self.imap() as client:
            response = cast(
                bytes,
                client.append(folder, message, flags=flags, msg_time=when),
            )

        # UIDPLUS: "[APPENDUID <validity> <uid>] Append completed."
        return int(response.split(b"]")[0].split()[-1])

    # ------------------------------------------------------------------------
    def deliver(self, message: bytes, sender: str) -> None:
        """Hand ``message`` to dovecot-lda, which runs the active script.

        This is new mail arriving, the half Sieve owns: what the rule
        mailctl uploaded does to it is what the rule really does.
        """
        result = subprocess.run(
            [
                "docker",
                "exec",
                "--interactive",
                self.server.container,
                "/usr/lib/dovecot/dovecot-lda",
                "-d",
                self.user,
                "-f",
                sender,
            ],
            input=message,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0, (
            f"dovecot-lda refused the message: {result.stderr.decode()}"
        )


# ----------------------------------------------------------------------------
@pytest.fixture
def account(dovecot, monkeypatch, tmp_path, capsys) -> Account:
    """A new, empty mailbox, with mailctl's settings pointed at it.

    The server takes any user name with the run's password and makes its
    mailbox on first login, so a name no test has used is a new account.
    """
    user = new_user()
    backup_dir = tmp_path / "backups"

    # Every setting mailctl reads, so nothing falls through to a default
    # that could name another host. The IMAP port is the implicit-TLS one:
    # mailctl treats only port 143 as STARTTLS, and Docker's port is not.
    settings = {
        "SSL_CERT_FILE": str(dovecot.cert_file),
        "MAILCTL_PROVIDER": "mxroute",
        "MAILCTL_HOST": HOST,
        "MAILCTL_IMAP_HOST": HOST,
        "MAILCTL_USER": user,
        "MAILCTL_PASSWORD_FILE": str(dovecot.password_file),
        "MAILCTL_IMAP_PORT": str(dovecot.imap_port),
        "MAILCTL_SIEVE_PORT": str(dovecot.sieve_port),
        "MAILCTL_SIEVE_TLS": "starttls",
        "MAILCTL_BACKUP_DIR": str(backup_dir),
    }

    for name, value in settings.items():
        monkeypatch.setenv(name, value)

    return Account(dovecot, user, backup_dir, capsys)


# ----------------------------------------------------------------------------
@pytest.fixture
def write_mailbox(account) -> Mailbox:
    """This test's mailbox, as the live tier's write guards reach one.

    The same :class:`Mailbox` the live tier builds from the user's
    configuration, pointed at the container instead, so the guards proved
    here are the code the live tier runs (#9).
    """
    return account.mailbox()
