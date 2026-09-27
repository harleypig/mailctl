"""Whole-command snapshots: what each CLI invocation prints and does.

Every scenario runs ``cli.main`` end to end against a ManageSieve fake and
the conftest IMAP double, then records the exit code, stdout, stderr, and
the exact calls each server was asked to make -- including the script that
was uploaded. The record is compared with ``tests/snapshots/cli/<name>.txt``.

This is the guard for the engine/front-end split: a refactor that changes
nothing a user sees leaves every snapshot untouched, and a deliberate
behaviour change shows up as a reviewable snapshot diff in the same commit.

Regenerate after an intended change with::

    MXFILTER_UPDATE_SNAPSHOTS=1 pytest tests/test_cli_snapshots.py

and read the diff before committing it.
"""

import io
import os
import re
import sys
from pathlib import Path

import pytest

from mxfilter import cli
from mxfilter import sieve as sieve_module

SNAPSHOTS = Path(__file__).parent / "snapshots" / "cli"


# ----------------------------------------------------------------------------
def update_requested(environ) -> bool:
    """Whether to rewrite the snapshots rather than check them.

    Refused under CI: a run that rewrites every snapshot then compares it
    with itself passes whatever the output is, so a CI job inheriting the
    variable would stop checking anything while staying green.
    """
    if environ.get("MXFILTER_UPDATE_SNAPSHOTS") != "1":
        return False

    if environ.get("CI"):
        pytest.fail(
            "MXFILTER_UPDATE_SNAPSHOTS=1 under CI would rewrite every "
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
    """A stand-in for ``sievelib.managesieve.Client`` with a script store."""

    # ------------------------------------------------------------------------
    def __init__(self, caps, active, script, reject):
        self.caps = caps
        self.scripts = {} if active is None else {active: script}
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

        return (self.active, others)

    # ------------------------------------------------------------------------
    def getscript(self, name):
        self.calls.append(("getscript", name))

        return self.scripts.get(name)

    # ------------------------------------------------------------------------
    def get_sieve_capabilities(self):
        return list(self.caps)

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

# name -> (argv, options). Options: caps, active, script, reject, config
# (the text of config.toml), file (the text of a file that "<FILE>" in
# argv is replaced with the path of), env (the text of a .env written,
# mode 0600, into the directory the command runs in), and mail / flags
# (extra messages and their IMAP flags, by UID).

# Host from the env file over the exported MXROUTE_HOST, port from a flag
# over the env file, TLS from the config file, and the password named
# indirectly -- one rung each, so the report has to tell them apart.
ENV_FILE = """# written by hand
export MXROUTE_HOST=mail.from-env-file.example
MXROUTE_SIEVE_PORT='4191'
MXROUTE_PASSWORD_CMD="printf %s not-a-real-password"
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

SCENARIOS = {
    "messages": (["messages"], MAIL),
    "messages-from": (["messages", *GITHUB], MAIL),
    "messages-limit": (["messages", "--limit", "2"], MAIL),
    "messages-search": (["messages", "--search", "UNSEEN"], MAIL),
    "messages-both": (["messages", *GITHUB, "--search", "ALL"], MAIL),
    "messages-none": (["messages", "--from", "nobody@x.y"], MAIL),
    "messages-folder": (["messages", "--folder", "Lists"], MAIL),
    "view": (["view", "4"], MAIL),
    "view-html": (["view", "5"], MAIL),
    "view-headers": (["view", "4", "--headers-only"], MAIL),
    "view-raw": (["view", "5", "--raw"], MAIL),
    "view-missing": (["view", "99"], MAIL),
    "list": (["list"], {}),
    "list-verbose": (["list", "--verbose"], {}),
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
    "backup-dry": (["backup", "--dry-run"], {}),
    "backup-noactive": (["backup"], {"active": None}),
    "subscribe": (["subscribe", "spam"], {}),
    "subscribe-dry": (["subscribe", "INBOX.spam", "--dry-run"], {}),
    "subscribe-already": (["subscribe", "Lists"], {}),
    "subscribe-missing": (["subscribe", "Nowhere"], {}),
    "unsubscribe": (["unsubscribe", "Lists"], {}),
    "unsubscribe-already": (["unsubscribe", "spam"], {}),
    "unsubscribe-missing": (["unsubscribe", "Nowhere"], {}),
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
    "remove-yes": (["remove-rule", "keep-boss", "--yes"], {}),
    "remove-dry": (["remove-rule", "keep-boss", "--dry-run"], {}),
    "remove-unknown": (["remove-rule", "phantom", "--yes"], {}),
    "remove-notty": (["remove-rule", "keep-boss"], {}),
    "remove-empty": (["remove-rule", "keep-boss", "--yes"], {"script": ""}),
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
}

# ############################################################################
# Running and recording
# ############################################################################


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

    monkeypatch.setattr(sieve_module, "Client", lambda *a, **k: sieve)
    if "file" in options:
        restore_file = tmp_path / "restore.sieve"
        text = script if options["file"] == "SAME" else options["file"]
        restore_file.write_text(text, encoding="utf-8")
        argv = [str(restore_file) if arg == "<FILE>" else arg for arg in argv]

    if "env" in options:
        env_file = tmp_path / ".env"
        env_file.write_text(options["env"], encoding="utf-8")
        env_file.chmod(0o600)
        monkeypatch.chdir(tmp_path)

    if "config" in options:
        config_dir = Path(os.environ["XDG_CONFIG_HOME"]) / "mxfilter"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.toml").write_text(options["config"])

    monkeypatch.setenv("MXROUTE_HOST", "mail.example.com")
    monkeypatch.setenv("MXROUTE_USER", "user@example.com")
    monkeypatch.setenv("MXROUTE_PASSWORD", "not-a-real-password")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    out, err = io.StringIO(), io.StringIO()
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
        scrub(f"$ mxfilter {' '.join(argv)}"),
        f"exit: {code}",
        "--- stdout",
        scrub(out.getvalue()),
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
        f"no snapshot for {name!r}; run with MXFILTER_UPDATE_SNAPSHOTS=1"
    )
    assert actual == snapshot.read_text(encoding="utf-8")


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
        pytest.param({"MXFILTER_UPDATE_SNAPSHOTS": "1"}, True, id="local"),
    ],
)
def test_snapshot_rewriting_is_opt_in(environ, expected):
    assert update_requested(environ) is expected


# ----------------------------------------------------------------------------
def test_snapshot_rewriting_is_refused_under_ci():
    """#55: CI inheriting the variable would turn the tier into a no-op."""
    environ = {"MXFILTER_UPDATE_SNAPSHOTS": "1", "CI": "true"}

    with pytest.raises(pytest.fail.Exception, match="under CI"):
        update_requested(environ)
