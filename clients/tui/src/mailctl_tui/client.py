"""A JSON-RPC 2.0 client of the mailctl API, over a child's stdio.

The API is a process this client starts: one JSON message per line on its
stdin and stdout, and diagnostics on its stderr (ADR 0008). Every request
gets an integer id; the API's own requests to us carry string ids, so the
two never meet.

The API may ask us something while one of our requests is waiting
(``credentials.prompt``), so the reader never blocks on an answer: each
request from the API is answered in a task of its own.
"""

import asyncio
import contextlib
import json
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

__all__ = ["ApiClient", "ApiError", "ApiGone", "Declined"]

# A filter set or a message can make a document far longer than asyncio's
# 64 KiB default line limit, and a line cut short does not parse.
LINE_LIMIT = 64 * 1024 * 1024

# The stderr lines kept to explain an API that ended unexpectedly.
STDERR_KEPT = 20

Notification = Callable[[str, dict], None]
ServerRequest = Callable[[str, dict], Awaitable[dict]]


class ApiError(Exception):
    """The API answered a request with an error.

    ``code`` is the JSON-RPC code and ``data`` the error document, whose
    own ``code`` is what to branch on (ADR 0008 *Errors keep their codes*).
    """

    # ------------------------------------------------------------------------
    def __init__(self, code: int, message: str, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}


class ApiGone(Exception):
    """The API process ended, or could not be started."""


class Declined(Exception):
    """Raised by a server-request handler to answer with a refusal."""


class ApiClient:
    """One API process and the conversation with it."""

    # ------------------------------------------------------------------------
    def __init__(
        self,
        command: list[str],
        *,
        on_notification: Notification,
        on_request: ServerRequest,
        env: dict[str, str] | None = None,
    ):
        self.command = command
        self.stderr: deque[str] = deque(maxlen=STDERR_KEPT)
        self._env = env
        self._on_notification = on_notification
        self._on_request = on_request
        self._process: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next = 0
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------------
    async def start(self) -> None:
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
                limit=LINE_LIMIT,
            )

        except OSError as exc:
            raise ApiGone(
                f"could not start {self.command[0]!r}: {exc.strerror}"
            ) from exc

        self._spawn(self._read_stdout())
        self._spawn(self._read_stderr())

    # ------------------------------------------------------------------------
    def _spawn(self, coroutine: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------------
    async def request(self, method: str, params: dict | None = None) -> Any:
        """Send a request and return its result; raise ``ApiError`` for
        an error answer and ``ApiGone`` once the process has ended."""
        self._next += 1
        ident = self._next
        future = asyncio.get_running_loop().create_future()
        self._pending[ident] = future

        message: dict = {"jsonrpc": "2.0", "id": ident, "method": method}

        if params is not None:
            message["params"] = params

        await self._send(message)

        return await future

    # ------------------------------------------------------------------------
    async def close(self) -> None:
        """Shut the API down if it is still there, and wait for it."""
        process = self._process

        if process is None or process.returncode is not None:
            return

        # The API answers in order, so a shutdown queued behind a request
        # still waiting on us (a password prompt) never comes back; ending
        # its input ends it as well.
        with contextlib.suppress(ApiError, ApiGone, TimeoutError):
            await asyncio.wait_for(self.request("shutdown"), timeout=2)

        if process.stdin is not None:
            process.stdin.close()

        try:
            await asyncio.wait_for(process.wait(), timeout=5)

        except TimeoutError:
            process.kill()
            await process.wait()

    # ------------------------------------------------------------------------
    async def _send(self, message: dict) -> None:
        process = self._process

        if process is None or process.stdin is None:
            raise ApiGone("the API is not running")

        try:
            process.stdin.write(json.dumps(message).encode("ascii") + b"\n")
            await process.stdin.drain()

        except (BrokenPipeError, ConnectionResetError) as exc:
            raise ApiGone(self._ended()) from exc

    # ------------------------------------------------------------------------
    async def _read_stdout(self) -> None:
        assert self._process is not None and self._process.stdout is not None

        while line := await self._process.stdout.readline():
            try:
                message = json.loads(line)

            except ValueError:
                continue

            if isinstance(message, dict):
                self._dispatch(message)

        await self._process.wait()
        gone = ApiGone(self._ended())

        for future in self._pending.values():
            if not future.done():
                future.set_exception(gone)

        self._pending.clear()

    # ------------------------------------------------------------------------
    async def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None

        while line := await self._process.stderr.readline():
            self.stderr.append(line.decode("utf-8", "replace").rstrip("\n"))

    # ------------------------------------------------------------------------
    def _dispatch(self, message: dict) -> None:
        method = message.get("method")

        if isinstance(method, str) and "id" in message:
            self._spawn(self._answer(message["id"], method, message))

        elif isinstance(method, str):
            params = message.get("params")
            self._on_notification(
                method, params if isinstance(params, dict) else {}
            )

        else:
            ident = message.get("id")
            future = (
                self._pending.pop(ident, None)
                if isinstance(ident, int)
                else None
            )

            if future is None or future.done():
                return

            error = message.get("error")

            if isinstance(error, dict):
                future.set_exception(
                    ApiError(
                        int(error.get("code", 0)),
                        str(error.get("message", "")),
                        error.get("data"),
                    )
                )

            else:
                future.set_result(message.get("result"))

    # ------------------------------------------------------------------------
    async def _answer(self, ident: object, method: str, message: dict) -> None:
        """Answer one request from the API, with what the handler gives.

        The reply is built and written here and nowhere else, and nothing
        holds it afterwards: for ``credentials.prompt`` it is the password.
        """
        params = message.get("params")

        try:
            result = await self._on_request(
                method, params if isinstance(params, dict) else {}
            )
            reply: dict = {"jsonrpc": "2.0", "id": ident, "result": result}

        except Declined:
            reply = {
                "jsonrpc": "2.0",
                "id": ident,
                "error": {"code": 1, "message": "declined"},
            }

        with contextlib.suppress(ApiGone):
            await self._send(reply)

    # ------------------------------------------------------------------------
    def _ended(self) -> str:
        code = self._process.returncode if self._process else None
        said = "; ".join(line for line in self.stderr if line.strip())
        ended = f"the API ended (exit {code})"

        return f"{ended}: {said}" if said else ended
