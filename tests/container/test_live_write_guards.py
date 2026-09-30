"""The live tier's write guards, proved against a real server first (#9).

``tests/live_write.py`` is what stands between a writing live test and the
user's real filters and mail. Before it is ever pointed at that account it
runs here, against a throwaway Dovecot, through the same ``Mailbox`` the
live tier builds -- so what is proved is the code the live tier runs.

Two kinds of proof. The guards used as context managers show what each
does when a block passes, raises, or is interrupted. The guards used as
**pytest fixtures**, in a child pytest run, show that pytest really does
tear them down after a failing test and after Ctrl-C -- the claim a
writing live test relies on, and one that no in-process test can make.

The oracle is the server, never the guard: the script file on the
container's disk for bytes, and sievelib's and IMAPClient's own clients
for listings, as everywhere in this tier.

Every test names, in its docstring, the break that turns it red.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from live_write import (
    SCRATCH_MARKER,
    FolderRefused,
    RestoreNotConfirmed,
    ScriptGuard,
    claim_scratch_folder,
    guard_scripts,
)

pytestmark = pytest.mark.container

TESTS = Path(__file__).resolve().parents[1]

# A Roundcube-shaped script in the two byte shapes a text round trip loses
# (#90): a CR in its first line ending, and no final newline.
ORIGINAL = (
    'require ["fileinto"];\r\n'
    "# rule:[keep-boss]\n"
    'if header :contains "from" "boss@example.com"\n'
    "{\n"
    '\tfileinto "INBOX";\n'
    "\tstop;\n"
    "}"
)

NAME = "managesieve"

MESSAGE = (
    b"From: Someone <someone@example.test>\r\n"
    b"To: user@example.test\r\n"
    b"Subject: scratch\r\n"
    b"\r\n"
    b"Test-owned.\r\n"
)


# ----------------------------------------------------------------------------
def scripts(account) -> tuple[str | None, list[str]]:
    """(active, every script name), from sievelib's own client."""
    with account.sieve() as client:
        active, others = client.listscripts()

    return active or None, sorted([*others, *([active] if active else [])])


# ----------------------------------------------------------------------------
def folders(account) -> tuple[set[str], set[str]]:
    """Every folder, and every subscribed one, from IMAPClient itself."""
    with account.imap() as client:
        listed = {entry[2] for entry in client.list_folders()}
        subscribed = {entry[2] for entry in client.list_sub_folders()}

    return listed, subscribed


# ----------------------------------------------------------------------------
def leaked(account, *texts: str) -> bool:
    """Whether the run's password appears in any of ``texts``.

    Returned as a bool so a failing assertion prints ``True``, never the
    two strings it compared.
    """
    password = account.server.password_file.read_text()

    return any(password in text for text in texts)


# ############################################################################
# The script guard
# ############################################################################


# ----------------------------------------------------------------------------
def test_an_unchanged_account_is_not_written_to(account, tmp_path):
    """Writes are only what a restore needs, so none when nothing changed.

    Red if the restore writes unconditionally -- a PUTSCRIPT, SETACTIVE, or
    DELETESCRIPT on an account the test left alone.
    """
    account.seed_script(NAME, ORIGINAL)

    with guard_scripts(account.mailbox(), tmp_path / "saved") as guard:
        pass

    assert guard.writes == []


# ----------------------------------------------------------------------------
def test_a_changed_script_is_put_back_byte_for_byte(account, tmp_path):
    """mailctl ``add`` rewrites the active script; the guard undoes it.

    Red if the changed script is not put back, if it is put back through a
    text round trip (the CR or the missing final newline changes), or if
    the saved copy on disk is not the server's bytes.
    """
    account.seed_script(NAME, ORIGINAL)
    stored = account.script_bytes(NAME)

    with guard_scripts(account.mailbox(), tmp_path / "saved") as guard:
        added = account.run(
            "filter",
            "add",
            "--subject",
            "guarded",
            "--name",
            "guarded",
            "--keep",
        )

        assert added.code == 0, added.err
        assert account.script_bytes(NAME) != stored

    assert guard.writes == [f"put back {NAME!r}"]
    assert account.script_bytes(NAME) == stored
    assert scripts(account) == (NAME, [NAME])
    assert (guard.saved / "00.sieve").read_bytes() == stored
    assert (guard.saved / "00.sieve").stat().st_mode & 0o077 == 0


# ----------------------------------------------------------------------------
def test_an_account_with_no_script_is_left_with_none(account, tmp_path):
    """A fresh account: ``add`` creates and activates a script; it goes.

    Red if the guard does not deactivate the script the test made active,
    or does not delete a script that did not exist before.
    """
    with guard_scripts(account.mailbox(), tmp_path / "saved"):
        added = account.run(
            "filter",
            "add",
            "--subject",
            "guarded",
            "--name",
            "guarded",
            "--keep",
        )

        assert added.code == 0, added.err
        assert scripts(account)[0] is not None

    assert scripts(account) == (None, [])


# ----------------------------------------------------------------------------
def test_a_raising_block_is_restored_and_the_error_kept(account, tmp_path):
    """The restore is a ``finally``: it runs, and the test's error wins.

    Red if an exception in the block skips the restore, or if the restore
    swallows the exception the test raised.
    """
    account.seed_script(NAME, ORIGINAL)
    stored = account.script_bytes(NAME)

    with (
        pytest.raises(RuntimeError, match="the test failed"),
        guard_scripts(account.mailbox(), tmp_path / "saved"),
    ):
        account.seed_script("other", "keep;\n")

        raise RuntimeError("the test failed")

    assert account.script_bytes(NAME) == stored
    assert scripts(account) == (NAME, [NAME])


# ----------------------------------------------------------------------------
def test_a_restore_that_cannot_be_confirmed_fails_loudly(
    account, tmp_path, monkeypatch
):
    """A server left holding something else is a failure, never a pass.

    The break is simulated at the one seam the guard writes through: its
    PUTSCRIPT stores a different script, as a server that rewrote it
    would. Red if the confirmation passes anyway, if its message does not
    name the saved copy, or if the saved copy is not the original's bytes.
    """
    account.seed_script(NAME, ORIGINAL)
    stored = account.script_bytes(NAME)

    def wrong_put(self, client, name, content):
        return client.putscript(name, content + "\n# not what was saved\n")

    monkeypatch.setattr(ScriptGuard, "_put", wrong_put)

    with (
        pytest.raises(RestoreNotConfirmed) as failure,
        guard_scripts(account.mailbox(), tmp_path / "saved") as guard,
    ):
        account.seed_script(NAME, "keep;\n")

    message = str(failure.value)

    assert NAME in message
    assert str(guard.saved) in message
    assert (guard.saved / "00.sieve").read_bytes() == stored
    assert not leaked(account, message)


# ############################################################################
# The guards as pytest fixtures, in a child run
# ############################################################################

CHILD_CONFTEST = """
    import os
    from pathlib import Path

    import pytest
    from live_write import Mailbox, guarded_scripts, scratch_folder

    from mailctl.config import Secret


    @pytest.fixture
    def write_mailbox():
        path = Path(os.environ["GUARD_PASSWORD_FILE"])

        return Mailbox(
            sieve_host="localhost",
            sieve_port=int(os.environ["GUARD_SIEVE_PORT"]),
            sieve_tls="starttls",
            imap_host="localhost",
            imap_port=int(os.environ["GUARD_IMAP_PORT"]),
            imap_tls="ssl",
            user=os.environ["GUARD_USER"],
            password=lambda: Secret(path.read_text()),
        )
"""

CHILD_TEST = """
    from live_write import _sieve
    from sievelib.managesieve import Client


    def test_writes_then_ends(guarded_scripts, scratch_folder):
        scratch_folder.append(b"Subject: scratch\\r\\n\\r\\nTest-owned.\\r\\n")

        with _sieve(guarded_scripts.mailbox, Client) as client:
            assert client.putscript("managesieve", "keep;\\n")
            assert client.putscript("extra", "keep;\\n")
            assert client.setactive("extra")

        {ending}
"""


# ----------------------------------------------------------------------------
def run_child(account, tmp_path, ending: str) -> subprocess.CompletedProcess:
    """Run one test that uses both fixtures, ending with ``ending``.

    The child is a separate pytest process, so what is seen is pytest's
    own handling of a fixture's teardown. It reaches the container through
    the same settings as this test, and the password only as a file path.
    """
    child = tmp_path / "child"
    child.mkdir()

    (child / "conftest.py").write_text(textwrap.dedent(CHILD_CONFTEST))
    (child / "test_child.py").write_text(
        textwrap.dedent(CHILD_TEST).replace("{ending}", ending)
    )

    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(TESTS), str(TESTS.parent)]),
        "GUARD_PASSWORD_FILE": str(account.server.password_file),
        "GUARD_SIEVE_PORT": str(account.server.sieve_port),
        "GUARD_IMAP_PORT": str(account.server.imap_port),
        "GUARD_USER": account.user,
    }

    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=child,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


# ----------------------------------------------------------------------------
def assert_restored(account, stored: bytes) -> None:
    """The script, the active choice, and the folders are as they were."""
    listed, subscribed = folders(account)

    assert account.script_bytes(NAME) == stored
    assert scripts(account) == (NAME, [NAME])
    assert not [name for name in listed if SCRATCH_MARKER in name]
    assert not [name for name in subscribed if SCRATCH_MARKER in name]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("ending", "code", "summary"),
    [
        ("assert 2 + 2 == 5", 1, "1 failed"),
        ("raise KeyboardInterrupt", 2, "KeyboardInterrupt"),
        ("pass", 0, "1 passed"),
    ],
    ids=["failing", "interrupted", "passing"],
)
def test_pytest_tears_the_fixtures_down(
    account, tmp_path, ending, code, summary
):
    """A test that fails, is interrupted, or passes leaves nothing behind.

    The child test rewrites the active script, adds and activates a second
    one, and appends to its scratch folder, then ends. Red if pytest skips
    either fixture's teardown for that ending, if the restore is not
    confirmed (the child's summary gains an error), or if the password
    reaches the child's output.
    """
    account.seed_script(NAME, ORIGINAL)
    stored = account.script_bytes(NAME)

    result = run_child(account, tmp_path, ending)
    output = result.stdout + result.stderr

    assert result.returncode == code, output
    assert summary in output, output
    assert " error" not in result.stdout.splitlines()[-1], output
    assert_restored(account, stored)
    assert not leaked(account, output)


# ############################################################################
# The scratch folder
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_scratch_folder_is_removed_with_its_mail(account):
    """Created for the block, holding its mail, gone afterwards.

    A child folder, made and subscribed under it by another client, goes
    with it. Red if the folder is not created, if an appended message does
    not land there, or if the folder, its child, or the child's
    subscription is left behind.
    """
    before, _ = folders(account)

    with claim_scratch_folder(account.mailbox()) as scratch:
        listed, _ = folders(account)

        assert scratch.name in listed - before
        assert scratch.name.startswith(SCRATCH_MARKER)
        assert scratch.append(MESSAGE) is not None

        child = scratch.name + scratch.delimiter + "child"

        with account.imap() as client:
            client.create_folder(child)
            client.subscribe_folder(child)
            client.append(child, MESSAGE)
            client.select_folder(scratch.name, readonly=True)

            assert len(client.search("ALL")) == 1

    after, subscribed = folders(account)

    assert after == before
    assert child not in subscribed


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["INBOX", "inbox"])
def test_inbox_is_refused_and_left_alone(account, name):
    """INBOX, in any case, is refused by name before anything is written.

    Red if the INBOX check goes: the name is then refused only by a later
    check -- that it exists, or lacks the marker -- whose reason differs.
    The later checks are a second line, not the INBOX guard. Red too if
    INBOX loses its mail.
    """
    account.append("INBOX", MESSAGE)

    with (
        pytest.raises(FolderRefused, match="INBOX is never a scratch folder"),
        claim_scratch_folder(account.mailbox(), name=name),
    ):
        pytest.fail("the block ran")

    with account.imap() as client:
        client.select_folder("INBOX", readonly=True)

        assert len(client.search("ALL")) == 1


# ----------------------------------------------------------------------------
def test_an_existing_folder_is_refused_and_kept(account):
    """A folder already there is not the test's, even with the marker.

    Red if the existence check goes: the guard would try to create it, or
    claim it and then delete it and the message in it.
    """
    existing = SCRATCH_MARKER + "preexisting"

    with account.imap() as client:
        client.create_folder(existing)

    account.append(existing, MESSAGE)

    with (
        pytest.raises(FolderRefused, match="already exists"),
        claim_scratch_folder(account.mailbox(), name=existing),
    ):
        pytest.fail("the block ran")

    with account.imap() as client:
        client.select_folder(existing, readonly=True)

        assert len(client.search("ALL")) == 1


# ----------------------------------------------------------------------------
def test_a_name_without_the_marker_is_refused(account):
    """Only a marked name can be claimed, so only one can be deleted.

    Red if the marker check goes: the unmarked folder is then created and
    claimed, and the refusal never comes.
    """
    with (
        pytest.raises(FolderRefused, match="does not start with"),
        claim_scratch_folder(account.mailbox(), name="Archive"),
    ):
        pytest.fail("the block ran")

    listed, _ = folders(account)

    assert "Archive" not in listed
