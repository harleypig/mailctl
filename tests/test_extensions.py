"""Sieve extensions: what mailctl emits, and the switch that narrows it (#82).

Two things are pinned here. The emit table (``engine.EMIT_TABLE``) is the
one place that says which extension each emitted command, test, and tag
needs, so it is checked against sievelib's own ``require`` line over every
rule shape the engine can build -- sievelib is the independent oracle, and
the shapes are derived from ``ActionSpec`` and the criteria modes rather
than hand-picked. Then ``disabled_extensions``: a disabled extension counts
as not advertised, which refuses a rule that needs it, falls back where a
fallback exists, and changes nothing where the server never had it.
"""

import itertools
import re
from typing import cast

import pytest

from mailctl import MailctlError, engine
from mailctl.config import FLAG, Source
from mailctl.criteria import COMPARE_OPS, MATCH_MODES, Criteria
from mailctl.engine import ActionSpec, RuleRequest, Sessions
from mailctl.sieve import SieveSession, merge_rule

FULL = ["fileinto", "imap4flags", "mailbox"]
NO_MAILBOX = ["fileinto", "imap4flags"]
FLAG_SOURCE = Source(FLAG, "--disable-extension")

# Sieve's control structure. Not features of a rule, so not in the table.
CONTROL = {"require", "if"}

# ############################################################################
# Fakes
# ############################################################################


class FakeSieveSession:
    """A ManageSieve session holding one empty active script."""

    # ------------------------------------------------------------------------
    def __init__(self, caps):
        self.caps = list(caps)
        self.calls: list[tuple] = []

    # ------------------------------------------------------------------------
    def missing_extensions(self, required):
        return sorted(name for name in required if name not in self.caps)

    # ------------------------------------------------------------------------
    def list_scripts(self):
        return ("managesieve", [])

    # ------------------------------------------------------------------------
    def active_script_name(self):
        return "managesieve"

    # ------------------------------------------------------------------------
    def get_script(self, name):
        return ""

    # ------------------------------------------------------------------------
    def check_script(self, content):
        self.calls.append(("check_script",))

    # ------------------------------------------------------------------------
    def put_script(self, name, content):
        self.calls.append(("put_script", name))

    # ------------------------------------------------------------------------
    def set_active(self, name):
        self.calls.append(("set_active", name))

    # ------------------------------------------------------------------------
    def names(self):
        return [call[0] for call in self.calls]


# ----------------------------------------------------------------------------
def live_sessions(sieve: FakeSieveSession, imap=None) -> Sessions:
    """Sessions over the fake, typed as the session it stands in for."""
    return Sessions(cast(SieveSession, sieve), imap)


# ----------------------------------------------------------------------------
def criteria() -> Criteria:
    built = Criteria()
    built.add("From", "noreply@github.com")

    return built


# ############################################################################
# The emit table
# ############################################################################


# ----------------------------------------------------------------------------
def rule_shapes():
    """Every action combination and criteria mode the engine can build.

    Each ``ActionSpec`` field that changes what is emitted is varied over
    all of its effective values, so a new emitted action reached through
    an existing field shows up here without this list being edited.
    """
    specs = itertools.product(
        (None, "Lists"),  # fileinto
        (False, True),  # discard
        ((), ("\\Seen",)),  # flags
        (False, True),  # keep
        (False, True),  # stop
        (False, True),  # use_create
    )

    for fileinto, discard, flags, keep, stop, create in specs:
        spec = ActionSpec(fileinto, discard, flags, keep, stop)
        actions = engine._action_tuples(spec, fileinto or "", create)

        if not actions:
            continue

        actions = engine.sieve_actions(spec, fileinto or "", create)

        for compare, match in itertools.product(COMPARE_OPS, MATCH_MODES):
            built = Criteria(match=match, compare=compare)
            built.add("From", "a@example.com")
            built.add("Subject", "b")

            yield actions, built


# ----------------------------------------------------------------------------
def render(actions, built: Criteria) -> str:
    return merge_rule(
        "",
        "r",
        built.sieve_conditions(),
        actions,
        matchtype=built.sieve_matchtype(),
    )


# ----------------------------------------------------------------------------
def test_the_rule_shapes_cover_every_emitted_action():
    """The matrix below is only as good as its reach; a known positive of
    each action must be in it, or the completeness test reads too little."""
    emitted = {
        action[0] for actions, _built in rule_shapes() for action in actions
    }
    tags = {
        part
        for actions, _built in rule_shapes()
        for action in actions
        for part in action[1:]
        if part.startswith(":")
    }

    assert emitted == {"addflag", "discard", "fileinto", "keep", "stop"}
    assert tags == {":create"}


# ----------------------------------------------------------------------------
def test_the_emit_table_matches_the_require_line_sievelib_writes():
    """sievelib works out ``require`` from what the rule contains; the
    table must agree with it for every shape, or 'test' and the refusals
    are reading the wrong set."""
    for actions, built in rule_shapes():
        script = render(actions, built)
        line = re.search(r"^require \[(.*)\];", script, re.MULTILINE)
        required = (
            set(re.findall(r'"([^"]+)"', line.group(1))) if line else set()
        )

        derived = engine.emitted_extensions(
            actions, built.sieve_conditions(), built.sieve_matchtype()
        )

        assert derived == required, script


# ----------------------------------------------------------------------------
def test_every_word_a_rule_emits_is_in_the_emit_table():
    """Every command, test, and tag in a rendered rule has a table entry.

    Strings and comments are stripped first: only Sieve's own words count.
    """
    for actions, built in rule_shapes():
        script = render(actions, built)
        code = re.sub(r'"(?:[^"\\]|\\.)*"|#[^\n]*', "", script)
        words = set(re.findall(r":?[a-z][a-z0-9]*", code))

        assert words - CONTROL <= set(engine.EMIT_TABLE), script


# ----------------------------------------------------------------------------
def test_the_required_set_is_derived_from_the_emit_table():
    assert engine.REQUIRED_EXTENSIONS == ("fileinto", "imap4flags", "mailbox")


# ----------------------------------------------------------------------------
def test_informational_extensions_are_ones_mailctl_never_emits():
    assert not set(engine.INFORMATIONAL_EXTENSIONS) & set(
        engine.REQUIRED_EXTENSIONS
    )


# ############################################################################
# Unknown names
# ############################################################################


# ----------------------------------------------------------------------------
def test_an_unknown_extension_name_is_refused_by_name(imap_config):
    imap_config.disabled_extensions = frozenset({"mailbx", "copy"})
    imap_config.sources["disabled_extensions"] = FLAG_SOURCE

    with pytest.raises(MailctlError) as caught:
        engine.check_disabled_extensions(imap_config)

    message = str(caught.value)

    assert "'mailbx'" in message
    assert "'copy'" not in message
    assert "flag --disable-extension" in message


# ----------------------------------------------------------------------------
def test_every_reported_name_is_accepted(imap_config):
    imap_config.disabled_extensions = frozenset(engine.KNOWN_EXTENSIONS)

    assert engine.check_disabled_extensions(imap_config) is None


# ----------------------------------------------------------------------------
def test_connecting_refuses_an_unknown_name_before_any_session(imap_config):
    """The check sits in the engine's connect, so every front-end gets it."""
    imap_config.disabled_extensions = frozenset({"nope"})

    with (
        pytest.raises(MailctlError, match="'nope'"),
        engine.connect(imap_config, sieve=False),
    ):
        pass


# ############################################################################
# A disabled extension counts as not advertised
# ############################################################################


# ----------------------------------------------------------------------------
def disable(config, *names):
    config.disabled_extensions = frozenset(names)
    config.sources["disabled_extensions"] = FLAG_SOURCE

    return config


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("spec", "extension"),
    [
        pytest.param(ActionSpec(fileinto="Lists"), "fileinto", id="fileinto"),
        pytest.param(ActionSpec(flags=("\\Seen",)), "imap4flags", id="flags"),
    ],
)
def test_a_rule_needing_a_disabled_extension_is_refused(
    spec, extension, imap_config, imap_session
):
    live = live_sessions(FakeSieveSession(FULL), imap_session)
    disable(imap_config, extension)
    folder = engine.plan_folder(live, imap_config, spec.fileinto)

    with pytest.raises(MailctlError) as caught:
        engine.plan_rule(
            live, imap_config, RuleRequest(criteria(), spec), folder
        )

    message = str(caught.value)

    assert f"'{extension}'" in message
    assert "disabled_extensions" in message
    assert "flag --disable-extension" in message


# ----------------------------------------------------------------------------
def test_a_rule_not_needing_the_disabled_extension_goes_ahead(
    imap_config, imap_session
):
    live = live_sessions(FakeSieveSession(FULL), imap_session)
    disable(imap_config, "imap4flags")
    folder = engine.plan_folder(live, imap_config, "Lists")

    plan = engine.plan_rule(
        live, imap_config, RuleRequest(criteria(), ActionSpec("Lists")), folder
    )

    assert 'fileinto "INBOX.Lists"' in plan.after


# ----------------------------------------------------------------------------
def test_execute_refuses_a_plan_the_setting_now_forbids(
    imap_config, imap_session, tmp_path
):
    """Checked again at execute, so a plan is not carried out under a
    setting it was never checked against."""
    imap_config.backup_dir = tmp_path
    sieve = FakeSieveSession(caps=FULL)
    live = live_sessions(sieve, imap_session)
    request = RuleRequest(criteria(), ActionSpec(flags=("\\Seen",)))
    plan = engine.plan_rule(
        live, imap_config, request, engine.plan_folder(live, imap_config, None)
    )

    disable(imap_config, "imap4flags")

    with pytest.raises(MailctlError, match="'imap4flags'"):
        engine.execute_script_change(live, imap_config, plan)

    assert "put_script" not in sieve.names()


# ----------------------------------------------------------------------------
def test_with_mailbox_disabled_imap_makes_the_folder_and_the_rule_is_plain(
    imap_config, imap_session, fake_imap, tmp_path
):
    """The fallback: no ``:create``, and the folder is made over IMAP."""
    imap_config.backup_dir = tmp_path
    live = live_sessions(FakeSieveSession(FULL), imap_session)
    disable(imap_config, "mailbox")

    folder = engine.plan_folder(live, imap_config, "New", create=True)

    assert folder.status == engine.FOLDER_IMAP_CREATE
    assert folder.mailbox_disabled_by == FLAG_SOURCE

    plan = engine.plan_rule(
        live, imap_config, RuleRequest(criteria(), ActionSpec("New")), folder
    )

    assert 'fileinto "INBOX.New"' in plan.after
    assert ":create" not in plan.after
    assert "mailbox" not in plan.after

    engine.execute_script_change(live, imap_config, plan)

    assert ("create_folder", "INBOX.New") in fake_imap.calls


# ----------------------------------------------------------------------------
def test_with_mailbox_disabled_and_no_imap_a_new_folder_is_refused(
    imap_config,
):
    """As --no-imap already is without 'mailbox', but naming the setting."""
    live = live_sessions(FakeSieveSession(FULL))
    disable(imap_config, "mailbox")

    folder = engine.plan_folder(live, imap_config, "New", create=True)

    assert folder.status == engine.FOLDER_UNCREATABLE

    with pytest.raises(MailctlError) as caught:
        engine.check_folder(folder)

    assert "disabled by mailctl" in str(caught.value)
    assert "flag --disable-extension" in str(caught.value)


# ----------------------------------------------------------------------------
def test_disabling_an_extension_the_server_lacks_changes_nothing(
    imap_config, imap_session
):
    """The no-op: the plan is the one an unset setting would give."""
    live = live_sessions(FakeSieveSession(NO_MAILBOX), imap_session)

    plain = engine.plan_folder(live, imap_config, "New", create=True)
    disable(imap_config, "mailbox")
    disabled = engine.plan_folder(live, imap_config, "New", create=True)

    assert disabled == plain
    assert disabled.mailbox_disabled_by is None

    plan = engine.plan_rule(
        live, imap_config, RuleRequest(criteria(), ActionSpec("New")), disabled
    )

    assert 'fileinto "INBOX.New"' in plan.after


# ############################################################################
# What 'test' reports
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_report_splits_required_from_informational(imap_config):
    probe = engine.SieveProbe(["FileInto", "mailbox", "regex"], None, [])
    disable(imap_config, "mailbox", "imap4flags")

    report = engine.report_extensions(probe, imap_config)

    assert [state.name for state in report.required] == list(
        engine.REQUIRED_EXTENSIONS
    )
    assert [state.name for state in report.informational] == list(
        engine.INFORMATIONAL_EXTENSIONS
    )

    by_name = {state.name: state for state in report.required}

    assert by_name["fileinto"].usable
    assert by_name["mailbox"].advertised
    assert by_name["mailbox"].disabled_by == FLAG_SOURCE
    assert not by_name["mailbox"].usable
    assert not by_name["imap4flags"].advertised
    assert by_name["imap4flags"].disabled_by == FLAG_SOURCE
