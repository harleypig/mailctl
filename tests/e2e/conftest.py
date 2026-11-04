"""The use-case e2e tier: mailctl installed, then run as a user runs it.

The container tier runs ``cli.main`` in the test process, so it never
installs mailctl and cannot show whether an install route works. This
tier installs mailctl from the checkout once per route -- **pipx**,
**uv**, and **Docker** -- and runs the **installed** commands as
subprocesses (#483). Each use case is a test module here, and each test in
it runs once per route; a use case stops at its first failed step, and
the other routes and use cases still run.

What a use case gets, so it needs no framework code of its own:

* ``installed`` -- the route's install, once per run. Its ``run`` starts
  an installed command and returns its exit status and output.
* ``mail_config`` -- a config in a temporary ``XDG_CONFIG_HOME`` pointing
  at the test server, in a good form or one of three broken ones.
* ``dovecot`` -- the container tier's server (``dovecot_support``), started
  only when a test asks for a config.

The gate is the container tier's shape, with its own variable:
``MAILCTL_E2E`` must equal exactly ``"1"``, and Docker must answer.
``MAILCTL_E2E_ROUTE`` names one route, and unset means all three. ``make
teste2e ROUTE=...`` sets both; a plain ``pytest`` skips every test here.

Isolation, which is what makes a green run mean the route works:

* **Nothing lands in the user's own directories.** pipx and uv install
  into the run's sandbox through their own variables (``PIPX_HOME``,
  ``PIPX_BIN_DIR``, ``PIPX_MAN_DIR``; ``UV_TOOL_DIR``, ``UV_TOOL_BIN_DIR``,
  and ``UV_PYTHON_INSTALL_DIR`` for an interpreter uv fetches), from a copy
  of the checkout's tracked files, so a build writes nothing into the
  checkout. Docker builds under a tag of the run's own, never
  ``mailctl:dev``, which may be the operator's.
* **Nothing installed elsewhere can stand in.** An installed command runs
  with a ``PATH`` of the sandbox's bin directory plus every ``PATH`` entry
  holding no mailctl command, so a mailctl in ``~/.local/bin`` cannot
  answer for a route whose install failed; and its environment is built
  from nothing, so no ``MAILCTL_*`` setting of the developer's reaches it.
* **Teardown uninstalls with the route's own command** (#482) and fails if
  a mailctl command is still on that ``PATH``, or the image still exists.

The password is the container tier's throwaway, and held to the same bar:
it is copied file to file, never printed, formatted into an assertion, or
put on a command line.
"""

import os
import shutil
import subprocess
import tomllib
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from dovecot_support import (
    HOST,
    Server,
    docker_reason,
    new_user,
    running_dovecot,
)

GATE_FLAG = "MAILCTL_E2E"
GATE_VALUE = "1"
ROUTE_FLAG = "MAILCTL_E2E_ROUTE"
ROUTES = ("pipx", "uv", "docker")

ROOT = Path(__file__).resolve().parents[2]
TUI_DIR = Path("clients") / "tui"

# The TUI's package name is what its uninstall takes; read, not repeated.
TUI_PACKAGE = tomllib.loads((ROOT / TUI_DIR / "pyproject.toml").read_text())[
    "project"
]["name"]

# Every command an install puts on PATH. Docker has one entrypoint, and
# the first word picks the command (docs/DOCKER.md): none for the CLI.
COMMANDS = ("mailctl", "mailctl-api", "mailctl-tui")
DOCKER_WORD = {"mailctl": (), "mailctl-api": ("api",), "mailctl-tui": ("tui",)}

# Where the Docker route mounts what the host supplies; the first two are
# docs/DOCKER.md's pattern, the certificate is the test server's own.
CONFIG_MOUNT = "/config/mailctl"
PASSWORD_MOUNT = "/run/secrets/mailctl-password"
CERT_MOUNT = "/run/mailctl-e2e/tls.crt"

# Seconds. An install builds wheels or an image, which on a cold CI runner
# means downloading a base image and Textual; a command is one process.
INSTALL_TIMEOUT = 900
RUN_TIMEOUT = 120


# ############################################################################
# The gate and the routes
# ############################################################################


# ----------------------------------------------------------------------------
def gate_reason() -> str | None:
    """Return why the e2e tier must not run, or None if it may."""
    if os.environ.get(GATE_FLAG) != GATE_VALUE:
        return (
            f"e2e tier is off: set {GATE_FLAG}={GATE_VALUE} (or run "
            f"'make teste2e') to install mailctl and run it"
        )

    return docker_reason()


# ----------------------------------------------------------------------------
def pytest_runtest_setup(item):
    """Skip every test in this directory unless the gate is open."""
    reason = gate_reason()

    if reason is not None:
        pytest.skip(reason)


# ----------------------------------------------------------------------------
def selected_routes() -> tuple[str, ...]:
    """The routes this run covers: the one named, or all of them.

    An unknown name is a usage error, not an empty run: a typo in ROUTE
    would otherwise pass by testing nothing.
    """
    named = os.environ.get(ROUTE_FLAG, "")

    if not named:
        return ROUTES

    if named not in ROUTES:
        raise pytest.UsageError(
            f"{ROUTE_FLAG}={named!r} is not a route; use one of "
            f"{', '.join(ROUTES)}, or leave it unset for all"
        )

    return (named,)


# ----------------------------------------------------------------------------
def pytest_generate_tests(metafunc):
    """Run every test that takes ``route`` once per selected route.

    Session-scoped, so pytest groups the tests by route and each route is
    installed once, whatever the number of use cases.
    """
    if "route" in metafunc.fixturenames:
        metafunc.parametrize("route", selected_routes(), scope="session")


# ############################################################################
# Overrides of the offline tier's guards
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def no_network():
    """Lift the offline tier's socket block: the server is the point.

    The installed commands are other processes, which the block never
    reached; this lifts it for the readiness probe and any oracle a use
    case opens. The environment scrubbing in ``tests/conftest.py`` stays.
    """
    return None


# ############################################################################
# The test server, and a config pointing at it
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.fixture(scope="session")
def dovecot(tmp_path_factory) -> Iterator[Server]:
    """The shared test server, started only if a test asks for it."""
    with running_dovecot(tmp_path_factory.mktemp("dovecot")) as server:
        yield server


# The config variants a use case can ask for: one that works, and one for
# each refusal a first run can meet.
GOOD = "good"
NO_CONFIG = "no_config"
NO_PASSWORD = "no_password"
PASSWORD_READABLE = "password_readable"
VARIANTS = (GOOD, NO_CONFIG, NO_PASSWORD, PASSWORD_READABLE)


@dataclass(frozen=True)
class MailConfig:
    """One user's config directory, as an install reads it.

    ``xdg`` is the ``XDG_CONFIG_HOME``; mailctl reads ``xdg/mailctl``,
    which always exists (the Docker route mounts it). ``password_file`` is
    None where the variant has none.
    """

    variant: str
    xdg: Path
    user: str
    password_file: Path | None
    cert_file: Path

    # ------------------------------------------------------------------------
    @property
    def directory(self) -> Path:
        return self.xdg / "mailctl"


# ----------------------------------------------------------------------------
def write_config(
    where: Path, server: Server, user: str, variant: str = GOOD
) -> MailConfig:
    """Write a config for ``user`` on ``server`` under ``where``.

    ``good`` is a ``config.toml`` naming the server and a ``0600`` password
    file; ``no_config`` is an empty config directory; ``no_password`` names
    no password at all; ``password_readable`` is ``good`` with the password
    file at ``0644``, which mailctl refuses.
    """
    if variant not in VARIANTS:
        raise ValueError(f"unknown config variant {variant!r}")

    xdg = where / "xdg"
    directory = xdg / "mailctl"
    directory.mkdir(parents=True)

    config = MailConfig(variant, xdg, user, None, server.cert_file)

    if variant == NO_CONFIG:
        return config

    lines = [
        'provider = "mxroute"',
        f'host = "{HOST}"',
        f'imap_host = "{HOST}"',
        f'user = "{user}"',
        f"imap_port = {server.imap_port}",
        f"sieve_port = {server.sieve_port}",
        'sieve_tls = "starttls"',
    ]

    password_file = None

    if variant != NO_PASSWORD:
        # Copied file to file, so the value never passes through a string
        # anything could print; made 0600 before a byte is in it.
        password_file = where / "password"
        password_file.touch(mode=0o600)
        password_file.write_bytes(server.password_file.read_bytes())

        if variant == PASSWORD_READABLE:
            password_file.chmod(0o644)

        lines.append(f'password_file = "{password_file}"')

    (directory / "config.toml").write_text("\n".join(lines) + "\n")

    return MailConfig(variant, xdg, user, password_file, server.cert_file)


# ----------------------------------------------------------------------------
@pytest.fixture
def mail_config(dovecot, tmp_path) -> Callable[..., MailConfig]:
    """A factory: ``mail_config(variant)``, each call a new, empty mailbox."""
    calls = iter(range(1_000_000))

    def make(variant: str = GOOD) -> MailConfig:
        return write_config(
            tmp_path / f"config-{next(calls)}", dovecot, new_user(), variant
        )

    return make


# ############################################################################
# The install, once per route
# ############################################################################


@dataclass
class Result:
    """What one installed command did."""

    code: int
    out: str
    err: str


@dataclass
class Installed:
    """One route's install, and the way to run what it installed.

    ``path`` is the ``PATH`` every installed command runs with; for Docker
    it is unused, and ``image`` is the tag the route built.
    """

    route: str
    sandbox: Path
    bin_dir: Path
    path: str
    image: str | None = None

    # ------------------------------------------------------------------------
    def run(
        self,
        command: str,
        *args: str,
        config: MailConfig | None = None,
    ) -> Result:
        """Run an installed ``command`` with ``args``, as a user would.

        ``command`` is one of ``COMMANDS``. With ``config`` the command
        reads that config and reaches the test server; without it, it
        reads an empty config directory and has no server to reach.
        """
        if command not in COMMANDS:
            raise ValueError(f"{command!r} is not an installed command")

        if self.image is not None:
            return self._docker_run(command, args, config)

        env = {
            "PATH": self.path,
            "HOME": str(self.sandbox / "home"),
            "XDG_CONFIG_HOME": str(
                config.xdg if config else self.sandbox / "no-config"
            ),
            "XDG_STATE_HOME": str(self.sandbox / "state"),
            "LANG": "C.UTF-8",
            "NO_COLOR": "1",
            "COLUMNS": "80",
        }

        if config is not None:
            env["SSL_CERT_FILE"] = str(config.cert_file)

        # A new session has no controlling terminal, so a password prompt
        # reads the empty stdin and ends rather than waiting on the tty of
        # whoever ran pytest.
        done = subprocess.run(
            [command, *args],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=RUN_TIMEOUT,
            start_new_session=True,
        )

        return Result(done.returncode, done.stdout, done.stderr)

    # ------------------------------------------------------------------------
    def _docker_run(
        self, command: str, args: tuple[str, ...], config: MailConfig | None
    ) -> Result:
        """``docker run`` the route's image as docs/DOCKER.md says to."""
        assert self.image is not None

        name = f"mailctl-e2e-run-{uuid.uuid4().hex[:12]}"
        argv = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--security-opt",
            "no-new-privileges",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
        ]

        if config is None:
            argv += ["--network", "none"]

        else:
            # The test server is published on the host's loopback, which
            # only the host's network reaches.
            argv += [
                "--network",
                "host",
                "--volume",
                f"{config.directory}:{CONFIG_MOUNT}",
                "--volume",
                f"{config.cert_file}:{CERT_MOUNT}:ro",
                "--env",
                f"SSL_CERT_FILE={CERT_MOUNT}",
            ]

            if config.password_file is not None:
                argv += [
                    "--volume",
                    f"{config.password_file}:{PASSWORD_MOUNT}:ro",
                    "--env",
                    f"MAILCTL_PASSWORD_FILE={PASSWORD_MOUNT}",
                ]

        argv += [self.image, *DOCKER_WORD[command], *args]

        # The container outlives a client killed by the timeout, so the
        # kill stops it too.
        try:
            done = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=RUN_TIMEOUT,
            )

        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", name], capture_output=True)
            raise

        return Result(done.returncode, done.stdout, done.stderr)

    # ------------------------------------------------------------------------
    def leftovers(self) -> list[str]:
        """What is still installed: commands on ``path``, or the image."""
        if self.image is not None:
            probe = subprocess.run(
                ["docker", "image", "inspect", self.image],
                capture_output=True,
                check=False,
            )

            return [self.image] if probe.returncode == 0 else []

        return [c for c in COMMANDS if shutil.which(c, path=self.path)]


# ----------------------------------------------------------------------------
def run_path(bin_dir: Path) -> str:
    """``bin_dir``, then each ``PATH`` entry that holds no mailctl command.

    The rest of ``PATH`` stays, so a command an install needs at run time
    is still found; what goes is anywhere a mailctl from outside this run
    could answer instead of the one installed.
    """
    rest = [
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and not any((Path(entry) / c).exists() for c in COMMANDS)
    ]

    return os.pathsep.join([str(bin_dir), *rest])


# ----------------------------------------------------------------------------
def copy_checkout(destination: Path) -> Path:
    """Copy the checkout's tracked files, as they are on disk, to install.

    A build from the checkout itself writes ``build/`` and ``*.egg-info``
    into it; a copy keeps the checkout as it was. Tracked files only, so a
    stray ``.venv`` or build directory is not installed from.
    """
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        capture_output=True,
        check=True,
    )

    for name in listed.stdout.decode().split("\0"):
        source = ROOT / name

        if name and source.is_file():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    return destination


# ----------------------------------------------------------------------------
def step(argv: list[str], env: dict[str, str], what: str) -> None:
    """Run one install or uninstall command, failing with its output."""
    try:
        done = subprocess.run(
            argv,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=INSTALL_TIMEOUT,
        )

    except subprocess.TimeoutExpired:
        pytest.fail(f"{what} did not finish in {INSTALL_TIMEOUT}s")

    if done.returncode != 0:
        pytest.fail(
            f"{what} failed ({done.returncode}): {' '.join(argv)}\n"
            f"{done.stdout}{done.stderr}",
            pytrace=False,
        )


# ----------------------------------------------------------------------------
def route_commands(
    route: str, source: Path, image: str
) -> tuple[list[list[str]], list[list[str]]]:
    """The route's install commands, and its uninstall commands, in order.

    Each pair is what the README, docs/TUI.md, and docs/DOCKER.md tell a
    user to type, from the copied checkout.
    """
    tui = str(source / TUI_DIR)

    if route == "pipx":
        return (
            [["pipx", "install", str(source)], ["pipx", "install", tui]],
            [
                ["pipx", "uninstall", TUI_PACKAGE],
                ["pipx", "uninstall", "mailctl"],
            ],
        )

    if route == "uv":
        return (
            [
                ["uv", "tool", "install", str(source)],
                ["uv", "tool", "install", tui],
            ],
            [
                ["uv", "tool", "uninstall", TUI_PACKAGE],
                ["uv", "tool", "uninstall", "mailctl"],
            ],
        )

    # `make image` from the copy, under the run's own tag.
    return (
        [["make", "-C", str(source), "image", f"IMAGE={image}"]],
        [["docker", "image", "rm", image]],
    )


# ----------------------------------------------------------------------------
@pytest.fixture(scope="session")
def installed(route, tmp_path_factory) -> Iterator[Installed]:
    """Install mailctl and the TUI by ``route``; uninstall them after.

    Teardown fails if a mailctl command is still on the run's ``PATH``, or
    the image still exists, once every uninstall command has run.
    """
    sandbox = tmp_path_factory.mktemp(f"e2e-{route}")
    bin_dir = sandbox / "bin"
    bin_dir.mkdir()

    # Builds and the installers' own scratch stay in the sandbox, which is
    # removed after; a host whose /tmp is small never holds a venv there.
    scratch = sandbox / "tmp"
    scratch.mkdir()

    env = dict(os.environ)
    env.update(
        {
            "TMPDIR": str(scratch),
            "PIPX_HOME": str(sandbox / "pipx"),
            "PIPX_BIN_DIR": str(bin_dir),
            "PIPX_MAN_DIR": str(sandbox / "man"),
            "UV_TOOL_DIR": str(sandbox / "uv-tools"),
            "UV_TOOL_BIN_DIR": str(bin_dir),
            "UV_PYTHON_INSTALL_DIR": str(sandbox / "uv-python"),
        }
    )

    image = f"mailctl-e2e:{uuid.uuid4().hex[:12]}"
    source = copy_checkout(sandbox / "checkout")
    install, uninstall = route_commands(route, source, image)

    result = Installed(
        route=route,
        sandbox=sandbox,
        bin_dir=bin_dir,
        path=run_path(bin_dir),
        image=image if route == "docker" else None,
    )

    try:
        for argv in install:
            step(argv, env, f"{route} install")

    except BaseException:
        # The install's own failure is the one to report; what it left is
        # removed on the way out, and not reported over it.
        uninstall_all(uninstall, env, result)
        raise

    try:
        yield result

    finally:
        left = uninstall_all(uninstall, env, result)

    if left:
        pytest.fail(f"{route} uninstall {left}", pytrace=False)


# ----------------------------------------------------------------------------
def uninstall_all(
    uninstall: list[list[str]], env: dict[str, str], installed: Installed
) -> str:
    """Run every uninstall command, remove the sandbox, and say what is
    left: an empty string, or what is still installed and which commands
    failed. Every command runs even where one fails, since a command left
    behind would shadow the next run's install.
    """
    failures = []

    for argv in uninstall:
        done = subprocess.run(
            argv,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=INSTALL_TIMEOUT,
        )

        if done.returncode != 0:
            failures.append(f"{' '.join(argv)}: {done.stderr.strip()}")

    left = installed.leftovers()
    shutil.rmtree(installed.sandbox, ignore_errors=True)

    if not left:
        return ""

    return f"left {', '.join(left)} behind" + "".join(
        f"; {failure}" for failure in failures
    )
