"""A stand-in for ``mailctl-api``, scripted by a scene file.

Not a test module: the TUI's tests start it as the API process, so the TUI
is driven across a real process boundary over the real framing (one JSON
message per line on stdin and stdout) while each test decides what the
API says. ``STUB_SCENE`` names a JSON file holding:

- ``capabilities`` and ``provider``: what ``initialize`` returns;
- ``filters``: the ``filter.list`` document;
- ``alerts``: ``server.alert`` documents sent before ``filter.list``
  answers, or before the method ``alerts_on`` names, as the real API
  sends one that arrives during a request;
- ``password_sha256``: where present, ``filter.list`` first asks for the
  password with ``credentials.prompt`` and refuses unless the answer's
  SHA-256 is this one;
- ``listing``: the ``mail.search`` document, and ``like``: what
  ``mail.search`` returns for ``like`` with ``build_filter``; null or
  absent refuses those parameters, as an API without them does;
- ``message``: the ``mail.view`` document;
- ``plan``: the ``filter.add.plan`` document, kept under the id ``p-1``;
- ``events`` and ``result``: the ``event`` documents
  ``filter.add.execute`` sends, in order, and its result;
- ``refuse``: a method name to answer with a refusal, as mailctl words
  one, from ``refusal_message``.

What happened is written back to the file under ``seen``: the methods
called, each request's parameters by method, and whether a password was
given and matched. The password itself is never written, printed, or
kept: no method takes one.
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
def invalid_params(ident: object, names: list[str]) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": ident,
        "error": {
            "code": -32602,
            "message": f"unknown parameter(s) {', '.join(names)}",
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
    given: dict[str, list] = {}
    plans = set()

    for line in sys.stdin:
        message = json.loads(line)
        method, ident = message.get("method"), message.get("id")
        params = message.get("params", {})
        called.append(method)
        given.setdefault(method, []).append(params)
        record(methods=called, params=given)

        if method == scene.get("alerts_on", "filter.list"):
            for alert in scene.get("alerts", []):
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "server.alert",
                        "params": {"request": ident, "document": alert},
                    }
                )

        if method == scene.get("refuse"):
            send(refusal(ident, scene["refusal_message"], "refused"))

            continue

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

            result = scene["filters"]

        elif method == "mail.search" and "build_filter" in params:
            if scene.get("like") is None:
                send(invalid_params(ident, ["build_filter", "like"]))

                continue

            result = scene["like"]

        elif method == "mail.search":
            result = scene["listing"]

        elif method == "mail.view":
            result = scene["message"]

        elif method == "filter.add.plan":
            plans.add("p-1")
            result = {"plan_id": "p-1", "document": scene["plan"]}

        elif method in ("filter.add.execute", "plan.discard"):
            if params.get("plan_id") not in plans:
                send(refusal(ident, "no plan with that id", "plan_unknown"))

                continue

            plans.discard(params["plan_id"])

            for event in (
                scene.get("events", []) if "execute" in method else ()
            ):
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {"request": ident, "document": event},
                    }
                )

            result = scene.get("result") if "execute" in method else None

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
