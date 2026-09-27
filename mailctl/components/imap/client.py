"""The IMAP session, over IMAPClient.

:class:`ImapSession` wraps IMAPClient with what mailctl needs: the folder
list plus its hierarchy delimiter, a search that is re-checked against the
caller's real comparison semantics, and a move that degrades gracefully
when the server has no MOVE capability (ADR 0006, I3). It takes plain
connection parameters, so nothing here knows which host, which
configuration, or which front-end is calling.

Existing and visible are two different questions, which is why both
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
from typing import Protocol

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError, LoginError

from ... import MailctlError
from .folders import (
    FolderCreation,
    case_variant_hint,
    case_variants,
    normalize_folder,
    same_folder,
)
from .messages import (
    BULK_CHUNK,
    MailActionPlan,
    MailActionResult,
    MessageSummary,
    PartialExecution,
    chunked,
    flag_names,
    header_values,
    summarize,
)
from .search import SearchCriteria
from .servers import ServerProfile, select_server

__all__ = [
    "ImapAuthenticationError",
    "ImapConnectionError",
    "ImapSession",
    "ImapTLSError",
    "Revealable",
]

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


class Revealable(Protocol):
    """A credential that hands its value over only when asked directly."""

    def reveal(self) -> str: ...


class ImapConnectionError(MailctlError):
    """The server could not be reached."""


class ImapTLSError(MailctlError):
    """The server was reached and TLS failed."""


class ImapAuthenticationError(MailctlError):
    """The server was reached and refused the credentials.

    ``reason`` is the server's own refusal, which never holds the password.
    """

    def __init__(self, username: str, reason: str):
        super().__init__(
            f"IMAP authentication failed for {username!r} [{reason}]"
        )
        self.username = username
        self.reason = reason


class ImapSession:
    """A connected IMAP client scoped to one account."""

    # ------------------------------------------------------------------------
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: Callable[[], Revealable],
        tls: str = "ssl",
        progress: Callable[[str], None] | None = None,
    ):
        """Record the settings; no connection is made until ``open()``.

        ``tls`` is ``ssl`` (implicit TLS) or ``starttls``.

        ``password`` is called only as the connection is made, so a prompt
        or a credential command runs when it is needed and never merely
        because a session was constructed.

        ``progress`` receives step-by-step messages, as a callback rather
        than a print so this module carries no presentation of its own.
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.tls = tls
        self.progress = progress
        self.client: IMAPClient | None = None
        self._delimiter = "."
        self._folders: list[str] = []
        self._subscribed: list[str] = []
        self._server: ServerProfile | None = None

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
        """Connect, authenticate, and read the folder list.

        Raises :class:`ImapAuthenticationError` when the server refuses the
        login, :class:`ImapTLSError` when TLS fails, and
        :class:`ImapConnectionError` when the server cannot be reached.
        """
        use_ssl = self.tls == "ssl"
        where = f"{self.host}:{self.port}"

        self._log(f"connecting to {where} (ssl={use_ssl}) as {self.username}")

        try:
            client = IMAPClient(self.host, port=self.port, ssl=use_ssl)

            if not use_ssl:
                client.starttls()

            client.login(self.username, self.password().reveal())

        except LoginError as exc:
            raise ImapAuthenticationError(self.username, str(exc)) from exc

        except ssl.SSLError as exc:
            raise ImapTLSError(
                f"TLS failure against {where} -- {exc}."
            ) from exc

        except (socket.gaierror, OSError) as exc:
            raise ImapConnectionError(
                f"cannot reach {where} -- {exc}."
            ) from exc

        except IMAPClientError as exc:
            raise MailctlError(f"IMAP error -- {exc}") from exc

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
            raise MailctlError("IMAP session is not open")

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
            raise MailctlError(f"LIST failed -- {exc}") from exc

        try:
            subscribed = client.list_sub_folders()

        except IMAPClientError as exc:
            raise MailctlError(f"LSUB failed -- {exc}") from exc

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
    def identity(self) -> dict[str, str]:
        """Return the server's ``ID`` response (RFC 2971) as a mapping.

        Field names are lower-cased. A server that does not advertise ``ID``,
        answers ``NIL``, or fails the command yields an empty mapping: the
        response is advisory, and its absence is ordinary.
        """
        client = self._require_client()

        if not client.has_capability("ID"):
            return {}

        try:
            response = client.id_()

        except IMAPClientError:
            return {}

        fields = response[0] if response else None

        if not isinstance(fields, tuple):
            return {}

        text = [
            item.decode("utf-8", "replace")
            if isinstance(item, bytes)
            else item
            for item in fields
        ]

        return {
            str(name).lower(): value
            for name, value in zip(text[::2], text[1::2], strict=False)
            if isinstance(value, str)
        }

    # ------------------------------------------------------------------------
    def server(self) -> ServerProfile:
        """Return the profile of the server software this session is on.

        Probed on first use and kept, so a session that never asks sends no
        ``ID`` at all.
        """
        if self._server is None:
            self._server = select_server(self.identity())

        return self._server

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
            raise MailctlError(
                f"could not subscribe to folder {folder!r} -- {exc}"
            ) from exc

        self._read_folders()

        if not self.is_subscribed(folder):
            raise MailctlError(
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
            raise MailctlError(
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
            raise MailctlError(
                f"could not create folder {folder!r} -- {exc}"
            ) from exc

        self._read_folders()

        if not subscribe:
            return FolderCreation(folder=folder, subscribed=False)

        try:
            self.subscribe(folder)

        except MailctlError as exc:
            return FolderCreation(
                folder=folder, subscribed=False, subscribe_error=str(exc)
            )

        return FolderCreation(folder=folder, subscribed=True)

    # ------------------------------------------------------------------------
    def search(
        self, criteria: SearchCriteria, folder: str, readonly: bool = True
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
        self, criteria: SearchCriteria, folder: str
    ) -> list[int]:
        """Run the criteria's IMAP SEARCH in the selected folder."""
        client = self._require_client()

        key = criteria.imap_search_key()
        self._log(f"searching {folder!r} with {key}")

        try:
            return list(client.search(key))

        except IMAPClientError as exc:
            raise MailctlError(
                f"IMAP search in {folder!r} failed -- {exc}"
            ) from exc

    # ------------------------------------------------------------------------
    def list_messages(
        self,
        folder: str,
        criteria: SearchCriteria | None = None,
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
                raise MailctlError(f"IMAP fetch failed -- {exc}") from exc

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
        criteria: SearchCriteria,
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

            except MailctlError as exc:
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
        self, uids: list[int], criteria: SearchCriteria, folder: str
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
            raise MailctlError(f"IMAP fetch failed -- {exc}") from exc

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
            raise MailctlError(
                f"cannot open folder {folder!r} -- {exc}. "
                f"{case_variant_hint(folder, self._folders)}Run 'mailctl "
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
            raise MailctlError(f"could not set flags -- {exc}") from exc

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
            raise MailctlError(
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
            raise MailctlError(f"could not delete messages -- {exc}") from exc

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
            raise MailctlError(f"IMAP fetch failed -- {exc}") from exc

        data = fetched.get(uid)

        if not data:
            raise MailctlError(f"no message with uid {uid} in {folder!r}")

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
            raise MailctlError(
                f"IMAP search {expression!r} failed -- {exc}. Use IMAP "
                f"syntax, e.g. 'FROM boss@example.com' or 'UNSEEN'."
            ) from exc
