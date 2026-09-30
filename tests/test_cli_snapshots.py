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

import contextlib
import io
import json
import os
import re
import sys
from pathlib import Path

import pytest
from imapclient.exceptions import IMAPClientError
from utilities_support import DEFAULT_UIDVALIDITY

from mailctl import __version__, cli, criteria, json_output
from mailctl.components.managesieve import client as sieve_client
from mailctl.components.managesieve.responses import ServerWarning
from mailctl.utilities import baseline, reports

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

# The filter document 'search --build-filter --json' prints for GITHUB.
FILTER_DOC = """{
  "version": 1,
  "criteria": {
    "match": "any",
    "compare": "contains",
    "terms": [
      {"header": "From", "value": "noreply@github.com"}
    ]
  }
}
"""

# ############################################################################
# Fakes
# ############################################################################


class FakeSieveClient:
    """A stand-in for ``SieveClient`` with a script store."""

    # ------------------------------------------------------------------------
    def __init__(
        self,
        caps,
        active,
        script,
        reject,
        others=None,
        stray=0,
        extra=b"",
        warnings=None,
    ):
        self.caps = caps
        self.extra = extra
        self.stray = stray
        self.scripts = {} if active is None else {active: script}
        self.scripts.update(others or {})
        self.active = active
        self.reject = reject
        self.calls: list[tuple] = []

        # By command, the WARNINGS text its OK carries, as SieveClient
        # leaves it in ``warning`` (#208).
        self.warnings = warnings or {}
        self.warning = None

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
        sieve = b'"SIEVE" "%s"\r\n' % " ".join(self.caps).encode()

        return self.extra + sieve

    # ------------------------------------------------------------------------
    def checkscript(self, content):
        self.calls.append(("checkscript",))
        self._warn("checkscript")

        return not self.reject

    # ------------------------------------------------------------------------
    def _warn(self, command):
        text = self.warnings.get(command)
        self.warning = None if text is None else ServerWarning(text)

    # ------------------------------------------------------------------------
    def putscript(self, name, content):
        self.calls.append(("putscript", name, content))
        self.scripts[name] = content
        self._warn("putscript")

        return True

    # ------------------------------------------------------------------------
    def setactive(self, name):
        self.calls.append(("setactive", name))
        self.active = name

        return True


# ----------------------------------------------------------------------------
def message(
    sender: str,
    subject: str,
    list_id: str | None = None,
    message_id: str | None = None,
) -> bytes:
    lines = [
        f"From: Someone <{sender}>",
        "To: user@example.com",
        f"Subject: {subject}",
        "Date: Tue, 3 Feb 2026 04:05:06 +0000",
    ]

    if list_id:
        lines.append(f"List-Id: Some list <{list_id}>")

    if message_id:
        lines.append(f"Message-ID: <{message_id}>")

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
# (extra messages and their IMAP flags, by UID), folder_mail (the mail one
# folder holds instead, by folder name, then UID), and stray (how many empty
# names the script listing carries), sieve_extra (raw CAPABILITY lines sent
# ahead of SIEVE), imap_caps (what IMAP advertises), password
# (MAILCTL_PASSWORD's value), folders (further folders, as (name, subscribed)
# pairs), imap_failures (an IMAPClient method, and the error text it
# raises), imap_welcome (the greeting), imap_responses (an IMAPClient
# method, and the response lines the server sends while it runs), and
# sieve_warnings (checkscript or putscript, and the WARNINGS text its OK
# carries).

# Host from the env file over the exported MAILCTL_HOST, port from a flag
# over the env file, TLS from the config file, and the password named
# indirectly -- one rung each, so the report has to tell them apart.
ENV_FILE = """# written by hand
export MAILCTL_HOST=mail.from-env-file.example
MAILCTL_SIEVE_PORT='4191'
MAILCTL_PASSWORD_CMD="printf %s not-a-real-password"
OTHER_TOOL=ignored
"""

# An inbox whose first match 'Lists' already holds a copy of, as a second
# run of 'apply --move-to Lists --keep' finds it; the third match has no
# Message-ID, so it cannot be looked for there.
KEEP_RERUN = {
    "mail": {
        1: message("noreply@github.com", "PR opened", message_id="1@gh"),
        2: message("noreply@github.com", "Issue closed", message_id="2@gh"),
        4: message("noreply@github.com", "No id"),
    },
    "folder_mail": {
        "INBOX.Lists": {
            7: message("noreply@github.com", "PR opened", message_id="1@gh"),
        },
    },
}

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

# MAIL on a server that sorts (#159).
SORTED = {**MAIL, "imap_caps": {"MOVE", "UIDPLUS", "SORT"}}

# A GitHub message larger than every other, so a size sort puts it last
# and the list message the re-check drops falls between two it keeps.
GITHUB_LONG = (
    b"From: Someone <noreply@github.com>\r\n"
    b"To: user@example.com\r\n"
    b"Subject: Release published, with a much longer subject line than "
    b"any other message here\r\n"
    b"Date: Tue, 3 Feb 2026 04:05:06 +0000\r\n\r\n"
)

# For 'mark': 1 read, 2 neither, 3 read, flagged, and keyword $Todo.
MARKED = {
    "flags": {
        1: (b"\\Seen",),
        3: (b"\\Seen", b"\\Flagged", b"$Todo"),
    }
}

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
# change in a Subject that 'add --like --derive subject' copies into the
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

# What a Dovecot sends about itself, for the probe scenarios: the
# ManageSieve lines around SIEVE (a hostile one included, to be escaped),
# and IMAP answering ID and NAMESPACE.
PROBE = {
    "sieve_extra": (
        b'"IMPLEMENTATION" "Dovecot Pigeonhole"\r\n'
        b'"SASL" "PLAIN"\r\n'
        b'"NOTIFY" "mailto"\r\n'
        b'"X-HOSTILE" "\x1b[31mred\x07"\r\n'
        b'"VERSION" "1.0"\r\n'
    ),
    "imap_caps": {"ID", "IMAP4REV1", "MOVE", "NAMESPACE", "UIDPLUS"},
}

# A server mailctl has no module for (#39), naming the account, the host,
# and a folder where a careless report would repeat them: OWNER is the
# logged-in user (RFC 5804), and the rest are a server being chatty.
UNKNOWN = {
    "sieve_extra": (
        b'"IMPLEMENTATION" "Acme Sieve 1.0"\r\n'
        b'"OWNER" "user@example.com"\r\n'
        b'"X-HOST" "mail.example.com 192.0.2.7"\r\n'
        b'"X-HOSTILE" "\x1b[31mred\x07"\r\n'
    ),
    "imap_caps": {"ID", "IMAP4REV1", "NAMESPACE", "UIDPLUS"},
    "imap_id": (
        (
            b"name",
            b"Acme IMAP",
            b"support-url",
            b"https://mail.example.com/help/Lists",
        ),
    ),
}

# ----------------------------------------------------------------------------
# Baselines (#18, #19). 'presave' runs 'save-baseline --yes' against the same
# fakes first, and 'baseline_edit' then changes the saved file: a function
# given the parsed document, which edits it in place or returns the text to
# write instead. Every edit moves the saved times back, so a save's diff
# does not depend on the clock.
OLD = "2026-01-01T00:00:00Z"


# ----------------------------------------------------------------------------
def aged(document):
    document["server"]["taken"] = OLD

    for account in document["accounts"].values():
        account["taken"] = OLD


# ----------------------------------------------------------------------------
def drifted(document):
    """Everything a check can report, from the stored side.

    Live has no 'fileinto' (the scenario's caps), which the active script
    requires; the stored side had it, lacked 'regex', saw another
    delimiter, namespace, active script, IMAP ID, ManageSieve SASL, and a
    hostile capability value, and lacked MOVE, which mailctl relies on.
    """
    aged(document)
    rules, mail = document["server"]["rules"], document["server"]["mail"]
    rules["extensions"] = ["fileinto", "imap4flags", "mailbox"]
    rules["identity"] = {"implementation": "Dovecot Pigeonhole 2.3"}

    for item in rules["capabilities"]:
        if item["name"] == "SASL":
            item["value"] = "PLAIN LOGIN"

        if item["name"] == "X-HOSTILE":
            item["value"] = "calm"

    mail["capabilities"] = [
        item for item in mail["capabilities"] if item["name"] != "MOVE"
    ] + [{"name": "QUOTA", "value": None}]
    mail["identity"] = {"name": "Dovecot", "version": "2.3.21"}
    mail["delimiter"] = "/"
    mail["namespaces"] = [{"kind": "personal", "prefix": "", "delimiter": "/"}]
    document["server"]["endpoints"][0]["value"] = "old.example.com:993"
    document["accounts"]["user@example.com"]["active_rule_set"] = "old"


# ----------------------------------------------------------------------------
def only_added(document):
    """The stored side lacked 'regex': informational drift only."""
    aged(document)
    document["server"]["rules"]["extensions"].remove("regex")


# ----------------------------------------------------------------------------
def other_account(document):
    aged(document)
    accounts = document["accounts"]
    accounts["other@example.com"] = accounts.pop("user@example.com")


BASELINE = {**PROBE, "presave": True, "baseline_edit": aged}
DRIFTED = {
    **PROBE,
    "caps": ["imap4flags", "mailbox", "regex"],
    "presave": True,
    "baseline_edit": drifted,
}

# #160: a sender report over the default three and four more -- an
# encoded display name, a shouting address, a domain shared by two
# senders -- with 1, 4, and 6 read. With 1-3 that is GitHub 3 (1 read),
# the list sender 1, boss 2 (1 read), news 1 (1 read).
SENDERS = {
    "mail": {
        4: b"From: =?utf-8?q?Boss_=C3=9Cber?= <boss@example.com>\r\n\r\n",
        5: b"From: GitHub <NoReply@GitHub.com>\r\n\r\n",
        6: b"From: News <news@example.com>\r\n\r\n",
        7: b"From: boss@example.com\r\n\r\n",
    },
    "flags": {1: (b"\\Seen",), 4: (b"\\Seen",), 6: (b"\\Seen",)},
}

# A display name and a List-Id description carrying terminal escapes.
HOSTILE_SENDER = {
    "mail": {
        9: b"From: =?utf-8?q?Evil=1B]0;pwn=07?= <evil@example.com>\r\n"
        b"List-Id: =?utf-8?q?L=1B[31m?= <l.example.com>\r\n\r\n",
    }
}

# #205: the same alert in the greeting and as an untagged OK during the
# login, then a second one on LIST's tagged completion.
ALERTS = {
    "imap_welcome": b"* OK [ALERT] Maintenance tonight at 22:00 UTC",
    "imap_responses": {
        "login": [
            b"* OK [ALERT] Maintenance tonight at 22:00 UTC",
            b"a1 OK Logged in",
        ],
        "list_folders": [b"a2 OK [ALERT] Mailbox is 95% full"],
    },
}

# #208: Pigeonhole's WARNINGS, one per line, naming the stored script on
# PUTSCRIPT and a temporary file on CHECKSCRIPT.
FLAG_WARNING = (
    "warning: IMAP flag '\\Bogus' specified for the addflag command is "
    "invalid and will be ignored (only first invalid is reported)."
)
SIEVE_WARNINGS = {
    "sieve_warnings": {
        "checkscript": f"1790698792.M906065P27.tmp: line 16: {FLAG_WARNING}",
        "putscript": (
            f"managesieve: line 16: {FLAG_WARNING}\r\n"
            "managesieve: line 17: warning: specified date part 'bogus' "
            "is not known.\r\n"
        ),
    }
}

HOSTILE = {
    "add-like-hostile": (
        [
            "filter",
            "add",
            "--like",
            "9",
            "--derive",
            "subject",
            "--move-to",
            "Lists",
            "--dry-run",
        ],
        {"mail": {9: HOSTILE_SUBJECT}},
    ),
    "search-like-hostile": (
        [
            "mail",
            "search",
            "--like",
            "9",
            "--derive",
            "subject",
            "--build-filter",
        ],
        {"mail": {9: HOSTILE_SUBJECT}},
    ),
    "search-like-hostile-json": (
        [
            "mail",
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
    # #205: a server's alert is its own text, escaped like mail's.
    "folders-alert-hostile": (
        ["folder", "list"],
        {"imap_welcome": b"* OK [ALERT] \x1b]0;pwned\x07\x1b[31mred"},
    ),
    # #208: a server's warning is its own text too.
    "add-warnings-hostile": (
        ["filter", "add", *GITHUB, "--flag", "\\Bogus"],
        {
            "sieve_warnings": {
                "putscript": "line 1: \x1b]0;pwned\x07\x1b[31mred\r\nnext"
            }
        },
    ),
    "show-hostile": (["filterset", "show"], {"script": HOSTILE_SCRIPT}),
    "rules-hostile": (["filter", "list"], {"script": HOSTILE_SCRIPT}),
    # #101: every line of both CAPABILITY answers, the identities, and the
    # namespaces, sorted -- a server's own text escaped like mail's.
    "probe": (["server", "probe"], PROBE),
    "probe-json": (["server", "probe", "--json"], PROBE),
    # --verbose's chatter goes to stderr, so stdout stays one document.
    "probe-json-verbose": (["server", "probe", "--json", "-v"], PROBE),
    # #39: a server with no module is named, and --report prints a body
    # with the account, host, folders, and script left out; a known one
    # has nothing to report, and --report is not a JSON document.
    "probe-unknown": (["server", "probe"], UNKNOWN),
    "probe-report": (["server", "probe", "--report"], UNKNOWN),
    "probe-report-known": (["server", "probe", "--report"], PROBE),
    "probe-report-json": (["server", "probe", "--report", "--json"], UNKNOWN),
    # #151: a document escapes what a sender or a script wrote, as the
    # filter document does, rather than carrying it raw.
    "rules-hostile-json": (
        ["filter", "list", "--json"],
        {"script": HOSTILE_SCRIPT},
    ),
    "search-hostile-json": (
        ["mail", "search", "--json"],
        {"mail": {9: HOSTILE_SUBJECT}},
    ),
    "view-hostile-json": (
        ["mail", "view", "9", "--json"],
        {"mail": {9: HOSTILE_SUBJECT}},
    ),
    # #160: a sender's name and a list's description are escaped too.
    "senders-hostile": (["mail", "senders"], HOSTILE_SENDER),
    "senders-hostile-list-id": (
        ["mail", "senders", "--by", "list-id"],
        HOSTILE_SENDER,
    ),
    "senders-hostile-json": (["mail", "senders", "--json"], HOSTILE_SENDER),
}

# A broad rule ahead of a narrow one it covers, so the narrow one never
# runs: the finding 'rules --json' reports.
BROAD_THEN_NARROW = """require ["fileinto"];
# rule:[all-lists]
if header :contains "to" "@lists.example.com"
{
\tfileinto "INBOX.Lists";
\tstop;
}
# rule:[announce]
if header :contains "to" "announce@lists.example.com"
{
\tfileinto "INBOX.Announce";
\tstop;
}
"""

# #151: --json on the data commands and on every write command's --dry-run
# plan; --uids-only on search. Each pins a document's shape.
# A server that counts every folder in one LIST (#157); the second leaves
# INBOX.Lists without a STATUS line, as a folder that cannot hold mail.
COUNTED = {"imap_caps": ["MOVE", "UIDPLUS", "LIST-STATUS", "STATUS=SIZE"]}
COUNTED_NO_SIZE = {
    "imap_caps": ["MOVE", "UIDPLUS", "LIST-STATUS"],
    "counts": {"INBOX": (3, 1, 2048), "INBOX.spam": (12, 12, 30822)},
}

# What the IMAP double's folders report as UIDVALIDITY, and one from
# before a renumbering (#204).
CURRENT = str(DEFAULT_UIDVALIDITY)
STALE = str(DEFAULT_UIDVALIDITY - 1)

JSON_SCENARIOS = {
    "mark-dry-json": (
        ["mail", "mark", "1", "2", "--read", "--flag", "--dry-run", "--json"],
        MARKED,
    ),
    "mark-already-json": (
        ["mail", "mark", "1", "--read", "--dry-run", "--json"],
        MARKED,
    ),
    "mark-missing-json": (
        ["mail", "mark", "1", "98", "99", "--flag", "--dry-run", "--json"],
        MARKED,
    ),
    "mark-json-nodry": (
        ["mail", "mark", "2", "--flag", "--yes", "--json"],
        MARKED,
    ),
    # #204: a stale pin fails as one JSON line, coded.
    "mark-stale-uidvalidity-json": (
        [
            "mail",
            "mark",
            "1",
            "--read",
            "--uidvalidity",
            STALE,
            "--dry-run",
            "--json",
        ],
        MARKED,
    ),
    "list-json": (
        ["filterset", "list", "--json"],
        {"others": {"spare": ONE_RULE}},
    ),
    "list-empty-json": (["filterset", "list", "--json"], {"active": None}),
    # Progress is said on the way, so it goes to stderr with the rest.
    "list-verbose-json": (["filterset", "list", "--verbose", "--json"], {}),
    "folders-json": (["folder", "list", "--json"], {}),
    # #157: one LIST-STATUS; size only where STATUS=SIZE is advertised.
    "folders-counts-json": (["folder", "list", "--counts", "--json"], COUNTED),
    "folders-counts-nosize-json": (
        ["folder", "list", "--counts", "--json"],
        COUNTED_NO_SIZE,
    ),
    "folders-counts-refused-json": (
        ["folder", "list", "--counts", "--json"],
        {},
    ),
    "rules-json": (["filter", "list", "--json"], {}),
    "rules-findings-json": (
        ["filter", "list", "--json"],
        {"script": BROAD_THEN_NARROW},
    ),
    # The same findings as a person reads them, under their heading.
    "rules-findings": (["filter", "list"], {"script": BROAD_THEN_NARROW}),
    "rules-disabled-json": (
        ["filter", "list", "--json"],
        {"script": DISABLED_BOSS},
    ),
    "search-json": (["mail", "search", "--json"], MAIL),
    "search-limit-json": (["mail", "search", "--json", "--limit", "2"], MAIL),
    "search-none-json": (
        ["mail", "search", "--json", "--from", "nobody@x.y"],
        {},
    ),
    "search-like-json": (["mail", "search", "--like", "3", "--json"], {}),
    "search-uids-only": (["mail", "search", "--uids-only"], MAIL),
    "search-uids-only-from": (["mail", "search", "--uids-only", *GITHUB], {}),
    "search-uids-only-none": (
        ["mail", "search", "--uids-only", "--from", "nobody@x.y"],
        {},
    ),
    "search-uids-only-json": (["mail", "search", "--uids-only", "--json"], {}),
    "search-uids-only-build-filter": (
        ["mail", "search", "--uids-only", "--build-filter", *GITHUB],
        {},
    ),
    "search-like-uids-only": (
        ["mail", "search", "--like", "3", "--uids-only"],
        {},
    ),
    # #160: the sender report as a document, and its ceiling as an error.
    "senders-json": (["mail", "senders", "--json"], SENDERS),
    "senders-domain-json": (
        ["mail", "senders", "--by", "domain", "--json"],
        SENDERS,
    ),
    "senders-overcap-json": (
        ["mail", "senders", "--json", "--max-messages", "2"],
        SENDERS,
    ),
    "view-json": (["mail", "view", "4", "--json"], MAIL),
    "view-html-json": (["mail", "view", "5", "--json"], MAIL),
    "view-attachment-json": (
        ["mail", "view", "6", "--json"],
        {"mail": {6: SPOOFED}},
    ),
    "view-missing-json": (["mail", "view", "99", "--json"], MAIL),
    "view-json-raw": (["mail", "view", "4", "--json", "--raw"], MAIL),
    "add-dry-json": (
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "Lists",
            "--dry-run",
            "--json",
        ],
        {},
    ),
    "add-dry-imapcreate-json": (
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "Lists/GitHub",
            "--create-folder",
            "--mark-read",
            "--dry-run",
            "--json",
        ],
        {},
    ),
    "add-like-json": (
        [
            "filter",
            "add",
            "--like",
            "2",
            "--move-to",
            "Lists",
            "--dry-run",
            "--json",
        ],
        {},
    ),
    "add-json-nodry": (
        ["filter", "add", *GITHUB, "--move-to", "Lists", "--json"],
        {},
    ),
    "add-json-refused": (
        [
            "filter",
            "add",
            *GITHUB,
            "--redirect",
            "a@b.c",
            "--dry-run",
            "--json",
        ],
        {},
    ),
    "apply-dry-json": (
        [
            "filter",
            "apply",
            *GITHUB,
            "--move-to",
            "Lists",
            "--dry-run",
            "--json",
        ],
        {},
    ),
    "apply-dry-overcap-json": (
        [
            "filter",
            "apply",
            *GITHUB,
            "--move-to",
            "Lists",
            "--max-messages",
            "1",
            "--dry-run",
            "--json",
        ],
        {},
    ),
    "apply-nomatch-json": (
        [
            "filter",
            "apply",
            "--from",
            "nobody@x.y",
            "--move-to",
            "Lists",
            "--dry-run",
            "--json",
        ],
        {},
    ),
    "apply-keep-only-json": (
        ["filter", "apply", *GITHUB, "--keep", "--dry-run", "--json"],
        {},
    ),
    "apply-keep-copy-json": (
        [
            "filter",
            "apply",
            *GITHUB,
            "--move-to",
            "Lists",
            "--keep",
            "--dry-run",
            "--json",
        ],
        {},
    ),
    "apply-json-nodry": (
        ["filter", "apply", *GITHUB, "--move-to", "Lists", "--yes", "--json"],
        {},
    ),
    "remove-dry-json": (
        ["filter", "remove", "keep-boss", "--dry-run", "--json"],
        {},
    ),
    "remove-unknown-json": (
        ["filter", "remove", "phantom", "--dry-run", "--json"],
        {},
    ),
    "move-dry-json": (
        ["filter", "move", "bin-the-noise", "--first", "--dry-run", "--json"],
        {},
    ),
    "move-noop-json": (
        ["filter", "move", "keep-boss", "--first", "--dry-run", "--json"],
        {},
    ),
    "move-starves-json": (
        ["filter", "move", "all-lists", "--first", "--dry-run", "--json"],
        {"script": NARROW_THEN_BROAD},
    ),
    "disable-dry-json": (
        ["filter", "disable", "keep-boss", "--dry-run", "--json"],
        {},
    ),
    "enable-dry-json": (
        ["filter", "enable", "keep-boss", "--dry-run", "--json"],
        {"script": DISABLED_BOSS},
    ),
    "enable-already-json": (
        ["filter", "enable", "keep-boss", "--dry-run", "--json"],
        {},
    ),
    "rename-dry-json": (
        ["filter", "rename", "keep-boss", "The boss", "--dry-run", "--json"],
        {},
    ),
    "rename-taken-json": (
        [
            "filter",
            "rename",
            "keep-boss",
            "bin-the-noise",
            "--dry-run",
            "--json",
        ],
        {},
    ),
    "create-folder-dry-json": (
        ["folder", "create", "Lists/GitHub/New", "--dry-run", "--json"],
        {},
    ),
    "create-folder-exists-json": (
        ["folder", "create", "Lists", "--dry-run", "--json"],
        {},
    ),
    "subscribe-dry-json": (
        ["folder", "subscribe", "INBOX.spam", "--dry-run", "--json"],
        {},
    ),
    "subscribe-already-json": (
        ["folder", "subscribe", "Lists", "--dry-run", "--json"],
        {},
    ),
    "unsubscribe-dry-json": (
        ["folder", "unsubscribe", "Lists", "--dry-run", "--json"],
        {},
    ),
    "restore-dry-json": (
        ["filterset", "restore", "<FILE>", "--dry-run", "--json"],
        {"file": ONE_RULE},
    ),
    "restore-identical-json": (
        ["filterset", "restore", "<FILE>", "--dry-run", "--json"],
        {"file": "SAME"},
    ),
    "restore-json-nodry": (
        ["filterset", "restore", "<FILE>", "--yes", "--json"],
        {"file": ONE_RULE},
    ),
}

# For 'rename-folder' (#5): a rule filing into INBOX.Lists, a disabled one
# filing into the folder under it, and a rule and a hand comment that must
# come through the rename byte for byte.
RENAME_SCRIPT = """require ["fileinto"];
# rule:[lists]
if header :contains "list-id" "lists.example.com"
{
\tfileinto "INBOX.Lists";
\tstop;
}
# my own note
# rule:[keep-boss]
if header :contains "from" "boss@example.com"
{
\tfileinto "INBOX.Boss";
}
# rule:[github]
if false # header :contains "from" "noreply@github.com"
{
\tfileinto "INBOX.Lists.GitHub";
}
"""

RENAME = {
    "script": RENAME_SCRIPT,
    "folders": [("INBOX.Lists.GitHub", True)],
    "counts": {"INBOX.Lists": (360, 0, 0)},
}

RENAME_ARGS = ["folder", "rename", "Lists", "Archive"]

# For 'optimize-rules' (#21): a catch-all starving a specific rule, a rule
# repeating the catch-all, three Trash rules to merge around a disabled
# one, and a glob that can only be suspected of shadowing.
OPTIMIZE_SCRIPT = """require ["fileinto"];
# rule:[Github catchall]
if allof (header :contains "from" "github.com")
{
\tfileinto "INBOX.Github";
\tstop;
}
# rule:[Github billing]
if allof (header :contains "from" "billing@github.com")
{
\tfileinto "INBOX.Github.Billing";
\tstop;
}
# rule:[Github again]
if allof (header :contains "from" "noreply@github.com")
{
\tfileinto "INBOX.Github";
\tstop;
}
# rule:[Herrschners Spam]
if allof (header :contains "to" "herrschners@example.com")
{
\tfileinto "Trash";
\tstop;
}
# rule:[Rumble]
if allof (header :contains "to" "rumble@example.com")
{
\tfileinto "Trash";
\tstop;
}
# rule:[Old list]
if false # allof (header :contains "to" "old@example.com")
{
\tfileinto "Trash";
\tstop;
}
# rule:[Dump list]
if allof (header :contains "to" "aur-general@lists.example.org")
{
\tfileinto "Trash";
\tstop;
}
# rule:[Any shop]
if allof (header :matches "from" "*shop*")
{
\tfileinto "INBOX.Shops";
\tstop;
}
# rule:[Bike shop]
if allof (header :contains "from" "bikeshop.example")
{
\tfileinto "INBOX.Bikes";
\tstop;
}
"""

OPTIMIZE = {"script": OPTIMIZE_SCRIPT}

SCENARIOS = {
    **HOSTILE,
    **JSON_SCENARIOS,
    # #18: a baseline saved, shown, and replaced; only a local file is
    # written, so no scenario here has a write among its server calls.
    "save-baseline": (["server", "baseline", "save"], PROBE),
    "save-baseline-dry": (["server", "baseline", "save", "--dry-run"], PROBE),
    "save-baseline-unchanged": (
        ["server", "baseline", "save", "--yes"],
        BASELINE,
    ),
    "save-baseline-notty": (["server", "baseline", "save"], DRIFTED),
    "save-baseline-yes": (["server", "baseline", "save", "--yes"], DRIFTED),
    "save-baseline-other-account": (
        ["server", "baseline", "save", "--yes"],
        {**BASELINE, "baseline_edit": other_account},
    ),
    "save-baseline-corrupt": (
        ["server", "baseline", "save", "--yes"],
        {**BASELINE, "baseline_edit": lambda document: "{not json\n"},
    ),
    "show-baseline": (["server", "baseline", "show"], BASELINE),
    "show-baseline-json": (["server", "baseline", "show", "--json"], BASELINE),
    "show-baseline-none": (["server", "baseline", "show"], PROBE),
    # #151: a failure under --json is one JSON line on stderr.
    "show-baseline-none-json": (
        ["server", "baseline", "show", "--json"],
        PROBE,
    ),
    "show-baseline-other-account": (
        ["server", "baseline", "show"],
        {**BASELINE, "baseline_edit": other_account},
    ),
    # #19: drift, in the terms of what it means for this account.
    "check-baseline": (["server", "baseline", "check"], BASELINE),
    "check-baseline-json": (
        ["server", "baseline", "check", "--json"],
        BASELINE,
    ),
    "check-baseline-none": (["server", "baseline", "check"], PROBE),
    "check-baseline-none-json": (
        ["server", "baseline", "check", "--json"],
        PROBE,
    ),
    "check-baseline-info": (
        ["server", "baseline", "check"],
        {**BASELINE, "baseline_edit": only_added},
    ),
    "check-baseline-other-account": (
        ["server", "baseline", "check"],
        {**DRIFTED, "baseline_edit": other_account},
    ),
    "check-baseline-version": (
        ["server", "baseline", "check"],
        {
            **BASELINE,
            "baseline_edit": lambda document: document.update(version=2),
        },
    ),
    "check-baseline-probe-version": (
        ["server", "baseline", "check"],
        {
            **BASELINE,
            "baseline_edit": lambda document: document["server"].update(
                version=9
            ),
        },
    ),
    "check-baseline-missing-key": (
        ["server", "baseline", "check", "--json"],
        {
            **BASELINE,
            "baseline_edit": lambda document: document.__delitem__("accounts"),
        },
    ),
    "check-baseline-unparseable-script": (
        ["server", "baseline", "check"],
        {**DRIFTED, "script": "this is not sieve {"},
    ),
    "test-baseline": (["server", "test"], BASELINE),
    "test-baseline-drift": (["server", "test"], DRIFTED),
    "test-baseline-corrupt": (
        ["server", "test"],
        {**BASELINE, "baseline_edit": lambda document: "[]\n"},
    ),
    "senders": (["mail", "senders"], SENDERS),
    "senders-domain": (["mail", "senders", "--by", "domain"], SENDERS),
    "senders-list-id": (["mail", "senders", "--by", "list-id"], SENDERS),
    "senders-top-min": (
        ["mail", "senders", "--top", "1", "--min", "2"],
        SENDERS,
    ),
    "senders-criteria": (
        ["mail", "senders", "--from", "example.com", "--since", "2026-01-01"],
        SENDERS,
    ),
    "senders-empty": (["mail", "senders", "--from", "nobody@x.y"], {}),
    "senders-overcap": (["mail", "senders", "--max-messages", "2"], SENDERS),
    "senders-bad-top": (["mail", "senders", "--top", "-1"], {}),
    "search": (["mail", "search"], MAIL),
    "search-from": (["mail", "search", *GITHUB], MAIL),
    "search-limit": (["mail", "search", "--limit", "2"], MAIL),
    "search-raw": (["mail", "search", "--raw", "UNSEEN"], MAIL),
    "search-both": (["mail", "search", *GITHUB, "--raw", "ALL"], MAIL),
    "search-none": (["mail", "search", "--from", "nobody@x.y"], MAIL),
    "search-folder": (["mail", "search", "--folder", "Lists"], MAIL),
    # #148: --like pre-fills the criteria from a message; a flag replaces
    # what was derived for its header, and --build-filter saves nothing.
    "search-like": (["mail", "search", "--like", "3"], {}),
    "search-like-combined": (
        [
            "mail",
            "search",
            "--like",
            "2",
            "--subject",
            "Issue",
            "--match",
            "all",
        ],
        {},
    ),
    "search-like-override": (
        ["mail", "search", "--like", "3", "--list-id", "other.example.com"],
        {},
    ),
    "search-like-missing": (["mail", "search", "--like", "99"], {}),
    "search-like-derive-missing": (
        ["mail", "search", "--like", "2", "--derive", "cc"],
        {},
    ),
    "search-like-raw": (["mail", "search", "--like", "3", "--raw", "ALL"], {}),
    "search-derive-alone": (["mail", "search", "--derive", "from"], {}),
    "search-build-filter": (["mail", "search", "--build-filter", *GITHUB], {}),
    "search-build-filter-json": (
        ["mail", "search", "--build-filter", "--json", *GITHUB],
        {},
    ),
    "search-like-build-filter-json": (
        ["mail", "search", "--like", "3", "--build-filter", "--json"],
        {},
    ),
    "search-build-filter-nothing": (["mail", "search", "--build-filter"], {}),
    "search-json-alone": (["mail", "search", "--json", *GITHUB], {}),
    # #63: source_folder in the config file is where --folder defaults to.
    "search-config-folder": (
        ["mail", "search"],
        {**MAIL, "config": 'source_folder = "Lists"\n'},
    ),
    "apply-config-folder": (
        ["filter", "apply", *GITHUB, "--move-to", "spam", "--dry-run"],
        {"config": 'source_folder = "Lists"\n'},
    ),
    "test-config-folder": (
        ["server", "test"],
        {"config": 'source_folder = "Lists"\n'},
    ),
    "view": (["mail", "view", "4"], MAIL),
    "view-html": (["mail", "view", "5"], MAIL),
    "view-headers": (["mail", "view", "4", "--headers-only"], MAIL),
    "view-raw": (["mail", "view", "5", "--raw"], {**MAIL, "tty": True}),
    "view-raw-pipe": (["mail", "view", "5", "--raw"], MAIL),
    "view-bidi": (["mail", "view", "6"], {"mail": {6: SPOOFED}}),
    "view-missing": (["mail", "view", "99"], MAIL),
    "mark-dry": (
        ["mail", "mark", "1", "2", "--read", "--flag", "--dry-run"],
        MARKED,
    ),
    "mark-yes": (
        ["mail", "mark", "1", "2", "--read", "--flag", "--yes"],
        MARKED,
    ),
    "mark-clear-yes": (
        [
            "mail",
            "mark",
            "3",
            "--unread",
            "--unflag",
            "--no-keyword",
            "$Todo",
            "--yes",
        ],
        MARKED,
    ),
    "mark-folder-dry": (
        [
            "mail",
            "mark",
            "1",
            "--folder",
            "Lists",
            "--keyword",
            "$Todo",
            "--dry-run",
        ],
        MARKED,
    ),
    "mark-already": (["mail", "mark", "1", "--read"], MARKED),
    "mark-notty": (["mail", "mark", "2", "--flag"], MARKED),
    "mark-missing": (["mail", "mark", "1", "98", "99", "--flag"], MARKED),
    # #204: UIDs pinned to the folder's UIDVALIDITY. A stale pin refuses
    # the whole command with no STORE; a current one marks as before.
    "mark-stale-uidvalidity": (
        ["mail", "mark", "1", "2", "--read", "--uidvalidity", STALE, "--yes"],
        MARKED,
    ),
    "mark-uidvalidity-yes": (
        [
            "mail",
            "mark",
            "1",
            "2",
            "--read",
            "--uidvalidity",
            CURRENT,
            "--yes",
        ],
        MARKED,
    ),
    "mark-uidvalidity-range": (
        ["mail", "mark", "1", "--read", "--uidvalidity", "0", "--yes"],
        MARKED,
    ),
    "view-stale-uidvalidity": (
        ["mail", "view", "4", "--uidvalidity", STALE],
        MAIL,
    ),
    "view-uidvalidity": (
        ["mail", "view", "4", "--uidvalidity", CURRENT],
        MAIL,
    ),
    "search-like-stale-uidvalidity": (
        ["mail", "search", "--like", "3", "--uidvalidity", STALE],
        {},
    ),
    "search-uidvalidity-no-like": (
        ["mail", "search", "--uidvalidity", CURRENT],
        {},
    ),
    "add-like-stale-uidvalidity": (
        [
            "filter",
            "add",
            "--like",
            "3",
            "--move-to",
            "Lists",
            "--uidvalidity",
            STALE,
            "--dry-run",
        ],
        {},
    ),
    "add-uidvalidity-no-like": (
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "Lists",
            "--uidvalidity",
            CURRENT,
        ],
        {},
    ),
    "apply-like-stale-uidvalidity": (
        [
            "filter",
            "apply",
            "--like",
            "2",
            "--move-to",
            "Lists",
            "--uidvalidity",
            STALE,
            "--yes",
        ],
        {},
    ),
    "mark-read-unread": (["mail", "mark", "1", "--read", "--unread"], MARKED),
    "mark-nothing": (["mail", "mark", "1"], MARKED),
    "mark-bad-keyword": (
        ["mail", "mark", "1", "--keyword", "two words"],
        MARKED,
    ),
    "mark-system-keyword": (
        ["mail", "mark", "1", "--keyword", "\\Deleted"],
        MARKED,
    ),
    "mark-keyword-both": (
        ["mail", "mark", "1", "--keyword", "$Todo", "--no-keyword", "$todo"],
        MARKED,
    ),
    "list": (["filterset", "list"], {}),
    "list-verbose": (["filterset", "list", "--verbose"], {}),
    # One script, and a listing sievelib read an empty name out of (#119).
    "list-stray-line": (["filterset", "list"], {"stray": 1}),
    "test-stray-line": (["server", "test"], {"stray": 1}),
    "show": (["filterset", "show"], {}),
    "rules": (["filter", "list"], {}),
    "folders": (["folder", "list"], {}),
    # #205: an ALERT is shown on stderr without --verbose, once however
    # often it is sent -- in the greeting and again as the login runs --
    # and one on a refused login is shown before the failure.
    "folders-alert": (["folder", "list"], ALERTS),
    "folders-alert-json": (["folder", "list", "--json"], ALERTS),
    "folders-alert-verbose": (["folder", "list", "-v"], ALERTS),
    "folders-alert-login-refused": (
        ["folder", "list"],
        {
            "imap_responses": {
                "login": [b"a1 NO [ALERT] Account suspended; call support"]
            },
            "imap_failures": {"login": "Account suspended; call support"},
        },
    ),
    "folders-counts": (["folder", "list", "--counts"], COUNTED),
    "folders-counts-nosize": (["folder", "list", "--counts"], COUNTED_NO_SIZE),
    # A server without LIST-STATUS: refused, naming it, rather than a
    # STATUS per folder.
    "folders-counts-refused": (["folder", "list", "--counts"], {}),
    "test": (["server", "test"], {}),
    # A server that advertises neither ID nor NAMESPACE, and sends only
    # the SIEVE line: the report says so rather than leaving gaps.
    "probe-bare": (["server", "probe"], {}),
    "test-verbose": (["server", "test", "-v"], {}),
    "test-env-file": (
        ["server", "test", "--env-file", "--sieve-port", "4192"],
        {"env": ENV_FILE, "config": 'sieve_tls = "ssl"\n'},
    ),
    "test-env-file-missing": (
        ["server", "test", "--env-file", "nowhere.env"],
        {},
    ),
    # #61: the Password line reports the outcome of reading it, so a
    # refused file is not shown as "set" above its own refusal.
    "test-password-refused": (
        ["server", "test", "--password-file", "<FILE>"],
        {"file": "not-a-real-password\n", "file_mode": 0o644},
    ),
    "test-password-file": (
        ["server", "test", "--password-file", "<FILE>"],
        {"file": "not-a-real-password\n"},
    ),
    # #50: backup_dir expands $VAR / ${VAR} and ~, like any path setting.
    "backup-dir-expanded": (
        ["filterset", "backup"],
        {"config": 'backup_dir = "${XDG_CONFIG_HOME}/elsewhere"\n'},
    ),
    "backup-dry": (["filterset", "backup", "--dry-run"], {}),
    "backup-noactive": (["filterset", "backup"], {"active": None}),
    "subscribe": (["folder", "subscribe", "spam"], {}),
    "subscribe-dry": (["folder", "subscribe", "INBOX.spam", "--dry-run"], {}),
    "subscribe-already": (["folder", "subscribe", "Lists"], {}),
    "subscribe-missing": (["folder", "subscribe", "Nowhere"], {}),
    "unsubscribe": (["folder", "unsubscribe", "Lists"], {}),
    "unsubscribe-already": (["folder", "unsubscribe", "spam"], {}),
    "unsubscribe-missing": (["folder", "unsubscribe", "Nowhere"], {}),
    # #155: a folder made on its own, not only by --create-folder.
    "create-folder-dry": (
        ["folder", "create", "Lists/GitHub", "--dry-run"],
        {},
    ),
    "create-folder-yes": (["folder", "create", "Lists/GitHub", "--yes"], {}),
    "create-folder-nosub": (
        ["folder", "create", "New", "--no-subscribe", "--yes"],
        {},
    ),
    "create-folder-notty": (["folder", "create", "New"], {}),
    "create-folder-parents": (
        ["folder", "create", "Work/2026/Q3", "--dry-run"],
        {},
    ),
    "create-folder-parent-nosub": (
        ["folder", "create", "Work/2026", "--no-subscribe", "--dry-run"],
        {},
    ),
    "create-folder-exists": (["folder", "create", "Lists"], {}),
    "create-folder-exists-nosub": (
        ["folder", "create", "Lists", "--no-subscribe"],
        {},
    ),
    "create-folder-unsubscribed": (["folder", "create", "spam"], {}),
    "create-folder-case-variant": (["folder", "create", "lists", "--yes"], {}),
    "add-nosubscribe-alone": (
        ["filter", "add", *GITHUB, "--move-to", "Lists", "--no-subscribe"],
        {},
    ),
    "move-dry": (
        ["filter", "move", "bin-the-noise", "--first", "--dry-run"],
        {},
    ),
    "move-yes": (["filter", "move", "bin-the-noise", "--first", "--yes"], {}),
    "move-notty": (["filter", "move", "keep-boss", "--last"], {}),
    "move-noop": (["filter", "move", "keep-boss", "--first"], {}),
    "move-unknown": (["filter", "move", "phantom", "--first"], {}),
    "move-anchor-unknown": (
        ["filter", "move", "keep-boss", "--after", "phantom"],
        {},
    ),
    "move-self-anchor": (
        ["filter", "move", "keep-boss", "--before", "keep-boss"],
        {},
    ),
    "move-no-position": (["filter", "move", "keep-boss"], {}),
    "move-starves": (
        ["filter", "move", "all-lists", "--first", "--dry-run"],
        {"script": NARROW_THEN_BROAD},
    ),
    "restore-dry": (
        ["filterset", "restore", "<FILE>", "--dry-run"],
        {"file": ONE_RULE},
    ),
    "restore-yes": (
        ["filterset", "restore", "<FILE>", "--yes"],
        {"file": ONE_RULE},
    ),
    "restore-notty": (["filterset", "restore", "<FILE>"], {"file": ONE_RULE}),
    "restore-identical": (
        ["filterset", "restore", "<FILE>", "--yes"],
        {"file": "SAME"},
    ),
    "restore-over-unparseable": (
        ["filterset", "restore", "<FILE>", "--yes"],
        {"file": ONE_RULE, "script": "if {{{ broken\n"},
    ),
    "restore-rejected": (
        ["filterset", "restore", "<FILE>", "--yes"],
        {"file": ONE_RULE, "reject": True},
    ),
    "restore-missing-file": (
        ["filterset", "restore", "/nonexistent/x.sieve"],
        {},
    ),
    # #52: a named target, and an empty file refused unless asked for.
    "restore-script": (
        ["filterset", "restore", "<FILE>", "--filterset", "spare", "--yes"],
        {"file": ONE_RULE, "others": {"spare": "# nothing yet\n"}},
    ),
    # #54: with nothing active a bare restore names --filterset; naming the
    # deactivated script is the recovery, and activates it.
    "restore-noactive": (
        ["filterset", "restore", "<FILE>", "--yes"],
        {"file": ONE_RULE, "active": None, "others": {"managesieve": "x\n"}},
    ),
    "restore-noactive-script": (
        [
            "filterset",
            "restore",
            "<FILE>",
            "--filterset",
            "managesieve",
            "--yes",
        ],
        {"file": ONE_RULE, "active": None, "others": {"managesieve": "x\n"}},
    ),
    "restore-empty": (
        ["filterset", "restore", "<FILE>", "--yes"],
        {"file": "\n"},
    ),
    "restore-empty-allowed": (
        ["filterset", "restore", "<FILE>", "--allow-empty", "--dry-run"],
        {"file": ""},
    ),
    "add-dry-missing": (
        ["filter", "add", *GITHUB, "--move-to", "Lists/GitHub", "--dry-run"],
        {},
    ),
    "add-dry-sievecreate": (
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "Lists/GitHub",
            "--create-folder",
            "--dry-run",
        ],
        {},
    ),
    "add-dry-sievecreate-nosub": (
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "Lists/GitHub",
            "--create-folder",
            "--no-subscribe",
            "--dry-run",
        ],
        {},
    ),
    "add-real-sievecreate": (
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "Lists/GitHub",
            "--create-folder",
        ],
        {},
    ),
    "add-noimap-sievecreate": (
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "Lists/GitHub",
            "--create-folder",
            "--no-mail",
        ],
        {},
    ),
    "add-dry-imapcreate": (
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "Lists/GitHub",
            "--create-folder",
            "--dry-run",
        ],
        {"caps": NO_MAILBOX},
    ),
    "add-real-imapcreate": (
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "Lists/GitHub",
            "--create-folder",
            "--mark-read",
        ],
        {"caps": NO_MAILBOX},
    ),
    "add-real-imapcreate-verbose": (
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "Lists/GitHub",
            "--create-folder",
            "-v",
        ],
        {"caps": NO_MAILBOX},
    ),
    "add-real-exists": (
        ["filter", "add", *GITHUB, "--move-to", "Lists", "--first"],
        {},
    ),
    "add-real-noconfirm": (
        ["filter", "add", *GITHUB, "--move-to", "Lists"],
        {},
    ),
    "apply-dry-overcap": (
        [
            "filter",
            "apply",
            *GITHUB,
            "--move-to",
            "Lists",
            "--dry-run",
            "--max-messages",
            "1",
        ],
        {},
    ),
    # #149: add saves the rule only, so the existing-mail pass's flags --
    # and from-message, which --like replaced -- are gone rather than
    # ignored.
    "add-noapply-gone": (
        ["filter", "add", *GITHUB, "--move-to", "Lists", "--no-apply"],
        {},
    ),
    "add-maxmessages-gone": (
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "Lists",
            "--max-messages",
            "1",
        ],
        {},
    ),
    "from-message-gone": (["from-message", "--uid", "2"], {}),
    # #219: the commands are grouped; an old name is refused, naming the
    # command it is now, and never run.
    "gone-rules": (["rules"], {}),
    "gone-messages": (["messages", "--folder", "INBOX"], {}),
    "gone-add": (["add", "--from", "a@b.c", "--move-to", "X"], {}),
    "gone-save-baseline": (["--verbose", "save-baseline"], {}),
    "gone-help-rules": (["help", "rules"], {}),
    "help-top": (["--help"], {}),
    "help-filter": (["help", "filter"], {}),
    "help-server-baseline": (["server", "baseline", "--help"], {}),
    "group-without-action": (["filter"], {}),
    "add-noimap": (
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "Lists/GitHub",
            "--no-mail",
            "--dry-run",
        ],
        {},
    ),
    "add-noimap-create-nomailbox": (
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "X",
            "--no-mail",
            "--create-folder",
        ],
        {"caps": NO_MAILBOX},
    ),
    "add-noaction": (["filter", "add", *GITHUB], {}),
    "add-nocriteria": (["filter", "add", "--move-to", "Lists"], {}),
    "add-redirect": (["filter", "add", *GITHUB, "--redirect", "x@y.z"], {}),
    "add-vacation": (["filter", "add", *GITHUB, "--vacation", "hi"], {}),
    "add-discard": (["filter", "add", *GITHUB, "--discard"], {}),
    "add-flag-only": (["filter", "add", *GITHUB, "--flag", "\\Flagged"], {}),
    # #208: PUTSCRIPT's WARNINGS are shown on stderr without --verbose, a
    # line each; CHECKSCRIPT's, the same warnings about a temporary file,
    # only under --verbose. --json is a dry run, which uploads nothing.
    "add-warnings": (
        ["filter", "add", *GITHUB, "--flag", "\\Bogus"],
        SIEVE_WARNINGS,
    ),
    "add-warnings-verbose": (
        ["filter", "add", *GITHUB, "--flag", "\\Bogus", "-v"],
        SIEVE_WARNINGS,
    ),
    "add-warnings-json": (
        ["filter", "add", *GITHUB, "--flag", "\\Bogus", "--dry-run", "--json"],
        SIEVE_WARNINGS,
    ),
    "add-keep-only": (["filter", "add", *GITHUB, "--keep"], {}),
    "add-default-folder-extmissing": (
        ["filter", "add", *GITHUB, "--dry-run"],
        {"caps": ["imap4flags"], "config": 'default_folder = "Lists"\n'},
    ),
    "apply-fileinto-source-normalized": (
        [
            "filter",
            "apply",
            *GITHUB,
            "--folder",
            "Lists",
            "--move-to",
            "INBOX.Lists",
            "--yes",
        ],
        {},
    ),
    "apply-fileinto-source": (
        ["filter", "apply", *GITHUB, "--move-to", "INBOX", "--yes"],
        {},
    ),
    "apply-discard-yes": (
        ["filter", "apply", *GITHUB, "--discard", "--yes"],
        {},
    ),
    "apply-flag-only": (
        ["filter", "apply", *GITHUB, "--flag", "\\Flagged", "--yes"],
        {},
    ),
    "apply-keep-only": (["filter", "apply", *GITHUB, "--keep", "--yes"], {}),
    "apply-keep-copy-yes": (
        ["filter", "apply", *GITHUB, "--move-to", "Lists", "--keep", "--yes"],
        {},
    ),
    "apply-keep-copy-dry": (
        [
            "filter",
            "apply",
            *GITHUB,
            "--move-to",
            "Lists",
            "--keep",
            "--dry-run",
        ],
        {},
    ),
    "apply-keep-copy-flag-yes": (
        [
            "filter",
            "apply",
            *GITHUB,
            "--move-to",
            "Lists",
            "--keep",
            "--mark-read",
            "--yes",
        ],
        {},
    ),
    "apply-discard-keep": (
        ["filter", "apply", *GITHUB, "--discard", "--keep", "--yes"],
        {},
    ),
    "apply-keep-rerun-dry": (
        [
            "filter",
            "apply",
            *GITHUB,
            "--move-to",
            "Lists",
            "--keep",
            "--dry-run",
        ],
        KEEP_RERUN,
    ),
    "apply-keep-rerun-yes": (
        ["filter", "apply", *GITHUB, "--move-to", "Lists", "--keep", "--yes"],
        KEEP_RERUN,
    ),
    "apply-keep-rerun-json": (
        [
            "filter",
            "apply",
            *GITHUB,
            "--move-to",
            "Lists",
            "--keep",
            "--dry-run",
            "--json",
        ],
        KEEP_RERUN,
    ),
    "apply-keep-rerun-all-held": (
        ["filter", "apply", *GITHUB, "--move-to", "Lists", "--keep", "--yes"],
        {
            "mail": {uid: KEEP_RERUN["mail"][uid] for uid in (1, 2)},
            "folder_mail": {
                "INBOX.Lists": {
                    7: KEEP_RERUN["mail"][1],
                    8: KEEP_RERUN["mail"][2],
                },
            },
        },
    ),
    "add-before-unknown": (
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "Lists",
            "--before",
            "phantom",
        ],
        {},
    ),
    "add-replace-existing": (
        [
            "filter",
            "add",
            "--subject",
            "newsletter",
            "--move-to",
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
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "Lists",
            "--name",
            "keep-boss",
        ],
        {},
    ),
    "add-rejected": (
        ["filter", "add", *GITHUB, "--move-to", "Lists"],
        {"reject": True},
    ),
    "add-empty-account": (
        ["filter", "add", *GITHUB, "--move-to", "Lists"],
        {"active": None},
    ),
    "add-extmissing": (
        [
            "filter",
            "add",
            *GITHUB,
            "--mark-read",
            "--move-to",
            "Lists",
            "--dry-run",
        ],
        {"caps": ["fileinto"]},
    ),
    "apply-yes": (
        ["filter", "apply", *GITHUB, "--move-to", "Lists", "--yes"],
        {},
    ),
    "apply-dry": (
        ["filter", "apply", *GITHUB, "--move-to", "Lists", "--dry-run"],
        {},
    ),
    "apply-missing": (
        ["filter", "apply", *GITHUB, "--move-to", "Lists/GitHub"],
        {},
    ),
    # #56: 'lists' is not INBOX.Lists; the near miss is named, missing or
    # about to be created beside it.
    "apply-case-variant": (
        ["filter", "apply", *GITHUB, "--move-to", "lists"],
        {},
    ),
    "add-dry-create-case-variant": (
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "lists",
            "--create-folder",
            "--dry-run",
        ],
        {},
    ),
    "apply-create": (
        [
            "filter",
            "apply",
            *GITHUB,
            "--move-to",
            "Lists/GitHub",
            "--create-folder",
            "--yes",
        ],
        {},
    ),
    "apply-create-dry": (
        [
            "filter",
            "apply",
            *GITHUB,
            "--move-to",
            "Lists/GitHub",
            "--create-folder",
            "--dry-run",
        ],
        {},
    ),
    "add-imapcreate-rejected": (
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "Lists/GitHub",
            "--create-folder",
        ],
        {"caps": NO_MAILBOX, "reject": True},
    ),
    "apply-create-nomatch": (
        [
            "filter",
            "apply",
            "--from",
            "nobody@x.y",
            "--move-to",
            "Lists/GitHub",
            "--create-folder",
            "--yes",
        ],
        {},
    ),
    "apply-nothing": (["filter", "apply", *GITHUB], {}),
    "apply-overcap": (
        [
            "filter",
            "apply",
            *GITHUB,
            "--move-to",
            "Lists",
            "--max-messages",
            "1",
            "--yes",
        ],
        {},
    ),
    "apply-discard-notty": (["filter", "apply", *GITHUB, "--discard"], {}),
    "apply-nomatch": (
        [
            "filter",
            "apply",
            "--from",
            "nobody@x.y",
            "--move-to",
            "Lists",
            "--yes",
        ],
        {},
    ),
    "disable-yes": (["filter", "disable", "keep-boss", "--yes"], {}),
    "disable-dry": (["filter", "disable", "keep-boss", "--dry-run"], {}),
    "disable-notty": (["filter", "disable", "keep-boss"], {}),
    "disable-unknown": (["filter", "disable", "phantom", "--yes"], {}),
    "disable-already": (
        ["filter", "disable", "keep-boss", "--yes"],
        {"script": DISABLED_BOSS},
    ),
    "disable-empty": (
        ["filter", "disable", "keep-boss", "--yes"],
        {"script": ""},
    ),
    "enable-yes": (
        ["filter", "enable", "keep-boss", "--yes"],
        {"script": DISABLED_BOSS},
    ),
    "enable-dry": (
        ["filter", "enable", "keep-boss", "--dry-run"],
        {"script": DISABLED_BOSS},
    ),
    "enable-already": (["filter", "enable", "keep-boss", "--yes"], {}),
    "enable-unknown": (
        ["filter", "enable", "phantom", "--yes"],
        {"script": DISABLED_BOSS},
    ),
    "enable-no-test": (
        ["filter", "enable", "keep-boss", "--yes"],
        {"script": BARE_FALSE},
    ),
    "rules-disabled": (["filter", "list"], {"script": DISABLED_BOSS}),
    # #216: only the name marker changes, disabled or not.
    "rename-yes": (["filter", "rename", "keep-boss", "The boss", "--yes"], {}),
    "rename-dry": (
        ["filter", "rename", "keep-boss", "The boss", "--dry-run"],
        {},
    ),
    "rename-notty": (["filter", "rename", "keep-boss", "The boss"], {}),
    "rename-disabled": (
        ["filter", "rename", "keep-boss", "The boss", "--yes"],
        {"script": DISABLED_BOSS},
    ),
    "rename-same": (
        ["filter", "rename", "keep-boss", "keep-boss", "--yes"],
        {},
    ),
    "rename-unknown": (
        ["filter", "rename", "phantom", "The boss", "--yes"],
        {},
    ),
    "rename-taken": (
        ["filter", "rename", "keep-boss", "bin-the-noise", "--yes"],
        {},
    ),
    "rename-blank": (["filter", "rename", "keep-boss", " ", "--yes"], {}),
    "rename-unwritable": (
        ["filter", "rename", "keep-boss", "two\nlines", "--yes"],
        {},
    ),
    "rename-empty-script": (
        ["filter", "rename", "keep-boss", "The boss", "--yes"],
        {"script": ""},
    ),
    "remove-yes": (["filter", "remove", "keep-boss", "--yes"], {}),
    "remove-dry": (["filter", "remove", "keep-boss", "--dry-run"], {}),
    "remove-unknown": (["filter", "remove", "phantom", "--yes"], {}),
    "remove-notty": (["filter", "remove", "keep-boss"], {}),
    "remove-empty": (
        ["filter", "remove", "keep-boss", "--yes"],
        {"script": ""},
    ),
    # Editing a stored script that is not the active one must not switch
    # which script the server runs (#53); --activate asks for exactly that.
    "remove-other-script": (
        ["filter", "remove", "keep-boss", "--filterset", "spare", "--yes"],
        {"others": {"spare": ONE_RULE}},
    ),
    "remove-other-script-activate": (
        [
            "filter",
            "remove",
            "keep-boss",
            "--filterset",
            "spare",
            "--activate",
            "--yes",
        ],
        {"others": {"spare": ONE_RULE}},
    ),
    # #149: --like takes the criteria from a message, as 'search --like'
    # does; a criteria flag replaces what was derived for its header.
    "add-like": (
        ["filter", "add", "--like", "2", "--move-to", "Lists", "--dry-run"],
        {},
    ),
    "add-like-listid": (
        ["filter", "add", "--like", "3", "--move-to", "Lists", "--dry-run"],
        {},
    ),
    "add-like-real": (
        ["filter", "add", "--like", "3", "--move-to", "Lists"],
        {},
    ),
    "add-like-combined": (
        [
            "filter",
            "add",
            "--like",
            "2",
            "--subject",
            "Issue",
            "--match",
            "all",
            "--move-to",
            "Lists",
            "--dry-run",
        ],
        {},
    ),
    "add-like-folder": (
        [
            "filter",
            "add",
            "--like",
            "2",
            "--folder",
            "Lists",
            "--move-to",
            "Lists",
            "--dry-run",
        ],
        {},
    ),
    "add-like-derive-missing": (
        [
            "filter",
            "add",
            "--like",
            "2",
            "--derive",
            "cc,list-id",
            "--move-to",
            "Lists",
            "--dry-run",
        ],
        {},
    ),
    "add-like-nothing-derived": (
        [
            "filter",
            "add",
            "--like",
            "2",
            "--derive",
            "cc",
            "--move-to",
            "Lists",
        ],
        {},
    ),
    "add-like-missing": (
        ["filter", "add", "--like", "99", "--move-to", "Lists"],
        {},
    ),
    "add-like-noimap": (
        ["filter", "add", "--like", "2", "--move-to", "Lists", "--no-mail"],
        {},
    ),
    "add-folder-alone": (
        ["filter", "add", *GITHUB, "--folder", "Lists", "--move-to", "Lists"],
        {},
    ),
    "add-derive-alone": (
        ["filter", "add", *GITHUB, "--derive", "from", "--move-to", "Lists"],
        {},
    ),
    "apply-like": (
        ["filter", "apply", "--like", "3", "--move-to", "Lists", "--dry-run"],
        {},
    ),
    "apply-like-yes": (
        ["filter", "apply", "--like", "2", "--move-to", "Lists", "--yes"],
        {},
    ),
    "apply-like-combined": (
        [
            "filter",
            "apply",
            "--like",
            "2",
            "--subject",
            "Issue",
            "--match",
            "all",
            "--move-to",
            "Lists",
            "--dry-run",
        ],
        {},
    ),
    # #149: --filter reads the document 'search --build-filter --json'
    # prints, from a file or standard input, instead of criteria flags.
    "add-filter": (
        [
            "filter",
            "add",
            "--filter",
            "<FILE>",
            "--move-to",
            "Lists",
            "--dry-run",
        ],
        {"file": FILTER_DOC},
    ),
    "add-filter-stdin": (
        ["filter", "add", "--filter", "-", "--move-to", "Lists", "--dry-run"],
        {"stdin": FILTER_DOC},
    ),
    "add-filter-both": (
        [
            "filter",
            "add",
            "--filter",
            "<FILE>",
            *GITHUB,
            "--move-to",
            "Lists",
        ],
        {"file": FILTER_DOC},
    ),
    "add-filter-match": (
        [
            "filter",
            "add",
            "--filter",
            "<FILE>",
            "--match",
            "all",
            "--move-to",
            "L",
        ],
        {"file": FILTER_DOC},
    ),
    "add-filter-like": (
        [
            "filter",
            "add",
            "--filter",
            "<FILE>",
            "--like",
            "2",
            "--move-to",
            "Lists",
        ],
        {"file": FILTER_DOC},
    ),
    "add-filter-bad": (
        ["filter", "add", "--filter", "<FILE>", "--move-to", "Lists"],
        {"file": '{"version": 2, "criteria": {}}\n'},
    ),
    "add-filter-missing": (
        [
            "filter",
            "add",
            "--filter",
            "/nonexistent/f.json",
            "--move-to",
            "Lists",
        ],
        {},
    ),
    "apply-filter": (
        [
            "filter",
            "apply",
            "--filter",
            "<FILE>",
            "--move-to",
            "Lists",
            "--yes",
        ],
        {"file": FILTER_DOC},
    ),
    "apply-filter-stdin": (
        [
            "filter",
            "apply",
            "--filter",
            "-",
            "--move-to",
            "Lists",
            "--dry-run",
        ],
        {"stdin": FILTER_DOC},
    ),
    "apply-filter-stdin-bad": (
        [
            "filter",
            "apply",
            "--filter",
            "-",
            "--move-to",
            "Lists",
            "--dry-run",
        ],
        {"stdin": "not json\n"},
    ),
    "apply-filter-both": (
        ["filter", "apply", "--filter", "-", *GITHUB, "--move-to", "Lists"],
        {"stdin": FILTER_DOC},
    ),
    # #152: body, date, and state criteria. Dates are fixed ones, never
    # --older-than, whose date moves with the day the snapshot is run.
    "search-more": (
        [
            "mail",
            "search",
            "--body",
            "merged",
            "--since",
            "2026-09-01",
            "--before",
            "2026-09-28",
            "--unread",
            "--flagged",
        ],
        MAIL,
    ),
    "search-body-nonascii": (["mail", "search", "--body", "Café"], MAIL),
    "search-bad-date": (["mail", "search", "--since", "1/9/2026"], {}),
    "search-bad-age": (["mail", "search", "--older-than", "0d"], {}),
    "search-empty-range": (
        ["mail", "search", "--since", "2026-09-28", "--before", "2026-09-01"],
        {},
    ),
    "search-build-filter-more": (
        [
            "mail",
            "search",
            "--build-filter",
            *GITHUB,
            "--body",
            "merged",
            "--since",
            "2026-09-01",
            "--older-than",
            "3w",
            "--unread",
        ],
        {},
    ),
    # #159: --sort, by the server where it advertises SORT (SORTED) and
    # client-side where it does not; --limit is taken after sorting.
    "search-sort-size": (
        ["mail", "search", "--sort", "size", "--reverse", "--limit", "2"],
        SORTED,
    ),
    "search-sort-size-fallback": (
        ["mail", "search", "--sort", "size", "--reverse", "--limit", "2"],
        MAIL,
    ),
    "search-sort-size-json": (
        [
            "mail",
            "search",
            "--sort",
            "size",
            "--reverse",
            "--limit",
            "2",
            "--json",
        ],
        SORTED,
    ),
    "search-sort-sent": (["mail", "search", "--sort", "sent"], SORTED),
    "search-sort-received-fallback": (
        ["mail", "search", "--sort", "received", "--reverse"],
        MAIL,
    ),
    "search-sort-uids-only": (
        ["mail", "search", "--sort", "size", "--reverse", "--uids-only"],
        SORTED,
    ),
    "search-sort-from-uids-only": (
        ["mail", "search", "--sort", "size", "--uids-only", *GITHUB],
        {"mail": {6: GITHUB_LONG}, "imap_caps": SORTED["imap_caps"]},
    ),
    "search-sort-raw": (
        ["mail", "search", "--raw", "UNSEEN", "--sort", "size"],
        SORTED,
    ),
    "search-sort-nonascii": (
        [
            "mail",
            "search",
            "--subject",
            "Café",
            "--sort",
            "size",
            "--limit",
            "1",
        ],
        SORTED,
    ),
    "search-sort-bad": (["mail", "search", "--sort", "from"], {}),
    "search-reverse-alone": (["mail", "search", "--reverse"], {}),
    "search-sort-build-filter": (
        ["mail", "search", "--sort", "size", "--build-filter", *GITHUB],
        {},
    ),
    "search-build-filter-more-json": (
        [
            "mail",
            "search",
            "--build-filter",
            "--json",
            *GITHUB,
            "--body",
            "merged",
            "--before",
            "2026-09-28",
            "--older-than",
            "30d",
            "--flagged",
        ],
        {},
    ),
    "apply-more": (
        [
            "filter",
            "apply",
            "--body",
            "merged",
            "--since",
            "2026-09-01",
            "--flagged",
            "--move-to",
            "Lists",
            "--dry-run",
        ],
        MAIL,
    ),
    "add-body": (
        [
            "filter",
            "add",
            *GITHUB,
            "--body",
            "merged",
            "--move-to",
            "Lists",
            "--dry-run",
        ],
        {"caps": [*FULL, "body"]},
    ),
    "add-body-json": (
        [
            "filter",
            "add",
            *GITHUB,
            "--body",
            "merged",
            "--move-to",
            "Lists",
            "--dry-run",
            "--json",
        ],
        {"caps": [*FULL, "body"]},
    ),
    "apply-more-json": (
        [
            "filter",
            "apply",
            "--body",
            "merged",
            "--since",
            "2026-09-01",
            "--before",
            "2026-09-28",
            "--flagged",
            "--move-to",
            "Lists",
            "--dry-run",
            "--json",
        ],
        MAIL,
    ),
    "search-more-json": (
        [
            "mail",
            "search",
            "--body",
            "merged",
            "--unread",
            "--since",
            "2026-09-01",
            "--json",
        ],
        MAIL,
    ),
    "add-body-unadvertised": (
        [
            "filter",
            "add",
            "--body",
            "merged",
            "--move-to",
            "Lists",
            "--dry-run",
        ],
        {},
    ),
    "add-body-disabled": (
        [
            "filter",
            "add",
            "--body",
            "merged",
            "--disable-extension",
            "body",
            "--move-to",
            "Lists",
            "--dry-run",
        ],
        {"caps": [*FULL, "body"]},
    ),
    "add-body-compare-is": (
        [
            "filter",
            "add",
            "--body",
            "merged",
            "--compare",
            "is",
            "--move-to",
            "L",
        ],
        {},
    ),
    "add-state-refused": (
        ["filter", "add", *GITHUB, "--unread", "--move-to", "Lists"],
        {},
    ),
    "add-before-is-placement": (
        [
            "filter",
            "add",
            *GITHUB,
            "--before",
            "2026-09-01",
            "--move-to",
            "Lists",
        ],
        {},
    ),
    # #196: an empty anchor is refused before connecting, not appended.
    "add-before-empty": (
        ["filter", "add", *GITHUB, "--before", "", "--move-to", "Lists"],
        {},
    ),
    "add-filter-state-refused": (
        ["filter", "add", "--filter", "<FILE>", "--move-to", "Lists"],
        {
            "file": FILTER_DOC.replace(
                '"compare"', '"since": "2026-09-01",\n    "compare"'
            )
        },
    ),
    # #82: disabled_extensions, from a flag and from the config file, and
    # what 'add' does with it -- refuse, or fall back to IMAP creation.
    "test-disabled": (
        [
            "server",
            "test",
            "--disable-extension",
            "Mailbox",
            "--disable-extension",
            "copy",
        ],
        {},
    ),
    "test-disabled-config": (
        ["server", "test"],
        {"config": 'disabled_extensions = ["imap4flags"]\n'},
    ),
    # #85: 'none' from the flag clears the config file's list, and says so.
    "test-disabled-none": (
        ["server", "test", "--disable-extension", "none"],
        {"config": 'disabled_extensions = ["imap4flags"]\n'},
    ),
    "test-disabled-none-mixed": (
        [
            "server",
            "test",
            "--disable-extension",
            "none",
            "--disable-extension",
            "copy",
        ],
        {},
    ),
    "test-disabled-unknown": (
        ["server", "test", "--disable-extension", "mailbx"],
        {},
    ),
    # A name only the server lists gets a row, folded to lower case once.
    "test-server-only": (
        ["server", "test"],
        {"caps": ["FileInto", "fileinto", "Body", "imap4flags"]},
    ),
    "test-no-extensions": (["server", "test"], {"caps": []}),
    # #19: every kind of drift, a hostile capability value among them.
    "check-baseline-drift": (["server", "baseline", "check"], DRIFTED),
    "check-baseline-drift-json": (
        ["server", "baseline", "check", "--json"],
        DRIFTED,
    ),
    "save-baseline-drift-dry": (
        ["server", "baseline", "save", "--dry-run"],
        DRIFTED,
    ),
    "add-disabled-mailbox": (
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "Lists/GitHub",
            "--create-folder",
            "--disable-extension",
            "mailbox",
        ],
        {},
    ),
    "add-disabled-mailbox-noimap": (
        [
            "filter",
            "add",
            *GITHUB,
            "--move-to",
            "Lists/GitHub",
            "--create-folder",
            "--no-mail",
            "--disable-extension",
            "mailbox",
        ],
        {},
    ),
    "add-disabled-imap4flags": (
        [
            "filter",
            "add",
            *GITHUB,
            "--mark-read",
            "--move-to",
            "Lists",
            "--disable-extension",
            "imap4flags",
        ],
        {},
    ),
    # #5: a folder renamed, its subfolder with it, its subscriptions
    # carried, and the rules filing into either repointed.
    "rename-folder-dry": ([*RENAME_ARGS, "--dry-run"], RENAME),
    "rename-folder-dry-json": ([*RENAME_ARGS, "--dry-run", "--json"], RENAME),
    "rename-folder-yes": ([*RENAME_ARGS, "--yes"], RENAME),
    "rename-folder-notty": (RENAME_ARGS, RENAME),
    "rename-folder-no-rules": (
        ["folder", "rename", "spam", "Junk", "--yes"],
        {},
    ),
    "rename-folder-inbox": (["folder", "rename", "INBOX", "Archive"], {}),
    "rename-folder-missing": (["folder", "rename", "lists", "Archive"], {}),
    "rename-folder-exists": (["folder", "rename", "Lists", "spam"], {}),
    "rename-folder-case-clash": (["folder", "rename", "Lists", "Spam"], {}),
    "rename-folder-rejected": (
        [*RENAME_ARGS, "--yes"],
        {**RENAME, "reject": True},
    ),
    "rename-folder-rename-fails": (
        [*RENAME_ARGS, "--yes"],
        {**RENAME, "imap_failures": {"rename_folder": "NO [INUSE]"}},
    ),
    "rename-folder-subscribe-fails": (
        [*RENAME_ARGS, "--yes"],
        {**RENAME, "imap_failures": {"subscribe_folder": "NO quota"}},
    ),
    # #21: proposals shown, applied, refused, and each kind skipped.
    "optimize-dry": (["filter", "optimize", "--dry-run"], OPTIMIZE),
    "optimize-dry-json": (
        ["filter", "optimize", "--dry-run", "--json"],
        OPTIMIZE,
    ),
    "optimize-yes": (["filter", "optimize", "--yes"], OPTIMIZE),
    "optimize-notty": (["filter", "optimize"], OPTIMIZE),
    "optimize-rejected": (
        ["filter", "optimize", "--yes"],
        {**OPTIMIZE, "reject": True},
    ),
    "optimize-skip": (
        [
            "filter",
            "optimize",
            "--skip",
            "merge",
            "--skip",
            "redundant",
            "--dry-run",
        ],
        OPTIMIZE,
    ),
    "optimize-skip-unknown": (
        ["filter", "optimize", "--skip", "tidy", "--dry-run"],
        OPTIMIZE,
    ),
    "optimize-nothing": (["filter", "optimize", "--yes"], {}),
    "optimize-empty": (["filter", "optimize", "--dry-run"], {"script": ""}),
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
        self._bytes = io.BytesIO()
        super().__init__(self._bytes, encoding="utf-8", newline="")
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty

    def text(self) -> str:
        self.flush()

        return self._bytes.getvalue().decode("utf-8")


# ----------------------------------------------------------------------------
def save_baseline(edit, sieve, imap) -> None:
    """Save a baseline through the CLI, then edit it; forget its calls."""
    quiet = io.StringIO()

    with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
        code = cli.main(["server", "baseline", "save", "--yes"])

    assert code == 0, quiet.getvalue()

    path = (
        Path(os.environ["XDG_CONFIG_HOME"])
        / "mailctl"
        / "baselines"
        / "mail.example.com.json"
    )

    if edit is not None:
        document = json.loads(path.read_text(encoding="utf-8"))
        text = edit(document)

        if text is None:
            text = json.dumps(document, indent=2) + "\n"

        path.write_text(text, encoding="utf-8")

    sieve.calls.clear()
    imap.calls.clear()


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
        options.get("sieve_extra", b""),
        options.get("sieve_warnings"),
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
    imap.folder_messages = options.get("folder_mail", {})

    if "imap_caps" in options:
        imap.caps = set(options["imap_caps"])

    if "counts" in options:
        imap.counts = options["counts"]

    if "imap_id" in options:
        imap.id_response = options["imap_id"]

    for folder, subscribed in options.get("folders", ()):
        imap.listing.append(((), b".", folder.encode()))

        if subscribed:
            imap.subscriptions.append(((), b".", folder.encode()))

    for method, text in options.get("imap_failures", {}).items():
        imap.failures[method] = IMAPClientError(text)

    if "imap_welcome" in options:
        imap.welcome = options["imap_welcome"]

    imap.responses = dict(options.get("imap_responses", {}))

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
    monkeypatch.setenv(
        "MAILCTL_PASSWORD", options.get("password", "not-a-real-password")
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(options.get("stdin", "")))

    if options.get("presave"):
        save_baseline(options.get("baseline_edit"), sieve, imap)

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

        text = re.sub(r"\d{8}T\d{6}(\.\d+)?Z?", "<STAMP>", text)

        # 'probe --report' dates itself by the day, and names this mailctl.
        text = re.sub(r"(Probed: )\d{4}-\d\d-\d\d", r"\1<DATE>", text)
        text = text.replace(f"mailctl `{__version__}`", "mailctl `<VERSION>`")

        # 'probe' dates itself, extended form.
        return re.sub(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", "<STAMP>", text)

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
@pytest.mark.parametrize(
    "argv",
    [
        ["server", "probe"],
        ["server", "probe", "--json"],
        ["server", "probe", "-v"],
        ["server", "probe", "--json", "-v"],
    ],
    ids=" ".join,
)
def test_probe_never_prints_the_password(
    argv, fake_imap, roundcube_script, monkeypatch, tmp_path
):
    """#101: nothing of the credential reaches stdout, stderr, or the
    document -- verbose protocol chatter included. The login is checked to
    have been handed the sentinel, so the absence is not vacuous."""
    sentinel = "s3ntinel-PROBE-never-shown-4b1d"
    logins = []
    login = fake_imap.login

    def recording_login(user, password):
        logins.append(password == sentinel)
        login(user, password)

    monkeypatch.setattr(fake_imap, "login", recording_login)

    actual = run_scenario(
        argv,
        {**PROBE, "password": sentinel},
        fake_imap,
        roundcube_script,
        monkeypatch,
        tmp_path,
    )

    assert logins == [True]
    assert "exit: 0" in actual
    assert sentinel not in actual


# ----------------------------------------------------------------------------
def outputs(record: str) -> tuple[str, str, str]:
    """The exit, stdout, and stderr of a rendered scenario."""
    head, rest = record.split("\n--- stdout\n", 1)
    stdout, rest = rest.split("\n--- stderr\n", 1)
    stderr = rest.split("\n--- sieve calls", 1)[0]

    return head.splitlines()[-1].removeprefix("exit: "), stdout, stderr


MACHINE = sorted(
    name
    for name, (argv, _) in SCENARIOS.items()
    if "--json" in argv or "--uids-only" in argv
)


# ----------------------------------------------------------------------------
def test_the_machine_output_scenarios_are_found():
    """Vacuous if the filter matched nothing."""
    assert "folders-json" in MACHINE
    assert "search-uids-only" in MACHINE
    assert "add-json-nodry" in MACHINE


# Non-zero exits that are a result, not a failure: the document is still
# printed. 'server baseline check' exits 3 or 4 on drift (#19).
RESULT_EXITS = {"server baseline check": ("3", "4")}


# Documents with a version of their own; every other --json document, and
# every failure line, carries json_output.JSON_VERSION.
OWN_VERSIONS = {
    "server probe": reports.PROBE_VERSION,
    "server baseline check": cli.DRIFT_VERSION,
    "server baseline show": baseline.BASELINE_VERSION,
}


# ----------------------------------------------------------------------------
def document_version(argv: list[str]) -> int:
    """The version the document ``argv`` prints on success carries."""
    if "--build-filter" in argv:
        return criteria.FILTER_VERSION

    for size in (3, 2):
        if " ".join(argv[:size]) in OWN_VERSIONS:
            return OWN_VERSIONS[" ".join(argv[:size])]

    return json_output.JSON_VERSION


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", MACHINE)
def test_stdout_holds_only_the_data(
    name, fake_imap, roundcube_script, monkeypatch, tmp_path
):
    """#151: under --json stdout is one document and nothing else, and a
    failure is one JSON line, the last on stderr; under --uids-only stdout
    is UIDs alone."""
    argv, options = SCENARIOS[name]
    code, stdout, stderr = outputs(
        run_scenario(
            argv, options, fake_imap, roundcube_script, monkeypatch, tmp_path
        )
    )

    if code.startswith("SystemExit"):
        # argparse's own usage error, before any output is chosen.
        assert stdout == ""

    elif "--uids-only" in argv and code == "0":
        assert all(line.isdigit() for line in stdout.splitlines()), stdout

    elif code == "0" or code in RESULT_EXITS.get(" ".join(argv[:3]), ()):
        assert json.loads(stdout)["version"] == document_version(argv)

    elif "--json" not in argv:
        # --uids-only is not JSON, so neither is its failure.
        assert stdout == ""
        assert stderr.startswith("mailctl: ")

    else:
        assert stdout == ""

        failure = json.loads(stderr.rstrip("\n").splitlines()[-1])

        assert failure["version"] == json_output.JSON_VERSION
        assert set(failure) == {"version", "error"}
        assert failure["error"]["message"]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", MACHINE)
def test_no_output_carries_the_password(
    name, fake_imap, roundcube_script, monkeypatch, tmp_path
):
    """A Secret is never serialised: the password each run is given
    appears nowhere it prints, document or error."""
    sentinel = "s3ntinel-VALUE-never-shown-151"
    argv, options = SCENARIOS[name]

    record = run_scenario(
        argv,
        {**options, "password": sentinel},
        fake_imap,
        roundcube_script,
        monkeypatch,
        tmp_path,
    )

    assert sentinel not in record


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("argv", "options"),
    [
        (["server", "baseline", "save"], PROBE),
        (["server", "baseline", "save", "--yes", "-v"], DRIFTED),
        (["server", "baseline", "show"], BASELINE),
        (["server", "baseline", "check", "-v"], DRIFTED),
        (["server", "baseline", "check", "--json", "-v"], DRIFTED),
        (["server", "test"], DRIFTED),
    ],
    ids=lambda value: " ".join(value) if isinstance(value, list) else "",
)
def test_a_baseline_never_holds_or_prints_the_password(
    argv, options, fake_imap, roundcube_script, monkeypatch, tmp_path
):
    """#18: nothing of the credential reaches the saved file, stdout, or
    stderr. The login is checked to have been handed the sentinel, so the
    absence is not vacuous."""
    sentinel = "s3ntinel-BASELINE-never-shown-7e0a"
    logins = []
    login = fake_imap.login

    def recording_login(user, password):
        logins.append(password == sentinel)
        login(user, password)

    monkeypatch.setattr(fake_imap, "login", recording_login)

    actual = run_scenario(
        argv,
        {**options, "password": sentinel},
        fake_imap,
        roundcube_script,
        monkeypatch,
        tmp_path,
    )
    saved = tmp_path / "config" / "mailctl" / "baselines"
    files = list(saved.glob("*.json"))

    assert logins and all(logins)
    assert files, "no baseline file was written -- the check reads nothing"
    assert sentinel not in actual

    for path in files:
        assert sentinel not in path.read_text(encoding="utf-8")


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "argv",
    [
        ["server", "baseline", "check", "--json"],
        ["server", "baseline", "check", "--json", "-v"],
        ["server", "baseline", "show", "--json"],
    ],
    ids=" ".join,
)
def test_a_baseline_json_document_is_all_of_stdout(
    argv, fake_imap, roundcube_script, monkeypatch, tmp_path
):
    """Red if anything but the document reaches stdout -- --verbose's
    protocol chatter included -- so a script can parse it whole."""
    actual = run_scenario(
        argv, DRIFTED, fake_imap, roundcube_script, monkeypatch, tmp_path
    )
    stdout = actual.split("--- stdout\n", 1)[1].split("\n--- stderr", 1)[0]

    assert json.loads(stdout)["version"] == 1


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
