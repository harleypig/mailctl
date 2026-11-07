"""The throwaway Dovecot + Pigeonhole server two tiers share.

The container tier (``tests/container/``) runs mailctl in-process against
it; the e2e tier (``tests/e2e/``) runs the installed commands against it.
Both start it the same way, so the way lives here once: build the image
from ``tests/container/image/``, start one container, hand it a random
password on stdin, wait until IMAP and ManageSieve greet, and copy out
the certificate (never the key).

The password is a throwaway, but it is held to the ``Secret`` bar anyway
(CONVENTIONS.md > *Credentials*): it is generated here, written to one
0600 file, handed to the container on stdin, and never printed,
formatted into an assertion, or passed on a command line.

Nothing here is a pytest fixture: each tier's conftest wraps
:func:`running_dovecot` in a session fixture of its own, so a tier that
does not ask for a server never starts one.
"""

import functools
import itertools
import secrets
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

IMAGE = "mailctl-test-dovecot"
IMAGE_DIR = Path(__file__).parent / "container" / "image"

# How long a fresh container gets to answer on every port. Startup is about
# two seconds here (the certificate is generated first); the margin is for
# a loaded CI runner, and a server that needs longer is a failure worth
# seeing rather than waiting out.
READY_TIMEOUT = 30.0

DOMAIN = "example.test"

# The address Docker publishes the server on, and the only host either tier
# connects to. An address, never ``localhost``: Debian and Ubuntu often map
# that name to 127.0.1.1, where nothing listens (#239 - container tier and
# 127.0.1.1). The certificate's SAN carries it (image/entrypoint.sh).
HOST = "127.0.0.1"

_serial = itertools.count(1)


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
@functools.cache
def docker_reason() -> str | None:
    """Why Docker cannot be used, or None; asked once per run."""
    if shutil.which("docker") is None:
        return "this tier needs docker, and it is not on PATH"

    probe = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        check=False,
    )

    if probe.returncode != 0:
        return "this tier needs a running Docker daemon"

    return None


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

    # One line per address family; the server binds HOST only.
    return int(mapping.splitlines()[0].rsplit(":", 1)[1])


# ----------------------------------------------------------------------------
def banner(port: int) -> bytes:
    """The first bytes a plaintext listener sends, or b"" if none yet."""
    try:
        with socket.create_connection((HOST, port), timeout=1) as sock:
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
@contextmanager
def running_dovecot(work: Path) -> Iterator[Server]:
    """Build the image, start one container, and remove it on exit.

    ``work`` is an empty directory for the password file and the
    certificate. The build is cached by Docker, so after the first run it
    costs a second. Ports are Docker's choice of free loopback ports, which
    is race-free where picking a free port here and handing it over is not.
    """
    docker("build", "--quiet", "--tag", IMAGE, str(IMAGE_DIR))

    name = f"mailctl-test-{uuid.uuid4().hex[:12]}"

    docker(
        "run",
        "--detach",
        "--rm",
        "--name",
        name,
        "--publish",
        f"{HOST}::143",
        "--publish",
        f"{HOST}::993",
        "--publish",
        f"{HOST}::4190",
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


# ----------------------------------------------------------------------------
def new_user() -> str:
    """A user name no test has used, and so a new, empty mailbox.

    The server takes any user name with the run's password and makes its
    mailbox on first login.
    """
    return f"t{next(_serial)}-{uuid.uuid4().hex[:6]}@{DOMAIN}"
