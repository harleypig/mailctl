"""A stand-in for ``mailctl-api``, scripted by a scene file.

Not a test module: the TUI's tests start it as the API process, so the TUI
is driven across a real process boundary over the real framing (one JSON
message per line on stdin and stdout) while each test decides what the
API says. ``STUB_SCENE`` names a JSON file holding:

- ``capabilities`` and ``provider``: what ``initialize`` returns;
- ``filters``: the ``filter.list`` document;
- ``alerts``: ``server.alert`` documents sent before ``filter.list``
  answers, as the real API sends one that arrives during a request;
- ``password_sha256``: where present, ``filter.list`` first asks for the
  password with ``credentials.prompt`` and refuses unless the answer's
  SHA-256 is this one.

What happened is written back to the file under ``seen``: the methods
called, and whether a password was given and matched. The password itself
is never written, printed, or kept.
"""

import hashlib
import json
import os
import sys
from pathlib import Path

SCENE = Path(os.environ["STUB_SCENE"])


# ----------------------------------------------------------------------------
def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


# ----------------------------------------------------------------------------
def record(**seen) -> None:
    scene = json.loads(SCENE.read_text(encoding="utf-8"))
    scene.setdefault("seen", {}).update(seen)
    SCENE.write_text(json.dumps(scene), encoding="utf-8")


# ----------------------------------------------------------------------------
def refusal(ident: object, message: str, code: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": ident,
        "error": {
            "code": 1,
            "message": message,
            "data": {
                "version": 2,
                "error": {"message": message, "code": code},
            },
        },
    }


# ----------------------------------------------------------------------------
def ask_password(ident: object, digest: str) -> dict | None:
    """Ask for the password; None where it matched, else the refusal."""
    send(
        {
            "jsonrpc": "2.0",
            "id": "mailctl-1",
            "method": "credentials.prompt",
            "params": {"request": ident, "prompt": "Password for stub:"},
        }
    )
    answer = json.loads(sys.stdin.readline())
    password = (answer.get("result") or {}).get("password")

    if not isinstance(password, str):
        record(password="declined")

        return refusal(
            ident,
            "the client declined to give a password",
            "password_declined",
        )

    matched = hashlib.sha256(password.encode()).hexdigest() == digest
    del password
    record(password="matched" if matched else "wrong")

    if matched:
        return None

    return refusal(ident, "the server refused the login", "auth_failed")


# ----------------------------------------------------------------------------
def main() -> int:
    scene = json.loads(SCENE.read_text(encoding="utf-8"))
    called: list[str] = []

    for line in sys.stdin:
        message = json.loads(line)
        method, ident = message.get("method"), message.get("id")
        called.append(method)
        record(methods=called)

        if method == "initialize":
            result = {
                "protocol": 1,
                "json_version": 2,
                "mailctl": "0.0.0-stub",
                "provider": scene.get("provider", "stub"),
                "capabilities": scene.get("capabilities", {}),
            }

        elif method == "filter.list":
            digest = scene.get("password_sha256")
            refused = digest and ask_password(ident, digest)

            if refused:
                send(refused)

                continue

            for alert in scene.get("alerts", []):
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "server.alert",
                        "params": {"request": ident, "document": alert},
                    }
                )

            result = scene["filters"]

        elif method == "shutdown":
            send({"jsonrpc": "2.0", "id": ident, "result": None})

            return 0

        else:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": ident,
                    "error": {"code": -32601, "message": "no method"},
                }
            )

            continue

        send({"jsonrpc": "2.0", "id": ident, "result": result})

    return 0


if __name__ == "__main__":
    sys.exit(main())
