"""A filter set changed between plan and execute, on a real server (#257).

``filter add`` plans the merge from the script it read, then uploads it.
Here another client -- sievelib's own, standing in for the webmail's
filter UI -- stores an edit in between, and the upload must be refused
with the edit left exactly as that client stored it.

The oracle is not mailctl: the script is read back off the container's
disk, and the folders through IMAPClient.
"""

import pytest

from mailctl import utilities

pytestmark = pytest.mark.container

ROUNDCUBE = """require ["fileinto","imap4flags"];
# rule:[keep-boss]
if header :contains "from" "boss@example.com"
{
\tfileinto "INBOX.Boss";
\tsetflag "\\\\Flagged";
\tstop;
}
"""

# What the other client adds while mailctl's plan waits.
EDITED = ROUNDCUBE + (
    "# rule:[made-elsewhere]\n"
    'if header :contains "subject" "elsewhere"\n'
    "{\n"
    "\tkeep;\n"
    "}\n"
)


# ----------------------------------------------------------------------------
def test_add_refuses_a_filter_set_edited_after_it_was_planned(
    account, monkeypatch
):
    """Red if ``filter add`` uploads without reading the script again: the
    stored script would then be the plan's merge, with the made-elsewhere
    rule gone, and a backup of the plan's copy would be on disk. Red too if
    the target folder is created before the refusal."""
    account.seed_script("managesieve", ROUNDCUBE)

    execute = utilities.rules.execute_script_change

    def edit_then_execute(*args, **kwargs):
        account.seed_script("managesieve", EDITED, activate=False)

        return execute(*args, **kwargs)

    monkeypatch.setattr(
        utilities.rules, "execute_script_change", edit_then_execute
    )

    result = account.run(
        "filter",
        "add",
        "--from",
        "noreply@github.com",
        "--name",
        "github",
        "--move-to",
        "Lists/GitHub",
        "--create-folder",
    )

    assert result.code != 0
    assert "changed on the server after this change was planned" in result.err
    assert "'mailctl filterset show managesieve' shows it." in result.err
    assert account.script_bytes("managesieve") == EDITED.encode()
    assert account.active_script() == "managesieve"
    assert not account.backup_dir.exists() or not any(
        account.backup_dir.iterdir()
    )

    with account.imap() as client:
        folders = [name for _, _, name in client.list_folders()]

    assert "Lists.GitHub" not in folders


# What another client makes active while mailctl's plan waits (#308).
ELSEWHERE = (
    '# rule:[elsewhere]\nif header :contains "subject" "x"\n{\n\tkeep;\n}\n'
)


# ----------------------------------------------------------------------------
def test_add_refuses_after_another_client_switched_the_active_filter_set(
    account, monkeypatch
):
    """Red if ``filter add`` checks only the planned filter set's bytes:
    the upload would store the merge in ``managesieve`` and make it active
    again, undoing the switch the other client made, and a backup would be
    on disk. Red too if the target folder is created before the refusal."""
    account.seed_script("managesieve", ROUNDCUBE)
    account.seed_script("elsewhere", ELSEWHERE, activate=False)

    execute = utilities.rules.execute_script_change

    def switch_then_execute(*args, **kwargs):
        with account.sieve() as client:
            assert client.setactive("elsewhere"), "SETACTIVE refused"

        return execute(*args, **kwargs)

    monkeypatch.setattr(
        utilities.rules, "execute_script_change", switch_then_execute
    )

    result = account.run(
        "filter",
        "add",
        "--from",
        "noreply@github.com",
        "--name",
        "github",
        "--move-to",
        "Lists/GitHub",
        "--create-folder",
    )

    assert result.code != 0
    assert "the active filter set changed on the server" in result.err
    assert "'mailctl filterset list' shows it." in result.err
    assert account.active_script() == "elsewhere"
    assert account.script_bytes("managesieve") == ROUNDCUBE.encode()
    assert not account.backup_dir.exists() or not any(
        account.backup_dir.iterdir()
    )

    with account.imap() as client:
        folders = [name for _, _, name in client.list_folders()]

    assert "Lists.GitHub" not in folders
