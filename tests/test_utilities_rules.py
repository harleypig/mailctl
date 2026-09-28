"""Checking, planning, and uploading rules, driven as a front-end would.

No argparse and no stdout here: every test builds plain inputs, calls
the utility, and asserts on what comes back and on what the fakes were
asked to do.

The Sieve side is a session-level fake (``FakeSieveSession``); the IMAP
side is the real ``ImapSession`` over the ``FakeIMAPClient`` double from
conftest, so folder normalization and planning run for real.
"""

import pytest
from utilities_support import FakeSieveSession, criteria

from mailctl import MailctlError, utilities
from mailctl.components.managesieve import rule_names
from mailctl.config import Config
from mailctl.providers.mxroute import MxrouteProvider
from mailctl.providers.mxroute.sieve import (
    merge_rule,
    parse_script,
    render_script,
)
from mailctl.utilities.rules import (
    PLACE_AFTER,
    PLACE_BEFORE,
    PLACE_FIRST,
    ActionSpec,
    Placement,
    RuleRequest,
)

# ############################################################################
# Reading the account
# ############################################################################


# ----------------------------------------------------------------------------
def test_read_rules_returns_rules_in_order_with_findings(sessions):
    report = utilities.rules.read_rules(sessions)

    assert report.script == "managesieve"
    assert [rule.name for rule in report.rules] == [
        "keep-boss",
        "bin-the-noise",
    ]
    assert report.findings == []


# ############################################################################
# Actions
# ############################################################################


# ----------------------------------------------------------------------------
def test_redirect_is_refused_with_the_forwarder_pointer():
    with pytest.raises(MailctlError, match=r"(?i)forward"):
        utilities.rules.reject_actions(Config(), ["redirect"])


# ----------------------------------------------------------------------------
def test_an_unimplemented_action_is_refused_as_our_choice():
    with pytest.raises(MailctlError, match="conservative choice of ours"):
        utilities.rules.reject_actions(Config(), ["vacation"])


# ----------------------------------------------------------------------------
def test_nothing_refused_when_nothing_refused_was_asked_for():
    assert utilities.rules.reject_actions(Config(), []) is None


# ----------------------------------------------------------------------------
def test_required_extensions_follow_the_actions():
    spec = ActionSpec(fileinto="Lists", flags=("\\Seen",))

    assert MxrouteProvider.required_features(spec, "INBOX.Lists", True) == {
        "fileinto",
        "imap4flags",
        "mailbox",
    }
    assert (
        MxrouteProvider.required_features(ActionSpec(discard=True), "", False)
        == set()
    )


# ----------------------------------------------------------------------------
def test_a_default_folder_needs_fileinto_like_an_explicit_one(imap_config):
    """The folder can come from config; the rule still files into it."""
    imap_config.default_folder = "Lists"
    live = MxrouteProvider(sieve=FakeSieveSession(caps=["imap4flags"]))

    folder = utilities.folders.plan_folder(live, imap_config, None)

    assert utilities.rules.missing_extensions(live, ActionSpec(), folder) == [
        "fileinto"
    ]


# ----------------------------------------------------------------------------
def test_the_default_rule_name_is_derived_from_the_first_criterion():
    assert (
        utilities.rules.default_rule_name(criteria())
        == "from-noreply-github-com"
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("Café", "subject-café", id="accented"),
        pytest.param("Cafe\u0301 Menu", "subject-café-menu", id="combining"),
        pytest.param("Ünïcödé_Test", "subject-ünïcödé-test", id="underscore"),
        pytest.param("会議 のお知らせ", "subject-会議-のお知らせ", id="cjk"),
    ],
)
def test_the_default_rule_name_keeps_non_ascii_letters(value, expected):
    """``Café`` used to become ``subject-caf`` (#118)."""
    name = utilities.rules.default_rule_name(criteria("Subject", value))

    assert name == expected


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["Café", "会議のお知らせ"])
def test_a_non_ascii_default_name_round_trips_through_the_script(value):
    """The name written into ``# rule:[...]`` is the name read back."""
    terms = criteria("Subject", value)
    name = utilities.rules.default_rule_name(terms)

    merged = merge_rule(
        "",
        name,
        terms.sieve_conditions(),
        [("fileinto", "INBOX.Archive"), ("stop",)],
        terms.sieve_matchtype(),
    )

    assert f"# rule:[{name}]" in merged
    rendered = render_script(parse_script(merged))

    assert rule_names(parse_script(merged)) == [name]
    assert f"# rule:[{name}]" in rendered
    assert rule_names(parse_script(rendered)) == [name]


# ############################################################################
# Adding and removing rules
# ############################################################################


# ----------------------------------------------------------------------------
def folder_for(sessions, config, name="Lists"):
    return utilities.folders.plan_folder(sessions, config, name)


# ----------------------------------------------------------------------------
def test_plan_rule_merges_without_touching_the_server(
    sessions, imap_config, fake_sieve, reparse
):
    request = RuleRequest(criteria(), ActionSpec(fileinto="Lists"))

    plan = utilities.rules.plan_rule(
        sessions, imap_config, request, folder_for(sessions, imap_config)
    )

    assert plan.script == "managesieve"
    assert plan.name == "from-noreply-github-com"
    assert rule_names(parse_script(plan.after)) == [
        "keep-boss",
        "bin-the-noise",
        "from-noreply-github-com",
    ]
    assert "noreply@github.com" in plan.diff.text
    assert fake_sieve.names() == ["get_script"]

    reparse(plan.after)


# ----------------------------------------------------------------------------
def test_on_an_empty_account_the_default_script_name_is_used(
    imap_session, imap_config
):
    live = MxrouteProvider(FakeSieveSession(active=None), imap_session)
    request = RuleRequest(criteria(), ActionSpec(fileinto="Lists"))

    plan = utilities.rules.plan_rule(
        live, imap_config, request, folder_for(live, imap_config)
    )

    assert plan.script == utilities.scripts.DEFAULT_SCRIPT_NAME
    assert plan.before == ""
    assert plan.script == "mailctl"


# ----------------------------------------------------------------------------
def test_an_inactive_old_name_script_is_reused_not_duplicated(
    imap_session, imap_config
):
    """A script mailctl wrote as 'mxfilter' is still ours: with nothing
    active it is picked up again rather than a 'mailctl' made beside it."""
    sieve = FakeSieveSession(
        script="# rule:[old]\n", active=None, others=["mxfilter"]
    )
    live = MxrouteProvider(sieve, imap_session)
    request = RuleRequest(criteria(), ActionSpec(fileinto="Lists"))

    plan = utilities.rules.plan_rule(
        live, imap_config, request, folder_for(live, imap_config)
    )

    assert plan.script == "mxfilter"
    assert plan.before == "# rule:[old]\n"
    assert ("get_script", "mxfilter") in sieve.calls


# ----------------------------------------------------------------------------
def test_an_active_old_name_script_stays_the_one_edited(
    imap_session, imap_config
):
    live = MxrouteProvider(FakeSieveSession(active="mxfilter"), imap_session)
    request = RuleRequest(criteria(), ActionSpec(fileinto="Lists"))

    plan = utilities.rules.plan_rule(
        live, imap_config, request, folder_for(live, imap_config)
    )

    assert plan.script == "mxfilter"


# ----------------------------------------------------------------------------
def test_a_rule_that_does_nothing_is_refused(sessions, imap_config):
    request = RuleRequest(criteria(), ActionSpec())

    with pytest.raises(MailctlError, match="no action requested"):
        utilities.rules.plan_rule(
            sessions,
            imap_config,
            request,
            folder_for(sessions, imap_config, None),
        )


# ----------------------------------------------------------------------------
def test_a_duplicate_name_is_refused_without_replace(sessions, imap_config):
    request = RuleRequest(
        criteria(), ActionSpec(fileinto="Lists"), name="keep-boss"
    )

    with pytest.raises(MailctlError, match="already exists"):
        utilities.rules.plan_rule(
            sessions, imap_config, request, folder_for(sessions, imap_config)
        )


# ----------------------------------------------------------------------------
def test_the_plan_judges_the_rule_where_it_will_land(sessions, imap_config):
    """First, a rule matching everything keep-boss matches starves it."""
    request = RuleRequest(
        criteria("from", "boss@example.com"),
        ActionSpec(fileinto="Lists"),
        name="grab-boss",
        placement=Placement(PLACE_FIRST),
    )

    plan = utilities.rules.plan_rule(
        sessions, imap_config, request, folder_for(sessions, imap_config)
    )

    assert "keep-boss" in {
        finding.narrow for finding in plan.placement.starves
    }


# ----------------------------------------------------------------------------
def test_missing_extensions_are_read_from_the_server(imap_config):
    live = MxrouteProvider(sieve=FakeSieveSession(caps=["fileinto"]))
    spec = ActionSpec(fileinto="Lists", flags=("\\Seen",))

    folder = utilities.folders.plan_folder(live, imap_config, "Lists")

    assert utilities.rules.missing_extensions(live, spec, folder) == [
        "imap4flags"
    ]


# ----------------------------------------------------------------------------
def test_an_upload_backs_up_first_and_reports_each_step(
    sessions, imap_config, fake_sieve, tmp_path
):
    imap_config.backup_dir = tmp_path / "backups"
    request = RuleRequest(criteria(), ActionSpec(fileinto="Lists"))
    plan = utilities.rules.plan_rule(
        sessions, imap_config, request, folder_for(sessions, imap_config)
    )

    events = []

    path = utilities.rules.execute_script_change(
        sessions, imap_config, plan, events.append
    )

    assert path.read_bytes() == plan.before.encode()
    assert [type(event) for event in events] == [
        utilities.events.ScriptBackedUp,
        utilities.events.ScriptUploaded,
    ]
    assert events[0].path == path
    assert fake_sieve.names()[-3:] == [
        "check_script",
        "put_script",
        "set_active",
    ]


# ----------------------------------------------------------------------------
def test_a_rejected_upload_still_leaves_and_announces_the_backup(
    sessions, imap_config, fake_sieve, tmp_path
):
    imap_config.backup_dir = tmp_path / "backups"
    fake_sieve.reject = True
    request = RuleRequest(criteria(), ActionSpec(fileinto="Lists"))
    plan = utilities.rules.plan_rule(
        sessions, imap_config, request, folder_for(sessions, imap_config)
    )

    events = []

    with pytest.raises(MailctlError, match="rejected"):
        utilities.rules.execute_script_change(
            sessions, imap_config, plan, events.append
        )

    assert [type(event) for event in events] == [
        utilities.events.ScriptBackedUp
    ]
    assert events[0].path.exists()
    assert "put_script" not in fake_sieve.names()


# ----------------------------------------------------------------------------
def test_a_removal_is_planned_without_touching_the_server(
    sessions, fake_sieve
):
    plan = utilities.rules.plan_removal(sessions, "keep-boss")

    assert rule_names(parse_script(plan.after)) == ["bin-the-noise"]
    assert "put_script" not in fake_sieve.names()


# ----------------------------------------------------------------------------
def test_removing_from_an_empty_script_or_an_unknown_rule_is_refused(
    sessions,
):
    with pytest.raises(MailctlError, match="no rule named"):
        utilities.rules.plan_removal(sessions, "phantom")

    empty = MxrouteProvider(sieve=FakeSieveSession(script=""))

    with pytest.raises(MailctlError, match="is empty"):
        utilities.rules.plan_removal(empty, "keep-boss")


# ############################################################################
# Which script ends up active (#53)
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "active", "requested", "expected"),
    [
        ("managesieve", "managesieve", False, True),
        ("spare", "managesieve", False, False),
        ("spare", "managesieve", True, True),
        ("spare", None, False, True),
    ],
)
def test_only_the_active_script_or_an_explicit_ask_activates(
    name, active, requested, expected
):
    assert utilities.scripts.activates(name, active, requested) is expected


# ----------------------------------------------------------------------------
def test_editing_another_script_leaves_the_active_one_running(
    imap_config, tmp_path, roundcube_script
):
    imap_config.backup_dir = tmp_path
    fake = FakeSieveSession(script=roundcube_script, others=["spare"])
    live = MxrouteProvider(sieve=fake)

    plan = utilities.rules.plan_removal(live, "keep-boss", script="spare")

    assert (plan.script, plan.active, plan.activate) == (
        "spare",
        "managesieve",
        False,
    )

    events = []
    utilities.rules.execute_script_change(
        live, imap_config, plan, events.append
    )

    assert "set_active" not in fake.names()
    assert fake.calls[-1][:2] == ("put_script", "spare")
    assert events[-1] == utilities.events.ScriptUploaded(
        "spare", activated=False
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["rule", "removal", "move"])
def test_activate_switches_the_running_script_when_asked(
    kind, imap_config, imap_session, tmp_path, roundcube_script
):
    imap_config.backup_dir = tmp_path
    fake = FakeSieveSession(script=roundcube_script, others=["spare"])
    live = MxrouteProvider(sieve=fake, imap=imap_session)

    if kind == "rule":
        request = RuleRequest(
            criteria(),
            ActionSpec(fileinto="Lists"),
            script="spare",
            activate=True,
        )
        plan = utilities.rules.plan_rule(
            live, imap_config, request, folder_for(live, imap_config)
        )

    elif kind == "removal":
        plan = utilities.rules.plan_removal(
            live, "keep-boss", "spare", activate=True
        )

    else:
        plan = utilities.rules.plan_move(
            live, "bin-the-noise", Placement(PLACE_FIRST), "spare", True
        )

    events = []
    utilities.rules.execute_script_change(
        live, imap_config, plan, events.append
    )

    assert fake.calls[-1] == ("set_active", "spare")
    assert events[-1] == utilities.events.ScriptUploaded(
        "spare", activated=True
    )


# ----------------------------------------------------------------------------
def test_with_no_active_script_the_edited_one_is_activated(
    sessions, imap_config, tmp_path
):
    imap_config.backup_dir = tmp_path
    sessions.sieve.active = None
    request = RuleRequest(criteria(), ActionSpec(fileinto="Lists"))

    plan = utilities.rules.plan_rule(
        sessions, imap_config, request, folder_for(sessions, imap_config)
    )

    assert plan.activate
    utilities.rules.execute_script_change(sessions, imap_config, plan)
    assert sessions.sieve.calls[-1] == ("set_active", plan.script)


# ############################################################################
# Moving a rule
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_move_changes_the_order_and_nothing_else(
    sessions, fake_sieve, reparse
):
    plan = utilities.rules.plan_move(
        sessions, "bin-the-noise", Placement(PLACE_FIRST)
    )

    assert (plan.from_index, plan.to_index, plan.count) == (1, 0, 2)
    assert plan.changes
    assert rule_names(parse_script(plan.after)) == [
        "bin-the-noise",
        "keep-boss",
    ]
    assert "put_script" not in fake_sieve.names()

    before = {
        r.name: (r.tests, r.actions)
        for r in MxrouteProvider.read_rules(plan.before)
    }
    after = {
        r.name: (r.tests, r.actions)
        for r in MxrouteProvider.read_rules(plan.after)
    }

    assert before == after
    reparse(plan.after)


# ----------------------------------------------------------------------------
def test_moving_a_broad_rule_first_reports_what_it_starves(imap_config):
    script = (
        'require ["fileinto"];\n'
        "# rule:[announce]\n"
        'if header :contains "to" "announce@lists.example.com" '
        '{ fileinto "A"; stop; }\n'
        "# rule:[all-lists]\n"
        'if header :contains "to" "@lists.example.com" '
        '{ fileinto "L"; stop; }\n'
    )
    live = MxrouteProvider(sieve=FakeSieveSession(script=script))

    plan = utilities.rules.plan_move(live, "all-lists", Placement(PLACE_FIRST))

    assert {f.narrow for f in plan.placement.starves} == {"announce"}
    assert plan.placement.dead_on_arrival == []


# ----------------------------------------------------------------------------
def test_a_move_to_where_the_rule_already_is_changes_nothing(sessions):
    plan = utilities.rules.plan_move(
        sessions, "keep-boss", Placement(PLACE_FIRST)
    )

    assert not plan.changes


# ----------------------------------------------------------------------------
def test_moving_an_unknown_rule_or_against_an_unknown_anchor_is_refused(
    sessions,
):
    with pytest.raises(MailctlError, match="no rule named 'phantom'"):
        utilities.rules.plan_move(sessions, "phantom", Placement(PLACE_FIRST))

    with pytest.raises(MailctlError, match="Known rules"):
        utilities.rules.plan_move(
            sessions, "keep-boss", Placement(PLACE_AFTER, "phantom")
        )

    with pytest.raises(MailctlError, match="has no position"):
        utilities.rules.plan_move(
            sessions, "keep-boss", Placement(PLACE_BEFORE, "keep-boss")
        )


# ----------------------------------------------------------------------------
def test_an_executed_move_is_backed_up_and_uploaded(
    sessions, imap_config, fake_sieve, tmp_path
):
    imap_config.backup_dir = tmp_path
    plan = utilities.rules.plan_move(
        sessions, "bin-the-noise", Placement(PLACE_FIRST)
    )

    utilities.rules.execute_script_change(sessions, imap_config, plan)

    assert fake_sieve.names()[-3:] == [
        "check_script",
        "put_script",
        "set_active",
    ]
    assert len(list(tmp_path.iterdir())) == 1
