"""The ManageSieve session, over a sievelib client with its gaps closed.

Two layers. :class:`SieveClient` is sievelib's ``Client`` with the gaps
ADR 0006 names closed where they are: GETSCRIPT read byte-exact (S1, mailctl
#90), the CAPABILITY response kept whole (S4), a configurable read timeout
(S5), debug output impossible (S8), and a closed connection an error rather
than an endless loop (S9, mailctl #95); it also reads an OK's text, which
sievelib drops, so a WARNINGS is kept and a literal is not left unread
(mailctl #208). :class:`SieveSession` sits on it,
turns sievelib's two failure conventions -- a raised ``Error`` and a
returned ``False`` -- into ``MailctlError`` with a readable message, and
takes plain connection parameters so that nothing here knows which host,
which configuration, or which front-end is calling.

sievelib keeps what :class:`SieveClient` needs as name-mangled privates of
its ``Client``: the read buffer, the response reader, the capability table,
and the STARTTLS, TLS, error, and authentication steps. Reaching them is
why sievelib is bounded below 2 in ``pyproject.toml``; a release that
renames one fails ``tests/test_managesieve_client.py`` rather than a
user's backup.
"""

import contextlib
import re
import socket
import ssl
from collections.abc import Callable
from typing import Any, Protocol

from sievelib.managesieve import (
    CRLF,
    KNOWN_CAPABILITIES,
    Client,
    Literal,
    Response,
)
from sievelib.managesieve import Error as SieveProtocolError

from ... import MailctlError
from .capabilities import Capabilities, parse_capabilities
from .responses import ServerWarning, literal_size, response_text, warning_in
from .servers import ServerProfile, select_server

__all__ = [
    "CONNECTION_CLOSED",
    "DEFAULT_TIMEOUT",
    "Revealable",
    "SieveAuthenticationError",
    "SieveClient",
    "SieveConnectionError",
    "SieveSession",
    "connection_lost",
]

# sievelib's own hard-coded read timeout, in seconds, so a caller that sets
# nothing sees no change.
DEFAULT_TIMEOUT = 5.0

# What the client raises when the server said BYE or the socket hit EOF.
CONNECTION_CLOSED = "Connection closed by server"

# The prefix Python gives a private attribute of sievelib's ``Client``.
_SIEVELIB_PRIVATE = "_Client__"

_LITERAL = re.compile(rb"\{(\d+)\+?\}")
_STATUS = re.compile(rb"(OK|NO|BYE)(?:\s+(.*))?", re.DOTALL)


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


class SieveClient(Client):
    """sievelib's client with ADR 0006's gaps S1, S4, S5, S8, and S9 closed.

    Every other command is sievelib's, unchanged.
    """

    # ------------------------------------------------------------------------
    def __init__(self, host: str, port: int, timeout: float = DEFAULT_TIMEOUT):
        """Record the server; nothing is sent until :meth:`connect`.

        There is no ``debug`` parameter, on purpose: sievelib's debug mode
        prints the base64 AUTHENTICATE payload, which is the password
        (ADR 0006, S8). It is pinned off here, and the printer it would use
        is silenced below as well.
        """
        super().__init__(host, port, debug=False)
        self.timeout = timeout
        self.capability_response = b""

        # The WARNINGS the last status response carried, if any.
        self.warning: ServerWarning | None = None

    # ------------------------------------------------------------------------
    def _private(self, name: str) -> Any:
        """Return one of sievelib's name-mangled ``Client`` privates."""
        return getattr(self, _SIEVELIB_PRIVATE + name)

    # ------------------------------------------------------------------------
    def _Client__dprint(self, message) -> None:
        """Print nothing, whatever sievelib's debug flag says (S8)."""

    # ------------------------------------------------------------------------
    def _Client__read_line(self) -> bytes:
        """sievelib's line reader, failing on a closed connection (S9).

        sievelib's own returns an empty line at end of file, which its
        response reader skips and asks for again, forever. This reads
        through :meth:`_fill`, which raises instead, and otherwise keeps
        sievelib's contract: a literal's size is raised as ``Literal``, a
        status line as ``Response``, and ``BYE`` as an error.

        An OK's text is read here too, into ``warning``. sievelib never
        reads it, so an OK whose text is a literal -- Pigeonhole's
        WARNINGS -- left the literal for the next command to read.
        """
        line = self._read_line()

        if not line:
            return line

        literal = _LITERAL.match(line)

        if literal is not None:
            raise Literal(int(literal[1]))

        status = self._private("respcode_expr").match(line)

        if status is None:
            return line

        self.warning = None

        if status[1] == b"BYE":
            raise SieveProtocolError(CONNECTION_CLOSED)

        if status[1] == b"NO":
            self._private("parse_error")(status[2])

        else:
            self.warning = self._ok_warning(status[2] or b"")

        raise Response(status[1], status[2])

    # ------------------------------------------------------------------------
    def _ok_warning(self, rest: bytes) -> ServerWarning | None:
        """Read an OK's text, literal included; return its WARNINGS."""
        code, string = response_text(rest)
        size = literal_size(string)

        if size is not None:
            string = self._read_exact(size)

            if self._read_line() != b"":
                raise SieveProtocolError(
                    "malformed response: no line end after its text"
                )

        return warning_in(code, string)

    # ------------------------------------------------------------------------
    def _Client__read_block(self, size: int) -> bytes:
        """Return exactly ``size`` bytes, failing on a closed connection (S9).

        sievelib's own makes one ``recv`` and returns whatever came, so a
        literal cut short is returned short and the reader then loops.
        """
        return self._read_exact(size)

    # ------------------------------------------------------------------------
    def connect(
        self,
        login: str,
        password: str,
        authz_id: str = "",
        starttls: bool = False,
        ssl: bool = False,
        authmech: str | None = None,
    ) -> bool:
        """sievelib's ``connect``, reading with ``self.timeout`` (S5).

        sievelib sets its socket timeout from the class attribute
        ``Client.read_timeout``, so an instance cannot change it without
        changing it for every client in the process. The steps are
        sievelib's own, in its order.
        """
        try:
            self.sock = socket.create_connection((self.srvaddr, self.srvport))
            self.sock.settimeout(self.timeout)

        except OSError as exc:
            raise SieveProtocolError(
                f"Connection to server failed: {exc}"
            ) from exc

        if ssl:
            self._private("enable_ssl")()

        if not self._private("get_capabilities")():
            raise SieveProtocolError("Failed to read capabilities from server")

        if not ssl and starttls and not self._private("starttls")():
            return False

        return bool(
            self._private("authenticate")(login, password, authz_id, authmech)
        )

    # ------------------------------------------------------------------------
    def _Client__get_capabilities(self) -> bool:
        """Read the capability list, keeping the raw response (S4).

        sievelib calls this after connecting and again after STARTTLS, so
        ``capability_response`` is always the latest the server sent.
        sievelib's own table is filled with the capabilities it knows, as
        its commands rely on it.
        """
        code, _data, raw = self._private("read_response")()

        if code == b"NO":
            return False

        self.capability_response = raw
        known = self._private("capabilities")

        for name, value in parse_capabilities(raw).entries:
            if name in KNOWN_CAPABILITIES:
                known[name] = value

        return True

    # ------------------------------------------------------------------------
    def getscript_bytes(self, name: str) -> bytes | None:
        """GETSCRIPT, returning the script exactly as the server sent it.

        sievelib's ``getscript`` splits the literal into lines and joins
        them with LF, which turns CRLF into LF and drops the final line
        ending (tonioo/sievelib#95; mailctl #90). This reads the literal by
        its declared length instead. Returns None when the server answers
        NO, with the reason in ``errcode`` / ``errmsg`` as sievelib leaves
        it.

        The name is quoted as sievelib quotes it for every other command,
        so all commands agree on which script a name means.
        """
        if not self.authenticated:
            raise SieveProtocolError("Authentication required")

        self.sock.sendall(b'GETSCRIPT "' + name.encode("utf-8") + b'"' + CRLF)

        first = self._read_line()
        literal = _LITERAL.fullmatch(first)

        if literal is None:
            self._read_status(first)

            return None

        script = self._read_exact(int(literal[1]))

        if self._read_line() != b"":
            raise SieveProtocolError(
                "malformed GETSCRIPT response: no line end after the script"
            )

        if self._read_status(self._read_line()) != b"OK":
            return None

        return script

    # ------------------------------------------------------------------------
    def _read_status(self, line: bytes) -> bytes:
        """Return a status line's code, OK or NO; raise on anything else."""
        match = _STATUS.fullmatch(line)

        if match is None:
            raise SieveProtocolError("malformed response from the server")

        code, text = match[1], match[2]

        if code == b"BYE":
            raise SieveProtocolError(CONNECTION_CLOSED)

        if code == b"NO" and text:
            self._private("parse_error")(text)

        return code

    # ------------------------------------------------------------------------
    def _fill(self) -> None:
        """Append one read from the socket to sievelib's shared buffer.

        The buffer is sievelib's, not a copy, so bytes this reads past the
        end of a response are there for the next sievelib command.
        """
        try:
            data = self.sock.recv(self.read_size)

        except (TimeoutError, ssl.SSLError) as exc:
            raise SieveProtocolError(
                "Failed to read data from the server"
            ) from exc

        if not data:
            raise SieveProtocolError(CONNECTION_CLOSED)

        buffer = self._private("read_buffer")
        setattr(self, _SIEVELIB_PRIVATE + "read_buffer", buffer + data)

    # ------------------------------------------------------------------------
    def _take(self, size: int) -> bytes:
        """Remove and return the first ``size`` bytes of the buffer."""
        buffer = self._private("read_buffer")
        setattr(self, _SIEVELIB_PRIVATE + "read_buffer", buffer[size:])

        return buffer[:size]

    # ------------------------------------------------------------------------
    def _read_line(self) -> bytes:
        """Return the next line, without its CRLF."""
        while CRLF not in self._private("read_buffer"):
            self._fill()

        line = self._take(self._private("read_buffer").index(CRLF))
        self._take(len(CRLF))

        return line

    # ------------------------------------------------------------------------
    def _read_exact(self, size: int) -> bytes:
        """Return exactly the next ``size`` bytes."""
        while len(self._private("read_buffer")) < size:
            self._fill()

        return self._take(size)


# ----------------------------------------------------------------------------
def connection_lost(error: BaseException) -> bool:
    """Whether ``error``, or anything that caused it, is the connection
    going away -- a BYE such as an idle timeout, an EOF, a reset -- rather
    than the server refusing a command.

    The client reports a BYE or an EOF as :data:`CONNECTION_CLOSED`, a
    failed send arrives as an ``OSError``, and this session wraps either in
    ``MailctlError`` with the original as its cause.
    """
    seen: set[int] = set()
    current: BaseException | None = error

    while current is not None and id(current) not in seen:
        if isinstance(current, OSError) or (
            isinstance(current, SieveProtocolError)
            and str(current) == CONNECTION_CLOSED
        ):
            return True

        seen.add(id(current))
        current = current.__cause__ or current.__context__

    return False


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
        progress: Callable[[str | ServerWarning], None] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        """Record the settings; no connection is made until ``open()``.

        ``tls`` is ``starttls``, ``ssl`` (implicit TLS), or ``none``.
        ``timeout`` is how long, in seconds, one read may wait on the
        server before the session fails.

        ``password`` is called only as the connection is made, so a prompt
        or a credential command runs when it is needed and never merely
        because a session was constructed.

        ``progress`` receives step-by-step messages. It is a callback rather
        than a print so this module stays free of presentation -- the CLI
        passes a printer under ``--verbose``, and anything else can route
        the same messages to a status bar or a log. A :class:`ServerWarning`
        PUTSCRIPT returns goes to it too, whatever the caller does with the
        rest: RFC 5804 says a client presents one.
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.tls = tls
        self.progress = progress
        self.timeout = timeout
        self.client: SieveClient | None = None

    # ------------------------------------------------------------------------
    def __enter__(self) -> "SieveSession":
        self.open()

        return self

    # ------------------------------------------------------------------------
    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.close()

        return False

    # ------------------------------------------------------------------------
    def _log(self, message: str | ServerWarning) -> None:
        """Hand a progress message to the caller's callback, if any."""
        if self.progress is not None:
            self.progress(message)

    # ------------------------------------------------------------------------
    def open(self) -> None:
        """Connect and authenticate.

        Raises :class:`SieveConnectionError` when the server cannot be
        reached or the transport fails, and
        :class:`SieveAuthenticationError` when it refuses the login.
        """
        where = f"{self.host}:{self.port}"

        self._log(f"connecting to {where} (tls={self.tls}) as {self.username}")

        client = SieveClient(self.host, self.port, timeout=self.timeout)

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
    def _require_client(self) -> SieveClient:
        """Return the live client or fail loudly."""
        if self.client is None:
            raise MailctlError("ManageSieve session is not open")

        return self.client

    # ------------------------------------------------------------------------
    def server_capabilities(self) -> Capabilities:
        """Return everything the server advertised, as data."""
        return parse_capabilities(self._require_client().capability_response)

    # ------------------------------------------------------------------------
    def server(self) -> ServerProfile:
        """Return the profile of the server software this session is on."""
        return select_server(self.server_capabilities())

    # ------------------------------------------------------------------------
    def capabilities(self) -> list[str]:
        """Return the Sieve extensions the server advertises."""
        return list(self.server_capabilities().sieve_extensions)

    # ------------------------------------------------------------------------
    def list_scripts(self) -> tuple[str | None, list[str]]:
        """Return ``(active_script, other_scripts)``.

        An empty name is dropped. sievelib splits the listing on any line
        break and keeps a line it cannot parse as a name with its quotes
        stripped, so a stray CR or LF in the response becomes a script
        called ``""``. No script can have that name: RFC 5804 reserves
        ``SETACTIVE ""`` for deactivating every script.
        """
        client = self._require_client()

        try:
            result = client.listscripts()

        except SieveProtocolError as exc:
            raise MailctlError(f"LISTSCRIPTS failed -- {exc}") from exc

        if result is None:
            return (None, [])

        active, others = result

        return (active or None, [name for name in others or [] if name])

    # ------------------------------------------------------------------------
    def active_script_name(self) -> str | None:
        """Return the active script's name, or None if none is active."""
        active, _others = self.list_scripts()

        return active

    # ------------------------------------------------------------------------
    def get_script_bytes(self, name: str) -> bytes:
        """Fetch one script exactly as the server holds it."""
        client = self._require_client()

        try:
            content = client.getscript_bytes(name)

        except SieveProtocolError as exc:
            raise MailctlError(f"GETSCRIPT {name!r} failed -- {exc}") from exc

        if content is None:
            raise MailctlError(f"could not download filter set {name!r}")

        return content

    # ------------------------------------------------------------------------
    def get_script(self, name: str) -> str:
        """Fetch one script's source as text, line endings untouched.

        Sieve scripts are UTF-8 (RFC 5228), and a strict decode of valid
        UTF-8 encodes back to the same bytes, so the text is still the
        server's exact script.
        """
        content = self.get_script_bytes(name)

        try:
            return content.decode("utf-8")

        except UnicodeDecodeError as exc:
            raise MailctlError(
                f"filter set {name!r} is not valid UTF-8, so it cannot be "
                f"read ({exc.reason} at byte {exc.start})"
            ) from exc

    # ------------------------------------------------------------------------
    def check_script(self, content: str) -> None:
        """Ask the server to validate a script before uploading it.

        A warning it returns is progress, not a :class:`ServerWarning`:
        the PUTSCRIPT that follows returns the same warnings about the
        stored script, by its name, where Pigeonhole names a temporary
        file here. It is quoted with ``repr``, since it is the server's.
        """
        client = self._require_client()
        self._log("validating script with CHECKSCRIPT")

        try:
            accepted = client.checkscript(content)

        except SieveProtocolError as exc:
            raise MailctlError(
                f"the server rejected the filter set -- {exc}"
            ) from exc

        if not accepted:
            raise MailctlError(
                "the server rejected the filter set when checking it; "
                "nothing was uploaded"
            )

        if client.warning is not None:
            self._log(f"CHECKSCRIPT warned: {client.warning.text!r}")

    # ------------------------------------------------------------------------
    def put_script(self, name: str, content: str) -> None:
        """Upload a script, replacing any script of the same name.

        A warning the server returns with its OK goes to ``progress``.
        """
        client = self._require_client()
        self._log(f"uploading script {name!r} ({len(content)} bytes)")

        try:
            stored = client.putscript(name, content)

        except SieveProtocolError as exc:
            raise MailctlError(f"PUTSCRIPT {name!r} failed -- {exc}") from exc

        if not stored:
            raise MailctlError(f"PUTSCRIPT {name!r} failed")

        if client.warning is not None:
            self._log(client.warning)

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
