"""The tier's own config writer and server, through each route's install.

Not a use case: the check that what every later use case stands on works
on every route before one relies on it -- that a ``mail_config`` reaches
the shared test server from an installed ``mailctl`` (on Docker, through
the mounts docs/DOCKER.md gives and the host's network), and that each
broken variant meets the refusal it exists to produce rather than some
other failure.

``folder list --json`` is the probe: it logs in over IMAP, so it needs
the whole config, and its ``--json`` error carries a stable code.

Red under: a config the server refuses or cannot be reached by (``good``
exits non-zero); a variant written wrong, so it meets a different refusal
or none (its code differs); and the password reaching either stream.
"""

import json

import pytest

pytestmark = pytest.mark.e2e

# Each broken variant, and the code mailctl's refusal of it carries.
REFUSALS = {
    "no_config": "missing_settings",
    "no_password": "no_password",
    "password_readable": "password_file_mode",
}


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("variant", ["good", *REFUSALS])
def test_config_variant(installed, mail_config, dovecot, variant):
    config = mail_config(variant)

    listed = installed.run(
        "mailctl", "folder", "list", "--json", config=config
    )

    # A bool, never the value: an assertion would print what it compares.
    secret = dovecot.password_file.read_text()
    leaked = secret in listed.out or secret in listed.err

    assert not leaked, "the password reached mailctl's output"

    if variant == "good":
        assert listed.code == 0, listed.err

        names = [
            folder["name"] for folder in json.loads(listed.out)["folders"]
        ]

        assert "INBOX" in names, names

    else:
        assert listed.code != 0, listed.out

        # Under --json a refusal is a JSON document ending stderr. With no
        # password, getpass's warning and prompt come first, the prompt on
        # the same line, so the document is read from its opening brace.
        last = listed.err.splitlines()[-1]
        error = json.loads(last[last.index("{") :])["error"]

        assert error["code"] == REFUSALS[variant], error
