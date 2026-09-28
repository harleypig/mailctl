"""The engine, driven the way any front-end would drive it.

No argparse and no stdout here: every test builds the engine's plain
inputs, calls it, and asserts on what comes back and on what the fakes
were asked to do. That is the contract a TUI or web front-end would rely
on, so it is tested without the CLI in the way.

The Sieve side is a session-level fake (``FakeSieveSession``); the IMAP
side is the real ``ImapSession`` over the ``FakeIMAPClient`` double from
conftest, so folder normalization and planning run for real.
"""

import email

import pytest

from mailctl import MailctlError, engine
from mailctl.components.managesieve import client as sieve_client
from mailctl.components.managesieve import rule_names
from mailctl.config import Config
from mailctl.criteria import Criteria
from mailctl.engine import (
    PLACE_AFTER,
    PLACE_BEFORE,
    PLACE_FIRST,
    ActionSpec,
    MailActionPlan,
    Placement,
    RuleRequest,
)
from mailctl.providers.mxroute import MxrouteProvider
from mailctl.providers.mxroute.sieve import parse_script

FULL = ["fileinto", "imap4flags", "mailbox"]
NO_MAILBOX = ["fileinto", "imap4flags"]

# ############################################################################
# Fakes and helpers
# ############################################################################


class FakeSieveSession:
    """Stands in for ``SieveSession``, recording what it was asked to do."""

    # ------------------------------------------------------------------------
    def __init__(
        self,
        script="",
        active: str | None = "managesieve",
        caps=FULL,
        others=(),
    ):
        self.script = script
        self.active = active
        self.others = list(others)
        self.caps = list(caps)
        self.reject = False
        self.calls: list[tuple] = []

    # ------------------------------------------------------------------------
    def capabilities(self):
        return list(self.caps)

    # ------------------------------------------------------------------------
    def missing_extensions(self, required):
        return sorted(name for name in required if name not in self.caps)

    # ------------------------------------------------------------------------
    def list_scripts(self):
        return (self.active, list(self.others))

    # ------------------------------------------------------------------------
    def active_script_name(self):
        return self.active

    # ------------------------------------------------------------------------
    def get_script(self, name):
        self.calls.append(("get_script", name))

        return self.script

    # ------------------------------------------------------------------------
    def check_script(self, content):
        self.calls.append(("check_script",))

        if self.reject:
            raise MailctlError("the server rejected the script")

    # ------------------------------------------------------------------------
    def put_script(self, name, content):
        self.calls.append(("put_script", name, content))

    # ------------------------------------------------------------------------
    def set_active(self, name):
        self.calls.append(("set_active", name))

    # ------------------------------------------------------------------------
    def names(self):
        return [call[0] for call in self.calls]


# ----------------------------------------------------------------------------
def criteria(header="From", value="noreply@github.com") -> Criteria:
    built = Criteria()
    built.add(header, value)

    return built


# ----------------------------------------------------------------------------
def headers(**fields) -> email.message.Message:
    text = "".join(
        f"{name.replace('_', '-')}: {value}\r\n"
        for name, value in fields.items()
    )

    return email.message_from_string(text + "\r\n")


# ----------------------------------------------------------------------------
def raw_message(sender: str, subject: str) -> bytes:
    return (
        f"From: Someone <{sender}>\r\nSubject: {subject}\r\n"
        f"Date: Tue, 3 Feb 2026 04:05:06 +0000\r\n\r\n"
    ).encode()


# ----------------------------------------------------------------------------
@pytest.fixture
def fake_sieve(roundcube_script) -> FakeSieveSession:
    return FakeSieveSession(script=roundcube_script)


# ----------------------------------------------------------------------------
@pytest.fixture
def sessions(fake_sieve, imap_session) -> MxrouteProvider:
    return MxrouteProvider(sieve=fake_sieve, imap=imap_session)


# ############################################################################
# connect
# ############################################################################


# ----------------------------------------------------------------------------
def test_connect_opens_only_what_was_asked_for_and_tags_progress(
    fake_imap, imap_config, monkeypatch
):
    opened = []
    monkeypatch.setattr(
        sieve_client, "SieveClient", lambda *a, **k: opened.append("sieve")
    )

    seen = []

    with engine.connect(
        imap_config,
        rules=False,
        mail=True,
        progress=lambda channel, message: seen.append(channel),
    ) as live:
        assert isinstance(live, MxrouteProvider)
        assert live.sieve is None
        assert live.imap is not None

    assert opened == []
    assert seen and set(seen) == {"imap"}
    assert fake_imap.names()[-1] == "logout"


# ----------------------------------------------------------------------------
def test_an_operation_without_its_session_raises_rather_than_crashing():
    with pytest.raises(MailctlError, match="no ManageSieve session"):
        engine.list_scripts(MxrouteProvider())

    with pytest.raises(MailctlError, match="no IMAP session"):
        engine.list_folders(MxrouteProvider())


# ############################################################################
# Reading the account
# ############################################################################


# ----------------------------------------------------------------------------
def test_read_script_takes_the_active_one_and_parses_only_on_demand(
    sessions,
):
    script = engine.read_script(sessions)

    assert script.name == "managesieve"
    assert script.rule_names() == ["keep-boss", "bin-the-noise"]

    # Showing an unparseable script must still be possible.
    broken = engine.ScriptText("broken", "if {{{", MxrouteProvider)

    with pytest.raises(MailctlError):
        broken.rule_names()


# ----------------------------------------------------------------------------
def test_reading_with_no_active_script_says_so():
    empty = MxrouteProvider(sieve=FakeSieveSession(active=None))

    with pytest.raises(MailctlError, match="name one explicitly"):
        engine.read_script(empty)

    with pytest.raises(MailctlError, match="no rules to show"):
        engine.read_rules(empty)


# ----------------------------------------------------------------------------
def test_read_rules_returns_rules_in_order_with_findings(sessions):
    report = engine.read_rules(sessions)

    assert report.script == "managesieve"
    assert [rule.name for rule in report.rules] == [
        "keep-boss",
        "bin-the-noise",
    ]
    assert report.findings == []


# ----------------------------------------------------------------------------
def test_the_mail_probe_reads_capabilities_off_the_server(sessions, fake_imap):
    """What each capability means is the provider's to say, as facts."""
    fake_imap.caps = {"UIDPLUS", "FILTER=SIEVE"}

    probe = engine.probe_mail(sessions)
    facts = {fact.label: fact.text for fact in probe.facts}

    assert list(facts) == ["MOVE", "UIDPLUS", "FILTER=SIEVE"]
    assert facts["FILTER=SIEVE"].startswith("yes -- ")
    assert facts["UIDPLUS"] == "yes"
    assert facts["MOVE"] == "no (COPY+EXPUNGE)"
    assert probe.delimiter == "."


# ############################################################################
# Backups
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_backup_is_planned_then_written_byte_for_byte(
    sessions, imap_config, tmp_path
):
    plan = engine.plan_backup(sessions, imap_config, str(tmp_path / "c.sieve"))

    assert plan.target == tmp_path / "c.sieve"
    assert not plan.target.exists()

    written = engine.execute_backup(plan)

    assert written.read_bytes() == plan.source.encode()


# ----------------------------------------------------------------------------
def test_a_backup_with_no_active_script_is_refused(imap_config):
    with pytest.raises(MailctlError, match="nothing to back up"):
        engine.plan_backup(
            MxrouteProvider(sieve=FakeSieveSession(active=None)), imap_config
        )


# ----------------------------------------------------------------------------
def test_count_rules_reports_rather_than_raises_on_a_broken_script(
    roundcube_script,
):
    assert engine.count_rules(MxrouteProvider, roundcube_script) == 2
    assert engine.count_rules(MxrouteProvider, "if {{{") is None


# ############################################################################
# Actions
# ############################################################################


# ----------------------------------------------------------------------------
def test_redirect_is_refused_with_the_forwarder_pointer():
    with pytest.raises(MailctlError, match=r"(?i)forward"):
        engine.reject_actions(Config(), ["redirect"])


# ----------------------------------------------------------------------------
def test_an_unimplemented_action_is_refused_as_our_choice():
    with pytest.raises(MailctlError, match="conservative choice of ours"):
        engine.reject_actions(Config(), ["vacation"])


# ----------------------------------------------------------------------------
def test_nothing_refused_when_nothing_refused_was_asked_for():
    assert engine.reject_actions(Config(), []) is None


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

    folder = engine.plan_folder(live, imap_config, None)

    assert engine.missing_extensions(live, ActionSpec(), folder) == [
        "fileinto"
    ]


# ----------------------------------------------------------------------------
def test_the_default_rule_name_is_derived_from_the_first_criterion():
    assert engine.default_rule_name(criteria()) == "from-noreply-github-com"


# ############################################################################
# The target folder
# ############################################################################


# ----------------------------------------------------------------------------
def test_no_folder_means_no_folder_plan(sessions, imap_config):
    plan = engine.plan_folder(sessions, imap_config, None)

    assert plan.status == engine.FOLDER_NONE


# ----------------------------------------------------------------------------
def test_the_config_default_folder_is_used_when_none_is_given(
    sessions, imap_config
):
    imap_config.default_folder = "Lists"

    plan = engine.plan_folder(sessions, imap_config, None)

    assert plan.folder == "INBOX.Lists"
    assert plan.status == engine.FOLDER_EXISTS


# ----------------------------------------------------------------------------
def test_a_missing_folder_is_reported_not_created(
    sessions, imap_config, fake_imap
):
    plan = engine.plan_folder(sessions, imap_config, "Lists/GitHub")

    assert plan.folder == "INBOX.Lists.GitHub"
    assert plan.status == engine.FOLDER_MISSING
    assert "create_folder" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_with_mailbox_and_imap_the_folder_is_made_both_ways(
    sessions, imap_config, fake_imap
):
    """Sieve's :create stays as the fallback; IMAP makes it visible now."""
    plan = engine.plan_folder(sessions, imap_config, "New", create=True)

    assert plan.status == engine.FOLDER_BOTH_CREATE
    assert plan.use_create
    assert engine.folder_pending(sessions, plan)
    assert "create_folder" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_without_imap_only_sieve_creates_the_folder(fake_sieve, imap_config):
    """--no-imap: nothing can create or subscribe it now (#40)."""
    live = MxrouteProvider(sieve=fake_sieve, imap=None)

    plan = engine.plan_folder(live, imap_config, "New", create=True)

    assert plan.status == engine.FOLDER_SIEVE_CREATES
    assert plan.use_create
    assert not engine.folder_pending(live, plan)


# ----------------------------------------------------------------------------
def test_without_mailbox_the_folder_is_planned_for_imap_then_created(
    imap_session, imap_config, fake_imap
):
    live = MxrouteProvider(FakeSieveSession(caps=NO_MAILBOX), imap_session)

    plan = engine.plan_folder(
        live, imap_config, "New", create=True, subscribe=False
    )

    assert plan.status == engine.FOLDER_IMAP_CREATE
    assert "create_folder" not in fake_imap.names()

    result = engine.create_folder(live, plan)

    assert result.folder == "INBOX.New"
    assert not result.subscribed
    assert ("create_folder", "INBOX.New") in fake_imap.calls
    assert "subscribe_folder" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_creating_a_folder_that_was_not_planned_for_it_is_refused(
    sessions, imap_config
):
    plan = engine.plan_folder(sessions, imap_config, "Lists")

    with pytest.raises(MailctlError, match="not planned for IMAP creation"):
        engine.create_folder(sessions, plan)


# ----------------------------------------------------------------------------
def test_without_imap_the_delimiter_is_assumed_and_said_so(imap_config):
    live = MxrouteProvider(sieve=FakeSieveSession())

    plan = engine.plan_folder(live, imap_config, "Lists/GitHub")

    assert plan.delimiter_assumed
    assert plan.delimiter == "."
    assert plan.folder == "INBOX.Lists.GitHub"


# ----------------------------------------------------------------------------
def test_a_folder_that_cannot_be_created_is_planned_then_refused(
    imap_config,
):
    live = MxrouteProvider(sieve=FakeSieveSession(caps=NO_MAILBOX))

    plan = engine.plan_folder(live, imap_config, "New", create=True)

    assert plan.status == engine.FOLDER_UNCREATABLE

    with pytest.raises(MailctlError, match="cannot be created"):
        engine.check_folder(plan)

    request = RuleRequest(criteria(), ActionSpec(fileinto="New"))

    with pytest.raises(MailctlError, match="cannot be created"):
        engine.plan_rule(live, imap_config, request, plan)


# ############################################################################
# Adding and removing rules
# ############################################################################


# ----------------------------------------------------------------------------
def folder_for(sessions, config, name="Lists"):
    return engine.plan_folder(sessions, config, name)


# ----------------------------------------------------------------------------
def test_plan_rule_merges_without_touching_the_server(
    sessions, imap_config, fake_sieve, reparse
):
    request = RuleRequest(criteria(), ActionSpec(fileinto="Lists"))

    plan = engine.plan_rule(
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

    plan = engine.plan_rule(
        live, imap_config, request, folder_for(live, imap_config)
    )

    assert plan.script == engine.DEFAULT_SCRIPT_NAME
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

    plan = engine.plan_rule(
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

    plan = engine.plan_rule(
        live, imap_config, request, folder_for(live, imap_config)
    )

    assert plan.script == "mxfilter"


# ----------------------------------------------------------------------------
def test_a_rule_that_does_nothing_is_refused(sessions, imap_config):
    request = RuleRequest(criteria(), ActionSpec())

    with pytest.raises(MailctlError, match="no action requested"):
        engine.plan_rule(
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
        engine.plan_rule(
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

    plan = engine.plan_rule(
        sessions, imap_config, request, folder_for(sessions, imap_config)
    )

    assert "keep-boss" in {
        finding.narrow for finding in plan.placement.starves
    }


# ----------------------------------------------------------------------------
def test_missing_extensions_are_read_from_the_server(imap_config):
    live = MxrouteProvider(sieve=FakeSieveSession(caps=["fileinto"]))
    spec = ActionSpec(fileinto="Lists", flags=("\\Seen",))

    folder = engine.plan_folder(live, imap_config, "Lists")

    assert engine.missing_extensions(live, spec, folder) == ["imap4flags"]


# ----------------------------------------------------------------------------
def test_an_upload_backs_up_first_and_reports_each_step(
    sessions, imap_config, fake_sieve, tmp_path
):
    imap_config.backup_dir = tmp_path / "backups"
    request = RuleRequest(criteria(), ActionSpec(fileinto="Lists"))
    plan = engine.plan_rule(
        sessions, imap_config, request, folder_for(sessions, imap_config)
    )

    events = []

    path = engine.execute_script_change(
        sessions, imap_config, plan, events.append
    )

    assert path.read_bytes() == plan.before.encode()
    assert [type(event) for event in events] == [
        engine.ScriptBackedUp,
        engine.ScriptUploaded,
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
    plan = engine.plan_rule(
        sessions, imap_config, request, folder_for(sessions, imap_config)
    )

    events = []

    with pytest.raises(MailctlError, match="rejected"):
        engine.execute_script_change(
            sessions, imap_config, plan, events.append
        )

    assert [type(event) for event in events] == [engine.ScriptBackedUp]
    assert events[0].path.exists()
    assert "put_script" not in fake_sieve.names()


# ----------------------------------------------------------------------------
def test_a_removal_is_planned_without_touching_the_server(
    sessions, fake_sieve
):
    plan = engine.plan_removal(sessions, "keep-boss")

    assert rule_names(parse_script(plan.after)) == ["bin-the-noise"]
    assert "put_script" not in fake_sieve.names()


# ----------------------------------------------------------------------------
def test_removing_from_an_empty_script_or_an_unknown_rule_is_refused(
    sessions,
):
    with pytest.raises(MailctlError, match="no rule named"):
        engine.plan_removal(sessions, "phantom")

    empty = MxrouteProvider(sieve=FakeSieveSession(script=""))

    with pytest.raises(MailctlError, match="is empty"):
        engine.plan_removal(empty, "keep-boss")


# ############################################################################
# The existing-mail pass
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.fixture
def mailbox(fake_imap):
    fake_imap.messages = {
        1: raw_message("noreply@github.com", "PR opened"),
        2: raw_message("noreply@github.com", "Issue closed"),
        3: raw_message("friend@example.com", "Lunch"),
    }

    return fake_imap


# ----------------------------------------------------------------------------
def test_planning_the_mail_pass_is_read_only(sessions, mailbox):
    plan = engine.plan_mail(
        sessions, criteria(), ActionSpec(), "INBOX", "INBOX.Lists"
    )

    assert plan.uids == [1, 2]
    assert plan.moves
    assert ("select_folder", "INBOX", True) in mailbox.calls
    assert "move" not in mailbox.names()


# ----------------------------------------------------------------------------
def test_an_approved_plan_is_executed(sessions, mailbox):
    plan = engine.plan_mail(
        sessions, criteria(), ActionSpec(), "INBOX", "INBOX.Lists"
    )

    result = engine.execute_mail(sessions, plan, max_messages=2)

    assert result.moved == 2
    assert ("move", (1, 2), "INBOX.Lists") in mailbox.calls


# ----------------------------------------------------------------------------
def test_a_plan_over_the_cap_is_refused_whole(sessions, mailbox):
    plan = engine.plan_mail(
        sessions, criteria(), ActionSpec(), "INBOX", "INBOX.Lists"
    )

    with pytest.raises(MailctlError, match="NO existing message"):
        engine.check_message_cap(plan, 1)

    with pytest.raises(MailctlError, match="NO existing message"):
        engine.execute_mail(sessions, plan, max_messages=1)

    assert "move" not in mailbox.names()


# ----------------------------------------------------------------------------
def test_a_mail_pass_that_would_do_nothing_is_refused(sessions, imap_config):
    folder = engine.plan_folder(sessions, imap_config, None)

    with pytest.raises(MailctlError, match="nothing to do"):
        engine.require_mail_action(folder, ActionSpec())

    engine.require_mail_action(folder, ActionSpec(discard=True))


# ############################################################################
# Deriving a rule from a message
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_search_picks_the_newest_match_and_says_how_many(sessions, mailbox):
    picked = engine.pick_message(sessions, "INBOX", search="ALL")

    assert picked.uid == 3
    assert picked.candidates == 3
    assert picked.headers["Subject"] == "Lunch"


# ----------------------------------------------------------------------------
def test_a_search_with_no_match_is_refused(sessions, fake_imap):
    with pytest.raises(MailctlError, match="matched"):
        engine.pick_message(sessions, "INBOX", search="FROM nobody")


# ----------------------------------------------------------------------------
def test_auto_derivation_prefers_list_id_over_from():
    listed = headers(From="A <a@x.org>", List_Id="Dev <dev.x.org>")
    plain = headers(From="A <a@x.org>")

    assert engine.derive_criteria(listed).criteria.terms[0].value == (
        "dev.x.org"
    )
    assert engine.derive_criteria(plain).criteria.terms[0].value == "a@x.org"


# ----------------------------------------------------------------------------
def test_a_missing_header_is_reported_as_skipped_not_raised():
    derived = engine.derive_criteria(headers(From="a@x.org"), "cc,from")

    assert derived.skipped == ["cc"]
    assert [term.value for term in derived.criteria.terms] == ["a@x.org"]

    empty = engine.derive_criteria(headers(From="a@x.org"), "cc")

    with pytest.raises(MailctlError):
        empty.criteria.require_terms()


# ############################################################################
# Folder creation happens on execute
# ############################################################################


# ----------------------------------------------------------------------------
def imap_created_folder_plan(imap_session, imap_config):
    live = MxrouteProvider(FakeSieveSession(caps=NO_MAILBOX), imap_session)
    folder = engine.plan_folder(live, imap_config, "New", create=True)
    request = RuleRequest(criteria(), ActionSpec(fileinto="New"))

    return live, engine.plan_rule(live, imap_config, request, folder)


# ----------------------------------------------------------------------------
def test_a_rule_plan_creates_its_folder_only_on_execute(
    imap_session, imap_config, fake_imap, tmp_path
):
    imap_config.backup_dir = tmp_path
    live, plan = imap_created_folder_plan(imap_session, imap_config)

    assert "create_folder" not in fake_imap.names()

    events = []
    engine.execute_script_change(live, imap_config, plan, events.append)

    assert ("create_folder", "INBOX.New") in fake_imap.calls
    assert [type(event) for event in events] == [
        engine.ScriptBackedUp,
        engine.FolderCreated,
        engine.ScriptUploaded,
    ]


# ----------------------------------------------------------------------------
def test_a_rejected_script_leaves_no_folder_behind(
    imap_session, imap_config, fake_imap, tmp_path
):
    imap_config.backup_dir = tmp_path
    live, plan = imap_created_folder_plan(imap_session, imap_config)
    live.sieve.reject = True

    with pytest.raises(MailctlError, match="rejected"):
        engine.execute_script_change(live, imap_config, plan)

    assert "create_folder" not in fake_imap.names()


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("subscribe", "expected"), [(True, True), (False, False)]
)
def test_a_sieve_create_rule_also_creates_and_subscribes_on_execute(
    sessions, imap_config, fake_imap, tmp_path, subscribe, expected
):
    """The :create path is the default one here, so it has to subscribe
    too, not only the IMAP-creation path (#40)."""
    imap_config.backup_dir = tmp_path
    folder = engine.plan_folder(
        sessions, imap_config, "New", create=True, subscribe=subscribe
    )
    plan = engine.plan_rule(
        sessions,
        imap_config,
        RuleRequest(criteria(), ActionSpec(fileinto="New")),
        folder,
    )

    assert 'fileinto :create "INBOX.New"' in plan.after
    assert "create_folder" not in fake_imap.names()

    events = []
    engine.execute_script_change(sessions, imap_config, plan, events.append)

    assert ("create_folder", "INBOX.New") in fake_imap.calls
    assert (("subscribe_folder", "INBOX.New") in fake_imap.calls) is expected
    assert [type(event) for event in events] == [
        engine.ScriptBackedUp,
        engine.FolderCreated,
        engine.ScriptUploaded,
    ]


# ----------------------------------------------------------------------------
def test_a_rejected_sieve_create_rule_leaves_no_folder_behind(
    sessions, imap_config, fake_imap, tmp_path
):
    imap_config.backup_dir = tmp_path
    folder = engine.plan_folder(sessions, imap_config, "New", create=True)
    plan = engine.plan_rule(
        sessions,
        imap_config,
        RuleRequest(criteria(), ActionSpec(fileinto="New")),
        folder,
    )
    sessions.sieve.reject = True

    with pytest.raises(MailctlError, match="rejected"):
        engine.execute_script_change(sessions, imap_config, plan)

    assert "create_folder" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_the_mail_pass_creates_its_folder_once_and_only_on_execute(
    sessions, imap_config, mailbox
):
    sessions.sieve = None
    folder = engine.plan_folder(sessions, imap_config, "New", create=True)
    plan = engine.plan_mail(
        sessions, criteria(), ActionSpec(), "INBOX", "INBOX.New"
    )

    assert engine.folder_pending(sessions, folder)
    assert "create_folder" not in mailbox.names()

    engine.execute_mail(sessions, plan, 10, folder)
    engine.realize_folder(sessions, folder)

    assert mailbox.names().count("create_folder") == 1
    assert not engine.folder_pending(sessions, folder)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("spec", "destination", "noop"),
    [
        (ActionSpec(keep=True), "", True),
        (ActionSpec(fileinto="INBOX"), "inbox", True),
        (ActionSpec(fileinto="Lists"), "INBOX.Lists", False),
        (ActionSpec(keep=True, flags=("\\Seen",)), "", False),
        (ActionSpec(discard=True), "", False),
    ],
)
def test_a_mail_pass_that_changes_nothing_is_recognised(
    spec, destination, noop
):
    assert engine.mail_pass_is_noop(spec, "INBOX", destination) is noop


# ############################################################################
# Subscription
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_folder_listing_says_which_folders_webmail_shows(sessions):
    listing = engine.list_folders(sessions)

    assert listing.unsubscribed == ["INBOX.spam"]
    assert listing.is_subscribed("INBOX.Lists")
    assert not listing.is_subscribed("INBOX.lists")  # #56: exact
    assert engine.probe_mail(sessions).unsubscribed == ["INBOX.spam"]


# ----------------------------------------------------------------------------
def test_subscribing_is_planned_then_executed(sessions, fake_imap):
    plan = engine.plan_subscription(sessions, "spam", subscribe=True)

    assert plan.folder == "INBOX.spam"
    assert plan.changes
    assert "subscribe_folder" not in fake_imap.names()

    engine.execute_subscription(sessions, plan)

    assert ("subscribe_folder", "INBOX.spam") in fake_imap.calls
    assert engine.list_folders(sessions).unsubscribed == []


# ----------------------------------------------------------------------------
def test_unsubscribing_hides_without_deleting(sessions, fake_imap):
    plan = engine.plan_subscription(sessions, "Lists", subscribe=False)

    engine.execute_subscription(sessions, plan)

    listing = engine.list_folders(sessions)

    assert "INBOX.Lists" in listing.folders
    assert "INBOX.Lists" in listing.unsubscribed


# ----------------------------------------------------------------------------
def test_a_plan_that_changes_nothing_does_nothing(sessions, fake_imap):
    plan = engine.plan_subscription(sessions, "Lists", subscribe=True)

    assert not plan.changes

    engine.execute_subscription(sessions, plan)

    assert "subscribe_folder" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_a_missing_folder_cannot_be_subscribed(sessions):
    with pytest.raises(MailctlError, match="nothing to subscribe to"):
        engine.plan_subscription(sessions, "Nowhere", subscribe=True)

    with pytest.raises(MailctlError, match="no folder or subscription"):
        engine.plan_subscription(sessions, "Nowhere", subscribe=False)


# ----------------------------------------------------------------------------
def test_a_stale_subscription_to_a_gone_folder_can_be_removed(
    sessions, fake_imap
):
    fake_imap.subscriptions.append(((), b".", b"INBOX.Gone"))
    sessions.imap._read_folders()

    plan = engine.plan_subscription(sessions, "Gone", subscribe=False)

    assert plan.changes


# ----------------------------------------------------------------------------
def test_a_subscribe_the_server_ignores_is_an_error(sessions, fake_imap):
    fake_imap.subscribe_takes_effect = False
    plan = engine.plan_subscription(sessions, "spam", subscribe=True)

    with pytest.raises(MailctlError, match="still does not list it"):
        engine.execute_subscription(sessions, plan)


# ############################################################################
# Restore
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_restore_is_planned_then_backs_up_and_uploads_exact_bytes(
    imap_config, tmp_path
):
    imap_config.backup_dir = tmp_path / "backups"
    backup = tmp_path / "old.sieve"
    backup.write_bytes(b'require "fileinto";\r\n# rule:[a]\r\n')
    fake = FakeSieveSession(script="current\n")
    live = MxrouteProvider(sieve=fake)

    plan = engine.plan_restore(live, engine.read_backup_file(backup))

    assert plan.changes
    assert plan.after == 'require "fileinto";\r\n# rule:[a]\r\n'
    assert "-current" in plan.diff.text
    assert fake.names() == ["get_script"]

    events = []
    path = engine.execute_restore(live, imap_config, plan, events.append)

    assert path.read_text() == "current\n"
    assert fake.calls[-2] == ("put_script", "managesieve", plan.after)
    assert [type(event) for event in events] == [
        engine.ScriptBackedUp,
        engine.ScriptUploaded,
    ]


# ----------------------------------------------------------------------------
def test_a_restore_over_an_unparseable_script_is_allowed(
    imap_config, tmp_path, roundcube_script
):
    """ADR 0005: the backup, not a refusal, is what protects it."""
    imap_config.backup_dir = tmp_path / "backups"
    backup = tmp_path / "good.sieve"
    backup.write_text(roundcube_script)
    live = MxrouteProvider(sieve=FakeSieveSession(script="if {{{ broken"))

    engine.execute_restore(
        live,
        imap_config,
        engine.plan_restore(live, engine.read_backup_file(backup)),
    )

    assert "put_script" in live.sieve.names()


# ----------------------------------------------------------------------------
def test_restoring_an_identical_file_sends_nothing(imap_config, tmp_path):
    backup = tmp_path / "same.sieve"
    backup.write_text("same\n")
    live = MxrouteProvider(sieve=FakeSieveSession(script="same\n"))

    plan = engine.plan_restore(live, engine.read_backup_file(backup))

    assert not plan.changes
    assert engine.execute_restore(live, imap_config, plan) is None
    assert "put_script" not in live.sieve.names()


# ----------------------------------------------------------------------------
def test_a_restore_needs_a_readable_file(tmp_path):
    with pytest.raises(MailctlError, match="could not read backup"):
        engine.read_backup_file(tmp_path / "no")


# ----------------------------------------------------------------------------
def test_with_nothing_active_a_restore_asks_for_script(tmp_path):
    backup = tmp_path / "b.sieve"
    backup.write_text("x")
    live = MxrouteProvider(sieve=FakeSieveSession(active=None))

    with pytest.raises(MailctlError, match=r"no active script.*--script"):
        engine.plan_restore(live, engine.read_backup_file(backup))


# ----------------------------------------------------------------------------
def test_with_nothing_active_a_named_restore_uploads_and_activates(
    imap_config, tmp_path
):
    """The recovery case (#54): the account's script was deactivated."""
    imap_config.backup_dir = tmp_path / "backups"
    backup = tmp_path / "b.sieve"
    backup.write_text("new\n")
    fake = FakeSieveSession(script="old\n", active=None, others=["spare"])
    live = MxrouteProvider(sieve=fake)

    plan = engine.plan_restore(
        live, engine.read_backup_file(backup), script="spare"
    )

    assert (plan.active, plan.activate) == (None, True)

    engine.execute_restore(live, imap_config, plan)

    assert fake.calls[-2:] == [
        ("put_script", "spare", "new\n"),
        ("set_active", "spare"),
    ]


# ----------------------------------------------------------------------------
def test_a_backup_path_expands_home_and_variables(monkeypatch, tmp_path):
    (tmp_path / "b.sieve").write_text("x")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAILCTL_TEST_DIR", str(tmp_path))

    for spelled in ("~/b.sieve", "$MAILCTL_TEST_DIR/b.sieve"):
        assert engine.read_backup_file(spelled).path == tmp_path / "b.sieve"

    assert engine.read_backup_file("${MAILCTL_TEST_DIR}/b.sieve").text == "x"


# ----------------------------------------------------------------------------
def test_a_restore_targets_the_named_script_and_leaves_it_inactive(
    imap_config, tmp_path
):
    imap_config.backup_dir = tmp_path / "backups"
    backup = tmp_path / "b.sieve"
    backup.write_text("new\n")
    fake = FakeSieveSession(script="old\n", others=["spare"])
    live = MxrouteProvider(sieve=fake)

    plan = engine.plan_restore(
        live, engine.read_backup_file(backup), script="spare"
    )

    assert (plan.script, plan.activate) == ("spare", False)
    assert fake.calls == [("get_script", "spare")]

    engine.execute_restore(live, imap_config, plan)

    assert fake.calls[-1] == ("put_script", "spare", "new\n")
    assert "set_active" not in fake.names()


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("content", ["", "\n", "  \r\n\t"])
def test_an_empty_backup_is_refused_unless_allowed(content, tmp_path):
    backup = tmp_path / "empty.sieve"
    backup.write_bytes(content.encode())
    live = MxrouteProvider(sieve=FakeSieveSession(script="old\n"))

    with pytest.raises(MailctlError, match="--allow-empty"):
        engine.read_backup_file(backup)

    plan = engine.plan_restore(
        live, engine.read_backup_file(backup, allow_empty=True)
    )

    assert plan.after == content
    assert plan.changes


# ----------------------------------------------------------------------------
def test_a_rejected_restore_leaves_the_backup_and_stores_nothing(
    imap_config, tmp_path
):
    imap_config.backup_dir = tmp_path / "backups"
    backup = tmp_path / "b.sieve"
    backup.write_text("new\n")
    fake = FakeSieveSession(script="old\n")
    fake.reject = True
    live = MxrouteProvider(sieve=fake)

    with pytest.raises(MailctlError, match="rejected"):
        engine.execute_restore(
            live,
            imap_config,
            engine.plan_restore(live, engine.read_backup_file(backup)),
        )

    assert "put_script" not in fake.names()
    assert len(list((tmp_path / "backups").iterdir())) == 1


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
    assert engine.activates(name, active, requested) is expected


# ----------------------------------------------------------------------------
def test_editing_another_script_leaves_the_active_one_running(
    imap_config, tmp_path, roundcube_script
):
    imap_config.backup_dir = tmp_path
    fake = FakeSieveSession(script=roundcube_script, others=["spare"])
    live = MxrouteProvider(sieve=fake)

    plan = engine.plan_removal(live, "keep-boss", script="spare")

    assert (plan.script, plan.active, plan.activate) == (
        "spare",
        "managesieve",
        False,
    )

    events = []
    engine.execute_script_change(live, imap_config, plan, events.append)

    assert "set_active" not in fake.names()
    assert fake.calls[-1][:2] == ("put_script", "spare")
    assert events[-1] == engine.ScriptUploaded("spare", activated=False)


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
        plan = engine.plan_rule(
            live, imap_config, request, folder_for(live, imap_config)
        )

    elif kind == "removal":
        plan = engine.plan_removal(live, "keep-boss", "spare", activate=True)

    else:
        plan = engine.plan_move(
            live, "bin-the-noise", Placement(PLACE_FIRST), "spare", True
        )

    events = []
    engine.execute_script_change(live, imap_config, plan, events.append)

    assert fake.calls[-1] == ("set_active", "spare")
    assert events[-1] == engine.ScriptUploaded("spare", activated=True)


# ----------------------------------------------------------------------------
def test_with_no_active_script_the_edited_one_is_activated(
    sessions, imap_config, tmp_path
):
    imap_config.backup_dir = tmp_path
    sessions.sieve.active = None
    request = RuleRequest(criteria(), ActionSpec(fileinto="Lists"))

    plan = engine.plan_rule(
        sessions, imap_config, request, folder_for(sessions, imap_config)
    )

    assert plan.activate
    engine.execute_script_change(sessions, imap_config, plan)
    assert sessions.sieve.calls[-1] == ("set_active", plan.script)


# ############################################################################
# Moving a rule
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_move_changes_the_order_and_nothing_else(
    sessions, fake_sieve, reparse
):
    plan = engine.plan_move(sessions, "bin-the-noise", Placement(PLACE_FIRST))

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

    plan = engine.plan_move(live, "all-lists", Placement(PLACE_FIRST))

    assert {f.narrow for f in plan.placement.starves} == {"announce"}
    assert plan.placement.dead_on_arrival == []


# ----------------------------------------------------------------------------
def test_a_move_to_where_the_rule_already_is_changes_nothing(sessions):
    plan = engine.plan_move(sessions, "keep-boss", Placement(PLACE_FIRST))

    assert not plan.changes


# ----------------------------------------------------------------------------
def test_moving_an_unknown_rule_or_against_an_unknown_anchor_is_refused(
    sessions,
):
    with pytest.raises(MailctlError, match="no rule named 'phantom'"):
        engine.plan_move(sessions, "phantom", Placement(PLACE_FIRST))

    with pytest.raises(MailctlError, match="Known rules"):
        engine.plan_move(
            sessions, "keep-boss", Placement(PLACE_AFTER, "phantom")
        )

    with pytest.raises(MailctlError, match="has no position"):
        engine.plan_move(
            sessions, "keep-boss", Placement(PLACE_BEFORE, "keep-boss")
        )


# ----------------------------------------------------------------------------
def test_an_executed_move_is_backed_up_and_uploaded(
    sessions, imap_config, fake_sieve, tmp_path
):
    imap_config.backup_dir = tmp_path
    plan = engine.plan_move(sessions, "bin-the-noise", Placement(PLACE_FIRST))

    engine.execute_script_change(sessions, imap_config, plan)

    assert fake_sieve.names()[-3:] == [
        "check_script",
        "put_script",
        "set_active",
    ]
    assert len(list(tmp_path.iterdir())) == 1


# ############################################################################
# Folder names are case-sensitive, except INBOX (RFC 3501 section 5.1)
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("source", "destination", "noop"),
    [
        ("INBOX.Foo", "INBOX.foo", False),
        ("INBOX.foo", "INBOX.foo", True),
        ("inbox", "INBOX", True),
        ("Inbox", "INBOX", True),
    ],
)
def test_only_inbox_compares_case_insensitively(source, destination, noop):
    """On a case-sensitive server INBOX.Foo and INBOX.foo are two folders.

    Treating them as one skipped a real move as a no-op.
    """
    spec = ActionSpec(fileinto=destination)

    assert engine.mail_pass_is_noop(spec, source, destination) is noop
    assert MailActionPlan(source, destination, [], False).moves is not noop


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("create", [False, True])
def test_a_case_variant_is_reported_not_taken_for_the_folder(
    sessions, imap_config, create
):
    """#56: 'lists' is not INBOX.Lists, and planning must say that one
    exists rather than silently treat the name as absent -- above all
    when --create-folder is about to make a second, differently cased
    folder beside it."""
    plan = engine.plan_folder(sessions, imap_config, "lists", create=create)

    assert plan.folder == "INBOX.lists"
    assert plan.status != engine.FOLDER_EXISTS
    assert plan.case_variants == ("INBOX.Lists",)


# ----------------------------------------------------------------------------
def test_an_exact_folder_has_no_case_variants(sessions, imap_config):
    plan = engine.plan_folder(sessions, imap_config, "Lists")

    assert plan.status == engine.FOLDER_EXISTS
    assert plan.case_variants == ()


# ----------------------------------------------------------------------------
def test_subscribing_a_case_variant_names_the_real_folder(sessions):
    with pytest.raises(MailctlError, match=r"'INBOX\.Lists' exists"):
        engine.plan_subscription(sessions, "lists", subscribe=True)


# ----------------------------------------------------------------------------
def test_the_source_folder_is_normalized_like_the_destination(
    sessions, fake_imap
):
    """--folder Lists and --fileinto Lists name the same folder."""
    fake_imap.listing.append(((), b".", b"INBOX.Lists.X"))
    sessions.imap._read_folders()

    source = engine.source_folder(sessions, "Lists/X")

    assert source == "INBOX.Lists.X"
    assert engine.mail_pass_is_noop(
        ActionSpec(fileinto="Lists/X"), source, "INBOX.Lists.X"
    )
