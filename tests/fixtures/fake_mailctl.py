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
- ``like-drops-source`` -- ``search --like`` does not list its own message
- ``unread-lists-read`` -- ``search --unread`` lists a message already read
- ``filter-on-stdout`` -- ``--build-filter --json`` shows the message on
  stdout, ahead of the document
- ``switch-uploads``  -- ``disable-rule --dry-run`` uploads anyway
- ``mark-writes``     -- ``mark --dry-run`` flags its message anyway
- ``mark-reports-change`` -- ``mark --dry-run`` says it marked something
- ``probe-not-json``  -- ``probe --json`` prints the human report instead
- ``probe-no-mail``   -- ``probe --json`` has no mail section
- ``json-noise``      -- ``folders --json`` prints a line ahead of the
  document
- ``counts-missing``  -- ``folders --counts --json`` leaves a folder's
  unread count null
- ``no-list-status``  -- ``folders --counts`` is refused, the server not
  advertising LIST-STATUS
- ``sort-unordered``  -- ``search --sort size --reverse`` lists a smaller
  message ahead of a larger one
- ``no-baseline``     -- no baseline has been saved (``show-baseline``
  and ``check-baseline`` exit 1)
- ``baseline-info``   -- ``check-baseline`` finds informational drift (3)
- ``baseline-serious`` -- ``check-baseline`` finds serious drift (4)
- ``senders-unsorted`` -- ``senders --json`` lists a quieter sender first
- ``senders-unread-over`` -- a ``senders --json`` row has more unread than
  total
- ``senders-overcap`` -- ``senders`` is refused, the search finding more
  than ``--max-messages``
- ``optimize-uploads`` -- ``optimize-rules --dry-run`` uploads anyway
- ``optimize-no-diff`` -- an ``optimize-rules --json`` plan proposes a
  change but carries no diff
- ``uidvalidity-null`` -- ``search --json`` reports no uidvalidity
- ``uidvalidity-ignored`` -- ``view --uidvalidity`` shows the message
  whatever value it is given
"""

import json
import os
import sys
from pathlib import Path

# The real CLI's commands, grouped noun first (#219), and the name each had
# before, which the canned output below is keyed by. A command line starting
# with neither is refused, as the real CLI refuses an old name, so a check
# still calling one goes red.
GROUPED = {
    "mail search": "search",
    "mail view": "view",
    "mail mark": "mark",
    "mail senders": "senders",
    "folder list": "folders",
    "folder create": "create-folder",
    "folder rename": "rename-folder",
    "folder subscribe": "subscribe",
    "folder unsubscribe": "unsubscribe",
    "filter list": "rules",
    "filter add": "add",
    "filter apply": "apply",
    "filter remove": "remove-rule",
    "filter move": "move-rule",
    "filter rename": "rename-rule",
    "filter enable": "enable-rule",
    "filter disable": "disable-rule",
    "filter optimize": "optimize-rules",
    "filterset list": "list",
    "filterset show": "show",
    "filterset backup": "backup",
    "filterset restore": "restore",
    "server test": "test",
    "server probe": "probe",
    "server baseline save": "save-baseline",
    "server baseline show": "show-baseline",
    "server baseline check": "check-baseline",
    "config migrate": "migrate-config",
}


# ----------------------------------------------------------------------------
def ungrouped(argv: list[str]) -> list[str] | None:
    """``argv`` with its command's path swapped for its old name; None
    where it names no command."""
    for size in (3, 2):
        path = " ".join(argv[:size])

        if path in GROUPED:
            return [GROUPED[path], *argv[size:]]

    return None


GIVEN = sys.argv[1:]
ARGV = ungrouped(GIVEN) or []
BREAK = set(filter(None, os.environ.get("STUB_BREAK", "").split(",")))
STATE = Path(os.environ["STUB_LOG"]).with_suffix(".state")
FLAGGED = Path(os.environ["STUB_LOG"]).with_suffix(".flagged")

# What INBOX's UIDs are valid under.
UIDVALIDITY = 1727000000

SCRIPT = """\
--- filter set 'managesieve' ---
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
--- 2 filter(s): keep boss, bin-the-noise ---
"""

RULES = """\
Script 'managesieve':

2 filter(s), in evaluation order:

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
--- diff ---
--- managesieve (current)
+++ managesieve (proposed)
@@ -1,3 +1,8 @@
+# rule:[x]
--- end diff ---

[dry-run] the filter set was NOT uploaded.
"""


# The shape of 'mailctl server probe --json'
# (tests/snapshots/cli/probe-json.txt), cut down.
PROBE = {
    "version": 1,
    "taken": "2026-09-29T14:30:05Z",
    "provider": "mxroute",
    "endpoints": [{"label": "IMAP", "value": "mail.example.com:993"}],
    "rules": {
        "identity": {"implementation": "Dovecot Pigeonhole"},
        "capabilities_after_login": False,
        "capabilities": [{"name": "SASL", "value": "PLAIN"}],
        "extensions": ["fileinto"],
        "active_rule_set": "managesieve",
    },
    "mail": {
        "identity": {"name": "Dovecot"},
        "capabilities_after_login": True,
        "capabilities": [{"name": "IMAP4REV1", "value": None}],
        "delimiter": ".",
        "namespaces": [{"kind": "personal", "prefix": "", "delimiter": "."}],
    },
}


# ----------------------------------------------------------------------------
def probe() -> str:
    if "probe-not-json" in BREAK:
        return "Probe of mxroute, taken 2026-09-29T14:30:05Z\n"

    document = dict(PROBE)

    if "probe-no-mail" in BREAK:
        document["mail"] = None

    return json.dumps(document, indent=2) + "\n"


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


FILTER = """\
{
  "version": 1,
  "criteria": {
    "match": "any",
    "compare": "contains",
    "terms": [
      {
        "header": "From",
        "value": "news@example.com"
      }
    ]
  }
}
"""


# ----------------------------------------------------------------------------
def search() -> str:
    like = option("--like")
    shown = (
        f"Message uid {like} in 'INBOX':\n"
        "  From:    News <news@example.com>\n"
        "  Subject: Weekly\n"
    )

    if like and "--build-filter" in ARGV:
        if "filter-on-stdout" in BREAK:
            return shown + "\n" + FILTER

        sys.stderr.write(shown)

        return FILTER

    read_uid = STATE.read_text() if STATE.exists() else ""
    flagged_uid = FLAGGED.read_text() if FLAGGED.exists() else ""
    rows = []

    for uid in (5, 4, 3, 2, 1):
        if like == str(uid) and "like-drops-source" in BREAK:
            continue

        unread = uid != 1 and "no-unread" not in BREAK
        mark = "N" if unread and str(uid) != read_uid else ""
        mark += "F" if str(uid) == flagged_uid else ""

        # The server narrows --unread; the fault is one that does not.
        if (
            "--unread" in ARGV
            and "N" not in mark
            and "unread-lists-read" not in BREAK
        ):
            continue

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
        *(
            [shown, "Criteria: From contains 'news@example.com'", ""]
            if like
            else []
        ),
        f"{len(rows)} message(s) in 'INBOX' (UIDVALIDITY {UIDVALIDITY}), "
        "newest first:",
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
def mark() -> str:
    uid = next(arg for arg in ARGV[1:] if arg.isdigit())

    if "mark-writes" in BREAK:
        FLAGGED.write_text(uid)

    if "mark-reports-change" in BREAK:
        return "Marked 1 message(s) in 'INBOX'.\n"

    return (
        "Marking in 'INBOX': set \\Flagged\n"
        f"  uid {uid:<8} set \\Flagged  (now: no flags)\n"
        "[dry-run] would mark 1 message(s); none was marked.\n"
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

    return f"[dry-run] would write 2 filter(s) to {target}\n"


# ----------------------------------------------------------------------------
def add() -> str:
    folder = option("--move-to")
    out = ""

    if "--create-folder" in ARGV:
        out += f"[dry-run] would create folder '{folder}'\n"

    subject = option("--subject")
    name = f"subject-{subject.lower()}" if subject else "from-probe"

    return out + f"\nFilter '{name}' in filter set 'managesieve':\n\n" + DIFF


# ----------------------------------------------------------------------------
def counted() -> list[dict]:
    """The folders of 'folders --counts --json', each with its counts."""
    folders = [
        {"name": name, "subscribed": True, "messages": total}
        | {"unseen": unseen, "size": size}
        for name, total, unseen, size in (
            ("INBOX", 12, 3, 40960),
            ("INBOX.Lists", 0, 0, 0),
        )
    ]

    if "counts-missing" in BREAK:
        folders[1]["unseen"] = None

    return folders


# ----------------------------------------------------------------------------
def senders() -> dict:
    """The body of 'senders --json', busiest first."""
    rows = [
        {"key": key, "name": "", "total": total, "unread": unread}
        | {"unread_percent": round(100 * unread / total, 1)}
        for key, total, unread in (
            ("noreply@github.com", 9, 6),
            ("news@example.com", 4, 4),
            (None, 1, 0),
        )
    ]

    if "senders-unsorted" in BREAK:
        rows.reverse()

    if "senders-unread-over" in BREAK:
        rows[1]["unread"] = 5

    return {
        "folder": "INBOX",
        "by": "address",
        "messages": 14,
        "unread": 10,
        "groups": 3,
        "senders": rows,
    }


# ----------------------------------------------------------------------------
def document(command: str) -> str:
    """A --json document shaped like the real one for ``command``."""
    if command == "search" and option("--sort"):
        sizes = (
            [100, 5000, 800] if "sort-unordered" in BREAK else [5000, 800, 100]
        )
        body = {
            "folder": "INBOX",
            "more": True,
            "sort": {"key": option("--sort"), "reverse": "--reverse" in ARGV},
            "messages": [
                {"uid": uid, "size": size}
                for uid, size in enumerate(sizes, start=1)
            ],
        }

    elif command == "search":
        body = {
            "folder": "INBOX",
            "uidvalidity": None
            if "uidvalidity-null" in BREAK
            else UIDVALIDITY,
            "more": True,
            "sort": None,
            "messages": [{"uid": 5, "size": 131}],
        }

    elif command == "senders":
        body = senders()

    elif command == "folders" and "--counts" in ARGV:
        body = {"delimiter": ".", "prefix": None, "folders": counted()}

    elif command == "folders":
        body = {"delimiter": ".", "prefix": None, "folders": []}

    else:
        body = {"filterset": "managesieve", "filters": [], "findings": []}

    out = json.dumps({"version": 2, **body}, indent=2) + "\n"

    if command == "folders" and "json-noise" in BREAK:
        out = "Hierarchy delimiter: '.'\n" + out

    return out


# ----------------------------------------------------------------------------
def baseline(command: str) -> int:
    """'show-baseline' and 'check-baseline', exit status included."""
    where = "/home/u/.config/mailctl/baselines/mail.example.com.json"

    if "no-baseline" in BREAK:
        message = (
            f"no baseline has been saved for mail.example.com (looked for "
            f"{where}); 'mailctl server baseline save' records one"
        )

        # Under --json a failure is one JSON line on stderr (#151).
        if "--json" in ARGV:
            error = {
                "version": 2,
                "error": {"message": message, "code": "no_baseline"},
            }
            print(json.dumps(error), file=sys.stderr)

        else:
            print(f"mailctl: {message}", file=sys.stderr)

        return 1

    if command == "show-baseline":
        sys.stdout.write(json.dumps({"version": 1}, indent=2) + "\n")

        return 0

    print(
        f"Baseline for mail.example.com, taken 2026-09-01T08:00:00Z: {where}"
    )

    if "baseline-serious" in BREAK:
        print("\n1 serious, 0 informational change(s):")
        print("  ! the folder delimiter is now '/', not '.'")

        return 4

    if "baseline-info" in BREAK:
        print("\n0 serious, 1 informational change(s):")
        print("  - Sieve extensions: 'regex' is new")

        return 3

    print("\nNo drift: the servers say what they said then.")

    return 0


# ----------------------------------------------------------------------------
def optimize() -> str:
    """'optimize-rules --dry-run --json': a merge of two Trash rules."""
    plan = {
        "command": "filter optimize",
        "changes": True,
        "diff": {
            "label": "sieve",
            "text": "--- managesieve (current)\n+++ managesieve (proposed)\n",
            "reformats": False,
        },
        "considered": ["redundant", "reorder", "merge"],
        "removals": [],
        "reorders": [],
        "merges": [
            {
                "into": "Herrschners Spam",
                "absorbed": ["Rumble"],
                "header": "To",
                "match_type": "contains",
                "keys": ["herrschners@example.com", "rumble@example.com"],
            }
        ],
        "uncertain": [],
        "filterset": "managesieve",
        "active": "managesieve",
        "activate": True,
    }

    if "optimize-no-diff" in BREAK:
        plan["diff"] = None

    out = json.dumps({"version": 2, "plan": plan}, indent=2) + "\n"

    if "optimize-uploads" in BREAK:
        out += "Uploaded and activated script 'managesieve'\n"

    return out


# ----------------------------------------------------------------------------
def main() -> int:
    with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps(GIVEN) + "\n")

    if not ARGV:
        print(f"fake mailctl: no command in {GIVEN}", file=sys.stderr)

        return 2

    command = ARGV[0]

    if command in ("show-baseline", "check-baseline"):
        return baseline(command)

    if (
        command == "folders"
        and "--counts" in ARGV
        and "no-list-status" in BREAK
    ):
        print(
            "mailctl: the mxroute provider cannot count the messages in "
            "every folder in one request: the IMAP server does not "
            "advertise LIST-STATUS, and without it every folder would be a "
            "request of its own",
            file=sys.stderr,
        )

        return 1

    pin = option("--uidvalidity")

    if (
        command == "view"
        and pin is not None
        and int(pin) != UIDVALIDITY
        and "uidvalidity-ignored" not in BREAK
    ):
        print(
            f"mailctl: the UIDs for 'INBOX' were valid under UIDVALIDITY "
            f"{pin}, but the folder's is now {UIDVALIDITY}: the server has "
            f"renumbered it, so they may name other messages. None was "
            f"used, and nothing was changed.",
            file=sys.stderr,
        )

        return 1

    if command == "senders" and "senders-overcap" in BREAK:
        print(
            "mailctl: the search found 7342 message(s) in 'INBOX' but "
            "--max-messages is 5000, so no header was read.",
            file=sys.stderr,
        )

        return 1

    if (
        "--json" in ARGV
        and command in ("search", "folders", "rules", "senders")
        and "--build-filter" not in ARGV
    ):
        out = document(command)

    elif command == "list":
        out = "* managesieve   (active)\n"

        if "two-active" in BREAK:
            out += "* other   (active)\n"

    elif command == "test":
        out = TEST

        if "password-unset" in BREAK:
            out = out.replace("Password:  set (via file)", "Password:  unset")

    elif command == "probe":
        out = probe()

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

    elif command == "mark":
        out = mark()

    elif command == "apply":
        out = "Searching 'INBOX' for existing matches...\n"
        out += "[dry-run] would move 1 message(s) to 'INBOX.Lists'\n"

    elif command == "add":
        out = add()

    elif command == "remove-rule":
        out = DIFF

    elif command == "disable-rule":
        out = DIFF

        if "switch-uploads" in BREAK:
            out += "Uploaded and activated script 'managesieve'\n"

    elif command == "enable-rule":
        out = "Filter 'keep boss' is already enabled in 'managesieve'; "
        out += "nothing to change.\n"

    elif command == "subscribe":
        out = "'INBOX' is already subscribed; nothing to change.\n"

    elif command == "create-folder":
        out = (
            f"[dry-run] would create folder 'INBOX.{ARGV[-1]}' and "
            f"subscribe to it\n"
        )

    elif command == "optimize-rules":
        out = optimize()

    elif command == "rename-folder":
        out = (
            f"Rename folder '{ARGV[-2]}' to 'INBOX.{ARGV[-1]}' "
            f"(3 messages)\n\n[dry-run] nothing was renamed, and the script "
            f"was NOT uploaded.\n"
        )

        if "rename-renames" in BREAK:
            out += f"Renamed folder '{ARGV[-2]}' to 'INBOX.{ARGV[-1]}'\n"

    else:
        print(f"fake mailctl: no canned output for {command}", file=sys.stderr)

        return 2

    if command in ("add", "apply") and "--like" in ARGV:
        out = f"Message uid {option('--like')} in 'INBOX':\n\n" + out

    sys.stdout.write(out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
