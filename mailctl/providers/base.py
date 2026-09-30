"""The provider interface: the one shape every host presents (ADR 0006).

A provider is a **two-way translator**. The utilities speak one
provider-neutral model -- a rule as :class:`~mailctl.criteria.Criteria`
plus an :class:`ActionSpec`, folders, messages, capabilities, results, and
``MailctlError`` -- and a provider converts it into its host's terms on the
way out (Sieve text over ManageSieve, for ``mxroute``) and converts the
host's answers back into the same model on the way in (a parsed script
becomes :class:`~mailctl.rules.Rule` values; a server refusal becomes a
``MailctlError``).

It does so in two halves (ADR 0007). The :class:`Dialect` is offline: it
translates, parses, edits, refuses, and words, and never touches the
network. The :class:`Transport` is communication only: one exchange with a
server per operation, or a composite the host performs natively, and it
builds and validates nothing. A :class:`Provider` names the pair and what
the host can do. The utilities do the building, asking the dialect for
the host-specific parts and the transport to read and store.

Sameness is the default. What a host can and cannot do is declared as data
in :class:`ProviderCapabilities`, and the utilities read that data; they
never ask which provider they have. An operation a host cannot perform is
still present on its half, marked with :func:`declined`, so every
registered provider answers every call in :data:`OPERATIONS` --
``tests/test_providers.py`` holds that.

The neutral model itself is :mod:`mailctl.providers.model`, re-exported
here so a provider or a utility reaches the interface and its records
through one import.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from typing import ClassVar

from .. import MailctlError
from ..config import Config
from ..criteria import Criteria
from ..rules import Rule, Slot
from .model import (
    DISCARD,
    FILEINTO,
    FLAG,
    KEEP,
    NAMESPACE_KINDS,
    PLACE_AFTER,
    PLACE_BEFORE,
    PLACE_FIRST,
    PLACE_LAST,
    SORT_KEYS,
    SORT_RECEIVED,
    SORT_SENT,
    SORT_SIZE,
    ActionSpec,
    Capability,
    CountSupport,
    DeliveryCreate,
    DisplayDiff,
    DriftTerms,
    ExtensionState,
    Fact,
    FetchedMessage,
    FolderCreation,
    FolderListing,
    FolderReference,
    FolderStatus,
    MailActionPlan,
    MailActionResult,
    MessageSummary,
    Namespace,
    Placement,
    ProbeRecord,
    ServerAlert,
    ServerDescription,
    SortOrder,
    Wording,
    action_names,
    decode_header_value,
    same_folder,
)

__all__ = [
    "CONNECTION",
    "DIALECT_OPERATIONS",
    "DISCARD",
    "FILEINTO",
    "FLAG",
    "KEEP",
    "MAIL",
    "NAMESPACE_KINDS",
    "OPERATIONS",
    "PLACE_AFTER",
    "PLACE_BEFORE",
    "PLACE_FIRST",
    "PLACE_LAST",
    "READ",
    "RULES",
    "SORT_KEYS",
    "SORT_RECEIVED",
    "SORT_SENT",
    "SORT_SIZE",
    "TRANSPORT_KINDS",
    "TRANSPORT_OPERATIONS",
    "WRITE",
    "ActionSpec",
    "Capability",
    "CountSupport",
    "DeliveryCreate",
    "Dialect",
    "DisplayDiff",
    "DriftTerms",
    "ExtensionState",
    "Fact",
    "FetchedMessage",
    "FolderCreation",
    "FolderListing",
    "FolderReference",
    "FolderStatus",
    "MailActionPlan",
    "MailActionResult",
    "MessageSummary",
    "Namespace",
    "Operation",
    "Placement",
    "ProbeRecord",
    "Progress",
    "Provider",
    "ProviderCapabilities",
    "ServerAlert",
    "ServerDescription",
    "SortOrder",
    "Specific",
    "Transport",
    "Wording",
    "action_names",
    "classified",
    "declined",
    "decode_header_value",
    "refuse",
    "same_folder",
    "validate_specifics",
]

# (channel, message) -- the channel names which of a provider's sessions
# the message came from. A message is step-by-step progress, or a
# ServerAlert -- an alert or a warning -- which a front-end shows even
# when it shows no progress.
Progress = Callable[[str, str | ServerAlert], None]


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
    * ``disable`` -- a rule can be switched off and back on without being
      removed.
    * ``rename`` -- a rule's name can be changed, leaving the rule as it
      was.
    * ``actions`` -- the neutral action names (``ActionSpec``) it can emit.
    * ``extensions`` -- it reports rule-language extensions by name.
    * ``raw_query`` -- a message search may be given as a query in the
      host's own search language, passed to the host unchanged.
    * ``mark`` -- a message's flags (read, flagged, and named keywords) can
      be set and cleared by UID, each as its own write.
    * ``folder_counts`` -- every folder's total and unread messages can be
      read in one request, where the server offers it (the dialect's
      :meth:`Dialect.count_support` says whether this one does).
    * ``uidvalidity`` -- a folder reports the value its message UIDs are
      valid under (IMAP's UIDVALIDITY), so a UID kept from an earlier run
      can be checked before it is used.
    * ``specifics`` -- the namespaced keys a request's ``specifics`` may
      carry, each with its schema. An unknown key is refused.
    * ``settings`` -- which of ``config.CONNECTION_SETTINGS`` it reads, each
      with the help its flag shows (empty for none). A front-end offers
      only these, and one given by flag that is not here is refused.
    * ``declined`` -- the :data:`OPERATIONS` this provider does not perform;
      each is implemented with :func:`declined`.
    """

    ordering: bool
    stop: bool
    rule_sets: bool
    disable: bool
    rename: bool
    actions: frozenset[str]
    extensions: bool
    raw_query: bool
    mark: bool
    folder_counts: bool
    uidvalidity: bool
    specifics: Mapping[str, Specific] = field(default_factory=dict)
    declined: frozenset[str] = frozenset()
    settings: Mapping[str, str] = field(default_factory=dict)


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

    The operation is still defined, so its half stays complete against the
    interface; calling it raises the one refusal error. Its name must also
    appear in the provider's ``capabilities.declined``, and the
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
# The dialect -- offline: the neutral model in the host's language and back
# ############################################################################


class Dialect(ABC):
    """One host's language, spoken offline (ADR 0007).

    Every operation is a classmethod and none touches the network: a
    dialect translates actions, parses and renders the host's stored rule
    set, edits it, holds the host's refusals and required features as
    checks, and words the host for a person. A utility asks it for the
    host-specific part of a piece of work, and hands what it builds to the
    transport to save.
    """

    name: ClassVar[str]
    wording: ClassVar[Wording]

    # ------------------------------------------------------------------------
    # Refusals and requirements
    # ------------------------------------------------------------------------

    @classmethod
    @abstractmethod
    def validate(cls, config: Config) -> None:
        """Refuse a configuration this host cannot run under."""

    @classmethod
    @abstractmethod
    def refuse_actions(cls, requested: Iterable[str]) -> None:
        """Refuse, with a pointer, actions this host will not take."""

    @classmethod
    @abstractmethod
    def translate_actions(
        cls, spec: ActionSpec, folder: str, use_create: bool
    ) -> list:
        """Translate a spec into the host's actions; refuse an empty one.

        ``spec.stop`` arrives settled, never None: the utility has applied
        the default the capabilities give. The result is opaque to the
        utilities: it is carried on the plan and handed back, never
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
    def missing_features(
        cls, needed: set[str], advertised: list[str]
    ) -> list[str]:
        """Which of ``needed`` are not among the ``advertised`` features."""

    @classmethod
    @abstractmethod
    def delivery_create(
        cls, config: Config, advertised: list[str] | None
    ) -> DeliveryCreate:
        """Whether a rule can create its folder when mail arrives.

        ``advertised`` is what the rule half advertises, or None when that
        half is not connected.
        """

    @classmethod
    @abstractmethod
    def check_actions(cls, config: Config, actions: list) -> None:
        """Refuse translated actions the configuration has turned off."""

    @classmethod
    @abstractmethod
    def check_criteria(
        cls, config: Config, criteria: Criteria, advertised: list[str]
    ) -> None:
        """Refuse criteria a rule on this host cannot test: a feature the
        rule half does not ``advertised``, or one the configuration has
        turned off. Date and state filters are refused before this, by the
        utilities, whatever the host."""

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

    # ------------------------------------------------------------------------
    # Rule sets -- the host's stored form, and the neutral one
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
    def rule_set_requires(cls, source: str) -> list[str]:
        """The host features a stored rule set declares it needs, sorted.

        Raises ``MailctlError`` where the rule set will not parse.
        """

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
    def rename_rule(cls, source: str, old: str, new: str) -> str:
        """Give the named rule ``old`` the name ``new``, changing nothing
        else in the stored rule set.

        Refused when ``old`` is not a rule, or ``new`` is empty, taken, or
        not a name the host can store. Returns ``source`` unchanged when
        the two names are the same.
        """

    @classmethod
    @abstractmethod
    def disable_rule(cls, source: str, name: str) -> str:
        """Switch a named rule off, keeping it in the stored rule set.

        Returns ``source`` unchanged when the rule is already off.
        """

    @classmethod
    @abstractmethod
    def enable_rule(cls, source: str, name: str) -> str:
        """Switch a named rule that is off back on.

        Returns ``source`` unchanged when the rule is already on.
        """

    @classmethod
    @abstractmethod
    def rearrange_rules(cls, source: str, layout: Sequence[Slot]) -> str:
        """Lay a stored rule set out as ``layout`` says, and change nothing
        else: each slot's rule in the new order, the rules it absorbs
        merged into its key list, and a rule no slot names removed.

        The rules are those :meth:`read_rules` reads, by index. A merge
        the dialect cannot make exactly -- the rules do not test one
        header the same way with the same actions -- is refused.
        """

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

    @classmethod
    @abstractmethod
    def folder_references(cls, source: str) -> list[FolderReference]:
        """Every folder a rule in a stored rule set files into, in order."""

    @classmethod
    @abstractmethod
    def retarget_folders(cls, source: str, renames: Mapping[str, str]) -> str:
        """Point every filing action whose folder is a key of ``renames``
        at its value, and change nothing else in ``source``.

        Names match exactly, as :meth:`folder_references` reports them.
        Rules that file nowhere renamed come back byte for byte, so the
        host's own formatting and any comments survive; a target the
        dialect cannot rewrite safely is refused rather than skipped.
        """

    # ------------------------------------------------------------------------
    # Backups -- where the host's exact bytes are kept on disk
    # ------------------------------------------------------------------------

    @classmethod
    @abstractmethod
    def backup_path(cls, name: str, backup_dir: Path) -> Path:
        """The fresh file a backup of ``name`` taken now is written to."""

    @classmethod
    @abstractmethod
    def backup_target(
        cls, output: str | None, name: str, backup_dir: Path
    ) -> Path:
        """Where a backup of ``name`` goes, ``output`` given or not."""

    # ------------------------------------------------------------------------
    # Folders
    # ------------------------------------------------------------------------

    @classmethod
    @abstractmethod
    def normalize(cls, name: str, listing: FolderListing) -> str:
        """A user's folder name as the host spells it, against its folders.

        An existing folder is looked up in ``listing``; a new one is placed
        where the listing's delimiter and prefix say it belongs.
        """

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
    # Describing the host
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

    @classmethod
    @abstractmethod
    def count_support(cls, capabilities: list[str]) -> CountSupport:
        """Whether the mail half's advertised capabilities let every
        folder be counted in one request, and with sizes."""

    @classmethod
    @abstractmethod
    def sorts_messages(cls, capabilities: list[str]) -> bool:
        """Whether a mail half advertising ``capabilities`` orders a search
        itself, so :meth:`Transport.sort_messages` may be asked; where it
        does not, the utilities order what they fetched."""

    @classmethod
    @abstractmethod
    def drift_terms(cls) -> DriftTerms:
        """How to read a change between two probes of this host."""


# ############################################################################
# The transport -- communication with the host's servers, and nothing else
# ############################################################################

# The two halves of a transport, each its own connection.
RULES = "rules"
MAIL = "mail"

# What an operation does to the server. A read may be sent again after the
# connection is re-made; a write may have landed before the connection
# went, so it never is. A connection operation manages or describes the
# connection itself and is no exchange with the server.
READ = "read"
WRITE = "write"
CONNECTION = "connection"


@dataclass(frozen=True)
class Operation:
    """What one transport operation is: its half, and read or write.

    ``half`` is :data:`RULES` or :data:`MAIL`, or None for a connection
    operation, which belongs to neither.
    """

    kind: str
    half: str | None = None


# ----------------------------------------------------------------------------
def classified(operation: Operation) -> Callable[[Callable], Callable]:
    """Declare what a transport operation is, on the interface itself.

    Declared once, on :class:`Transport`, so every provider's operation of
    the same name is the same kind; the session reads it from there.
    """

    def mark(function: Callable) -> Callable:
        function.__operation__ = operation  # type: ignore[attr-defined]

        return function

    return mark


class Transport(ABC):
    """One host's servers, spoken to and nothing else.

    Each operation is one exchange with a server, or a composite the host
    performs natively (a move and its COPY + EXPUNGE fallback). A transport
    neither builds nor validates what it is handed: it tries to store it
    and reports success, or raises ``MailctlError`` with the server's
    answer.

    Each half is its own connection, made by :meth:`connect` and dropped by
    :meth:`disconnect`; an instance is made, connected to nothing, by
    :meth:`open`. When to connect, and whether to try again after the
    server has gone, is the session's (``mailctl.engine``): it reads what
    each operation is -- which half, read or write -- from the
    classification declared here, never from a provider.
    """

    name: ClassVar[str]

    # ------------------------------------------------------------------------
    # The connection
    # ------------------------------------------------------------------------

    @classmethod
    @abstractmethod
    @classified(Operation(CONNECTION))
    def open(
        cls, config: Config, *, progress: Progress | None = None
    ) -> "Transport":
        """A transport for ``config``, connected to nothing yet."""

    @abstractmethod
    @classified(Operation(CONNECTION))
    def connect(self, half: str) -> None:
        """Connect ``half`` (:data:`RULES` or :data:`MAIL`) unless it is
        connected already. A half with nothing to connect with is left as
        it is, and its operations raise."""

    @abstractmethod
    @classified(Operation(CONNECTION))
    def disconnect(self, half: str) -> None:
        """Close ``half``, ignoring a connection already gone; it can be
        connected again."""

    @abstractmethod
    @classified(Operation(CONNECTION))
    def dropped(self, error: BaseException) -> bool:
        """Whether ``error`` means the server closed or lost the
        connection, rather than refusing what it was asked."""

    # ------------------------------------------------------------------------
    # The rule half
    # ------------------------------------------------------------------------

    @property
    @abstractmethod
    @classified(Operation(CONNECTION))
    def has_rules(self) -> bool:
        """Whether the rule half is connected or can be."""

    @abstractmethod
    @classified(Operation(READ, RULES))
    def rules_capabilities(self) -> list[str]:
        """What the rule half advertises, as the host names it."""

    @abstractmethod
    @classified(Operation(READ, RULES))
    def list_rule_sets(self) -> tuple[str | None, list[str]]:
        """``(active, others)``."""

    @abstractmethod
    @classified(Operation(READ, RULES))
    def active_rule_set(self) -> str | None:
        """The name of the rule set that runs, or None."""

    @abstractmethod
    @classified(Operation(READ, RULES))
    def read_rule_set(self, name: str) -> str:
        """A stored rule set, exactly as the host holds it."""

    @abstractmethod
    @classified(Operation(READ, RULES))
    def check_rule_set(self, source: str) -> None:
        """Have the host validate a rule set without storing it; raise
        with the host's verdict when it refuses."""

    @abstractmethod
    @classified(Operation(WRITE, RULES))
    def store_rule_set(self, name: str, source: str) -> None:
        """Store a rule set under ``name``."""

    @abstractmethod
    @classified(Operation(WRITE, RULES))
    def activate_rule_set(self, name: str) -> None:
        """Make ``name`` the rule set that runs."""

    @abstractmethod
    @classified(Operation(READ, RULES))
    def describe_rules_server(self) -> ServerDescription:
        """What the rule half's server says about itself: its identity and
        every capability it advertised, not only its extensions."""

    # ------------------------------------------------------------------------
    # The mail half -- folders
    # ------------------------------------------------------------------------

    @property
    @abstractmethod
    @classified(Operation(CONNECTION))
    def has_mail(self) -> bool:
        """Whether the mail half is connected or can be."""

    @abstractmethod
    @classified(Operation(READ, MAIL))
    def mail_capabilities(self) -> list[str]:
        """What the mail half advertises, as the host names it."""

    @abstractmethod
    @classified(Operation(READ, MAIL))
    def list_folders(self) -> FolderListing:
        """The folders in the host's order, the delimiter, the subscribed
        ones, and where a new folder goes."""

    @abstractmethod
    @classified(Operation(WRITE, MAIL))
    def create_folder(self, folder: str) -> None:
        """Create a folder, as named; subscribing to it is a second step."""

    @abstractmethod
    @classified(Operation(WRITE, MAIL))
    def subscribe(self, folder: str) -> None:
        """Subscribe to a folder, confirming it took effect."""

    @abstractmethod
    @classified(Operation(WRITE, MAIL))
    def unsubscribe(self, folder: str) -> None:
        """Unsubscribe from a folder."""

    @abstractmethod
    @classified(Operation(READ, MAIL))
    def describe_mail_server(self) -> ServerDescription:
        """What the mail half's server says about itself: its identity and
        every capability it advertised."""

    @abstractmethod
    @classified(Operation(READ, MAIL))
    def folder_status(self, sizes: bool) -> list[FolderStatus]:
        """Every folder's counts, in one request; ``size`` too when
        ``sizes`` is set. A folder the host gives no counts for is left
        out. The utility has asked :meth:`Dialect.count_support` first."""

    @abstractmethod
    @classified(Operation(READ, MAIL))
    def mail_namespaces(self) -> list[Namespace]:
        """Every namespace the mail half reports, in its order; empty
        where it reports none."""

    # ------------------------------------------------------------------------
    # The mail half -- the existing-mail pass and messages
    # ------------------------------------------------------------------------

    @abstractmethod
    @classified(Operation(READ, MAIL))
    def search(self, folder: str, criteria: Criteria) -> list[int]:
        """The UIDs the host's own search matches for ``criteria``.

        Candidates, read-only: a host search may be coarser than a rule's
        comparison, and the utilities re-check what comes back.
        """

    @abstractmethod
    @classified(Operation(READ, MAIL))
    def search_messages(self, folder: str, expression: str) -> list[int]:
        """The UIDs a host-native search expression matches."""

    @abstractmethod
    @classified(Operation(READ, MAIL))
    def uidvalidity(self, folder: str) -> int | None:
        """What the UIDs in ``folder`` are valid under: a UID names the
        same message only while this is unchanged. None where the host
        reported none."""

    @abstractmethod
    @classified(Operation(READ, MAIL))
    def fetch_headers(
        self, uids: list[int], folder: str
    ) -> list[FetchedMessage]:
        """The headers and date of ``uids`` in ``folder``, in UID order;
        one per message the host still has."""

    @abstractmethod
    @classified(Operation(READ, MAIL))
    def fetch_summaries(
        self, uids: list[int], folder: str
    ) -> list[FetchedMessage]:
        """What a listing shows of ``uids`` in ``folder``, in the order
        asked for; one per message the host still has."""

    @abstractmethod
    @classified(Operation(WRITE, MAIL))
    def apply_mail(self, plan: MailActionPlan) -> MailActionResult:
        """Carry out a plan: flag, then move or delete, its messages.

        A plan that copies is not handed here: its copy is
        :meth:`copy_messages`, a write of its own.
        """

    @abstractmethod
    @classified(Operation(WRITE, MAIL))
    def copy_messages(
        self, folder: str, uids: list[int], destination: str
    ) -> int:
        """Copy ``uids`` from ``folder`` into ``destination``, leaving them
        in ``folder``; return how many were copied.

        Not undone by running it again: a second call files second copies.
        """

    @abstractmethod
    @classified(Operation(WRITE, MAIL))
    def add_flags(
        self, folder: str, uids: list[int], flags: list[str]
    ) -> None:
        """Add ``flags`` to ``uids`` in ``folder``, as the host names them.

        A flag a message has already is left as it is. Removing one is
        :meth:`remove_flags`, a write of its own.
        """

    @abstractmethod
    @classified(Operation(WRITE, MAIL))
    def remove_flags(
        self, folder: str, uids: list[int], flags: list[str]
    ) -> None:
        """Remove ``flags`` from ``uids`` in ``folder``; a flag a message
        does not have is no error."""

    @abstractmethod
    @classified(Operation(READ, MAIL))
    def message_headers(self, folder: str, uid: int) -> Message:
        """One message's headers, as an ``email.message.Message``."""

    @abstractmethod
    @classified(Operation(READ, MAIL))
    def message_source(
        self, folder: str, uid: int
    ) -> tuple[bytes, tuple[str, ...]]:
        """One message's exact bytes and its flags, left unread."""

    @abstractmethod
    @classified(Operation(READ, MAIL))
    def sort_messages(
        self,
        folder: str,
        order: SortOrder,
        criteria: Criteria | None,
        expression: str | None,
    ) -> list[int]:
        """The UIDs :meth:`search` (for ``criteria``) or
        :meth:`search_messages` (for ``expression``) would return, or every
        message's with neither, in ``order``, the host sorting them.

        Asked only where the dialect's :meth:`Dialect.sorts_messages` says
        the server can; the utilities still re-check what comes back.
        """

    @abstractmethod
    @classified(Operation(WRITE, MAIL))
    def rename_folder(self, old: str, new: str) -> None:
        """Rename a folder, and every folder under it, as the host does.

        Subscriptions are not carried: IMAP's RENAME leaves them as they
        were (RFC 3501 section 6.3.5), so that is a step of its own.
        """

    @abstractmethod
    @classified(Operation(READ, MAIL))
    def message_count(self, folder: str) -> int:
        """How many messages ``folder`` holds, without selecting it."""


# ############################################################################
# The provider -- one host, as the registry names it
# ############################################################################


@dataclass(frozen=True)
class Provider:
    """One host: what it can do, and the two halves that do it.

    ``name`` is what the ``provider`` setting selects it by, and both
    halves carry the same name, for their refusals. ``dialect`` needs no
    connection; ``transport`` is opened by the session.
    """

    name: str
    capabilities: ProviderCapabilities
    dialect: type[Dialect]
    transport: type[Transport]

    # ------------------------------------------------------------------------
    @property
    def wording(self) -> Wording:
        """The words the host is described in; the dialect's."""
        return self.dialect.wording


# Every operation of each half, by name. The exhaustiveness test holds
# these equal to the abstract methods above, so they cannot drift from
# them, and holds the two apart, so a name says which half it is on.
DIALECT_OPERATIONS = tuple(sorted(Dialect.__abstractmethods__))
TRANSPORT_OPERATIONS = tuple(sorted(Transport.__abstractmethods__))
OPERATIONS = DIALECT_OPERATIONS + TRANSPORT_OPERATIONS


# ----------------------------------------------------------------------------
def _operation(name: str) -> Operation | None:
    """What the interface declares transport operation ``name`` to be."""
    member = Transport.__dict__.get(name)
    function = getattr(member, "fget", None) or getattr(
        member, "__func__", member
    )

    return getattr(function, "__operation__", None)


# Every transport operation and what it is, as declared above. An
# operation missing here is one the session cannot guard, and the
# classification test holds that none is.
TRANSPORT_KINDS: Mapping[str, Operation] = {
    name: kind
    for name in TRANSPORT_OPERATIONS
    if (kind := _operation(name)) is not None
}
