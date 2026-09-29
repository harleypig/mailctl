"""The one live test that writes -- behind a second opt-in, and the guards.

**This writes to the real account.** It runs only with both
``MAILCTL_LIVE=1`` and ``MAILCTL_LIVE_WRITE=1`` (``write_mailbox`` in
``conftest.py``); ``make testlive`` on its own skips it. It exists so the
first live write is one short, known sequence rather than whatever test
happens to be added next, and it proves on MXroute what the container tier
proved on Dovecot: that the guards put the account back.

What it sends, once each, with no loop: a CREATE and an APPEND for the
scratch folder; ``mailctl add``'s PUTSCRIPT (and a SETACTIVE, on an
account with no active script); the guard's one restoring command per
changed script; and the folder's DELETE.
"""

import uuid

import pytest
from live_write import _imap, _sieve
from sievelib.managesieve import Client

from mailctl import cli

pytestmark = pytest.mark.live


# ----------------------------------------------------------------------------
def test_a_guarded_write_leaves_the_account_as_it_was(
    scratch_folder, guarded_scripts, tmp_path
):
    """``mailctl add`` writes a rule; the guard takes it out again.

    ``scratch_folder`` is requested first so it is torn down last: the
    script is restored before the folder goes. The rule is a ``keep`` on a
    subject nobody sends, so for the moment it exists it changes nothing.
    Red if the message does not land in the scratch folder, if ``add``
    does not store the rule, or -- in teardown, as an error -- if the
    guard cannot confirm the scripts or the folder are back as they were.
    """
    marker = f"mailctl-live-write-{uuid.uuid4().hex[:12]}"

    uid = scratch_folder.append(
        f"Subject: {marker}\r\nFrom: mailctl test <test@example.invalid>\r\n"
        f"\r\nAppended by mailctl's live write smoke test.\r\n".encode()
    )

    with _imap(scratch_folder.mailbox) as client:
        client.select_folder(scratch_folder.name, readonly=True)
        found = client.search("ALL")

    assert len(found) == 1
    assert uid is None or found == [uid]

    code = cli.main(
        [
            "add",
            "--subject",
            marker,
            "--name",
            marker,
            "--keep",
            "--backup-dir",
            str(tmp_path / "mailctl-backups"),
        ]
    )

    assert code == 0

    with _sieve(guarded_scripts.mailbox, Client) as client:
        active, _others = client.listscripts()

        assert active, "add left no active script"
        assert f"# rule:[{marker}]" in client.getscript(active)
