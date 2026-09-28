"""The provider interface: the one shape every host presents (ADR 0006).

A provider is a **two-way translator**. The engine speaks one
provider-neutral model -- a rule as :class:`~mailctl.criteria.Criteria`
plus an :class:`ActionSpec`, folders, messages, capabilities, results, and
``MailctlError`` -- and a provider converts it into its host's terms on the
way out (Sieve text over ManageSieve, for ``mxroute``) and converts the
host's answers back into the same model on the way in (a parsed script
becomes :class:`~mailctl.rules.Rule` values; a server refusal becomes a
``MailctlError``).

Sameness is the default. What a host can and cannot do is declared as data
in :class:`ProviderCapabilities`, and the engine reads that data; it never
asks which provider it has. An operation a host cannot perform is still
present on its provider, marked with :func:`declined`, so every registered
provider answers every call in :data:`OPERATIONS` --
``tests/test_providers.py`` holds that.

The neutral model itself is :mod:`mailctl.providers.model`, re-exported
here so a provider or the engine reaches the interface and its records
through one import.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from typing import ClassVar

from .. import MailctlError
from ..config import Config
from ..criteria import Criteria
from ..rules import Rule
from .model import (
    DISCARD,
    FILEINTO,
    FLAG,
    KEEP,
    PLACE_AFTER,
    PLACE_BEFORE,
    PLACE_FIRST,
    PLACE_LAST,
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
    Wording,
    action_names,
    decode_header_value,
    same_folder,
)

__all__ = [
    "DISCARD",
    "FILEINTO",
    "FLAG",
    "KEEP",
    "OPERATIONS",
    "PLACE_AFTER",
    "PLACE_BEFORE",
    "PLACE_FIRST",
    "PLACE_LAST",
    "ActionSpec",
    "DeliveryCreate",
    "DisplayDiff",
    "ExtensionState",
    "Fact",
    "FolderCreation",
    "FolderListing",
    "MailActionPlan",
    "MailActionResult",
    "MessageSummary",
    "Placement",
    "Progress",
    "Provider",
    "ProviderCapabilities",
    "Specific",
    "Wording",
    "action_names",
    "declined",
    "decode_header_value",
    "refuse",
    "same_folder",
    "validate_specifics",
]

# (channel, message) -- the channel names which of a provider's sessions
# the message came from.
Progress = Callable[[str, str], None]


# ############################################################################
# Capabilities and specifics -- what varies, as data
# ############################################################################


@dataclass(frozen=True)
class Specific:
    """One provider-only rule parameter: a namespaced key and its schema.

    ``kind`` is the Python type a value must be; ``description`` says what
    it does, for anything that lists what a provider accepts.
    """

    kind: type
    description: str


@dataclass(frozen=True)
class ProviderCapabilities:
    """What one provider can do, frozen and enumerable.

    * ``ordering`` -- rules run in a stored order that a request may place
      a rule within; ``False`` means every rule is evaluated on its own.
    * ``stop`` -- a rule can end evaluation so later rules do not run.
    * ``rule_sets`` -- the host stores several named rule sets, one active.
    * ``actions`` -- the neutral action names (``ActionSpec``) it can emit.
    * ``extensions`` -- it reports rule-language extensions by name.
    * ``specifics`` -- the namespaced keys a request's ``specifics`` may
      carry, each with its schema. An unknown key is refused.
    * ``declined`` -- the :data:`OPERATIONS` this provider does not perform;
      each is implemented with :func:`declined`.
    """

    ordering: bool
    stop: bool
    rule_sets: bool
    actions: frozenset[str]
    extensions: bool
    specifics: Mapping[str, Specific] = field(default_factory=dict)
    declined: frozenset[str] = frozenset()


# ----------------------------------------------------------------------------
def refuse(provider: str, construct: str, why: str) -> MailctlError:
    """The one error for something a provider cannot express or do.

    Returned rather than raised so a caller writes ``raise refuse(...)``
    and the traceback points at the refusal, not at this helper.
    """
    return MailctlError(f"the {provider} provider cannot {construct}: {why}")


# ----------------------------------------------------------------------------
def validate_specifics(
    provider: str,
    schema: Mapping[str, Specific],
    specifics: Mapping[str, object],
) -> None:
    """Refuse an unknown or ill-typed specific; never ignore one.

    A key must be namespaced (``<namespace>.<name>``), so a specific can
    never be mistaken for a field of the shared model.
    """
    for key, value in specifics.items():
        if "." not in key:
            raise refuse(
                provider,
                f"take the specific {key!r}",
                "a provider specific is namespaced, as <namespace>.<name>",
            )

        spec = schema.get(key)

        if spec is None:
            known = ", ".join(sorted(schema)) or "none"

            raise refuse(
                provider,
                f"take the specific {key!r}",
                f"it is not one this provider declares (declared: {known})",
            )

        if not isinstance(value, spec.kind):
            raise refuse(
                provider,
                f"take {key}={value!r}",
                f"it must be a {spec.kind.__name__}",
            )


# ----------------------------------------------------------------------------
def declined(operation: Callable) -> Callable:
    """Mark an operation as one this provider does not perform.

    The operation is still defined, so the provider stays complete against
    the interface; calling it raises the one refusal error. Its name must
    also appear in the provider's ``capabilities.declined``, and the
    exhaustiveness test holds the two together.
    """
    name = operation.__name__

    def refused(self_or_cls, *args, **kwargs):
        raise refuse(self_or_cls.name, f"perform {name!r}", "it is declined")

    refused.__name__ = name
    refused.__qualname__ = operation.__qualname__
    refused.__doc__ = operation.__doc__
    refused.__declined__ = True  # type: ignore[attr-defined]

    return refused


# ############################################################################
# The interface
# ############################################################################


class Provider(ABC):
    """One host, presenting the operations the engine uses.

    A subclass is registered by ``name`` in
    :mod:`mailctl.providers.registry`. The classmethods need no connection:
    they validate and translate, so they run before any network work. An
    instance is one open connection, made by :meth:`open`.
    """

    name: ClassVar[str]
    capabilities: ClassVar[ProviderCapabilities]
    wording: ClassVar[Wording]

    # ------------------------------------------------------------------------
    # Before any network work
    # ------------------------------------------------------------------------

    @classmethod
    @abstractmethod
    def validate(cls, config: Config) -> None:
        """Refuse a configuration this provider cannot run under."""

    @classmethod
    @abstractmethod
    def refuse_actions(cls, requested: Iterable[str]) -> None:
        """Refuse, with a pointer, actions this provider will not emit."""

    @classmethod
    @abstractmethod
    def translate_actions(
        cls, spec: ActionSpec, folder: str, use_create: bool
    ) -> list:
        """Translate a spec into the host's actions; refuse an empty one.

        ``spec.stop`` arrives settled, never None: the engine has applied
        the default the capabilities give. The result is opaque to the
        engine: it is carried on the plan and handed back, never
        inspected.
        """

    @classmethod
    @abstractmethod
    def required_features(
        cls, spec: ActionSpec, folder: str, use_create: bool
    ) -> set[str]:
        """The host features (extensions) the translated rule needs."""

    @classmethod
    @abstractmethod
    def check_actions(cls, config: Config, actions: list) -> None:
        """Refuse translated actions the configuration has turned off."""

    @classmethod
    @abstractmethod
    def describe_actions(cls, actions: list) -> str:
        """Translated actions as one line a person reads, host escaping
        undone: the plan's summary, beside the diff that shows the source.
        """

    @classmethod
    @abstractmethod
    def candidate_rule(
        cls, name: str, criteria: Criteria, actions: list
    ) -> Rule:
        """Read translated actions back as the neutral rule they make."""

    @classmethod
    @abstractmethod
    def open(
        cls,
        config: Config,
        *,
        rules: bool,
        mail: bool,
        progress: Progress | None = None,
    ) -> AbstractContextManager["Provider"]:
        """Connect the requested halves; close them on the way out."""

    # ------------------------------------------------------------------------
    # Rule sets, offline -- the host's stored form, and the neutral one
    # ------------------------------------------------------------------------

    @classmethod
    @abstractmethod
    def rule_names(cls, source: str) -> list[str]:
        """The names of the rules in a stored rule set, in order."""

    @classmethod
    @abstractmethod
    def read_rules(cls, source: str) -> list[Rule]:
        """Translate a stored rule set into neutral rules, in order."""

    @classmethod
    @abstractmethod
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
        """Merge a rule into a stored rule set, never overwriting it."""

    @classmethod
    @abstractmethod
    def remove_rule(cls, source: str, name: str) -> str:
        """Take a named rule out of a stored rule set."""

    @classmethod
    @abstractmethod
    def move_rule(cls, source: str, name: str, placement: Placement) -> str:
        """Move a named rule within a stored rule set."""

    @classmethod
    @abstractmethod
    def position(
        cls, names: list[str], placement: Placement | None, name: str
    ) -> int:
        """Where a placement puts ``name``, counting the other rules."""

    @classmethod
    @abstractmethod
    def diff(cls, before: str, after: str, name: str) -> DisplayDiff:
        """A diff of a rule change, for a person to read."""

    @classmethod
    @abstractmethod
    def raw_diff(cls, before: str, after: str, name: str) -> DisplayDiff:
        """A diff of the exact bytes, formatting included."""

    @classmethod
    @abstractmethod
    def report_extensions(
        cls, advertised: list[str], config: Config
    ) -> list[ExtensionState]:
        """Every extension mailctl knows or the host lists, and its state."""

    # ------------------------------------------------------------------------
    # Folders, offline
    # ------------------------------------------------------------------------

    @classmethod
    @abstractmethod
    def assumed_folder(
        cls, name: str, delimiter: str | None
    ) -> tuple[str, str]:
        """A folder name as the host would spell it, with no folder list.

        For when the mail half is not connected. ``delimiter`` is the one
        the user said to assume, or None for the host's usual one; returns
        the name and the delimiter used.
        """

    # ------------------------------------------------------------------------
    # Describing the host, offline
    # ------------------------------------------------------------------------

    @classmethod
    @abstractmethod
    def connection_facts(cls, config: Config) -> list[Fact]:
        """Where each half connects, as ``config`` resolves it.

        Each fact names the settings it reports, so the front-end can say
        where they came from.
        """

    @classmethod
    @abstractmethod
    def mail_facts(cls, capabilities: list[str]) -> list[Fact]:
        """What the mail half's advertised capabilities mean for mailctl."""

    # ------------------------------------------------------------------------
    # Backups, offline -- the host's exact bytes, on disk
    # ------------------------------------------------------------------------

    @classmethod
    @abstractmethod
    def backup_target(
        cls, output: str | None, name: str, backup_dir: Path
    ) -> Path:
        """Where a backup of ``name`` goes."""

    @classmethod
    @abstractmethod
    def write_backup(cls, source: str, target: Path) -> Path:
        """Write the host's exact bytes to ``target``."""

    @classmethod
    @abstractmethod
    def backup(cls, source: str, name: str, backup_dir: Path) -> Path:
        """Back up ``source`` under a fresh name before an upload."""

    # ------------------------------------------------------------------------
    # Connected: the rule half
    # ------------------------------------------------------------------------

    @property
    @abstractmethod
    def has_rules(self) -> bool:
        """Whether the rule half is connected."""

    @abstractmethod
    def rules_capabilities(self) -> list[str]:
        """What the rule half advertises, as the host names it."""

    @abstractmethod
    def missing_features(self, needed: set[str]) -> list[str]:
        """Which of ``needed`` the host does not advertise."""

    @abstractmethod
    def delivery_create(self, config: Config) -> DeliveryCreate:
        """Whether a rule can create its folder when mail arrives."""

    @abstractmethod
    def list_rule_sets(self) -> tuple[str | None, list[str]]:
        """``(active, others)``."""

    @abstractmethod
    def active_rule_set(self) -> str | None:
        """The name of the rule set that runs, or None."""

    @abstractmethod
    def read_rule_set(self, name: str) -> str:
        """A stored rule set, exactly as the host holds it."""

    @abstractmethod
    def check_rule_set(self, source: str) -> None:
        """Have the host validate a rule set without storing it."""

    @abstractmethod
    def store_rule_set(self, name: str, source: str) -> None:
        """Store a rule set under ``name``."""

    @abstractmethod
    def activate_rule_set(self, name: str) -> None:
        """Make ``name`` the rule set that runs."""

    # ------------------------------------------------------------------------
    # Connected: the mail half -- folders
    # ------------------------------------------------------------------------

    @property
    @abstractmethod
    def has_mail(self) -> bool:
        """Whether the mail half is connected."""

    @abstractmethod
    def mail_capabilities(self) -> list[str]:
        """What the mail half advertises, as the host names it."""

    @abstractmethod
    def list_folders(self) -> FolderListing:
        """The folders, sorted, the delimiter, and the subscribed ones."""

    @abstractmethod
    def delimiter(self) -> str:
        """The folder hierarchy delimiter the host reports."""

    @abstractmethod
    def normalize(self, name: str) -> str:
        """A user's folder name as the host spells it."""

    @abstractmethod
    def exists(self, folder: str) -> bool:
        """Whether a folder exists."""

    @abstractmethod
    def case_variants(self, folder: str) -> list[str]:
        """Existing folders that differ from ``folder`` only in case."""

    @abstractmethod
    def is_subscribed(self, folder: str) -> bool:
        """Whether a folder is subscribed."""

    @abstractmethod
    def create_folder(self, folder: str, subscribe: bool) -> FolderCreation:
        """Create a folder, subscribing to it unless declined."""

    @abstractmethod
    def subscribe(self, folder: str) -> None:
        """Subscribe to a folder, confirming it took effect."""

    @abstractmethod
    def unsubscribe(self, folder: str) -> None:
        """Unsubscribe from a folder."""

    # ------------------------------------------------------------------------
    # Connected: the mail half -- the existing-mail pass and messages
    # ------------------------------------------------------------------------

    @abstractmethod
    def select_mail(
        self,
        criteria: Criteria,
        source: str,
        destination: str,
        flags: list[str],
        discard: bool,
    ) -> MailActionPlan:
        """Select the messages a rule matches, read-only, and plan the act.

        Selection -- *does this rule match this message* -- is the
        provider's to answer (#26), so the existing-mail pass stands on it
        whatever the host.
        """

    @abstractmethod
    def apply_mail(self, plan: MailActionPlan) -> MailActionResult:
        """Carry out a selection's plan."""

    @abstractmethod
    def search_messages(self, folder: str, expression: str) -> list[int]:
        """The UIDs a host-native search expression matches."""

    @abstractmethod
    def message_headers(self, folder: str, uid: int) -> Message:
        """One message's headers, as an ``email.message.Message``."""

    @abstractmethod
    def list_messages(
        self,
        folder: str,
        *,
        criteria: Criteria | None,
        expression: str | None,
        limit: int | None,
    ) -> tuple[list[MessageSummary], bool]:
        """The newest matches, newest first, and whether there are more."""

    @abstractmethod
    def message_source(
        self, folder: str, uid: int
    ) -> tuple[bytes, tuple[str, ...]]:
        """One message's exact bytes and its flags, left unread."""


# Every operation of the interface, by name. The exhaustiveness test holds
# this equal to the abstract methods above, so it cannot drift from them.
OPERATIONS = tuple(sorted(Provider.__abstractmethods__))
