"""Rules: checked, read, planned into a script, and uploaded.

Merge, never overwrite: a new rule is merged into the parsed existing
script, and a parse failure is raised rather than fallen back from
(ADR 0002). Every upload goes through ``scripts.upload_script``, which
backs up first.
"""

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from .. import MailctlError
from ..config import Config
from ..criteria import Criteria
from ..engine import Session

# The PLACE_* names are re-exported (``X as X``): a front-end builds and
# renders the neutral model through the utilities alone, never importing a
# provider or a component.
from ..providers.base import (
    PLACE_AFTER as PLACE_AFTER,
)
from ..providers.base import (
    PLACE_BEFORE as PLACE_BEFORE,
)
from ..providers.base import (
    PLACE_FIRST as PLACE_FIRST,
)
from ..providers.base import (
    PLACE_LAST as PLACE_LAST,
)
from ..providers.base import (
    ActionSpec,
    DisplayDiff,
    Placement,
    Provider,
    action_names,
    refuse,
    validate_specifics,
)
from ..providers.registry import provider_for
from ..rules import Analysis, Rule, Shadow, analyze_placement, audit
from .events import EventSink
from .folders import FolderPlan, check_folder, realize_folder
from .scripts import (
    DEFAULT_SCRIPT_NAME,
    activates,
    fetch_active,
    upload_script,
)

# ############################################################################
# Inputs
# ############################################################################


@dataclass(frozen=True)
class RuleRequest:
    """A rule to merge into the account's rule set.

    ``script``, ``activate``, and ``placement`` need a provider declaring
    ``rule_sets`` and ``ordering``. ``specifics`` carries provider-only
    parameters under namespaced keys, checked against the provider's
    schema before any network work (:func:`check_rule`).
    """

    criteria: Criteria
    actions: ActionSpec
    name: str | None = None
    script: str | None = None
    replace: bool = False
    placement: Placement | None = None
    activate: bool = False
    specifics: Mapping[str, object] = field(default_factory=dict)


# ############################################################################
# Capabilities a request needs
# ############################################################################


# ----------------------------------------------------------------------------
def check_move(config: Config) -> None:
    """Refuse moving a rule under a provider without ``ordering``.

    Needs no connection, so a front-end calls it before connecting;
    :func:`plan_move` holds the same line for any other caller.
    """
    require_capability(provider_for(config), "ordering")


# ----------------------------------------------------------------------------
def check_switch(config: Config) -> None:
    """Refuse disabling or enabling a rule under a provider without
    ``disable``.

    Needs no connection, so a front-end calls it before connecting;
    :func:`plan_switch` holds the same line for any other caller.
    """
    require_capability(provider_for(config), "disable")


# ----------------------------------------------------------------------------
def require_capability(provider: Provider | Session, name: str) -> None:
    """Refuse work needing a capability the provider does not declare."""
    if getattr(provider.capabilities, name):
        return

    raise refuse(
        provider.name,
        CAPABILITY_CONSTRUCTS[name],
        f"it does not declare the {name!r} capability",
    )


# What a request asks for, in words, when it needs each capability.
CAPABILITY_CONSTRUCTS = {
    "disable": "switch a rule off or on without removing it",
    "folder_counts": "count the messages in every folder in one request",
    "mark": "set or clear a message's flags",
    "ordering": "place a rule at a position in evaluation order",
    "raw_query": "search with a query in its own search language",
    "rule_sets": "name or activate one of several rule sets",
    "stop": "end evaluation after a rule",
}


# ############################################################################
# Reading rules
# ############################################################################


@dataclass(frozen=True)
class RulesReport:
    """A script's rules in evaluation order, and which cannot fire."""

    script: str
    rules: list[Rule]
    findings: list[Shadow]


# ----------------------------------------------------------------------------
def read_rules(session: Session, script: str | None = None) -> RulesReport:
    """Read a script's rules and audit their order.

    The audit is about order, so a provider that does not declare
    ``ordering`` has nothing for it to find.
    """
    name = script or session.transport.active_rule_set()

    if not name:
        raise MailctlError(
            "no active script on the server, so there are no rules to show.",
            code="no_active_script",
            fields={"operation": "list"},
        )

    rules = session.dialect.read_rules(session.transport.read_rule_set(name))
    findings = audit(rules) if session.capabilities.ordering else []

    return RulesReport(name, rules, findings)


# ############################################################################
# Actions
# ############################################################################


# ----------------------------------------------------------------------------
def reject_actions(config: Config, requested: Iterable[str]) -> None:
    """Refuse actions the configured provider will not emit, and say why.

    Needs no connection, so a front-end calls it before connecting.
    """
    provider_for(config).dialect.refuse_actions(requested)


# ----------------------------------------------------------------------------
def check_rule(
    config: Config,
    request: RuleRequest,
    provider: Provider | Session | None = None,
) -> None:
    """Refuse a rule the provider cannot express, before any network work.

    Every refusal goes through one error (``providers.base.refuse``) naming
    the provider, the construct, and why. ``provider`` defaults to the one
    ``config`` selects; :func:`plan_rule` passes its own.
    """
    provider = provider or provider_for(config)
    caps = provider.capabilities

    folder = request.actions.fileinto or config.default_folder or ""
    requested = action_names(request.actions, folder)

    if not requested:
        raise MailctlError("no action requested", code="no_action")

    unsupported = requested - caps.actions

    if unsupported:
        raise refuse(
            provider.name,
            f"emit {', '.join(sorted(unsupported))}",
            f"it declares only {', '.join(sorted(caps.actions)) or 'none'}",
        )

    if request.placement is not None:
        require_capability(provider, "ordering")

    if request.script or request.activate:
        require_capability(provider, "rule_sets")

    if request.actions.stop:
        require_capability(provider, "stop")

    validate_specifics(provider.name, caps.specifics, request.specifics)

    request.criteria.check_deliverable()


# ----------------------------------------------------------------------------
def resolve_stop(provider: Provider | Session, spec: ActionSpec) -> ActionSpec:
    """Settle a spec's ``stop`` default from the provider's capabilities.

    None asks for the provider's default, which is to stop where it
    declares ``stop``: a host that cannot end evaluation is not asked to,
    so a rule nobody asked to stop is never refused for it. An explicit
    True or False is left as it was.
    """
    if spec.stop is not None:
        return spec

    return replace(spec, stop=provider.capabilities.stop)


# ----------------------------------------------------------------------------
def default_rule_name(criteria: Criteria) -> str:
    """Derive a stable rule name from the first criterion.

    Letters and digits in any script are kept (``Café`` gives
    ``subject-café``): Roundcube's ``# rule:[...]`` marker holds UTF-8, so
    there is nothing to gain from dropping them. NFC first, so an accent
    typed as a combining mark stays on its letter instead of becoming a
    separator. With no header term, the first body test names it
    (``body-...``).
    """
    if criteria.terms:
        label, value = criteria.terms[0].header, criteria.terms[0].value

    else:
        label, value = "body", criteria.body[0]

    value = unicodedata.normalize("NFC", value)
    slug = re.sub(r"[\W_]+", "-", value).strip("-").lower()

    return f"{label.lower()}-{slug}"[:60] or DEFAULT_SCRIPT_NAME


# ############################################################################
# Adding and removing rules
# ############################################################################


@dataclass(frozen=True)
class RulePlan:
    """A rule merged into the script, not yet uploaded.

    ``actions`` are the provider's own and opaque here; ``summary`` is the
    provider's rendering of them for a person to read.
    """

    name: str
    script: str
    before: str
    after: str
    criteria: Criteria
    actions: list
    summary: str
    placement: Analysis
    diff: DisplayDiff
    folder: FolderPlan
    active: str | None
    activate: bool


@dataclass(frozen=True)
class RemovalPlan:
    """A rule taken out of the script, not yet uploaded."""

    rule: str
    script: str
    before: str
    after: str
    diff: DisplayDiff
    active: str | None
    activate: bool


@dataclass(frozen=True)
class MovePlan:
    """A rule moved within the script, not yet uploaded.

    Positions are 0-based over the whole script. ``placement`` judges the
    rule where it lands, both ways: what would stop it running, and what it
    would now stop.
    """

    rule: str
    script: str
    before: str
    after: str
    from_index: int
    to_index: int
    count: int
    placement: Analysis
    diff: DisplayDiff
    active: str | None
    activate: bool

    # ------------------------------------------------------------------------
    @property
    def changes(self) -> bool:
        return self.from_index != self.to_index


@dataclass(frozen=True)
class SwitchPlan:
    """A rule switched off or on, not yet uploaded.

    ``enable`` is the state asked for. A rule already in it leaves the
    script as it was, and ``changes`` is False.
    """

    rule: str
    enable: bool
    script: str
    before: str
    after: str
    diff: DisplayDiff
    active: str | None
    activate: bool

    # ------------------------------------------------------------------------
    @property
    def changes(self) -> bool:
        return self.after != self.before


# ----------------------------------------------------------------------------
def missing_extensions(
    session: Session, spec: ActionSpec, folder: FolderPlan
) -> list[str]:
    """Return the extensions the rule needs that the server does not list."""
    needed = session.dialect.required_features(
        resolve_stop(session, spec), folder.folder, folder.use_create
    )

    return session.dialect.missing_features(
        needed, session.transport.rules_capabilities()
    )


# ----------------------------------------------------------------------------
def placement_analysis(
    provider: Provider | Session,
    before: str,
    name: str,
    criteria: Criteria,
    actions: list,
    placement: Placement | None = None,
) -> Analysis:
    """Judge a rule at the position it will actually occupy.

    A rule of the same name is dropped from the comparison set first. With
    a replace the old copy is being overwritten, so leaving it in would
    have the new rule shadowed by the version it replaces -- and would put
    the indexes out by one, since ``position`` counts the other rules only.

    A provider that does not declare ``ordering`` evaluates every rule on
    its own, so no position can shadow one and the analysis is empty.
    """
    if not provider.capabilities.ordering:
        return Analysis()

    dialect = provider.dialect
    present = dialect.read_rules(before)
    rules = [entry for entry in present if entry.name != name]

    candidate = dialect.candidate_rule(name, criteria, actions)

    # Resolved against every name in the script, including the one being
    # replaced -- that is how "no placement, so leave it where it is" finds
    # where it currently is.
    #
    # NOT rule_from_criteria's index=-1 default: analyze_placement clamps
    # with max(0, at_index), so a -1 here would mean the FRONT of the
    # script rather than the end of it.
    at_index = dialect.position(
        [entry.name for entry in present], placement, name
    )

    return analyze_placement(rules, candidate, at_index=at_index)


# ----------------------------------------------------------------------------
def plan_rule(
    session: Session,
    config: Config,
    request: RuleRequest,
    folder: FolderPlan,
) -> RulePlan:
    """Merge a rule into the active script without uploading it.

    Never overwrites: the rule is merged into the parsed existing script,
    and a parse failure is raised rather than fallen back from (ADR 0002).
    A rule the provider cannot express is refused before the script is
    read (:func:`check_rule`). A rule needing an extension named in
    ``disabled_extensions`` is refused; the one with a fallback,
    ``mailbox``, was already dropped by :func:`plan_folder`. Criteria the
    host's rules cannot test -- a body test on a server without the
    feature, say -- are refused too, since a test has no fallback.
    """
    check_rule(config, request, session)
    request.criteria.require_terms()
    check_folder(session, folder)

    dialect = session.dialect
    actions = dialect.translate_actions(
        resolve_stop(session, request.actions),
        folder.folder,
        folder.use_create,
    )
    dialect.check_actions(config, actions)
    dialect.check_criteria(
        config, request.criteria, session.transport.rules_capabilities()
    )
    name = request.name or default_rule_name(request.criteria)
    script, before, active = fetch_active(session, request.script)

    after = dialect.add_rule(
        before,
        name,
        request.criteria,
        actions,
        replace=request.replace,
        placement=request.placement,
    )

    return RulePlan(
        name=name,
        script=script,
        before=before,
        after=after,
        criteria=request.criteria,
        actions=actions,
        summary=dialect.describe_actions(actions),
        placement=placement_analysis(
            session,
            before,
            name,
            request.criteria,
            actions,
            request.placement,
        ),
        diff=dialect.diff(before, after, script),
        folder=folder,
        active=active,
        activate=activates(script, active, request.activate),
    )


# ----------------------------------------------------------------------------
def plan_removal(
    session: Session,
    rule: str,
    script: str | None = None,
    activate: bool = False,
) -> RemovalPlan:
    """Take a named rule out of the script without uploading the result."""
    name, before, active = fetch_active(session, script)

    if not before.strip():
        raise MailctlError(f"script {name!r} is empty")

    after = session.dialect.remove_rule(before, rule)

    return RemovalPlan(
        rule,
        name,
        before,
        after,
        session.dialect.diff(before, after, name),
        active,
        activates(name, active, activate),
    )


# ----------------------------------------------------------------------------
def plan_move(
    session: Session,
    rule: str,
    placement: Placement,
    script: str | None = None,
    activate: bool = False,
) -> MovePlan:
    """Reorder a named rule without restating it, and without uploading.

    Refused, before the script is read, by a provider that does not
    declare ``ordering``.
    """
    require_capability(session, "ordering")

    name, before, active = fetch_active(session, script)

    if not before.strip():
        raise MailctlError(f"script {name!r} is empty")

    dialect = session.dialect
    after = dialect.move_rule(before, rule, placement)

    present = dialect.read_rules(before)
    names = [entry.name for entry in present]
    from_index = names.index(rule)
    to_index = dialect.position(names, placement, rule)

    candidate = present[from_index]
    others = present[:from_index] + present[from_index + 1 :]

    return MovePlan(
        rule=rule,
        script=name,
        before=before,
        after=after,
        from_index=from_index,
        to_index=to_index,
        count=len(present),
        placement=analyze_placement(others, candidate, at_index=to_index),
        diff=dialect.diff(before, after, name),
        active=active,
        activate=activates(name, active, activate),
    )


# ----------------------------------------------------------------------------
def plan_switch(
    session: Session,
    rule: str,
    enable: bool,
    script: str | None = None,
    activate: bool = False,
) -> SwitchPlan:
    """Switch a named rule off (``enable`` False) or on, without uploading.

    The rule is kept either way; only whether it runs changes. Refused,
    before the script is read, by a provider that does not declare
    ``disable``.
    """
    require_capability(session, "disable")

    name, before, active = fetch_active(session, script)

    if not before.strip():
        raise MailctlError(f"script {name!r} is empty")

    dialect = session.dialect
    switch = dialect.enable_rule if enable else dialect.disable_rule
    after = switch(before, rule)

    return SwitchPlan(
        rule=rule,
        enable=enable,
        script=name,
        before=before,
        after=after,
        diff=dialect.diff(before, after, name),
        active=active,
        activate=activates(name, active, activate),
    )


# ----------------------------------------------------------------------------
def execute_script_change(
    session: Session,
    config: Config,
    plan: RulePlan | RemovalPlan | MovePlan | SwitchPlan,
    on_event: EventSink | None = None,
) -> Path:
    """Upload a planned rule change; return the backup's path.

    A rule's target folder, when it is planned for IMAP creation, is
    created here -- after the server has accepted the script and before it
    is stored -- so neither a dry run nor a rejected script leaves a stray
    folder, and the rule never goes live pointing at a missing one.
    """
    before_put: Callable[[], None] | None = None

    if isinstance(plan, RulePlan):
        session.dialect.check_actions(config, plan.actions)
        session.dialect.check_criteria(
            config, plan.criteria, session.transport.rules_capabilities()
        )
        folder = plan.folder

        def create_target() -> None:
            realize_folder(session, folder, on_event)

        before_put = create_target

    return upload_script(
        session,
        config,
        plan.script,
        plan.before,
        plan.after,
        on_event,
        before_put,
        activate=plan.activate,
    )
