"""``mailctl rename-rule`` against a real Dovecot + Pigeonhole (#216).

A rename is only the name. So the script is read back twice, never with
mailctl: its exact bytes from the file Pigeonhole wrote, and its text
through sievelib's own client, and each must be the seeded script with the
one name marker changed. Then new mail is delivered, to show the renamed
rule still files and the renamed disabled rule still does not.
"""

import uuid

import pytest

pytestmark = pytest.mark.container

# An enabled rule, a hand comment, and a rule Roundcube switched off: all
# three must come through a rename of either rule byte for byte.
SCRIPT = """require ["fileinto"];
# rule:[Herrschners Spam]
if header :contains "subject" "yarn"
{
\tfileinto "Yarn";
\tstop;
}
# written by hand, keep me
# rule:[paused]
if false # header :contains "subject" "invoice"
{
    fileinto   "Bills" ;
}
"""


# ----------------------------------------------------------------------------
def message(subject: str) -> bytes:
    return (
        f"From: someone@example.test\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: <{uuid.uuid4().hex}@example.test>\r\n"
        f"\r\n"
        f"Body.\r\n"
    ).encode()


# ----------------------------------------------------------------------------
def subjects_in(account, folder: str) -> list[str]:
    """The subjects in ``folder``, read without marking anything seen."""
    with account.imap() as client:
        client.select_folder(folder, readonly=True)
        uids = client.search("ALL")

        if not uids:
            return []

        fetched = client.fetch(uids, ["BODY.PEEK[HEADER.FIELDS (SUBJECT)]"])

    return sorted(
        data[b"BODY[HEADER.FIELDS (SUBJECT)]"]
        .decode()
        .removeprefix("Subject: ")
        .strip()
        for data in fetched.values()
    )


# ----------------------------------------------------------------------------
def test_rename_rule_changes_the_name_and_no_other_byte(account):
    """Red if any byte but the two name markers moves -- the hand comment,
    the oddly spaced disabled rule, and Roundcube's layout included --, if
    sievelib's client reads back anything else, if the renamed rule stops
    filing, or if the renamed disabled rule starts."""
    with account.imap() as client:
        for folder in ("Yarn", "Bills"):
            client.create_folder(folder)

    account.seed_script("managesieve", SCRIPT)
    before = account.script_bytes("managesieve")

    first = account.run(
        "rename-rule", "Herrschners Spam", "Yarn shops", "--yes"
    )
    second = account.run("rename-rule", "paused", "Invoices (off)", "--yes")

    assert first.code == 0, first.err + first.out
    assert second.code == 0, second.err + second.out

    expected = before.replace(
        b"# rule:[Herrschners Spam]", b"# rule:[Yarn shops]"
    ).replace(b"# rule:[paused]", b"# rule:[Invoices (off)]")

    assert expected != before
    assert account.script_bytes("managesieve") == expected
    assert account.script("managesieve") == expected.decode()
    assert account.active_script() == "managesieve"

    account.deliver(message("yarn sale"), "shop@example.org")
    account.deliver(message("invoice 7"), "billing@example.org")

    assert subjects_in(account, "Yarn") == ["yarn sale"]
    assert subjects_in(account, "Bills") == []
    assert subjects_in(account, "INBOX") == ["invoice 7"]


# ----------------------------------------------------------------------------
def test_rename_rule_refuses_a_taken_name_and_changes_nothing(account):
    """Red if a rename onto another rule's name is uploaded, or the script
    is touched on the way to refusing it."""
    account.seed_script("managesieve", SCRIPT)
    before = account.script_bytes("managesieve")

    result = account.run("rename-rule", "paused", "Herrschners Spam", "--yes")

    assert result.code == 1
    assert "already exists" in result.err
    assert account.script_bytes("managesieve") == before
