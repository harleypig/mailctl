"""Baselines (#18) and drift (#19) against a real Dovecot + Pigeonhole.

What only a server can say: that a baseline saved from it compares clean
against it a moment later -- nothing in a real probe that varies between
two reads of an unchanged server is mistaken for drift -- and that saving
one writes a local file and nothing on the server. Drift itself is made by
editing the saved file, since the server cannot be changed underneath a
test; the offline tier covers each kind of drift one by one.

Every test names, in its docstring, the break that turns it red.
"""

import json
import os
import stat
from pathlib import Path

import pytest

pytestmark = pytest.mark.container

SCRIPT = 'require ["fileinto"];\n'


# ----------------------------------------------------------------------------
def baseline_file() -> Path:
    return (
        Path(os.environ["XDG_CONFIG_HOME"])
        / "mailctl"
        / "baselines"
        / "localhost.json"
    )


# ----------------------------------------------------------------------------
def edit(change) -> None:
    path = baseline_file()
    document = json.loads(path.read_text(encoding="utf-8"))

    change(document)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------
def test_a_fresh_baseline_checks_clean_and_saving_touches_no_server(account):
    """Red if two probes of an unchanged server differ (a time, an order, a
    per-connection value read as drift), if the file is readable by anyone
    else, or if saving changes a script on the server."""
    account.seed_script("managesieve", SCRIPT)
    before = account.script("managesieve"), account.active_script()

    saved = account.run("server", "baseline", "save")

    assert saved.code == 0, saved.err
    assert stat.S_IMODE(baseline_file().stat().st_mode) == 0o600
    assert (account.script("managesieve"), account.active_script()) == before

    checked = account.run("server", "baseline", "check")

    assert checked.code == 0, checked.out + checked.err
    assert "No drift" in checked.out

    document = account.run("server", "baseline", "check", "--json")

    assert document.code == 0
    assert json.loads(document.out)["drift"] == []


# ----------------------------------------------------------------------------
def test_serious_drift_exits_4_and_informational_drift_exits_3(account):
    """Red if a changed delimiter or active script is not serious, if an
    extension that appeared is not merely informational, or if either exit
    status moves -- a scheduled run keys on them."""
    account.seed_script("managesieve", SCRIPT)
    assert account.run("server", "baseline", "save").code == 0

    def added(document):
        document["server"]["rules"]["extensions"].remove("fileinto")

    edit(added)
    informational = account.run("server", "baseline", "check")

    assert informational.code == 3, informational.out + informational.err
    assert "'fileinto' is new" in informational.out

    def moved(document):
        document["server"]["mail"]["delimiter"] = "\\"
        document["accounts"][account.user]["active_rule_set"] = "other"

    edit(moved)
    serious = account.run("server", "baseline", "check", "--json")
    report = json.loads(serious.out)

    assert serious.code == 4, serious.err
    assert {(item["severity"], item["kind"]) for item in report["drift"]} >= {
        ("serious", "delimiter"),
        ("serious", "active-rule-set"),
    }


# ----------------------------------------------------------------------------
def test_a_lost_extension_the_script_does_not_require_is_informational(
    account,
):
    """Red if the live script's own require line is not what decides: the
    stored side claims an extension this server never had, and only
    reading the script shows it is not one the rules need. (A lost
    extension the script does require cannot be made here -- the server
    refuses a script requiring what it lacks -- so that case is the
    offline tier's.)"""
    account.seed_script("managesieve", SCRIPT)
    assert account.run("server", "baseline", "save").code == 0

    def invented(document):
        document["server"]["rules"]["extensions"].append("vnd.invented")

    edit(invented)
    result = account.run("server", "baseline", "check", "--json")
    drift = json.loads(result.out)["drift"]

    assert result.code == 3, result.err
    assert drift == [
        {
            "severity": "info",
            "kind": "extension-removed",
            "half": "rules",
            "name": "vnd.invented",
            "before": "vnd.invented",
            "after": None,
        }
    ]


# ----------------------------------------------------------------------------
def test_test_reports_drift_in_one_line_and_still_passes(account):
    """Red if 'test' fails over drift, or says nothing about it."""
    account.seed_script("managesieve", SCRIPT)
    assert account.run("server", "baseline", "save").code == 0

    edit(lambda document: document["server"]["mail"].update(delimiter="\\"))
    result = account.run("server", "test")

    lines = [
        line for line in result.out.splitlines() if line.startswith("Baseline")
    ]

    assert result.code == 0, result.err
    assert len(lines) == 1
    assert lines[0].startswith("Baseline:  1 serious, 0 informational")
