"""Shared fixtures for the offline tier.

Everything here is offline by construction. The IMAP double below stands in
for ``IMAPClient`` at the module boundary -- it is never a stand-in for
anything mailctl owns, so a test that passes against it is still testing
mailctl's logic rather than its own mock.

Two hazards this file exists to remove:

* The real user's ``config.toml`` and ``MAILCTL_*`` variables would
  otherwise leak into ``load_config`` and make the resolution-order tests
  depend on the machine they run on.
* A test that forgot to patch ``IMAPClient`` would open a socket. The
  ``no_network`` fixture below turns that into an immediate named failure
  rather than a slow, flaky pass against a real host.
"""

import datetime
import email
import email.utils
import os
import socket

import pytest
from imapclient import exceptions as imapclient_exceptions
from sievelib import parser
from utilities_support import DEFAULT_UIDVALIDITY, FakeSieveSession, mxroute

from mailctl.components.imap import client as imap_client
from mailctl.config import Config, Secret
from mailctl.engine import Session
from mailctl.providers.mxroute.imap import new_imap_session

# ############################################################################
# Environment isolation
# ############################################################################

# Variables that steer the test run rather than configure the tool. They
# share the tool's prefix, so the scrubbing below has to step round them.
TEST_CONTROLS = {
    "MAILCTL_CONTAINER",
    "MAILCTL_LIVE",
    "MAILCTL_UPDATE_SNAPSHOTS",
}


# ----------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    """Detach every test from the developer's own account settings.

    ``load_config`` reads ``$XDG_CONFIG_HOME/mailctl/config.toml`` and the
    ``MAILCTL_*`` variables, and reports any old ``MXROUTE_*`` ones. Without
    this the suite would pass or fail depending on whose shell it ran in,
    and a real password could reach a test's assertion output.
    """
    for name in list(os.environ):
        if name in TEST_CONTROLS:
            continue

        if name.startswith(("MAILCTL_", "MXROUTE_")):
            monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


# ----------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Make an accidental connection fail loudly instead of succeeding.

    The offline tier's defining property is that it opens no socket. A
    test that forgot to patch ``IMAPClient`` or ``managesieve.Client``
    would otherwise reach a real host -- slowly, flakily, and with the
    developer's own credentials -- and still pass. This turns that into an
    immediate, named failure.

    ``socket.socket`` is the one chokepoint both client libraries go
    through, so patching it needs no knowledge of either.
    """

    def refuse(*args, **kwargs):
        raise RuntimeError(
            "this test tried to open a network connection; the offline "
            "tier must patch the client at mailctl's import boundary "
            "(see the fake_imap fixture)"
        )

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


# ############################################################################
# Sieve fixtures
# ############################################################################

# A script in the shape Roundcube's managesieve plugin writes: tab-indented,
# brace on its own line, and a `# rule:[name]` marker rather than sievelib's
# own `# Filter:` comment. This is the script the merge must not destroy --
# it is what an MXroute account actually has in it before mailctl ever
# runs (ADR 0002).
ROUNDCUBE_SCRIPT = """require ["fileinto","imap4flags"];
# rule:[keep-boss]
if header :contains "from" "boss@example.com"
{
\tfileinto "INBOX.Boss";
\tsetflag "\\\\Flagged";
\tstop;
}
# rule:[bin-the-noise]
if header :contains "subject" "newsletter"
{
\tfileinto "INBOX.Noise";
\tstop;
}
"""


# ----------------------------------------------------------------------------
@pytest.fixture
def roundcube_script() -> str:
    """The hand-made script a merge has to carry through untouched."""
    return ROUNDCUBE_SCRIPT


# ----------------------------------------------------------------------------
def _reparse(text: str) -> parser.Parser:
    """Parse ``text`` with sievelib, failing the test if it will not.

    Round-tripping through sievelib's own parser is the only check that
    actually proves the emitted script is loadable; string assertions alone
    would happily pass on syntactically broken output.
    """
    script_parser = parser.Parser()

    assert script_parser.parse(text.encode("utf-8")), (
        f"emitted script does not reparse: "
        f"{getattr(script_parser, 'error', 'unknown parse error')}\n{text}"
    )

    return script_parser


# ----------------------------------------------------------------------------
@pytest.fixture
def reparse():
    """Hand tests the sievelib round-trip check as a callable."""
    return _reparse


# ############################################################################
# IMAP double
# ############################################################################


class FakeImaplib:
    """IMAPClient's imaplib connection, reduced to what LIST-STATUS uses.

    ``_simple_command`` answers ``LIST "" "*" RETURN (STATUS (...))`` with
    a STATUS line per folder in the double's ``counts`` carrying only the
    items asked for, left where imaplib leaves them: in
    ``untagged_responses``, the word STATUS already taken off.

    ``_get_response`` is imaplib's reader, handing back the next response
    line the server sent -- what the session watches for alerts.
    """

    # ------------------------------------------------------------------------
    def __init__(self, client: "FakeIMAPClient"):
        self.client = client
        self.untagged_responses: dict[str, list] = {}
        self.pending: list[bytes] = []

    # ------------------------------------------------------------------------
    def _get_response(self) -> bytes:
        return self.pending.pop(0)

    # ------------------------------------------------------------------------
    def _simple_command(self, name: str, *args: str):
        self.client._maybe_fail("list_status")
        self.client.calls.append((name, *args))

        items = args[-1].removeprefix("(STATUS (").removesuffix("))").split()
        listed, status = [], []

        for _flags, _delimiter, folder in self.client.listing:
            listed.append(b'() "." "' + folder + b'"')
            counts = self.client.counts.get(folder.decode())

            if counts is None:
                continue

            values: dict[str, int] = dict(
                zip(("MESSAGES", "UNSEEN", "SIZE"), counts, strict=True)
            )
            body = " ".join(f"{item} {values[item]}" for item in items)
            status.append(b'"' + folder + b'" (' + body.encode() + b")")

        self.untagged_responses["LIST"] = listed
        self.untagged_responses["STATUS"] = status

        return "OK", [b"LIST completed"]


class FakeIMAPClient:
    """A stand-in for ``IMAPClient``, recording what it was asked to do.

    Only the handful of methods ``ImapSession`` calls are implemented. It
    deliberately does not emulate IMAP semantics -- the point is to observe
    the calls mailctl makes and to feed it canned responses, not to
    reimplement a server.
    """

    # ------------------------------------------------------------------------
    def __init__(self):
        """Start out as an empty INBOX on a Maildir++ style server.

        LIST and LSUB deliberately disagree out of the box: ``INBOX.spam``
        exists without being subscribed, which is the condition observed on
        a real account (issue #38) and the one a double that quietly kept
        the two in step could never reproduce.
        """
        self.calls: list[tuple] = []
        self.connected_to: tuple | None = None
        self.starttls_called = False
        self.listing = [
            ((), b".", b"INBOX"),
            ((), b".", b"INBOX.Lists"),
            ((), b".", b"INBOX.spam"),
        ]
        self.subscriptions = [
            ((), b".", b"INBOX"),
            ((), b".", b"INBOX.Lists"),
        ]
        self.messages: dict[int, bytes] = {}
        self.flags: dict[int, tuple] = {}

        # Mail held by one folder in particular, by folder name. A folder
        # not named here holds ``messages``, so a test that never sets
        # this sees one mailbox whatever it selects.
        self.folder_messages: dict[str, dict[int, bytes]] = {}
        self.selected: str | None = None
        self.structures: dict[int, object] = {}
        self.caps = {"MOVE", "UIDPLUS"}

        # The parsed ID response (RFC 2971), as IMAPClient's ``id_`` returns
        # it; MXroute's reads ``name: Dovecot`` with no version (#18).
        self.id_response: tuple = ((b"name", b"Dovecot"),)

        # The NAMESPACE response (RFC 2342) as IMAPClient's ``namespace``
        # returns it: (personal, other, shared). Answered only once a test
        # adds NAMESPACE to ``caps``, so by default the prefix is unknown.
        self.namespace_response: tuple = ((("INBOX.", "."),), None, None)

        # Every FETCH as (uids, items), and the \Seen a real server would
        # have set in response -- kept apart from ``calls`` so the snapshot
        # records stay as they are.
        self.fetches: list[tuple[tuple, tuple]] = []
        self.readonly = True
        self.marked_seen: set[int] = set()
        self.failures: dict[str, Exception] = {}

        # A server that answers OK to SUBSCRIBE and does not act on it.
        # Nothing advertises this, so the only defence is re-reading LSUB.
        self.subscribe_takes_effect = True

        # Each folder's (MESSAGES, UNSEEN, SIZE), for LIST-STATUS; a folder
        # left out gets no STATUS line, as one that cannot hold mail.
        self.counts: dict[str, tuple[int, int, int]] = {
            "INBOX": (3, 1, 2048),
            "INBOX.Lists": (0, 0, 0),
            "INBOX.spam": (12, 12, 30822),
        }
        self._imap = FakeImaplib(self)

        # The greeting imaplib read as it connected, and, by method, the
        # response lines the server sends while that command runs -- read
        # through imaplib's reader, as IMAPClient reads them, and before
        # any failure the method is armed with.
        self.welcome = b"* OK Dovecot ready."
        self.responses: dict[str, list[bytes]] = {}

        # Each folder's UIDVALIDITY, as SELECT and EXAMINE report it; a
        # folder not named here reports ``DEFAULT_UIDVALIDITY``, and one
        # named with None reports none at all.
        self.uidvalidity: dict[str, int | None] = {}

    # ------------------------------------------------------------------------
    def _maybe_fail(self, name: str) -> None:
        """Read the lines the server sends this command, then raise
        whatever the test armed the method with."""
        for line in self.responses.pop(name, ()):
            self._imap.pending.append(line)
            self._imap._get_response()

        error = self.failures.get(name)

        if error is not None:
            raise error

    # ------------------------------------------------------------------------
    def login(self, user, password) -> None:
        self._maybe_fail("login")
        self.calls.append(("login", user))

    # ------------------------------------------------------------------------
    def starttls(self) -> None:
        self.starttls_called = True

    # ------------------------------------------------------------------------
    def logout(self) -> None:
        self.calls.append(("logout",))

    # ------------------------------------------------------------------------
    def list_folders(self):
        self._maybe_fail("list_folders")

        return self.listing

    # ------------------------------------------------------------------------
    def list_sub_folders(self):
        self._maybe_fail("list_sub_folders")

        return self.subscriptions

    # ------------------------------------------------------------------------
    def subscribe_folder(self, folder: str) -> None:
        self._maybe_fail("subscribe_folder")
        self.calls.append(("subscribe_folder", folder))

        if self.subscribe_takes_effect:
            self.subscriptions.append(((), b".", folder.encode()))

    # ------------------------------------------------------------------------
    def unsubscribe_folder(self, folder: str) -> None:
        self._maybe_fail("unsubscribe_folder")
        self.calls.append(("unsubscribe_folder", folder))

        self.subscriptions = [
            entry
            for entry in self.subscriptions
            if entry[2] != folder.encode()
        ]

    # ------------------------------------------------------------------------
    def capabilities(self):
        return [name.encode() for name in sorted(self.caps)]

    # ------------------------------------------------------------------------
    def has_capability(self, name: str) -> bool:
        return name in self.caps

    # ------------------------------------------------------------------------
    def id_(self):
        self._maybe_fail("id_")
        self.calls.append(("id_",))

        return self.id_response

    # ------------------------------------------------------------------------
    def namespace(self):
        self._maybe_fail("namespace")
        self.calls.append(("namespace",))

        return self.namespace_response

    # ------------------------------------------------------------------------
    def create_folder(self, folder: str) -> None:
        self._maybe_fail("create_folder")
        self.calls.append(("create_folder", folder))
        self.listing.append(((), b".", folder.encode()))

    # ------------------------------------------------------------------------
    def rename_folder(self, old: str, new: str) -> None:
        """RENAME as RFC 3501 section 6.3.5 has it: the folder and those
        under it move, and the subscription list is left as it was."""
        self._maybe_fail("rename_folder")
        self.calls.append(("rename_folder", old, new))

        def moved(name: bytes) -> bytes:
            text = name.decode()

            if text == old or text.startswith(old + "."):
                return (new + text[len(old) :]).encode()

            return name

        self.listing = [
            (flags, separator, moved(name))
            for flags, separator, name in self.listing
        ]

        # Rebound, not edited: a snapshot scenario hands every run the same
        # counts mapping.
        self.counts = {
            (new if name == old else name): counts
            for name, counts in self.counts.items()
        }

    # ------------------------------------------------------------------------
    def folder_status(self, folder: str, what=None) -> dict:
        self._maybe_fail("folder_status")
        self.calls.append(("folder_status", folder, tuple(what or ())))

        # STATUS MESSAGES is the first of a folder's LIST-STATUS counts; a
        # folder with none holds every message in ``messages``.
        counts = self.counts.get(folder)

        return {
            b"MESSAGES": len(self.messages) if counts is None else counts[0]
        }

    # ------------------------------------------------------------------------
    def select_folder(self, folder: str, readonly: bool = True) -> dict:
        self._maybe_fail("select_folder")
        self.calls.append(("select_folder", folder, readonly))
        self.readonly = readonly
        self.selected = folder

        validity = self.uidvalidity.get(folder, DEFAULT_UIDVALIDITY)

        # IMAPClient's parsed SELECT response, cut to the one item read.
        return {} if validity is None else {b"UIDVALIDITY": validity}

    # ------------------------------------------------------------------------
    def _mailbox(self) -> dict[int, bytes]:
        """The mail in the selected folder."""
        if self.selected is None:
            return self.messages

        return self.folder_messages.get(self.selected, self.messages)

    # ------------------------------------------------------------------------
    def search(self, key, charset=None):
        self._maybe_fail("search")
        self.calls.append(
            ("search", key) if charset is None else ("search", key, charset)
        )

        return sorted(self._mailbox())

    # ------------------------------------------------------------------------
    def sort(self, sort_criteria, criteria="ALL", charset="UTF-8"):
        """Every message, ordered as RFC 5256 would order it.

        Like ``search``, the criteria are recorded and not applied. SIZE
        is the source's length, DATE the Date header (or, as for ARRIVAL,
        the fixed INTERNALDATE ``fetch`` reports), and ties keep UID
        order in either direction.
        """
        self._maybe_fail("sort")
        self.calls.append(("sort", tuple(sort_criteria), criteria, charset))

        if "SORT" not in self.caps:
            raise imapclient_exceptions.CapabilityError(
                "Server does not support SORT"
            )

        reverse = sort_criteria[0] == "REVERSE"
        key = sort_criteria[-1]
        stamp = datetime.datetime(2026, 2, 3, 4, 5, 6).astimezone()

        def value(uid):
            if key == "SIZE":
                return len(self.messages[uid])

            if key == "DATE":
                header = email.message_from_bytes(self.messages[uid])["Date"]

                if header:
                    return email.utils.parsedate_to_datetime(header)

            return stamp

        return sorted(sorted(self.messages), key=value, reverse=reverse)

    # ------------------------------------------------------------------------
    def fetch(self, uids, parts):
        self._maybe_fail("fetch")
        self.calls.append(("fetch", tuple(uids)))
        self.fetches.append((tuple(uids), tuple(parts)))

        # RFC 3501 6.4.5: these items set \Seen, unless the folder was
        # opened read-only (EXAMINE). The .PEEK forms never do.
        if not self.readonly and any(
            part.upper().startswith("BODY[")
            or part.upper() in ("RFC822", "RFC822.TEXT")
            for part in parts
        ):
            self.marked_seen.update(
                uid for uid in uids if uid in self.messages
            )

        stamp = datetime.datetime(2026, 2, 3, 4, 5, 6)
        mailbox = self._mailbox()
        response = {}

        for uid in uids:
            if uid not in mailbox:
                continue

            source = mailbox[uid]
            data = {b"BODY[HEADER]": source, b"INTERNALDATE": stamp}

            if "BODY.PEEK[]" in parts:
                data[b"BODY[]"] = source

            if "RFC822.SIZE" in parts:
                data[b"RFC822.SIZE"] = len(source)

            if "FLAGS" in parts:
                data[b"FLAGS"] = self.flags.get(uid, ())

            if "BODYSTRUCTURE" in parts:
                data[b"BODYSTRUCTURE"] = self.structures.get(uid)

            response[uid] = data

        return response

    # ------------------------------------------------------------------------
    def add_flags(self, uids, flags) -> None:
        self._maybe_fail("add_flags")
        self.calls.append(("add_flags", tuple(uids), tuple(flags)))

    # ------------------------------------------------------------------------
    def remove_flags(self, uids, flags) -> None:
        self._maybe_fail("remove_flags")
        self.calls.append(("remove_flags", tuple(uids), tuple(flags)))

    # ------------------------------------------------------------------------
    def move(self, uids, destination) -> None:
        self._maybe_fail("move")
        self.calls.append(("move", tuple(uids), destination))

    # ------------------------------------------------------------------------
    def copy(self, uids, destination) -> None:
        self._maybe_fail("copy")
        self.calls.append(("copy", tuple(uids), destination))

    # ------------------------------------------------------------------------
    def uid_expunge(self, uids) -> None:
        self.calls.append(("uid_expunge", tuple(uids)))

    # ------------------------------------------------------------------------
    def expunge(self) -> None:
        self.calls.append(("expunge",))

    # ------------------------------------------------------------------------
    def names(self) -> list[str]:
        """Return just the recorded method names, for order assertions."""
        return [call[0] for call in self.calls]


# ----------------------------------------------------------------------------
@pytest.fixture
def fake_imap(monkeypatch) -> FakeIMAPClient:
    """Patch ``IMAPClient`` at mailctl's boundary and hand back the double."""
    client = FakeIMAPClient()

    def factory(host, port=None, ssl=True):
        client.connected_to = (host, port, ssl)

        return client

    monkeypatch.setattr(imap_client, "IMAPClient", factory)

    return client


# ----------------------------------------------------------------------------
@pytest.fixture
def imap_config() -> Config:
    """A Config with the credential already resolved, so nothing prompts."""
    config = Config(
        host="mail.example.com",
        imap_host="mail.example.com",
        user="user@example.com",
    )
    config._password = Secret("not-a-real-password")

    return config


# ----------------------------------------------------------------------------
@pytest.fixture
def imap_session(fake_imap, imap_config):
    """An opened ImapSession wired to the double."""
    session = new_imap_session(imap_config)
    session.open()

    return session


# ############################################################################
# The provider the utilities tests drive
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.fixture
def fake_sieve(roundcube_script) -> FakeSieveSession:
    return FakeSieveSession(script=roundcube_script)


# ----------------------------------------------------------------------------
@pytest.fixture
def sessions(fake_sieve, imap_session) -> Session:
    return mxroute(sieve=fake_sieve, imap=imap_session)
