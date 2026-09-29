"""Rule-set optimization: a better arrangement proposed, then applied (#21).

Three kinds of change, each made only where the rules' own conditions
decide it -- the CERTAIN tier of ``mailctl.rules`` (one header, one
comparator, one key set containing the other). Anything less certain is
reported and left alone: reordering a working filter set on a guess is
worse than leaving a suboptimal one.

* **Redundant** -- a rule an earlier rule covers, the earlier one carrying
  ``stop`` and doing exactly the same thing. It can never run, and nothing
  would change if it did, so it is removed.
* **Reorder** -- a rule an earlier, broader rule covers and stops, doing
  something else. It never runs where it is; it is moved to just before
  the first rule covering it, so the specific rule precedes its container.
  That is a change of behaviour, and the one the move exists to make: its
  mail gets its own actions instead of the broader rule's. Nothing else
  changes, since that broader rule already stopped all of that mail
  before any rule it now passes.
* **Merge** -- consecutive rules testing the same header the same way,
  with the same actions and ``stop``, become one rule with a key list.
  A key list is an OR, so the one rule files exactly what the several did.
  Only a disabled rule may sit between them; a live one might catch some
  of the same mail first.

Disabled rules are never merged, moved, or removed, and a rule mailctl
cannot fully read is never the basis of a change. Plan, then execute:
``plan_optimize`` is read-only and returns the proposals and a diff;
``execute_optimize`` uploads through ``scripts.upload_script``, which
backs up first. The result stays a flat list of rules -- Roundcube
co-edits the script (ADR 0002).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from .. import MailctlError
from ..config import Config
from ..engine import Session
from ..providers.base import DisplayDiff
from ..providers.registry import provider_for
from ..rules import CERTAIN, POSSIBLE, Rule, Slot, covers
from .events import EventSink
from .rules import require_capability
from .scripts import activates, fetch_active, upload_script

REDUNDANT = "redundant"
REORDER = "reorder"
MERGE = "merge"
KINDS = (REDUNDANT, REORDER, MERGE)

# ############################################################################
# Proposals
# ############################################################################


@dataclass(frozen=True)
class Removal:
    """A rule that can never run and would do nothing new if it did."""

    rule: str
    covered_by: str


@dataclass(frozen=True)
class Reorder:
    """A rule moved to just before the broader rule that starved it."""

    rule: str
    before: str


@dataclass(frozen=True)
class Merge:
    """Rules folded into the first of them, which keeps its name."""

    into: str
    absorbed: tuple[str, ...]
    header: str
    match_type: str
    keys: tuple[str, ...]


@dataclass(frozen=True)
class Uncertain:
    """A change that might be right, which mailctl will not make on a
    guess. ``kind`` is the kind of change it would have been."""

    kind: str
    rules: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class Proposals:
    """What to change, and the layout that makes it.

    ``layout`` is the new order as slots over the rules as read; it
    names every rule when nothing changes.
    """

    removals: tuple[Removal, ...]
    reorders: tuple[Reorder, ...]
    merges: tuple[Merge, ...]
    uncertain: tuple[Uncertain, ...]
    layout: tuple[Slot, ...]

    # ------------------------------------------------------------------------
    @property
    def changes(self) -> bool:
        return bool(self.removals or self.reorders or self.merges)


# ----------------------------------------------------------------------------
def check_kinds(kinds: Iterable[str]) -> tuple[str, ...]:
    """Refuse a kind of change that is not one of :data:`KINDS`."""
    kinds = tuple(kinds)
    unknown = sorted(set(kinds) - set(KINDS))

    if unknown:
        raise MailctlError(
            f"unknown kind of change: {', '.join(unknown)}. Known: "
            f"{', '.join(KINDS)}"
        )

    return kinds


# ----------------------------------------------------------------------------
def _stopping_covers(
    rules: Sequence[Rule], order: list[int], position: int
) -> tuple[int | None, int | None]:
    """The first live rule ahead of ``position`` that carries stop and
    certainly covers the rule there, and the first that possibly does."""
    narrow = rules[order[position]]
    possible = None

    for index in order[:position]:
        broad = rules[index]

        if broad.disabled or not broad.stops:
            continue

        verdict = covers(broad, narrow)

        if verdict == CERTAIN:
            return index, possible

        if verdict == POSSIBLE and possible is None:
            possible = index

    return None, possible


# ----------------------------------------------------------------------------
def _remove_redundant(
    rules: Sequence[Rule], order: list[int]
) -> list[Removal]:
    removals = []

    for index in list(order):
        rule = rules[index]

        if rule.disabled or not rule.effects:
            continue

        broad, _ = _stopping_covers(rules, order, order.index(index))

        if broad is not None and rules[broad].effects == rule.effects:
            order.remove(index)
            removals.append(Removal(rule.name, rules[broad].name))

    return removals


# ----------------------------------------------------------------------------
def _reorder(
    rules: Sequence[Rule], order: list[int]
) -> tuple[list[Reorder], list[Uncertain]]:
    """Move each starved rule to just before its first stopping cover,
    until none is left that can be moved."""
    reorders: list[Reorder] = []
    uncertain: list[Uncertain] = []
    settled: set[int] = set()

    # Each move puts a strictly narrower rule ahead of a broader one, so
    # this ends; the bound only keeps a wrong `covers` from looping.
    for _ in range(len(order) ** 2 + 1):
        found = None

        for position, index in enumerate(order):
            if index in settled or rules[index].disabled:
                continue

            broad, _ = _stopping_covers(rules, order, position)

            if broad is not None:
                found = (index, broad)

                break

        if found is None:
            break

        index, broad = found
        narrow_rule, broad_rule = rules[index], rules[broad]

        if narrow_rule.effects == broad_rule.effects:
            # Redundant, and not asked to be removed: moving it changes
            # nothing, so it stays where it is.
            settled.add(index)

        elif covers(narrow_rule, broad_rule) == CERTAIN:
            settled.add(index)
            uncertain.append(
                Uncertain(
                    REORDER,
                    (broad_rule.name, narrow_rule.name),
                    f"{broad_rule.name!r} and {narrow_rule.name!r} test for "
                    f"the same mail and do different things, so only the "
                    f"first can run; which should win is yours to say",
                )
            )

        else:
            order.remove(index)
            order.insert(order.index(broad), index)
            reorders.append(Reorder(narrow_rule.name, broad_rule.name))

    for position, index in enumerate(order):
        rule = rules[index]

        if rule.disabled:
            continue

        broad, possible = _stopping_covers(rules, order, position)

        # A narrower rule ahead of a broader one is the order asked for,
        # not a doubt about it.
        if (
            broad is None
            and possible is not None
            and covers(rule, rules[possible]) != CERTAIN
        ):
            uncertain.append(
                Uncertain(
                    REORDER,
                    (rules[possible].name, rule.name),
                    f"{rules[possible].name!r} carries stop and may catch "
                    f"mail meant for {rule.name!r} first; whether it does "
                    f"cannot be decided from the rules alone",
                )
            )

    return reorders, uncertain


# ----------------------------------------------------------------------------
def _merge_key(rule: Rule) -> tuple | None:
    """What two rules must share to become one, or None if this one
    cannot be merged at all: one header test, fully read, enabled."""
    if rule.disabled or not rule.modelled or len(rule.tests) != 1:
        return None

    if not rule.effects:
        return None

    (test,) = rule.tests

    return (
        test.header.lower(),
        test.match_type,
        test.comparator,
        rule.effects,
    )


# ----------------------------------------------------------------------------
def _merge(
    rules: Sequence[Rule], order: list[int]
) -> tuple[dict[int, list[int]], list[Merge], list[Uncertain]]:
    """Group runs of mergeable rules; disabled rules do not break a run."""
    runs: list[list[int]] = []
    current: tuple | None = None
    # The last rule seen with each merge key, for a pair a live rule
    # keeps apart.
    last: dict[tuple, str] = {}
    uncertain: list[Uncertain] = []

    for index in order:
        rule = rules[index]

        if rule.disabled:
            continue

        key = _merge_key(rule)

        if key is not None and key == current:
            runs[-1].append(index)

        elif key is not None:
            if key in last:
                uncertain.append(
                    Uncertain(
                        MERGE,
                        (last[key], rule.name),
                        f"{last[key]!r} and {rule.name!r} do the same "
                        f"thing, but a rule between them may catch some of "
                        f"the same mail first",
                    )
                )

            runs.append([index])

        current = key

        if key is not None:
            last[key] = rule.name

    absorbs: dict[int, list[int]] = {}
    merges: list[Merge] = []

    for run in runs:
        if len(run) < 2:
            continue

        first = rules[run[0]]

        if not first.stops:
            uncertain.append(
                Uncertain(
                    MERGE,
                    tuple(rules[index].name for index in run),
                    "they do the same thing but do not stop, so a message "
                    "matching more than one gets the actions more than "
                    "once today; merged, it would get them once",
                )
            )

            continue

        keys: list[str] = []

        for index in run:
            keys += [
                key for key in rules[index].tests[0].keys if key not in keys
            ]

        absorbs[run[0]] = run[1:]
        merges.append(
            Merge(
                first.name,
                tuple(rules[index].name for index in run[1:]),
                first.tests[0].header,
                first.tests[0].match_type,
                tuple(keys),
            )
        )

    return absorbs, merges, uncertain


# ----------------------------------------------------------------------------
def propose(rules: Sequence[Rule], kinds: Iterable[str] = KINDS) -> Proposals:
    """Work out a better arrangement of ``rules``, offline.

    ``rules`` is a rule set as its dialect reads it, in order. Only the
    ``kinds`` of change named are made; redundant rules go first, then
    reorders, then merges over the order that leaves.
    """
    kinds = check_kinds(kinds)
    order = list(range(len(rules)))
    removals: list[Removal] = []
    reorders: list[Reorder] = []
    uncertain: list[Uncertain] = []
    absorbs: dict[int, list[int]] = {}
    merges: list[Merge] = []

    if REDUNDANT in kinds:
        removals = _remove_redundant(rules, order)

    if REORDER in kinds:
        reorders, found = _reorder(rules, order)
        uncertain += found

    if MERGE in kinds:
        absorbs, merges, found = _merge(rules, order)
        uncertain += found

    absorbed = {index for members in absorbs.values() for index in members}
    layout = tuple(
        Slot(index, tuple(absorbs.get(index, ())))
        for index in order
        if index not in absorbed
    )

    return Proposals(
        tuple(removals),
        tuple(reorders),
        tuple(merges),
        tuple(uncertain),
        layout,
    )


# ############################################################################
# The plan
# ############################################################################


@dataclass(frozen=True)
class OptimizePlan:
    """A rule set rearranged, not yet uploaded."""

    script: str
    before: str
    after: str
    rules: tuple[Rule, ...]
    proposals: Proposals
    kinds: tuple[str, ...]
    diff: DisplayDiff
    active: str | None
    activate: bool

    # ------------------------------------------------------------------------
    @property
    def changes(self) -> bool:
        return self.proposals.changes


# ----------------------------------------------------------------------------
def check_optimize(config: Config) -> None:
    """Refuse optimizing under a provider without ``ordering``.

    Needs no connection, so a front-end calls it before connecting;
    :func:`plan_optimize` holds the same line for any other caller.
    """
    require_capability(provider_for(config), "ordering")


# ----------------------------------------------------------------------------
def expected_rules(rules: Sequence[Rule], proposals: Proposals) -> list[Rule]:
    """The rules the rearranged set must read back as, positions aside."""
    keys = {merge.into: merge.keys for merge in proposals.merges}
    expected = []

    for slot in proposals.layout:
        rule = replace(rules[slot.index], index=0)

        if slot.absorbs:
            (test,) = rule.tests
            rule = replace(rule, tests=(replace(test, keys=keys[rule.name]),))

        expected.append(rule)

    return expected


# ----------------------------------------------------------------------------
def plan_optimize(
    session: Session,
    script: str | None = None,
    kinds: Iterable[str] = KINDS,
    activate: bool = False,
) -> OptimizePlan:
    """Propose a better arrangement of the active script; change nothing.

    The rearranged script is read back through the dialect before it is
    offered, and refused unless it is exactly the rules proposed -- the
    same rules, tests, and actions, in the proposed order. Refused,
    before the script is read, by a provider without ``ordering``.
    """
    require_capability(session, "ordering")
    kinds = check_kinds(kinds)

    name, before, active = fetch_active(session, script)

    if not before.strip():
        raise MailctlError(f"script {name!r} is empty")

    dialect = session.dialect
    rules = dialect.read_rules(before)
    proposals = propose(rules, kinds)
    after = before

    if proposals.changes:
        after = dialect.rearrange_rules(before, proposals.layout)
        landed = [replace(rule, index=0) for rule in dialect.read_rules(after)]

        if landed != expected_rules(rules, proposals):
            raise MailctlError(
                f"the rearranged script {name!r} does not read back as the "
                f"rules proposed, so nothing is offered; this is a mailctl "
                f"defect, not a problem with your script"
            )

    return OptimizePlan(
        script=name,
        before=before,
        after=after,
        rules=tuple(rules),
        proposals=proposals,
        kinds=kinds,
        diff=dialect.diff(before, after, name),
        active=active,
        activate=activates(name, active, activate),
    )


# ----------------------------------------------------------------------------
def execute_optimize(
    session: Session,
    config: Config,
    plan: OptimizePlan,
    on_event: EventSink | None = None,
) -> Path:
    """Upload a planned rearrangement; return the backup's path."""
    if not plan.changes:
        raise MailctlError(f"nothing to change in {plan.script!r}")

    return upload_script(
        session,
        config,
        plan.script,
        plan.before,
        plan.after,
        on_event,
        activate=plan.activate,
    )
