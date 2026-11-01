"""Four write paths on the real account, each one short, guarded run (#388).

**These write to the real account.** Like ``test_live_write_smoke.py``,
each needs both ``MAILCTL_LIVE=1`` and ``MAILCTL_LIVE_WRITE=1``: the
guards reach the account only through ``write_mailbox``, which skips
without the second opt-in. ``make testlive`` alone skips all four.

Each test covers one command the container tier has already proved on
Dovecot -- ``filter apply``, ``folder create``, ``filter remove``,
``filterset restore`` -- and runs it once on MXroute, behind the guards:

* every folder a test makes is under its own ``scratch_folder``, which
  deletes it and everything under it, so nothing is ever made at the top
  level and INBOX is never read or written;
* every test that changes a filter set asks for ``guarded_scripts``,
  which puts back whatever changed and confirms it.

No test loops, and each writes the least its path needs; the commands
each one sends are listed in its docstring. mailctl runs through
``run_mailctl``, which hands it the env file ``MAILCTL_LIVE_ENV_FILE``
names, so the account it writes to is the one the gate read.

``tests/container/test_live_writes_on_dovecot.py`` runs these same
functions against a throwaway Dovecot first, with the account named by an
env file as an operator's run names it.
"""

import uuid
from typing import cast

import pytest
from live_write import _imap, _read_bytes, _sieve, run_mailctl
from sievelib.managesieve import Client

pytestmark = pytest.mark.live


# ----------------------------------------------------------------------------
def marker() -> str:
    """A subject and filter name no other test, and no real sender, uses."""
    return f"mailctl-live-write-{uuid.uuid4().hex[:12]}"


# ----------------------------------------------------------------------------
def message(subject: str) -> bytes:
    """One small test-owned message with ``subject``."""
    return (
        f"Subject: {subject}\r\n"
        f"From: mailctl test <test@example.invalid>\r\n"
        f"\r\n"
        f"Appended by mailctl's live write tests.\r\n"
    ).encode()


# ----------------------------------------------------------------------------
def subjects(mailbox, folder: str) -> list[str]:
    """Every Subject in ``folder``, read-only, through IMAPClient."""
    with _imap(mailbox) as client:
        client.select_folder(folder, readonly=True)
        uids = client.search("ALL")

        if not uids:
            return []

        fetched = client.fetch(uids, ["BODY.PEEK[HEADER.FIELDS (SUBJECT)]"])

    found = []

    for data in fetched.values():
        # IMAPClient is unannotated; a BODY[...] item is the raw bytes.
        header = cast(bytes, data[b"BODY[HEADER.FIELDS (SUBJECT)]"]).decode()
        found.append(header.split(":", 1)[1].strip())

    return sorted(found)


# ----------------------------------------------------------------------------
def rule_names(mailbox) -> tuple[str | None, list[str]]:
    """The active filter set's name and its filters' names, in order."""
    with _sieve(mailbox, Client) as client:
        active, _others = client.listscripts()

        if not active:
            return None, []

        script = client.getscript(active)

    return active, [
        line.split("[", 1)[1].rsplit("]", 1)[0]
        for line in script.splitlines()
        if line.startswith("# rule:[")
    ]


# ----------------------------------------------------------------------------
def test_apply_moves_existing_mail(scratch_folder, guarded_scripts):
    """``filter apply`` moves the one matching message out of its folder.

    Sends: CREATE and APPEND for the scratch folder; ``filter apply``'s
    CREATE of the target (unsubscribed) and its MOVE (or COPY, STORE and
    EXPUNGE where the server lacks MOVE); the folders' DELETEs. No filter
    is saved, so ``guarded_scripts`` reads and writes nothing back; it is
    there to fail the test if ``apply`` touched a filter set anyway.

    Red if ``apply`` exits non-zero, if the message is still in the
    scratch folder, or if it is not the one message in the target.
    """
    subject = marker()
    scratch_folder.append(message(subject))
    target = f"{scratch_folder.name}{scratch_folder.delimiter}applied"

    code = run_mailctl(
        "filter",
        "apply",
        "--folder",
        scratch_folder.name,
        "--subject",
        subject,
        "--move-to",
        target,
        "--create-folder",
        "--no-subscribe",
        "--max-messages",
        "1",
        "--yes",
    )

    assert code == 0

    assert subjects(scratch_folder.mailbox, scratch_folder.name) == []
    assert subjects(scratch_folder.mailbox, target) == [subject]


# ----------------------------------------------------------------------------
def test_folder_create_makes_and_subscribes_it(scratch_folder):
    """``folder create`` makes a folder under the scratch folder.

    Sends: CREATE for the scratch folder; ``folder create``'s CREATE and
    SUBSCRIBE; the guard's UNSUBSCRIBE and the folders' DELETEs. No filter
    set is read or written.

    Red if ``create`` exits non-zero, or if the folder is not listed and
    subscribed afterwards, read by IMAPClient rather than by mailctl.
    """
    name = f"{scratch_folder.name}{scratch_folder.delimiter}created"

    code = run_mailctl("folder", "create", name, "--yes")

    assert code == 0

    with _imap(scratch_folder.mailbox) as client:
        listed = {entry[2] for entry in client.list_folders(name)}
        subscribed = {entry[2] for entry in client.list_sub_folders(name)}

    assert name in listed
    assert name in subscribed


# ----------------------------------------------------------------------------
def test_filter_remove_takes_out_only_its_filter(guarded_scripts, tmp_path):
    """``filter remove`` takes out the filter ``add`` put in, and no other.

    Sends: ``filter add``'s PUTSCRIPT (and a SETACTIVE on an account with
    no active filter set), ``filter remove``'s PUTSCRIPT, and the guard's
    one restoring command per filter set that is not byte for byte as it
    was. The filter is a ``keep`` on a subject nobody sends, so for the
    moment it exists it changes nothing. Backups go to the test's own
    directory.

    Red if either command exits non-zero, if ``add`` does not store the
    filter, or if after ``remove`` the active filter set does not hold
    exactly the filters it held before, in the same order.
    """
    mailbox = guarded_scripts.mailbox
    name = marker()
    backups = str(tmp_path / "mailctl-backups")
    _active, before = rule_names(mailbox)

    added = run_mailctl(
        "filter",
        "add",
        "--subject",
        name,
        "--name",
        name,
        "--keep",
        "--backup-dir",
        backups,
    )

    assert added == 0
    assert name in rule_names(mailbox)[1]

    removed = run_mailctl(
        "filter", "remove", name, "--yes", "--backup-dir", backups
    )

    assert removed == 0
    assert rule_names(mailbox)[1] == before


# ----------------------------------------------------------------------------
def test_filterset_restore_puts_back_the_backup(guarded_scripts, tmp_path):
    """``filterset restore`` puts back exactly what ``backup`` saved.

    Sends: ``filter add``'s PUTSCRIPT, then ``filterset restore``'s
    CHECKSCRIPT and PUTSCRIPT; ``filterset backup`` only reads. The guard
    then finds the filter set as it captured it and sends nothing more.
    Skipped on an account with no active filter set, since there is then
    nothing to back up.

    Red if any command exits non-zero, if the backup is not the server's
    bytes, if ``add`` leaves those bytes unchanged (so the restore would
    prove nothing), or if after the restore the server's bytes, read by
    the guard's byte-exact reader, are not the backup's.
    """
    mailbox = guarded_scripts.mailbox
    active = guarded_scripts.active

    if active is None:
        pytest.skip("no active filter set on this account to back up")

    name = marker()
    backups = str(tmp_path / "mailctl-backups")
    saved = tmp_path / "before.sieve"

    backed_up = run_mailctl(
        "filterset", "backup", "--output", str(saved), "--backup-dir", backups
    )

    assert backed_up == 0
    assert saved.read_bytes() == guarded_scripts.scripts[active]

    added = run_mailctl(
        "filter",
        "add",
        "--subject",
        name,
        "--name",
        name,
        "--keep",
        "--backup-dir",
        backups,
    )

    assert added == 0
    assert _read_bytes(mailbox)[1][active] != saved.read_bytes()

    restored = run_mailctl(
        "filterset", "restore", str(saved), "--yes", "--backup-dir", backups
    )

    assert restored == 0

    now_active, scripts = _read_bytes(mailbox)

    assert now_active == active
    assert scripts[active] == saved.read_bytes()
