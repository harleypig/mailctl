"""The session, opened the way any front-end would open it.

Only what is about the connection lives here: which halves are opened,
and what an operation does without the half it needs. The work done
over a session is in the ``test_utilities_*`` files.
"""

import threading

import pytest
from imapclient.exceptions import IMAPClientAbortError, IMAPClientError
from sievelib.managesieve import Error as SieveProtocolError
from utilities_support import criteria, mxroute

from mailctl import MailctlError, engine, utilities
from mailctl.components.imap import connection_lost as imap_lost
from mailctl.components.managesieve import client as sieve_client
from mailctl.components.managesieve import connection_lost as sieve_lost
from mailctl.components.managesieve.client import CONNECTION_CLOSED
from mailctl.providers.base import (
    CONNECTION,
    MAIL,
    READ,
    RULES,
    TRANSPORT_KINDS,
    TRANSPORT_OPERATIONS,
    WRITE,
    MailActionPlan,
    MessageSummary,
)
from mailctl.providers.mxroute import MXROUTE, MxrouteTransport

# ############################################################################
# Fakes
# ############################################################################


class FakeSieveClient:
    """Just enough of ``SieveClient`` to connect, list, store, and drop.

    ``drops`` names each call that fails, once per entry, as a server that
    has closed the connection does.
    """

    # ------------------------------------------------------------------------
    def __init__(self):
        self.calls: list[str] = []
        self.drops: list[str] = []

    # ------------------------------------------------------------------------
    def _call(self, name: str):
        self.calls.append(name)

        if name in self.drops:
            self.drops.remove(name)

            raise SieveProtocolError(CONNECTION_CLOSED)

    # ------------------------------------------------------------------------
    def connect(self, user, password, starttls=False, ssl=False):
        self._call("connect")

        return True

    def logout(self):
        self._call("logout")

    def listscripts(self):
        self._call("listscripts")

        return ("managesieve", [])

    def putscript(self, name, content):
        self._call("putscript")

        return True

    @property
    def capability_response(self) -> bytes:
        return b'"SIEVE" "fileinto"\r\n'


# ----------------------------------------------------------------------------
@pytest.fixture
def fake_sieve_client(monkeypatch) -> FakeSieveClient:
    """Patch ``SieveClient`` at mailctl's boundary; one client per test."""
    client = FakeSieveClient()
    monkeypatch.setattr(sieve_client, "SieveClient", lambda *a, **k: client)

    return client


# ----------------------------------------------------------------------------
def healthy_on_login(fake_imap, monkeypatch) -> None:
    """Make a fresh login a fresh connection: whatever was armed to fail
    on the old one is gone on the new."""
    login = fake_imap.login

    def relogin(user, password):
        fake_imap.failures.clear()

        return login(user, password)

    monkeypatch.setattr(fake_imap, "login", relogin)


# ----------------------------------------------------------------------------
def attempts(fake_imap, monkeypatch, name: str) -> list[int]:
    """Count every call of the double's ``name``, failed ones included --
    its own record keeps only the calls that went through."""
    seen: list[int] = []
    method = getattr(fake_imap, name)

    def counted(*args, **kwargs):
        seen.append(1)

        return method(*args, **kwargs)

    monkeypatch.setattr(fake_imap, name, counted)

    return seen


DROP = IMAPClientAbortError("socket error: EOF")


# ############################################################################
# connect -- lazy, per half
# ############################################################################


# ----------------------------------------------------------------------------
def test_connect_opens_nothing_until_a_half_is_used(
    fake_imap, imap_config, fake_sieve_client
):
    """A session that needs neither half costs no connection at all."""
    with engine.connect(imap_config) as live:
        assert isinstance(live, engine.Session)
        assert live.provider is MXROUTE
        assert isinstance(live.connection, MxrouteTransport)
        assert live.has_rules and live.has_mail
        assert live.opened == ()

    assert fake_imap.calls == []
    assert fake_sieve_client.calls == []


# ----------------------------------------------------------------------------
def test_a_half_opens_the_first_time_it_is_used_and_only_that_half(
    fake_imap, imap_config, fake_sieve_client
):
    seen = []

    with engine.connect(
        imap_config,
        progress=lambda channel, message: seen.append(channel),
    ) as live:
        utilities.folders.list_folders(live)
        utilities.folders.list_folders(live)

        assert live.opened == (MAIL,)
        assert fake_sieve_client.calls == []

    assert fake_imap.names().count("login") == 1
    assert fake_imap.names()[-1] == "logout"
    assert seen and set(seen) == {"imap"}


# ----------------------------------------------------------------------------
def test_a_half_the_session_may_not_open_is_never_opened(
    fake_imap, imap_config, fake_sieve_client
):
    with engine.connect(imap_config, rules=False) as live:
        assert not live.has_rules

        with pytest.raises(MailctlError, match="no ManageSieve session"):
            utilities.scripts.list_scripts(live)

    assert fake_sieve_client.calls == []


# ----------------------------------------------------------------------------
def test_open_all_opens_what_was_asked_for_mail_first(
    fake_imap, imap_config, fake_sieve_client, monkeypatch
):
    """Each named half up front, IMAP before ManageSieve, and closed the
    other way round."""
    order = []
    monkeypatch.setattr(fake_imap, "logout", lambda: order.append("imap"))
    fake_sieve_client.logout = lambda: order.append("sieve")

    with engine.connect(imap_config) as live:
        live.open_all()
        assert live.opened == (MAIL, RULES)

    assert order == ["sieve", "imap"]

    with engine.connect(imap_config, rules=False) as live:
        live.open_all()
        assert live.opened == (MAIL,)


# ############################################################################
# Reconnect -- reads once, writes never
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_read_the_server_cut_off_reconnects_once_and_succeeds(
    fake_imap, imap_config, monkeypatch
):
    healthy_on_login(fake_imap, monkeypatch)
    searches = attempts(fake_imap, monkeypatch, "search")

    with engine.connect(imap_config, rules=False) as live:
        utilities.folders.list_folders(live)
        fake_imap.failures["search"] = DROP

        uids = live.transport.search("INBOX", criteria())

    assert uids == []
    assert len(searches) == 2
    assert fake_imap.names().count("login") == 2


# ----------------------------------------------------------------------------
def test_a_read_that_fails_again_on_the_new_connection_is_raised(
    fake_imap, imap_config, monkeypatch
):
    searches = attempts(fake_imap, monkeypatch, "search")
    fake_imap.failures["search"] = DROP

    with (
        engine.connect(imap_config, rules=False) as live,
        pytest.raises(MailctlError, match="socket error: EOF"),
    ):
        live.transport.search("INBOX", criteria())

    assert len(searches) == 2
    assert fake_imap.names().count("login") == 2


# ----------------------------------------------------------------------------
def test_a_read_the_server_refused_is_not_sent_again(
    fake_imap, imap_config, monkeypatch
):
    """Only a lost connection earns a retry; a NO is the server's answer."""
    searches = attempts(fake_imap, monkeypatch, "search")
    fake_imap.failures["search"] = IMAPClientError("NO bad charset")

    with (
        engine.connect(imap_config, rules=False) as live,
        pytest.raises(MailctlError, match="bad charset"),
    ):
        live.transport.search("INBOX", criteria())

    assert len(searches) == 1
    assert fake_imap.names().count("login") == 1


# ----------------------------------------------------------------------------
def test_a_write_cut_off_mid_flight_is_raised_and_never_sent_again(
    fake_imap, imap_config, monkeypatch
):
    """It may have landed, so sending it again could do it twice. The dead
    connection is let go, so the next read makes a new one."""
    healthy_on_login(fake_imap, monkeypatch)
    moves = attempts(fake_imap, monkeypatch, "move")
    fake_imap.messages = {1: b"From: a@example.com\r\n\r\n"}
    plan = MailActionPlan(
        "INBOX",
        "INBOX.Lists",
        [],
        False,
        [MessageSummary(1, "", "a@example.com", "", "INBOX")],
    )

    with engine.connect(imap_config, rules=False) as live:
        utilities.folders.list_folders(live)
        fake_imap.failures["move"] = DROP

        with pytest.raises(MailctlError, match="socket error: EOF"):
            live.transport.apply_mail(plan)

        assert live.opened == ()
        assert len(moves) == 1

        live.transport.list_folders()

    assert len(moves) == 1
    assert fake_imap.names().count("login") == 2


# ----------------------------------------------------------------------------
def test_a_managesieve_read_reconnects_and_a_write_does_not(
    imap_config, fake_sieve_client
):
    """The same rule over the other protocol: the EOF and BYE the client
    reports as a closed connection (#95)."""
    with engine.connect(imap_config, mail=False) as live:
        fake_sieve_client.drops = ["listscripts"]

        assert live.transport.list_rule_sets() == ("managesieve", [])
        assert fake_sieve_client.calls.count("connect") == 2

        fake_sieve_client.drops = ["putscript"]

        with pytest.raises(MailctlError, match=CONNECTION_CLOSED):
            live.transport.store_rule_set("managesieve", "keep;\n")

    assert fake_sieve_client.calls.count("putscript") == 1
    assert fake_sieve_client.calls.count("connect") == 2


# ----------------------------------------------------------------------------
def test_a_lost_connection_is_told_from_a_refusal():
    """Both components' reading of their library's errors, cause included."""
    wrapped = MailctlError("search failed")
    wrapped.__cause__ = DROP

    assert imap_lost(DROP)
    assert imap_lost(wrapped)
    assert imap_lost(ConnectionResetError())
    assert not imap_lost(IMAPClientError("NO"))
    assert not imap_lost(MailctlError("no IMAP session is open"))

    closed = MailctlError("PUTSCRIPT failed")
    closed.__cause__ = SieveProtocolError(CONNECTION_CLOSED)

    assert sieve_lost(closed)
    assert sieve_lost(BrokenPipeError())
    assert not sieve_lost(SieveProtocolError("NO quota exceeded"))


# ############################################################################
# Serialised, one session per user
# ############################################################################


# ----------------------------------------------------------------------------
def test_calls_on_one_connection_wait_their_turn(fake_imap, imap_config):
    """Two threads, one IMAP connection: the second call does not start
    until the first has finished."""
    inside = threading.Semaphore(0)
    release = threading.Event()
    running = []
    search = fake_imap.search

    def slow_search(key, charset=None):
        running.append(1)
        inside.release()
        release.wait(5)
        overlapped = len(running) > 1
        running.pop()

        assert not overlapped

        return search(key, charset)

    fake_imap.search = slow_search
    errors = []

    def worker(live):
        try:
            live.transport.search("INBOX", criteria())

        except Exception as error:
            errors.append(error)

    with engine.connect(imap_config, rules=False) as live:
        threads = [threading.Thread(target=worker, args=(live,))]
        threads[0].start()

        assert inside.acquire(timeout=5)

        threads.append(threading.Thread(target=worker, args=(live,)))
        threads[1].start()

        # The second search must not reach the server while the first holds
        # the connection.
        assert not inside.acquire(timeout=0.2)

        release.set()

        for thread in threads:
            thread.join(5)

    assert errors == []
    assert fake_imap.names().count("search") == 2


# ----------------------------------------------------------------------------
def test_two_sessions_share_nothing(fake_imap, imap_config):
    """One per user: each session owns its connections and closes only
    its own."""
    with (
        engine.connect(imap_config, rules=False) as first,
        engine.connect(imap_config, rules=False) as second,
    ):
        assert first.connection is not second.connection

        utilities.folders.list_folders(first)

        assert first.opened == (MAIL,)
        assert second.opened == ()


# ############################################################################
# The classification the session reads
# ############################################################################

# What each operation is. Pinned here so a change of kind -- a write
# re-classified as a read would be retried after it may have landed -- is a
# deliberate edit to this table, not a side effect.
EXPECTED_KINDS = {
    "rules_capabilities": (READ, RULES),
    "list_rule_sets": (READ, RULES),
    "active_rule_set": (READ, RULES),
    "read_rule_set": (READ, RULES),
    "check_rule_set": (READ, RULES),
    "store_rule_set": (WRITE, RULES),
    "activate_rule_set": (WRITE, RULES),
    "describe_rules_server": (READ, RULES),
    "mail_capabilities": (READ, MAIL),
    "list_folders": (READ, MAIL),
    "create_folder": (WRITE, MAIL),
    "subscribe": (WRITE, MAIL),
    "unsubscribe": (WRITE, MAIL),
    "describe_mail_server": (READ, MAIL),
    "mail_namespaces": (READ, MAIL),
    "folder_status": (READ, MAIL),
    "search": (READ, MAIL),
    "search_messages": (READ, MAIL),
    "fetch_headers": (READ, MAIL),
    "fetch_summaries": (READ, MAIL),
    "apply_mail": (WRITE, MAIL),
    "add_flags": (WRITE, MAIL),
    "remove_flags": (WRITE, MAIL),
    "message_headers": (READ, MAIL),
    "message_source": (READ, MAIL),
    "sort_messages": (READ, MAIL),
    "open": (CONNECTION, None),
    "connect": (CONNECTION, None),
    "disconnect": (CONNECTION, None),
    "dropped": (CONNECTION, None),
    "has_rules": (CONNECTION, None),
    "has_mail": (CONNECTION, None),
}


# ----------------------------------------------------------------------------
def test_every_transport_operation_is_classified():
    assert set(TRANSPORT_KINDS) == set(TRANSPORT_OPERATIONS)
    assert {
        name: (operation.kind, operation.half)
        for name, operation in TRANSPORT_KINDS.items()
    } == EXPECTED_KINDS


# ----------------------------------------------------------------------------
def test_an_operation_without_its_session_raises_rather_than_crashing():
    with pytest.raises(MailctlError, match="no ManageSieve session"):
        utilities.scripts.list_scripts(mxroute())

    with pytest.raises(MailctlError, match="no IMAP session"):
        utilities.folders.list_folders(mxroute())
