"""``mailctl filter optimize`` against a real Dovecot + Pigeonhole (#21).

The claim a merge makes is about behaviour, not bytes: three rules come
back as one, so the script cannot match what went in. What has to match is
where mail goes. So the same messages are handed to ``dovecot-lda`` before
and after, and each must land in the same folder both times; the script
is read back with sievelib's own client, never with mailctl.
"""

import uuid

import pytest
from sievelib.parser import Parser

pytestmark = pytest.mark.container

FOLDERS = ("Trash", "Github", "Github.Billing", "Other")

# The live account's three Trash rules, a disabled one among them, a rule
# repeating the one ahead of it, and a rule that must not move.
MERGEABLE = """require ["fileinto"];
# rule:[Github]
if allof (header :contains "from" "github.com")
{
\tfileinto "Github";
\tstop;
}
# rule:[Github again]
if allof (header :contains "from" "noreply@github.com")
{
\tfileinto "Github";
\tstop;
}
# rule:[Herrschners Spam]
if allof (header :contains "to" "herrschners@example.test")
{
\tfileinto "Trash";
\tstop;
}
# rule:[Rumble]
if allof (header :contains "to" "rumble@example.test")
{
\tfileinto "Trash";
\tstop;
}
# rule:[Off]
if false # allof (header :contains "to" "off@example.test")
{
\tfileinto "Trash";
\tstop;
}
# rule:[Dump list]
if allof (header :contains "to" "aur-general@lists.example.test")
{
\tfileinto "Trash";
\tstop;
}
# rule:[Other]
if allof (header :contains "subject" "other")
{
\tfileinto "Other";
\tstop;
}
"""

# Who each message is from and to, and its subject; one per rule, one
# matching two rules, one disabled-rule address, and one matching none.
TRAFFIC = (
    ("a@github.com", "user@example.test", "gh"),
    ("noreply@github.com", "user@example.test", "gh noreply"),
    ("shop@example.test", "herrschners@example.test", "h"),
    ("vid@example.test", "rumble@example.test", "r"),
    ("list@example.test", "aur-general@lists.example.test", "aur"),
    ("list@example.test", "rumble@example.test", "other r"),
    ("x@example.test", "off@example.test", "off"),
    ("x@example.test", "user@example.test", "nothing"),
)

# A catch-all starving the specific rule after it.
STARVED = """require ["fileinto"];
# rule:[Github catchall]
if allof (header :contains "from" "github.com")
{
\tfileinto "Github";
\tstop;
}
# rule:[Github billing]
if allof (header :contains "from" "billing@github.com")
{
\tfileinto "Github.Billing";
\tstop;
}
"""


# ----------------------------------------------------------------------------
def message(sender: str, recipient: str, subject: str) -> bytes:
    return (
        f"From: {sender}\r\n"
        f"To: {recipient}\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: <{uuid.uuid4().hex}@example.test>\r\n"
        f"\r\n"
        f"Body.\r\n"
    ).encode()


# ----------------------------------------------------------------------------
def where_filed(account) -> dict[str, str]:
    """Subject -> the folder each delivered message is in."""
    found = {}

    with account.imap() as client:
        for folder in ("INBOX", *FOLDERS):
            client.select_folder(folder, readonly=True)
            uids = client.search("ALL")

            if not uids:
                continue

            for data in client.fetch(
                uids, ["BODY.PEEK[HEADER.FIELDS (SUBJECT)]"]
            ).values():
                header = data[b"BODY[HEADER.FIELDS (SUBJECT)]"].decode()
                found[header.split(":", 1)[1].strip()] = folder

    return found


# ----------------------------------------------------------------------------
def deliver_all(account, tag: str, traffic=TRAFFIC) -> None:
    for sender, recipient, subject in traffic:
        account.deliver(message(sender, recipient, f"{tag} {subject}"), sender)


# ----------------------------------------------------------------------------
def rules_in(text: str) -> list[str]:
    """The ``# rule:[NAME]`` markers in ``text``, in order."""
    return [
        line.removeprefix("# rule:[").removesuffix("]")
        for line in text.splitlines()
        if line.startswith("# rule:[")
    ]


# ----------------------------------------------------------------------------
@pytest.fixture
def folders(account):
    with account.imap() as client:
        for folder in FOLDERS:
            client.create_folder(folder)
            client.subscribe_folder(folder)

    return account


# ----------------------------------------------------------------------------
def test_merging_and_removing_files_the_same_mail_as_before(folders):
    """Red if the merged key list loses a key or the merge jumps the
    disabled rule wrongly (a Trash message lands elsewhere), if the
    removed rule was not really redundant (noreply mail moves), if the
    upload skips its backup, or if the stored script is not the flat,
    Roundcube-named list proposed."""
    account = folders
    account.seed_script("managesieve", MERGEABLE)
    deliver_all(account, "before")

    result = account.run("filter", "optimize", "--yes")

    assert result.code == 0, result.err + result.out
    assert list(account.backup_dir.iterdir())

    stored = account.script("managesieve")

    assert Parser().parse(stored)
    assert rules_in(stored) == ["Github", "Herrschners Spam", "Off", "Other"]
    assert (
        '["herrschners@example.test", "rumble@example.test", '
        '"aur-general@lists.example.test"]'
    ) in stored
    assert "if false" in stored

    deliver_all(account, "after")
    filed = where_filed(account)

    for _, _, subject in TRAFFIC:
        assert filed[f"after {subject}"] == filed[f"before {subject}"], (
            subject,
            filed,
        )

    assert filed["after h"] == "Trash"
    assert filed["after gh noreply"] == "Github"
    assert filed["after off"] == "INBOX"


# ----------------------------------------------------------------------------
def test_a_reorder_gives_the_starved_rule_its_mail_and_nothing_else(folders):
    """The one proposal that changes behaviour, by design. Red if billing
    mail still reaches the catch-all, or if any other mail moves."""
    account = folders
    traffic = (
        ("billing@github.com", "user@example.test", "invoice"),
        ("noreply@github.com", "user@example.test", "pr"),
        ("x@example.test", "user@example.test", "nothing"),
    )
    account.seed_script("managesieve", STARVED)
    deliver_all(account, "before", traffic)

    result = account.run("filter", "optimize", "--yes")

    assert result.code == 0, result.err + result.out
    assert rules_in(account.script("managesieve")) == [
        "Github billing",
        "Github catchall",
    ]

    deliver_all(account, "after", traffic)
    filed = where_filed(account)

    assert filed["before invoice"] == "Github"
    assert filed["after invoice"] == "Github.Billing"
    assert filed["after pr"] == filed["before pr"] == "Github"
    assert filed["after nothing"] == filed["before nothing"] == "INBOX"


# ----------------------------------------------------------------------------
def test_a_dry_run_changes_nothing_on_the_server(folders):
    """Red if --dry-run stores anything."""
    account = folders
    account.seed_script("managesieve", MERGEABLE)
    before = account.script_bytes("managesieve")

    result = account.run("filter", "optimize", "--dry-run")

    assert result.code == 0, result.err
    assert "[dry-run]" in result.out
    assert account.script_bytes("managesieve") == before
