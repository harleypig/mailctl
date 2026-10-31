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

Each ManageSieve connection is counted in the file's ``sieve_connects``,
so a test can see that a method never touched the rules half.

What the IMAP double is asked to write is appended to the file's
``imap_writes``, and its UIDVALIDITY is read from the file on every
SELECT, so a test can see what an execute did to the mail and renumber a
folder between a plan and its execute. ``MAILCTL_TEST_DECLINE`` names
capabilities, comma separated, the provider is to declare false, so a
test reaches the refusal a provider without them meets.

Nothing here prints. Whatever this process writes to stdout is the API's.
"""

import dataclasses
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
from mailctl.providers import registry

# Every IMAP command the double takes that changes the mailbox.
IMAP_WRITES = (
    "add_flags",
    "remove_flags",
    "move",
    "copy",
    "uid_expunge",
    "expunge",
    "create_folder",
    "subscribe_folder",
)

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
        state = load()
        state["sieve_connects"] = state.get("sieve_connects", 0) + 1
        save(state)

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
        # As imaplib's _command sends LOGIN's arguments, so a password it
        # cannot send fails here as it does there, naming the character.
        bytes(user, "ascii")
        bytes(password, "ascii")

        if not accepted(password):
            raise LoginError("[AUTHENTICATIONFAILED] Authentication failed.")

        client.calls.append(("login", user))

    client.login = login

    for name in IMAP_WRITES:
        setattr(client, name, recorded(name, getattr(client, name)))

    select = client.select_folder

    def selecting(folder, readonly=True):
        client.uidvalidity.update(load().get("uidvalidity", {}))

        return select(folder, readonly)

    client.select_folder = selecting

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
def recorded(name: str, write):
    """``write``, appending its name and arguments to the file's
    ``imap_writes`` before it runs."""

    def call(*args):
        state = load()
        state.setdefault("imap_writes", []).append(
            json.loads(json.dumps([name, *args], default=str))
        )
        save(state)

        return write(*args)

    return call


# ----------------------------------------------------------------------------
def decline(names: str) -> None:
    """Register mxroute again, declaring each capability ``names`` lists
    false."""
    provider = registry.PROVIDERS["mxroute"]
    declined = {name: False for name in names.split(",") if name}
    registry.PROVIDERS["mxroute"] = dataclasses.replace(
        provider,
        capabilities=dataclasses.replace(provider.capabilities, **declined),
    )


# ----------------------------------------------------------------------------
def refuse_network(*_args, **_kwargs):
    raise OSError("the API test double opens no socket")


if __name__ == "__main__":
    socket.socket.connect = refuse_network  # type: ignore[method-assign]
    decline(os.environ.get("MAILCTL_TEST_DECLINE", ""))

    double = imap_double()
    imap_client.IMAPClient = lambda *_a, **_k: double  # type: ignore[assignment]
    sieve_client.SieveClient = FileSieveClient  # type: ignore[assignment,misc]

    sys.exit(api.main(sys.argv[1:]))
