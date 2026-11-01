"""The live tier's read gate: which settings open it (#369, #380).

``tests/live/conftest.py`` skips every live test unless the account is
fully configured. A credential counts if any rung mailctl's own config
ladder reads is set -- ``MAILCTL_PASSWORD_FILE``, ``MAILCTL_PASSWORD_CMD``,
or ``MAILCTL_PASSWORD``. Before #369 the gate knew only the last two, so
an environment valid for every mailctl command was refused here.

Offline. The gate tests presence only, so the values below are
placeholders: the file named does not exist, and nothing here (or in the
gate) opens, reads, or prints one.

An account may also be named by an env file, ``MAILCTL_LIVE_ENV_FILE``
(#380), read by mailctl's own loader: the gate and ``live_config`` see
what any mailctl command given ``--env-file`` would, the file's mode is
checked where it sets ``MAILCTL_PASSWORD``, and nothing it holds is
exported into the process. Those tests write real files under
``tmp_path``; the password in them is a sentinel no output may carry.

Not covered: the ``MAILCTL_LIVE`` flag's exactness and the host/user
check, which this file sets to their open state and leaves to the gate's
own docstring; and the write gate's own values, which are
``test_live_write_gate.py``. That every live write test stands behind
that gate is pinned at the end (#388).
"""

import ast
import importlib.util
import os
from pathlib import Path

import pytest

PASSWORD_SETTINGS = (
    "MAILCTL_PASSWORD_FILE",
    "MAILCTL_PASSWORD_CMD",
    "MAILCTL_PASSWORD",
)


ENV_FILE_VAR = "MAILCTL_LIVE_ENV_FILE"

# Stands in for a password: if it reaches a reason, a repr, or the
# environment, the env file's value escaped the loader's Secret.
SENTINEL = "s3ntinel-380-not-a-password"


# ----------------------------------------------------------------------------
@pytest.fixture
def live_module():
    """The live tier's conftest, loaded under a name of its own.

    Imported as plain ``conftest`` it would collide with ``tests/
    conftest.py``; loading it by path keeps the two apart and registers
    none of its fixtures here.
    """
    path = Path(__file__).parent / "live" / "conftest.py"
    spec = importlib.util.spec_from_file_location("live_gate_conftest", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


# ----------------------------------------------------------------------------
@pytest.fixture
def live_gate(live_module):
    """The live tier's ``gate_reason``."""
    return live_module.gate_reason


# ----------------------------------------------------------------------------
@pytest.fixture
def open_but_credential(monkeypatch):
    """Everything the gate checks set open, except a credential.

    ``tests/conftest.py`` has already cleared every ``MAILCTL_*``
    variable, so the three credential settings start unset.
    """
    monkeypatch.setenv("MAILCTL_LIVE", "1")
    monkeypatch.setenv("MAILCTL_HOST", "mail.example.invalid")
    monkeypatch.setenv("MAILCTL_USER", "user@example.invalid")

    for name in PASSWORD_SETTINGS:
        monkeypatch.delenv(name, raising=False)

    return monkeypatch


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", PASSWORD_SETTINGS)
def test_each_credential_rung_alone_opens_the_gate(
    live_gate, open_but_credential, name
):
    """Red if the gate refuses a rung mailctl reads -- the #369 defect.

    ``MAILCTL_PASSWORD_FILE`` alone was refused before the fix. The path
    does not exist: the gate must not look at it.
    """
    open_but_credential.setenv(name, "/nonexistent/369-placeholder")

    assert live_gate() is None


# ----------------------------------------------------------------------------
def test_no_credential_keeps_it_shut(live_gate, open_but_credential):
    """Red if the gate opens with none of the three set.

    The refusal must name every rung, so the reader is pointed at the
    one their setup uses rather than told only two exist.
    """
    reason = live_gate()

    assert reason is not None

    for name in PASSWORD_SETTINGS:
        assert name in reason


# ############################################################################
# An env file names the account (#380)
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.fixture
def only_the_flag(monkeypatch):
    """``MAILCTL_LIVE=1`` and nothing else: no account in the environment.

    ``tests/conftest.py`` has cleared every ``MAILCTL_*`` variable and
    pointed ``XDG_CONFIG_HOME`` at an empty directory, so no
    ``config.toml`` supplies a setting either.
    """
    monkeypatch.setenv("MAILCTL_LIVE", "1")

    return monkeypatch


# ----------------------------------------------------------------------------
def write_env_file(path: Path, lines: list[str], mode: int) -> Path:
    """Write ``lines`` to ``path`` and set its mode, which is the point."""
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    path.chmod(mode)

    return path


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "credential",
    [
        "MAILCTL_PASSWORD_FILE=/nonexistent/380-placeholder",
        "MAILCTL_PASSWORD_CMD=false",
        f"MAILCTL_PASSWORD={SENTINEL}",
    ],
)
def test_an_env_file_alone_opens_the_gate(
    live_module, only_the_flag, tmp_path, credential
):
    """Red if the gate reads the account from ``os.environ`` only -- #380.

    Before the fix this refused with *needs MAILCTL_HOST, MAILCTL_USER in
    the environment*: the file was never read. Each credential rung is
    tried in the file alone. The password file named does not exist and
    the command is never run: the gate tests presence, as before.
    """
    env_file = write_env_file(
        tmp_path / "account.env",
        [
            "MAILCTL_HOST=mail.example.invalid",
            "MAILCTL_USER=user@example.invalid",
            credential,
        ],
        0o600,
    )
    only_the_flag.setenv(ENV_FILE_VAR, str(env_file))

    assert live_module.gate_reason() is None

    config = live_module.live_settings()

    assert config.host == "mail.example.invalid"
    assert config.user == "user@example.invalid"
    assert config.env_file is not None
    assert config.env_file.path == env_file


# ----------------------------------------------------------------------------
def test_the_env_file_password_is_a_secret(
    live_module, only_the_flag, tmp_path
):
    """Red if ``live_config`` holds the file's password as plain text.

    mailctl's loader wraps ``MAILCTL_PASSWORD`` in ``Secret`` as the line
    is parsed; a hand-rolled reader in the live tier would not, and the
    value would then show in a ``repr`` -- a traceback frame's, say.
    """
    env_file = write_env_file(
        tmp_path / "account.env",
        [
            "MAILCTL_HOST=mail.example.invalid",
            "MAILCTL_USER=user@example.invalid",
            f"MAILCTL_PASSWORD={SENTINEL}",
        ],
        0o600,
    )
    only_the_flag.setenv(ENV_FILE_VAR, str(env_file))

    config = live_module.live_settings()

    assert config.password().reveal() == SENTINEL
    assert SENTINEL not in repr(config)
    assert SENTINEL not in repr(config.env_file)
    assert SENTINEL not in repr(config.password())


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644])
def test_a_readable_env_file_with_a_password_keeps_it_shut(
    live_module, only_the_flag, tmp_path, mode
):
    """Red if the gate opens on an env file others can read.

    mailctl refuses such a file, coded ``env_file_mode``; the live tier
    must refuse it too rather than read around the check. The refusal
    names the fix and never the value.
    """
    env_file = write_env_file(
        tmp_path / "account.env",
        [
            "MAILCTL_HOST=mail.example.invalid",
            "MAILCTL_USER=user@example.invalid",
            f"MAILCTL_PASSWORD={SENTINEL}",
        ],
        mode,
    )
    only_the_flag.setenv(ENV_FILE_VAR, str(env_file))

    reason = live_module.gate_reason()

    assert reason is not None
    assert "env_file_mode" in reason
    assert f"chmod 600 {env_file}" in reason
    assert SENTINEL not in reason


# ----------------------------------------------------------------------------
def test_a_named_env_file_that_cannot_be_read_keeps_it_shut(
    live_module, only_the_flag, tmp_path
):
    """Red if a mistyped path falls through to whatever else is set.

    The file was named for this run; a run that cannot read it must not
    go on against the account the environment happens to describe.
    """
    only_the_flag.setenv("MAILCTL_HOST", "mail.example.invalid")
    only_the_flag.setenv("MAILCTL_USER", "user@example.invalid")
    only_the_flag.setenv("MAILCTL_PASSWORD_FILE", "/nonexistent/380")
    only_the_flag.setenv(ENV_FILE_VAR, str(tmp_path / "missing.env"))

    reason = live_module.gate_reason()

    assert reason is not None
    assert "env_file_unreadable" in reason


# ----------------------------------------------------------------------------
def test_an_env_file_without_a_credential_keeps_it_shut(
    live_module, only_the_flag, tmp_path
):
    """Red if host and user from the file are taken as a whole account."""
    env_file = write_env_file(
        tmp_path / "account.env",
        [
            "MAILCTL_HOST=mail.example.invalid",
            "MAILCTL_USER=u@example.invalid",
        ],
        0o600,
    )
    only_the_flag.setenv(ENV_FILE_VAR, str(env_file))

    reason = live_module.gate_reason()

    assert reason is not None

    for name in PASSWORD_SETTINGS:
        assert name in reason


# ----------------------------------------------------------------------------
def test_the_env_file_is_never_exported(live_module, only_the_flag, tmp_path):
    """Red if opening the gate or building ``live_config`` writes
    ``os.environ`` -- the ``set -a; . file`` step #380 removes, done by
    the code instead of the shell.
    """
    env_file = write_env_file(
        tmp_path / "account.env",
        [
            "MAILCTL_HOST=mail.example.invalid",
            "MAILCTL_USER=user@example.invalid",
            f"MAILCTL_PASSWORD={SENTINEL}",
        ],
        0o600,
    )
    only_the_flag.setenv(ENV_FILE_VAR, str(env_file))
    before = dict(os.environ)

    assert live_module.gate_reason() is None

    live_module.live_settings().password()

    assert dict(os.environ) == before
    assert SENTINEL not in os.environ.values()


# ############################################################################
# Every live write test stands behind the write gate (#388)
# ############################################################################

LIVE_DIR = Path(__file__).parent / "live"

# The fixtures that reach the account through ``write_mailbox``, the one
# place the second opt-in (``MAILCTL_LIVE_WRITE=1``) is checked.
WRITE_GUARDS = {"guarded_scripts", "scratch_folder"}


# ----------------------------------------------------------------------------
def live_write_tests() -> dict[str, list[str]]:
    """``module::test`` -> the fixtures it asks for, for every live write test.

    Derived from the files named ``test_live_write_*.py`` under
    ``tests/live``, read statically so nothing in them is imported or run.
    """
    found = {}

    for path in sorted(LIVE_DIR.glob("test_live_write_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith(
                "test_"
            ):
                found[f"{path.name}::{node.name}"] = [
                    arg.arg for arg in node.args.args
                ]

    return found


# ----------------------------------------------------------------------------
def test_the_walk_reaches_every_live_write_test():
    """Red if the glob or the parse reads nothing, or reads the wrong set.

    The smoke test and the four #388 paths are known positives, so a
    pattern that matches no file, or a parse that finds no function,
    fails here rather than letting the check below pass on an empty set.
    """
    names = set(live_write_tests())

    assert {
        "test_live_write_smoke.py::"
        "test_a_guarded_write_leaves_the_account_as_it_was",
        "test_live_write_paths.py::test_apply_moves_existing_mail",
        "test_live_write_paths.py::test_folder_create_makes_and_subscribes_it",
        "test_live_write_paths.py::"
        "test_filter_remove_takes_out_only_its_filter",
        "test_live_write_paths.py::"
        "test_filterset_restore_puts_back_the_backup",
    } <= names


# ----------------------------------------------------------------------------
def test_every_live_write_test_asks_for_a_write_guard():
    """Red if a live write test can write without ``MAILCTL_LIVE_WRITE=1``.

    The second opt-in lives in ``write_mailbox``, and a test reaches it
    only through ``guarded_scripts`` or ``scratch_folder``. A test that
    asks for neither has no gate past ``MAILCTL_LIVE=1``, so a read-only
    ``make testlive`` would run it. Taking the fixtures out of one test's
    signature turns this red.
    """
    unguarded = [
        name
        for name, fixtures in live_write_tests().items()
        if not WRITE_GUARDS & set(fixtures)
    ]

    assert unguarded == []


# ----------------------------------------------------------------------------
def test_each_write_guard_reaches_the_write_gate():
    """Red if either guard stops depending on ``write_mailbox``.

    The check above trusts the two guards to carry the gate. This pins
    that each fixture asks for ``write_mailbox``, the fixture that skips
    without ``MAILCTL_LIVE_WRITE=1``.
    """
    tree = ast.parse(
        (Path(__file__).parent / "live_write.py").read_text(encoding="utf-8")
    )
    fixtures = {
        node.name: [arg.arg for arg in node.args.args]
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in WRITE_GUARDS
    }

    assert set(fixtures) == WRITE_GUARDS

    for name, arguments in fixtures.items():
        assert "write_mailbox" in arguments, name
