"""The ManageSieve session: sievelib's client behind one failure convention.

sievelib reports failure two ways -- a raised ``Error`` and a returned
``False``. :class:`SieveSession` turns both into ``MailctlError`` with a
readable message, and takes plain connection parameters so that nothing
here knows which host, which configuration, or which front-end is calling.
"""

import contextlib
import ssl
from collections.abc import Callable
from typing import Protocol

from sievelib.managesieve import Client
from sievelib.managesieve import Error as SieveProtocolError

from ... import MailctlError

__all__ = [
    "Revealable",
    "SieveAuthenticationError",
    "SieveConnectionError",
    "SieveSession",
]


class Revealable(Protocol):
    """A credential that hands its value over only when asked directly."""

    def reveal(self) -> str: ...


class SieveConnectionError(MailctlError):
    """The server could not be reached, or TLS or the protocol failed."""


class SieveAuthenticationError(MailctlError):
    """The server was reached and refused the credentials."""

    def __init__(self, username: str):
        super().__init__(f"ManageSieve authentication failed for {username!r}")
        self.username = username


class SieveSession:
    """A connected ManageSieve client with human-readable failures."""

    # ------------------------------------------------------------------------
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: Callable[[], Revealable],
        tls: str = "starttls",
        progress: Callable[[str], None] | None = None,
    ):
        """Record the settings; no connection is made until ``open()``.

        ``password`` is called only as the connection is made, so a prompt
        or a credential command runs when it is needed and never merely
        because a session was constructed.

        ``progress`` receives step-by-step messages. It is a callback rather
        than a print so this module stays free of presentation -- the CLI
        passes a printer under ``--verbose``, and anything else can route
        the same messages to a status bar or a log.
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.tls = tls
        self.progress = progress
        self.client: Client | None = None

    # ------------------------------------------------------------------------
    def __enter__(self) -> "SieveSession":
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
        """Connect and authenticate.

        Raises :class:`SieveConnectionError` when the server cannot be
        reached or the transport fails, and
        :class:`SieveAuthenticationError` when it refuses the login.

        sievelib's ``debug=True`` echoes the raw protocol, including the
        base64 AUTHENTICATE payload that carries the password. There is no
        safe way to expose that as a flag, so it is pinned off here.
        """
        where = f"{self.host}:{self.port}"

        self._log(f"connecting to {where} (tls={self.tls}) as {self.username}")

        client = Client(self.host, self.port, debug=False)

        try:
            authenticated = client.connect(
                self.username,
                self.password().reveal(),
                starttls=(self.tls == "starttls"),
                ssl=(self.tls == "ssl"),
            )

        except SieveProtocolError as exc:
            raise SieveConnectionError(
                f"ManageSieve error talking to {where} -- {exc}."
            ) from exc

        except ssl.SSLError as exc:
            raise SieveConnectionError(
                f"TLS failure against {where} -- {exc}."
            ) from exc

        except OSError as exc:
            raise SieveConnectionError(
                f"cannot reach {where} -- {exc}."
            ) from exc

        if not authenticated:
            raise SieveAuthenticationError(self.username)

        self.client = client
        self._log("authenticated")

    # ------------------------------------------------------------------------
    def close(self) -> None:
        """Log out, ignoring a connection that has already gone away."""
        if self.client is None:
            return

        with contextlib.suppress(SieveProtocolError, OSError):
            self.client.logout()

        self.client = None

    # ------------------------------------------------------------------------
    def _require_client(self) -> Client:
        """Return the live client or fail loudly."""
        if self.client is None:
            raise MailctlError("ManageSieve session is not open")

        return self.client

    # ------------------------------------------------------------------------
    def capabilities(self) -> list[str]:
        """Return the Sieve extensions the server advertises."""
        client = self._require_client()

        try:
            return list(client.get_sieve_capabilities())

        except (KeyError, TypeError):
            return []

    # ------------------------------------------------------------------------
    def missing_extensions(self, required: set[str]) -> list[str]:
        """Return the requested extensions the server does not advertise."""
        advertised = {name.lower() for name in self.capabilities()}

        return sorted(
            name for name in required if name.lower() not in advertised
        )

    # ------------------------------------------------------------------------
    def list_scripts(self) -> tuple[str | None, list[str]]:
        """Return ``(active_script, other_scripts)``."""
        client = self._require_client()

        try:
            result = client.listscripts()

        except SieveProtocolError as exc:
            raise MailctlError(f"LISTSCRIPTS failed -- {exc}") from exc

        if result is None:
            return (None, [])

        active, others = result

        return (active, list(others or []))

    # ------------------------------------------------------------------------
    def active_script_name(self) -> str | None:
        """Return the active script's name, or None if none is active."""
        active, _others = self.list_scripts()

        return active

    # ------------------------------------------------------------------------
    def get_script(self, name: str) -> str:
        """Fetch one script's source."""
        client = self._require_client()

        try:
            content = client.getscript(name)

        except SieveProtocolError as exc:
            raise MailctlError(f"GETSCRIPT {name!r} failed -- {exc}") from exc

        if content is False or content is None:
            raise MailctlError(f"could not download script {name!r}")

        return content

    # ------------------------------------------------------------------------
    def check_script(self, content: str) -> None:
        """Ask the server to validate a script before uploading it."""
        client = self._require_client()
        self._log("validating script with CHECKSCRIPT")

        try:
            accepted = client.checkscript(content)

        except SieveProtocolError as exc:
            raise MailctlError(
                f"the server rejected the generated script -- {exc}"
            ) from exc

        if not accepted:
            raise MailctlError(
                "the server rejected the generated script (CHECKSCRIPT "
                "returned failure); nothing was uploaded"
            )

    # ------------------------------------------------------------------------
    def put_script(self, name: str, content: str) -> None:
        """Upload a script, replacing any script of the same name."""
        client = self._require_client()
        self._log(f"uploading script {name!r} ({len(content)} bytes)")

        try:
            stored = client.putscript(name, content)

        except SieveProtocolError as exc:
            raise MailctlError(f"PUTSCRIPT {name!r} failed -- {exc}") from exc

        if not stored:
            raise MailctlError(f"PUTSCRIPT {name!r} failed")

    # ------------------------------------------------------------------------
    def set_active(self, name: str) -> None:
        """Make a script the active one."""
        client = self._require_client()
        self._log(f"activating script {name!r}")

        try:
            activated = client.setactive(name)

        except SieveProtocolError as exc:
            raise MailctlError(f"SETACTIVE {name!r} failed -- {exc}") from exc

        if not activated:
            raise MailctlError(f"SETACTIVE {name!r} failed")
