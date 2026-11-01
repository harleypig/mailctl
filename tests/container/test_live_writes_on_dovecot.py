"""The live tier's write-path tests, run here first against Dovecot (#388).

``tests/live/test_live_write_paths.py`` and the smoke test beside it
write to the real MXroute account. Before anyone points them there, their
test functions run here, unchanged, against a throwaway mailbox on the
container. They use the same
guards (``guarded_scripts``, ``scratch_folder``), built on the same
:class:`Mailbox` type, so what passes here is the code an operator's run
executes.

The live tests are loaded by path, not copied, so a change to them is a
change to what runs here. Two things here make the run look like an
operator's:

* **The account is named by an env file.** The ``account`` fixture
  exports mailctl's settings. Here they are moved into a 0600 file that
  ``MAILCTL_LIVE_ENV_FILE`` names, and the environment is cleared of them,
  as in ``make testlive MAILCTL_LIVE_ENV_FILE=PATH``. A test that called
  ``cli.main`` without the file would then find no host and fail, which is
  how ``run_mailctl`` is seen to hand it on.
* **The account has a filter set already**, a Roundcube-shaped one, as
  the real account does. The guard therefore has something of the user's
  own to put back, and the restore test has something to back up.

After each test the guards confirm the account is as it was. This module
then checks again with its own oracle: the script file on the container's
disk, and IMAPClient's folder listing. It also checks that the password
reached no output.
"""

import importlib.util
import os
from pathlib import Path

import pytest
from live_write import LIVE_ENV_FILE, SCRATCH_MARKER

pytestmark = pytest.mark.container

LIVE_DIR = Path(__file__).resolve().parents[1] / "live"

# Roundcube's script name and shape, with a CR in one line ending and no
# final newline: a text round trip would change both (#90).
NAME = "managesieve"
ORIGINAL = (
    'require ["fileinto"];\r\n'
    "# rule:[keep-boss]\n"
    'if header :contains "from" "boss@example.com"\n'
    "{\n"
    '\tfileinto "INBOX";\n'
    "\tstop;\n"
    "}"
)


# ----------------------------------------------------------------------------
def _load_live_tests() -> dict:
    """Every test in ``tests/live/test_live_write_*.py``, by its name.

    Each module is imported by path, under a name of its own, because
    ``tests/live`` is not a package. A module's own ``pytestmark``
    (``live``) stays with it; the functions taken from it carry this
    module's mark instead. The glob is the one ``test_live_gate.py``
    holds to the write gate, so the set run here is the set gated there.
    """
    tests = {}

    for path in sorted(LIVE_DIR.glob("test_live_write_*.py")):
        spec = importlib.util.spec_from_file_location(
            f"{path.stem}_under_container", path
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        tests.update(
            (name, value)
            for name, value in vars(module).items()
            if name.startswith("test_")
        )

    return tests


# The live tests, by their own names, so pytest collects them here.
# Derived rather than listed: a test added there runs here with no edit.
_live = _load_live_tests()
LIVE_TEST_NAMES = sorted(_live)

globals().update(_live)


# ----------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def as_an_operator_runs_it(account, monkeypatch, tmp_path, capsys):
    """Seed a filter set, name the account by an env file, then check.

    Requested ahead of the live test's own fixtures, so it is set up
    before the guards capture and torn down after they restore. Its checks
    therefore read the account the guards left behind.
    """
    account.seed_script(NAME, ORIGINAL)
    stored = account.script_bytes(NAME)

    settings = {
        name: value
        for name, value in os.environ.items()
        if name.startswith("MAILCTL_")
    }
    env_file = tmp_path / "account.env"
    env_file.write_text(
        "".join(f"{name}={value}\n" for name, value in settings.items())
    )
    env_file.chmod(0o600)

    for name in settings:
        monkeypatch.delenv(name)

    monkeypatch.setenv(LIVE_ENV_FILE, str(env_file))

    yield

    output = capsys.readouterr()
    password = account.server.password_file.read_text()

    # A bool, so a failure prints True and never the strings compared.
    leaked = password in output.out + output.err

    assert not leaked

    with account.imap() as client:
        listed = {entry[2] for entry in client.list_folders()}
        subscribed = {entry[2] for entry in client.list_sub_folders()}

    assert account.script_bytes(NAME) == stored
    assert account.active_script() == NAME
    assert not [name for name in listed if SCRATCH_MARKER in name]
    assert not [name for name in subscribed if SCRATCH_MARKER in name]


# ----------------------------------------------------------------------------
def test_every_live_write_path_is_collected_here():
    """Red if the live modules' tests stop reaching this module.

    The smoke test and the four paths #388 covers are known positives. If
    the glob, the loader, or the name filter fails, the list comes back
    short and this test says so. It does not pass by finding nothing.
    """
    assert LIVE_TEST_NAMES == [
        "test_a_guarded_write_leaves_the_account_as_it_was",
        "test_apply_moves_existing_mail",
        "test_filter_remove_takes_out_only_its_filter",
        "test_filterset_restore_puts_back_the_backup",
        "test_folder_create_makes_and_subscribes_it",
    ]
