"""The provider interface, the registry, and a second provider (ADR 0006).

Three things are held here.

* **Every registered provider answers every operation.** Each one either
  implements an operation of ``Provider`` or explicitly declines it, and
  what it declines is exactly what its capabilities say it declines. This
  is the exhaustiveness an in-tree ABC gives in place of a compiler.
* **The ``provider`` setting** climbs the same ladder as every other
  setting, with provenance, and an unknown name is refused naming the
  known ones -- before any connection.
* **Adding a provider needs no engine change.** A fake second provider,
  registered for the test, is driven through the engine's representative
  operations; the calls the engine makes on it are recorded and compared
  with the calls it makes on ``mxroute``. They are the same calls in the
  same shape, because the engine reads capabilities and never asks which
  provider it has. A capability the fake declines is refused at
  validation, before anything that would be network work.
"""

import argparse
import difflib
import email
import inspect
import json
from contextlib import contextmanager
from typing import cast

import pytest

from mailctl import MailctlError, cli, engine
from mailctl.components.managesieve import SieveSession
from mailctl.config import (
    CONFIG_FILE,
    DEFAULT,
    ENV_FILE,
    ENVIRONMENT,
    FLAG,
    Config,
    Source,
    config_path,
    load_config,
)
from mailctl.criteria import Criteria
from mailctl.engine import ActionSpec, RuleRequest
from mailctl.providers import model, registry
from mailctl.providers.base import (
    DISCARD,
    FILEINTO,
    KEEP,
    OPERATIONS,
    DeliveryCreate,
    DisplayDiff,
    FolderCreation,
    FolderListing,
    MailActionPlan,
    MailActionResult,
    MessageSummary,
    Placement,
    Provider,
    ProviderCapabilities,
    Specific,
    declined,
)
from mailctl.providers.base import (
    FLAG as FLAG_ACTION,
)
from mailctl.providers.mxroute import MxrouteProvider
from mailctl.rules import rule_from_criteria

# ############################################################################
# A second provider, for the test only
# ############################################################################

GITHUB = "noreply@github.com"

FULL = ProviderCapabilities(
    ordering=True,
    stop=True,
    rule_sets=True,
    actions=frozenset((FILEINTO, DISCARD, FLAG_ACTION, KEEP)),
    extensions=True,
    specifics={"fake.label": Specific(str, "a label to add")},
)


class FakeProvider(Provider):
    """A host with nothing in common with MXroute but the interface.

    Rule sets are JSON, not Sieve; the mailbox is a dict. Every call the
    engine makes can be answered offline, and ``opened`` counts
    connections, which is what "before any network work" is checked on.
    """

    name = "fake"
    capabilities = FULL
    opened = 0

    # ------------------------------------------------------------------------
    def __init__(self, rules=True, mail=True):
        self.rules_on = rules
        self.mail_on = mail
        self.scripts = {"main": json.dumps([_stored("keep-boss", "boss")])}
        self.active = "main"
        self.folders = ["INBOX", "INBOX.Lists"]
        self.subscribed = list(self.folders)
        self.messages = {
            7: {"FROM": [GITHUB], "SUBJECT": ["hello"]},
            8: {"FROM": ["someone@example.com"], "SUBJECT": ["hi"]},
        }

    # ------------------------------------------------------------------------
    @classmethod
    def validate(cls, config):
        pass

    @classmethod
    def refuse_actions(cls, requested):
        if "redirect" in set(requested):
            raise MailctlError("fake does not redirect")

    @classmethod
    def translate_actions(cls, spec, folder, use_create):
        actions = [f"flag:{flag}" for flag in spec.flags]
        actions += ["discard"] if spec.discard else []
        actions += [f"file:{folder}"] if folder and not spec.discard else []
        actions += ["keep"] if spec.keep else []

        if not actions:
            raise MailctlError("no action requested")

        return actions + (["stop"] if spec.stop else [])

    @classmethod
    def required_features(cls, spec, folder, use_create):
        return set()

    @classmethod
    def check_actions(cls, config, actions):
        pass

    @classmethod
    def candidate_rule(cls, name, criteria, actions):
        return rule_from_criteria(
            name, criteria, tuple(actions), stops="stop" in actions
        )

    @classmethod
    @contextmanager
    def open(cls, config, *, rules, mail, progress=None):
        cls.opened += 1

        yield cls(rules=rules, mail=mail)

    # -- rule sets, offline --------------------------------------------------

    @classmethod
    def rule_names(cls, source):
        return [entry["name"] for entry in json.loads(source or "[]")]

    @classmethod
    def read_rules(cls, source):
        rules = []

        for index, entry in enumerate(json.loads(source or "[]")):
            criteria = Criteria()
            criteria.add("From", entry["from"])
            rule = rule_from_criteria(
                entry["name"],
                criteria,
                tuple(entry["actions"]),
                stops="stop" in entry["actions"],
                index=index,
            )
            rules.append(rule)

        return rules

    @classmethod
    def add_rule(cls, source, name, criteria, actions, *, replace, placement):
        entries = [e for e in json.loads(source or "[]") if e["name"] != name]
        entry = {
            "name": name,
            "from": criteria.terms[0].value,
            "actions": actions,
        }
        at = cls.position([e["name"] for e in entries], placement, name)

        return json.dumps([*entries[:at], entry, *entries[at:]])

    @classmethod
    def remove_rule(cls, source, name):
        entries = json.loads(source)

        return json.dumps([e for e in entries if e["name"] != name])

    @classmethod
    def move_rule(cls, source, name, placement):
        entries = json.loads(source)
        moving = next(e for e in entries if e["name"] == name)
        others = [e for e in entries if e["name"] != name]
        at = cls.position([e["name"] for e in others], placement, name)

        return json.dumps([*others[:at], moving, *others[at:]])

    @classmethod
    def position(cls, names, placement, name):
        others = [other for other in names if other != name]

        if placement is None or placement.where == "last":
            return names.index(name) if name in names else len(others)

        if placement.where == "first":
            return 0

        at = others.index(placement.anchor or "")

        return at if placement.where == "before" else at + 1

    @classmethod
    def diff(cls, before, after, name):
        lines = difflib.unified_diff(
            before.splitlines(), after.splitlines(), name, name, lineterm=""
        )

        return DisplayDiff("\n".join(lines), reformats=False, label="json")

    @classmethod
    def raw_diff(cls, before, after, name):
        return cls.diff(before, after, name)

    @classmethod
    def report_extensions(cls, advertised, config):
        return []

    # -- folders, offline ----------------------------------------------------

    @classmethod
    def assumed_folder(cls, name, delimiter):
        return name.replace("/", delimiter or "."), delimiter or "."

    # -- backups, offline ----------------------------------------------------

    @classmethod
    def backup_target(cls, output, name, backup_dir):
        return backup_dir / f"{name}.json"

    @classmethod
    def write_backup(cls, source, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)

        return target

    @classmethod
    def backup(cls, source, name, backup_dir):
        return cls.write_backup(source, backup_dir / f"{name}.json")

    # -- connected: rules ----------------------------------------------------

    @property
    def has_rules(self):
        return self.rules_on

    def rules_capabilities(self):
        return []

    def missing_features(self, needed):
        return []

    def delivery_create(self, config):
        return DeliveryCreate(False)

    def list_rule_sets(self):
        return (self.active, [n for n in self.scripts if n != self.active])

    def active_rule_set(self):
        return self.active

    def read_rule_set(self, name):
        return self.scripts[name]

    def check_rule_set(self, source):
        json.loads(source)

    def store_rule_set(self, name, source):
        self.scripts[name] = source

    def activate_rule_set(self, name):
        self.active = name

    # -- connected: mail -----------------------------------------------------

    @property
    def has_mail(self):
        return self.mail_on

    def mail_capabilities(self):
        return []

    def list_folders(self):
        return FolderListing(".", sorted(self.folders), list(self.subscribed))

    def delimiter(self):
        return "."

    def normalize(self, name):
        name = name.replace("/", ".")

        return name if name.startswith("INBOX") else f"INBOX.{name}"

    def exists(self, folder):
        return folder in self.folders

    def case_variants(self, folder):
        return []

    def is_subscribed(self, folder):
        return folder in self.subscribed

    def create_folder(self, folder, subscribe):
        self.folders.append(folder)

        return FolderCreation(folder, subscribe)

    def subscribe(self, folder):
        self.subscribed.append(folder)

    def unsubscribe(self, folder):
        self.subscribed.remove(folder)

    def select_mail(self, criteria, source, destination, flags, discard):
        matched = [
            MessageSummary(uid, "", headers["FROM"][0], "", source)
            for uid, headers in sorted(self.messages.items())
            if criteria.matches(headers)
        ]

        return MailActionPlan(source, destination, flags, discard, matched)

    def apply_mail(self, plan):
        return MailActionResult(moved=plan.count)

    def search_messages(self, folder, expression):
        return sorted(self.messages)

    def message_headers(self, folder, uid):
        return email.message_from_string(
            f"From: {self.messages[uid]['FROM'][0]}\r\n\r\n"
        )

    def list_messages(self, folder, *, criteria, expression, limit):
        return [], False

    def message_source(self, folder, uid):
        return b"", ()


class UnorderedProvider(FakeProvider):
    """The fake again, with every rule evaluated on its own."""

    name = "unordered"
    capabilities = ProviderCapabilities(
        ordering=False,
        stop=True,
        rule_sets=True,
        actions=frozenset((FILEINTO, FLAG_ACTION, KEEP)),
        extensions=False,
        declined=frozenset(("move_rule", "position")),
    )
    opened = 0

    @classmethod
    def add_rule(cls, source, name, criteria, actions, *, replace, placement):
        entries = [e for e in json.loads(source or "[]") if e["name"] != name]
        entry = {
            "name": name,
            "from": criteria.terms[0].value,
            "actions": actions,
        }

        return json.dumps([*entries, entry])

    @classmethod
    @declined
    def move_rule(cls, source, name, placement):
        """Nothing to move a rule within."""

    @classmethod
    @declined
    def position(cls, names, placement, name):
        """No position to resolve."""


# ----------------------------------------------------------------------------
def _stored(name: str, sender: str) -> dict:
    return {"name": name, "from": sender, "actions": ["file:x", "stop"]}


# ----------------------------------------------------------------------------
@pytest.fixture
def fakes(monkeypatch):
    """Register both fakes for the test, and zero their counters."""
    for provider in (FakeProvider, UnorderedProvider):
        monkeypatch.setitem(registry.PROVIDERS, provider.name, provider)
        monkeypatch.setattr(provider, "opened", 0)


# ############################################################################
# Recording the engine's calls
# ############################################################################


class Recorder:
    """Wrap a provider and write down every operation the engine calls.

    Each call is kept as its name and its shape: the type of each
    positional argument and the names of the keyword ones. Attributes that
    are data (``name``, ``capabilities``, the ``has_*`` properties) pass
    straight through unrecorded -- reading them is how the engine is meant
    to decide, so they are not part of what must match.
    """

    # ------------------------------------------------------------------------
    def __init__(self, provider):
        self._provider = provider
        self.calls: list[tuple] = []

    # ------------------------------------------------------------------------
    def __getattr__(self, attribute):
        value = getattr(self._provider, attribute)

        if attribute not in OPERATIONS or not callable(value):
            return value

        def record(*args, **kwargs):
            self.calls.append(
                (
                    attribute,
                    tuple(type(arg).__name__ for arg in args),
                    tuple(sorted(kwargs)),
                )
            )

            return value(*args, **kwargs)

        return record


class RuleSession:
    """Just enough of ``SieveSession`` for the mxroute side of the proof."""

    # ------------------------------------------------------------------------
    def __init__(self, script):
        self.script = script

    def capabilities(self):
        return ["fileinto", "imap4flags", "mailbox"]

    def missing_extensions(self, required):
        return sorted(name for name in required if name not in self.caps)

    @property
    def caps(self):
        return self.capabilities()

    def list_scripts(self):
        return ("managesieve", [])

    def active_script_name(self):
        return "managesieve"

    def get_script(self, name):
        return self.script

    def check_script(self, content):
        pass

    def put_script(self, name, content):
        self.script = content

    def set_active(self, name):
        pass


# ----------------------------------------------------------------------------
def rule_session(script: str) -> SieveSession:
    """A ``RuleSession``, typed as the session it stands in for."""
    return cast(SieveSession, RuleSession(script))


# ----------------------------------------------------------------------------
def github_message(uid: int) -> bytes:
    return (
        f"From: GitHub <{GITHUB}>\r\nSubject: note {uid}\r\n"
        f"Date: Tue, 3 Feb 2026 04:05:06 +0000\r\n\r\n"
    ).encode()


# ----------------------------------------------------------------------------
def drive(recorded: Provider | Recorder, config: Config) -> None:
    """The representative engine operations, as a front-end runs them."""
    provider = cast(Provider, recorded)
    criteria = Criteria()
    criteria.add("From", GITHUB)
    spec = ActionSpec(fileinto="Lists")
    request = RuleRequest(criteria=criteria, actions=spec, name="github")

    folder = engine.plan_folder(provider, config, spec.fileinto)
    engine.missing_extensions(provider, spec, folder)
    plan = engine.plan_rule(provider, config, request, folder)
    engine.execute_script_change(provider, config, plan)

    engine.list_scripts(provider)
    engine.read_rules(provider)

    source = engine.source_folder(provider, "INBOX")
    mail = engine.plan_mail(provider, criteria, spec, source, folder.folder)
    engine.execute_mail(provider, mail, folder=folder)


# ############################################################################
# Every provider answers every operation
# ############################################################################


# ----------------------------------------------------------------------------
def declined_operations(provider: type[Provider]) -> set[str]:
    """The operations a provider marks with ``declined``."""
    found = set()

    for operation in OPERATIONS:
        member = inspect.getattr_static(provider, operation)
        function = getattr(member, "__func__", None) or getattr(
            member, "fget", member
        )

        if getattr(function, "__declined__", False):
            found.add(operation)

    return found


# ----------------------------------------------------------------------------
def test_the_operation_list_is_the_interface():
    """A list that drifted from the ABC would check the wrong set."""
    assert set(OPERATIONS) == Provider.__abstractmethods__
    assert {"select_mail", "add_rule", "open", "validate"} <= set(OPERATIONS)
    assert len(OPERATIONS) > 40


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "provider",
    [*registry.PROVIDERS.values(), FakeProvider, UnorderedProvider],
    ids=lambda provider: provider.name,
)
def test_every_provider_implements_or_declines_every_operation(provider):
    """No abstract operation is left, and what is declined is declared."""
    assert not provider.__abstractmethods__
    assert declined_operations(provider) == provider.capabilities.declined


# ----------------------------------------------------------------------------
def test_the_check_would_catch_a_missing_or_undeclared_operation():
    """Known positives: a gap is abstract, and an undeclared decline shows."""

    class Gap(Provider):
        name = "gap"
        capabilities = FULL

    assert "select_mail" in Gap.__abstractmethods__

    class Undeclared(FakeProvider):
        @classmethod
        @declined
        def position(cls, names, placement, name):
            """Declined without saying so in the capabilities."""

    assert declined_operations(Undeclared) != Undeclared.capabilities.declined


# ----------------------------------------------------------------------------
def test_a_declined_operation_refuses_through_the_one_error():
    with pytest.raises(MailctlError, match="the unordered provider cannot"):
        UnorderedProvider.position(["a"], None, "a")


# ############################################################################
# The registry and the provider setting
# ############################################################################


# ----------------------------------------------------------------------------
def test_mxroute_is_registered_and_is_the_default():
    config = load_config(argparse.Namespace())

    assert config.provider == "mxroute"
    assert config.sources["provider"] == Source(DEFAULT)
    assert registry.provider_for(config) is MxrouteProvider


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "rung", ["flag", "env file", "environment", "config file"]
)
def test_the_provider_setting_climbs_the_ladder(
    rung, fakes, monkeypatch, tmp_path
):
    args = argparse.Namespace()

    if rung == "flag":
        args.provider = "fake"

    elif rung == "env file":
        env = tmp_path / ".env"
        env.write_text("MAILCTL_PROVIDER=fake\n")
        args.env_file = str(env)

    elif rung == "environment":
        monkeypatch.setenv("MAILCTL_PROVIDER", "fake")

    else:
        config_path().parent.mkdir(parents=True)
        config_path().write_text('provider = "fake"\n')

    config = load_config(args)

    expected = {
        "flag": Source(FLAG, "--provider"),
        "env file": Source(ENV_FILE, "MAILCTL_PROVIDER", tmp_path / ".env"),
        "environment": Source(ENVIRONMENT, "MAILCTL_PROVIDER"),
        "config file": Source(CONFIG_FILE, "provider", config_path()),
    }[rung]

    assert config.provider == "fake"
    assert config.sources["provider"] == expected
    assert registry.provider_for(config) is FakeProvider


# ----------------------------------------------------------------------------
def test_an_unknown_provider_is_refused_naming_the_known_ones(monkeypatch):
    monkeypatch.setenv("MAILCTL_PROVIDER", "gmial")
    config = load_config(argparse.Namespace())
    opened = []
    monkeypatch.setattr(
        MxrouteProvider, "open", classmethod(lambda *a, **k: opened.append(1))
    )

    with (
        pytest.raises(MailctlError) as caught,
        engine.connect(config),
    ):
        pass

    message = str(caught.value)

    assert "'gmial' is not a known provider" in message
    assert "(from environment)" in message
    assert "Known: mxroute" in message
    assert opened == []


# ----------------------------------------------------------------------------
def test_the_cli_takes_the_provider_flag_and_refuses_an_unknown_one(capsys):
    assert cli.main(["list", "--provider", "nope"]) == 1

    error = capsys.readouterr().err

    assert "'nope' is not a known provider (from flag --provider)" in error


# ############################################################################
# A second provider needs no engine change
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_engine_makes_the_same_calls_whichever_provider_it_has(
    fake_imap, imap_session, imap_config, roundcube_script, fakes, tmp_path
):
    """The heart of it: identical calls, identical shapes."""
    imap_config.backup_dir = tmp_path / "backups"
    fake_imap.messages = {7: github_message(7), 8: github_message(8)}

    mxroute = Recorder(
        MxrouteProvider(
            sieve=rule_session(roundcube_script), imap=imap_session
        )
    )
    fake = Recorder(FakeProvider())

    drive(mxroute, imap_config)
    drive(fake, imap_config)

    assert len(mxroute.calls) > 15
    assert [call[0] for call in fake.calls] == [
        call[0] for call in mxroute.calls
    ]
    assert fake.calls == mxroute.calls


# ----------------------------------------------------------------------------
def test_the_proof_would_see_the_engine_branch_on_the_provider(
    fake_imap, imap_session, imap_config, roundcube_script, fakes, tmp_path
):
    """Known positive: one extra call on one provider is a difference."""
    imap_config.backup_dir = tmp_path / "backups"

    mxroute = Recorder(
        MxrouteProvider(
            sieve=rule_session(roundcube_script), imap=imap_session
        )
    )
    fake = Recorder(FakeProvider())

    drive(mxroute, imap_config)
    drive(fake, imap_config)
    fake.list_rule_sets()

    assert fake.calls != mxroute.calls


# ----------------------------------------------------------------------------
def test_the_fake_really_stored_the_rule_and_moved_the_mail(
    imap_config, fakes, tmp_path
):
    """The fake is a working host, not a recorder of no-ops."""
    imap_config.backup_dir = tmp_path / "backups"
    provider = FakeProvider()

    drive(provider, imap_config)

    assert provider.rule_names(provider.scripts["main"]) == [
        "keep-boss",
        "github",
    ]


# ----------------------------------------------------------------------------
def test_a_declined_capability_is_refused_before_any_connection(fakes):
    """``check_rule`` needs only the config: nothing is opened."""
    criteria = Criteria()
    criteria.add("From", GITHUB)
    request = RuleRequest(
        criteria=criteria,
        actions=ActionSpec(fileinto="Lists"),
        placement=Placement("first"),
    )
    config = Config(provider="unordered")

    with pytest.raises(MailctlError) as caught:
        engine.check_rule(config, request)

    assert str(caught.value) == (
        "the unordered provider cannot place a rule at a position in "
        "evaluation order: it does not declare the 'ordering' capability"
    )
    assert UnorderedProvider.opened == 0


# ----------------------------------------------------------------------------
def test_plan_rule_refuses_it_before_touching_the_provider(fakes):
    criteria = Criteria()
    criteria.add("From", GITHUB)
    request = RuleRequest(
        criteria=criteria,
        actions=ActionSpec(fileinto="Lists"),
        placement=Placement("first"),
    )
    recorder = Recorder(UnorderedProvider())
    provider = cast(Provider, recorder)
    folder = engine.plan_folder(FakeProvider(), Config(), "Lists")

    with pytest.raises(MailctlError, match="'ordering'"):
        engine.plan_rule(provider, Config(), request, folder)

    with pytest.raises(MailctlError, match="'ordering'"):
        engine.plan_move(provider, "keep-boss", Placement("first"))

    assert recorder.calls == []


# ----------------------------------------------------------------------------
def test_the_cli_refuses_it_before_connecting(fakes, capsys):
    code = cli.main(
        [
            "add",
            "--provider",
            "unordered",
            "--from",
            GITHUB,
            "--fileinto",
            "Lists",
            "--first",
        ]
    )

    assert code == 1
    assert "'ordering' capability" in capsys.readouterr().err
    assert UnorderedProvider.opened == 0


# ----------------------------------------------------------------------------
def test_without_ordering_no_position_can_shadow_a_rule(fakes):
    """The audit is about order; an unordered host has nothing to find."""
    provider = UnorderedProvider()
    provider.scripts["main"] = json.dumps(
        [_stored("broad", "example.com"), _stored("narrow", "a@example.com")]
    )

    assert engine.read_rules(provider).findings == []
    assert engine.read_rules(FakeProvider()).findings == []


# ----------------------------------------------------------------------------
def test_an_undeclared_action_is_refused_naming_what_is_declared(fakes):
    criteria = Criteria()
    criteria.add("From", GITHUB)
    request = RuleRequest(criteria=criteria, actions=ActionSpec(discard=True))

    with pytest.raises(MailctlError) as caught:
        engine.check_rule(Config(provider="unordered"), request)

    assert "cannot emit discard" in str(caught.value)
    assert "fileinto, flag, keep" in str(caught.value)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("specifics", "refused"),
    [
        ({"fake.label": "work"}, None),
        ({"fake.colour": "red"}, "not one this provider declares"),
        ({"fake.label": 3}, "must be a str"),
        ({"label": "work"}, "namespaced"),
    ],
)
def test_specifics_are_checked_against_the_declared_schema(
    specifics, refused, fakes
):
    criteria = Criteria()
    criteria.add("From", GITHUB)
    request = RuleRequest(
        criteria=criteria,
        actions=ActionSpec(fileinto="Lists"),
        specifics=specifics,
    )

    if refused is None:
        assert engine.check_rule(Config(provider="fake"), request) is None

        return

    with pytest.raises(MailctlError, match=refused):
        engine.check_rule(Config(provider="fake"), request)


# ----------------------------------------------------------------------------
def test_mxroute_declares_no_specifics_so_any_is_refused():
    criteria = Criteria()
    criteria.add("From", GITHUB)
    request = RuleRequest(
        criteria=criteria,
        actions=ActionSpec(fileinto="Lists"),
        specifics={"gmail.label": "work"},
    )

    with pytest.raises(MailctlError, match="declared: none"):
        engine.check_rule(Config(), request)


# ############################################################################
# mxroute hands back the neutral model, never its components' records
# ############################################################################


# ----------------------------------------------------------------------------
def test_mxroute_translates_every_record_it_returns(
    fake_imap, imap_session, roundcube_script
):
    """The components keep records of their own; the engine gets the
    model's. A record passed through untranslated would still work -- the
    fields agree -- which is why only the type can show it."""
    fake_imap.messages = {7: github_message(7)}
    provider = MxrouteProvider(
        sieve=rule_session(roundcube_script), imap=imap_session
    )
    criteria = Criteria()
    criteria.add("From", GITHUB)

    diff = provider.diff(roundcube_script, roundcube_script, "managesieve")
    raw = provider.raw_diff(roundcube_script, "", "managesieve")
    plan = provider.select_mail(criteria, "INBOX", "INBOX.Lists", [], False)
    result = provider.apply_mail(plan)
    created = provider.create_folder("INBOX.New", subscribe=True)
    listed, _more = provider.list_messages(
        "INBOX", criteria=criteria, expression=None, limit=None
    )

    assert type(diff) is model.DisplayDiff
    assert type(raw) is model.DisplayDiff
    assert (diff.label, raw.label) == ("sieve", "sieve")
    assert type(plan) is model.MailActionPlan
    assert plan.count == 1
    assert {type(message) for message in plan.messages} == {
        model.MessageSummary
    }
    assert type(result) is model.MailActionResult
    assert result.moved == 1
    assert type(created) is model.FolderCreation
    assert listed
    assert {type(message) for message in listed} == {model.MessageSummary}


# ############################################################################
# What mxroute declares
# ############################################################################


# ----------------------------------------------------------------------------
def test_mxroute_capabilities_are_what_sieve_over_managesieve_offers():
    caps = MxrouteProvider.capabilities

    assert (caps.ordering, caps.stop, caps.rule_sets, caps.extensions) == (
        True,
        True,
        True,
        True,
    )
    assert caps.actions == {FILEINTO, DISCARD, FLAG_ACTION, KEEP}
    assert caps.declined == frozenset()
    assert dict(caps.specifics) == {}


# ----------------------------------------------------------------------------
def test_the_redirect_policy_is_the_providers_not_the_engines(fakes):
    """The same refusal call reaches each provider's own policy."""
    with pytest.raises(MailctlError, match="mxroute_forwarder"):
        engine.reject_actions(Config(), ["redirect"])

    with pytest.raises(MailctlError, match="fake does not redirect"):
        engine.reject_actions(Config(provider="fake"), ["redirect"])


# ----------------------------------------------------------------------------
def test_an_unordered_host_plans_a_rule_with_no_placement_findings(
    fakes, tmp_path
):
    """Without ``ordering`` the engine never asks where a rule would land."""
    criteria = Criteria()
    criteria.add("From", "example.com")
    request = RuleRequest(criteria=criteria, actions=ActionSpec(keep=True))
    provider = UnorderedProvider()
    provider.scripts["main"] = json.dumps([_stored("narrow", "a@example.com")])
    folder = engine.plan_folder(provider, Config(), None)

    plan = engine.plan_rule(provider, Config(), request, folder)

    assert not plan.placement
    assert provider.rule_names(plan.after) == ["narrow", "from-example-com"]
