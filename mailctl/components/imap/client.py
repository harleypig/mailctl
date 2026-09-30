"""The IMAP session, over IMAPClient.

:class:`ImapSession` wraps IMAPClient with what mailctl needs: the folder
list plus its hierarchy delimiter, a search and the header fetch a caller
re-checks its candidates against, and a move that degrades gracefully when
the server has no MOVE capability (ADR 0006, I3). The re-check itself --
the real comparison semantics -- is the caller's (ADR 0007). It takes plain
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
from typing import Any, Protocol

from imapclient import IMAPClient
from imapclient.exceptions import (
    IMAPClientAbortError,
    IMAPClientError,
    LoginError,
)

from ... import MailctlError
from .alerts import ServerAlert, alert_in_line, alert_in_text
from .folders import case_variant_hint, same_folder
from .messages import (
    FetchedMessage,
    MailActionPlan,
    MailActionResult,
    PartialExecution,
    chunked,
    flag_names,
    summarize,
)
from .search import SEARCH_CHARSET, SearchCriteria, encode_search_key
from .servers import ServerProfile, select_server
from .status import FolderStatus, list_status_arguments, parse_status

__all__ = [
    "ImapAuthenticationError",
    "ImapConnectionError",
    "ImapSession",
    "ImapTLSError",
    "Revealable",
    "connection_lost",
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


# ----------------------------------------------------------------------------
def connection_lost(error: BaseException) -> bool:
    """Whether ``error``, or anything that caused it, is the connection
    going away -- a BYE such as an autologout, an EOF, a reset -- rather
    than the server refusing a command.

    imaplib reports all of those as its ``abort``, a socket failure it did
    not catch arrives as an ``OSError``, and this session wraps either in
    ``MailctlError`` with the original as its cause.
    """
    seen: set[int] = set()
    current: BaseException | None = error

    while current is not None and id(current) not in seen:
        if isinstance(current, IMAPClientAbortError | OSError):
            return True

        seen.add(id(current))
        current = current.__cause__ or current.__context__

    return False


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
        progress: Callable[[str | ServerAlert], None] | None = None,
    ):
        """Record the settings; no connection is made until ``open()``.

        ``tls`` is ``ssl`` (implicit TLS) or ``starttls``.

        ``password`` is called only as the connection is made, so a prompt
        or a credential command runs when it is needed and never merely
        because a session was constructed.

        ``progress`` receives step-by-step messages, as a callback rather
        than a print so this module carries no presentation of its own. A
        :class:`ServerAlert` the server sends goes to it too, whatever the
        caller does with the rest: RFC 9051 says an alert is shown.
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
        self._prefix: str | None = None
        self._server: ServerProfile | None = None
        # The folder the server has selected on this connection, if any,
        # and the UIDVALIDITY its SELECT or EXAMINE reported.
        self._selected: str | None = None
        self._selected_validity: int | None = None

    # ------------------------------------------------------------------------
    def __enter__(self) -> "ImapSession":
        self.open()

        return self

    # ------------------------------------------------------------------------
    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.close()

        return False

    # ------------------------------------------------------------------------
    def _log(self, message: str | ServerAlert) -> None:
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

            self._watch_alerts(client, greeting=use_ssl)

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
        self._selected = None
        self._read_folders()
        self._read_namespace()
        self._log(
            f"connected; delimiter {self._delimiter!r}, "
            f"{len(self._folders)} folders, "
            f"{len(self._subscribed)} subscribed"
        )

    # ------------------------------------------------------------------------
    def _watch_alerts(self, client: IMAPClient, greeting: bool) -> None:
        """Hand every ALERT the server sends from now on to ``progress``.

        IMAPClient drops a status response's text once it has the status,
        so imaplib's own response reader is wrapped on this connection:
        every response's first line passes through it, untagged or tagged,
        whichever command it answers. That reader is a private of imaplib,
        which ``tests/test_imap_alerts.py`` runs for real, so a rename
        fails a test rather than silently hiding alerts.

        What imaplib read inside its constructor -- the greeting, and the
        CAPABILITY it asks for -- is past the reader, so ``greeting`` says
        whether to look at it: only on implicit TLS, since RFC 9051 has a
        client ignore an alert sent before TLS (section 7.1), which on
        STARTTLS all of it was. imaplib keeps that exchange's untagged
        lines, which are read here, but not the CAPABILITY command's tagged
        completion: an alert there is the one this cannot see.
        """
        # A private, reached as such: the checker's view of imaplib has no
        # room for a reader replaced on one connection.
        imap: Any = client._imap

        if greeting:
            self._alert(alert_in_line(client.welcome or b""))

            for kind in ("OK", "NO", "BAD"):
                for text in imap.untagged_responses.get(kind, ()):
                    if isinstance(text, bytes):
                        self._alert(alert_in_text(text))

        read = imap._get_response

        def read_watching_for_alerts():
            line = read()

            # None is a continuation request, which carries no status.
            if line is not None:
                self._alert(alert_in_line(line))

            return line

        imap._get_response = read_watching_for_alerts

    # ------------------------------------------------------------------------
    def _alert(self, alert: ServerAlert | None) -> None:
        if alert is not None:
            self._log(alert)

    # ------------------------------------------------------------------------
    def close(self) -> None:
        """Log out, ignoring a connection that has already gone away."""
        if self.client is None:
            return

        with contextlib.suppress(IMAPClientError, OSError):
            self.client.logout()

        self.client = None
        self._selected = None

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
    def _read_namespace(self) -> None:
        """Cache the personal namespace prefix (RFC 2342), if reported.

        The prefix is where a new top-level folder belongs, which the
        delimiter alone does not say: a ``.``-delimited Dovecot may keep
        folders at the root or under ``INBOX.``. A server that does not
        advertise ``NAMESPACE``, fails it, or reports no personal namespace
        leaves the prefix unknown, and ``normalize_folder`` falls back to
        guessing from the delimiter.
        """
        client = self._require_client()

        if not client.has_capability("NAMESPACE"):
            return

        try:
            personal = client.namespace()[0]

        except IMAPClientError:
            return

        if not personal:
            return

        prefix = personal[0][0]

        self._prefix = (
            prefix.decode() if isinstance(prefix, bytes) else str(prefix)
        )

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
    def namespace_prefix(self) -> str | None:
        """The personal namespace prefix, or None when the server gave none.

        ``""`` is a real answer -- folders live at the root -- and is not
        the same as None, which means the layout is unknown.
        """
        return self._prefix

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
    def namespaces(
        self,
    ) -> tuple[tuple[tuple[str, str | None], ...], ...]:
        """Return the whole ``NAMESPACE`` response (RFC 2342).

        Three tuples -- personal, other users', shared -- each of
        ``(prefix, delimiter)`` pairs in the server's order. A kind the
        server reports none of is empty, and so is every kind when it does
        not advertise ``NAMESPACE`` or fails the command: like ``ID``, the
        response is advisory.
        """
        client = self._require_client()

        if not client.has_capability("NAMESPACE"):
            return ((), (), ())

        try:
            response = client.namespace()

        except IMAPClientError:
            return ((), (), ())

        def text(value) -> str | None:
            if value is None or isinstance(value, str):
                return value

            return value.decode("utf-8", "replace")

        kinds = []

        for part in tuple(response)[:3]:
            pairs = [
                (text(prefix) or "", text(delimiter))
                for prefix, delimiter in part or ()
            ]
            kinds.append(tuple(pairs))

        return tuple(kinds)

    # ------------------------------------------------------------------------
    def list_status(self, sizes: bool = False) -> list[FolderStatus]:
        """Every selectable folder's counts, in one ``LIST`` (RFC 5819).

        The caller has checked that ``LIST-STATUS`` is advertised, and
        ``STATUS=SIZE`` too when ``sizes`` is set; this sends what it is
        asked to. A folder the server gives no ``STATUS`` line for -- one
        that cannot hold mail -- is absent from the result.
        """
        client = self._require_client()
        arguments = list_status_arguments(sizes)

        self._log(f"LIST {' '.join(arguments)}")

        # IMAPClient has no LIST-STATUS, so the command goes through its
        # imaplib connection (the private ``_imap``, which imapclient<5
        # bounds). Both kinds of untagged line are taken off it, so none is
        # left for a later command to read as its own.
        imap = client._imap

        try:
            typ, data = imap._simple_command("LIST", *arguments)

        except IMAPClientError as exc:
            raise MailctlError(f"LIST-STATUS failed -- {exc}") from exc

        finally:
            imap.untagged_responses.pop("LIST", None)
            status = imap.untagged_responses.pop("STATUS", [])

        if typ != "OK":
            reason = data[0] if data else b""
            text = (
                reason.decode("utf-8", "replace")
                if isinstance(reason, bytes)
                else str(reason)
            )

            raise MailctlError(f"LIST-STATUS failed -- {typ} {text}")

        return parse_status(status)

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
    def create_folder(self, folder: str) -> None:
        """Create a folder, as named; subscribing to it is a second step."""
        client = self._require_client()
        self._log(f"creating folder {folder!r}")

        try:
            client.create_folder(folder)

        except IMAPClientError as exc:
            raise MailctlError(
                f"could not create folder {folder!r} -- {exc}"
            ) from exc

        self._read_folders()

    # ------------------------------------------------------------------------
    def rename_folder(self, old: str, new: str) -> None:
        """Rename a folder; the server moves the folders under it too.

        Subscriptions are left as they were (RFC 3501 section 6.3.5), and
        the cached lists are re-read so a caller sees what RENAME did.
        """
        client = self._require_client()
        self._log(f"renaming folder {old!r} to {new!r}")

        try:
            client.rename_folder(old, new)

        except IMAPClientError as exc:
            raise MailctlError(
                f"could not rename folder {old!r} to {new!r} -- {exc}"
            ) from exc

        # The selected folder may have been the one renamed; select afresh
        # rather than trust a name that no longer exists.
        self._selected = None
        self._read_folders()

    # ------------------------------------------------------------------------
    def message_count(self, folder: str) -> int:
        """How many messages ``folder`` holds (STATUS MESSAGES)."""
        client = self._require_client()

        try:
            status = client.folder_status(folder, ["MESSAGES"])

        except IMAPClientError as exc:
            raise MailctlError(
                f"could not read the message count of {folder!r} -- {exc}"
            ) from exc

        return int(status[b"MESSAGES"])

    # ------------------------------------------------------------------------
    def search_uids(self, criteria: SearchCriteria, folder: str) -> list[int]:
        """Return the UIDs the server matches for ``criteria`` in ``folder``.

        The folder is selected read-only first. These are candidates: IMAP
        can only substring-match, so a caller holding the real comparison
        semantics narrows them against the headers ``fetch_headers``
        returns.
        """
        self._select(folder, readonly=True)

        return self._criteria_candidates(criteria, folder)

    # ------------------------------------------------------------------------
    def _criteria_candidates(
        self, criteria: SearchCriteria, folder: str
    ) -> list[int]:
        """Run the criteria's IMAP SEARCH in the selected folder."""
        client = self._require_client()

        key = criteria.imap_search_key()
        self._log(f"searching {folder!r} with {key}")
        wire_key, charset = encode_search_key(key)

        try:
            # IMAPClient is unannotated, so pyright takes ``criteria``'s
            # type from its "ALL" default; it documents a sequence too.
            return list(
                client.search(
                    wire_key,  # pyright: ignore[reportArgumentType]
                    charset=charset,
                )
            )

        except IMAPClientError as exc:
            raise MailctlError(
                f"IMAP search in {folder!r} failed -- {exc}"
            ) from exc

    # ------------------------------------------------------------------------
    def fetch_headers(
        self, uids: list[int], folder: str
    ) -> list[FetchedMessage]:
        """Fetch the headers and date of ``uids`` in ``folder``.

        Returned in UID order, one per message the server still has. A
        broad search can return far more candidates than --max-messages
        (the cap applies once the caller has narrowed them), so the FETCH
        is chunked for the same line-length reason as the bulk writes.
        """
        client = self._require_client()
        self._ensure_selected(folder)
        fetched = {}

        # Announces the caller's narrowing, which follows this fetch; the
        # wording is what --verbose has always printed here.
        self._log(f"{len(uids)} candidate message(s); re-checking headers")

        try:
            for chunk in chunked(uids):
                fetched.update(
                    client.fetch(chunk, ["BODY.PEEK[HEADER]", "INTERNALDATE"])
                )

        except IMAPClientError as exc:
            raise MailctlError(f"IMAP fetch failed -- {exc}") from exc

        return [
            _fetched(uid, data, folder)
            for uid, data in sorted(fetched.items())
        ]

    # ------------------------------------------------------------------------
    def fetch_summaries(
        self, uids: list[int], folder: str
    ) -> list[FetchedMessage]:
        """Fetch what a listing shows of ``uids`` in ``folder``.

        One FETCH of headers, date, size, flags, and structure; returned in
        the order asked for, one per message the server still has -- a UID
        a search returned and the fetch did not was expunged in between.
        """
        client = self._require_client()
        self._ensure_selected(folder)

        try:
            fetched = client.fetch(uids, SUMMARY_ITEMS)

        except IMAPClientError as exc:
            raise MailctlError(f"IMAP fetch failed -- {exc}") from exc

        return [
            _fetched(uid, fetched[uid], folder)
            for uid in uids
            if fetched.get(uid)
        ]

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
    def _select(self, folder: str, readonly: bool = True) -> None:
        """Select a folder, naming it in the error if it is missing."""
        client = self._require_client()

        # A failed SELECT leaves nothing selected (RFC 3501 6.3.1).
        self._selected = None
        self._selected_validity = None

        try:
            response = client.select_folder(folder, readonly=readonly)

        except IMAPClientError as exc:
            raise MailctlError(
                (
                    f"cannot open folder {folder!r} -- {exc}. "
                    f"{case_variant_hint(folder, self._folders)}"
                ).rstrip(),
                code="folder_unopenable",
                fields={"operation": "folder list"},
            ) from exc

        self._selected = folder
        self._selected_validity = _validity(response)

    # ------------------------------------------------------------------------
    def uidvalidity(self, folder: str) -> int | None:
        """The UIDVALIDITY of ``folder``: what its UIDs are valid under.

        A UID names one message only together with this value (RFC 9051
        section 2.3.1.1), and a change means every UID remembered from
        before it may name another message. SELECT and EXAMINE report it,
        so the folder's own selection is where it is read: the one this
        connection holds already, or an EXAMINE made now, which a read that
        follows keeps using. None when the server reported none.
        """
        self._ensure_selected(folder)

        return self._selected_validity

    # ------------------------------------------------------------------------
    def _ensure_selected(self, folder: str) -> None:
        """Select ``folder`` read-only unless this connection has it
        selected already.

        A fetch names its folder rather than trusting whatever the last
        call left selected: another caller, or a reconnect, may have
        changed it. Skipping the SELECT only when the folder is already
        the selected one keeps the wire what it was for the search and
        fetch that run back to back.
        """
        if self._selected != folder:
            self._select(folder, readonly=True)

    # ------------------------------------------------------------------------
    def add_flags(self, uids: list[int], flags: Sequence[str | bytes]) -> None:
        """Set flags on messages in the currently selected folder."""
        client = self._require_client()
        self._log(f"flagging {len(uids)} message(s) with {flags}")

        try:
            client.add_flags(uids, flags)

        except IMAPClientError as exc:
            raise MailctlError(f"could not set flags -- {exc}") from exc

    # ------------------------------------------------------------------------
    def add_folder_flags(
        self, folder: str, uids: list[int], flags: list[str]
    ) -> None:
        """STORE +FLAGS on ``uids`` in ``folder``, selected read-write."""
        client = self._require_client()
        self._store_flags(folder, uids, flags, client.add_flags, "set")

    # ------------------------------------------------------------------------
    def remove_folder_flags(
        self, folder: str, uids: list[int], flags: list[str]
    ) -> None:
        """STORE -FLAGS on ``uids`` in ``folder``, selected read-write."""
        client = self._require_client()
        self._store_flags(folder, uids, flags, client.remove_flags, "clear")

    # ------------------------------------------------------------------------
    def _store_flags(
        self,
        folder: str,
        uids: list[int],
        flags: list[str],
        store: Callable,
        verb: str,
    ) -> None:
        """Send one STORE per chunk of ``uids``; ``store`` is the
        IMAPClient method whose sign it carries."""
        if not uids or not flags:
            return

        self._select(folder, readonly=False)
        self._log(f"{verb} {flags} on {len(uids)} message(s) in {folder!r}")

        encoded = [flag.encode() for flag in flags]

        try:
            for chunk in chunked(uids):
                store(chunk, encoded)

        except IMAPClientError as exc:
            raise MailctlError(
                f"could not {verb} flags in {folder!r} -- {exc}"
            ) from exc

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
    def copy_messages(
        self, folder: str, uids: list[int], destination: str
    ) -> int:
        """COPY ``uids`` from ``folder`` into ``destination``.

        The originals are left as they were: nothing is flagged
        ``\\Deleted`` and nothing is expunged. The copies carry the
        originals' flags (RFC 3501 section 6.4.7). Sent in chunks, and a
        failure after the first says how many were copied, because a
        second run copies those again.
        """
        client = self._require_client()

        if not uids:
            return 0

        self._ensure_selected(folder)
        self._log(f"COPY {len(uids)} message(s) to {destination!r}")

        copied = 0

        try:
            for chunk in chunked(uids):
                client.copy(chunk, destination)
                copied += len(chunk)

        except IMAPClientError as exc:
            done = (
                f" {copied} of {len(uids)} were copied before it failed, "
                f"and copying again would copy them a second time."
                if copied
                else ""
            )

            raise MailctlError(
                f"could not copy messages to {destination!r} -- {exc}.{done}"
            ) from exc

        return copied

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
        """Return one message's headers, for ``--like``."""
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
        """Run a raw IMAP SEARCH expression, for ``search --raw``."""
        client = self._require_client()
        self._select(folder, readonly=True)

        self._log(f"raw search in {folder!r}: {expression}")
        _refuse_non_ascii(expression)

        try:
            return list(client.search(expression))

        except IMAPClientError as exc:
            raise MailctlError(
                f"IMAP search {expression!r} failed -- {exc}. Use IMAP "
                f"syntax, e.g. 'FROM boss@example.com' or 'UNSEEN'."
            ) from exc

    # ------------------------------------------------------------------------
    def sort_uids(
        self,
        folder: str,
        order: list[str],
        criteria: SearchCriteria | None = None,
        expression: str | None = None,
    ) -> list[int]:
        """Return the UIDs a search matches, in the server's sort order.

        One ``UID SORT`` (RFC 5256) in ``folder``, selected read-only:
        ``order`` is its sort criteria (``["REVERSE", "SIZE"]``), and the
        search is ``criteria``, else the raw ``expression``, else ``ALL``.
        The charset is always UTF-8, which RFC 5256 requires every server
        to take. The server must advertise ``SORT``.
        """
        client = self._require_client()
        self._select(folder, readonly=True)

        if criteria is not None:
            key, _charset = encode_search_key(criteria.imap_search_key())

        else:
            key = expression or "ALL"
            _refuse_non_ascii(key)

        self._log(f"sorting {folder!r} by {' '.join(order)} with {key}")

        try:
            # As in search(): pyright infers ``criteria: str`` from
            # IMAPClient's default, where a sequence is documented.
            return list(
                client.sort(
                    order,
                    key,  # pyright: ignore[reportArgumentType]
                    charset=SEARCH_CHARSET,
                )
            )

        except IMAPClientError as exc:
            raise MailctlError(
                f"IMAP sort in {folder!r} failed -- {exc}"
            ) from exc


# ----------------------------------------------------------------------------
def _refuse_non_ascii(expression: str) -> None:
    """Refuse a raw search expression IMAPClient cannot send.

    IMAPClient sends a whole-string expression unquoted, so a non-ASCII
    one would go out as a single literal the server cannot parse as search
    keys. Only structured criteria can carry it.
    """
    if not expression.isascii():
        refused = (
            f"IMAP search {expression!r} has non-ASCII text, which a raw "
            f"expression cannot carry"
        )

        raise MailctlError(
            f"{refused}; criteria can, searching in {SEARCH_CHARSET}.",
            code="raw_non_ascii",
            fields={"refused": refused, "charset": SEARCH_CHARSET},
        )


# ----------------------------------------------------------------------------
def _validity(response) -> int | None:
    """The UIDVALIDITY in IMAPClient's SELECT response, if it has one."""
    value = (response or {}).get(b"UIDVALIDITY")

    return None if value is None else int(value)


# ----------------------------------------------------------------------------
def _fetched(uid: int, data: dict, folder: str) -> FetchedMessage:
    """One FETCH response as its parsed headers and its summary."""
    headers = email.message_from_bytes(data.get(b"BODY[HEADER]") or b"")

    return FetchedMessage(headers, summarize(uid, data, headers, folder))
