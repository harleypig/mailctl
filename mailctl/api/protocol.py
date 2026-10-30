"""JSON-RPC 2.0 over a pair of byte streams, one message per line.

Each message is one JSON document on one line, ended by a newline, as
``json_output.dumps`` writes it with no indent: a newline inside a string
is escaped, and so is everything outside printable ASCII, so a newline is
an unambiguous frame and a line reader is all a client in any language
needs (ADR 0008).

The client's lines are read here and nowhere else, so this is where a
password a client sends is wrapped: any ``password`` member holding a
string becomes a :class:`Secret` as the line is parsed, before anything
dispatches it, logs it, or could echo it.
"""

import json
from collections import deque
from typing import BinaryIO, TypeGuard

from .. import json_output
from ..config import Secret

__all__ = [
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "REFUSED",
    "Channel",
    "ClientGone",
    "RpcError",
    "parse",
]

JSONRPC = "2.0"

# The specification's own codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# The request was understood and mailctl refused it: a ``MailctlError``.
# Outside the range the specification reserves, which leaves the rest to
# the application; ``data`` holds the error document, whose ``code`` is
# what a client branches on.
REFUSED = 1


# ############################################################################
# Errors
# ############################################################################


class RpcError(Exception):
    """A JSON-RPC error object, raised to be sent back as one.

    ``message`` names what was wrong and never quotes what the client sent,
    since a value a client sent may be the password.
    """

    # ------------------------------------------------------------------------
    def __init__(self, code: int, message: str, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    # ------------------------------------------------------------------------
    def to_dict(self) -> dict:
        body: dict = {"code": self.code, "message": self.message}

        if self.data is not None:
            body["data"] = self.data

        return body


class ClientGone(Exception):
    """The client's input ended while a request to it was unanswered."""


# ############################################################################
# Reading a line
# ############################################################################


# ----------------------------------------------------------------------------
def _wrap_passwords(pairs: list[tuple[str, object]]) -> dict:
    """An object, with every string ``password`` member wrapped."""
    return {
        key: Secret(value)
        if key == "password" and isinstance(value, str)
        else value
        for key, value in pairs
    }


# ----------------------------------------------------------------------------
def parse(line: bytes) -> object:
    """One line from the client, parsed; raises ``RpcError`` for one that
    is not UTF-8 JSON. The message says where, never what."""
    try:
        text = line.decode("utf-8")

    except UnicodeDecodeError as exc:
        raise RpcError(PARSE_ERROR, "a message must be UTF-8 text") from exc

    try:
        return json.loads(text, object_pairs_hook=_wrap_passwords)

    except (json.JSONDecodeError, RecursionError) as exc:
        raise RpcError(
            PARSE_ERROR, "a message must be one JSON value on one line"
        ) from exc


# ----------------------------------------------------------------------------
def is_response(message: object) -> TypeGuard[dict]:
    """Whether ``message`` answers a request, rather than making one."""
    return (
        isinstance(message, dict)
        and "method" not in message
        and ("result" in message or "error" in message)
    )


# ############################################################################
# The channel
# ############################################################################


class Channel:
    """The client's end: its requests in, and everything sent to it out.

    The server may ask the client something while one of the client's own
    requests is in hand (``credentials.prompt``). Whatever else the client
    sends while that answer is awaited is kept, in order, and is what
    :meth:`receive` returns next.
    """

    # ------------------------------------------------------------------------
    def __init__(self, reader: BinaryIO, writer: BinaryIO):
        self._reader = reader
        self._writer = writer
        self._held: deque[object | RpcError] = deque()
        self._sent_requests = 0

    # ------------------------------------------------------------------------
    def _read(self) -> object | RpcError | None:
        """The next message off the stream, a parse failure as its error,
        or None at the end of input; blank lines are skipped."""
        while True:
            line = self._reader.readline()

            if not line:
                return None

            if not line.strip():
                continue

            try:
                return parse(line)

            except RpcError as error:
                return error

    # ------------------------------------------------------------------------
    def receive(self) -> object | None:
        """The client's next message, or None once its input has ended.

        Raises ``RpcError`` for a line that did not parse; it is answered
        with a null id, as the specification has it.
        """
        message = self._held.popleft() if self._held else self._read()

        if isinstance(message, RpcError):
            raise message

        return message

    # ------------------------------------------------------------------------
    def send(self, message: dict) -> None:
        """Write one message as one line, refusing a credential in it."""
        line = json_output.dumps(message, indent=None)

        self._writer.write(line.encode("ascii"))
        self._writer.flush()

    # ------------------------------------------------------------------------
    def respond(self, ident: object, result: object) -> None:
        self.send({"jsonrpc": JSONRPC, "id": ident, "result": result})

    # ------------------------------------------------------------------------
    def fail(self, ident: object, error: RpcError) -> None:
        self.send({"jsonrpc": JSONRPC, "id": ident, "error": error.to_dict()})

    # ------------------------------------------------------------------------
    def notify(self, method: str, params: dict) -> None:
        self.send({"jsonrpc": JSONRPC, "method": method, "params": params})

    # ------------------------------------------------------------------------
    def request(self, method: str, params: dict) -> object:
        """Ask the client something, and wait for its answer.

        Returns the answer's ``result``; raises ``RpcError`` with the
        client's error code where it answered with an error, and
        :class:`ClientGone` where its input ended first. The server's ids
        are strings of their own, so they cannot be mistaken for the
        client's.
        """
        self._sent_requests += 1
        ident = f"mailctl-{self._sent_requests}"

        self.send(
            {
                "jsonrpc": JSONRPC,
                "id": ident,
                "method": method,
                "params": params,
            }
        )

        while True:
            message = self._read()

            if message is None:
                raise ClientGone(method)

            # A line that does not parse may be the answer, mistyped. Held,
            # it would leave both sides waiting on each other; so it is
            # answered as any such line is, and taken as a declined answer.
            if isinstance(message, RpcError):
                self.fail(None, message)

                raise RpcError(message.code, "")

            if is_response(message) and message.get("id") == ident:
                error = message.get("error")

                if error is not None:
                    code = error.get("code") if isinstance(error, dict) else 0

                    raise RpcError(code if isinstance(code, int) else 0, "")

                return message.get("result")

            self._held.append(message)
