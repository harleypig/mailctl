"""``mailctl probe --json`` against a real Dovecot + Pigeonhole (#101).

The probe is what a provider record's *Observed* tier is copied from, so
what it prints has to be what the server says. Each assertion below reads
the same fact through the libraries' own clients -- never through mailctl
-- and compares.
"""

import json
import re

import pytest

pytestmark = pytest.mark.container

SCRIPT = 'require ["fileinto"];\n'


# ----------------------------------------------------------------------------
def test_probe_json_is_what_the_server_says(account):
    """Red if a capability is dropped or invented on either half, if the
    identity strings are not the server's, if the namespace or delimiter
    is guessed rather than read, if the active script is not the one the
    server runs, or if the document's shape or version moves."""
    account.seed_script("managesieve", SCRIPT)

    result = account.run("probe", "--json")

    assert result.code == 0, result.err
    assert result.err == ""

    document = json.loads(result.out)

    assert document["version"] == 1
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", document["taken"])
    assert document["provider"] == "mxroute"
    assert {item["label"] for item in document["endpoints"]} == {
        "IMAP",
        "Sieve",
    }

    with account.sieve() as client:
        implementation = client.get_implementation()
        sasl = client.get_sasl_mechanisms()
        extensions = client.get_sieve_capabilities()

    rules = document["rules"]
    sieve_caps = {
        item["name"]: item["value"] for item in rules["capabilities"]
    }

    assert rules["identity"] == {"implementation": implementation}
    assert rules["capabilities_after_login"] is False
    assert sieve_caps["IMPLEMENTATION"] == implementation
    assert sieve_caps["SASL"].split() == sasl
    assert rules["extensions"] == sorted(extensions)
    assert rules["active_rule_set"] == "managesieve"

    with account.imap() as client:
        capabilities = {item.decode() for item in client.capabilities()}
        fields = client.id_()[0]
        personal = client.namespace().personal
        delimiter = client.list_folders()[0][1].decode()

    mail = document["mail"]
    names = [item["name"] for item in mail["capabilities"]]

    assert mail["capabilities_after_login"] is True
    assert set(names) == capabilities
    assert names == sorted(names, key=str.upper)

    server_id = {
        name.decode().lower(): value.decode()
        for name, value in zip(fields[::2], fields[1::2], strict=True)
        if value is not None
    }

    assert mail["identity"] == server_id
    assert mail["delimiter"] == delimiter
    assert [
        (space["prefix"], space["delimiter"])
        for space in mail["namespaces"]
        if space["kind"] == "personal"
    ] == [(prefix, separator) for prefix, separator in personal]


# ----------------------------------------------------------------------------
def test_probe_prints_no_credential_and_changes_nothing(account):
    """Red if the password reaches either stream in either form, or if
    probing leaves the account with a script or folder it did not have."""
    password = account.server.password_file.read_text().strip()
    account.seed_script("managesieve", SCRIPT)

    before = account.script("managesieve"), account.active_script()

    human = account.run("probe", "-v")
    document = account.run("probe", "--json", "-v")

    assert human.code == document.code == 0
    assert password
    assert password not in human.out + human.err
    assert password not in document.out + document.err
    assert (account.script("managesieve"), account.active_script()) == before


# ----------------------------------------------------------------------------
def test_a_known_server_has_no_unknown_server_report(account):
    """#39: the report is offered only for a server no module matches.

    Red if a Dovecot and Pigeonhole the libraries' own clients identify
    are reported as unrecognised by 'probe', 'probe --report', or 'test',
    or if the report path prints a credential. The unknown path is driven
    offline, since this server is a known one."""
    password = account.server.password_file.read_text().strip()

    with account.sieve() as client:
        implementation = client.get_implementation()

    with account.imap() as client:
        fields = client.id_()[0]

    assert "pigeonhole" in implementation.lower()
    assert b"dovecot" in b" ".join(fields[1::2]).lower()

    report = account.run("probe", "--report")
    human = account.run("probe")
    test = account.run("test")

    assert report.code == human.code == test.code == 0, report.err
    assert report.out == ""
    assert "nothing to report" in report.err
    assert "does not recognise" not in human.out
    assert "does not recognise" not in test.out
    assert password not in report.out + report.err
