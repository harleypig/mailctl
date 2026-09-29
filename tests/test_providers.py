"""The provider interface, the registry, and a second provider (ADR 0006).

Three things are held here.

* **Every registered provider answers every operation.** Each half -- the
  dialect and the transport (ADR 0007) -- either implements an operation
  of its interface or explicitly declines it, and what the two decline is
  exactly what the provider's capabilities say it declines. This is the
  exhaustiveness an in-tree ABC gives in place of a compiler.
* **The ``provider`` setting** climbs the same ladder as every other
  setting, with provenance, and an unknown name is refused naming the
  known ones -- before any connection.
* **Adding a provider needs no utility change.** A fake second provider,
  registered for the test, is driven through the utilities'
  representative operations; the calls they make on its dialect and its
  transport are recorded and compared with the calls they make on
  ``mxroute``'s. They are the same calls in the same shape, because the
  utilities read capabilities and never ask which provider they have. A
  capability the fake declines is refused at validation, before anything
  that would be network work.
"""

import argparse
import difflib
import email
import inspect
import json
import re
from dataclasses import replace
from typing import cast

import pytest
from utilities_support import mxroute

from mailctl import MailctlError, cli, engine, utilities
from mailctl.components.managesieve import SieveSession
from mailctl.components.managesieve.capabilities import Capabilities
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
from mailctl.engine import Session
from mailctl.providers import model, registry
from mailctl.providers.base import (
    DIALECT_OPERATIONS,
    DISCARD,
    FILEINTO,
    KEEP,
    OPERATIONS,
    TRANSPORT_OPERATIONS,
    Capability,
    CountSupport,
    DeliveryCreate,
    Dialect,
    DisplayDiff,
    DriftTerms,
    Fact,
    FetchedMessage,
    FolderListing,
    FolderReference,
    FolderStatus,
    MailActionResult,
    MessageSummary,
    Namespace,
    Placement,
    Provider,
    ProviderCapabilities,
    ServerDescription,
    Specific,
    Transport,
    Wording,
    declined,
    refuse,
)
from mailctl.providers.base import (
    FLAG as FLAG_ACTION,
)
from mailctl.providers.mxroute import MXROUTE, MxrouteDialect, MxrouteTransport
from mailctl.rules import rule_from_criteria
from mailctl.utilities.rules import ActionSpec, RuleRequest

# ############################################################################
# A second provider, for the test only
# ############################################################################

GITHUB = "noreply@github.com"

FULL = ProviderCapabilities(
    ordering=True,
    stop=True,
    rule_sets=True,
    disable=True,
    actions=frozenset((FILEINTO, DISCARD, FLAG_ACTION, KEEP)),
    extensions=True,
    raw_query=True,
    mark=True,
    folder_counts=True,
    specifics={"fake.label": Specific(str, "a label to add")},
)


class FakeDialect(Dialect):
    """A host with nothing in common with MXroute but the interface.

    Rule sets are JSON, not Sieve, and every call is answered offline.
    """

    name = "fake"
    wording = Wording(
        rules_service="Fake rules",
        mail_service="Fake mail",
        extensions="Fake extensions",
        notes=("the fake host keeps its rules as JSON.",),
    )

    # -- refusals and requirements -------------------------------------------

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
    def missing_features(cls, needed, advertised):
        return []

    @classmethod
    def delivery_create(cls, config, advertised):
        return DeliveryCreate(False)

    @classmethod
    def check_actions(cls, config, actions):
        pass

    @classmethod
    def check_criteria(cls, config, criteria, advertised):
        if criteria.body:
            raise refuse(cls.name, "test a message's body", "it keeps From")

    @classmethod
    def describe_actions(cls, actions):
        return ", ".join(actions)

    @classmethod
    def candidate_rule(cls, name, criteria, actions):
        return rule_from_criteria(
            name, criteria, tuple(actions), stops="stop" in actions
        )

    # -- rule sets -----------------------------------------------------------

    @classmethod
    def rule_names(cls, source):
        return [entry["name"] for entry in json.loads(source or "[]")]

    @classmethod
    def rule_set_requires(cls, source):
        return []

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
            rules.append(replace(rule, disabled=entry.get("off", False)))

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
    def disable_rule(cls, source, name):
        return cls._switch(source, name, off=True)

    @classmethod
    def enable_rule(cls, source, name):
        return cls._switch(source, name, off=False)

    @classmethod
    def _switch(cls, source, name, off):
        entries = json.loads(source)
        entry = next((e for e in entries if e["name"] == name), None)

        if entry is None:
            raise MailctlError(f"no rule named {name!r}")

        if entry.get("off", False) == off:
            return source

        entry["off"] = off

        return json.dumps(entries)

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
    def folder_references(cls, source):
        return [
            FolderReference(entry["name"], action.removeprefix("file:"))
            for entry in json.loads(source or "[]")
            for action in entry["actions"]
            if action.startswith("file:")
        ]

    @classmethod
    def retarget_folders(cls, source, renames):
        entries = json.loads(source or "[]")

        for entry in entries:
            entry["actions"] = [
                f"file:{renames.get(action[5:], action[5:])}"
                if action.startswith("file:")
                else action
                for action in entry["actions"]
            ]

        return json.dumps(entries)

    @classmethod
    def report_extensions(cls, advertised, config):
        return []

    # -- backups -------------------------------------------------------------

    @classmethod
    def backup_path(cls, name, backup_dir):
        return backup_dir / f"{name}.json"

    @classmethod
    def backup_target(cls, output, name, backup_dir):
        return cls.backup_path(name, backup_dir)

    # -- folders -------------------------------------------------------------

    @classmethod
    def normalize(cls, name, listing):
        name = name.replace("/", listing.delimiter)

        return name if name.startswith("INBOX") else f"INBOX.{name}"

    @classmethod
    def assumed_folder(cls, name, delimiter):
        return name.replace("/", delimiter or "."), delimiter or "."

    # -- describing the host -------------------------------------------------

    @classmethod
    def connection_facts(cls, config):
        return [Fact("Fake", "fake.example", (("host", "host"),))]

    @classmethod
    def mail_facts(cls, capabilities):
        return [Fact("Labels", "yes\nevery folder is a label")]

    @classmethod
    def count_support(cls, capabilities):
        return CountSupport(sizes=True)

    @classmethod
    def sorts_messages(cls, capabilities):
        return "ORDERED" in capabilities

    @classmethod
    def drift_terms(cls):
        return DriftTerms(relied=frozenset({"LABELS"}))


class FakeTransport(Transport):
    """The fake host's servers: the rule sets and the mailbox are dicts.

    ``opened`` counts connections, which is what "before any network work"
    is checked on.
    """

    name = "fake"
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
            7: f"From: {GITHUB}\r\nSubject: hello\r\n\r\n",
            8: "From: someone@example.com\r\nSubject: hi\r\n\r\n",
        }
        self.flags: dict[int, tuple[str, ...]] = {}
        # Each folder's (messages, unseen, size).
        self.counts = {"INBOX": (2, 1, 512), "INBOX.Lists": (0, 0, 0)}
        self.mail_caps: list[str] = []

    # ------------------------------------------------------------------------
    @classmethod
    def open(cls, config, *, progress=None):
        return cls()

    def connect(self, half):
        type(self).opened += 1

    def disconnect(self, half):
        pass

    def dropped(self, error):
        return False

    # -- the rule half -------------------------------------------------------

    @property
    def has_rules(self):
        return self.rules_on

    def rules_capabilities(self):
        return []

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

    def describe_rules_server(self):
        return ServerDescription(
            (("name", "fake rules"),), (Capability("JSON"),), True
        )

    # -- the mail half -------------------------------------------------------

    @property
    def has_mail(self):
        return self.mail_on

    def mail_capabilities(self):
        return list(self.mail_caps)

    def list_folders(self):
        return FolderListing(".", list(self.folders), list(self.subscribed))

    def create_folder(self, folder):
        self.folders.append(folder)

    def subscribe(self, folder):
        self.subscribed.append(folder)

    def unsubscribe(self, folder):
        self.subscribed.remove(folder)

    def describe_mail_server(self):
        return ServerDescription(
            (("name", "fake mail"),), (Capability("LABELS"),), True
        )

    def mail_namespaces(self):
        return [Namespace("personal", "", ".")]

    def folder_status(self, sizes):
        return [
            FolderStatus(name, messages, unseen, size if sizes else None)
            for name, (messages, unseen, size) in self.counts.items()
        ]

    def search(self, folder, criteria):
        # A host search coarser than the rule: every message is a
        # candidate, and the utilities' re-check has to narrow them.
        return sorted(self.messages)

    def search_messages(self, folder, expression):
        return sorted(self.messages)

    def fetch_headers(self, uids, folder):
        return [self._fetched(uid, folder) for uid in sorted(uids)]

    def fetch_summaries(self, uids, folder):
        return [self._fetched(uid, folder) for uid in uids]

    def _fetched(self, uid, folder):
        headers = email.message_from_string(self.messages[uid])
        summary = MessageSummary(
            uid,
            "",
            headers["From"],
            "",
            folder,
            size=len(self.messages[uid]),
            flags=self.flags.get(uid, ()),
        )

        return FetchedMessage(headers, summary)

    def apply_mail(self, plan):
        return MailActionResult(moved=plan.count)

    def add_flags(self, folder, uids, flags):
        for uid in uids:
            self.flags[uid] = (*self.flags.get(uid, ()), *flags)

    def remove_flags(self, folder, uids, flags):
        for uid in uids:
            self.flags[uid] = tuple(
                flag for flag in self.flags.get(uid, ()) if flag not in flags
            )

    def message_headers(self, folder, uid):
        return email.message_from_string(self.messages[uid])

    def message_source(self, folder, uid):
        return self.messages[uid].encode(), ()

    def sort_messages(self, folder, order, criteria, expression):
        # As coarse as search: every message, ordered by size or by UID.
        def key(uid):
            return len(self.messages[uid]) if order.key == "size" else uid

        return sorted(sorted(self.messages), key=key, reverse=order.reverse)

    def rename_folder(self, old, new):
        self.folders = [
            new + name[len(old) :]
            if name == old or name.startswith(f"{old}.")
            else name
            for name in self.folders
        ]

    def message_count(self, folder):
        return len(self.messages)


FAKE = Provider("fake", FULL, FakeDialect, FakeTransport)


class UnorderedDialect(FakeDialect):
    """The fake again, with every rule evaluated on its own."""

    name = "unordered"

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


class UnorderedTransport(FakeTransport):
    name = "unordered"
    opened = 0


UNORDERED = Provider(
    "unordered",
    ProviderCapabilities(
        ordering=False,
        stop=True,
        rule_sets=True,
        disable=True,
        actions=frozenset((FILEINTO, FLAG_ACTION, KEEP)),
        extensions=False,
        raw_query=False,
        mark=True,
        folder_counts=True,
        declined=frozenset(("move_rule", "position")),
    ),
    UnorderedDialect,
    UnorderedTransport,
)


class StoplessDialect(FakeDialect):
    """The fake again, on a host where nothing ends evaluation early."""

    name = "stopless"


class StoplessTransport(FakeTransport):
    name = "stopless"
    opened = 0


STOPLESS = Provider(
    "stopless",
    ProviderCapabilities(
        ordering=True,
        stop=False,
        rule_sets=True,
        disable=True,
        actions=frozenset((FILEINTO, DISCARD, FLAG_ACTION, KEEP)),
        extensions=False,
        raw_query=False,
        mark=True,
        folder_counts=True,
    ),
    StoplessDialect,
    StoplessTransport,
)


# ----------------------------------------------------------------------------
def _stored(name: str, sender: str) -> dict:
    return {"name": name, "from": sender, "actions": ["file:x", "stop"]}


# ----------------------------------------------------------------------------
def fake_session(provider: Provider = FAKE) -> Session:
    """A fake provider's session, both halves connected."""
    return Session(provider, provider.transport())


# ----------------------------------------------------------------------------
def fake_transport(session: Session) -> FakeTransport:
    """A fake session's transport, typed as the fake it is."""
    return cast(FakeTransport, session.transport)


# ----------------------------------------------------------------------------
@pytest.fixture
def fakes(monkeypatch):
    """Register the fakes for the test, and zero their counters."""
    for provider in (FAKE, UNORDERED, STOPLESS):
        monkeypatch.setitem(registry.PROVIDERS, provider.name, provider)
        monkeypatch.setattr(provider.transport, "opened", 0)


# ############################################################################
# Recording the utilities' calls
# ############################################################################


class RecordedHalf:
    """One half of a provider, writing down every operation called on it.

    Each call is kept as its half and name, and its shape: the type of each
    positional argument and the names of the keyword ones. Attributes that
    are data (``name``, ``wording``, the ``has_*`` properties) pass
    straight through unrecorded -- reading them is how a utility is meant
    to decide, so they are not part of what must match.
    """

    # ------------------------------------------------------------------------
    def __init__(self, half, label: str, operations, calls: list):
        self._half = half
        self._label = label
        self._operations = operations
        self._calls = calls

    # ------------------------------------------------------------------------
    def __getattr__(self, attribute):
        value = getattr(self._half, attribute)

        if attribute not in self._operations or not callable(value):
            return value

        def record(*args, **kwargs):
            self._calls.append(
                (
                    f"{self._label}.{attribute}",
                    tuple(type(arg).__name__ for arg in args),
                    tuple(sorted(kwargs)),
                )
            )

            return value(*args, **kwargs)

        return record


class Recorder:
    """A session whose dialect and transport both record their calls."""

    # ------------------------------------------------------------------------
    def __init__(self, session: Session):
        self.calls: list[tuple] = []

        dialect = RecordedHalf(
            session.dialect, "dialect", DIALECT_OPERATIONS, self.calls
        )
        transport = RecordedHalf(
            session.transport, "transport", TRANSPORT_OPERATIONS, self.calls
        )

        self.session = Session(
            replace(session.provider, dialect=cast(type[Dialect], dialect)),
            cast(Transport, transport),
        )


class RuleSession:
    """Just enough of ``SieveSession`` for the mxroute side of the proof."""

    # ------------------------------------------------------------------------
    def __init__(self, script):
        self.script = script

    def capabilities(self):
        return ["fileinto", "imap4flags", "mailbox"]

    def server_capabilities(self):
        return Capabilities((("SIEVE", " ".join(self.capabilities())),))

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
def drive(session: Session, config: Config) -> None:
    """The representative utility operations, as a front-end runs them."""
    criteria = Criteria()
    criteria.add("From", GITHUB)
    spec = ActionSpec(fileinto="Lists")
    request = RuleRequest(criteria=criteria, actions=spec, name="github")

    folder = utilities.folders.plan_folder(session, config, spec.fileinto)
    utilities.rules.missing_extensions(session, spec, folder)
    plan = utilities.rules.plan_rule(session, config, request, folder)
    utilities.rules.execute_script_change(session, config, plan)

    switch = utilities.rules.plan_switch(session, "github", enable=False)
    utilities.rules.execute_script_change(session, config, switch)

    utilities.scripts.list_scripts(session)
    utilities.rules.read_rules(session)

    source = utilities.mail.source_folder(session, "INBOX")
    mail = utilities.mail.plan_mail(
        session, criteria, spec, source, folder.folder
    )
    utilities.mail.execute_mail(session, mail, folder=folder)

    add, remove = utilities.flags.mark_flags(read=True, flagged=False)
    marks = utilities.flags.plan_mark(session, "INBOX", [7, 8], add, remove)
    utilities.flags.execute_mark(session, marks)

    utilities.folders.list_folder_counts(session)
    utilities.senders.count_senders(session, "INBOX", criteria)

    utilities.reports.probe_servers(session, config)


# ############################################################################
# Every provider answers every operation
# ############################################################################


# ----------------------------------------------------------------------------
def declined_operations(provider: Provider) -> set[str]:
    """The operations either half of a provider marks with ``declined``."""
    found = set()

    for half, operations in (
        (provider.dialect, DIALECT_OPERATIONS),
        (provider.transport, TRANSPORT_OPERATIONS),
    ):
        for operation in operations:
            member = inspect.getattr_static(half, operation)
            function = getattr(member, "__func__", None) or getattr(
                member, "fget", member
            )

            if getattr(function, "__declined__", False):
                found.add(operation)

    return found


# ----------------------------------------------------------------------------
def test_the_operation_lists_are_the_interface():
    """A list that drifted from its ABC would check the wrong set."""
    assert set(DIALECT_OPERATIONS) == Dialect.__abstractmethods__
    assert set(TRANSPORT_OPERATIONS) == Transport.__abstractmethods__
    assert not set(DIALECT_OPERATIONS) & set(TRANSPORT_OPERATIONS)
    assert set(OPERATIONS) == set(DIALECT_OPERATIONS) | set(
        TRANSPORT_OPERATIONS
    )
    assert {"add_rule", "validate", "diff"} <= set(DIALECT_OPERATIONS)
    assert {"search", "open", "store_rule_set"} <= set(TRANSPORT_OPERATIONS)
    assert len(OPERATIONS) > 40


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "provider",
    [*registry.PROVIDERS.values(), FAKE, UNORDERED, STOPLESS],
    ids=lambda provider: provider.name,
)
def test_every_provider_implements_or_declines_every_operation(provider):
    """No abstract operation is left in either half, both halves carry the
    provider's name, and what is declined is declared."""
    assert not provider.dialect.__abstractmethods__
    assert not provider.transport.__abstractmethods__
    assert provider.dialect.name == provider.transport.name == provider.name
    assert declined_operations(provider) == provider.capabilities.declined


# ----------------------------------------------------------------------------
def test_the_check_would_catch_a_missing_or_undeclared_operation():
    """Known positives: a gap in either half is abstract, and an
    undeclared decline shows."""

    class GapDialect(Dialect):
        name = "gap"

    class GapTransport(Transport):
        name = "gap"

    assert "add_rule" in GapDialect.__abstractmethods__
    assert "search" in GapTransport.__abstractmethods__

    class Undeclared(FakeDialect):
        @classmethod
        @declined
        def position(cls, names, placement, name):
            """Declined without saying so in the capabilities."""

    undeclared = replace(FAKE, dialect=Undeclared)

    assert declined_operations(undeclared) != FULL.declined


# ----------------------------------------------------------------------------
def test_a_declined_operation_refuses_through_the_one_error():
    with pytest.raises(MailctlError, match="the unordered provider cannot"):
        UnorderedDialect.position(["a"], None, "a")


# ############################################################################
# The registry and the provider setting
# ############################################################################


# ----------------------------------------------------------------------------
def test_mxroute_is_registered_and_is_the_default():
    config = load_config(argparse.Namespace())

    assert config.provider == "mxroute"
    assert config.sources["provider"] == Source(DEFAULT)
    assert registry.provider_for(config) is MXROUTE


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
    assert registry.provider_for(config) is FAKE


# ----------------------------------------------------------------------------
def test_an_unknown_provider_is_refused_naming_the_known_ones(monkeypatch):
    monkeypatch.setenv("MAILCTL_PROVIDER", "gmial")
    config = load_config(argparse.Namespace())
    opened = []
    monkeypatch.setattr(
        MxrouteTransport,
        "open",
        classmethod(lambda *a, **k: opened.append(1)),
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
# A second provider needs no utility change
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_utilities_make_the_same_calls_whichever_provider_they_have(
    fake_imap, imap_session, imap_config, roundcube_script, fakes, tmp_path
):
    """The heart of it: identical calls, identical shapes, on each half."""
    imap_config.backup_dir = tmp_path / "backups"
    fake_imap.messages = {7: github_message(7), 8: github_message(8)}
    fake_imap.caps |= {"LIST-STATUS", "STATUS=SIZE"}

    real = Recorder(
        mxroute(sieve=rule_session(roundcube_script), imap=imap_session)
    )
    fake = Recorder(fake_session())

    drive(real.session, imap_config)
    drive(fake.session, imap_config)

    mxroute_calls = real.calls
    halves = {call[0].split(".")[0] for call in mxroute_calls}

    assert len(mxroute_calls) > 15
    assert halves == {"dialect", "transport"}
    assert [call[0] for call in fake.calls] == [
        call[0] for call in mxroute_calls
    ]
    assert fake.calls == mxroute_calls


# ----------------------------------------------------------------------------
def test_the_proof_would_see_a_utility_branch_on_the_provider(
    fake_imap, imap_session, imap_config, roundcube_script, fakes, tmp_path
):
    """Known positive: one extra call on either half is a difference."""
    imap_config.backup_dir = tmp_path / "backups"
    fake_imap.messages = {7: github_message(7), 8: github_message(8)}
    fake_imap.caps |= {"LIST-STATUS", "STATUS=SIZE"}

    for extra in ("transport", "dialect"):
        real = Recorder(
            mxroute(sieve=rule_session(roundcube_script), imap=imap_session)
        )
        fake = Recorder(fake_session())

        drive(real.session, imap_config)
        drive(fake.session, imap_config)

        if extra == "transport":
            fake.session.transport.list_rule_sets()

        else:
            fake.session.dialect.rule_names("[]")

        assert fake.calls != real.calls, extra


# ----------------------------------------------------------------------------
def test_the_fake_really_stored_the_rule_and_moved_the_mail(
    imap_config, fakes, tmp_path
):
    """The fake is a working host, not a recorder of no-ops."""
    imap_config.backup_dir = tmp_path / "backups"
    session = fake_session()

    drive(session, imap_config)

    stored = FakeDialect.read_rules(fake_transport(session).scripts["main"])

    assert [(rule.name, rule.disabled) for rule in stored] == [
        ("keep-boss", False),
        ("github", True),
    ]


# ----------------------------------------------------------------------------
def test_the_probe_reports_the_fake_in_its_own_terms(imap_config, fakes):
    """#101: the document is the neutral model filled from the fake's
    transport and dialect -- nothing about Sieve or MXroute."""
    record = utilities.reports.probe_servers(fake_session(), imap_config)
    text = utilities.reports.dump_probe(record)
    document = json.loads(text)

    assert document["provider"] == "fake"
    assert document["endpoints"] == [
        {"label": "Fake", "value": "fake.example"}
    ]
    assert document["rules"]["identity"] == {"name": "fake rules"}
    assert document["rules"]["active_rule_set"] == "main"
    assert document["mail"]["capabilities"] == [
        {"name": "LABELS", "value": None}
    ]
    assert document["mail"]["namespaces"] == [
        {"kind": "personal", "prefix": "", "delimiter": "."}
    ]
    assert not re.search(r"(?i)sieve|mxroute|imap", text)


# ----------------------------------------------------------------------------
def test_the_probe_lists_no_extensions_for_a_host_without_them(
    imap_config, fakes
):
    """Extensions are asked for only where the capabilities declare
    them; the fake that does is the known positive."""
    asked = {}

    for provider in (FAKE, UNORDERED):
        recorder = Recorder(fake_session(provider))
        record = utilities.reports.probe_servers(recorder.session, imap_config)
        asked[provider.name] = [call[0] for call in recorder.calls]

        assert record.extensions == ()
        assert record.rules is not None

    assert "transport.rules_capabilities" in asked["fake"]
    assert "transport.rules_capabilities" not in asked["unordered"]


# ----------------------------------------------------------------------------
def test_a_coarse_host_search_is_narrowed_by_the_utilities(fakes):
    """The fake's search returns every message; selection is still exact,
    because the re-check is the utilities', not the transport's."""
    criteria = Criteria()
    criteria.add("From", GITHUB)

    plan = utilities.mail.plan_mail(
        fake_session(), criteria, ActionSpec(), "INBOX", "INBOX.Lists"
    )

    assert fake_session().transport.search("INBOX", criteria) == [7, 8]
    assert plan.uids == [7]


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
        utilities.rules.check_rule(config, request)

    assert str(caught.value) == (
        "the unordered provider cannot place a rule at a position in "
        "evaluation order: it does not declare the 'ordering' capability"
    )
    assert UnorderedTransport.opened == 0


# ----------------------------------------------------------------------------
def test_plan_rule_refuses_it_before_touching_the_provider(fakes):
    criteria = Criteria()
    criteria.add("From", GITHUB)
    request = RuleRequest(
        criteria=criteria,
        actions=ActionSpec(fileinto="Lists"),
        placement=Placement("first"),
    )
    recorder = Recorder(fake_session(UNORDERED))
    session = recorder.session
    folder = utilities.folders.plan_folder(fake_session(), Config(), "Lists")

    with pytest.raises(MailctlError, match="'ordering'"):
        utilities.rules.plan_rule(session, Config(), request, folder)

    with pytest.raises(MailctlError, match="'ordering'"):
        utilities.rules.plan_move(session, "keep-boss", Placement("first"))

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
    assert UnorderedTransport.opened == 0


# ----------------------------------------------------------------------------
def test_without_ordering_no_position_can_shadow_a_rule(fakes):
    """The audit is about order; an unordered host has nothing to find."""
    session = fake_session(UNORDERED)
    fake_transport(session).scripts["main"] = json.dumps(
        [_stored("broad", "example.com"), _stored("narrow", "a@example.com")]
    )

    assert utilities.rules.read_rules(session).findings == []
    assert utilities.rules.read_rules(fake_session()).findings == []


# ----------------------------------------------------------------------------
def test_an_undeclared_action_is_refused_naming_what_is_declared(fakes):
    criteria = Criteria()
    criteria.add("From", GITHUB)
    request = RuleRequest(criteria=criteria, actions=ActionSpec(discard=True))

    with pytest.raises(MailctlError) as caught:
        utilities.rules.check_rule(Config(provider="unordered"), request)

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
        assert (
            utilities.rules.check_rule(Config(provider="fake"), request)
            is None
        )

        return

    with pytest.raises(MailctlError, match=refused):
        utilities.rules.check_rule(Config(provider="fake"), request)


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
        utilities.rules.check_rule(Config(), request)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "actions",
    [ActionSpec(), ActionSpec(stop=True)],
    ids=["nothing", "stop-only"],
)
def test_a_rule_with_no_action_is_refused_before_connecting(actions):
    """#137: refused by the utilities, so a front-end never logs in for it."""
    criteria = Criteria()
    criteria.add("From", GITHUB)
    request = RuleRequest(criteria=criteria, actions=actions)

    with pytest.raises(MailctlError, match="no action requested"):
        utilities.rules.check_rule(Config(), request)


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
    transport = MxrouteTransport(
        sieve=rule_session(roundcube_script), imap=imap_session
    )
    criteria = Criteria()
    criteria.add("From", GITHUB)

    diff = MxrouteDialect.diff(
        roundcube_script, roundcube_script, "managesieve"
    )
    raw = MxrouteDialect.raw_diff(roundcube_script, "", "managesieve")
    listing = transport.list_folders()
    uids = transport.search("INBOX", criteria)
    fetched = [
        *transport.fetch_headers(uids, "INBOX"),
        *transport.fetch_summaries(uids, "INBOX"),
    ]
    result = transport.apply_mail(
        model.MailActionPlan(
            "INBOX", "INBOX.Lists", [], False, [fetched[0].summary]
        )
    )
    statuses = transport.folder_status(sizes=True)
    support = MxrouteDialect.count_support(["LIST-STATUS"])

    assert type(diff) is model.DisplayDiff
    assert type(raw) is model.DisplayDiff
    assert (diff.label, raw.label) == ("sieve", "sieve")
    assert type(listing) is model.FolderListing
    assert len(fetched) == 2
    assert {type(item) for item in fetched} == {model.FetchedMessage}
    assert {type(item.summary) for item in fetched} == {model.MessageSummary}
    assert type(result) is model.MailActionResult
    assert result.moved == 1
    assert {type(item) for item in statuses} == {model.FolderStatus}
    assert type(support) is model.CountSupport


# ############################################################################
# What mxroute declares
# ############################################################################


# ----------------------------------------------------------------------------
def test_mxroute_capabilities_are_what_sieve_over_managesieve_offers():
    caps = MXROUTE.capabilities

    assert (
        caps.ordering,
        caps.stop,
        caps.rule_sets,
        caps.disable,
        caps.extensions,
    ) == (True, True, True, True, True)
    assert caps.actions == {FILEINTO, DISCARD, FLAG_ACTION, KEEP}
    assert caps.declined == frozenset()
    assert dict(caps.specifics) == {}


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("advertised", "expected"),
    [
        (["IMAP4REV1", "MOVE"], model.CountSupport("LIST-STATUS", False)),
        (["LIST-STATUS"], model.CountSupport(None, False)),
        (["list-status", "status=size"], model.CountSupport(None, True)),
        (["STATUS=SIZE"], model.CountSupport("LIST-STATUS", True)),
    ],
)
def test_mxroute_counts_folders_only_where_list_status_is_advertised(
    advertised, expected
):
    assert MxrouteDialect.count_support(advertised) == expected


# ----------------------------------------------------------------------------
def test_the_redirect_policy_is_the_providers_not_the_engines(fakes):
    """The same refusal call reaches each provider's own policy."""
    with pytest.raises(MailctlError, match="mxroute_forwarder"):
        utilities.rules.reject_actions(Config(), ["redirect"])

    with pytest.raises(MailctlError, match="fake does not redirect"):
        utilities.rules.reject_actions(Config(provider="fake"), ["redirect"])


# ----------------------------------------------------------------------------
def test_an_unordered_host_plans_a_rule_with_no_placement_findings(
    fakes, tmp_path
):
    """Without ``ordering`` no utility asks where a rule would land."""
    criteria = Criteria()
    criteria.add("From", "example.com")
    request = RuleRequest(criteria=criteria, actions=ActionSpec(keep=True))
    session = fake_session(UNORDERED)
    fake_transport(session).scripts["main"] = json.dumps(
        [_stored("narrow", "a@example.com")]
    )
    folder = utilities.folders.plan_folder(session, Config(), None)

    plan = utilities.rules.plan_rule(session, Config(), request, folder)

    assert not plan.placement
    assert UnorderedDialect.rule_names(plan.after) == [
        "narrow",
        "from-example-com",
    ]


# ############################################################################
# The stop default is the provider's (#99)
# ############################################################################


# ----------------------------------------------------------------------------
def github_rule(**actions) -> RuleRequest:
    criteria = Criteria()
    criteria.add("From", GITHUB)

    return RuleRequest(
        criteria=criteria, actions=ActionSpec(fileinto="Lists", **actions)
    )


# ----------------------------------------------------------------------------
def test_a_host_without_stop_plans_a_default_rule(fakes, tmp_path):
    """A rule nobody asked to stop is not refused for lacking stop."""
    session = fake_session(STOPLESS)
    request = github_rule()
    folder = utilities.folders.plan_folder(session, Config(), "Lists")

    utilities.rules.check_rule(Config(provider="stopless"), request)
    plan = utilities.rules.plan_rule(session, Config(), request, folder)

    assert plan.actions == ["file:INBOX.Lists"]


# ----------------------------------------------------------------------------
def test_a_host_with_stop_still_stops_by_default():
    """mxroute's rules end evaluation unless --no-stop says otherwise."""
    folder = utilities.folders.plan_folder(
        mxroute(), Config(), "Lists", delimiter="."
    )
    spec = utilities.rules.resolve_stop(MXROUTE, ActionSpec(fileinto="Lists"))
    actions = MxrouteDialect.translate_actions(spec, folder.folder, False)

    assert spec.stop is True
    assert actions[-1] == ("stop",)


# ----------------------------------------------------------------------------
def test_asking_a_host_without_stop_to_stop_is_refused(fakes):
    """An explicit request is still one the provider has to honour."""
    with pytest.raises(MailctlError) as caught:
        utilities.rules.check_rule(
            Config(provider="stopless"), github_rule(stop=True)
        )

    assert str(caught.value) == (
        "the stopless provider cannot end evaluation after a rule: it does "
        "not declare the 'stop' capability"
    )


# ----------------------------------------------------------------------------
def test_the_cli_adds_a_default_rule_on_a_host_without_stop(fakes, capsys):
    code = cli.main(
        [
            "add",
            "--provider",
            "stopless",
            "--from",
            GITHUB,
            "--fileinto",
            "Lists",
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert '"actions": ["file:INBOX.Lists"]}]' in captured.out


# ############################################################################
# The host's own wording is the provider's data (#99)
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_diff_and_its_actions_are_shown_in_the_hosts_own_words(
    fakes, capsys
):
    """No Sieve heading and no Sieve tuple rendering on a JSON host."""
    code = cli.main(
        [
            "add",
            "--provider",
            "fake",
            "--from",
            GITHUB,
            "--fileinto",
            "Lists",
            "--dry-run",
        ]
    )

    out = capsys.readouterr().out

    assert code == 0
    assert "  then:  file:INBOX.Lists, stop\n" in out
    assert "\n--- json diff ---\n" in out
    assert "sieve" not in out.lower()


# ----------------------------------------------------------------------------
def test_the_test_report_is_laid_out_from_the_providers_data(
    fakes, capsys, monkeypatch
):
    """Service names, capability facts, and closing notes are all the
    provider's; nothing about MXroute or Sieve is left in the CLI."""
    monkeypatch.setenv("MAILCTL_PASSWORD", "not-a-real-password")

    code = cli.main(["test", "--provider", "fake"])

    out = capsys.readouterr().out

    assert code == 0
    assert "\nFake:      fake.example  (default)\n" in out
    assert "\nFake rules: connected\n" in out
    assert "\nFake mail: connected\n" in out
    assert "\n  Labels:    yes\n             every folder is a label\n" in out
    assert "\nNote: the fake host keeps its rules as JSON.\n" in out
    assert "extensions" not in out
    assert "sieve" not in out.lower()
    assert "MXRoute" not in out
    assert "Exim" not in out


# ############################################################################
# disabled_extensions belongs to a provider that declares extensions (#99)
# ############################################################################


# ----------------------------------------------------------------------------
def test_disabled_extensions_is_refused_by_a_host_without_extensions(
    fakes, monkeypatch
):
    """A setting the provider has no use for is an error, never ignored."""
    monkeypatch.setenv("MAILCTL_DISABLED_EXTENSIONS", "mailbox")
    monkeypatch.setenv("MAILCTL_PROVIDER", "stopless")
    config = load_config(argparse.Namespace())

    with pytest.raises(MailctlError) as caught, engine.connect(config):
        pass

    assert str(caught.value) == (
        "the stopless provider cannot take disabled_extensions (from "
        "environment): it does not declare the 'extensions' capability"
    )
    assert StoplessTransport.opened == 0


# ----------------------------------------------------------------------------
def test_an_empty_disabled_extensions_is_no_request_at_all(fakes):
    """Nothing disabled asks nothing of the provider."""
    with engine.connect(Config(provider="stopless")) as live:
        assert live.name == "stopless"


# ----------------------------------------------------------------------------
def test_the_cli_refuses_disable_extension_for_such_a_host(fakes, capsys):
    code = cli.main(
        ["list", "--provider", "stopless", "--disable-extension", "mailbox"]
    )

    assert code == 1
    assert (
        "the stopless provider cannot take disabled_extensions (from flag "
        "--disable-extension)" in capsys.readouterr().err
    )
    assert StoplessTransport.opened == 0


# ----------------------------------------------------------------------------
def test_mxroute_still_takes_disabled_extensions(monkeypatch):
    """The owner of the setting: accepted, and its names still checked."""
    config = Config(disabled_extensions=frozenset({"mailbox"}))

    MxrouteDialect.validate(config)

    with pytest.raises(MailctlError, match="unknown Sieve extension"):
        MxrouteDialect.validate(
            Config(disabled_extensions=frozenset({"nope"}))
        )


# ############################################################################
# Help offers only what the selected provider declares (#26, #99)
# ############################################################################

PLACEMENT_FLAGS = ("--first", "--last", "--before", "--after")

# Flags hidden for reasons that have nothing to do with the provider:
# refused actions, accepted only to explain the refusal, and apply's
# undocumented --delimiter.
ALWAYS_HIDDEN = {"--redirect", "--notify", "--vacation", "--delimiter"}


class BareDialect(UnorderedDialect):
    """The unordered fake's dialect, on a host with nothing else either."""

    name = "bare"

    @classmethod
    @declined
    def disable_rule(cls, source, name):
        """No rule can be switched off."""

    @classmethod
    @declined
    def enable_rule(cls, source, name):
        """No rule can be switched on."""

    @classmethod
    @declined
    def count_support(cls, capabilities):
        """No folder counts to read."""


class BareTransport(FakeTransport):
    name = "bare"
    opened = 0

    @declined
    def add_flags(self, folder, uids, flags):
        """No message flags to set."""

    @declined
    def remove_flags(self, folder, uids, flags):
        """No message flags to clear."""

    @declined
    def folder_status(self, sizes):
        """No folder counts to read."""

    @declined
    def sort_messages(self, folder, order, criteria, expression):
        """No ordered search; the utilities sort what they fetch."""


# The fake with no connection settings, no ordering, no extensions.
BARE = Provider(
    "bare",
    ProviderCapabilities(
        ordering=False,
        stop=False,
        rule_sets=True,
        disable=False,
        actions=frozenset((FILEINTO, FLAG_ACTION, KEEP)),
        extensions=False,
        raw_query=False,
        mark=False,
        folder_counts=False,
        declined=frozenset(
            (
                "move_rule",
                "position",
                "disable_rule",
                "enable_rule",
                "add_flags",
                "remove_flags",
                "count_support",
                "folder_status",
                "sort_messages",
            )
        ),
    ),
    BareDialect,
    BareTransport,
)


# ----------------------------------------------------------------------------
@pytest.fixture
def bare(fakes, monkeypatch):
    monkeypatch.setitem(registry.PROVIDERS, BARE.name, BARE)
    monkeypatch.setattr(BareTransport, "opened", 0)


# ----------------------------------------------------------------------------
def help_text(capsys, *argv: str) -> str:
    """What ``mailctl ARGV --help`` prints."""
    with pytest.raises(SystemExit) as stopped:
        cli.main([*argv, "--help"])

    assert stopped.value.code == 0

    return capsys.readouterr().out


# ----------------------------------------------------------------------------
def offered_flags(parser: argparse.ArgumentParser) -> dict[str, set[str]]:
    """Every subcommand's flags, split into shown and hidden."""
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    found = {"shown": set(), "hidden": set()}

    for sub in subparsers.choices.values():
        for action in sub._actions:
            key = "hidden" if action.help == argparse.SUPPRESS else "shown"
            found[key].update(action.option_strings)

    return found


# ----------------------------------------------------------------------------
def test_mxroute_declares_everything_so_nothing_is_hidden():
    """Its help is the help every earlier version printed."""
    flags = offered_flags(cli.build_parser())

    assert flags["hidden"] - {"--verbose", "--debug"} == ALWAYS_HIDDEN
    assert {*PLACEMENT_FLAGS, "--no-stop", "--disable-extension", "--raw"} <= (
        flags["shown"]
    )
    assert {"--host", "--sieve-port", "--sieve-tls", "--imap-host"} <= (
        flags["shown"]
    )


# ----------------------------------------------------------------------------
def test_placement_is_not_offered_without_ordering(bare, capsys):
    text = help_text(capsys, "add", "--provider", "bare")

    for flag in PLACEMENT_FLAGS:
        assert flag not in text

    assert "--name" in text


# ----------------------------------------------------------------------------
def test_move_rule_is_not_listed_without_ordering(bare, capsys, monkeypatch):
    monkeypatch.setenv("MAILCTL_PROVIDER", "bare")

    text = help_text(capsys)
    usage = text.split("\n\n")[0]

    # remove-rule contains the name, so it is matched as a whole word.
    assert not re.search(r"(?<![\w-])move-rule", text)
    assert "remove-rule" in usage


# ----------------------------------------------------------------------------
def test_disable_and_enable_are_not_listed_without_disable(
    bare, capsys, monkeypatch
):
    listed = help_text(capsys)

    assert "disable-rule" in listed
    assert "enable-rule" in listed

    monkeypatch.setenv("MAILCTL_PROVIDER", "bare")
    text = help_text(capsys)

    assert "disable-rule" not in text
    assert "enable-rule" not in text


# ----------------------------------------------------------------------------
def test_the_bare_fake_declines_exactly_what_it_does_not_declare():
    assert declined_operations(BARE) == BARE.capabilities.declined


# ----------------------------------------------------------------------------
def test_a_switch_is_refused_by_a_host_without_disable(bare):
    """The utility holds the line for any front-end, not only the CLI."""
    session = fake_session(BARE)

    with pytest.raises(MailctlError, match="'disable' capability"):
        utilities.rules.plan_switch(session, "keep-boss", enable=False)

    assert session.opened == ()


# ----------------------------------------------------------------------------
def test_no_stop_is_not_offered_without_stop(bare, capsys):
    assert "--no-stop" not in help_text(capsys, "add", "--provider", "bare")
    assert "--no-stop" in help_text(capsys, "add")


# ----------------------------------------------------------------------------
def test_disable_extension_is_not_offered_without_extensions(bare, capsys):
    text = help_text(capsys, "list", "--provider", "bare")

    assert "--disable-extension" not in text


# ----------------------------------------------------------------------------
def test_raw_is_not_offered_without_raw_query(bare, capsys):
    assert "--raw" not in help_text(capsys, "search", "--provider", "bare")
    assert "--raw" in help_text(capsys, "search")


# ----------------------------------------------------------------------------
def test_a_raw_query_is_refused_by_a_provider_without_raw_query(fakes):
    """The utility holds the line for any front-end, not only the CLI."""
    session = fake_session(UNORDERED)

    with pytest.raises(MailctlError, match="'raw_query'"):
        utilities.messages.list_messages(session, "INBOX", raw="ALL")

    assert session.opened == ()


# ----------------------------------------------------------------------------
def test_bare_declines_exactly_what_it_says():
    """Held here, not in the registry-wide check, which runs before BARE
    is defined; without ``mark`` both flag writes are declined."""
    assert declined_operations(BARE) == BARE.capabilities.declined
    assert {"add_flags", "remove_flags"} <= BARE.capabilities.declined


# ----------------------------------------------------------------------------
def test_mark_is_listed_only_where_declared(bare, capsys, monkeypatch):
    listed = re.compile(r"[{,]mark[,}]")

    assert listed.search(help_text(capsys).split("\n\n")[0])

    monkeypatch.setenv("MAILCTL_PROVIDER", "bare")

    assert not listed.search(help_text(capsys).split("\n\n")[0])


# ----------------------------------------------------------------------------
def test_mark_is_refused_by_a_provider_without_mark(fakes):
    """The utility holds the line for any front-end, before connecting."""
    session = fake_session(BARE)

    with pytest.raises(MailctlError, match="'mark'"):
        utilities.flags.plan_mark(session, "INBOX", [7], ("\\Seen",))

    assert session.opened == ()


# ----------------------------------------------------------------------------
def test_the_fake_really_marked_and_unmarked_the_mail(fakes):
    session = fake_session()
    transport = fake_transport(session)

    plan = utilities.flags.plan_mark(
        session, "INBOX", [7, 8], ("\\Seen", "$Todo"), ()
    )
    utilities.flags.execute_mark(session, plan)

    assert transport.flags == {
        7: ("\\Seen", "$Todo"),
        8: ("\\Seen", "$Todo"),
    }

    plan = utilities.flags.plan_mark(session, "INBOX", [7], (), ("$Todo",))
    utilities.flags.execute_mark(session, plan)

    assert transport.flags[7] == ("\\Seen",)
    assert transport.flags[8] == ("\\Seen", "$Todo")


# ----------------------------------------------------------------------------
def test_counts_are_offered_only_where_declared(bare, capsys, monkeypatch):
    assert "--counts" in help_text(capsys, "folders")

    monkeypatch.setenv("MAILCTL_PROVIDER", "bare")

    assert "--counts" not in help_text(capsys, "folders")


# ----------------------------------------------------------------------------
def test_counts_are_refused_by_a_provider_without_folder_counts(fakes):
    """The utility holds the line for any front-end, before connecting."""
    session = fake_session(BARE)

    with pytest.raises(MailctlError, match="'folder_counts'"):
        utilities.folders.list_folder_counts(session)

    assert session.opened == ()
    assert {"count_support", "folder_status"} <= BARE.capabilities.declined


# ----------------------------------------------------------------------------
def test_the_fake_counts_its_folders_in_its_own_terms(fakes):
    counts = utilities.folders.list_folder_counts(fake_session())

    assert counts.sizes
    assert [
        (s.folder, s.messages, s.unseen, s.size) for s in counts.statuses
    ] == [("INBOX", 2, 1, 512), ("INBOX.Lists", 0, 0, 0)]


# ----------------------------------------------------------------------------
def test_connection_flags_are_the_providers_own(bare, capsys, monkeypatch):
    """A host that reads no --sieve-port does not offer one, and its help
    says nothing about MXroute."""
    monkeypatch.setenv("MAILCTL_PROVIDER", "bare")

    text = help_text(capsys, "list")

    for flag in ("--sieve-port", "--sieve-tls", "--imap-host", "--host "):
        assert flag not in text

    assert "MXRoute" not in text
    assert "--user" in text


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("argv", "refusal"),
    [
        (
            ["add", "--from", GITHUB, "--fileinto", "L", "--first"],
            "'ordering'",
        ),
        (["move-rule", "x", "--first"], "'ordering'"),
        (["disable-rule", "x", "--yes"], "'disable'"),
        (["enable-rule", "x", "--yes"], "'disable'"),
        (["list", "--sieve-port", "4190"], "flag --sieve-port"),
        (["list", "--disable-extension", "mailbox"], "disabled_extensions"),
        (["search", "--raw", "ALL"], "'raw_query'"),
        (["mark", "7", "--flag"], "'mark'"),
        (["folders", "--counts"], "'folder_counts'"),
    ],
    ids=[
        "placement",
        "move-rule",
        "disable-rule",
        "enable-rule",
        "connection-flag",
        "disable-extension",
        "raw-query",
        "mark",
        "folder-counts",
    ],
)
def test_a_hidden_option_given_anyway_is_refused_by_name(
    bare, capsys, argv, refusal
):
    """Refusal stays the backstop: named, and before any connection."""
    assert cli.main([*argv, "--provider", "bare"]) == 1

    error = capsys.readouterr().err

    assert "the bare provider cannot" in error
    assert refusal in error
    assert BareTransport.opened == 0


# ----------------------------------------------------------------------------
def test_an_unreadable_selection_falls_back_to_offering_everything(
    capsys, monkeypatch
):
    """Help is never where a bad setting is reported; the run is."""
    monkeypatch.setenv("MAILCTL_PROVIDER", "no-such-provider")

    assert "--first" in help_text(capsys, "add")


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "argv",
    [
        ["help", "add", "--provider", "bare"],
        ["help", "--provider", "bare", "add"],
    ],
)
def test_help_shows_the_selected_providers_offer(bare, capsys, argv):
    """#153: 'help add' resolves the provider as 'add --help' does."""
    with pytest.raises(SystemExit) as stopped:
        cli.main(argv)

    assert stopped.value.code == 0

    text = capsys.readouterr().out

    assert text == help_text(capsys, "add", "--provider", "bare")
    assert text != help_text(capsys, "add")
    assert "--first" not in text
    assert BareTransport.opened == 0


# ############################################################################
# Criteria a host's rules cannot test (#152)
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_host_that_cannot_test_the_body_refuses_it_by_name(fakes):
    """The dialect answers for its own rule language: the fake keeps only
    From, so a body test is refused naming the provider, before anything
    is stored."""
    request = github_rule()
    request.criteria.add_body("merged")
    session = fake_session()
    before = dict(fake_transport(session).scripts)
    folder = utilities.folders.plan_folder(session, Config(), "Lists")

    with pytest.raises(MailctlError, match="the fake provider cannot test"):
        utilities.rules.plan_rule(session, Config(), request, folder)

    assert fake_transport(session).scripts == before


# ############################################################################
# Sorting a listing (#159)
# ############################################################################


# ----------------------------------------------------------------------------
def sorted_listing(session: Session):
    """``search --sort size --reverse --limit 1`` for GitHub's mail."""
    criteria = Criteria()
    criteria.add("From", GITHUB)

    return utilities.messages.list_messages(
        session,
        "INBOX",
        criteria=criteria,
        limit=1,
        order=model.SortOrder(model.SORT_SIZE, reverse=True),
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("server", [True, False], ids=["sorts", "does-not"])
def test_a_sorted_listing_makes_the_same_calls_whichever_provider(
    fake_imap, imap_session, fakes, server
):
    """Whether the host sorts is the dialect's reading of what the mail
    half advertises; the utility then makes the same calls on either
    provider -- one ordered search, or a search and a local sort."""
    fake_imap.messages = {7: github_message(7), 8: github_message(8)}
    fake = fake_session()

    if server:
        fake_imap.caps.add("SORT")
        fake_transport(fake).mail_caps = ["ORDERED"]

    real = Recorder(mxroute(imap=imap_session))
    recorded = Recorder(fake)

    sorted_listing(real.session)
    sorted_listing(recorded.session)

    names = [call[0] for call in real.calls]

    assert ("transport.sort_messages" in names) is server
    assert ("transport.search" in names) is not server
    assert recorded.calls == real.calls


# ----------------------------------------------------------------------------
def test_the_fake_sorts_by_its_own_sizes(fakes):
    """The fake is a working host: its largest GitHub message is listed,
    though it is not the newest, on either path."""
    for caps in ([], ["ORDERED"]):
        session = fake_session()
        transport = fake_transport(session)
        transport.mail_caps = caps
        transport.messages[6] = transport.messages[7] + "Body: long\r\n" * 5

        listing = sorted_listing(session)

        assert [message.uid for message in listing.messages] == [6], caps
        assert listing.more is True


# ----------------------------------------------------------------------------
def test_a_host_that_cannot_sort_is_sorted_by_the_utilities(bare):
    """``bare`` declines the ordered search outright; its dialect never
    says it sorts, so the utility sorts what it fetched and the declined
    operation is never called."""
    session = fake_session(BARE)
    transport = fake_transport(session)
    transport.messages[6] = transport.messages[7] + "Body: long\r\n" * 5

    listing = sorted_listing(session)

    assert [message.uid for message in listing.messages] == [6]

    with pytest.raises(MailctlError, match="the bare provider cannot"):
        transport.sort_messages("INBOX", model.SortOrder("size"), None, None)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("advertised", "sorts"),
    [
        (["IMAP4REV1", "SORT", "SORT=DISPLAY"], True),
        (["imap4rev1", "sort"], True),
        (["IMAP4REV1", "ESEARCH", "THREAD=REFS"], False),
        ([], False),
    ],
)
def test_mxroute_sorts_on_the_server_where_it_advertises_sort(
    advertised, sorts
):
    """Read at runtime from the capability list, never assumed."""
    assert MxrouteDialect.sorts_messages(advertised) is sorts


# ############################################################################
# A folder rename, on a host with nothing in common with MXroute (#5)
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_folder_rename_runs_through_a_second_provider(fakes, tmp_path):
    """The rename is utilities over the interface: the fake's own folder
    move, subscription list, and JSON rules all follow it."""
    session = fake_session()
    transport = fake_transport(session)
    transport.scripts["main"] = json.dumps(
        [
            {"name": "lists", "from": "x", "actions": ["file:INBOX.Lists"]},
            _stored("keep-boss", "boss"),
        ]
    )
    config = Config(backup_dir=tmp_path)

    plan = utilities.folder_rename.plan_folder_rename(
        session, "Lists", "Archive"
    )
    result = utilities.folder_rename.execute_folder_rename(
        session, config, plan
    )

    assert transport.folders == ["INBOX", "INBOX.Archive"]
    assert "INBOX.Archive" in transport.subscribed
    assert "INBOX.Lists" not in transport.subscribed
    assert [
        reference.folder
        for reference in FakeDialect.folder_references(
            transport.scripts["main"]
        )
    ] == ["INBOX.Archive", "x"]
    assert result.ok, result.checks
