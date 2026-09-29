"""MXroute's side of ManageSieve: its policy, its webmail, its login advice.

The layer-1 ``managesieve`` library knows the protocol and Sieve. What
lives here is what MXroute has decided or ships: ``redirect`` refused in
favour of its forwarders, rule names in the dialect of Roundcube (the
webmail it runs), and ``disabled_extensions`` checked against what a rule
needs.

It is also where the provider's translation to Sieve happens: a neutral
``ActionSpec`` becomes Sieve action tuples, and a neutral placement or diff
crosses to and from the component's own records. All of it is offline: it
is the dialect's (``dialect.py``), and the ManageSieve connection is the
transport's (``managesieve.py``).
"""

import re
from collections.abc import Iterable
from typing import overload

from sievelib import factory

from ... import MailctlError
from ...components.managesieve import script as _script
from ...components.managesieve.emit import (
    KNOWN_EXTENSIONS,
    REQUIRED_EXTENSIONS,
    emitted_extensions,
)
from ...components.managesieve.script import (
    SIEVELIB_NAME_MARKER,
    UNIMPLEMENTED_ACTIONS,
    NameDialect,
    rewrite_hash_comments,
)
from ...config import DEFAULT, Config, Source
from ...criteria import Criteria, escape_sieve_string
from ...rules import Rule, rule_from_criteria
from ..base import ActionSpec, DisplayDiff, ExtensionState, Placement

__all__ = [
    "DIFF_LABEL",
    "MXROUTE_FORBIDDEN_ACTIONS",
    "ROUNDCUBE_DIALECT",
    "ROUNDCUBE_NAME_MARKER",
    "candidate_rule",
    "check_disabled_extensions",
    "check_rule_extensions",
    "component_placement",
    "describe_actions",
    "disable_rule",
    "display_diff",
    "enable_rule",
    "merge_rule",
    "move_rule",
    "parse_script",
    "reject_actions",
    "remove_rule",
    "render_script",
    "report_extensions",
    "required_extensions",
    "sieve_actions",
]

# What a diff of this host's rule set is called: a Sieve script.
DIFF_LABEL = "sieve"

# Confirmed disabled by MXRoute, from MXroute's own blog (2024-03-22):
# they "decided to disable the ability for users to create redirect sieve
# filters" because their real forwarders are designed to handle SRS
# properly. This one is a documented policy, not a capability, so it will
# not show up as a missing extension -- refusing it here is the only way to
# catch it before the server does.
#
# The message names BOTH routes deliberately. The Terraform resource is the
# as-code path and the one that will age best -- MXroute is phasing
# DirectAdmin out as a user interface, so panel-shaped instructions may go
# stale while the API-backed resource will not. But a domain that is not
# under Terraform yet has only the panel, and that reader is exactly the one
# hitting this error. Naming both costs a clause.
MXROUTE_FORBIDDEN_ACTIONS = {
    "redirect": (
        "MXRoute disables the Sieve 'redirect' action server-side (their "
        "2024-03-22 announcement). Their own forwarders are built to handle "
        "SRS correctly, which a Sieve redirect does not -- so set up a "
        "forwarder in the control panel, or with the 'mxroute_forwarder' "
        "Terraform resource, instead."
    ),
}

# Two dialects name a rule in a Sieve script, and mailctl has to read both
# and write one.
#
# `# Filter: NAME` is sievelib's, and the only one its parser recognises.
# `# rule:[NAME]` is Roundcube's managesieve plugin's -- and Roundcube is the
# webmail MXRoute actually ships, so it is the form already sitting in the
# account's active script.
#
# Read: both, because a name that reaches the parser under only one of them
# is a name that gets replaced by "Unnamed rule N" -- and the name is what
# --replace and remove-rule identify a rule by, so losing it turns "update
# the rule I named" into "append a second rule that never fires".
#
# Write: `# rule:[NAME]`. Interoperating with the webmail on the host beats
# matching the library's internal default: rules mailctl writes stay
# visible and editable in the panel's filter UI, and rules the user wrote
# there keep their names through a merge.
ROUNDCUBE_NAME_MARKER = re.compile(r"#\s*rule:\[(?P<name>.+)\]")


# ############################################################################
# Refusals -- the actions mailctl will not emit here
# ############################################################################


# ----------------------------------------------------------------------------
def reject_actions(requested: Iterable[str]) -> None:
    """Refuse actions this tool will not generate, and say why.

    ``redirect`` is refused because MXRoute has publicly disabled it -- a
    policy, so the alternative is named. The rest are simply not
    implemented here, and mailctl has no evidence either way about whether
    this server supports them.
    """
    requested = set(requested)

    for name, explanation in MXROUTE_FORBIDDEN_ACTIONS.items():
        if name in requested:
            raise MailctlError(explanation)

    for name, label in UNIMPLEMENTED_ACTIONS.items():
        if name in requested:
            raise MailctlError(
                f"mailctl does not generate the Sieve '{label}' action. "
                f"This is a conservative choice of ours, not a documented "
                f"MXRoute restriction -- the MXRoute control panel is where "
                f"this feature lives if you need it. To see whether the "
                f"server advertises the extension at all, run "
                f"'mailctl test'."
            )


# ############################################################################
# Translation -- the neutral actions, as Sieve
# ############################################################################


# ----------------------------------------------------------------------------
def sieve_actions(spec: ActionSpec, folder: str, use_create: bool) -> list:
    """Build the sievelib action tuples for the requested actions.

    Flags are emitted before ``fileinto`` so the delivered copy carries
    them, and ``stop`` last so later rules do not also fire.
    """
    actions = _action_tuples(spec, folder, use_create)

    if not actions:
        raise MailctlError(
            "no action requested -- use --fileinto, --discard, --mark-read, "
            "--flag, or --keep"
        )

    if spec.stop:
        actions.append(("stop",))

    return actions


# ----------------------------------------------------------------------------
def _action_tuples(spec: ActionSpec, folder: str, use_create: bool) -> list:
    """The actions ahead of ``stop``; empty when nothing was asked for."""
    actions: list[tuple] = []

    for flag in spec.flags:
        actions.append(("addflag", escape_sieve_string(flag)))

    if spec.discard:
        actions.append(("discard",))

    elif folder:
        if use_create:
            actions.append(
                ("fileinto", ":create", escape_sieve_string(folder))
            )

        else:
            actions.append(("fileinto", escape_sieve_string(folder)))

    if spec.keep:
        actions.append(("keep",))

    return actions


# ----------------------------------------------------------------------------
def describe_actions(actions: list) -> str:
    """Render action tuples as a readable summary line.

    Sieve escaping is undone for display: the summary should say
    ``addflag \\Seen``, which is the flag the user asked for, rather than
    the ``\\\\Seen`` that has to appear in the script source. The diff
    printed underneath shows the real source, so nothing is hidden.
    """
    return "; ".join(
        " ".join(_unescape_sieve_string(str(part)) for part in action)
        for action in actions
    )


# ----------------------------------------------------------------------------
def _unescape_sieve_string(value: str) -> str:
    """Reverse ``escape_sieve_string`` for display purposes only."""
    return value.replace('\\"', '"').replace("\\\\", "\\")


# ----------------------------------------------------------------------------
def required_extensions(
    spec: ActionSpec, folder: str, use_create: bool
) -> set[str]:
    """Return the Sieve extensions the generated rule will need.

    ``folder`` is the resolved target, so a folder that came from
    ``Config.default_folder`` rather than ``spec.fileinto`` counts too.
    """
    return emitted_extensions(_action_tuples(spec, folder, use_create))


# ----------------------------------------------------------------------------
def candidate_rule(name: str, criteria: Criteria, actions: list) -> Rule:
    """Read Sieve action tuples back as the neutral rule they make."""
    stops = any(action[0] == "stop" for action in actions)
    action_names = tuple(action[0] for action in actions)

    return rule_from_criteria(name, criteria, action_names, stops=stops)


# ############################################################################
# disabled_extensions -- what the server advertises, less what is disabled
# ############################################################################


# ----------------------------------------------------------------------------
def check_disabled_extensions(config: Config) -> None:
    """Refuse a disabled_extensions entry mailctl does not know.

    A typo would otherwise disable nothing and say nothing, which is the
    one outcome a switch must not have.
    """
    unknown = sorted(config.disabled_extensions - set(KNOWN_EXTENSIONS))

    if unknown:
        raise MailctlError(
            f"disabled_extensions: unknown Sieve extension(s) "
            f"{', '.join(repr(name) for name in unknown)} "
            f"(from {disabled_source(config).describe()}). Known: "
            f"{', '.join(KNOWN_EXTENSIONS)}"
        )


# ----------------------------------------------------------------------------
def disabled_source(config: Config) -> Source:
    """Where disabled_extensions came from; a hand-built Config has none."""
    return config.sources.get("disabled_extensions", Source(DEFAULT))


# ----------------------------------------------------------------------------
def disabled_message(config: Config, names: Iterable[str]) -> str:
    """Name what is disabled and the setting that disabled it."""
    names = sorted(names)
    listed = ", ".join(repr(name) for name in names)
    noun = "extension" if len(names) == 1 else "extensions"
    verb = "is" if len(names) == 1 else "are"

    return (
        f"the Sieve {noun} {listed} {verb} disabled by mailctl "
        f"(disabled_extensions, from {disabled_source(config).describe()})"
    )


# ----------------------------------------------------------------------------
def check_rule_extensions(config: Config, actions: list) -> None:
    """Refuse actions needing an extension disabled_extensions turns off.

    Checked at plan and again at execute, so a plan made under one
    setting is not carried out under another.
    """
    blocked = emitted_extensions(actions) & config.disabled_extensions

    if blocked:
        it = "it" if len(blocked) == 1 else "them"

        raise MailctlError(
            f"{disabled_message(config, blocked)}, and this rule needs "
            f"{it}. Drop the action that needs {it}, or take {it} out of "
            f"disabled_extensions."
        )


# ----------------------------------------------------------------------------
def report_extensions(
    capabilities: Iterable[str], config: Config
) -> list[ExtensionState]:
    """One state per extension mailctl knows or the server lists, by name.

    A server-listed name mailctl does not know is never disabled:
    ``check_disabled_extensions`` refuses such a name before this runs.
    """
    advertised = {name.lower() for name in capabilities}
    origin = disabled_source(config)

    return [
        ExtensionState(
            name,
            name in advertised,
            name in REQUIRED_EXTENSIONS,
            origin if name in config.disabled_extensions else None,
        )
        for name in sorted(advertised | set(KNOWN_EXTENSIONS))
    ]


# ############################################################################
# The Roundcube name dialect
# ############################################################################


# ----------------------------------------------------------------------------
def _to_sievelib_names(text: str) -> str:
    """Rewrite Roundcube name markers into the form sievelib recognises."""

    def translate(comment: str) -> str | None:
        match = ROUNDCUBE_NAME_MARKER.fullmatch(comment.strip())

        if match is None:
            return None

        return f"{SIEVELIB_NAME_MARKER}{match['name']}"

    return rewrite_hash_comments(text, translate)


# ----------------------------------------------------------------------------
def _to_roundcube_names(text: str) -> str:
    """Rewrite sievelib's name markers into Roundcube's form."""

    def translate(comment: str) -> str | None:
        stripped = comment.strip()

        if not stripped.startswith(SIEVELIB_NAME_MARKER):
            return None

        name = stripped[len(SIEVELIB_NAME_MARKER) :]

        if not name:
            return None

        return f"# rule:[{name}]"

    return rewrite_hash_comments(text, translate)


ROUNDCUBE_DIALECT = NameDialect(
    read=_to_sievelib_names, write=_to_roundcube_names
)


# ############################################################################
# Script handling in MXroute's dialect
# ############################################################################


# ----------------------------------------------------------------------------
def parse_script(text: str) -> factory.FiltersSet:
    """Parse a script, reading Roundcube's rule names as well as sievelib's."""
    return _script.parse_script(text, ROUNDCUBE_DIALECT)


# ----------------------------------------------------------------------------
def render_script(filters: factory.FiltersSet) -> str:
    """Render a filter set with rule names in Roundcube's form."""
    return _script.render_script(filters, ROUNDCUBE_DIALECT)


# ----------------------------------------------------------------------------
def merge_rule(
    existing: str,
    name: str,
    conditions: list[tuple],
    actions: list[tuple],
    matchtype: str = "anyof",
    replace: bool = False,
    placement: Placement | None = None,
) -> str:
    """``managesieve.merge_rule``, with Roundcube's rule names."""
    return _script.merge_rule(
        existing,
        name,
        conditions,
        actions,
        matchtype,
        replace,
        component_placement(placement),
        ROUNDCUBE_DIALECT,
    )


# ----------------------------------------------------------------------------
def remove_rule(existing: str, name: str) -> str:
    """``managesieve.remove_rule``, with Roundcube's rule names."""
    return _script.remove_rule(existing, name, ROUNDCUBE_DIALECT)


# ----------------------------------------------------------------------------
def disable_rule(existing: str, name: str) -> str:
    """``managesieve.disable_rule``, with Roundcube's rule names."""
    return _script.disable_rule(existing, name, ROUNDCUBE_DIALECT)


# ----------------------------------------------------------------------------
def enable_rule(existing: str, name: str) -> str:
    """``managesieve.enable_rule``, with Roundcube's rule names."""
    return _script.enable_rule(existing, name, ROUNDCUBE_DIALECT)


# ----------------------------------------------------------------------------
def move_rule(existing: str, name: str, placement: Placement) -> str:
    """``managesieve.move_rule``, with Roundcube's rule names."""
    return _script.move_rule(
        existing, name, component_placement(placement), ROUNDCUBE_DIALECT
    )


# ----------------------------------------------------------------------------
def display_diff(before: str, after: str, name: str = "sieve") -> DisplayDiff:
    """``managesieve.display_diff``, with Roundcube's rule names."""
    diff = _script.display_diff(before, after, name, ROUNDCUBE_DIALECT)

    return DisplayDiff(diff.text, diff.reformats, DIFF_LABEL)


# ----------------------------------------------------------------------------
@overload
def component_placement(value: Placement) -> _script.Placement: ...


@overload
def component_placement(value: None) -> None: ...


def component_placement(value: Placement | None) -> _script.Placement | None:
    """A neutral placement, as the ManageSieve component takes it."""
    if value is None:
        return None

    return _script.Placement(value.where, value.anchor)
