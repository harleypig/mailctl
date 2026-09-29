"""scripts/live-readonly.sh, driven against a fake mailctl.

The script is the read-only live pass, so the offline tier can check
everything about it except the account: its TAP, test selection, failure
and skip reporting, and -- the part that matters most -- that it never sends
a subcommand that could change something without ``--dry-run``.
``tests/fixtures/fake_mailctl.py`` stands in for the binary and records
every argv it is handed.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "live-readonly.sh"
FAKE = Path(__file__).resolve().parent / "fixtures" / "fake_mailctl.py"

ALL_TESTS = [
    "test",
    "probe",
    "list",
    "show",
    "rules",
    "folders",
    "backup",
    "search",
    "search-like",
    "build-filter",
    "view-keeps-unread",
    "mark",
    "apply",
    "apply-like",
    "add",
    "add-create-folder",
    "add-like",
    "remove-rule",
    "disable-rule",
    "subscribe",
    "create-folder",
    "unchanged",
]

MUTATING = [
    "add",
    "apply",
    "backup",
    "create-folder",
    "disable-rule",
    "enable-rule",
    "mark",
    "migrate-config",
    "move-rule",
    "remove-rule",
    "restore",
    "subscribe",
    "unsubscribe",
]

# Stands in for a real password; it must never reach the script's output.
SENTINEL = "s3ntinel-never-print-me"


# ----------------------------------------------------------------------------
def run(tmp_path, *args, breaks=(), binary=None):
    """Run the script; return the completed process and the recorded argvs."""
    log = tmp_path / "calls.jsonl"
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "LIVECHECK_PAUSE": "0",
        "MAILCTL_BIN": str(binary or FAKE),
        "MAILCTL_PASSWORD": SENTINEL,
        "STUB_LOG": str(log),
        "STUB_BACKUP_DIR": str(tmp_path / "backups"),
        "STUB_BREAK": ",".join(breaks),
    }
    proc = subprocess.run(
        [str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    calls = (
        [json.loads(line) for line in log.read_text().splitlines()]
        if log.exists()
        else []
    )

    return proc, calls


# ----------------------------------------------------------------------------
def results(stdout):
    """The TAP result lines, as (ok, number, name, directive)."""
    pattern = re.compile(r"^(ok|not ok) (\d+) - (\S+)(?: # (.*))?$")

    return [
        (m[1] == "ok", int(m[2]), m[3], m[4])
        for m in map(pattern.match, stdout.splitlines())
        if m
    ]


# ############################################################################
# Plan, numbering, selection
# ############################################################################


# ----------------------------------------------------------------------------
def test_list_prints_every_test_name_in_order(tmp_path):
    proc, calls = run(tmp_path, "--list")

    assert proc.returncode == 0
    assert proc.stdout.split() == ALL_TESTS
    assert calls == []


# ----------------------------------------------------------------------------
def test_help_exits_zero_and_calls_nothing(tmp_path):
    proc, calls = run(tmp_path, "--help")

    assert proc.returncode == 0
    assert proc.stdout.startswith("Usage: live-readonly.sh")
    assert calls == []


# ----------------------------------------------------------------------------
def test_no_names_runs_everything_and_passes(tmp_path):
    proc, _ = run(tmp_path)

    assert proc.returncode == 0, proc.stdout
    assert proc.stdout.splitlines()[0] == f"1..{len(ALL_TESTS)}"

    got = results(proc.stdout)

    assert [(n, name) for _, n, name, _ in got] == list(
        enumerate(ALL_TESTS, start=1)
    )
    assert all(ok and directive is None for ok, _, _, directive in got)


# ----------------------------------------------------------------------------
def test_one_name_runs_only_that_test(tmp_path):
    proc, calls = run(tmp_path, "list")

    assert proc.returncode == 0
    assert proc.stdout.splitlines() == ["1..1", "ok 1 - list"]
    assert calls == [["list"]]


# ----------------------------------------------------------------------------
def test_several_names_run_in_the_standard_order(tmp_path):
    proc, _ = run(tmp_path, "unchanged", "folders", "list")

    assert proc.returncode == 0
    assert proc.stdout.splitlines() == [
        "1..3",
        "ok 1 - list",
        "ok 2 - folders",
        "ok 3 - unchanged",
    ]


# ----------------------------------------------------------------------------
def test_an_unknown_name_is_a_usage_error_before_any_call(tmp_path):
    proc, calls = run(tmp_path, "list", "nonesuch")

    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "unknown test: nonesuch" in proc.stderr
    assert calls == []


# ----------------------------------------------------------------------------
def test_a_missing_binary_bails_out(tmp_path):
    proc, _ = run(tmp_path, "list", binary=tmp_path / "no-such-mailctl")

    assert proc.returncode == 2
    assert proc.stdout.startswith("Bail out!")


# ############################################################################
# Failure and skip reporting
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("fault", "name"),
    [
        ("two-active", "list"),
        ("view-marks-read", "view-keeps-unread"),
        ("drift", "unchanged"),
        ("backup-writes", "backup"),
        ("backup-writes-elsewhere", "backup"),
        ("like-drops-source", "search-like"),
        ("filter-on-stdout", "build-filter"),
        ("mark-writes", "mark"),
        ("mark-reports-change", "mark"),
        ("probe-not-json", "probe"),
        ("probe-no-mail", "probe"),
    ],
)
def test_a_failing_check_is_not_ok_and_the_run_exits_nonzero(
    tmp_path, fault, name
):
    proc, _ = run(tmp_path, name, breaks=[fault])

    assert proc.returncode == 1
    assert proc.stdout.splitlines()[:2] == ["1..1", f"not ok 1 - {name}"]
    assert proc.stdout.splitlines()[2].startswith("# ")


# ----------------------------------------------------------------------------
def test_a_failure_does_not_stop_the_later_tests(tmp_path):
    proc, _ = run(tmp_path, "list", "folders", breaks=["two-active"])

    assert proc.returncode == 1
    assert [ok for ok, *_ in results(proc.stdout)] == [False, True]


# ----------------------------------------------------------------------------
def test_no_unread_message_skips_view_keeps_unread(tmp_path):
    proc, calls = run(tmp_path, "view-keeps-unread", breaks=["no-unread"])

    assert proc.returncode == 0
    assert proc.stdout.splitlines() == [
        "1..1",
        "ok 1 - view-keeps-unread # SKIP no unread message among the "
        "newest 20",
    ]
    assert not any(call[0] == "view" for call in calls)


# ----------------------------------------------------------------------------
def test_the_live_password_line_passes(tmp_path):
    """``set (via file)`` is how a password file reads; only ``set`` counts."""
    proc, _ = run(tmp_path, "test")

    assert proc.stdout.splitlines() == ["1..1", "ok 1 - test"]


# ----------------------------------------------------------------------------
def test_an_unset_password_fails_test_by_name(tmp_path):
    proc, _ = run(tmp_path, "test", breaks=["password-unset"])

    assert proc.returncode == 1
    assert proc.stdout.splitlines()[1:3] == [
        "not ok 1 - test",
        "# the password is unset",
    ]


# ----------------------------------------------------------------------------
def test_probe_makes_one_call_and_passes_on_a_probe_document(tmp_path):
    proc, calls = run(tmp_path, "probe")

    assert proc.stdout.splitlines() == ["1..1", "ok 1 - probe"]
    assert calls == [["probe", "--json"]]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("probe-not-json", "# probe --json: not JSON: "),
        ("probe-no-mail", "# probe --json: no mail section"),
    ],
)
def test_probe_names_what_is_wrong_with_the_document(tmp_path, fault, reason):
    proc, _ = run(tmp_path, "probe", breaks=[fault])

    assert proc.stdout.splitlines()[2].startswith(reason)


# ----------------------------------------------------------------------------
def test_remove_rule_reads_names_from_a_crlf_script(tmp_path):
    """The live server's script ends its lines in CRLF, and ``show`` passes
    them through; a rule name must still come out without the CR."""
    proc, calls = run(tmp_path, "remove-rule")

    assert proc.stdout.splitlines() == ["1..1", "ok 1 - remove-rule"]
    assert ["remove-rule", "--dry-run", "keep boss"] in calls


# ----------------------------------------------------------------------------
def test_a_marker_that_does_not_parse_fails_rather_than_skips(tmp_path):
    proc, calls = run(tmp_path, "remove-rule", breaks=["bad-marker"])

    assert proc.returncode == 1
    assert proc.stdout.splitlines()[1] == "not ok 1 - remove-rule"
    assert not any(call[0] == "remove-rule" for call in calls)


# ----------------------------------------------------------------------------
def test_disable_rule_plans_both_switches_on_a_real_rule_name(tmp_path):
    proc, calls = run(tmp_path, "disable-rule")

    assert proc.stdout.splitlines() == ["1..1", "ok 1 - disable-rule"]
    assert ["disable-rule", "--dry-run", "keep boss"] in calls
    assert ["enable-rule", "--dry-run", "keep boss"] in calls


# ----------------------------------------------------------------------------
def test_disable_rule_fails_when_a_switch_reports_a_change(tmp_path):
    proc, _ = run(tmp_path, "disable-rule", breaks=["switch-uploads"])

    assert proc.returncode == 1
    assert proc.stdout.splitlines()[1] == "not ok 1 - disable-rule"


# ----------------------------------------------------------------------------
def test_mark_reuses_the_uid_search_found_and_only_dry_runs(tmp_path):
    proc, calls = run(tmp_path, "mark")

    assert proc.stdout.splitlines() == ["1..1", "ok 1 - mark"]
    assert calls == [
        ["search", "--limit", "1"],
        ["mark", "--dry-run", "5", "--flag"],
        ["search", "--limit", "20"],
    ]


# ############################################################################
# Safety
# ############################################################################


# ----------------------------------------------------------------------------
def test_every_mutating_call_carries_dry_run_and_none_carries_yes(tmp_path):
    proc, calls = run(tmp_path)

    assert proc.returncode == 0
    assert calls

    for call in calls:
        assert "--yes" not in call

        if call[0] in MUTATING:
            assert "--dry-run" in call, call


# ----------------------------------------------------------------------------
def test_the_baseline_is_taken_only_when_unchanged_is_selected(tmp_path):
    _, without = run(tmp_path, "subscribe")

    assert without == [["subscribe", "--dry-run", "INBOX"]]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("command", MUTATING)
def test_the_guard_refuses_a_mutating_call_without_dry_run(tmp_path, command):
    proc, calls = run(tmp_path, "--selftest-guard", command, "x")

    assert (proc.returncode, proc.stdout) == (3, "refused\n")
    assert calls == []


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("command", MUTATING)
def test_the_guard_allows_a_mutating_call_with_dry_run(tmp_path, command):
    proc, _ = run(tmp_path, "--selftest-guard", command, "--dry-run", "x")

    assert (proc.returncode, proc.stdout) == (0, "allowed\n")


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "argv",
    [
        ["add", "--dry-run", "--yes"],
        ["list", "--yes"],
    ],
)
def test_the_guard_refuses_yes_whatever_else_is_given(tmp_path, argv):
    proc, _ = run(tmp_path, "--selftest-guard", *argv)

    assert proc.returncode == 3


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "argv",
    [
        ["list"],
        ["show"],
        ["probe", "--json"],
        ["search", "--limit", "5"],
        ["view", "4"],
    ],
)
def test_the_guard_allows_read_only_calls(tmp_path, argv):
    proc, _ = run(tmp_path, "--selftest-guard", *argv)

    assert proc.returncode == 0


# ----------------------------------------------------------------------------
def test_nothing_from_the_environment_reaches_the_output(tmp_path):
    proc, _ = run(tmp_path, breaks=["two-active"])

    assert SENTINEL not in proc.stdout
    assert SENTINEL not in proc.stderr
