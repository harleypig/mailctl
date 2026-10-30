"""The utilities for reading scripts, driven as any front-end would.

No argparse and no stdout here: every test builds plain inputs, calls
the utility, and asserts on what comes back and on what the fakes were
asked to do.

The Sieve side is a session-level fake (``FakeSieveSession``); the IMAP
side is the real ``ImapSession`` over the ``FakeIMAPClient`` double from
conftest, so folder normalization and planning run for real.
"""

import pytest
from utilities_support import FakeSieveSession, criteria, mxroute

from mailctl import MailctlError, utilities
from mailctl.cli import error_text
from mailctl.providers.base import ActionSpec, Placement
from mailctl.providers.mxroute import MXROUTE
from mailctl.utilities.backup import BackupFile
from mailctl.utilities.rules import RuleRequest

# ############################################################################
# Reading the account
# ############################################################################


# ----------------------------------------------------------------------------
def test_read_script_takes_the_active_one_and_parses_only_on_demand(
    sessions,
):
    script = utilities.scripts.read_script(sessions)

    assert script.name == "managesieve"
    assert script.rule_names() == ["keep-boss", "bin-the-noise"]

    # Showing an unparseable script must still be possible.
    broken = utilities.scripts.ScriptText("broken", "if {{{", MXROUTE)

    with pytest.raises(MailctlError):
        broken.rule_names()


# ----------------------------------------------------------------------------
def test_reading_with_no_active_script_says_so():
    empty = mxroute(sieve=FakeSieveSession(active=None))

    with pytest.raises(MailctlError, match="name one explicitly"):
        utilities.scripts.read_script(empty)

    with pytest.raises(MailctlError, match="no filters to show"):
        utilities.rules.read_rules(empty)


# ############################################################################
# A filter set changed between plan and execute (#257)
# ############################################################################

# What another client -- the webmail's filter UI, a second run -- adds
# while a plan waits for its decision.
EDIT = "# rule:[made-elsewhere]\nif true { keep; }\n"


# ----------------------------------------------------------------------------
def _add(session, config):
    request = RuleRequest(criteria(), ActionSpec(fileinto="Lists"))
    folder = utilities.folders.plan_folder(session, config, "Lists")

    return utilities.rules.plan_rule(session, config, request, folder)


# ----------------------------------------------------------------------------
def _restore(session, config):
    backup = BackupFile(config.backup_dir / "old.sieve", 'require "x";\n')

    return utilities.backup.plan_restore(session, backup)


# Every plan kind that ends in ``execute_script_change`` or
# ``execute_restore``; optimize and folder rename are in their own files.
SCRIPT_PLANS = {
    "add": _add,
    "remove": lambda s, _: utilities.rules.plan_removal(s, "keep-boss"),
    "move": lambda s, _: utilities.rules.plan_move(
        s, "bin-the-noise", Placement("first")
    ),
    "switch": lambda s, _: utilities.rules.plan_switch(
        s, "keep-boss", enable=False
    ),
    "rename": lambda s, _: utilities.rules.plan_rename(s, "keep-boss", "boss"),
    "restore": _restore,
}


# ----------------------------------------------------------------------------
def _execute(session, config, plan):
    if isinstance(plan, utilities.backup.RestorePlan):
        return utilities.backup.execute_restore(session, config, plan)

    return utilities.rules.execute_script_change(session, config, plan)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("kind", sorted(SCRIPT_PLANS))
def test_a_filter_set_changed_since_the_plan_is_refused_unsent(
    kind, sessions, imap_config, fake_sieve, tmp_path
):
    """Red if an execute path uploads without reading the server's copy
    again: the upload would overwrite the edit, and the backup would hold
    the plan's copy rather than what was overwritten."""
    imap_config.backup_dir = tmp_path / "backups"
    plan = SCRIPT_PLANS[kind](sessions, imap_config)
    edited = plan.before + EDIT
    fake_sieve.script = edited
    fake_sieve.calls.clear()

    with pytest.raises(MailctlError) as caught:
        _execute(sessions, imap_config, plan)

    assert caught.value.code == "filterset_changed"
    assert fake_sieve.names() == ["get_script"]
    assert fake_sieve.script == edited
    assert not (tmp_path / "backups").exists()


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("kind", sorted(SCRIPT_PLANS))
def test_an_unchanged_filter_set_is_read_again_then_uploaded(
    kind, sessions, imap_config, fake_sieve, tmp_path
):
    """The other side: red if the check refuses what it should let by, or
    reads the server's copy only after the upload has begun."""
    imap_config.backup_dir = tmp_path / "backups"
    plan = SCRIPT_PLANS[kind](sessions, imap_config)
    fake_sieve.calls.clear()

    path = _execute(sessions, imap_config, plan)

    assert path is not None
    assert path.read_text() == plan.before
    assert fake_sieve.names()[:3] == [
        "get_script",
        "check_script",
        "put_script",
    ]


# ----------------------------------------------------------------------------
def test_a_filter_set_created_since_the_plan_is_refused(imap_config, tmp_path):
    """A plan for an account with no filter set was made from nothing; one
    another client created meanwhile is a change like any other."""
    imap_config.backup_dir = tmp_path / "backups"
    fake = FakeSieveSession(active=None)
    session = mxroute(sieve=fake)
    request = RuleRequest(criteria(), ActionSpec(discard=True))
    folder = utilities.folders.plan_folder(session, imap_config, None)
    plan = utilities.rules.plan_rule(session, imap_config, request, folder)

    assert plan.before == ""

    fake.others = [plan.script]
    fake.script = EDIT

    with pytest.raises(MailctlError) as caught:
        utilities.rules.execute_script_change(session, imap_config, plan)

    assert caught.value.code == "filterset_changed"
    assert "put_script" not in fake.names()


# ----------------------------------------------------------------------------
def test_the_refusal_names_the_filter_set_and_the_cli_how_to_see_it(
    sessions, fake_sieve
):
    """Red if the code has no rendering, or the rendering drops the name
    of the filter set to look at."""
    fake_sieve.script += EDIT

    with pytest.raises(MailctlError) as caught:
        utilities.scripts.require_unchanged(sessions, "managesieve", "")

    assert error_text(caught.value) == (
        "the filter set 'managesieve' changed on the server after this "
        "change was planned, so nothing was sent and the change made there "
        "is kept. Plan the change again against what it holds now. "
        "'mailctl filterset show managesieve' shows it."
    )
