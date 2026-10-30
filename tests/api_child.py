"""The API process, with its two servers replaced by doubles.

Not a test module: ``test_api.py`` starts this as the subprocess its
line-reading client talks to, so the API is driven across a real process
boundary while the mail it reads and the filter set it writes stay on
this machine. Everything above the two protocol clients -- the sessions,
the provider, the utilities, the API -- is the real code.

The doubles keep their state in a JSON file the test named in
``MAILCTL_TEST_STATE``, read afresh on every call, so the test can change
the filter set between a plan and its execute as another client would,
and read back what the API stored. The password is never in that file:
only its SHA-256, which a login is checked against, so a password that
reached the connection is known to have done so without being written
anywhere.

Nothing here prints. Whatever this process writes to stdout is the API's.
"""

import hashlib
import json
import os
import socket
import sys
from pathlib import Path

from conftest import FakeIMAPClient
from imapclient.exceptions import LoginError

from mailctl import api
from mailctl.components.imap import client as imap_client
from mailctl.components.managesieve import client as sieve_client

STATE = Path(os.environ["MAILCTL_TEST_STATE"])


# ----------------------------------------------------------------------------
def load() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------
def save(state: dict) -> None:
    STATE.write_text(json.dumps(state), encoding="utf-8")


# ----------------------------------------------------------------------------
def accepted(password: str) -> bool:
    """Whether ``password`` is the account's, counting the attempt."""
    state = load()
    state["logins"] = state.get("logins", 0) + 1
    save(state)

    digest = hashlib.sha256(password.encode("utf-8")).hexdigest()

    return digest == state["password_sha256"]


class FileSieveClient:
    """``SieveClient`` over the state file: scripts, and which is active."""

    warning = None

    # ------------------------------------------------------------------------
    def __init__(self, *_args, **_kwargs):
        pass

    # ------------------------------------------------------------------------
    def connect(self, user, password, starttls=False, ssl=False) -> bool:
        return accepted(password)

    # ------------------------------------------------------------------------
    def logout(self) -> None:
        pass

    # ------------------------------------------------------------------------
    def listscripts(self):
        state = load()
        active = state["active"]

        return active, [name for name in state["scripts"] if name != active]

    # ------------------------------------------------------------------------
    def getscript_bytes(self, name: str):
        text = load()["scripts"].get(name)

        return None if text is None else text.encode("utf-8")

    # ------------------------------------------------------------------------
    def checkscript(self, content: str) -> bool:
        return True

    # ------------------------------------------------------------------------
    def putscript(self, name: str, content: str) -> bool:
        state = load()
        state["scripts"][name] = content
        state.setdefault("stored", []).append(name)
        save(state)

        return True

    # ------------------------------------------------------------------------
    def setactive(self, name: str) -> bool:
        state = load()
        state["active"] = name
        save(state)

        return True

    # ------------------------------------------------------------------------
    @property
    def capability_response(self) -> bytes:
        return b'"SIEVE" "fileinto imap4flags mailbox"\r\n'


# ----------------------------------------------------------------------------
def imap_double() -> FakeIMAPClient:
    """The suite's IMAP double, holding the state file's messages, with
    its login checked like the ManageSieve one's."""
    state = load()
    client = FakeIMAPClient()
    client.messages = {
        int(uid): raw.encode("utf-8") for uid, raw in state["messages"].items()
    }
    client.uidvalidity.update(state.get("uidvalidity", {}))

    def login(user, password):
        if not accepted(password):
            raise LoginError("[AUTHENTICATIONFAILED] Authentication failed.")

        client.calls.append(("login", user))

    client.login = login

    alert = state.get("alert")

    # Sent during every SEARCH, so a second search meets it again: the
    # folder list is read once, at login, and would show it only once.
    if alert:
        search = client.search

        def alerting(*args, **kwargs):
            client.responses["search"] = [
                f"* OK [ALERT] {alert}".encode(),
                b"a2 OK Search completed",
            ]

            return search(*args, **kwargs)

        client.search = alerting

    return client


# ----------------------------------------------------------------------------
def refuse_network(*_args, **_kwargs):
    raise OSError("the API test double opens no socket")


if __name__ == "__main__":
    socket.socket.connect = refuse_network  # type: ignore[method-assign]

    double = imap_double()
    imap_client.IMAPClient = lambda *_a, **_k: double  # type: ignore[assignment]
    sieve_client.SieveClient = FileSieveClient  # type: ignore[assignment,misc]

    sys.exit(api.main(sys.argv[1:]))
