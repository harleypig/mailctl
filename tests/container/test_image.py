"""The Docker image's guard on where backups go, run in the image (#230).

Builds mailctl's own image from the top-level ``Dockerfile`` and runs it
as a user would. Every backup must land in the mounted config directory,
since anything else is lost with the container: the entrypoint refuses a
run as root or without the mount, and mailctl itself refuses a backup
location outside ``MAILCTL_BACKUP_ROOT``, which the image sets, from any
source. ``/dev/shm`` is the case the filesystem does not catch: it is
writable to every user at run time.

No mail server is needed. Each refusal comes before any connection, and
the container has no network, so a guard that let a run through would
fail on connecting instead, which the control below shows.
"""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.container

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "mailctl-test-image"
CONFIG = "/config/mailctl"
OUTSIDE = "/dev/shm/backups"
REFUSED = "outside /config/mailctl, the one directory MAILCTL_BACKUP_ROOT"


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
) -> subprocess.CompletedProcess:
    """``docker run`` the image with ``argv``, offline, as the host user."""
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

    return subprocess.run(
        [*command, image, *argv],
        capture_output=True,
        text=True,
        check=False,
    )


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


# ----------------------------------------------------------------------------
def test_without_the_mount_the_run_is_refused(image):
    result = run_image(image, None, *BACKUP)

    assert result.returncode == 2
    assert f"{CONFIG} is not a mount" in result.stderr


# ----------------------------------------------------------------------------
def test_as_root_the_run_is_refused(image, mount):
    result = run_image(image, mount, *BACKUP, user="0:0")

    assert result.returncode == 2
    assert "running as root defeats the guard" in result.stderr


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
def test_help_and_the_version_need_no_mount(image):
    result = run_image(image, None, "--version")

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("mailctl ")
