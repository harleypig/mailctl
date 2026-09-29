"""Rule-set optimization (#21): what is proposed, what is refused, and how
the result is uploaded.

The proposals are worked out over the neutral rules the MXroute dialect
reads, and the rearranged script is read back with sievelib, so a test
here fails if the Sieve that would be uploaded is not the arrangement
proposed. Each test's docstring names the break that turns it red.
"""

import pytest
from utilities_support import FakeSieveSession, mxroute

from mailctl import MailctlError, utilities
from mailctl.providers.mxroute import MxrouteDialect
from mailctl.providers.mxroute.sieve import parse_script
from mailctl.rules import Slot
from mailctl.utilities.optimize import (
    KINDS,
    MERGE,
    REDUNDANT,
    REORDER,
    check_kinds,
    execute_optimize,
    plan_optimize,
    propose,
)

REQUIRE = 'require ["fileinto"];\n'


# ----------------------------------------------------------------------------
def rule(
    name: str,
    test: str,
    folder: str = "Trash",
    stop: bool = True,
    comment: str = "",
) -> str:
    """One rule in Roundcube's form."""
    note = f"# {comment}\n" if comment else ""
    ending = "\tstop;\n" if stop else ""

    return (
        f"{note}# rule:[{name}]\nif {test}\n{{\n"
        f'\tfileinto "{folder}";\n{ending}}}\n'
    )


# ----------------------------------------------------------------------------
def to(address: str) -> str:
    """Roundcube's single-condition test on To."""
    return f'allof (header :contains "to" "{address}")'


# ----------------------------------------------------------------------------
def sender(address: str, match: str = "contains") -> str:
    return f'allof (header :{match} "from" "{address}")'


# ----------------------------------------------------------------------------
def script(*rules: str) -> str:
    return REQUIRE + "".join(rules)


# The live account's shape: three rules filing into Trash from To.
TRASH_TRIO = script(
    rule("Herrschners Spam", to("herrschners@harleypig.com")),
    rule("Rumble", to("rumble@harleypig.com"), comment="my note"),
    rule("Dump archlinux list", to("aur-general@lists.archlinux.org")),
)

# A catch-all ahead of the rule it starves.
GITHUB = script(
    rule("Github catchall", sender("github.com"), "Github"),
    rule("Github billing", sender("billing@github.com"), "Github.Billing"),
)


# ----------------------------------------------------------------------------
def proposed(source: str, kinds=KINDS):
    return propose(MxrouteDialect.read_rules(source), kinds)


# ----------------------------------------------------------------------------
def rearranged(source: str, kinds=KINDS) -> str:
    return MxrouteDialect.rearrange_rules(
        source, proposed(source, kinds).layout
    )


# ----------------------------------------------------------------------------
def names(source: str) -> list[str]:
    return [item.name for item in MxrouteDialect.read_rules(source)]


# ############################################################################
# Merging
# ############################################################################


# ----------------------------------------------------------------------------
def test_three_rules_doing_the_same_become_one_with_a_key_list():
    """Red if the merge is missed, if a key is dropped or reordered, if the
    survivor loses its name, or if the merged rule no longer stops."""
    proposals = proposed(TRASH_TRIO)

    (merge,) = proposals.merges

    assert merge.into == "Herrschners Spam"
    assert merge.absorbed == ("Rumble", "Dump archlinux list")
    assert merge.keys == (
        "herrschners@harleypig.com",
        "rumble@harleypig.com",
        "aur-general@lists.archlinux.org",
    )
    assert proposals.layout == (Slot(0, (1, 2)),)

    (merged,) = MxrouteDialect.read_rules(rearranged(TRASH_TRIO))

    assert merged.name == "Herrschners Spam"
    assert merged.tests[0].keys == merge.keys
    assert merged.stops
    assert merged.effects == ('fileinto "Trash";', "stop;")


# ----------------------------------------------------------------------------
def test_the_merged_script_is_a_flat_list_in_roundcubes_form():
    """Red if a merge nests one rule inside another, or loses the
    Roundcube name marker or an absorbed rule's own comment."""
    after = rearranged(TRASH_TRIO)

    assert "# rule:[Herrschners Spam]" in after
    assert "# my note" in after

    for entry in parse_script(after).filters:
        assert not any(
            child.name in ("if", "elsif", "else")
            for child in entry["content"].children
        ), after


# ----------------------------------------------------------------------------
def test_a_live_rule_between_them_keeps_two_rules_apart():
    """Red if the merge jumps a live rule, which might catch some of the
    second rule's mail first."""
    source = script(
        rule("a", to("a@x.test")),
        rule("between", sender("b@x.test"), "Other"),
        rule("c", to("c@x.test")),
    )

    proposals = proposed(source)

    assert proposals.merges == ()
    assert [item.kind for item in proposals.uncertain] == [MERGE]
    assert proposals.uncertain[0].rules == ("a", "c")


# ----------------------------------------------------------------------------
def test_a_disabled_rule_between_them_does_not_and_is_never_merged():
    """Red if a disabled rule of the same shape is merged, or blocks the
    rules around it."""
    source = script(
        rule("a", to("a@x.test")),
        rule("off", 'false # header :contains "to" "off@x.test"'),
        rule("c", to("c@x.test")),
    )

    proposals = proposed(source)

    (merge,) = proposals.merges

    assert (merge.into, merge.absorbed) == ("a", ("c",))
    assert names(rearranged(source)) == ["a", "off"]
    assert MxrouteDialect.read_rules(rearranged(source))[1].disabled


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "second",
    [
        rule("b", to("b@x.test"), folder="Junk"),
        rule("b", 'allof (header :is "to" "b@x.test")'),
        rule(
            "b",
            'allof (header :comparator "i;octet" :contains "to" "b@x.test")',
        ),
        rule("b", 'allof (header :contains "cc" "b@x.test")'),
        rule(
            "b",
            'allof (header :contains "to" "b@x.test", '
            'header :contains "subject" "hi")',
        ),
        rule("b", 'allof (header :contains ["to", "cc"] "b@x.test")'),
        rule("b", 'allof (exists "x-spam")'),
    ],
    ids=[
        "other-action",
        "other-match",
        "other-comparator",
        "other-header",
        "two-tests",
        "two-headers",
        "unmodelled",
    ],
)
def test_rules_that_are_not_the_same_filter_are_not_merged(second):
    """Red if anything but the keys may differ between rules merged."""
    source = script(rule("a", to("a@x.test")), second)

    proposals = proposed(source)

    assert proposals.merges == ()
    assert proposals.layout == (Slot(0), Slot(1))


# ----------------------------------------------------------------------------
def test_rules_that_do_not_stop_are_reported_not_merged():
    """Red if two rules without stop are merged: today a message matching
    both gets the actions twice, merged it would get them once."""
    source = script(
        rule("a", to("a@x.test"), stop=False),
        rule("b", to("b@x.test"), stop=False),
    )

    proposals = proposed(source)

    assert proposals.merges == ()
    assert [(item.kind, item.rules) for item in proposals.uncertain] == [
        (MERGE, ("a", "b"))
    ]


# ############################################################################
# Reordering
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_starved_specific_rule_is_moved_ahead_of_its_container():
    """Red if the billing rule is left behind the catch-all that stops all
    of its mail, or is moved anywhere but just before it."""
    proposals = proposed(GITHUB)

    (move,) = proposals.reorders

    assert (move.rule, move.before) == ("Github billing", "Github catchall")
    assert names(rearranged(GITHUB)) == ["Github billing", "Github catchall"]
    assert proposals.uncertain == ()


# ----------------------------------------------------------------------------
def test_the_move_lands_just_before_the_container_not_at_the_top():
    """Red if a moved rule jumps rules ahead of its container, whose
    relative order is not the move's to change."""
    source = script(
        rule("first", sender("first@x.test"), "First"),
        GITHUB.removeprefix(REQUIRE),
    )

    assert names(rearranged(source)) == [
        "first",
        "Github billing",
        "Github catchall",
    ]


# ----------------------------------------------------------------------------
def test_a_chain_is_ordered_most_specific_first():
    source = script(
        rule("broad", sender("github.com"), "A"),
        rule("middle", sender("noreply@github.com"), "B"),
        rule("narrow", sender("billing-noreply@github.com"), "C"),
    )

    assert names(rearranged(source)) == ["narrow", "middle", "broad"]


# ----------------------------------------------------------------------------
def test_two_rules_testing_the_same_mail_differently_are_not_reordered():
    """Red if mailctl picks a winner between equal conditions with
    different actions: which should run is the user's call."""
    source = script(
        rule("a", sender("x@y.test"), "A"),
        rule("b", sender("x@y.test"), "B"),
    )

    proposals = proposed(source)

    assert proposals.reorders == ()
    assert [(item.kind, item.rules) for item in proposals.uncertain] == [
        (REORDER, ("a", "b"))
    ]


# ----------------------------------------------------------------------------
def test_a_suspected_shadow_is_reported_and_left_alone():
    """Red if a rule is moved on a POSSIBLE finding (a glob here)."""
    source = script(
        rule("glob", sender("*github*", "matches"), "A"),
        rule("billing", sender("billing@github.com"), "B"),
    )

    proposals = proposed(source)

    assert proposals.reorders == ()
    assert [(item.kind, item.rules) for item in proposals.uncertain] == [
        (REORDER, ("glob", "billing"))
    ]


# ----------------------------------------------------------------------------
def test_a_broad_rule_without_stop_starves_nothing():
    source = script(
        rule("broad", sender("github.com"), "A", stop=False),
        rule("narrow", sender("billing@github.com"), "B"),
    )

    assert not proposed(source).changes


# ----------------------------------------------------------------------------
def test_a_rule_mailctl_cannot_read_is_passed_but_never_moved():
    """Red if the unmodelled rule moves, or counts as a container."""
    source = script(
        rule("broad", sender("github.com"), "A"),
        rule("unread", 'allof (exists "x-github")', "B"),
        rule("narrow", sender("billing@github.com"), "C"),
    )

    assert names(rearranged(source)) == ["narrow", "broad", "unread"]


# ############################################################################
# Redundancy
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_rule_that_repeats_an_earlier_one_is_removed():
    """Red if a rule that can never run, and would only do what the rule
    ahead of it already did, is kept -- or moved instead."""
    source = script(
        rule("github", sender("github.com"), "Github"),
        rule("noreply", sender("noreply@github.com"), "Github"),
    )

    proposals = proposed(source)

    assert [(r.rule, r.covered_by) for r in proposals.removals] == [
        ("noreply", "github")
    ]
    assert proposals.reorders == ()
    assert names(rearranged(source)) == ["github"]


# ----------------------------------------------------------------------------
def test_a_repeat_behind_a_rule_without_stop_is_kept():
    """Red if a rule that still runs is removed as redundant."""
    source = script(
        rule("github", sender("github.com"), "Github", stop=False),
        rule("noreply", sender("noreply@github.com"), "Github"),
    )

    assert proposed(source).removals == ()


# ############################################################################
# Kinds
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_skipped_kind_is_not_proposed():
    source = script(
        GITHUB.removeprefix(REQUIRE), TRASH_TRIO.removeprefix(REQUIRE)
    )

    proposals = proposed(source, (REDUNDANT, REORDER))

    assert proposals.merges == ()
    assert len(proposals.reorders) == 1
    assert not proposed(source, ()).changes


# ----------------------------------------------------------------------------
def test_an_unknown_kind_is_refused_naming_the_known_ones():
    with pytest.raises(MailctlError, match="unknown kind of change: tidy"):
        check_kinds(["merge", "tidy"])


# ############################################################################
# Plan and execute
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.fixture
def trio():
    return FakeSieveSession(script=TRASH_TRIO)


# ----------------------------------------------------------------------------
def test_the_plan_reads_and_changes_nothing(trio):
    """Red if planning writes to the server."""
    plan = plan_optimize(mxroute(sieve=trio))

    assert plan.changes
    assert plan.script == "managesieve"
    assert plan.after == rearranged(TRASH_TRIO)
    assert '["herrschners@harleypig.com"' in plan.diff.text
    assert trio.names() == ["get_script"]


# ----------------------------------------------------------------------------
def test_applying_backs_up_first_then_uploads(trio, imap_config, tmp_path):
    """Red if the upload bypasses ``upload_script`` -- no backup, or the
    backup written after the server saw the script."""
    imap_config.backup_dir = tmp_path / "backups"
    session = mxroute(sieve=trio)
    plan = plan_optimize(session)
    events = []

    path = execute_optimize(session, imap_config, plan, events.append)

    assert path.read_bytes() == TRASH_TRIO.encode()
    assert [type(event) for event in events] == [
        utilities.events.ScriptBackedUp,
        utilities.events.ScriptUploaded,
    ]
    assert trio.names()[-3:] == ["check_script", "put_script", "set_active"]
    assert trio.calls[-2] == ("put_script", "managesieve", plan.after)


# ----------------------------------------------------------------------------
def test_a_rejected_rearrangement_leaves_the_backup(
    trio, imap_config, tmp_path
):
    imap_config.backup_dir = tmp_path / "backups"
    trio.reject = True
    session = mxroute(sieve=trio)
    plan = plan_optimize(session)

    with pytest.raises(MailctlError, match="rejected"):
        execute_optimize(session, imap_config, plan)

    assert "put_script" not in trio.names()
    assert list((tmp_path / "backups").iterdir())


# ----------------------------------------------------------------------------
def test_a_rearrangement_that_does_not_read_back_is_refused(trio, monkeypatch):
    """Red if a script the dialect got wrong is offered anyway: here the
    merge is dropped, so the survivor keeps one key."""
    wrong = classmethod(
        lambda cls, source, layout: MxrouteDialect.remove_rule(
            MxrouteDialect.remove_rule(source, "Rumble"), "Dump archlinux list"
        )
    )
    monkeypatch.setattr(MxrouteDialect, "rearrange_rules", wrong)

    with pytest.raises(MailctlError, match="does not read back"):
        plan_optimize(mxroute(sieve=trio))


# ----------------------------------------------------------------------------
def test_nothing_to_change_is_a_plan_that_cannot_be_executed(
    imap_config,
):
    session = mxroute(sieve=FakeSieveSession(script=GITHUB))
    plan = plan_optimize(session, kinds=(MERGE,))

    assert not plan.changes
    assert plan.after == plan.before

    with pytest.raises(MailctlError, match="nothing to change"):
        execute_optimize(session, imap_config, plan)


# ----------------------------------------------------------------------------
def test_an_empty_script_is_refused():
    with pytest.raises(MailctlError, match="is empty"):
        plan_optimize(mxroute(sieve=FakeSieveSession(script="")))
