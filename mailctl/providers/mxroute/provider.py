"""The ``mxroute`` provider: ManageSieve for rules, IMAP for mail.

It composes the two layer-1 libraries and translates both ways. Outbound, a
neutral rule becomes Sieve text in Roundcube's name dialect, merged into
the account's active script over ManageSieve; inbound, that script is read
back as neutral rules, and a server's answer or refusal as data or a
``MailctlError``. MXroute's own policies are applied on the way:
``redirect`` refused with a pointer to forwarders, and the login and TLS
advice a failed connection gains.

Selection -- which delivered messages a rule matches -- is the IMAP
``SEARCH`` translation plus the client-side re-check against the real Sieve
semantics (``ImapSession.plan_actions``). It moves to the Sieve interpreter
of ADR 0004 behind the same operation once that exists.
"""

from collections.abc import Iterable, Iterator
from contextlib import ExitStack, contextmanager
from email.message import Message
from functools import partial
from pathlib import Path

from ... import MailctlError
from ...components.imap import ImapSession, normalize_folder
from ...components.managesieve import (
    SieveSession,
    backup_script,
    resolve_backup_target,
    resolve_position,
    rule_names,
    script_diff,
    write_backup,
)
from ...config import Config
from ...criteria import Criteria
from ...rules import Rule, read_rules
from ..base import (
    DISCARD,
    FILEINTO,
    FLAG,
    KEEP,
    ActionSpec,
    DeliveryCreate,
    DisplayDiff,
    ExtensionState,
    Fact,
    FolderCreation,
    FolderListing,
    MailActionPlan,
    MailActionResult,
    MessageSummary,
    Placement,
    Progress,
    Provider,
    ProviderCapabilities,
    Wording,
)
from . import records
from . import sieve as mxroute_sieve
from .imap import capability_facts, imap_session

# The delimiter guessed when there is no folder list to read one from
# (--no-imap). Maildir++'s, the layout observed on MXroute; with a
# session the delimiter is always read, never assumed.
ASSUMED_DELIMITER = "."


__all__ = ["MxrouteProvider"]


class MxrouteProvider(Provider):
    """MXroute, as the engine sees it.

    Built open by :meth:`open`, or directly from two layer-1 sessions (or
    stand-ins for them), either of which may be None when that half was
    not asked for.
    """

    name = "mxroute"

    capabilities = ProviderCapabilities(
        ordering=True,
        stop=True,
        rule_sets=True,
        actions=frozenset((FILEINTO, DISCARD, FLAG, KEEP)),
        extensions=True,
        settings={
            "host": "MXRoute server hostname",
            "imap_host": "",
            "imap_port": "",
            "sieve_port": "",
            "sieve_tls": "",
        },
    )

    wording = Wording(
        rules_service="ManageSieve",
        mail_service="IMAP",
        extensions="Sieve extensions",
        notes=(
            "MXRoute disables the Sieve 'redirect' action as a matter of "
            "policy (2024-03-21) -- use a panel forwarder, which handles "
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

    # ------------------------------------------------------------------------
    def __init__(
        self,
        sieve: SieveSession | None = None,
        imap: ImapSession | None = None,
    ):
        self.sieve = sieve
        self.imap = imap

    # ------------------------------------------------------------------------
    def _sieve(self) -> SieveSession:
        """Return the ManageSieve session, or raise if none was opened."""
        if self.sieve is None:
            raise MailctlError("no ManageSieve session is open")

        return self.sieve

    # ------------------------------------------------------------------------
    def _imap(self) -> ImapSession:
        """Return the IMAP session, or raise if none was opened."""
        if self.imap is None:
            raise MailctlError("no IMAP session is open")

        return self.imap

    # ########################################################################
    # Before any network work
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

    # ------------------------------------------------------------------------
    @classmethod
    @contextmanager
    def open(
        cls,
        config: Config,
        *,
        rules: bool,
        mail: bool,
        progress: Progress | None = None,
    ) -> Iterator["MxrouteProvider"]:
        """Open the requested sessions and close them on the way out.

        IMAP is opened first, so a login failure there is reported before
        any ManageSieve traffic.
        """

        def channel(name: str):
            return partial(progress, name) if progress else None

        with ExitStack() as stack:
            provider = cls()

            if mail:
                provider.imap = stack.enter_context(
                    imap_session(config, progress=channel("imap"))
                )

            if rules:
                provider.sieve = stack.enter_context(
                    mxroute_sieve.sieve_session(
                        config, progress=channel("sieve")
                    )
                )

            yield provider

    # ########################################################################
    # Rule sets, offline
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
        return resolve_position(names, records.placement(placement), name)

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
            label=records.DIFF_LABEL,
        )

    # ------------------------------------------------------------------------
    @classmethod
    def report_extensions(
        cls, advertised: list[str], config: Config
    ) -> list[ExtensionState]:
        return mxroute_sieve.report_extensions(advertised, config)

    # ########################################################################
    # Folders, offline
    # ########################################################################

    # ------------------------------------------------------------------------
    @classmethod
    def assumed_folder(
        cls, name: str, delimiter: str | None
    ) -> tuple[str, str]:
        assumed = delimiter or ASSUMED_DELIMITER

        return normalize_folder(name, assumed, None), assumed

    # ########################################################################
    # Describing the host, offline
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

    # ########################################################################
    # Backups, offline
    # ########################################################################

    # ------------------------------------------------------------------------
    @classmethod
    def backup_target(
        cls, output: str | None, name: str, backup_dir: Path
    ) -> Path:
        return resolve_backup_target(output, name, backup_dir)

    # ------------------------------------------------------------------------
    @classmethod
    def write_backup(cls, source: str, target: Path) -> Path:
        return write_backup(source, target)

    # ------------------------------------------------------------------------
    @classmethod
    def backup(cls, source: str, name: str, backup_dir: Path) -> Path:
        return backup_script(source, name, backup_dir)

    # ########################################################################
    # Connected: the rule half, over ManageSieve
    # ########################################################################

    # ------------------------------------------------------------------------
    @property
    def has_rules(self) -> bool:
        return self.sieve is not None

    # ------------------------------------------------------------------------
    def rules_capabilities(self) -> list[str]:
        return self._sieve().capabilities()

    # ------------------------------------------------------------------------
    def missing_features(self, needed: set[str]) -> list[str]:
        return self._sieve().missing_extensions(needed)

    # ------------------------------------------------------------------------
    def delivery_create(self, config: Config) -> DeliveryCreate:
        """Whether ``fileinto :create`` is available: the server lists
        ``mailbox`` and ``disabled_extensions`` does not turn it off."""
        advertised = self.sieve is not None and not (
            self.sieve.missing_extensions({"mailbox"})
        )

        if advertised and "mailbox" in config.disabled_extensions:
            return DeliveryCreate(True, mxroute_sieve.disabled_source(config))

        return DeliveryCreate(advertised)

    # ------------------------------------------------------------------------
    def list_rule_sets(self) -> tuple[str | None, list[str]]:
        return self._sieve().list_scripts()

    # ------------------------------------------------------------------------
    def active_rule_set(self) -> str | None:
        return self._sieve().active_script_name()

    # ------------------------------------------------------------------------
    def read_rule_set(self, name: str) -> str:
        return self._sieve().get_script(name)

    # ------------------------------------------------------------------------
    def check_rule_set(self, source: str) -> None:
        self._sieve().check_script(source)

    # ------------------------------------------------------------------------
    def store_rule_set(self, name: str, source: str) -> None:
        self._sieve().put_script(name, source)

    # ------------------------------------------------------------------------
    def activate_rule_set(self, name: str) -> None:
        self._sieve().set_active(name)

    # ########################################################################
    # Connected: the mail half, over IMAP
    # ########################################################################

    # ------------------------------------------------------------------------
    @property
    def has_mail(self) -> bool:
        return self.imap is not None

    # ------------------------------------------------------------------------
    def mail_capabilities(self) -> list[str]:
        return self._imap().capabilities()

    # ------------------------------------------------------------------------
    def list_folders(self) -> FolderListing:
        imap = self._imap()

        return FolderListing(
            imap.delimiter, sorted(imap.folders), list(imap.subscribed_folders)
        )

    # ------------------------------------------------------------------------
    def delimiter(self) -> str:
        return self._imap().delimiter

    # ------------------------------------------------------------------------
    def normalize(self, name: str) -> str:
        return self._imap().normalize(name)

    # ------------------------------------------------------------------------
    def exists(self, folder: str) -> bool:
        return self._imap().exists(folder)

    # ------------------------------------------------------------------------
    def case_variants(self, folder: str) -> list[str]:
        return list(self._imap().case_variants(folder))

    # ------------------------------------------------------------------------
    def is_subscribed(self, folder: str) -> bool:
        return self._imap().is_subscribed(folder)

    # ------------------------------------------------------------------------
    def create_folder(self, folder: str, subscribe: bool) -> FolderCreation:
        return records.folder_creation(
            self._imap().create_folder(folder, subscribe=subscribe)
        )

    # ------------------------------------------------------------------------
    def subscribe(self, folder: str) -> None:
        self._imap().subscribe(folder)

    # ------------------------------------------------------------------------
    def unsubscribe(self, folder: str) -> None:
        self._imap().unsubscribe(folder)

    # ------------------------------------------------------------------------
    def select_mail(
        self,
        criteria: Criteria,
        source: str,
        destination: str,
        flags: list[str],
        discard: bool,
    ) -> MailActionPlan:
        return records.mail_plan(
            self._imap().plan_actions(
                criteria,
                source=source,
                destination=destination,
                flags=flags,
                discard=discard,
            )
        )

    # ------------------------------------------------------------------------
    def apply_mail(self, plan: MailActionPlan) -> MailActionResult:
        return records.mail_result(
            self._imap().execute(records.session_plan(plan))
        )

    # ------------------------------------------------------------------------
    def search_messages(self, folder: str, expression: str) -> list[int]:
        return self._imap().raw_search(folder, expression)

    # ------------------------------------------------------------------------
    def message_headers(self, folder: str, uid: int) -> Message:
        return self._imap().fetch_message_headers(folder, uid)

    # ------------------------------------------------------------------------
    def list_messages(
        self,
        folder: str,
        *,
        criteria: Criteria | None,
        expression: str | None,
        limit: int | None,
    ) -> tuple[list[MessageSummary], bool]:
        summaries, more = self._imap().list_messages(
            folder, criteria=criteria, expression=expression, limit=limit
        )

        return [records.message_summary(item) for item in summaries], more

    # ------------------------------------------------------------------------
    def message_source(
        self, folder: str, uid: int
    ) -> tuple[bytes, tuple[str, ...]]:
        return self._imap().fetch_message_source(folder, uid)
