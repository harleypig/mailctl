"""The provider-neutral model: the records the engine speaks (ADR 0006).

Every provider translates to and from these, and the engine and every
front-end build and read nothing else. They are defined here, not borrowed
from a layer-1 library: a protocol component keeps whatever records suit
its protocol, and the provider that composes it converts between the two.
That is what lets a provider with no Sieve and no IMAP import this module
without importing either protocol.

All of it is plain data. Nothing here speaks a protocol, reads a
configuration, or prints.
"""

from dataclasses import dataclass, field
from email.errors import HeaderParseError
from email.header import Header, decode_header, make_header

from ..config import Source

__all__ = [
    "DISCARD",
    "FILEINTO",
    "FLAG",
    "KEEP",
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
    "Wording",
    "action_names",
    "decode_header_value",
    "same_folder",
]

# The neutral names of the things a rule can do, as ActionSpec spells them.
FILEINTO = "fileinto"
DISCARD = "discard"
FLAG = "flag"
KEEP = "keep"

# Where a rule goes in evaluation order, for a provider that declares
# ``ordering``. Position is part of what a rule means there, so appending
# is a choice rather than a neutral default, and these are the vocabulary
# for saying so.
PLACE_FIRST = "first"
PLACE_LAST = "last"
PLACE_BEFORE = "before"
PLACE_AFTER = "after"


# ############################################################################
# Folder names and headers -- the two readings every record shares
# ############################################################################


# ----------------------------------------------------------------------------
def same_folder(left: str, right: str) -> bool:
    """Whether two folder names name the same folder.

    Names are case-sensitive except ``INBOX`` itself (RFC 3501 section
    5.1): ``INBOX.Foo`` and ``INBOX.foo`` are two folders, so folding case
    everywhere would treat a move between them as a no-op.
    """
    if left.upper() == "INBOX" and right.upper() == "INBOX":
        return True

    return left == right


# ----------------------------------------------------------------------------
def decode_header_value(raw: "str | Header") -> str:
    """Decode RFC 2047 encoded words, falling back to the raw value.

    A raw 8-bit header (RFC 6532) is read as the UTF-8 it usually is,
    rather than as replacement characters; one that is not valid UTF-8 is
    read as before.
    """
    raw = _utf8_header(raw)

    try:
        return str(make_header(decode_header(raw)))

    # HeaderParseError is not a ValueError: a bad base64 encoded word
    # raises it, and one such header must not abort a whole listing.
    except (UnicodeDecodeError, LookupError, ValueError, HeaderParseError):
        return str(raw)


# ----------------------------------------------------------------------------
def _utf8_header(raw: "str | Header") -> "str | Header":
    """Read ``email``'s ``unknown-8bit`` chunks as UTF-8 where they are.

    The IMAP component has the same reading for its search re-check; the
    layers may not import each other, so each keeps its own (ADR 0006).
    """
    if not isinstance(raw, Header):
        return raw

    try:
        return "".join(
            chunk.decode(
                "utf-8" if charset in (None, "unknown-8bit") else charset
            )
            if isinstance(chunk, bytes)
            else chunk
            for chunk, charset in decode_header(raw)
        )

    except (UnicodeError, LookupError):
        return raw


# ############################################################################
# Rules
# ############################################################################


@dataclass(frozen=True)
class ActionSpec:
    """What a rule, or the existing-mail pass, should do to a message.

    ``fileinto`` is the folder as the user named it, before normalization;
    None falls back to ``Config.default_folder``. ``flags`` are IMAP flag
    names, unescaped and in the order they should be added.

    ``stop`` None is the provider's default: the rule ends evaluation where
    the provider declares ``stop`` and simply runs where it does not.
    True or False is an explicit request, and True is refused by a
    provider without ``stop``.
    """

    fileinto: str | None = None
    discard: bool = False
    flags: tuple[str, ...] = ()
    keep: bool = False
    stop: bool | None = None


# ----------------------------------------------------------------------------
def action_names(spec: ActionSpec, folder: str) -> frozenset[str]:
    """The neutral actions a spec asks for, given its resolved folder."""
    names = set()

    if spec.flags:
        names.add(FLAG)

    if spec.discard:
        names.add(DISCARD)

    elif folder:
        names.add(FILEINTO)

    if spec.keep:
        names.add(KEEP)

    return frozenset(names)


@dataclass(frozen=True)
class Placement:
    """A request to put a rule somewhere, resolved against a real rule set.

    ``anchor`` names the existing rule that ``before`` and ``after`` are
    relative to, and is unused by ``first`` and ``last``. Carrying the pair
    as one value rather than as two parallel parameters is what stops a
    caller passing an anchor with nowhere for it to apply.
    """

    where: str
    anchor: str | None = None


@dataclass(frozen=True)
class DisplayDiff:
    """A diff meant for a person, and the one thing it deliberately hides.

    ``reformats`` is true when the rule set the host holds is not already
    in the formatting the provider writes, so the upload rewrites more than
    ``text`` shows; carrying the flag beside the diff is what lets the
    front-end say so. ``label`` names what was diffed in the host's own
    word -- ``sieve`` for a Sieve script -- for the heading above it.
    """

    text: str
    reformats: bool
    label: str


@dataclass(frozen=True)
class ExtensionState:
    """One rule-language extension as a run of mailctl sees it.

    ``advertised`` is whether the server lists it; ``required`` is whether
    mailctl's own rules can need it. ``disabled_by`` is where
    ``disabled_extensions`` came from when the name is in it, else None.
    """

    name: str
    advertised: bool
    required: bool = False
    disabled_by: Source | None = None

    # ------------------------------------------------------------------------
    @property
    def enabled(self) -> bool | None:
        """Whether mailctl may use it; None when the server lacks it.

        Disabling is a narrowing of what mailctl emits, never a claim about
        the server, so for an unadvertised name it decides nothing.
        """
        if not self.advertised:
            return None

        return self.disabled_by is None


@dataclass(frozen=True)
class DeliveryCreate:
    """Whether a rule can create its target folder when mail arrives.

    ``advertised`` is whether the host offers it at all; ``disabled_by``
    names the setting that turned it off, when one did.
    """

    advertised: bool
    disabled_by: Source | None = None

    # ------------------------------------------------------------------------
    @property
    def usable(self) -> bool:
        return self.advertised and self.disabled_by is None


# ############################################################################
# Folders
# ############################################################################


@dataclass(frozen=True)
class FolderListing:
    """The account's folders, the hierarchy delimiter, and which folders
    are subscribed -- the ones webmail actually draws."""

    delimiter: str
    folders: list[str]
    subscribed: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------------
    def is_subscribed(self, folder: str) -> bool:
        return any(same_folder(name, folder) for name in self.subscribed)

    # ------------------------------------------------------------------------
    @property
    def unsubscribed(self) -> list[str]:
        """Folders that exist but that webmail will not show."""
        return [name for name in self.folders if not self.is_subscribed(name)]


@dataclass(frozen=True)
class FolderCreation:
    """What creating a folder actually achieved.

    Creating and subscribing are two steps, so they can disagree: the
    folder can exist while the subscription that makes it visible does
    not. Reporting them as one boolean would lose exactly the case this
    record exists for.

    ``subscribed`` false with an empty ``subscribe_error`` means the caller
    declined to subscribe; with an error it means the attempt failed. The
    folder exists either way -- mail filed there will arrive.
    """

    folder: str
    subscribed: bool
    subscribe_error: str = ""


# ############################################################################
# Messages and the existing-mail pass
# ############################################################################


@dataclass(frozen=True)
class MessageSummary:
    """One matched message, as data.

    Deliberately carries no rendering of itself: how a message is displayed
    is the front-end's business.
    """

    uid: int
    date: str
    sender: str
    subject: str
    folder: str
    size: int = 0
    flags: tuple[str, ...] = ()
    has_attachments: bool = False


@dataclass(frozen=True)
class MailActionPlan:
    """What the existing-mail pass would do, worked out but not yet done.

    Produced by a read-only selection, so building a plan is always safe.
    The front-end decides whether to render it, confirm it, or have it
    carried out -- which is what makes a dry run a matter of not executing
    it.
    """

    source: str
    destination: str
    flags: list[str]
    discard: bool
    messages: list[MessageSummary] = field(default_factory=list)

    # ------------------------------------------------------------------------
    @property
    def count(self) -> int:
        """How many messages the plan covers."""
        return len(self.messages)

    # ------------------------------------------------------------------------
    @property
    def uids(self) -> list[int]:
        """The UIDs the plan would act on."""
        return [message.uid for message in self.messages]

    # ------------------------------------------------------------------------
    @property
    def moves(self) -> bool:
        """Whether executing this plan relocates mail."""
        return bool(
            self.destination
            and not self.discard
            and not same_folder(self.destination, self.source)
        )

    # ------------------------------------------------------------------------
    @property
    def is_empty(self) -> bool:
        """Whether the plan would do nothing at all."""
        return not self.messages


@dataclass(frozen=True)
class MailActionResult:
    """What carrying out a plan actually did."""

    flagged: int = 0
    moved: int = 0
    deleted: int = 0


# ############################################################################
# The host, in its own words
# ############################################################################


@dataclass(frozen=True)
class Fact:
    """One labelled line about the host, for a front-end to show.

    ``text`` may run to several lines. ``settings`` pairs a label with each
    setting the line reports, as ``(label, setting)``, so a front-end can
    say where each value came from; it is empty for a fact the server
    reported.
    """

    label: str
    text: str
    settings: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Wording:
    """The words a provider's host is described in, as data.

    A front-end lays out the same report for every provider and fills it
    from here, so nothing about one host is written into the front-end.

    * ``rules_service`` / ``mail_service`` -- what each half connects to,
      as a person would name it (``ManageSieve``, ``IMAP``).
    * ``extensions`` -- what the rule language calls its extensions, for
      a provider that declares ``extensions``.
    * ``notes`` -- what a report about the host should end by saying: the
      host's policies and the limits of what mailctl can see there.
    """

    rules_service: str
    mail_service: str
    extensions: str
    notes: tuple[str, ...] = ()
