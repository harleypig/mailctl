#!/usr/bin/env python3
"""A stand-in for ``mailctl`` that scripts/live-readonly.sh can drive offline.

It prints canned output shaped like the real CLI's (see
``tests/snapshots/cli``), appends each argv to ``$STUB_LOG`` as a JSON line,
and never touches a network. ``$STUB_BREAK`` is a comma-separated list of
faults to inject, so a test can watch a check go red:

- ``two-active``      -- ``list`` shows two active scripts
- ``no-unread``       -- no message is unread
- ``view-marks-read`` -- ``view`` marks its message read, as a regression would
- ``drift``           -- ``folders`` changes between calls
- ``password-unset``  -- ``test`` reports the password unset
- ``bad-marker``      -- a ``# rule:[`` marker ``show`` cannot close
- ``backup-writes``   -- ``backup --dry-run`` writes its file anyway
- ``backup-writes-elsewhere`` -- it writes some other file beside it
"""

import json
import os
import sys
from pathlib import Path

ARGV = sys.argv[1:]
BREAK = set(filter(None, os.environ.get("STUB_BREAK", "").split(",")))
STATE = Path(os.environ["STUB_LOG"]).with_suffix(".state")

SCRIPT = """\
# ---- managesieve ----
require ["fileinto"];
# rule:[keep boss]
if header :contains "from" "boss@example.com"
{
\tfileinto "INBOX.Boss";
\tstop;
}
# rule:[bin-the-noise]
if header :contains "subject" "newsletter"
{
\tfileinto "INBOX.Noise";
\tstop;
}
# ---- 2 rule(s): keep boss, bin-the-noise
"""

RULES = """\
Script 'managesieve':

2 rule(s), in evaluation order:

  1. keep boss  [stop]
       when:  From contains 'boss@example.com'
       then:  fileinto, stop

  2. bin-the-noise  [stop]
       when:  Subject contains 'newsletter'
       then:  fileinto, stop

No rule is shadowed by an earlier one.
"""

TEST = """\
Sources:   environment
Provider:  mxroute  (default)
{password}

ManageSieve: connected
  active script: managesieve

IMAP: connected
  delimiter: '.'
""".format(
    # The shape a live run printed on 2026-09-28, path aside.
    password="Password:  set (via file)  (password_file, config file "
    "/home/u/.config/mailctl/config.toml)"
)

DIFF = """\
--- sieve diff ---
--- managesieve (current)
+++ managesieve (proposed)
@@ -1,3 +1,8 @@
+# rule:[x]
--- end diff ---

[dry-run] the script was NOT uploaded.
"""


# ----------------------------------------------------------------------------
def option(name: str) -> str | None:
    """The value after ``name`` in the argv, if it is there."""
    if name in ARGV:
        return ARGV[ARGV.index(name) + 1]

    return None


# ----------------------------------------------------------------------------
def show() -> str:
    """The script as the live server sends it: CRLF, banner lines aside."""
    lines = SCRIPT.splitlines()

    if "bad-marker" in BREAK:
        lines = [line.replace("]", "") for line in lines]

    body = "".join(f"{line}\r\n" for line in lines[1:-1])

    return f"{lines[0]}\n{body}{lines[-1]}\n"


# ----------------------------------------------------------------------------
def folders() -> str:
    names = ["INBOX", "INBOX.Lists", "INBOX.spam"]

    if "drift" in BREAK:
        calls = int(STATE.read_text()) if STATE.exists() else 0
        STATE.write_text(str(calls + 1))
        names += [f"INBOX.Drift{n}" for n in range(calls)]

    lines = [
        "Hierarchy delimiter: '.'",
        f"{len(names)} folder(s), {len(names) - 1} subscribed "
        "(webmail shows only subscribed folders):",
    ]
    width = max(len(name) for name in names)

    for name in names:
        if name == "INBOX.spam":
            lines.append(f"  {name:<{width}}  (not subscribed)")

        else:
            lines.append(f"  {name}")

    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------
def search() -> str:
    read_uid = STATE.read_text() if STATE.exists() else ""
    rows = []

    for uid in (5, 4, 3, 2, 1):
        unread = uid != 1 and "no-unread" not in BREAK
        mark = "N" if unread and str(uid) != read_uid else ""
        rows.append(
            f"{uid:>8}  {'2026-02-03 04:05:06':<19}  {'131B':>6}  "
            f"{mark:<4}  {'News <news@example.com>':<28}  Weekly"
        )

    rows = rows[: int(option("--limit") or 50)]
    header = (
        f"{'UID':>8}  {'Received':<19}  {'Size':>6}  {'Mark':<4}  "
        f"{'From':<28}  Subject"
    )
    legend = "Marks: N unread, F flagged, R replied, D deleted, @ attachment"
    lines = [
        f"{len(rows)} message(s) in 'INBOX', newest first:",
        header,
        *rows,
        legend,
    ]

    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------
def view() -> str:
    uid = ARGV[1]

    if "view-marks-read" in BREAK:
        STATE.write_text(uid)

    return (
        f"Message uid {uid} in 'INBOX' (131B; no flags):\n"
        "  From:    News <news@example.com>\n"
        "  Subject: Weekly\n"
    )


# ----------------------------------------------------------------------------
def backup() -> str:
    target = Path(os.environ["STUB_BACKUP_DIR"]) / "managesieve-1.sieve"

    if "backup-writes" in BREAK:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("require [];\n")

    if "backup-writes-elsewhere" in BREAK:
        target.parent.mkdir(parents=True, exist_ok=True)
        (target.parent / "managesieve-2.sieve").write_text("require [];\n")

    return f"[dry-run] would write 2 rule(s) to {target}\n"


# ----------------------------------------------------------------------------
def add() -> str:
    folder = option("--fileinto")
    out = ""

    if "--create-folder" in ARGV:
        out += f"[dry-run] would create IMAP folder '{folder}'\n"

    subject = option("--subject")
    name = f"subject-{subject.lower()}" if subject else "from-probe"

    return out + f"\nRule '{name}' on script 'managesieve':\n\n" + DIFF


# ----------------------------------------------------------------------------
def main() -> int:
    with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps(ARGV) + "\n")

    command = ARGV[0]

    if command == "list":
        out = "* managesieve   (active)\n"

        if "two-active" in BREAK:
            out += "* other   (active)\n"

    elif command == "test":
        out = TEST

        if "password-unset" in BREAK:
            out = out.replace("Password:  set (via file)", "Password:  unset")

    elif command == "show":
        out = show()

    elif command == "rules":
        out = RULES

    elif command == "folders":
        out = folders()

    elif command == "search":
        out = search()

    elif command == "view":
        out = view()

    elif command == "backup":
        out = backup()

    elif command == "apply":
        out = "Searching 'INBOX' for existing matches...\n"
        out += "[dry-run] would move 1 message(s) to 'INBOX.Lists'\n"

    elif command in ("add", "from-message"):
        out = add()

    elif command == "remove-rule":
        out = DIFF

    elif command == "subscribe":
        out = "'INBOX' is already subscribed; nothing to change.\n"

    else:
        print(f"fake mailctl: no canned output for {command}", file=sys.stderr)

        return 2

    sys.stdout.write(out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
