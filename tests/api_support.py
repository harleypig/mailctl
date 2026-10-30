"""A client of the API process, as a client in any language would be one.

Not a test module. ``test_api.py`` and the container tier's API test share
it: it writes one JSON line to the process's stdin and reads JSON lines
off its stdout, and nothing else crosses.
"""

import json
import queue
import subprocess
import threading
from pathlib import Path

# Long enough for a slow CI machine to start Python; a hung process fails
# the test instead of the run.
TIMEOUT = 30


class Client:
    """A client of the API process: one JSON line out, one JSON line in.

    ``prompted`` counts the ``credentials.prompt`` requests answered;
    ``answer`` is what each is answered with, the reply's ``result`` or
    ``error`` member. Every line the process wrote is kept, so a test can
    look through all of it.
    """

    # ------------------------------------------------------------------------
    def __init__(self, command: list[str], env: dict[str, str], cwd: Path):
        self.lines: list[bytes] = []
        self.answer: dict = {"error": {"code": 1, "message": "no"}}
        self.prompted = 0
        self._next = 0
        self._out: queue.Queue[bytes] = queue.Queue()
        self._err: list[bytes] = []

        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
        )

        threading.Thread(target=self._pump_out, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()

    # ------------------------------------------------------------------------
    def _pump_out(self) -> None:
        assert self.process.stdout is not None

        for line in self.process.stdout:
            self.lines.append(line)
            self._out.put(line)

    # ------------------------------------------------------------------------
    def _pump_err(self) -> None:
        assert self.process.stderr is not None

        self._err.extend(self.process.stderr)

    # ------------------------------------------------------------------------
    def send_line(self, line: bytes) -> None:
        assert self.process.stdin is not None

        self.process.stdin.write(line)
        self.process.stdin.flush()

    # ------------------------------------------------------------------------
    def send(self, message: dict) -> None:
        self.send_line(json.dumps(message).encode() + b"\n")

    # ------------------------------------------------------------------------
    def receive(self) -> dict:
        line = self._out.get(timeout=TIMEOUT)

        assert line.endswith(b"\n") and line.count(b"\n") == 1

        return json.loads(line)

    # ------------------------------------------------------------------------
    def request(self, method: str, params: dict | None = None) -> dict:
        """Send a request; return its response, with every notification
        that came first under ``notes``, in order."""
        self._next += 1
        message: dict = {"jsonrpc": "2.0", "id": self._next, "method": method}

        if params is not None:
            message["params"] = params

        self.send(message)

        return self.until(self._next)

    # ------------------------------------------------------------------------
    def until(self, ident: object) -> dict:
        """Read to the response to ``ident``, answering what the server
        asks on the way."""
        notes = []

        while True:
            message = self.receive()

            if "method" not in message and message.get("id") == ident:
                return {**message, "notes": notes}

            if "method" in message and "id" in message:
                assert message["method"] == "credentials.prompt"

                self.prompted += 1
                self.send(
                    {"jsonrpc": "2.0", "id": message["id"], **self.answer}
                )

            notes.append(message)

    # ------------------------------------------------------------------------
    def close(self) -> tuple[int, bytes]:
        """End the input; return the exit code and everything on stderr."""
        assert self.process.stdin is not None

        self.process.stdin.close()
        code = self.process.wait(timeout=TIMEOUT)

        return code, b"".join(self._err)
