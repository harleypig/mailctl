"""The ``mxroute`` dialect: Sieve in Roundcube's rule-name form, offline.

Outbound, a neutral rule becomes Sieve text merged into the account's
script; inbound, that script is read back as neutral rules. MXroute's own
policies are applied on the way: ``redirect`` refused with a pointer to
forwarders, and ``disabled_extensions`` narrowing what a rule may use.
Nothing here opens a connection -- the ManageSieve and IMAP sessions are
the transport's (``transport.py``).
"""

from collections.abc import Iterable
from pathlib import Path

from ...components.imap.folders import normalize_folder
from ...components.managesieve.backup import (
    backup_path,
    resolve_backup_target,
)
from ...components.managesieve.script import (
    resolve_position,
    rule_names,
    script_diff,
)
from ...config import Config
from ...criteria import Criteria
from ...rules import Rule, read_rules
from ..base import (
    ActionSpec,
    DeliveryCreate,
    Dialect,
    DisplayDiff,
    ExtensionState,
    Fact,
    Placement,
    Wording,
)
from . import sieve as mxroute_sieve

__all__ = ["ASSUMED_DELIMITER", "MxrouteDialect", "capability_facts"]

# The delimiter guessed when there is no folder list to read one from
# (--no-imap). Maildir++'s, the layout observed on MXroute; with a
# session the delimiter is always read, never assumed.
ASSUMED_DELIMITER = "."


class MxrouteDialect(Dialect):
    """MXroute's rule language and wording, as the utilities use them."""

    name = "mxroute"

    wording = Wording(
        rules_service="ManageSieve",
        mail_service="IMAP",
        extensions="Sieve extensions",
        notes=(
            "MXRoute disables the Sieve 'redirect' action as a matter of "
            "policy (2024-03-22) -- use a panel forwarder, which handles "
            "SRS properly. That is the only MXRoute restriction mailctl "
            "asserts; everything else above came from the server.",
            "this covers the Sieve stage only. Mail may first pass a "
            "DirectAdmin panel filter (an Exim filter, run before Sieve) "
            "that mailctl cannot see or change; a message it drops never "
            "reaches any Sieve rule. Whether your account has one is "
            "unconfirmed -- see 'A filtering stage mailctl cannot see' in "
            "the README.",
        ),
    )

    # ########################################################################
    # Refusals and requirements
    # ########################################################################

    # ------------------------------------------------------------------------
    @classmethod
    def validate(cls, config: Config) -> None:
        mxroute_sieve.check_disabled_extensions(config)

    # ------------------------------------------------------------------------
    @classmethod
    def refuse_actions(cls, requested: Iterable[str]) -> None:
        mxroute_sieve.reject_actions(requested)

    # ------------------------------------------------------------------------
    @classmethod
    def translate_actions(
        cls, spec: ActionSpec, folder: str, use_create: bool
    ) -> list:
        return mxroute_sieve.sieve_actions(spec, folder, use_create)

    # ------------------------------------------------------------------------
    @classmethod
    def required_features(
        cls, spec: ActionSpec, folder: str, use_create: bool
    ) -> set[str]:
        return mxroute_sieve.required_extensions(spec, folder, use_create)

    # ------------------------------------------------------------------------
    @classmethod
    def missing_features(
        cls, needed: set[str], advertised: list[str]
    ) -> list[str]:
        """Sieve extension names compare without regard to case."""
        listed = {name.lower() for name in advertised}

        return sorted(name for name in needed if name.lower() not in listed)

    # ------------------------------------------------------------------------
    @classmethod
    def delivery_create(
        cls, config: Config, advertised: list[str] | None
    ) -> DeliveryCreate:
        """Whether ``fileinto :create`` is available: the server lists
        ``mailbox`` and ``disabled_extensions`` does not turn it off."""
        offered = advertised is not None and not cls.missing_features(
            {"mailbox"}, advertised
        )

        if offered and "mailbox" in config.disabled_extensions:
            return DeliveryCreate(True, mxroute_sieve.disabled_source(config))

        return DeliveryCreate(offered)

    # ------------------------------------------------------------------------
    @classmethod
    def check_actions(cls, config: Config, actions: list) -> None:
        mxroute_sieve.check_rule_extensions(config, actions)

    # ------------------------------------------------------------------------
    @classmethod
    def describe_actions(cls, actions: list) -> str:
        return mxroute_sieve.describe_actions(actions)

    # ------------------------------------------------------------------------
    @classmethod
    def candidate_rule(
        cls, name: str, criteria: Criteria, actions: list
    ) -> Rule:
        return mxroute_sieve.candidate_rule(name, criteria, actions)

    # ########################################################################
    # Rule sets
    # ########################################################################

    # ------------------------------------------------------------------------
    @classmethod
    def rule_names(cls, source: str) -> list[str]:
        return rule_names(mxroute_sieve.parse_script(source))

    # ------------------------------------------------------------------------
    @classmethod
    def read_rules(cls, source: str) -> list[Rule]:
        return read_rules(mxroute_sieve.parse_script(source))

    # ------------------------------------------------------------------------
    @classmethod
    def add_rule(
        cls,
        source: str,
        name: str,
        criteria: Criteria,
        actions: list,
        *,
        replace: bool,
        placement: Placement | None,
    ) -> str:
        return mxroute_sieve.merge_rule(
            source,
            name,
            criteria.sieve_conditions(),
            actions,
            matchtype=criteria.sieve_matchtype(),
            replace=replace,
            placement=placement,
        )

    # ------------------------------------------------------------------------
    @classmethod
    def remove_rule(cls, source: str, name: str) -> str:
        return mxroute_sieve.remove_rule(source, name)

    # ------------------------------------------------------------------------
    @classmethod
    def move_rule(cls, source: str, name: str, placement: Placement) -> str:
        return mxroute_sieve.move_rule(source, name, placement)

    # ------------------------------------------------------------------------
    @classmethod
    def position(
        cls, names: list[str], placement: Placement | None, name: str
    ) -> int:
        return resolve_position(
            names, mxroute_sieve.component_placement(placement), name
        )

    # ------------------------------------------------------------------------
    @classmethod
    def diff(cls, before: str, after: str, name: str) -> DisplayDiff:
        return mxroute_sieve.display_diff(before, after, name)

    # ------------------------------------------------------------------------
    @classmethod
    def raw_diff(cls, before: str, after: str, name: str) -> DisplayDiff:
        return DisplayDiff(
            script_diff(before, after, name),
            reformats=False,
            label=mxroute_sieve.DIFF_LABEL,
        )

    # ------------------------------------------------------------------------
    @classmethod
    def report_extensions(
        cls, advertised: list[str], config: Config
    ) -> list[ExtensionState]:
        return mxroute_sieve.report_extensions(advertised, config)

    # ########################################################################
    # Backups
    # ########################################################################

    # ------------------------------------------------------------------------
    @classmethod
    def backup_path(cls, name: str, backup_dir: Path) -> Path:
        return backup_path(name, backup_dir)

    # ------------------------------------------------------------------------
    @classmethod
    def backup_target(
        cls, output: str | None, name: str, backup_dir: Path
    ) -> Path:
        return resolve_backup_target(output, name, backup_dir)

    # ########################################################################
    # Folders
    # ########################################################################

    # ------------------------------------------------------------------------
    @classmethod
    def assumed_folder(
        cls, name: str, delimiter: str | None
    ) -> tuple[str, str]:
        assumed = delimiter or ASSUMED_DELIMITER

        return normalize_folder(name, assumed, None), assumed

    # ########################################################################
    # Describing the host
    # ########################################################################

    # ------------------------------------------------------------------------
    @classmethod
    def connection_facts(cls, config: Config) -> list[Fact]:
        return [
            Fact(
                "IMAP",
                f"{config.imap_host}:{config.imap_port}",
                (("host", "imap_host"), ("port", "imap_port")),
            ),
            Fact(
                "Sieve",
                f"{config.host}:{config.sieve_port} (tls={config.sieve_tls})",
                (("port", "sieve_port"), ("tls", "sieve_tls")),
            ),
        ]

    # ------------------------------------------------------------------------
    @classmethod
    def mail_facts(cls, capabilities: list[str]) -> list[Fact]:
        return capability_facts(capabilities)


# ----------------------------------------------------------------------------
def capability_facts(capabilities: list[str]) -> list[Fact]:
    """What MOVE, UIDPLUS, and FILTER=SIEVE mean here, one fact each.

    Dovecot's Pigeonhole ``imap_filter_sieve`` plugin advertises
    ``FILTER=SIEVE``, which lets a client ask the *server* to run a Sieve
    script over messages matching an IMAP search -- the retroactive pass
    done properly, server-side. It is experimental and off by default, so
    it is almost certainly absent here; one CAPABILITY line settles it
    either way, and an answer on the record beats an assumption.
    """
    move = "MOVE" in capabilities
    uidplus = "UIDPLUS" in capabilities
    filter_sieve = any(
        item.upper().startswith("FILTER=SIEVE") for item in capabilities
    )

    if filter_sieve:
        server_side = (
            "yes -- this server can apply a Sieve script to existing mail "
            "itself.\nmailctl still uses its own client-side pass; the "
            "server-side path is not implemented."
        )

    else:
        server_side = (
            "no -- no server-side retroactive filtering (Dovecot "
            "imap_filter_sieve is not enabled).\nmailctl's client-side "
            "search-and-move pass is the only option here."
        )

    return [
        Fact("MOVE", "yes" if move else "no (COPY+EXPUNGE)"),
        Fact("UIDPLUS", "yes" if uidplus else "no"),
        Fact("FILTER=SIEVE", server_side),
    ]
