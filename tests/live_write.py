"""The guards a test must stand behind before it writes to a real mailbox.

The live tier's safety contract (``tests/live/conftest.py``, TESTS.md) has
two clauses for a test that writes, and this module is both of them:

* **``guard_scripts``** captures every Sieve script -- its exact bytes --
  and which one is active, before the test; afterwards it puts back
  whatever the test changed, removes any script the test created, and
  then *confirms* the server holds what was captured. A restore it cannot
  confirm raises :class:`RestoreNotConfirmed`, naming the saved copy on
  disk, however the test ended -- passed, failed, or interrupted.
* **``claim_scratch_folder``** makes a new, uniquely named folder for the
  test and deletes it, with everything in it, afterwards. It refuses INBOX
  and any folder that already exists, so it can only ever delete what it
  created itself. Messages the test needs are appended there
  (:meth:`ScratchFolder.append`).

**It is generic over the connection.** Everything takes a :class:`Mailbox`,
which each tier builds its own way: the live tier from the user's resolved
configuration, the container tier from its throwaway server. That is how
the container tier proves this code before it is pointed at a real
account (#9).

**What it is built on, and why.** The fixture exists to protect the account
from a mailctl bug, so as little of it as possible is mailctl:

* Capture reads with mailctl's layer-1 ``SieveClient.getscript_bytes``,
  because it is the only byte-exact GETSCRIPT there is -- sievelib's own
  turns CRLF into LF and drops the final line ending (#90), and a restore
  from that would silently rewrite the user's script.
* Every write -- PUTSCRIPT, SETACTIVE, DELETESCRIPT -- goes through
  sievelib's own ``Client``, and every IMAP step through IMAPClient, so no
  mailctl write path sits between the test and the restore.
* The confirmation reads twice: byte for byte through the same reader as
  the capture, and through sievelib's own GETSCRIPT compared with what
  sievelib returned at capture. The second is blind to line endings; it is
  there so a reader defect that captured and re-read the script the same
  wrong way still shows. The container tier adds a third oracle, the
  script file on the server's disk.

**Writes are few and never looped.** A real account is on the other end,
and a burst of commands can read as abuse. The script guard writes
nothing when the test changed nothing, and otherwise one command per
script it must put back or remove. The folder costs one CREATE, one APPEND
per message the test asks for, and one DELETE per folder at the end.

**The password is revealed only as it is handed to a login.** A
:class:`Mailbox` holds a callable returning a ``config.Secret``, never the
value, and it is kept out of the dataclass's repr. No message raised here
carries it, and neither client library is run in a debug mode that would
print the AUTHENTICATE exchange (ADR 0006, S8).
"""

import hashlib
import os
import ssl
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar, cast

import pytest
from imapclient import IMAPClient
from sievelib.managesieve import Client as SievelibClient

from mailctl.components.managesieve.client import SieveClient as ByteClient
from mailctl.config import Config, Secret

_Client = TypeVar("_Client", bound=SievelibClient)

__all__ = [
    "SCRATCH_MARKER",
    "WRITE_FLAG",
    "FolderNotRemoved",
    "FolderRefused",
    "Mailbox",
    "RestoreNotConfirmed",
    "ScratchFolder",
    "ScriptGuard",
    "ScriptGuardError",
    "claim_scratch_folder",
    "guard_scripts",
    "guarded_scripts",
    "scratch_folder",
    "write_gate_reason",
]

# Every scratch folder's last component starts with this, so a folder the
# guard did not make can never pass for one it did.
SCRATCH_MARKER = "mailctl-test-"

# The second opt-in a live test needs before it may write; see
# ``write_gate_reason``.
WRITE_FLAG = "MAILCTL_LIVE_WRITE"
WRITE_VALUE = "1"

# The one IMAP port that means STARTTLS, as mailctl's own MXroute provider
# reads it (providers/mxroute/imap.py); every other port is implicit TLS.
STARTTLS_PORT = 143


# ############################################################################
# The write gate
# ############################################################################


# ----------------------------------------------------------------------------
def write_gate_reason(environ) -> str | None:
    """Why a live test may not write, or None if it may.

    ``MAILCTL_LIVE_WRITE`` must equal exactly ``"1"`` -- ``true``, ``yes``
    and ``0`` all refuse -- on top of the live tier's own gate, so a run
    meant only to read can never write by accident.
    """
    if environ.get(WRITE_FLAG) != WRITE_VALUE:
        return (
            f"live writes are off: set {WRITE_FLAG}={WRITE_VALUE} as well "
            f"as MAILCTL_LIVE=1 to let a test write to the account"
        )

    return None


# ############################################################################
# The connection
# ############################################################################


@dataclass(frozen=True)
class Mailbox:
    """Where a guard connects, and as whom.

    ``sieve_tls`` is ``starttls``, ``ssl``, or ``none``; ``imap_tls`` is
    ``ssl`` or ``starttls``. ``password`` is called only as a login is
    made, and returns a ``Secret``.
    """

    sieve_host: str
    sieve_port: int
    sieve_tls: str
    imap_host: str
    imap_port: int
    imap_tls: str
    user: str
    password: Callable[[], Secret] = field(repr=False)

    # ------------------------------------------------------------------------
    @classmethod
    def from_config(cls, config: Config) -> "Mailbox":
        """The mailbox mailctl itself would reach with ``config``."""
        config.require("host", "imap_host", "user")

        return cls(
            sieve_host=config.host,
            sieve_port=config.sieve_port,
            sieve_tls=config.sieve_tls,
            imap_host=config.imap_host,
            imap_port=config.imap_port,
            imap_tls="starttls"
            if config.imap_port == STARTTLS_PORT
            else "ssl",
            user=config.user,
            password=config.password,
        )


# ----------------------------------------------------------------------------
@contextmanager
def _sieve(mailbox: Mailbox, client_class: type[_Client]) -> Iterator[_Client]:
    """A logged-in ManageSieve client of ``client_class``, logged out after."""
    client = client_class(mailbox.sieve_host, mailbox.sieve_port)

    if not client.connect(
        mailbox.user,
        mailbox.password().reveal(),
        starttls=mailbox.sieve_tls == "starttls",
        ssl=mailbox.sieve_tls == "ssl",
    ):
        raise ScriptGuardError(
            f"ManageSieve login as {mailbox.user!r} on "
            f"{mailbox.sieve_host}:{mailbox.sieve_port} was refused"
        )

    try:
        yield client

    finally:
        client.logout()


# ----------------------------------------------------------------------------
@contextmanager
def _imap(mailbox: Mailbox) -> Iterator[IMAPClient]:
    """A logged-in IMAPClient, logged out after."""
    context = ssl.create_default_context()
    implicit = mailbox.imap_tls == "ssl"

    client = IMAPClient(
        mailbox.imap_host,
        port=mailbox.imap_port,
        ssl=implicit,
        ssl_context=context if implicit else None,
    )

    if not implicit:
        client.starttls(context)

    client.login(mailbox.user, mailbox.password().reveal())

    try:
        yield client

    finally:
        client.logout()


# ############################################################################
# The active script
# ############################################################################


class ScriptGuardError(Exception):
    """The guard could not start, so the test must not run."""


class RestoreNotConfirmed(AssertionError):
    """The scripts were not seen back as they were captured.

    An ``AssertionError`` so pytest reports it as a failure of the test's
    teardown, in red, rather than as a crash in the harness.
    """


@dataclass
class ScriptGuard:
    """What was captured, and where the saved copy is.

    ``scripts`` maps each script's name to its exact bytes; ``views`` maps
    it to sievelib's own reading of it, the second oracle. ``writes`` is
    what the last restore sent, one line per command.
    """

    mailbox: Mailbox
    active: str | None
    scripts: dict[str, bytes]
    views: dict[str, str]
    saved: Path
    writes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------------
    def _put(self, client: SievelibClient, name: str, content: str) -> bool:
        """PUTSCRIPT through sievelib; one seam, so a test can break it."""
        return client.putscript(name, content)

    # ------------------------------------------------------------------------
    def restore(self) -> None:
        """Put back what changed, then confirm it.

        Only what differs is written, in this order: a captured script that
        changed or went missing is put back; the captured active script is
        made active again (or none, if none was); a script the test created
        is deleted, which RFC 5804 forbids while it is active -- hence
        after the activation.
        """
        now_active, now = self._read_bytes()
        done = self.writes = []

        with _sieve(self.mailbox, SievelibClient) as client:
            for name, content in self.scripts.items():
                if now.get(name) != content:
                    if not self._put(client, name, content.decode("utf-8")):
                        self._fail([f"PUTSCRIPT {name!r} was refused"])

                    done.append(f"put back {name!r}")

            if now_active != self.active:
                if not client.setactive(self.active or ""):
                    self._fail(
                        [f"SETACTIVE {self.active or ''!r} was refused"]
                    )

                done.append(f"activated {self.active!r}")

            for name in sorted(set(now) - set(self.scripts)):
                if not client.deletescript(name):
                    self._fail([f"DELETESCRIPT {name!r} was refused"])

                done.append(f"deleted {name!r}")

        self.confirm()

    # ------------------------------------------------------------------------
    def confirm(self) -> None:
        """Raise :class:`RestoreNotConfirmed` unless both oracles agree."""
        problems = []
        active, scripts = self._read_bytes()

        if active != self.active:
            problems.append(
                f"active script is {active!r}, captured {self.active!r}"
            )

        if set(scripts) != set(self.scripts):
            problems.append(
                f"scripts are {sorted(scripts)}, captured "
                f"{sorted(self.scripts)}"
            )

        for name, content in self.scripts.items():
            if name in scripts and scripts[name] != content:
                problems.append(
                    f"{name!r} holds {_describe(scripts[name])}, captured "
                    f"{_describe(content)}"
                )

        view_active, views = self._read_views()

        if view_active != self.active or views != self.views:
            problems.append(
                "sievelib's own GETSCRIPT does not read back what it read "
                "at capture"
            )

        if problems:
            self._fail(problems)

    # ------------------------------------------------------------------------
    def _fail(self, problems: list[str]) -> None:
        raise RestoreNotConfirmed(
            f"the Sieve scripts of {self.mailbox.user!r} could not be "
            f"confirmed restored: {'; '.join(problems)}. The captured copy "
            f"is in {self.saved} (active: {self.active!r}); put it back with "
            f"'mailctl filterset restore FILE' before doing anything else."
        )

    # ------------------------------------------------------------------------
    def _read_bytes(self) -> tuple[str | None, dict[str, bytes]]:
        return _read_bytes(self.mailbox)

    # ------------------------------------------------------------------------
    def _read_views(self) -> tuple[str | None, dict[str, str]]:
        return _read_views(self.mailbox)


# ----------------------------------------------------------------------------
def _listing(client: SievelibClient) -> tuple[str | None, list[str]]:
    """LISTSCRIPTS as (active, every name), the active one included."""
    result = client.listscripts()

    if result is None:
        raise ScriptGuardError("LISTSCRIPTS was refused")

    active, others = result
    names = [name for name in others if name]

    if active:
        names.append(active)

    return active or None, names


# ----------------------------------------------------------------------------
def _read_bytes(mailbox: Mailbox) -> tuple[str | None, dict[str, bytes]]:
    """Every script's exact bytes, through the byte-exact reader."""
    with _sieve(mailbox, ByteClient) as client:
        active, names = _listing(client)
        scripts = {}

        for name in names:
            content = client.getscript_bytes(name)

            if content is None:
                raise ScriptGuardError(f"GETSCRIPT {name!r} was refused")

            scripts[name] = content

    return active, scripts


# ----------------------------------------------------------------------------
def _read_views(mailbox: Mailbox) -> tuple[str | None, dict[str, str]]:
    """Every script as sievelib's own GETSCRIPT reads it."""
    with _sieve(mailbox, SievelibClient) as client:
        active, names = _listing(client)

        return active, {name: client.getscript(name) for name in names}


# ----------------------------------------------------------------------------
def _describe(content: bytes) -> str:
    """A script's length and a hash prefix: enough to tell two apart."""
    digest = hashlib.sha256(content).hexdigest()[:12]

    return f"{len(content)} bytes (sha256 {digest})"


# ----------------------------------------------------------------------------
def _save(scripts: dict[str, bytes], active: str | None, where: Path) -> Path:
    """Write each script's exact bytes to ``where``, owner-only.

    The file names are numbered because a script name need not be a safe
    file name; ``MANIFEST`` says which is which and which was active.
    """
    where.mkdir(mode=0o700, parents=True, exist_ok=True)
    manifest = [f"active: {active!r}"]

    for index, (name, content) in enumerate(sorted(scripts.items())):
        path = where / f"{index:02d}.sieve"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)

        manifest.append(f"{path.name}: {name!r}")

    (where / "MANIFEST").write_text("\n".join(manifest) + "\n")

    return where


# ----------------------------------------------------------------------------
@contextmanager
def guard_scripts(mailbox: Mailbox, saved: Path) -> Iterator[ScriptGuard]:
    """Capture every script, run the block, then restore and confirm.

    The restore runs however the block ends -- normally, by an exception,
    or by ``KeyboardInterrupt`` -- because it is a ``finally``. A capture
    that fails raises before the block runs, so nothing is written.

    Only UTF-8 scripts can be put back byte for byte through sievelib's
    ``putscript``, which takes text; anything else is refused up front.
    """
    active, scripts = _read_bytes(mailbox)

    for name, content in scripts.items():
        try:
            content.decode("utf-8")

        except UnicodeDecodeError as exc:
            raise ScriptGuardError(
                f"script {name!r} is not UTF-8, so it could not be put back "
                f"byte for byte; not starting"
            ) from exc

    view_active, views = _read_views(mailbox)

    if view_active != active or set(views) != set(scripts):
        raise ScriptGuardError(
            "the two readers disagree about which scripts exist; not starting"
        )

    guard = ScriptGuard(
        mailbox, active, scripts, views, _save(scripts, active, saved)
    )

    try:
        yield guard

    finally:
        guard.restore()


# ############################################################################
# The scratch folder
# ############################################################################


class FolderRefused(Exception):
    """The folder asked for is not one the guard may claim."""


class FolderNotRemoved(AssertionError):
    """The scratch folder was still listed after it was deleted."""


@dataclass(frozen=True)
class ScratchFolder:
    """A folder this test made, and the one place it may put mail."""

    mailbox: Mailbox
    name: str
    delimiter: str

    # ------------------------------------------------------------------------
    def append(self, message: bytes, flags=(), when=None) -> int | None:
        """APPEND ``message`` here; return its UID where UIDPLUS gives one.

        ``when`` is its internal date, the one IMAP's SINCE and BEFORE
        compare, or None for now.
        """
        # IMAPClient is unannotated; with unpack=True, append() returns
        # the server's one response line.
        with _imap(self.mailbox) as client:
            response = cast(
                bytes,
                client.append(self.name, message, flags=flags, msg_time=when),
            )

        # UIDPLUS: "[APPENDUID <validity> <uid>] Append completed."
        if not response.startswith(b"[APPENDUID"):
            return None

        return int(response.split(b"]")[0].split()[-1])


# ----------------------------------------------------------------------------
def _personal_namespace(client: IMAPClient) -> tuple[str, str]:
    """The personal namespace's prefix and delimiter, read off the server."""
    if client.has_capability("NAMESPACE"):
        personal = client.namespace().personal

        if personal:
            prefix, delimiter = personal[0]

            return prefix, delimiter

    ((_flags, delimiter, _name),) = client.list_folders("", "")

    return "", delimiter.decode() if isinstance(
        delimiter, bytes
    ) else delimiter


# ----------------------------------------------------------------------------
def _refusal(name: str, delimiter: str, existing: set[str]) -> str | None:
    """Why ``name`` may not be claimed, or None if it may.

    Three checks, in order, and each can be seen to fire on its own: INBOX
    in any case, any folder already on the server, and a name the guard did
    not make.
    """
    if name.upper() == "INBOX":
        return "INBOX is never a scratch folder"

    if name in existing:
        return f"{name!r} already exists, so it is not this test's to delete"

    if not name.rsplit(delimiter, 1)[-1].startswith(SCRATCH_MARKER):
        return f"{name!r} does not start with {SCRATCH_MARKER!r}"

    return None


# ----------------------------------------------------------------------------
@contextmanager
def claim_scratch_folder(
    mailbox: Mailbox, name: str | None = None
) -> Iterator[ScratchFolder]:
    """Create a new folder for the block, then delete it and its contents.

    ``name`` is for proving the refusals; a test leaves it None and gets
    ``SCRATCH_MARKER`` plus a random suffix in the personal namespace. A
    refused name raises :class:`FolderRefused` before anything is written.

    The deletion takes the folder's own children first, deepest first,
    drops their subscriptions, and is then confirmed by listing: a folder
    still there raises :class:`FolderNotRemoved`.
    """
    with _imap(mailbox) as client:
        prefix, delimiter = _personal_namespace(client)
        existing = {entry[2] for entry in client.list_folders()}

        if name is None:
            name = f"{prefix}{SCRATCH_MARKER}{uuid.uuid4().hex[:12]}"

        reason = _refusal(name, delimiter, existing)

        if reason is not None:
            raise FolderRefused(reason)

        client.create_folder(name)

    try:
        yield ScratchFolder(mailbox, name, delimiter)

    finally:
        _remove(mailbox, name, delimiter)


# ----------------------------------------------------------------------------
def _remove(mailbox: Mailbox, name: str, delimiter: str) -> None:
    """Delete ``name`` and everything under it, then confirm it is gone."""
    with _imap(mailbox) as client:
        ours = [name] + [
            entry[2] for entry in client.list_folders(name + delimiter, "*")
        ]
        subscribed = {entry[2] for entry in client.list_sub_folders()}

        for folder in sorted(
            ours, key=lambda f: f.count(delimiter), reverse=True
        ):
            if folder in subscribed:
                client.unsubscribe_folder(folder)

            client.delete_folder(folder)

        left = [entry[2] for entry in client.list_folders(name)]

    if left:
        raise FolderNotRemoved(
            f"scratch folder {name!r} is still listed after DELETE"
        )


# ############################################################################
# The fixtures
# ############################################################################

# A tier makes these available by importing them into its conftest and
# providing a ``write_mailbox`` fixture returning a :class:`Mailbox`. The
# live tier's is gated on a second opt-in (``tests/live/conftest.py``).


# ----------------------------------------------------------------------------
@pytest.fixture
def guarded_scripts(write_mailbox, tmp_path) -> Iterator[ScriptGuard]:
    """Every Sieve script captured before the test and restored after."""
    with guard_scripts(write_mailbox, tmp_path / "sieve-backup") as guard:
        yield guard


# ----------------------------------------------------------------------------
@pytest.fixture
def scratch_folder(write_mailbox) -> Iterator[ScratchFolder]:
    """A new folder for this test's mail, deleted with it afterwards."""
    with claim_scratch_folder(write_mailbox) as folder:
        yield folder
