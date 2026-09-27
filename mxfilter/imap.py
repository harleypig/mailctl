"""IMAP access for the existing-mail pass.

Wraps IMAPClient with the three things the CLI actually needs: the folder
list plus its hierarchy delimiter, a search that is re-checked against the
real Sieve comparison semantics, and a move that degrades gracefully when
the server has no MOVE capability.

Folder naming is the reason the delimiter matters. A Maildir++ layout --
which MXRoute's published maildir paths suggest, though nothing documents
it -- spells a subfolder ``INBOX.Lists.GitHub``, while other layouts spell
the same thing ``Lists/GitHub``. Users should be able to type either, so
every folder name is normalized against the delimiter the server actually
reports. That detection is mandatory, not an optimization: guessing wrong
files mail into a folder nobody reads.

The same trap applies to the spam folder, which on MXRoute is
``INBOX.spam`` in lower case. Filing into ``Junk`` would silently create a
second folder next to the real one, so folder names are matched against the
server's own list (case-insensitively) rather than taken on trust.

Existing and visible are also two different questions, which is why both
folder views are cached. ``LIST`` reports what the account has; ``LSUB``
reports what the user subscribed to, and a webmail client -- Roundcube
included -- draws its folder tree from ``LSUB``. A folder that was created
but never subscribed therefore exists, receives mail, and is invisible in
webmail: the same failure as filing into the wrong folder, arrived at from
the other direction.
"""

import contextlib
import email
import socket
import ssl
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from email.errors import HeaderParseError
from email.header import decode_header, make_header

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError, LoginError

from . import MxFilterError
from .config import Config
from .criteria import Criteria

# UIDs per IMAP command. Every UID goes into the command line, and servers
# cap line length -- RFC 7162 section 4 asks clients to keep command lines
# under 8192 octets. 250 UIDs of up to ten digits plus separators is about
# 2.7 KB, leaving room for a long folder name. Kept well below the default
# --max-messages (500) so the two stay independent: the cap is a policy on
# how much to touch, this is transport and never the user's concern.
BULK_CHUNK = 250

# Every FETCH item here leaves \Seen alone. BODY[...] and RFC822 /
# RFC822.TEXT set it (RFC 3501 6.4.5); the .PEEK forms and the metadata
# items do not. Folders are also selected read-only (EXAMINE), which
# forbids the change outright -- two independent guards, because reading a
# message is not a reason to mark it read.
SUMMARY_ITEMS = [
    "BODY.PEEK[HEADER]",
    "INTERNALDATE",
    "RFC822.SIZE",
    "FLAGS",
    "BODYSTRUCTURE",
]
SOURCE_ITEMS = ["BODY.PEEK[]", "FLAGS"]

__all__ = [
    "FolderCreation",
    "ImapSession",
    "MailActionPlan",
    "MailActionResult",
    "MessageSummary",
    "PartialExecution",
    "case_variant_hint",
    "case_variants",
    "decode_header_value",
    "normalize_folder",
    "same_folder",
    "split_path",
]


# ############################################################################
# Folder naming
# ############################################################################


# ----------------------------------------------------------------------------
def split_path(name: str, delimiter: str) -> list[str]:
    """Split a user-supplied folder name into its components.

    Both ``/`` and the server's own delimiter are accepted as separators so
    that ``Lists/GitHub`` and ``INBOX.Lists.GitHub`` describe the same
    folder on a Maildir++ server.
    """
    separators = {"/", delimiter}
    parts = [name]

    for separator in separators:
        if not separator:
            continue

        parts = [piece for part in parts for piece in part.split(separator)]

    return [part for part in parts if part]


# ----------------------------------------------------------------------------
def same_folder(left: str, right: str) -> bool:
    """Whether two mailbox names name the same folder.

    Mailbox names are case-sensitive except ``INBOX`` itself (RFC 3501
    section 5.1), and Dovecot honours that: ``INBOX.Foo`` and ``INBOX.foo``
    are two folders. Folding case everywhere would treat a move between
    them as a no-op and silently skip it.
    """
    if left.upper() == "INBOX" and right.upper() == "INBOX":
        return True

    return left == right


# ----------------------------------------------------------------------------
def case_variants(name: str, known: list[str]) -> list[str]:
    """The known folders that differ from ``name`` only in case.

    Name matching is exact (``same_folder``), which makes these the
    folders a user most likely meant: ones the lookup does not resolve to,
    and ones a create would put a second folder beside.
    """
    return [
        folder
        for folder in known
        if folder.casefold() == name.casefold()
        and not same_folder(folder, name)
    ]


# ----------------------------------------------------------------------------
def case_variant_hint(name: str, known: list[str]) -> str:
    """A sentence naming the case variants of ``name``, or nothing.

    For an error about a folder that is missing: the likeliest reason is
    that the one meant is spelled with different case.
    """
    variants = case_variants(name, known)

    if not variants:
        return ""

    listed = ", ".join(repr(folder) for folder in variants)

    return (
        f"{listed} {'exists' if len(variants) == 1 else 'exist'}, but "
        f"folder names are case-sensitive. "
    )


# ----------------------------------------------------------------------------
def normalize_folder(
    name: str, delimiter: str, known: list[str] | None = None
) -> str:
    """Return the server's spelling of a user-supplied folder name.

    When the folder list is available the answer is looked up rather than
    guessed. The lookup is exact except for ``INBOX`` (``same_folder``),
    so a folder whose case differs is not a match -- ``case_variants``
    finds those. The fallback only kicks in for a folder that does not
    exist yet: on a Maildir++ server (delimiter ``.``) a new folder belongs
    under ``INBOX``, while a ``/``-delimited server keeps it as a top-level
    sibling.
    """
    components = split_path(name, delimiter)

    if not components:
        raise MxFilterError("empty folder name")

    candidate = delimiter.join(components)

    if components[0].upper() == "INBOX":
        candidate = delimiter.join(["INBOX", *components[1:]])

    for option in (candidate, f"INBOX{delimiter}{candidate}"):
        for folder in known or ():
            if same_folder(folder, option):
                return folder

    if delimiter == "." and components[0].upper() != "INBOX":
        return delimiter.join(["INBOX", *components])

    return candidate


@dataclass(frozen=True)
class FolderCreation:
    """What creating a folder actually achieved.

    Creating and subscribing are two IMAP operations, so they can disagree:
    the folder can exist while the subscription that makes it visible does
    not. Reporting them as one boolean would lose exactly the case this
    record exists for, so the outcome is returned rather than reduced to
    "it worked".

    ``subscribed`` false with an empty ``subscribe_error`` means the caller
    declined to subscribe; with an error it means the attempt failed. The
    folder exists either way -- mail filed there will arrive.
    """

    folder: str
    subscribed: bool
    subscribe_error: str = ""


# ############################################################################
# Messages
# ############################################################################


@dataclass(frozen=True)
class MessageSummary:
    """One matched message, as data.

    Deliberately carries no rendering of itself: how a message is displayed
    is the front-end's business, and a record that knows how to draw itself
    is the thing a second front-end has to work around.
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

    Produced by a read-only search, so building a plan is always safe. The
    caller decides whether to render it, confirm it, or execute it -- which
    is what makes a dry run a matter of not calling ``execute``, rather than
    of a flag changing what some deeper function prints.
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
    """What executing a plan actually did."""

    flagged: int = 0
    moved: int = 0
    deleted: int = 0


class PartialExecution(MxFilterError):
    """A chunked pass failed after some chunks were already applied.

    ``result`` counts what completed -- every chunk before the failing one
    was flagged and moved or deleted in full -- and ``total`` is the size
    of the whole plan.
    """

    # ------------------------------------------------------------------------
    def __init__(
        self,
        cause: Exception,
        result,
        total: int,
        fallback: bool,
        destination: str = "",
    ):
        self.result = result
        self.total = total

        done = result.moved or result.deleted or result.flagged

        # Whether a re-run is safe depends on the mode, and saying "safe"
        # where it is not is the dangerous error. MOVE is atomic per command
        # and a delete or flag re-applies harmlessly, so the search simply
        # finds what is left. The COPY fallback is the exception: a batch
        # can be copied and not yet expunged, so it still matches in the
        # source and a re-run copies it a second time.
        if fallback:
            advice = (
                f"The failing batch may have been copied to {destination!r} "
                f"but not yet removed from the source (this server has no "
                f"MOVE), so a message can appear in both. Re-running copies "
                f"them again: check {destination!r} for copies from that "
                f"batch first, or remove the duplicates afterwards."
            )

        else:
            advice = (
                "The failing batch may be partly applied. Re-running the "
                "same command is safe: it searches again, so mail already "
                "moved or deleted is not matched twice, and a flag already "
                "set stays set."
            )

        super().__init__(
            f"{cause} -- stopped part-way: {done} of {total} message(s) were "
            f"fully processed, in batches of {BULK_CHUNK}; later batches were "
            f"not touched. {advice}"
        )


# ----------------------------------------------------------------------------
def chunked(items: list, size: int = BULK_CHUNK):
    """Yield ``items`` in consecutive slices of at most ``size``."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


# ----------------------------------------------------------------------------
def decode_header_value(raw: str) -> str:
    """Decode RFC 2047 encoded words, falling back to the raw value."""
    try:
        return str(make_header(decode_header(raw)))

    # HeaderParseError is not a ValueError: a bad base64 encoded word
    # raises it, and one such header must not abort a whole listing.
    except (UnicodeDecodeError, LookupError, ValueError, HeaderParseError):
        return raw


# ----------------------------------------------------------------------------
def header_values(message) -> dict[str, list[str]]:
    """Map upper-cased header names to every occurrence of that header.

    Each occurrence contributes both its decoded and its raw form. Sieve
    compares against the MIME-decoded value, so that is the one that
    matters; keeping the raw form as well means a search for the literal
    encoded text still finds its message, and costs only a wider candidate
    set.
    """
    collected: dict[str, list[str]] = {}

    for name, raw in message.items():
        key = name.upper()
        decoded = decode_header_value(raw)

        values = collected.setdefault(key, [])
        values.append(decoded)

        if decoded != raw:
            values.append(raw)

    return collected


# ----------------------------------------------------------------------------
def flag_names(raw) -> tuple[str, ...]:
    """Turn a FETCH FLAGS response into plain strings."""
    return tuple(
        flag.decode("ascii", "replace") if isinstance(flag, bytes) else flag
        for flag in raw or ()
    )


# ----------------------------------------------------------------------------
def structure_has_attachment(node) -> bool:
    """Whether a parsed BODYSTRUCTURE holds a part that is not message text.

    The same line ``engine.parse_message`` draws on the full source: a
    text/plain or text/html part is text unless it is marked as an
    attachment or carries a file name; any other leaf -- a PDF, an inline
    image, an attached message -- is an attachment. Cheap because it reads
    only the structure the server already sent with the summary.
    """
    if not isinstance(node, tuple) or not node:
        return False

    if isinstance(node[0], list):
        return any(structure_has_attachment(part) for part in node[0])

    kind = (
        f"{_ascii(node[0])}/{_ascii(node[1])}".lower() if len(node) > 1 else ""
    )

    if kind not in ("text/plain", "text/html"):
        return True

    params = node[2] if len(node) > 2 and isinstance(node[2], tuple) else ()

    if any(_ascii(item).lower() == "name" for item in params[::2]):
        return True

    # The disposition's position shifts with the part type, but it is the
    # only extension field shaped (bytes, tuple-or-None).
    for item in node[7:]:
        if (
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], bytes)
            and (item[1] is None or isinstance(item[1], tuple))
        ):
            fields = item[1] or ()

            return _ascii(item[0]).lower() == "attachment" or any(
                _ascii(name).lower() == "filename" for name in fields[::2]
            )

    return False


# ----------------------------------------------------------------------------
def _ascii(value) -> str:
    """Render a BODYSTRUCTURE atom, which IMAPClient hands over as bytes."""
    if isinstance(value, bytes):
        return value.decode("ascii", "replace")

    return "" if value is None else str(value)


# ----------------------------------------------------------------------------
def summarize(uid: int, data: dict, message, folder: str) -> MessageSummary:
    """Build a summary from one FETCH response and its parsed headers.

    Items the FETCH did not ask for come back as defaults, so the
    existing-mail pass and the message listing share this one reading.
    """
    internal = data.get(b"INTERNALDATE")

    return MessageSummary(
        uid=uid,
        date=internal.strftime("%Y-%m-%d %H:%M:%S") if internal else "",
        sender=decode_header_value(message.get("From", "")),
        subject=decode_header_value(message.get("Subject", "")),
        folder=folder,
        size=data.get(b"RFC822.SIZE") or 0,
        flags=flag_names(data.get(b"FLAGS")),
        has_attachments=structure_has_attachment(data.get(b"BODYSTRUCTURE")),
    )


# ############################################################################
# Session
# ############################################################################


class ImapSession:
    """A connected IMAP client scoped to one account."""

    # ------------------------------------------------------------------------
    def __init__(
        self,
        config: Config,
        progress: Callable[[str], None] | None = None,
    ):
        """Record the settings; no connection is made until ``open()``.

        ``progress`` receives step-by-step messages, as a callback rather
        than a print so this module carries no presentation of its own.
        """
        self.config = config
        self.progress = progress
        self.client: IMAPClient | None = None
        self._delimiter = "."
        self._folders: list[str] = []
        self._subscribed: list[str] = []

    # ------------------------------------------------------------------------
    def __enter__(self) -> "ImapSession":
        self.open()

        return self

    # ------------------------------------------------------------------------
    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.close()

        return False

    # ------------------------------------------------------------------------
    def _log(self, message: str) -> None:
        """Hand a progress message to the caller's callback, if any."""
        if self.progress is not None:
            self.progress(message)

    # ------------------------------------------------------------------------
    def open(self) -> None:
        """Connect, authenticate, and read the folder list."""
        config = self.config
        config.require("imap_host", "user")

        use_ssl = config.imap_port != 143

        self._log(
            f"connecting to {config.imap_host}:{config.imap_port} "
            f"(ssl={use_ssl}) as {config.user}"
        )

        try:
            client = IMAPClient(
                config.imap_host, port=config.imap_port, ssl=use_ssl
            )

            if not use_ssl:
                client.starttls()

            client.login(config.user, config.password().reveal())

        except LoginError as exc:
            raise MxFilterError(
                f"IMAP authentication failed for {config.user!r} (password "
                f"{config.password_state()}). MXRoute expects the FULL "
                f"email address as the username, e.g. you@yourdomain.com. "
                f"[{exc}]"
            ) from exc

        except ssl.SSLError as exc:
            raise MxFilterError(
                f"TLS failure against {config.imap_host}:{config.imap_port} "
                f"-- {exc}. Port 993 is implicit TLS; port 143 uses STARTTLS."
            ) from exc

        except (socket.gaierror, OSError) as exc:
            raise MxFilterError(
                f"cannot reach {config.imap_host}:{config.imap_port} -- "
                f"{exc}. Check --imap-host and --imap-port."
            ) from exc

        except IMAPClientError as exc:
            raise MxFilterError(f"IMAP error -- {exc}") from exc

        self.client = client
        self._read_folders()
        self._log(
            f"connected; delimiter {self._delimiter!r}, "
            f"{len(self._folders)} folders, "
            f"{len(self._subscribed)} subscribed"
        )

    # ------------------------------------------------------------------------
    def close(self) -> None:
        """Log out, ignoring a connection that has already gone away."""
        if self.client is None:
            return

        with contextlib.suppress(IMAPClientError, OSError):
            self.client.logout()

        self.client = None

    # ------------------------------------------------------------------------
    def _require_client(self) -> IMAPClient:
        """Return the live client or fail loudly."""
        if self.client is None:
            raise MxFilterError("IMAP session is not open")

        return self.client

    # ------------------------------------------------------------------------
    def _read_folders(self) -> None:
        """Cache both folder views, plus the hierarchy delimiter.

        LIST and LSUB answer different questions -- what exists, and what a
        client will draw -- and the second one is not derivable from the
        first. Reading only LIST is what let a created-but-unsubscribed
        folder look present to this tool while being invisible in webmail.

        An LSUB failure is raised rather than shrugged off, because an
        empty subscription list is indistinguishable from "nothing is
        subscribed": the tool would then report the very condition it is
        supposed to detect, confidently and wrongly.
        """
        client = self._require_client()

        try:
            listing = client.list_folders()

        except IMAPClientError as exc:
            raise MxFilterError(f"LIST failed -- {exc}") from exc

        try:
            subscribed = client.list_sub_folders()

        except IMAPClientError as exc:
            raise MxFilterError(f"LSUB failed -- {exc}") from exc

        self._folders, delimiter = self._decode_listing(listing)
        self._subscribed, _ = self._decode_listing(subscribed)

        if delimiter:
            self._delimiter = delimiter

    # ------------------------------------------------------------------------
    @staticmethod
    def _decode_listing(listing) -> tuple[list[str], str]:
        """Turn a LIST/LSUB response into names and the delimiter it used."""
        folders = []
        delimiter = ""

        for _flags, separator, name in listing:
            if separator:
                delimiter = (
                    separator.decode()
                    if isinstance(separator, bytes)
                    else str(separator)
                )

            folders.append(
                name.decode() if isinstance(name, bytes) else str(name)
            )

        return folders, delimiter

    # ------------------------------------------------------------------------
    @property
    def delimiter(self) -> str:
        """The server's folder hierarchy delimiter."""
        return self._delimiter

    # ------------------------------------------------------------------------
    @property
    def folders(self) -> list[str]:
        """Every folder the account can see."""
        return list(self._folders)

    # ------------------------------------------------------------------------
    @property
    def subscribed_folders(self) -> list[str]:
        """Every folder the account is subscribed to (LSUB)."""
        return list(self._subscribed)

    # ------------------------------------------------------------------------
    def capabilities(self) -> list[str]:
        """Return the advertised IMAP capabilities as strings."""
        client = self._require_client()

        return [
            item.decode() if isinstance(item, bytes) else str(item)
            for item in client.capabilities()
        ]

    # ------------------------------------------------------------------------
    def normalize(self, name: str) -> str:
        """Normalize a folder name against this server's naming."""
        return normalize_folder(name, self._delimiter, self._folders)

    # ------------------------------------------------------------------------
    def exists(self, folder: str) -> bool:
        """Whether a (already normalized) folder exists, matched exactly
        except for ``INBOX`` (``same_folder``)."""
        return any(
            same_folder(candidate, folder) for candidate in self._folders
        )

    # ------------------------------------------------------------------------
    def case_variants(self, folder: str) -> list[str]:
        """Existing folders that differ from ``folder`` only in case."""
        return case_variants(folder, self._folders)

    # ------------------------------------------------------------------------
    def is_subscribed(self, folder: str) -> bool:
        """Whether a (already normalized) folder is subscribed to.

        Answered from LSUB, never from LIST: existing is what LIST reports,
        and being drawn in a mail client is what this reports.
        """
        return any(
            same_folder(candidate, folder) for candidate in self._subscribed
        )

    # ------------------------------------------------------------------------
    def subscribe(self, folder: str) -> None:
        """Subscribe to a folder, and confirm the server agrees it took.

        The confirmation is not ceremony. No server advertises whether its
        CREATE subscribes on its own, and a SUBSCRIBE that returns OK is
        still only the server's word for it -- so the only way to know is
        to re-read LSUB and look. Assuming the write took is precisely the
        mistake that shipped the invisible folder.
        """
        client = self._require_client()
        self._log(f"subscribing to folder {folder!r}")

        try:
            client.subscribe_folder(folder)

        except IMAPClientError as exc:
            raise MxFilterError(
                f"could not subscribe to folder {folder!r} -- {exc}"
            ) from exc

        self._read_folders()

        if not self.is_subscribed(folder):
            raise MxFilterError(
                f"the server accepted SUBSCRIBE for {folder!r} but still "
                f"does not list it as subscribed (LSUB), so mail clients "
                f"will not show it"
            )

    # ------------------------------------------------------------------------
    def unsubscribe(self, folder: str) -> None:
        """Unsubscribe from a folder, hiding it from mail clients."""
        client = self._require_client()
        self._log(f"unsubscribing from folder {folder!r}")

        try:
            client.unsubscribe_folder(folder)

        except IMAPClientError as exc:
            raise MxFilterError(
                f"could not unsubscribe from folder {folder!r} -- {exc}"
            ) from exc

        self._read_folders()

    # ------------------------------------------------------------------------
    def create_folder(
        self, folder: str, subscribe: bool = True
    ) -> FolderCreation:
        """Create a folder, subscribe to it, and report what happened.

        Subscribing is the default because a folder made to be a
        ``fileinto`` target is by definition one the user is meant to see;
        an unsubscribed one receives mail that never appears in webmail.

        A failed subscription does **not** undo the creation and does not
        raise: the folder exists and mail filed there will arrive, so
        tearing it back down would trade a visibility problem for a data
        one. The outcome is returned instead, for the caller to say out
        loud.
        """
        client = self._require_client()
        self._log(f"creating folder {folder!r}")

        try:
            client.create_folder(folder)

        except IMAPClientError as exc:
            raise MxFilterError(
                f"could not create folder {folder!r} -- {exc}"
            ) from exc

        self._read_folders()

        if not subscribe:
            return FolderCreation(folder=folder, subscribed=False)

        try:
            self.subscribe(folder)

        except MxFilterError as exc:
            return FolderCreation(
                folder=folder, subscribed=False, subscribe_error=str(exc)
            )

        return FolderCreation(folder=folder, subscribed=True)

    # ------------------------------------------------------------------------
    def search(
        self, criteria: Criteria, folder: str, readonly: bool = True
    ) -> list[MessageSummary]:
        """Return the messages in ``folder`` that really match ``criteria``.

        The IMAP search narrows the mailbox; the fetched headers then decide.
        That second pass is not belt-and-braces -- IMAP can only substring
        match, so it is the only thing that makes ``--compare is`` and
        ``--compare matches`` mean the same here as they will in Sieve.
        """
        self._select(folder, readonly=readonly)

        uids = self._criteria_candidates(criteria, folder)

        if not uids:
            return []

        self._log(f"{len(uids)} candidate message(s); re-checking headers")

        return self._confirm(uids, criteria, folder)

    # ------------------------------------------------------------------------
    def _criteria_candidates(
        self, criteria: Criteria, folder: str
    ) -> list[int]:
        """Run the criteria's IMAP SEARCH in the selected folder."""
        client = self._require_client()

        key = criteria.imap_search_key()
        self._log(f"searching {folder!r} with {key}")

        try:
            return list(client.search(key))

        except IMAPClientError as exc:
            raise MxFilterError(
                f"IMAP search in {folder!r} failed -- {exc}"
            ) from exc

    # ------------------------------------------------------------------------
    def list_messages(
        self,
        folder: str,
        criteria: Criteria | None = None,
        expression: str | None = None,
        limit: int | None = None,
    ) -> tuple[list[MessageSummary], bool]:
        """Return up to ``limit`` matching messages, newest (highest UID)
        first, and whether candidates beyond the limit went unexamined.

        ``criteria`` is re-checked against the fetched headers exactly as
        ``search`` does; ``expression`` is a raw IMAP SEARCH taken as given;
        with neither, every message is a candidate. Headers are fetched a
        chunk at a time from the newest end, so a small limit on a large
        folder reads only what it shows.
        """
        client = self._require_client()

        if criteria is not None:
            self._select(folder, readonly=True)
            uids = self._criteria_candidates(criteria, folder)

        else:
            uids = self.raw_search(folder, expression or "ALL")

        newest = sorted(uids, reverse=True)
        step = min(BULK_CHUNK, limit or BULK_CHUNK)
        matches: list[MessageSummary] = []
        examined = 0

        while examined < len(newest) and not (limit and len(matches) >= limit):
            chunk = newest[examined : examined + step]

            try:
                fetched = client.fetch(chunk, SUMMARY_ITEMS)

            except IMAPClientError as exc:
                raise MxFilterError(f"IMAP fetch failed -- {exc}") from exc

            for uid in chunk:
                examined += 1
                data = fetched.get(uid)

                # A UID the search returned and the fetch did not was
                # expunged in between; it is simply gone.
                if not data:
                    continue

                message = email.message_from_bytes(
                    data.get(b"BODY[HEADER]") or b""
                )

                if criteria is not None and not criteria.matches(
                    header_values(message)
                ):
                    continue

                matches.append(summarize(uid, data, message, folder))

                if limit and len(matches) >= limit:
                    break

        return matches, examined < len(newest)

    # ------------------------------------------------------------------------
    def plan_actions(
        self,
        criteria: Criteria,
        source: str,
        destination: str = "",
        flags: Sequence[str] = (),
        discard: bool = False,
    ) -> MailActionPlan:
        """Work out what the existing-mail pass would do, without doing it.

        Read-only by construction -- the mailbox is opened read-only and
        nothing is written -- so a caller can always build a plan first and
        decide afterwards. That is what a dry run is: a plan that is never
        executed, rather than a flag threaded down into the operations.
        """
        messages = self.search(criteria, source, readonly=True)

        return MailActionPlan(
            source=source,
            destination=destination,
            flags=list(flags),
            discard=discard,
            messages=messages,
        )

    # ------------------------------------------------------------------------
    def execute(self, plan: MailActionPlan) -> MailActionResult:
        """Carry out a plan and report what was done.

        Work goes to the server in chunks of ``BULK_CHUNK`` UIDs, and each
        chunk is finished -- flagged, then moved or deleted -- before the
        next starts, so a failure leaves whole chunks done and later ones
        untouched. Flags go first within a chunk because a move invalidates
        the UIDs the flag call would otherwise use. A failure after the
        first chunk raises :class:`PartialExecution` with the counts.

        Nothing here asks the user anything: whether a plan should run at
        all is the caller's decision, already made by the time this is
        called.
        """
        if plan.is_empty:
            return MailActionResult()

        self._select(plan.source, readonly=False)

        flags = [flag.encode() for flag in plan.flags]
        done = MailActionResult()

        for chunk in chunked(plan.uids):
            try:
                step = self._execute_chunk(plan, chunk, flags)

            except MxFilterError as exc:
                if not (done.flagged or done.moved or done.deleted):
                    raise

                fallback = (
                    plan.moves
                    and not self._require_client().has_capability("MOVE")
                )

                raise PartialExecution(
                    exc, done, plan.count, fallback, plan.destination
                ) from exc

            done = MailActionResult(
                flagged=done.flagged + step.flagged,
                moved=done.moved + step.moved,
                deleted=done.deleted + step.deleted,
            )

        return done

    # ------------------------------------------------------------------------
    def _execute_chunk(
        self, plan: MailActionPlan, uids: list[int], flags: list[bytes]
    ) -> MailActionResult:
        """Apply the whole plan to one chunk of its UIDs."""
        flagged = 0

        if flags:
            self.add_flags(uids, flags)
            flagged = len(uids)

        if plan.discard:
            return MailActionResult(flagged=flagged, deleted=self.delete(uids))

        if plan.moves:
            return MailActionResult(
                flagged=flagged, moved=self.move(uids, plan.destination)
            )

        return MailActionResult(flagged=flagged)

    # ------------------------------------------------------------------------
    def _confirm(
        self, uids: list[int], criteria: Criteria, folder: str
    ) -> list[MessageSummary]:
        """Fetch headers for candidates and keep only the real matches."""
        client = self._require_client()
        fetched = {}

        # A broad search can return far more candidates than --max-messages
        # (the cap applies after this re-check), so the FETCH is chunked for
        # the same line-length reason as the bulk writes.
        try:
            for chunk in chunked(uids):
                fetched.update(
                    client.fetch(chunk, ["BODY.PEEK[HEADER]", "INTERNALDATE"])
                )

        except IMAPClientError as exc:
            raise MxFilterError(f"IMAP fetch failed -- {exc}") from exc

        matches = []

        for uid, data in sorted(fetched.items()):
            raw = data.get(b"BODY[HEADER]") or b""
            message = email.message_from_bytes(raw)
            headers = header_values(message)

            if not criteria.matches(headers):
                continue

            matches.append(summarize(uid, data, message, folder))

        return matches

    # ------------------------------------------------------------------------
    def _select(self, folder: str, readonly: bool = True) -> None:
        """Select a folder, naming it in the error if it is missing."""
        client = self._require_client()

        try:
            client.select_folder(folder, readonly=readonly)

        except IMAPClientError as exc:
            raise MxFilterError(
                f"cannot open folder {folder!r} -- {exc}. "
                f"{case_variant_hint(folder, self._folders)}Run 'mxfilter "
                f"folders' to see the exact names this server uses."
            ) from exc

    # ------------------------------------------------------------------------
    def add_flags(self, uids: list[int], flags: list[str]) -> None:
        """Set flags on messages in the currently selected folder."""
        client = self._require_client()
        self._log(f"flagging {len(uids)} message(s) with {flags}")

        try:
            client.add_flags(uids, flags)

        except IMAPClientError as exc:
            raise MxFilterError(f"could not set flags -- {exc}") from exc

    # ------------------------------------------------------------------------
    def move(self, uids: list[int], destination: str) -> int:
        """Move messages out of the selected folder into ``destination``.

        Prefers RFC 6851 MOVE, which is atomic. The fallback is the classic
        COPY + \\Deleted + EXPUNGE dance; UID EXPUNGE is used when UIDPLUS
        is advertised so that only the copied messages are expunged, never
        someone else's concurrently-deleted mail.
        """
        client = self._require_client()

        if not uids:
            return 0

        try:
            if client.has_capability("MOVE"):
                self._log(f"MOVE {len(uids)} message(s) to {destination!r}")
                client.move(uids, destination)

                return len(uids)

            self._log(
                f"server has no MOVE; COPY+EXPUNGE {len(uids)} message(s) "
                f"to {destination!r}"
            )

            client.copy(uids, destination)
            client.add_flags(uids, [b"\\Deleted"])

            if client.has_capability("UIDPLUS"):
                client.uid_expunge(uids)

            else:
                client.expunge()

        except IMAPClientError as exc:
            raise MxFilterError(
                f"could not move messages to {destination!r} -- {exc}"
            ) from exc

        return len(uids)

    # ------------------------------------------------------------------------
    def delete(self, uids: list[int]) -> int:
        """Delete messages from the selected folder, permanently."""
        client = self._require_client()

        if not uids:
            return 0

        self._log(f"deleting {len(uids)} message(s)")

        try:
            client.add_flags(uids, [b"\\Deleted"])

            if client.has_capability("UIDPLUS"):
                client.uid_expunge(uids)

            else:
                client.expunge()

        except IMAPClientError as exc:
            raise MxFilterError(f"could not delete messages -- {exc}") from exc

        return len(uids)

    # ------------------------------------------------------------------------
    def fetch_message_headers(self, folder: str, uid: int):
        """Return one message's headers, for ``from-message``."""
        data = self._fetch_one(folder, uid, ["BODY.PEEK[HEADER]"])

        return email.message_from_bytes(data.get(b"BODY[HEADER]") or b"")

    # ------------------------------------------------------------------------
    def fetch_message_source(
        self, folder: str, uid: int
    ) -> tuple[bytes, tuple[str, ...]]:
        """Return one message's full RFC 822 source and its flags, without
        marking it read."""
        data = self._fetch_one(folder, uid, SOURCE_ITEMS)

        return data.get(b"BODY[]") or b"", flag_names(data.get(b"FLAGS"))

    # ------------------------------------------------------------------------
    def _fetch_one(self, folder: str, uid: int, items: list[str]) -> dict:
        """FETCH ``items`` for one UID from ``folder``, selected read-only."""
        client = self._require_client()
        self._select(folder, readonly=True)

        try:
            fetched = client.fetch([uid], items)

        except IMAPClientError as exc:
            raise MxFilterError(f"IMAP fetch failed -- {exc}") from exc

        data = fetched.get(uid)

        if not data:
            raise MxFilterError(f"no message with uid {uid} in {folder!r}")

        return data

    # ------------------------------------------------------------------------
    def raw_search(self, folder: str, expression: str) -> list[int]:
        """Run a raw IMAP SEARCH expression, for ``from-message --search``."""
        client = self._require_client()
        self._select(folder, readonly=True)

        self._log(f"raw search in {folder!r}: {expression}")

        try:
            return list(client.search(expression))

        except IMAPClientError as exc:
            raise MxFilterError(
                f"IMAP search {expression!r} failed -- {exc}. Use IMAP "
                f"syntax, e.g. 'FROM boss@example.com' or 'UNSEEN'."
            ) from exc
