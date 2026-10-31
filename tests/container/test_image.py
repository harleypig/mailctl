"""The Docker image's guard on where backups go, run in the image (#230),
and its three commands, the CLI, the API, and the TUI (#347).

Builds mailctl's own image from the top-level ``Dockerfile`` and runs it
as a user would. The first argument ``api`` runs ``mailctl-api`` and
``tui`` runs ``mailctl-tui``; anything else is the CLI's, and every guard
holds whichever runs. Every backup must land in the mounted config directory,
since anything else is lost with the container: the entrypoint refuses a
run as root or without the mount, and mailctl itself refuses a backup
location outside ``MAILCTL_BACKUP_ROOT``, which the image sets, from any
source. ``/dev/shm`` is the case the filesystem does not catch: it is
writable to every user at run time.

No mail server is needed. Each refusal comes before any connection, and
the container has no network, so a guard that let a run through would
fail on connecting instead, which the control below shows.
"""

import fcntl
import itertools
import json
import os
import pty
import re
import select
import struct
import subprocess
import termios
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.container

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "mailctl-test-image"
CONFIG = "/config/mailctl"
OUTSIDE = "/dev/shm/backups"
REFUSED = "outside /config/mailctl, the one directory MAILCTL_BACKUP_ROOT"
RUNS = itertools.count()


# ----------------------------------------------------------------------------
@pytest.fixture(scope="session")
def image() -> str:
    """mailctl's image, built once per run; Docker caches the layers."""
    build = subprocess.run(
        ["docker", "build", "--quiet", "--tag", IMAGE, str(ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )

    if build.returncode != 0:
        pytest.fail(f"docker build failed: {build.stderr}", pytrace=False)

    return IMAGE


# ----------------------------------------------------------------------------
@pytest.fixture
def mount(tmp_path) -> Path:
    """The host directory mounted as the config directory."""
    directory = tmp_path / "config"
    directory.mkdir()

    return directory


# ----------------------------------------------------------------------------
def run_image(
    image: str,
    mount: Path | None,
    *argv: str,
    user: str | None = None,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess:
    """``docker run`` the image with ``argv``, offline, as the host user;
    ``stdin``, where given, is sent to it under ``--interactive``."""
    name = f"mailctl-test-run-{os.getpid()}-{next(RUNS)}"
    command = [*docker_run(mount, user=user, env=env), "--name", name]

    if stdin is not None:
        command.append("--interactive")

    # A guard that lets the API or the TUI start where it should refuse
    # leaves it waiting; the timeout fails the test, and the kill stops the
    # container, which outlives the client killed by the timeout.
    try:
        return subprocess.run(
            [*command, image, *argv],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True)
        raise


# ----------------------------------------------------------------------------
def docker_run(
    mount: Path | None,
    *,
    user: str | None = None,
    env: dict[str, str] | None = None,
) -> list[str]:
    """``docker run``'s words up to the image: offline, as the host user."""
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        user or f"{os.getuid()}:{os.getgid()}",
        "--env",
        "MAILCTL_HOST=127.0.0.1",
        "--env",
        "MAILCTL_USER=user@example.test",
    ]

    for name, value in (env or {}).items():
        command += ["--env", f"{name}={value}"]

    if mount is not None:
        command += ["--volume", f"{mount}:{CONFIG}"]

    return command


BACKUP = ("filterset", "backup", "--dry-run")


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "rung", ["flag", "flag-prefix", "env-file", "environment", "config-file"]
)
def test_a_backup_dir_in_dev_shm_is_refused_from_every_source(
    image, mount, rung
):
    """Red if any source of the backup directory escapes the root: the
    #230 bypass was config.toml, and an env file was never checked."""
    argv = list(BACKUP)
    env = {}

    if rung == "flag":
        argv += ["--backup-dir", OUTSIDE]

    elif rung == "flag-prefix":
        argv += [f"--backup={OUTSIDE}"]

    elif rung == "env-file":
        (mount / "env").write_text(f"MAILCTL_BACKUP_DIR={OUTSIDE}\n")
        argv += ["--env-file", f"{CONFIG}/env"]

    elif rung == "environment":
        env["MAILCTL_BACKUP_DIR"] = OUTSIDE

    else:
        (mount / "config.toml").write_text(f'backup_dir = "{OUTSIDE}"\n')

    result = run_image(image, mount, *argv, env=env)

    assert result.returncode == 1, result.stderr
    assert f"backups would be written to {OUTSIDE}" in result.stderr
    assert REFUSED in result.stderr


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "where",
    [
        pytest.param(f"{CONFIG}/../../dev/shm", id="dot-dot"),
        pytest.param(f"{CONFIG}/link", id="link"),
    ],
)
def test_a_path_through_the_mount_cannot_lead_out_of_it(image, mount, where):
    """Red if the path is compared as written rather than resolved."""
    (mount / "link").symlink_to("/dev/shm")

    result = run_image(image, mount, *BACKUP, "--backup-dir", where)

    assert result.returncode == 1, result.stderr
    assert "backups would be written to /dev/shm " in result.stderr


# ----------------------------------------------------------------------------
def test_an_output_in_dev_shm_is_refused(image, mount):
    """Red if ``filterset backup --output`` is not held to the root."""
    result = run_image(
        image, mount, "filterset", "backup", "--output", "/dev/shm/x"
    )

    assert result.returncode == 1, result.stderr
    assert "backups would be written to /dev/shm/x," in result.stderr


# ----------------------------------------------------------------------------
def test_the_run_cannot_lift_the_root(image, mount):
    """Red if ``docker run -e MAILCTL_BACKUP_ROOT=/`` widens the root."""
    result = run_image(
        image,
        mount,
        *BACKUP,
        "--backup-dir",
        OUTSIDE,
        env={"MAILCTL_BACKUP_ROOT": "/"},
    )

    assert result.returncode == 1, result.stderr
    assert REFUSED in result.stderr


# ----------------------------------------------------------------------------
def test_the_root_holds_without_the_entrypoint(image, mount):
    """Red if only the entrypoint sets the root, not the image's ENV."""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--volume",
            f"{mount}:{CONFIG}",
            "--entrypoint",
            "mailctl",
            image,
            *BACKUP,
            "--host",
            "127.0.0.1",
            "--user",
            "user@example.test",
            "--backup-dir",
            OUTSIDE,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1, result.stderr
    assert REFUSED in result.stderr


# The three commands, each as it starts with nothing more given: the API
# and the TUI run with no arguments, which is the CLI's help.
COMMANDS = [
    pytest.param(BACKUP, id="cli"),
    pytest.param(("api",), id="api"),
    pytest.param(("api", "help"), id="api-help"),
    pytest.param(("tui",), id="tui"),
    pytest.param(("tui", "--version"), id="tui-version"),
]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("argv", COMMANDS)
def test_without_the_mount_the_run_is_refused(image, argv):
    """Red if a command the CLI would take as help, no arguments or
    ``help``, skips the guard for the API or the TUI, which it starts;
    or if the TUI's ``--version``, which it hands to the API and keeps
    running, does."""
    result = run_image(image, None, *argv, stdin="")

    assert result.returncode == 2, result.stderr
    assert f"{CONFIG} is not a mount" in result.stderr


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("argv", COMMANDS)
def test_as_root_the_run_is_refused(image, mount, argv):
    result = run_image(image, mount, *argv, user="0:0", stdin="")

    assert result.returncode == 2, result.stderr
    assert "running as root defeats the guard" in result.stderr


# ----------------------------------------------------------------------------
def test_the_tui_without_a_terminal_is_refused(image, mount):
    """Red if ``docker run`` without ``-t`` starts the TUI on a pipe."""
    result = run_image(image, mount, "tui", stdin="")

    assert result.returncode == 2, result.stderr
    assert "the TUI needs a terminal" in result.stderr


# ----------------------------------------------------------------------------
def api_lines(stdout: str) -> list[dict]:
    """The API's JSON lines, one message each."""
    return [json.loads(line) for line in stdout.splitlines()]


INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
SHUTDOWN = {"jsonrpc": "2.0", "id": 2, "method": "shutdown"}


# ----------------------------------------------------------------------------
def test_the_api_serves_on_stdin_and_stdout(image, mount):
    """Red if ``api`` does not reach ``mailctl-api``: the CLI would refuse
    the word, and nothing would answer ``initialize``."""
    stdin = f"{json.dumps(INITIALIZE)}\n{json.dumps(SHUTDOWN)}\n"

    result = run_image(image, mount, "api", stdin=stdin)

    assert result.returncode == 0, result.stderr
    first, second = api_lines(result.stdout)
    assert first["id"] == 1
    assert first["result"]["provider"] == "mxroute"
    assert second == {"jsonrpc": "2.0", "id": 2, "result": None}


# ----------------------------------------------------------------------------
def test_the_api_cannot_lift_the_root(image, mount):
    """Red if the API, started by the image, is not held to the root that
    the entrypoint exports over ``-e MAILCTL_BACKUP_ROOT=/``."""
    result = run_image(
        image,
        mount,
        "api",
        "--backup-dir",
        OUTSIDE,
        env={"MAILCTL_BACKUP_ROOT": "/"},
        stdin=f"{json.dumps(INITIALIZE)}\n",
    )

    (answer,) = api_lines(result.stdout)
    assert REFUSED in answer["error"]["message"], result.stderr


# Escape sequences a terminal acts on, taken out of what the TUI drew.
ESCAPES = re.compile(rb"\x1b\[[0-9;?<>=$]*[A-Za-z~]|\x1b\][^\x07]*\x07")
# The TUI's own line under the password field. The field is opened only
# when the API asks for the password, which it does for the filter list
# the TUI reads once ``initialize`` has answered.
PROMPTED = b"Enter to send it, Escape to cancel."


# ----------------------------------------------------------------------------
def test_the_tui_reaches_the_containers_own_api(image, mount):
    """Red if ``tui`` does not start ``mailctl-tui``, or it cannot start
    the image's ``mailctl-api``: no password prompt is ever drawn."""
    name = f"mailctl-test-tui-{os.getpid()}"
    command = [*docker_run(mount), "--interactive", "--tty", "--name", name]
    main, child = pty.openpty()
    fcntl.ioctl(child, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))

    process = subprocess.Popen(
        [*command, image, "tui"], stdin=child, stdout=child, stderr=child
    )
    os.close(child)
    drawn = b""
    deadline = time.monotonic() + 60

    try:
        while PROMPTED not in ESCAPES.sub(b"", drawn):
            assert time.monotonic() < deadline, ESCAPES.sub(b"", drawn)
            ready, _, _ = select.select([main], [], [], 0.5)

            if ready:
                try:
                    drawn += os.read(main, 65536)

                except OSError:  # the terminal closed: the run ended
                    break

    finally:
        # The terminal is closed before the wait: the client would block
        # writing to a terminal nobody reads.
        subprocess.run(["docker", "kill", name], capture_output=True)
        os.close(main)
        process.wait(timeout=60)

    assert PROMPTED in ESCAPES.sub(b"", drawn), ESCAPES.sub(b"", drawn)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "argv",
    [
        pytest.param((), id="default"),
        pytest.param(("--backup-dir", f"{CONFIG}/elsewhere"), id="inside"),
    ],
)
def test_a_backup_dir_in_the_mount_passes_the_guard(image, mount, argv):
    """The control: inside the mount, the run gets as far as connecting.

    Red if the guard refuses the default or a directory inside the mount,
    which would make every refusal above meaningless.
    """
    password = mount / "password"
    password.write_text("not-a-real-password\n")
    password.chmod(0o600)

    result = run_image(
        image,
        mount,
        *BACKUP,
        *argv,
        "--password-file",
        f"{CONFIG}/password",
    )

    assert result.returncode == 1
    assert "backups would be written" not in result.stderr
    assert "Connection to server failed" in result.stderr


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        pytest.param(("--version",), "mailctl ", id="cli"),
        pytest.param(("api", "--version"), "mailctl ", id="api"),
        pytest.param(("tui", "--help"), "usage: mailctl-tui", id="tui"),
    ],
)
def test_help_and_the_version_need_no_mount(image, argv, expected):
    result = run_image(image, None, *argv)

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(expected)
