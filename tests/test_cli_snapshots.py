"""Whole-command snapshots: what each CLI invocation prints and does.

Every scenario runs ``cli.main`` end to end against a ManageSieve fake and
the conftest IMAP double, then records the exit code, stdout, stderr, and
the exact calls each server was asked to make -- including the script that
was uploaded. The record is compared with ``tests/snapshots/cli/<name>.txt``.

This is the guard for the engine/front-end split: a refactor that changes
nothing a user sees leaves every snapshot untouched, and a deliberate
behaviour change shows up as a reviewable snapshot diff in the same commit.

Regenerate after an intended change with::

    MAILCTL_UPDATE_SNAPSHOTS=1 pytest tests/test_cli_snapshots.py

and read the diff before committing it.
"""

import io
import os
import re
import sys
from pathlib import Path

import pytest

from mailctl import cli
from mailctl.components.managesieve import client as sieve_client

SNAPSHOTS = Path(__file__).parent / "snapshots" / "cli"


# ----------------------------------------------------------------------------
def update_requested(environ) -> bool:
    """Whether to rewrite the snapshots rather than check them.

    Refused under CI: a run that rewrites every snapshot then compares it
    with itself passes whatever the output is, so a CI job inheriting the
    variable would stop checking anything while staying green.
    """
    if environ.get("MAILCTL_UPDATE_SNAPSHOTS") != "1":
        return False

    if environ.get("CI"):
        pytest.fail(
            "MAILCTL_UPDATE_SNAPSHOTS=1 under CI would rewrite every "
            "snapshot and pass unconditionally; unset it",
            pytrace=False,
        )

    return True


FULL = ["fileinto", "imap4flags", "mailbox", "regex"]
NO_MAILBOX = ["fileinto", "imap4flags"]
GITHUB = ["--from", "noreply@github.com"]

# ############################################################################
# Fakes
# ############################################################################


class FakeSieveClient:
    """A stand-in for ``SieveClient`` with a script store."""

    # ------------------------------------------------------------------------
    def __init__(self, caps, active, script, reject, others=None, stray=0):
        self.caps = caps
        self.stray = stray
        self.scripts = {} if active is None else {active: script}
        self.scripts.update(others or {})
        self.active = active
        self.reject = reject
        self.calls: list[tuple] = []

    # ------------------------------------------------------------------------
    def connect(self, user, password, starttls=False, ssl=False):
        self.calls.append(("connect",))

        return True

    # ------------------------------------------------------------------------
    def logout(self):
        self.calls.append(("logout",))

    # ------------------------------------------------------------------------
    def listscripts(self):
        self.calls.append(("listscripts",))

        others = sorted(name for name in self.scripts if name != self.active)

        # sievelib's reading of a stray line break in the listing (#119).
        return (self.active, others + [""] * self.stray)

    # ------------------------------------------------------------------------
    def getscript_bytes(self, name):
        self.calls.append(("getscript", name))

        script = self.scripts.get(name)

        return None if script is None else script.encode("utf-8")

    # ------------------------------------------------------------------------
    @property
    def capability_response(self) -> bytes:
        return b'"SIEVE" "%s"\r\n' % " ".join(self.caps).encode()

    # ------------------------------------------------------------------------
    def checkscript(self, content):
        self.calls.append(("checkscript",))

        return not self.reject

    # ------------------------------------------------------------------------
    def putscript(self, name, content):
        self.calls.append(("putscript", name, content))
        self.scripts[name] = content

        return True

    # ------------------------------------------------------------------------
    def setactive(self, name):
        self.calls.append(("setactive", name))
        self.active = name

        return True


# ----------------------------------------------------------------------------
def message(sender: str, subject: str, list_id: str | None = None) -> bytes:
    lines = [
        f"From: Someone <{sender}>",
        "To: user@example.com",
        f"Subject: {subject}",
        "Date: Tue, 3 Feb 2026 04:05:06 +0000",
    ]

    if list_id:
        lines.append(f"List-Id: Some list <{list_id}>")

    return ("\r\n".join(lines) + "\r\n\r\n").encode()


# ############################################################################
# Scenarios
# ############################################################################

# name -> (argv, options). Options: caps, active, script, reject, others
# (further stored scripts, by name), config
# (the text of config.toml), file (the text of a file that "<FILE>" in
# argv is replaced with the path of; mode 0600 unless file_mode), env
# (the text of a .env written, mode 0600, into the directory the command
# runs in), mail / flags
# (extra messages and their IMAP flags, by UID), and stray (how many empty
# names the script listing carries).

# Host from the env file over the exported MAILCTL_HOST, port from a flag
# over the env file, TLS from the config file, and the password named
# indirectly -- one rung each, so the report has to tell them apart.
ENV_FILE = """# written by hand
export MAILCTL_HOST=mail.from-env-file.example
MAILCTL_SIEVE_PORT='4191'
MAILCTL_PASSWORD_CMD="printf %s not-a-real-password"
OTHER_TOOL=ignored
"""

# A narrow rule ahead of a broad one that covers it. Moving the broad one
# first is the move that starves the narrow one.
NARROW_THEN_BROAD = """require ["fileinto"];
# rule:[announce]
if header :contains "to" "announce@lists.example.com"
{
\tfileinto "INBOX.Announce";
\tstop;
}
# rule:[all-lists]
if header :contains "to" "@lists.example.com"
{
\tfileinto "INBOX.Lists";
\tstop;
}
"""

# The Roundcube script with its second rule gone -- a backup taken before
# that rule was added.
ONE_RULE = """require ["fileinto","imap4flags"];
# rule:[keep-boss]
if header :contains "from" "boss@example.com"
{
\tfileinto "INBOX.Boss";
\tsetflag "\\\\Flagged";
\tstop;
}
"""

# The fixture script with keep-boss switched off in Roundcube, CRLF as the
# server sends it: `if false # <its test>`, the body kept (#158).
DISABLED_BOSS = """require ["fileinto","imap4flags"];
# rule:[keep-boss]
if false # allof (header :contains "from" "boss@example.com")
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
""".replace("\n", "\r\n")

# The same rule off with nothing kept after `if false`: nothing to restore.
BARE_FALSE = DISABLED_BOSS.replace(
    ' # allof (header :contains "from" "boss@example.com")', ""
)

# A message worth reading: a plain part beside its HTML twin, and a PDF.
REPORT = (
    b"From: Boss <boss@example.com>\r\n"
    b"To: user@example.com\r\n"
    b"Subject: =?utf-8?q?Q3_report_=E2=80=94_draft?=\r\n"
    b"Date: Wed, 4 Feb 2026 09:00:00 +0000\r\n"
    b"MIME-Version: 1.0\r\n"
    b'Content-Type: multipart/mixed; boundary="outer"\r\n'
    b"\r\n"
    b"--outer\r\n"
    b'Content-Type: multipart/alternative; boundary="alt"\r\n'
    b"\r\n"
    b"--alt\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Numbers attached.\r\n"
    b"\r\n"
    b"-- Boss\r\n"
    b"--alt\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n"
    b"\r\n"
    b"<p>Numbers attached.</p>\r\n"
    b"--alt--\r\n"
    b"--outer\r\n"
    b"Content-Type: application/pdf\r\n"
    b'Content-Disposition: attachment; filename="q3.pdf"\r\n'
    b"Content-Transfer-Encoding: base64\r\n"
    b"\r\n"
    b"JVBERi0xLjQK\r\n"
    b"--outer--\r\n"
)

NEWSLETTER = (
    b"From: News <news@example.com>\r\n"
    b"Subject: Weekly\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n"
    b"\r\n"
    b"<h1>This week</h1><p>One &amp; two.</p>\r\n"
)

MAIL = {"mail": {4: REPORT, 5: NEWSLETTER}, "flags": {1: (b"\\Seen",)}}

# A bidi override reversing the tail of a Subject and an attachment name.
SPOOFED = (
    b"From: m@example.com\r\n"
    b"Subject: Pay =?utf-8?q?=E2=80=AEtoday?=\r\n"
    b'Content-Type: multipart/mixed; boundary="b"\r\n'
    b"\r\n"
    b"--b\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"see attached\r\n"
    b"--b\r\n"
    b"Content-Type: application/octet-stream\r\n"
    b"Content-Disposition: attachment;\r\n"
    b" filename*=utf-8''invoice_%E2%80%AEfdp.exe\r\n"
    b"\r\n"
    b"payload\r\n"
    b"--b--\r\n"
)

# Message- and script-derived text carrying terminal escapes: an OSC title
# change in a Subject that 'from-message --derive subject' copies into the
# rule and 'search --like --build-filter' into the filter, and colour
# sequences in a stored script's rule name, test, and folder, which 'show'
# and 'rules' print back.
HOSTILE_SUBJECT = (
    b"From: m@example.com\r\n"
    b"Subject: =?utf-8?q?Inv=1B]0;pwn=07oice?=\r\n"
    b"\r\n"
    b"b\r\n"
)

HOSTILE_SCRIPT = """require ["fileinto"];
# rule:[bad\x1b[31mname]
if header :contains "subject" "x\x1b]0;pwn\x07y"
{
\tfileinto "INBOX.\x1b[32mZ";
\tstop;
}
"""

HOSTILE = {
    "from-hostile": (
        [
            "from-message",
            "--uid",
            "9",
            "--derive",
            "subject",
            "--fileinto",
            "Lists",
            "--dry-run",
        ],
        {"mail": {9: HOSTILE_SUBJECT}},
    ),
    "search-like-hostile": (
        ["search", "--like", "9", "--derive", "subject", "--build-filter"],
        {"mail": {9: HOSTILE_SUBJECT}},
    ),
    "search-like-hostile-json": (
        [
            "search",
            "--like",
            "9",
            "--derive",
            "subject",
            "--build-filter",
            "--json",
        ],
        {"mail": {9: HOSTILE_SUBJECT}},
    ),
    "show-hostile": (["show"], {"script": HOSTILE_SCRIPT}),
    "rules-hostile": (["rules"], {"script": HOSTILE_SCRIPT}),
}

SCENARIOS = {
    **HOSTILE,
    "search": (["search"], MAIL),
    "search-from": (["search", *GITHUB], MAIL),
    "search-limit": (["search", "--limit", "2"], MAIL),
    "search-raw": (["search", "--raw", "UNSEEN"], MAIL),
    "search-both": (["search", *GITHUB, "--raw", "ALL"], MAIL),
    "search-none": (["search", "--from", "nobody@x.y"], MAIL),
    "search-folder": (["search", "--folder", "Lists"], MAIL),
    # #148: --like pre-fills the criteria from a message; a flag replaces
    # what was derived for its header, and --build-filter saves nothing.
    "search-like": (["search", "--like", "3"], {}),
    "search-like-combined": (
        ["search", "--like", "2", "--subject", "Issue", "--match", "all"],
        {},
    ),
    "search-like-override": (
        ["search", "--like", "3", "--list-id", "other.example.com"],
        {},
    ),
    "search-like-missing": (["search", "--like", "99"], {}),
    "search-like-derive-missing": (
        ["search", "--like", "2", "--derive", "cc"],
        {},
    ),
    "search-like-raw": (["search", "--like", "3", "--raw", "ALL"], {}),
    "search-derive-alone": (["search", "--derive", "from"], {}),
    "search-build-filter": (["search", "--build-filter", *GITHUB], {}),
    "search-build-filter-json": (
        ["search", "--build-filter", "--json", *GITHUB],
        {},
    ),
    "search-like-build-filter-json": (
        ["search", "--like", "3", "--build-filter", "--json"],
        {},
    ),
    "search-build-filter-nothing": (["search", "--build-filter"], {}),
    "search-json-alone": (["search", "--json", *GITHUB], {}),
    # #63: source_folder in the config file is where --folder defaults to.
    "search-config-folder": (
        ["search"],
        {**MAIL, "config": 'source_folder = "Lists"\n'},
    ),
    "apply-config-folder": (
        ["apply", *GITHUB, "--fileinto", "spam", "--dry-run"],
        {"config": 'source_folder = "Lists"\n'},
    ),
    "test-config-folder": (
        ["test"],
        {"config": 'source_folder = "Lists"\n'},
    ),
    "view": (["view", "4"], MAIL),
    "view-html": (["view", "5"], MAIL),
    "view-headers": (["view", "4", "--headers-only"], MAIL),
    "view-raw": (["view", "5", "--raw"], {**MAIL, "tty": True}),
    "view-raw-pipe": (["view", "5", "--raw"], MAIL),
    "view-bidi": (["view", "6"], {"mail": {6: SPOOFED}}),
    "view-missing": (["view", "99"], MAIL),
    "list": (["list"], {}),
    "list-verbose": (["list", "--verbose"], {}),
    # One script, and a listing sievelib read an empty name out of (#119).
    "list-stray-line": (["list"], {"stray": 1}),
    "test-stray-line": (["test"], {"stray": 1}),
    "show": (["show"], {}),
    "rules": (["rules"], {}),
    "folders": (["folders"], {}),
    "test": (["test"], {}),
    "test-verbose": (["test", "-v"], {}),
    "test-env-file": (
        ["test", "--env-file", "--sieve-port", "4192"],
        {"env": ENV_FILE, "config": 'sieve_tls = "ssl"\n'},
    ),
    "test-env-file-missing": (["test", "--env-file", "nowhere.env"], {}),
    # #61: the Password line reports the outcome of reading it, so a
    # refused file is not shown as "set" above its own refusal.
    "test-password-refused": (
        ["test", "--password-file", "<FILE>"],
        {"file": "not-a-real-password\n", "file_mode": 0o644},
    ),
    "test-password-file": (
        ["test", "--password-file", "<FILE>"],
        {"file": "not-a-real-password\n"},
    ),
    # #50: backup_dir expands $VAR / ${VAR} and ~, like any path setting.
    "backup-dir-expanded": (
        ["backup"],
        {"config": 'backup_dir = "${XDG_CONFIG_HOME}/elsewhere"\n'},
    ),
    "backup-dry": (["backup", "--dry-run"], {}),
    "backup-noactive": (["backup"], {"active": None}),
    "subscribe": (["subscribe", "spam"], {}),
    "subscribe-dry": (["subscribe", "INBOX.spam", "--dry-run"], {}),
    "subscribe-already": (["subscribe", "Lists"], {}),
    "subscribe-missing": (["subscribe", "Nowhere"], {}),
    "unsubscribe": (["unsubscribe", "Lists"], {}),
    "unsubscribe-already": (["unsubscribe", "spam"], {}),
    "unsubscribe-missing": (["unsubscribe", "Nowhere"], {}),
    # #155: a folder made on its own, not only by --create-folder.
    "create-folder-dry": (["create-folder", "Lists/GitHub", "--dry-run"], {}),
    "create-folder-yes": (["create-folder", "Lists/GitHub", "--yes"], {}),
    "create-folder-nosub": (
        ["create-folder", "New", "--no-subscribe", "--yes"],
        {},
    ),
    "create-folder-notty": (["create-folder", "New"], {}),
    "create-folder-parents": (
        ["create-folder", "Work/2026/Q3", "--dry-run"],
        {},
    ),
    "create-folder-parent-nosub": (
        ["create-folder", "Work/2026", "--no-subscribe", "--dry-run"],
        {},
    ),
    "create-folder-exists": (["create-folder", "Lists"], {}),
    "create-folder-exists-nosub": (
        ["create-folder", "Lists", "--no-subscribe"],
        {},
    ),
    "create-folder-unsubscribed": (["create-folder", "spam"], {}),
    "create-folder-case-variant": (["create-folder", "lists", "--yes"], {}),
    "add-nosubscribe-alone": (
        ["add", *GITHUB, "--fileinto", "Lists", "--no-subscribe"],
        {},
    ),
    "move-dry": (["move-rule", "bin-the-noise", "--first", "--dry-run"], {}),
    "move-yes": (["move-rule", "bin-the-noise", "--first", "--yes"], {}),
    "move-notty": (["move-rule", "keep-boss", "--last"], {}),
    "move-noop": (["move-rule", "keep-boss", "--first"], {}),
    "move-unknown": (["move-rule", "phantom", "--first"], {}),
    "move-anchor-unknown": (
        ["move-rule", "keep-boss", "--after", "phantom"],
        {},
    ),
    "move-self-anchor": (
        ["move-rule", "keep-boss", "--before", "keep-boss"],
        {},
    ),
    "move-no-position": (["move-rule", "keep-boss"], {}),
    "move-starves": (
        ["move-rule", "all-lists", "--first", "--dry-run"],
        {"script": NARROW_THEN_BROAD},
    ),
    "restore-dry": (["restore", "<FILE>", "--dry-run"], {"file": ONE_RULE}),
    "restore-yes": (["restore", "<FILE>", "--yes"], {"file": ONE_RULE}),
    "restore-notty": (["restore", "<FILE>"], {"file": ONE_RULE}),
    "restore-identical": (["restore", "<FILE>", "--yes"], {"file": "SAME"}),
    "restore-over-unparseable": (
        ["restore", "<FILE>", "--yes"],
        {"file": ONE_RULE, "script": "if {{{ broken\n"},
    ),
    "restore-rejected": (
        ["restore", "<FILE>", "--yes"],
        {"file": ONE_RULE, "reject": True},
    ),
    "restore-missing-file": (["restore", "/nonexistent/x.sieve"], {}),
    # #52: a named target, and an empty file refused unless asked for.
    "restore-script": (
        ["restore", "<FILE>", "--script", "spare", "--yes"],
        {"file": ONE_RULE, "others": {"spare": "# nothing yet\n"}},
    ),
    # #54: with nothing active a bare restore names --script; naming the
    # deactivated script is the recovery, and activates it.
    "restore-noactive": (
        ["restore", "<FILE>", "--yes"],
        {"file": ONE_RULE, "active": None, "others": {"managesieve": "x\n"}},
    ),
    "restore-noactive-script": (
        ["restore", "<FILE>", "--script", "managesieve", "--yes"],
        {"file": ONE_RULE, "active": None, "others": {"managesieve": "x\n"}},
    ),
    "restore-empty": (["restore", "<FILE>", "--yes"], {"file": "\n"}),
    "restore-empty-allowed": (
        ["restore", "<FILE>", "--allow-empty", "--dry-run"],
        {"file": ""},
    ),
    "add-dry-missing": (
        ["add", *GITHUB, "--fileinto", "Lists/GitHub", "--dry-run"],
        {},
    ),
    "add-dry-sievecreate": (
        [
            "add",
            *GITHUB,
            "--fileinto",
            "Lists/GitHub",
            "--create-folder",
            "--dry-run",
        ],
        {},
    ),
    "add-dry-sievecreate-nosub": (
        [
            "add",
            *GITHUB,
            "--fileinto",
            "Lists/GitHub",
            "--create-folder",
            "--no-subscribe",
            "--dry-run",
        ],
        {},
    ),
    "add-real-sievecreate": (
        [
            "add",
            *GITHUB,
            "--fileinto",
            "Lists/GitHub",
            "--create-folder",
            "--yes",
        ],
        {},
    ),
    "add-noimap-sievecreate": (
        [
            "add",
            *GITHUB,
            "--fileinto",
            "Lists/GitHub",
            "--create-folder",
            "--no-imap",
            "--yes",
        ],
        {},
    ),
    "add-dry-imapcreate": (
        [
            "add",
            *GITHUB,
            "--fileinto",
            "Lists/GitHub",
            "--create-folder",
            "--dry-run",
        ],
        {"caps": NO_MAILBOX},
    ),
    "add-real-imapcreate": (
        [
            "add",
            *GITHUB,
            "--fileinto",
            "Lists/GitHub",
            "--create-folder",
            "--yes",
            "--mark-read",
        ],
        {"caps": NO_MAILBOX},
    ),
    "add-real-imapcreate-verbose": (
        [
            "add",
            *GITHUB,
            "--fileinto",
            "Lists/GitHub",
            "--create-folder",
            "--yes",
            "-v",
        ],
        {"caps": NO_MAILBOX},
    ),
    "add-real-exists": (
        ["add", *GITHUB, "--fileinto", "Lists", "--yes", "--first"],
        {},
    ),
    "add-real-noconfirm": (["add", *GITHUB, "--fileinto", "Lists"], {}),
    "add-overcap": (
        [
            "add",
            *GITHUB,
            "--fileinto",
            "Lists",
            "--yes",
            "--max-messages",
            "1",
        ],
        {},
    ),
    "add-dry-overcap": (
        [
            "add",
            *GITHUB,
            "--fileinto",
            "Lists",
            "--dry-run",
            "--max-messages",
            "1",
        ],
        {},
    ),
    "add-noapply": (
        ["add", *GITHUB, "--fileinto", "Lists", "--no-apply"],
        {},
    ),
    "add-noimap": (
        [
            "add",
            *GITHUB,
            "--fileinto",
            "Lists/GitHub",
            "--no-imap",
            "--dry-run",
        ],
        {},
    ),
    "add-noimap-create-nomailbox": (
        ["add", *GITHUB, "--fileinto", "X", "--no-imap", "--create-folder"],
        {"caps": NO_MAILBOX},
    ),
    "add-noaction": (["add", *GITHUB], {}),
    "add-nocriteria": (["add", "--fileinto", "Lists"], {}),
    "add-redirect": (["add", *GITHUB, "--redirect", "x@y.z"], {}),
    "add-vacation": (["add", *GITHUB, "--vacation", "hi"], {}),
    "add-discard-yes": (["add", *GITHUB, "--discard", "--yes"], {}),
    "add-flag-only": (["add", *GITHUB, "--flag", "\\Flagged", "--yes"], {}),
    "add-keep-only": (["add", *GITHUB, "--keep", "--yes"], {}),
    "add-default-folder-extmissing": (
        ["add", *GITHUB, "--dry-run", "--no-apply"],
        {"caps": ["imap4flags"], "config": 'default_folder = "Lists"\n'},
    ),
    "add-fileinto-source-normalized": (
        [
            "add",
            *GITHUB,
            "--folder",
            "Lists",
            "--fileinto",
            "INBOX.Lists",
            "--yes",
        ],
        {},
    ),
    "add-fileinto-source": (
        ["add", *GITHUB, "--fileinto", "INBOX", "--yes"],
        {},
    ),
    "add-before-unknown": (
        ["add", *GITHUB, "--fileinto", "Lists", "--before", "phantom"],
        {},
    ),
    "add-replace-existing": (
        [
            "add",
            "--subject",
            "newsletter",
            "--fileinto",
            "Lists",
            "--name",
            "keep-boss",
            "--replace",
            "--first",
            "--dry-run",
        ],
        {},
    ),
    "add-dup-name": (
        ["add", *GITHUB, "--fileinto", "Lists", "--name", "keep-boss"],
        {},
    ),
    "add-rejected": (
        ["add", *GITHUB, "--fileinto", "Lists", "--no-apply"],
        {"reject": True},
    ),
    "add-empty-account": (
        ["add", *GITHUB, "--fileinto", "Lists", "--no-apply"],
        {"active": None},
    ),
    "add-extmissing": (
        ["add", *GITHUB, "--mark-read", "--fileinto", "Lists", "--dry-run"],
        {"caps": ["fileinto"]},
    ),
    "apply-yes": (["apply", *GITHUB, "--fileinto", "Lists", "--yes"], {}),
    "apply-dry": (["apply", *GITHUB, "--fileinto", "Lists", "--dry-run"], {}),
    "apply-missing": (["apply", *GITHUB, "--fileinto", "Lists/GitHub"], {}),
    # #56: 'lists' is not INBOX.Lists; the near miss is named, missing or
    # about to be created beside it.
    "apply-case-variant": (["apply", *GITHUB, "--fileinto", "lists"], {}),
    "add-dry-create-case-variant": (
        [
            "add",
            *GITHUB,
            "--fileinto",
            "lists",
            "--create-folder",
            "--dry-run",
        ],
        {},
    ),
    "apply-create": (
        [
            "apply",
            *GITHUB,
            "--fileinto",
            "Lists/GitHub",
            "--create-folder",
            "--yes",
        ],
        {},
    ),
    "apply-create-dry": (
        [
            "apply",
            *GITHUB,
            "--fileinto",
            "Lists/GitHub",
            "--create-folder",
            "--dry-run",
        ],
        {},
    ),
    "add-imapcreate-rejected": (
        [
            "add",
            *GITHUB,
            "--fileinto",
            "Lists/GitHub",
            "--create-folder",
            "--no-apply",
        ],
        {"caps": NO_MAILBOX, "reject": True},
    ),
    "apply-create-nomatch": (
        [
            "apply",
            "--from",
            "nobody@x.y",
            "--fileinto",
            "Lists/GitHub",
            "--create-folder",
            "--yes",
        ],
        {},
    ),
    "apply-nothing": (["apply", *GITHUB], {}),
    "apply-overcap": (
        [
            "apply",
            *GITHUB,
            "--fileinto",
            "Lists",
            "--max-messages",
            "1",
            "--yes",
        ],
        {},
    ),
    "apply-discard-notty": (["apply", *GITHUB, "--discard"], {}),
    "apply-nomatch": (
        ["apply", "--from", "nobody@x.y", "--fileinto", "Lists", "--yes"],
        {},
    ),
    "disable-yes": (["disable-rule", "keep-boss", "--yes"], {}),
    "disable-dry": (["disable-rule", "keep-boss", "--dry-run"], {}),
    "disable-notty": (["disable-rule", "keep-boss"], {}),
    "disable-unknown": (["disable-rule", "phantom", "--yes"], {}),
    "disable-already": (
        ["disable-rule", "keep-boss", "--yes"],
        {"script": DISABLED_BOSS},
    ),
    "disable-empty": (["disable-rule", "keep-boss", "--yes"], {"script": ""}),
    "enable-yes": (
        ["enable-rule", "keep-boss", "--yes"],
        {"script": DISABLED_BOSS},
    ),
    "enable-dry": (
        ["enable-rule", "keep-boss", "--dry-run"],
        {"script": DISABLED_BOSS},
    ),
    "enable-already": (["enable-rule", "keep-boss", "--yes"], {}),
    "enable-unknown": (
        ["enable-rule", "phantom", "--yes"],
        {"script": DISABLED_BOSS},
    ),
    "enable-no-test": (
        ["enable-rule", "keep-boss", "--yes"],
        {"script": BARE_FALSE},
    ),
    "rules-disabled": (["rules"], {"script": DISABLED_BOSS}),
    "remove-yes": (["remove-rule", "keep-boss", "--yes"], {}),
    "remove-dry": (["remove-rule", "keep-boss", "--dry-run"], {}),
    "remove-unknown": (["remove-rule", "phantom", "--yes"], {}),
    "remove-notty": (["remove-rule", "keep-boss"], {}),
    "remove-empty": (["remove-rule", "keep-boss", "--yes"], {"script": ""}),
    # Editing a stored script that is not the active one must not switch
    # which script the server runs (#53); --activate asks for exactly that.
    "remove-other-script": (
        ["remove-rule", "keep-boss", "--script", "spare", "--yes"],
        {"others": {"spare": ONE_RULE}},
    ),
    "remove-other-script-activate": (
        [
            "remove-rule",
            "keep-boss",
            "--script",
            "spare",
            "--activate",
            "--yes",
        ],
        {"others": {"spare": ONE_RULE}},
    ),
    "from-uid": (
        ["from-message", "--uid", "2", "--fileinto", "Lists", "--dry-run"],
        {},
    ),
    "from-uid-listid": (
        ["from-message", "--uid", "3", "--fileinto", "Lists", "--dry-run"],
        {},
    ),
    "from-search": (
        [
            "from-message",
            "--search",
            "ALL",
            "--fileinto",
            "Lists",
            "--dry-run",
        ],
        {},
    ),
    "from-derive-missing": (
        [
            "from-message",
            "--uid",
            "2",
            "--derive",
            "cc,list-id",
            "--fileinto",
            "Lists",
            "--dry-run",
        ],
        {},
    ),
    "from-nothing": (["from-message", "--fileinto", "Lists"], {}),
    # #82: disabled_extensions, from a flag and from the config file, and
    # what 'add' does with it -- refuse, or fall back to IMAP creation.
    "test-disabled": (
        [
            "test",
            "--disable-extension",
            "Mailbox",
            "--disable-extension",
            "copy",
        ],
        {},
    ),
    "test-disabled-config": (
        ["test"],
        {"config": 'disabled_extensions = ["imap4flags"]\n'},
    ),
    # #85: 'none' from the flag clears the config file's list, and says so.
    "test-disabled-none": (
        ["test", "--disable-extension", "none"],
        {"config": 'disabled_extensions = ["imap4flags"]\n'},
    ),
    "test-disabled-none-mixed": (
        ["test", "--disable-extension", "none", "--disable-extension", "copy"],
        {},
    ),
    "test-disabled-unknown": (["test", "--disable-extension", "mailbx"], {}),
    # A name only the server lists gets a row, folded to lower case once.
    "test-server-only": (
        ["test"],
        {"caps": ["FileInto", "fileinto", "Body", "imap4flags"]},
    ),
    "test-no-extensions": (["test"], {"caps": []}),
    "add-disabled-mailbox": (
        [
            "add",
            *GITHUB,
            "--fileinto",
            "Lists/GitHub",
            "--create-folder",
            "--disable-extension",
            "mailbox",
            "--yes",
        ],
        {},
    ),
    "add-disabled-mailbox-noimap": (
        [
            "add",
            *GITHUB,
            "--fileinto",
            "Lists/GitHub",
            "--create-folder",
            "--no-imap",
            "--disable-extension",
            "mailbox",
        ],
        {},
    ),
    "add-disabled-imap4flags": (
        [
            "add",
            *GITHUB,
            "--mark-read",
            "--fileinto",
            "Lists",
            "--disable-extension",
            "imap4flags",
        ],
        {},
    ),
}

# ############################################################################
# Running and recording
# ############################################################################


# ----------------------------------------------------------------------------
class Stdout(io.TextIOWrapper):
    """A captured stdout that is a terminal or a pipe, as asked.

    Backed by bytes, as a real one is, so a command writing to
    ``sys.stdout.buffer`` is recorded in order with its text output.
    """

    def __init__(self, tty: bool):
        super().__init__(io.BytesIO(), encoding="utf-8", newline="")
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty

    def text(self) -> str:
        self.flush()

        return self.buffer.getvalue().decode("utf-8")


# ----------------------------------------------------------------------------
def run_scenario(argv, options, imap, script, monkeypatch, tmp_path) -> str:
    """Run one invocation and render everything observable as text.

    ``imap`` is the conftest double, already patched in by ``fake_imap``.
    """
    sieve = FakeSieveClient(
        options.get("caps", FULL),
        options.get("active", "managesieve"),
        options.get("script", script),
        options.get("reject", False),
        options.get("others"),
        options.get("stray", 0),
    )

    imap.messages = {
        1: message("noreply@github.com", "PR opened"),
        2: message("noreply@github.com", "Issue closed"),
        3: message(
            "list@lists.example.com", "Digest", "dev.lists.example.com"
        ),
        **options.get("mail", {}),
    }
    imap.flags = options.get("flags", {})

    monkeypatch.setattr(sieve_client, "SieveClient", lambda *a, **k: sieve)
    if "file" in options:
        restore_file = tmp_path / "restore.sieve"
        text = script if options["file"] == "SAME" else options["file"]
        restore_file.write_text(text, encoding="utf-8")
        restore_file.chmod(options.get("file_mode", 0o600))
        argv = [str(restore_file) if arg == "<FILE>" else arg for arg in argv]

    if "env" in options:
        env_file = tmp_path / ".env"
        env_file.write_text(options["env"], encoding="utf-8")
        env_file.chmod(0o600)
        monkeypatch.chdir(tmp_path)

    if "config" in options:
        config_dir = Path(os.environ["XDG_CONFIG_HOME"]) / "mailctl"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.toml").write_text(options["config"])

    monkeypatch.setenv("MAILCTL_HOST", "mail.example.com")
    monkeypatch.setenv("MAILCTL_USER", "user@example.com")
    monkeypatch.setenv("MAILCTL_PASSWORD", "not-a-real-password")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    out, err = Stdout(tty=options.get("tty", False)), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    try:
        code = str(cli.main(argv))

    except SystemExit as exc:
        code = f"SystemExit({exc.code})"

    monkeypatch.undo()

    def scrub(text: str) -> str:
        text = text.replace(str(tmp_path), "<TMP>")

        # 'view --raw' keeps the source's CRLFs; shown as \r so the
        # snapshot file itself holds plain LF line endings.
        text = text.replace("\r", "\\r")

        return re.sub(r"\d{8}T\d{6}(\.\d+)?Z?", "<STAMP>", text)

    sections = [
        scrub(f"$ mailctl {' '.join(argv)}"),
        f"exit: {code}",
        "--- stdout",
        scrub(out.text()),
        "--- stderr",
        scrub(err.getvalue()),
        "--- sieve calls",
        *(repr(call) for call in sieve.calls),
        "--- imap calls",
        *(repr(call) for call in imap.calls),
    ]

    return "\n".join(sections).rstrip("\n") + "\n"


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_cli_output_matches_its_snapshot(
    name, fake_imap, roundcube_script, monkeypatch, tmp_path
):
    argv, options = SCENARIOS[name]
    snapshot = SNAPSHOTS / f"{name}.txt"

    actual = run_scenario(
        argv, options, fake_imap, roundcube_script, monkeypatch, tmp_path
    )

    if update_requested(os.environ):
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(actual, encoding="utf-8")

    assert snapshot.exists(), (
        f"no snapshot for {name!r}; run with MAILCTL_UPDATE_SNAPSHOTS=1"
    )
    assert actual == snapshot.read_text(encoding="utf-8")


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_hostile_text_reaches_the_terminal_escaped(
    name, fake_imap, roundcube_script, monkeypatch, tmp_path
):
    """What a sender or a stored script wrote is data, never a command to
    the terminal. The snapshot shows the escapes; this names the defect."""
    argv, options = SCENARIOS[name]

    actual = run_scenario(
        argv, options, fake_imap, roundcube_script, monkeypatch, tmp_path
    )

    output = actual.split("--- sieve calls")[0]

    assert "\x1b" not in output
    assert "\x07" not in output


# ----------------------------------------------------------------------------
def test_every_snapshot_belongs_to_a_scenario():
    """A renamed or dropped scenario must not leave a stale file behind."""
    names = {path.stem for path in SNAPSHOTS.glob("*.txt")}

    assert names, "no snapshots found -- the glob reads nothing"
    assert names == set(SCENARIOS)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        pytest.param({}, False, id="unset"),
        pytest.param({"CI": "true"}, False, id="ci-checking"),
        pytest.param({"MAILCTL_UPDATE_SNAPSHOTS": "1"}, True, id="local"),
    ],
)
def test_snapshot_rewriting_is_opt_in(environ, expected):
    assert update_requested(environ) is expected


# ----------------------------------------------------------------------------
def test_snapshot_rewriting_is_refused_under_ci():
    """#55: CI inheriting the variable would turn the tier into a no-op."""
    environ = {"MAILCTL_UPDATE_SNAPSHOTS": "1", "CI": "true"}

    with pytest.raises(pytest.fail.Exception, match="under CI"):
        update_requested(environ)
