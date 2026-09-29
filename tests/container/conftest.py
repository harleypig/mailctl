"""The container tier: mailctl's write paths against a local Dovecot.

These tests start a throwaway Dovecot + Pigeonhole server in Docker, built
from ``tests/container/image/``, and run mailctl against it the way a user
would -- through ``cli.main`` -- so that PUTSCRIPT, SETACTIVE, folder
creation, subscription, and the retroactive move/flag/discard pass are
exercised against a real server rather than a double. Nothing here can
reach a real account: the only host is ``localhost`` on ports Docker
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

import functools
import itertools
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from imapclient import IMAPClient
from sievelib.managesieve import Client as SieveClient

from mailctl import cli

GATE_FLAG = "MAILCTL_CONTAINER"
GATE_VALUE = "1"

IMAGE = "mailctl-test-dovecot"
IMAGE_DIR = Path(__file__).parent / "image"

# How long a fresh container gets to answer on every port. Startup is about
# two seconds here (the certificate is generated first); the margin is for
# a loaded CI runner, and a server that needs longer is a failure worth
# seeing rather than waiting out.
READY_TIMEOUT = 30.0

DOMAIN = "example.test"


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
@functools.cache
def docker_reason() -> str | None:
    """Why Docker cannot be used, or None; asked once per run."""
    if shutil.which("docker") is None:
        return "container tier needs docker, and it is not on PATH"

    probe = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        check=False,
    )

    if probe.returncode != 0:
        return "container tier needs a running Docker daemon"

    return None


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
# The server
# ############################################################################


@dataclass(frozen=True)
class Server:
    """A running container and what a client needs to reach it."""

    container: str
    imap_port: int  # implicit TLS (the container's 993)
    starttls_port: int  # IMAP STARTTLS (the container's 143)
    sieve_port: int  # ManageSieve STARTTLS (the container's 4190)
    cert_file: Path
    password_file: Path


# ----------------------------------------------------------------------------
def docker(*args: str, stdin: str | None = None) -> str:
    """Run a docker command, failing the run with its stderr if it fails.

    Nothing passed here carries the password on the command line; the one
    place it crosses to the container is ``stdin``.
    """
    result = subprocess.run(
        ["docker", *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        pytest.fail(
            f"docker {args[0]} failed ({result.returncode}): "
            f"{result.stderr.strip()}",
            pytrace=False,
        )

    return result.stdout.strip()


# ----------------------------------------------------------------------------
def host_port(container: str, port: int) -> int:
    """The loopback port Docker mapped the container's ``port`` to."""
    mapping = docker("port", container, f"{port}/tcp")

    # One line per address family; the fixture binds 127.0.0.1 only.
    return int(mapping.splitlines()[0].rsplit(":", 1)[1])


# ----------------------------------------------------------------------------
def banner(port: int) -> bytes:
    """The first bytes a plaintext listener sends, or b"" if none yet."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1) as sock:
            return sock.recv(512)

    except OSError:
        return b""


# ----------------------------------------------------------------------------
def wait_until_ready(container: str, starttls: int, sieve: int) -> None:
    """Block until IMAP and ManageSieve both greet, or fail the run.

    The two plaintext listeners greet before TLS, so a greeting on each is
    proof the server is serving; 993 shares the imap-login process with
    143, so it is up when 143 is.
    """
    deadline = time.monotonic() + READY_TIMEOUT

    while time.monotonic() < deadline:
        if banner(starttls).startswith(b"* OK") and b"IMPLEMENTATION" in (
            banner(sieve)
        ):
            return

        time.sleep(0.2)

    logs = subprocess.run(
        ["docker", "logs", container],
        capture_output=True,
        text=True,
        check=False,
    )

    pytest.fail(
        f"Dovecot did not become ready in {READY_TIMEOUT:.0f}s; its log:\n"
        f"{logs.stdout}{logs.stderr}",
        pytrace=False,
    )


# ----------------------------------------------------------------------------
@pytest.fixture(scope="session")
def dovecot(tmp_path_factory) -> Iterator[Server]:
    """Build the image, start one container for the run, remove it after.

    The build is cached by Docker, so after the first run it costs a
    second. Ports are Docker's choice of free loopback ports, which is
    race-free where picking a free port here and handing it over is not.
    """
    work = tmp_path_factory.mktemp("dovecot")

    docker("build", "--quiet", "--tag", IMAGE, str(IMAGE_DIR))

    name = f"mailctl-test-{uuid.uuid4().hex[:12]}"

    docker(
        "run",
        "--detach",
        "--rm",
        "--name",
        name,
        "--publish",
        "127.0.0.1::143",
        "--publish",
        "127.0.0.1::993",
        "--publish",
        "127.0.0.1::4190",
        IMAGE,
    )

    try:
        # The password first: the entrypoint holds Dovecot until it is
        # there. Written to a temporary name and renamed, so the entrypoint
        # never starts on half a file.
        password_file = work / "password"
        password_file.touch(mode=0o600)
        password_file.write_text(secrets.token_urlsafe(24))

        docker(
            "exec",
            "--interactive",
            name,
            "sh",
            "-c",
            "umask 077 && cat > /etc/dovecot/.secret.tmp "
            "&& mv /etc/dovecot/.secret.tmp /etc/dovecot/secret.conf",
            stdin=f"passdb_static_password = {password_file.read_text()}\n",
        )

        starttls = host_port(name, 143)
        sieve = host_port(name, 4190)

        wait_until_ready(name, starttls, sieve)

        # The certificate only; the key never leaves the container.
        cert = work / "tls.crt"

        docker("cp", f"{name}:/etc/dovecot/tls/tls.crt", str(cert))

        yield Server(
            container=name,
            imap_port=host_port(name, 993),
            starttls_port=starttls,
            sieve_port=sieve,
            cert_file=cert,
            password_file=password_file,
        )

    finally:
        subprocess.run(
            ["docker", "rm", "--force", name],
            capture_output=True,
            check=False,
        )


# ############################################################################
# One mailbox per test
# ############################################################################

_serial = itertools.count(1)


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
    @contextmanager
    def sieve(self) -> Iterator[SieveClient]:
        """sievelib's own client, logged in over STARTTLS."""
        client = SieveClient("localhost", self.server.sieve_port)

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
            "localhost",
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
    def append(self, folder: str, message: bytes, flags=()) -> int:
        """APPEND ``message`` to ``folder`` and return its UID."""
        with self.imap() as client:
            response = client.append(folder, message, flags=flags)

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
    user = f"t{next(_serial)}-{uuid.uuid4().hex[:6]}@{DOMAIN}"
    backup_dir = tmp_path / "backups"

    # Every setting mailctl reads, so nothing falls through to a default
    # that could name another host. The IMAP port is the implicit-TLS one:
    # mailctl treats only port 143 as STARTTLS, and Docker's port is not.
    settings = {
        "SSL_CERT_FILE": str(dovecot.cert_file),
        "MAILCTL_PROVIDER": "mxroute",
        "MAILCTL_HOST": "localhost",
        "MAILCTL_IMAP_HOST": "localhost",
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
