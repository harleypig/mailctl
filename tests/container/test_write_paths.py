"""mailctl's write paths, run against a real Dovecot + Pigeonhole.

Each test drives ``mailctl`` through ``cli.main`` against a fresh mailbox
(``conftest.py``) and then reads the server back with the libraries' own
clients, never with mailctl. The offline tier already pins what mailctl
*sends*; what only a server can say is whether that is what a server
*keeps* -- that the merged script is accepted and stored whole, that a
backup is the stored bytes, that a moved message lands and is flagged,
and that the rule uploaded really files new mail the way the retroactive
pass filed the old.

Every test names, in its docstring, the break that turns it red.
"""

import datetime
import re
import uuid

import pytest

pytestmark = pytest.mark.container

# A script in the shape Roundcube's managesieve plugin writes, under the
# name Roundcube gives it on MXroute. It is the thing a merge must not
# destroy (ADR 0002), so it is seeded as another client would have left
# it, before mailctl first runs.
ROUNDCUBE = """require ["fileinto","imap4flags"];
# rule:[keep-boss]
if header :contains "from" "boss@example.com"
{
\tfileinto "INBOX.Boss";
\tsetflag "\\\\Flagged";
\tstop;
}
"""

ROUNDCUBE_NAME = "managesieve"

# Three independent rules, so a move changes order and nothing else.
THREE_RULES = """require ["fileinto"];
# rule:[alpha]
if header :contains "subject" "alpha"
{
\tfileinto "INBOX";
\tstop;
}
# rule:[beta]
if header :contains "subject" "beta"
{
\tfileinto "INBOX";
\tstop;
}
# rule:[gamma]
if header :contains "subject" "gamma"
{
\tfileinto "INBOX";
\tstop;
}
"""

GITHUB = "noreply@github.com"


# ----------------------------------------------------------------------------
def message(sender: str, subject: str) -> bytes:
    """A minimal RFC 5322 message."""
    return (
        f"From: Someone <{sender}>\r\n"
        f"To: user@example.test\r\n"
        f"Subject: {subject}\r\n"
        f"Date: Tue, 3 Feb 2026 04:05:06 +0000\r\n"
        f"Message-ID: <{uuid.uuid4().hex}@example.test>\r\n"
        f"\r\n"
        f"Body of {subject}.\r\n"
    ).encode()


# ----------------------------------------------------------------------------
def squeeze(text: str) -> str:
    """``text`` with every run of whitespace made one space."""
    return " ".join(text.split())


# ----------------------------------------------------------------------------
def rule_names(text: str) -> list[str]:
    """The ``# rule:[NAME]`` markers in ``text``, in order."""
    return [
        line.removeprefix("# rule:[").removesuffix("]")
        for line in text.splitlines()
        if line.startswith("# rule:[")
    ]


# ----------------------------------------------------------------------------
def folder_state(account) -> tuple[set[str], set[str]]:
    """Every folder, and every subscribed one, as the server lists them."""
    with account.imap() as client:
        listed = {name for _, _, name in client.list_folders()}
        subscribed = {name for _, _, name in client.list_sub_folders()}

    return listed, subscribed


# ----------------------------------------------------------------------------
def mail_in(account, folder: str) -> dict[int, tuple[str, set[bytes]]]:
    """UID -> (subject, flags) for every message in ``folder``.

    Selected read-only and fetched with BODY.PEEK, so looking does not set
    the \\Seen flag a test may be asserting about.
    """
    with account.imap() as client:
        client.select_folder(folder, readonly=True)
        uids = client.search("ALL")

        if not uids:
            return {}

        fetched = client.fetch(
            uids, ["FLAGS", "BODY.PEEK[HEADER.FIELDS (SUBJECT)]"]
        )

    result = {}

    for uid, data in fetched.items():
        header = data[b"BODY[HEADER.FIELDS (SUBJECT)]"].decode()
        subject = header.split(":", 1)[1].strip()
        result[uid] = (subject, set(data[b"FLAGS"]))

    return result


# ############################################################################
# Rules: add, remove, move
# ############################################################################


# ----------------------------------------------------------------------------
def test_add_merges_into_a_roundcube_script_and_keeps_its_rule(account):
    """The ADR 0002 guarantee, on a real server.

    Red if the upload overwrites rather than merges (the keep-boss rule
    goes missing, or any of its tests or actions changes), if the new rule
    is not stored, if mailctl writes a second script instead of the active
    one, or if no backup of the script it replaced is written first.
    """
    account.seed_script(ROUNDCUBE_NAME, ROUNDCUBE)

    result = account.run(
        "add",
        "--from",
        GITHUB,
        "--name",
        "github",
        "--fileinto",
        "Lists/GitHub",
        "--create-folder",
    )

    assert result.code == 0, result.err

    stored = account.script(ROUNDCUBE_NAME)

    assert account.active_script() == ROUNDCUBE_NAME
    assert rule_names(stored) == ["keep-boss", "github"]

    # The hand-made rule's body survives, not merely its name. ADR 0002
    # promises the tests and actions unchanged and says whitespace is
    # normalized, so the comparison ignores layout and nothing else.
    boss_body = ROUNDCUBE.split("# rule:[keep-boss]\n", 1)[1]

    assert squeeze(boss_body) in squeeze(stored)

    backups = list(account.backup_dir.iterdir())

    assert len(backups) == 1
    assert backups[0].read_text() == ROUNDCUBE


# ----------------------------------------------------------------------------
def test_listing_commands_show_the_added_rule(account):
    """What ``add`` stored is what ``list``, ``rules`` and ``show`` report.

    Red if any of the three read paths disagrees with the write path --
    a script listed under the wrong name, a rule the reader cannot parse
    back out, or ``show`` printing something other than the stored text.
    """
    account.seed_script(ROUNDCUBE_NAME, ROUNDCUBE)

    added = account.run(
        "add",
        "--subject",
        "invoice",
        "--name",
        "invoices",
        "--keep",
    )

    assert added.code == 0, added.err

    listed = account.run("list")
    rules = account.run("rules")
    shown = account.run("show")

    assert listed.code == rules.code == shown.code == 0
    assert ROUNDCUBE_NAME in listed.out
    assert "keep-boss" in rules.out
    assert "invoices" in rules.out
    assert "# rule:[invoices]" in shown.out
    assert "# rule:[keep-boss]" in shown.out


# ----------------------------------------------------------------------------
def test_the_uploaded_rule_files_new_mail(account):
    """The going-forward half: Sieve itself runs what ``add`` uploaded.

    Delivered through dovecot-lda, so Pigeonhole executes the stored
    script. Red if the script is stored but does not do what mailctl said
    it would -- a wrong folder name (the delimiter guessed rather than
    discovered), a flag action the server ignores, or a rule the server
    accepts at PUTSCRIPT and then fails to run.
    """
    account.seed_script(ROUNDCUBE_NAME, ROUNDCUBE)

    added = account.run(
        "add",
        "--from",
        GITHUB,
        "--name",
        "github",
        "--fileinto",
        "Lists/GitHub",
        "--flag",
        "\\Flagged",
        "--create-folder",
    )

    assert added.code == 0, added.err

    account.deliver(message(GITHUB, "new pull request"), GITHUB)
    account.deliver(message("friend@example.org", "lunch?"), "friend@x.org")

    filed = mail_in(account, "Lists.GitHub")
    inbox = mail_in(account, "INBOX")

    assert [subject for subject, _ in filed.values()] == ["new pull request"]
    assert all(b"\\Flagged" in flags for _, flags in filed.values())
    assert [subject for subject, _ in inbox.values()] == ["lunch?"]


# ----------------------------------------------------------------------------
def test_remove_rule_takes_out_one_rule_and_leaves_the_rest(account):
    """Red if the wrong rule goes, if the other rule is rewritten on the
    way through, or if the server keeps the old script."""
    account.seed_script(ROUNDCUBE_NAME, THREE_RULES)

    result = account.run("remove-rule", "beta", "--yes")

    assert result.code == 0, result.err

    stored = account.script(ROUNDCUBE_NAME)

    assert rule_names(stored) == ["alpha", "gamma"]
    assert "beta" not in stored


# ----------------------------------------------------------------------------
def test_move_rule_reorders_without_changing_the_rule(account):
    """Red if the rule does not land first, if its body changes on the
    move, or if another rule is dropped or duplicated."""
    account.seed_script(ROUNDCUBE_NAME, THREE_RULES)

    result = account.run("move-rule", "gamma", "--first", "--yes")

    assert result.code == 0, result.err

    stored = account.script(ROUNDCUBE_NAME)

    assert rule_names(stored) == ["gamma", "alpha", "beta"]
    assert 'if header :contains "subject" "gamma"' in stored


# A rule that files invoices, for switching off and on (#158).
INVOICES = """require ["fileinto"];
# rule:[invoices]
if header :contains "subject" "invoice"
{
\tfileinto "INBOX.Bills";
\tstop;
}
"""


# ----------------------------------------------------------------------------
def test_disable_then_enable_switches_the_rule_off_and_on(account):
    """A disabled rule is stored in Roundcube's form and files nothing;
    enabled again, it files mail as before.

    Red if the stored script is not ``if false # <its test>`` on one line
    (Roundcube would no longer show the rule as disabled), if the server
    still runs a rule mailctl says is off, or if enabling does not restore
    a test that files the mail again.
    """
    account.seed_script(ROUNDCUBE_NAME, INVOICES)

    with account.imap() as client:
        client.create_folder("INBOX.Bills")

    disabled = account.run("disable-rule", "invoices", "--yes")

    assert disabled.code == 0, disabled.err

    stored = account.script_bytes(ROUNDCUBE_NAME).decode()

    assert (
        "# rule:[invoices]\n"
        'if false # header :contains "subject" "invoice"\n'
        "{\n"
    ) in stored.replace("\r\n", "\n")

    account.deliver(message("billing@example.org", "invoice 1"), "b@x.org")

    assert mail_in(account, "INBOX.Bills") == {}
    assert [s for s, _ in mail_in(account, "INBOX").values()] == ["invoice 1"]

    enabled = account.run("enable-rule", "invoices", "--yes")

    assert enabled.code == 0, enabled.err
    assert "if false" not in account.script(ROUNDCUBE_NAME)

    account.deliver(message("billing@example.org", "invoice 2"), "b@x.org")

    filed = mail_in(account, "INBOX.Bills")

    assert [subject for subject, _ in filed.values()] == ["invoice 2"]


# ----------------------------------------------------------------------------
def test_replacing_a_disabled_rule_keeps_it_disabled(account):
    """``add --replace`` on a rule switched off in Roundcube stores the new
    rule still switched off, in Roundcube's form (#168).

    Red if the stored rule is sievelib's ``if false { if <test> ... }``
    (Roundcube would show it as enabled), if the old test survives, if
    ``rules`` stops listing it as disabled or lists the old test, if the
    server runs the replacement, or if enabling does not restore the new
    test.
    """
    account.seed_script(
        ROUNDCUBE_NAME,
        INVOICES.replace(
            'if header :contains "subject" "invoice"',
            'if false # header :contains "subject" "invoice"',
        ),
    )

    with account.imap() as client:
        client.create_folder("INBOX.Bills")

    replaced = account.run(
        "add",
        "--subject",
        "receipt",
        "--name",
        "invoices",
        "--fileinto",
        "Bills",
        "--replace",
    )

    assert replaced.code == 0, replaced.err

    stored = account.script_bytes(ROUNDCUBE_NAME).decode()
    rule = stored.replace("\r\n", "\n").split("# rule:[invoices]\nif", 1)[1]
    false_line = rule.split("\n", 1)[0]

    # Roundcube's own test for a disabled rule, right after `if`
    # (rcube_sieve_script.php).
    assert re.match(r"^\s*false\s+#\s*", rule, re.IGNORECASE)
    assert "receipt" in false_line
    assert "invoice" not in rule
    assert "if " not in rule

    rules = account.run("rules")

    assert rules.code == 0, rules.err
    assert "invoices  [disabled]" in rules.out
    assert "receipt" in rules.out

    account.deliver(message("shop@example.org", "receipt 1"), "s@x.org")

    assert mail_in(account, "INBOX.Bills") == {}
    assert [s for s, _ in mail_in(account, "INBOX").values()] == ["receipt 1"]

    enabled = account.run("enable-rule", "invoices", "--yes")

    assert enabled.code == 0, enabled.err
    assert "if false" not in account.script(ROUNDCUBE_NAME)

    account.deliver(message("shop@example.org", "receipt 2"), "s@x.org")

    filed = mail_in(account, "INBOX.Bills")

    assert [subject for subject, _ in filed.values()] == ["receipt 2"]


# ----------------------------------------------------------------------------
def test_a_fresh_account_gets_a_new_active_script(account):
    """With nothing stored, ``add`` creates and activates ``mailctl``.

    Red if SETACTIVE is not sent (the script is stored and never runs), or
    if the new script is not named ``mailctl``.
    """
    assert account.active_script() is None

    result = account.run(
        "add",
        "--subject",
        "hello",
        "--keep",
    )

    assert result.code == 0, result.err
    assert account.active_script() == "mailctl"


# ############################################################################
# Backup and restore
# ############################################################################


# ----------------------------------------------------------------------------
def test_backup_then_restore_is_byte_exact(account, tmp_path):
    """The round trip #9's live fixture will depend on, proved here first.

    The seeded script ends without a newline and carries a CR, the two
    shapes GETSCRIPT truncation and newline translation lose (#90). Red if
    ``backup`` writes anything but the stored bytes, or if ``restore``
    uploads anything but the file's bytes.
    """
    original = THREE_RULES.replace("\n", "\r\n", 1).rstrip("\n")

    account.seed_script(ROUNDCUBE_NAME, original)

    stored = account.script_bytes(ROUNDCUBE_NAME)
    target = tmp_path / "saved.sieve"

    backed_up = account.run("backup", "--output", str(target))

    assert backed_up.code == 0, backed_up.err
    assert target.read_bytes() == stored

    changed = account.run("remove-rule", "alpha", "--yes")

    assert changed.code == 0, changed.err
    assert account.script_bytes(ROUNDCUBE_NAME) != stored

    restored = account.run("restore", str(target), "--yes")

    assert restored.code == 0, restored.err
    assert account.script_bytes(ROUNDCUBE_NAME) == stored


# ############################################################################
# Folders
# ############################################################################


# ----------------------------------------------------------------------------
def test_create_folder_subscribes_unless_told_not_to(account):
    """``add --create-folder`` makes the folder and subscribes it; with
    ``--no-subscribe`` it makes it hidden; ``subscribe`` and
    ``unsubscribe`` then toggle it.

    ``add`` rather than ``apply``: the existing-mail pass creates the
    folder only when there is mail to move into it, and ``add`` creates it
    before the upload whatever the mailbox holds. Red if CREATE is not
    sent, if SUBSCRIBE is sent when it should not be (or not when it
    should), or if a toggle does not reach the server's LSUB -- the list
    webmail draws its folder tree from (#38).
    """
    shown = account.run(
        "add",
        "--subject",
        "shown",
        "--fileinto",
        "Lists/Shown",
        "--create-folder",
    )
    hidden = account.run(
        "add",
        "--subject",
        "hidden",
        "--fileinto",
        "Lists/Hidden",
        "--create-folder",
        "--no-subscribe",
    )

    assert shown.code == 0, shown.err
    assert hidden.code == 0, hidden.err

    listed, subscribed = folder_state(account)

    assert {"Lists.Shown", "Lists.Hidden"} <= listed
    assert "Lists.Shown" in subscribed
    assert "Lists.Hidden" not in subscribed

    assert account.run("subscribe", "Lists/Hidden").code == 0
    assert "Lists.Hidden" in folder_state(account)[1]

    assert account.run("unsubscribe", "Lists/Shown").code == 0
    assert "Lists.Shown" not in folder_state(account)[1]


# ############################################################################
# The retroactive pass
# ############################################################################


# ----------------------------------------------------------------------------
def test_apply_moves_and_marks_existing_mail(account):
    """Red if a matching message is left behind, a non-matching one is
    moved, or the moved copies arrive without \\Seen."""
    for subject in ("pr 1", "pr 2", "pr 3"):
        account.append("INBOX", message(GITHUB, subject))

    account.append("INBOX", message("friend@example.org", "lunch?"))

    result = account.run(
        "apply",
        "--from",
        GITHUB,
        "--fileinto",
        "Lists/GitHub",
        "--create-folder",
        "--mark-read",
        "--yes",
    )

    assert result.code == 0, result.err

    moved = mail_in(account, "Lists.GitHub")
    left = mail_in(account, "INBOX")

    assert sorted(subject for subject, _ in moved.values()) == [
        "pr 1",
        "pr 2",
        "pr 3",
    ]
    assert all(b"\\Seen" in flags for _, flags in moved.values())
    assert [subject for subject, _ in left.values()] == ["lunch?"]


# ----------------------------------------------------------------------------
def test_apply_keep_copies_as_the_saved_rule_does(account):
    """#188: ``fileinto`` with ``keep`` leaves the original and files a
    copy, both flagged -- in the rule Sieve runs and in the pass alike.

    ``apply`` runs first, so the message delivered afterwards is not in
    its search. Red if the pass moves (the old message leaves INBOX),
    expunges after copying, drops the flag from either copy, or if
    Pigeonhole does something else with ``addflag; fileinto; keep``.
    """
    actions = (
        "--from",
        GITHUB,
        "--fileinto",
        "Lists/GitHub",
        "--keep",
        "--flag",
        "\\Flagged",
        "--create-folder",
    )

    account.append("INBOX", message(GITHUB, "old pr"))
    account.append("INBOX", message("friend@example.org", "lunch?"))

    applied = account.run("apply", *actions, "--yes")

    assert applied.code == 0, applied.err

    added = account.run("add", "--name", "github", *actions)

    assert added.code == 0, added.err

    account.deliver(message(GITHUB, "new pr"), GITHUB)

    inbox = sorted(mail_in(account, "INBOX").values())
    filed = sorted(mail_in(account, "Lists.GitHub").values())

    assert [subject for subject, _ in inbox] == ["lunch?", "new pr", "old pr"]
    assert [subject for subject, _ in filed] == ["new pr", "old pr"]

    for subject, flags in [*inbox, *filed]:
        assert (b"\\Flagged" in flags) is (subject != "lunch?"), subject


# ----------------------------------------------------------------------------
def test_apply_keep_run_again_copies_nothing_twice(account):
    """#192: running the same ``apply --keep`` again leaves one copy of
    each message in the folder, and so does running it over mail the
    saved rule already filed a copy of.

    Red if the second run copies what the folder holds -- the old
    messages then appear twice -- or if the plan's check misses a copy
    Pigeonhole filed, so the delivered message appears twice.
    """
    actions = (
        "--from",
        GITHUB,
        "--fileinto",
        "Lists/GitHub",
        "--keep",
        "--create-folder",
    )

    account.append("INBOX", message(GITHUB, "old pr"))
    account.append("INBOX", message(GITHUB, "old issue"))
    account.append("INBOX", message("friend@example.org", "lunch?"))

    first = account.run("apply", *actions, "--yes")

    assert first.code == 0, first.err

    added = account.run("add", "--name", "github", *actions)

    assert added.code == 0, added.err

    account.deliver(message(GITHUB, "new pr"), GITHUB)

    planned = account.run("apply", *actions, "--dry-run")

    assert planned.code == 0, planned.err
    assert "3 message(s) already in 'Lists.GitHub'" in planned.out

    again = account.run("apply", *actions, "--yes")

    assert again.code == 0, again.err
    assert "Nothing to do" in again.out

    inbox = sorted(
        subject for subject, _ in mail_in(account, "INBOX").values()
    )
    filed = sorted(
        subject for subject, _ in mail_in(account, "Lists.GitHub").values()
    )

    assert inbox == ["lunch?", "new pr", "old issue", "old pr"]
    assert filed == ["new pr", "old issue", "old pr"]


# ----------------------------------------------------------------------------
def test_apply_flags_in_place(account):
    """A flag-only action leaves the message where it is.

    Red if the flag is not set, if it lands on a message that did not
    match, or if flagging moves anything.
    """
    account.append("INBOX", message(GITHUB, "urgent"))
    account.append("INBOX", message("friend@example.org", "lunch?"))

    result = account.run(
        "apply",
        "--from",
        GITHUB,
        "--flag",
        "\\Flagged",
        "--yes",
    )

    assert result.code == 0, result.err

    inbox = dict(mail_in(account, "INBOX").values())

    assert set(inbox) == {"urgent", "lunch?"}
    assert b"\\Flagged" in inbox["urgent"]
    assert b"\\Flagged" not in inbox["lunch?"]


# ----------------------------------------------------------------------------
def test_apply_discard_removes_only_the_matches(account):
    """Red if a matching message survives, or anything else goes."""
    account.append("INBOX", message("spam@example.net", "win a prize"))
    account.append("INBOX", message("friend@example.org", "lunch?"))

    result = account.run(
        "apply",
        "--from",
        "spam@example.net",
        "--discard",
        "--yes",
    )

    assert result.code == 0, result.err

    subjects = [subject for subject, _ in mail_in(account, "INBOX").values()]

    assert subjects == ["lunch?"]


# ----------------------------------------------------------------------------
def test_max_messages_refuses_the_whole_pass(account):
    """Over the ceiling, nothing moves -- not a partial batch.

    Red if the ceiling is not enforced (all three move), or enforced by
    stopping part-way (some move), or if the refusal exits 0.
    """
    for subject in ("pr 1", "pr 2", "pr 3"):
        account.append("INBOX", message(GITHUB, subject))

    result = account.run(
        "apply",
        "--from",
        GITHUB,
        "--fileinto",
        "Lists/GitHub",
        "--create-folder",
        "--max-messages",
        "2",
        "--yes",
    )

    assert result.code != 0
    assert "max-messages" in result.err or "max-messages" in result.out
    assert len(mail_in(account, "INBOX")) == 3


# ----------------------------------------------------------------------------
def test_one_filter_document_drives_both_halves(account, tmp_path):
    """#149: ``search --like --build-filter --json`` writes the filter,
    ``add --filter`` saves it as the rule, and ``apply --filter`` acts on
    the mail already there -- the same criteria, from one file, for both.

    Red if the document does not read back as the criteria it was written
    from (the rule or the pass matches something else), if ``add`` touches
    delivered mail, if ``apply`` leaves a match behind or moves a
    non-match, or if the uploaded rule does not file new mail the way the
    pass filed the old.
    """
    account.seed_script(ROUNDCUBE_NAME, ROUNDCUBE)

    like = account.append("INBOX", message(GITHUB, "pr 1"))
    account.append("INBOX", message(GITHUB, "pr 2"))
    account.append("INBOX", message("friend@example.org", "lunch?"))

    built = account.run(
        "search", "--like", str(like), "--build-filter", "--json"
    )

    assert built.code == 0, built.err

    document = tmp_path / "github.json"
    document.write_text(built.out, encoding="utf-8")

    added = account.run(
        "add",
        "--filter",
        str(document),
        "--name",
        "github",
        "--fileinto",
        "Lists/GitHub",
        "--create-folder",
    )

    assert added.code == 0, added.err
    assert rule_names(account.script(ROUNDCUBE_NAME)) == [
        "keep-boss",
        "github",
    ]
    assert len(mail_in(account, "INBOX")) == 3

    applied = account.run(
        "apply",
        "--filter",
        str(document),
        "--fileinto",
        "Lists/GitHub",
        "--yes",
    )

    assert applied.code == 0, applied.err
    assert [subject for subject, _ in mail_in(account, "INBOX").values()] == [
        "lunch?"
    ]

    account.deliver(message(GITHUB, "pr 3"), GITHUB)
    account.deliver(message("friend@example.org", "dinner?"), "friend@x.org")

    filed = sorted(s for s, _ in mail_in(account, "Lists.GitHub").values())
    inbox = sorted(s for s, _ in mail_in(account, "INBOX").values())

    assert filed == ["pr 1", "pr 2", "pr 3"]
    assert inbox == ["dinner?", "lunch?"]


# ############################################################################
# Reading mail changes nothing
# ############################################################################


# ----------------------------------------------------------------------------
def test_view_does_not_mark_a_message_read(account):
    """``view`` promises not to set \\Seen.

    mailctl guards this twice: it fetches with BODY.PEEK, and it selects
    the folder read-only (EXAMINE), under which Dovecot sets no flag at
    all. Red when both give way -- a plain BODY[] fetch on a folder
    selected read-write -- which is when a real server marks the message
    read and a double, recording only what was asked, would not. Losing
    either guard alone stays green here; the offline tier pins each one.
    """
    uid = account.append("INBOX", message(GITHUB, "unread still"))

    result = account.run("view", str(uid))

    assert result.code == 0, result.err
    assert "unread still" in result.out

    (flags,) = [flags for _, flags in mail_in(account, "INBOX").values()]

    assert b"\\Seen" not in flags


# ----------------------------------------------------------------------------
def test_listing_messages_does_not_mark_them_read(account):
    """The listing utility is read-only too.

    Called as the utility rather than a CLI command, because the command's
    name is changing (``messages`` to ``search``) and this is about the
    read, not the name. Red under the same double break as the ``view``
    test above.
    """
    import argparse

    from mailctl import engine, utilities
    from mailctl.config import load_config

    account.append("INBOX", message(GITHUB, "one"))
    account.append("INBOX", message(GITHUB, "two"))

    config = load_config(argparse.Namespace())

    with engine.connect(config, rules=False, mail=True) as session:
        listing = utilities.messages.list_messages(session, "INBOX", limit=10)

    assert sorted(item.subject for item in listing.messages) == ["one", "two"]
    assert all(
        b"\\Seen" not in flags
        for _, flags in mail_in(account, "INBOX").values()
    )


# ############################################################################
# Marking messages
# ############################################################################


# ----------------------------------------------------------------------------
def flags_of(account, uid: int) -> set[bytes]:
    """A message's stored flags in INBOX, without the session-only
    \\Recent, which the server sets on its own."""
    return mail_in(account, "INBOX")[uid][1] - {b"\\Recent"}


# ----------------------------------------------------------------------------
def test_mark_sets_then_clears_read_flagged_and_a_keyword(account):
    """STORE +FLAGS and -FLAGS, as the server keeps them (#150).

    Red if ``--dry-run`` stores anything, if a flag is not set or not
    cleared (the folder examined rather than selected, or one sign sent
    for the other), if a flag lands on the message that was not named, or
    if the server refuses the keyword mailctl accepted.
    """
    uid = account.append("INBOX", message(GITHUB, "to mark"))
    other = account.append("INBOX", message("friend@example.org", "lunch?"))

    wanted = ["--read", "--flag", "--keyword", "$Todo"]

    dry = account.run("mark", str(uid), *wanted, "--dry-run")

    assert dry.code == 0, dry.err
    assert flags_of(account, uid) == set()

    marked = account.run("mark", str(uid), *wanted, "--yes")

    assert marked.code == 0, marked.err

    assert flags_of(account, uid) == {b"\\Seen", b"\\Flagged", b"$Todo"}
    assert flags_of(account, other) == set()

    cleared = account.run(
        "mark",
        str(uid),
        "--unread",
        "--unflag",
        "--no-keyword",
        "$Todo",
        "--yes",
    )

    assert cleared.code == 0, cleared.err
    assert flags_of(account, uid) == set()


# ----------------------------------------------------------------------------
def test_mark_refuses_a_uid_the_folder_does_not_hold(account):
    """Red if the UIDs that exist are marked while the missing one is
    reported, which would be a partial change reported as a refusal."""
    uid = account.append("INBOX", message(GITHUB, "to mark"))

    result = account.run("mark", str(uid), str(uid + 100), "--flag", "--yes")

    assert result.code == 1
    assert f"no message with uid {uid + 100}" in result.err
    assert flags_of(account, uid) == set()


# ############################################################################
# Body, dates, and state (#152)
# ############################################################################


# ----------------------------------------------------------------------------
def message_with(subject: str, body: str) -> bytes:
    """A minimal message whose body is ``body``, as UTF-8 text."""
    return (
        f"From: Build Bot <ci@example.org>\r\n"
        f"To: user@example.test\r\n"
        f"Subject: {subject}\r\n"
        f"Date: Tue, 3 Feb 2026 04:05:06 +0000\r\n"
        f"Message-ID: <{uuid.uuid4().hex}@example.test>\r\n"
        f"MIME-Version: 1.0\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Transfer-Encoding: 8bit\r\n"
        f"\r\n"
        f"{body}\r\n"
    ).encode()


# ----------------------------------------------------------------------------
def listed(result) -> set[str]:
    """The subjects a ``search`` listing shows, from its rows."""
    return {
        line.rsplit("  ", 1)[-1].strip()
        for line in result.out.splitlines()
        if re.match(r"^ +\d+  \d{4}-", line)
    }


# ----------------------------------------------------------------------------
def test_search_and_apply_select_by_body_date_and_state(account):
    """#152 against a real IMAP server: every new criterion narrows the
    search as it says, alone and together, and ``apply --dry-run`` plans
    exactly what ``search`` lists.

    Dates are relative to today, so the test means the same whenever it
    runs. Red if a key reaches Dovecot in a form it reads differently
    (a locale month name, a quoted date, a state key inside the OR
    group), if a filter is dropped or ORed rather than ANDed, if the
    re-check throws away what the server matched on the body, or if a
    non-ASCII body value is mangled on the way (#89's path).
    """
    now = datetime.datetime.now().replace(microsecond=0)
    old = now - datetime.timedelta(days=60)
    recent = now - datetime.timedelta(days=2)

    account.append("INBOX", message_with("old-merged", "PR merged"), (), old)
    account.append(
        "INBOX",
        message_with("read-merged", "PR merged"),
        (b"\\Seen",),
        recent,
    )
    account.append(
        "INBOX",
        message_with("flagged-other", "nothing here"),
        (b"\\Flagged",),
        recent,
    )
    account.append(
        "INBOX",
        message_with("flagged-merged", "PR MERGED today"),
        (b"\\Flagged",),
        recent,
    )
    account.append(
        "INBOX", message_with("accented", "un café crème"), (), recent
    )

    since = (now - datetime.timedelta(days=10)).date().isoformat()

    cases = {
        ("--body", "merged"): {"old-merged", "read-merged", "flagged-merged"},
        ("--body", "merged", "--since", since): {
            "read-merged",
            "flagged-merged",
        },
        ("--before", since): {"old-merged"},
        ("--older-than", "30d"): {"old-merged"},
        ("--unread",): {
            "old-merged",
            "flagged-other",
            "flagged-merged",
            "accented",
        },
        ("--flagged",): {"flagged-other", "flagged-merged"},
        ("--body", "merged", "--unread", "--flagged"): {"flagged-merged"},
        ("--body", "crème"): {"accented"},
        ("--subject", "accented", "--body", "merged", "--flagged"): {
            "flagged-merged"
        },
    }

    for flags, expected in cases.items():
        result = account.run("search", *flags)

        assert result.code == 0, (flags, result.err)
        assert listed(result) == expected, (flags, result.out)

    planned = account.run(
        "apply",
        "--body",
        "merged",
        "--since",
        since,
        "--flagged",
        "--fileinto",
        "Lists/CI",
        "--create-folder",
        "--dry-run",
    )

    assert planned.code == 0, planned.err
    assert "1 message(s) match" in planned.out
    assert "flagged-merged" in planned.out
    assert len(mail_in(account, "INBOX")) == 5


# ----------------------------------------------------------------------------
def test_a_body_rule_files_a_delivered_message_by_its_body(account):
    """``add --body`` writes the ``body`` extension's test, Pigeonhole
    accepts it, and on delivery it files a message by what its text says.

    Red if the rule is written without ``require "body"`` (PUTSCRIPT
    refuses it), if the test is shaped so Pigeonhole reads it differently
    (a transform or match type in the wrong place), or if it matches on
    anything but the body -- the two messages differ only there and in a
    subject the rule never reads.
    """
    account.seed_script(ROUNDCUBE_NAME, ROUNDCUBE)

    added = account.run(
        "add",
        "--body",
        "build failed",
        "--name",
        "ci-failures",
        "--fileinto",
        "Lists/CI",
        "--create-folder",
    )

    assert added.code == 0, added.err

    stored = account.script(ROUNDCUBE_NAME)

    assert rule_names(stored) == ["keep-boss", "ci-failures"]
    assert '"body"' in stored.splitlines()[0]

    account.deliver(
        message_with("nightly 1", "The nightly BUILD FAILED at step 3."),
        "ci@example.org",
    )
    account.deliver(
        message_with("nightly 2", "The nightly build passed."),
        "ci@example.org",
    )

    filed = [subject for subject, _ in mail_in(account, "Lists.CI").values()]
    inbox = [subject for subject, _ in mail_in(account, "INBOX").values()]

    assert filed == ["nightly 1"]
    assert inbox == ["nightly 2"]


# ----------------------------------------------------------------------------
def test_a_header_named_notes_files_only_mail_that_carries_it(account):
    """``--header notes=...`` is a test on the ``Notes`` header (#175).

    sievelib's builder read a name starting with ``not`` as a negation and
    wrote ``not header :contains "notes" ...`` -- a rule the server accepts
    and that files exactly the mail it should leave. Red if the rule is
    inverted (the message without the header is filed and the one with it
    stays), or if the name is read as any other test.
    """
    account.seed_script(ROUNDCUBE_NAME, ROUNDCUBE)

    added = account.run(
        "add",
        "--header",
        "notes=follow up",
        "--name",
        "notes",
        "--fileinto",
        "Notes",
        "--create-folder",
    )

    assert added.code == 0, added.err

    stored = account.script(ROUNDCUBE_NAME)

    assert 'header :contains "notes" "follow up"' in squeeze(stored)
    assert "not header" not in stored

    tagged = message("friend@example.org", "with notes").replace(
        b"\r\n\r\n", b"\r\nNotes: please follow up\r\n\r\n", 1
    )

    account.deliver(tagged, "friend@example.org")
    account.deliver(message("friend@example.org", "without"), "friend@x.org")

    filed = [subject for subject, _ in mail_in(account, "Notes").values()]
    inbox = [subject for subject, _ in mail_in(account, "INBOX").values()]

    assert filed == ["with notes"]
    assert inbox == ["without"]
