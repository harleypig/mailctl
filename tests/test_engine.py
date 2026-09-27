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

from mxfilter import MxFilterError, engine
from mxfilter import sieve as sieve_module
from mxfilter.criteria import Criteria
from mxfilter.engine import ActionSpec, RuleRequest, Sessions
from mxfilter.sieve import PLACE_FIRST, Placement, parse_script, rule_names

FULL = ["fileinto", "imap4flags", "mailbox"]
NO_MAILBOX = ["fileinto", "imap4flags"]

# ############################################################################
# Fakes and helpers
# ############################################################################


class FakeSieveSession:
    """Stands in for ``SieveSession``, recording what it was asked to do."""

    # ------------------------------------------------------------------------
    def __init__(self, script="", active="managesieve", caps=FULL):
        self.script = script
        self.active = active
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
        return (self.active, [])

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
            raise MxFilterError("the server rejected the script")

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
def sessions(fake_sieve, imap_session) -> Sessions:
    return Sessions(sieve=fake_sieve, imap=imap_session)


# ############################################################################
# connect
# ############################################################################


# ----------------------------------------------------------------------------
def test_connect_opens_only_what_was_asked_for_and_tags_progress(
    fake_imap, imap_config, monkeypatch
):
    opened = []
    monkeypatch.setattr(
        sieve_module, "Client", lambda *a, **k: opened.append("sieve")
    )

    seen = []

    with engine.connect(
        imap_config,
        sieve=False,
        imap=True,
        progress=lambda channel, message: seen.append(channel),
    ) as live:
        assert live.sieve is None
        assert live.imap is not None

    assert opened == []
    assert seen and set(seen) == {"imap"}
    assert fake_imap.names()[-1] == "logout"


# ----------------------------------------------------------------------------
def test_an_operation_without_its_session_raises_rather_than_crashing():
    with pytest.raises(MxFilterError, match="no ManageSieve session"):
        engine.list_scripts(Sessions())

    with pytest.raises(MxFilterError, match="no IMAP session"):
        engine.list_folders(Sessions())


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
    broken = engine.ScriptText("broken", "if {{{")

    with pytest.raises(MxFilterError):
        broken.rule_names()


# ----------------------------------------------------------------------------
def test_reading_with_no_active_script_says_so():
    empty = Sessions(sieve=FakeSieveSession(active=None))

    with pytest.raises(MxFilterError, match="name one explicitly"):
        engine.read_script(empty)

    with pytest.raises(MxFilterError, match="no rules to show"):
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
def test_the_imap_probe_reads_capabilities_off_the_server(sessions, fake_imap):
    fake_imap.caps = {"UIDPLUS", "FILTER=SIEVE"}

    probe = engine.probe_imap(sessions)

    assert probe.has_filter_sieve
    assert probe.has_uidplus
    assert not probe.has_move
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
    with pytest.raises(MxFilterError, match="nothing to back up"):
        engine.plan_backup(
            Sessions(sieve=FakeSieveSession(active=None)), imap_config
        )


# ----------------------------------------------------------------------------
def test_count_rules_reports_rather_than_raises_on_a_broken_script(
    roundcube_script,
):
    assert engine.count_rules(roundcube_script) == 2
    assert engine.count_rules("if {{{") is None


# ############################################################################
# Actions
# ############################################################################


# ----------------------------------------------------------------------------
def test_redirect_is_refused_with_the_forwarder_pointer():
    with pytest.raises(MxFilterError, match=r"(?i)forward"):
        engine.reject_actions(["redirect"])


# ----------------------------------------------------------------------------
def test_an_unimplemented_action_is_refused_as_our_choice():
    with pytest.raises(MxFilterError, match="conservative choice of ours"):
        engine.reject_actions(["vacation"])


# ----------------------------------------------------------------------------
def test_nothing_refused_when_nothing_refused_was_asked_for():
    assert engine.reject_actions([]) is None


# ----------------------------------------------------------------------------
def test_required_extensions_follow_the_actions():
    spec = ActionSpec(fileinto="Lists", flags=("\\Seen",))

    assert engine.required_extensions(spec, use_create=True) == {
        "fileinto",
        "imap4flags",
        "mailbox",
    }
    assert engine.required_extensions(ActionSpec(discard=True), False) == set()


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
def test_sieve_creates_the_folder_when_the_server_has_mailbox(
    sessions, imap_config
):
    plan = engine.plan_folder(sessions, imap_config, "New", create=True)

    assert plan.status == engine.FOLDER_SIEVE_CREATES
    assert plan.use_create


# ----------------------------------------------------------------------------
def test_without_mailbox_the_folder_is_planned_for_imap_then_created(
    imap_session, imap_config, fake_imap
):
    live = Sessions(FakeSieveSession(caps=NO_MAILBOX), imap_session)

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

    with pytest.raises(MxFilterError, match="not planned for IMAP creation"):
        engine.create_folder(sessions, plan)


# ----------------------------------------------------------------------------
def test_without_imap_the_delimiter_is_assumed_and_said_so(imap_config):
    live = Sessions(sieve=FakeSieveSession())

    plan = engine.plan_folder(live, imap_config, "Lists/GitHub")

    assert plan.delimiter_assumed
    assert plan.delimiter == "."
    assert plan.folder == "INBOX.Lists.GitHub"


# ----------------------------------------------------------------------------
def test_a_folder_that_cannot_be_created_is_planned_then_refused(
    imap_config,
):
    live = Sessions(sieve=FakeSieveSession(caps=NO_MAILBOX))

    plan = engine.plan_folder(live, imap_config, "New", create=True)

    assert plan.status == engine.FOLDER_UNCREATABLE

    with pytest.raises(MxFilterError, match="cannot be created"):
        engine.check_folder(plan)

    request = RuleRequest(criteria(), ActionSpec(fileinto="New"))

    with pytest.raises(MxFilterError, match="cannot be created"):
        engine.plan_rule(live, request, plan)


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
        sessions, request, folder_for(sessions, imap_config)
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
    live = Sessions(FakeSieveSession(active=None), imap_session)
    request = RuleRequest(criteria(), ActionSpec(fileinto="Lists"))

    plan = engine.plan_rule(live, request, folder_for(live, imap_config))

    assert plan.script == engine.DEFAULT_SCRIPT_NAME
    assert plan.before == ""


# ----------------------------------------------------------------------------
def test_a_rule_that_does_nothing_is_refused(sessions, imap_config):
    request = RuleRequest(criteria(), ActionSpec())

    with pytest.raises(MxFilterError, match="no action requested"):
        engine.plan_rule(
            sessions, request, folder_for(sessions, imap_config, None)
        )


# ----------------------------------------------------------------------------
def test_a_duplicate_name_is_refused_without_replace(sessions, imap_config):
    request = RuleRequest(
        criteria(), ActionSpec(fileinto="Lists"), name="keep-boss"
    )

    with pytest.raises(MxFilterError, match="already exists"):
        engine.plan_rule(sessions, request, folder_for(sessions, imap_config))


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
        sessions, request, folder_for(sessions, imap_config)
    )

    assert "keep-boss" in {
        finding.narrow for finding in plan.placement.starves
    }


# ----------------------------------------------------------------------------
def test_missing_extensions_are_read_from_the_server(imap_config):
    live = Sessions(sieve=FakeSieveSession(caps=["fileinto"]))
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
        sessions, request, folder_for(sessions, imap_config)
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
        sessions, request, folder_for(sessions, imap_config)
    )

    events = []

    with pytest.raises(MxFilterError, match="rejected"):
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
    with pytest.raises(MxFilterError, match="no rule named"):
        engine.plan_removal(sessions, "phantom")

    empty = Sessions(sieve=FakeSieveSession(script=""))

    with pytest.raises(MxFilterError, match="is empty"):
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

    with pytest.raises(MxFilterError, match="NO existing message"):
        engine.check_message_cap(plan, 1)

    with pytest.raises(MxFilterError, match="NO existing message"):
        engine.execute_mail(sessions, plan, max_messages=1)

    assert "move" not in mailbox.names()


# ----------------------------------------------------------------------------
def test_a_mail_pass_that_would_do_nothing_is_refused(sessions, imap_config):
    folder = engine.plan_folder(sessions, imap_config, None)

    with pytest.raises(MxFilterError, match="nothing to do"):
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
    with pytest.raises(MxFilterError, match="matched"):
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

    with pytest.raises(MxFilterError):
        empty.criteria.require_terms()


# ############################################################################
# Folder creation happens on execute
# ############################################################################


# ----------------------------------------------------------------------------
def imap_created_folder_plan(imap_session, imap_config):
    live = Sessions(FakeSieveSession(caps=NO_MAILBOX), imap_session)
    folder = engine.plan_folder(live, imap_config, "New", create=True)
    request = RuleRequest(criteria(), ActionSpec(fileinto="New"))

    return live, engine.plan_rule(live, request, folder)


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

    with pytest.raises(MxFilterError, match="rejected"):
        engine.execute_script_change(live, imap_config, plan)

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
