"""The session, opened the way any front-end would open it.

Only what is about the connection lives here: which halves are opened,
and what an operation does without the half it needs. The work done
over a session is in the ``test_utilities_*`` files.
"""

import pytest

from mailctl import MailctlError, engine, utilities
from mailctl.components.managesieve import client as sieve_client
from mailctl.providers.mxroute import MxrouteProvider

# ############################################################################
# connect
# ############################################################################


# ----------------------------------------------------------------------------
def test_connect_opens_only_what_was_asked_for_and_tags_progress(
    fake_imap, imap_config, monkeypatch
):
    opened = []
    monkeypatch.setattr(
        sieve_client, "SieveClient", lambda *a, **k: opened.append("sieve")
    )

    seen = []

    with engine.connect(
        imap_config,
        rules=False,
        mail=True,
        progress=lambda channel, message: seen.append(channel),
    ) as live:
        assert isinstance(live, MxrouteProvider)
        assert live.sieve is None
        assert live.imap is not None

    assert opened == []
    assert seen and set(seen) == {"imap"}
    assert fake_imap.names()[-1] == "logout"


# ----------------------------------------------------------------------------
def test_an_operation_without_its_session_raises_rather_than_crashing():
    with pytest.raises(MailctlError, match="no ManageSieve session"):
        utilities.scripts.list_scripts(MxrouteProvider())

    with pytest.raises(MailctlError, match="no IMAP session"):
        utilities.folders.list_folders(MxrouteProvider())
