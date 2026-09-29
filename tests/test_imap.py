"""Folder naming, the post-filtered search, and the plan/execute split.

Nothing here opens a socket: ``IMAPClient`` is replaced at mailctl's own
import boundary by the ``fake_imap`` double, so what is under test is
mailctl's logic and never IMAPClient's.

Folder naming carries the most risk of anything offline in this tool.
Getting the delimiter wrong does not fail -- it files mail into a folder
nobody opens, which looks exactly like the filter not running.
"""

import pytest
from imapclient.exceptions import IMAPClientError, LoginError
from utilities_support import mxroute

from mailctl import MailctlError, utilities
from mailctl.components.imap import (
    BULK_CHUNK,
    ImapAuthenticationError,
    ImapSession,
    MailActionPlan,
    MessageSummary,
    PartialExecution,
    case_variants,
    decode_header_value,
    normalize_folder,
    split_path,
)
from mailctl.config import Secret
from mailctl.criteria import Criteria
from mailctl.providers.base import ActionSpec
from mailctl.providers.mxroute import records
from mailctl.utilities.mail import header_values

# ############################################################################
# Helpers
# ############################################################################


# ----------------------------------------------------------------------------
def matching(imap_session, criteria, folder="INBOX"):
    """What the existing-mail pass selects: the session's candidates,
    re-checked against the real comparison by the mail utility."""
    session = mxroute(imap=imap_session)

    return utilities.mail.plan_mail(
        session, criteria, ActionSpec(), folder, ""
    ).messages


# ----------------------------------------------------------------------------
def normalized(session: ImapSession, name: str) -> str:
    """``name`` normalized against what this session read at open."""
    return normalize_folder(
        name, session.delimiter, session.folders, session.namespace_prefix
    )


# ----------------------------------------------------------------------------
def plan_actions(
    imap_session, criteria, source, destination="", flags=(), discard=False
):
    """The existing-mail pass's plan, as this session executes it."""
    plan = utilities.mail.plan_mail(
        mxroute(imap=imap_session),
        criteria,
        ActionSpec(flags=tuple(flags), discard=discard),
        source,
        destination,
    )

    return records.session_plan(plan)


# ----------------------------------------------------------------------------
def message(sender: str, subject: str = "Subject line") -> bytes:
    """Return a minimal RFC 822 header block for the fetch double."""
    return (
        f"From: {sender}\r\nSubject: {subject}\r\nTo: me@example.com\r\n\r\n"
    ).encode()


# ----------------------------------------------------------------------------
def plain_session(**overrides) -> ImapSession:
    """An unopened session from plain parameters, as any provider builds it."""
    settings = {
        "host": "mail.example.com",
        "port": 993,
        "username": "user@example.com",
        "password": lambda: Secret("not-a-real-password"),
        **overrides,
    }

    return ImapSession(**settings)


# ----------------------------------------------------------------------------
def summary(uid: int) -> MessageSummary:
    """Return a MessageSummary, for plan tests that need no real mail."""
    return MessageSummary(
        uid=uid, date="", sender="a@example.com", subject="s", folder="INBOX"
    )


# ############################################################################
# Folder naming
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "delimiter", "known", "expected"),
    [
        pytest.param(
            "Lists/GitHub",
            ".",
            None,
            "INBOX.Lists.GitHub",
            id="maildir-new-folder-gains-the-inbox-prefix",
        ),
        pytest.param(
            "INBOX.Lists.GitHub",
            ".",
            None,
            "INBOX.Lists.GitHub",
            id="maildir-name-already-carrying-the-prefix",
        ),
        pytest.param(
            "Lists/GitHub",
            "/",
            None,
            "Lists/GitHub",
            id="slash-server-keeps-it-top-level",
        ),
        pytest.param(
            "INBOX/Lists/GitHub",
            "/",
            None,
            "INBOX/Lists/GitHub",
            id="slash-name-already-carrying-the-prefix",
        ),
        # A dot is only a separator on a server that reports it as one, so
        # on a /-delimited server this stays one literal folder name. That
        # is deliberate: the alternative is deciding that no folder may
        # ever contain a dot in its name.
        pytest.param(
            "INBOX.Lists.GitHub",
            "/",
            None,
            "INBOX.Lists.GitHub",
            id="dots-are-not-separators-on-a-slash-server",
        ),
        pytest.param(
            "INBOX", ".", None, "INBOX", id="inbox-itself-is-untouched"
        ),
        pytest.param(
            "Lists/GitHub",
            ".",
            ["INBOX.Lists.GitHub"],
            "INBOX.Lists.GitHub",
            id="existing-folder-looked-up",
        ),
        # Only INBOX is case-insensitive (RFC 3501 section 5.1, #56): the
        # rest of the name is taken as typed, never matched to a folder
        # whose case differs.
        pytest.param(
            "inbox.lists.github",
            ".",
            ["INBOX.Lists.GitHub"],
            "INBOX.lists.github",
            id="only-inbox-case-is-folded",
        ),
        pytest.param(
            "Inbox/Lists/GitHub",
            ".",
            ["INBOX.Lists.GitHub"],
            "INBOX.Lists.GitHub",
            id="inbox-in-any-case-finds-the-folder",
        ),
        pytest.param(
            "spam",
            ".",
            ["INBOX.spam"],
            "INBOX.spam",
            id="lowercase-spam-folder-is-found-not-guessed",
        ),
    ],
)
def test_normalize_folder(name, delimiter, known, expected):
    """``Lists/GitHub`` and ``INBOX.Lists.GitHub`` name the same folder.

    Users type whichever spelling they have seen. The delimiter comes from
    the server's own LIST response, so this function is where a
    ``.``-delimited Maildir++ account and a ``/``-delimited one stop being
    two different user experiences.
    """
    assert normalize_folder(name, delimiter, known) == expected


# ----------------------------------------------------------------------------
def test_normalize_prefers_an_existing_folder_over_the_guess():
    """Matching the server's list is what avoids a near-duplicate folder.

    Filing into ``INBOX.Junk`` when the account's spam folder is
    ``INBOX.spam`` creates a second folder beside the real one, and the
    mail lands where nothing looks.
    """
    assert normalize_folder("spam", ".", ["INBOX.spam"]) == "INBOX.spam"

    # No such folder: the guess is used, and it is a *new* folder name.
    assert normalize_folder("Junk", ".", ["INBOX.spam"]) == "INBOX.Junk"


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "prefix", "expected"),
    [
        pytest.param("Probe", "", "Probe", id="empty-prefix-is-the-root"),
        pytest.param(
            "Lists/GitHub", "", "Lists.GitHub", id="empty-prefix-nested"
        ),
        pytest.param(
            "INBOX/Probe", "", "INBOX.Probe", id="empty-prefix-asked-inbox"
        ),
        pytest.param(
            "Probe", "INBOX.", "INBOX.Probe", id="inbox-prefix-goes-under"
        ),
        pytest.param(
            "inbox.Probe",
            "INBOX.",
            "INBOX.Probe",
            id="inbox-prefix-not-doubled",
        ),
        pytest.param(
            "Probe", None, "INBOX.Probe", id="no-prefix-keeps-the-guess"
        ),
    ],
)
def test_a_new_folder_goes_under_the_namespace_prefix(name, prefix, expected):
    """#116: the server's personal namespace decides a new folder's parent.

    MXroute is ``.``-delimited with an empty personal prefix, so a new
    folder belongs at the root; the old Maildir++ guess put it under
    ``INBOX``. The guess survives only where the prefix is unknown.
    """
    assert normalize_folder(name, ".", ["INBOX"], prefix) == expected


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("prefix", ["", "INBOX.", None])
def test_the_namespace_prefix_never_changes_an_existing_folders_lookup(
    prefix,
):
    """#116: only a folder that does not exist yet is placed by the prefix."""
    known = ["INBOX", "INBOX.Lists.GitHub", "Archive", "INBOX.spam"]

    assert (
        normalize_folder("Lists/GitHub", ".", known, prefix)
        == "INBOX.Lists.GitHub"
    )
    assert (
        normalize_folder("INBOX.Lists.GitHub", ".", known, prefix)
        == "INBOX.Lists.GitHub"
    )
    assert normalize_folder("spam", ".", known, prefix) == "INBOX.spam"
    assert normalize_folder("Archive", ".", known, prefix) == "Archive"


# ----------------------------------------------------------------------------
def test_case_variants_are_the_folders_that_differ_only_in_case():
    """#56: what exact matching would otherwise hide from the user."""
    known = ["INBOX", "INBOX.Lists", "INBOX.LISTS", "INBOX.spam"]

    assert case_variants("INBOX.lists", known) == [
        "INBOX.Lists",
        "INBOX.LISTS",
    ]
    assert case_variants("INBOX.Lists", known) == ["INBOX.LISTS"]
    assert case_variants("inbox", known) == []
    assert case_variants("INBOX.Junk", known) == []


# ----------------------------------------------------------------------------
def test_exists_is_exact_except_for_inbox(imap_session):
    """#56: on Dovecot Maildir++ INBOX.Lists and INBOX.lists are two
    folders, so one existing says nothing about the other."""
    listing = mxroute(imap=imap_session).transport.list_folders()

    assert listing.exists("INBOX.Lists") is True
    assert listing.exists("INBOX.lists") is False
    assert listing.exists("inbox") is True


# ----------------------------------------------------------------------------
def test_a_missing_folder_names_its_case_variant(imap_session, fake_imap):
    """Selecting a folder that exists only in another case says so."""
    fake_imap.failures["select_folder"] = IMAPClientError("no such mailbox")

    with pytest.raises(MailctlError, match=r"'INBOX\.Lists' exists"):
        imap_session._select("INBOX.lists")


# ----------------------------------------------------------------------------
def test_normalize_refuses_an_empty_folder_name():
    with pytest.raises(MailctlError, match="empty folder name"):
        normalize_folder("///", ".")


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "delimiter", "parts"),
    [
        pytest.param("Lists/GitHub", ".", ["Lists", "GitHub"], id="slashes"),
        pytest.param("A.B", ".", ["A", "B"], id="delimiter"),
        pytest.param("A.B/C", ".", ["A", "B", "C"], id="both-mixed"),
        pytest.param("//a//", ".", ["a"], id="empty-components-dropped"),
        pytest.param("A.B", "/", ["A.B"], id="dot-is-not-special-here"),
    ],
)
def test_split_path(name, delimiter, parts):
    assert split_path(name, delimiter) == parts


# ############################################################################
# Header decoding
# ############################################################################


# ----------------------------------------------------------------------------
def test_decode_header_value_decodes_an_encoded_word():
    """Sieve compares against the decoded value, so mailctl must too."""
    assert decode_header_value("=?utf-8?q?caf=C3=A9?=") == "café"


# ----------------------------------------------------------------------------
def test_decode_header_value_falls_back_to_the_raw_text():
    """A malformed encoded word must not abort the whole retroactive pass."""
    malformed = "=?bogus-charset?q?x?="

    assert decode_header_value(malformed) == malformed


# ----------------------------------------------------------------------------
def test_decode_header_value_survives_a_bad_base64_encoded_word():
    """The stdlib raises HeaderParseError, which is not a ValueError."""
    malformed = "=?utf-8?b?G=?="

    assert decode_header_value(malformed) == malformed


# ----------------------------------------------------------------------------
def test_header_values_keeps_both_the_decoded_and_the_raw_form():
    """Broader candidates cost nothing; a missed match costs a lost mail.

    A user who copied the literal encoded text out of a header dump still
    finds their message, while ``matches`` against the decoded value works
    the way Sieve will.
    """
    import email

    raw = b"Subject: =?utf-8?q?caf=C3=A9?=\r\nFrom: a@example.com\r\n\r\n"
    values = header_values(email.message_from_bytes(raw))

    assert "café" in values["SUBJECT"]
    assert "=?utf-8?q?caf=C3=A9?=" in values["SUBJECT"]
    assert values["FROM"] == ["a@example.com"]


# ############################################################################
# Session lifecycle
# ############################################################################


# ----------------------------------------------------------------------------
def test_open_reads_the_delimiter_and_the_folder_list(imap_session):
    """Discovered, never hardcoded -- MXroute documents neither."""
    assert imap_session.delimiter == "."
    assert imap_session.folders == ["INBOX", "INBOX.Lists", "INBOX.spam"]


# ----------------------------------------------------------------------------
def test_open_reads_an_empty_personal_prefix_and_places_new_folders_at_root(
    fake_imap,
):
    """#116: MXroute answers NAMESPACE with personal ``(("", "."),)``."""
    fake_imap.caps.add("NAMESPACE")
    fake_imap.namespace_response = ((("", "."),), None, None)
    fake_imap.listing = [((), b".", b"INBOX"), ((), b".", b"Archive")]

    with plain_session() as session:
        assert session.namespace_prefix == ""
        assert (
            normalized(session, "MailctlDryRunProbe") == "MailctlDryRunProbe"
        )

    assert fake_imap.names().count("namespace") == 1


# ----------------------------------------------------------------------------
def test_open_reads_an_inbox_personal_prefix(fake_imap):
    fake_imap.caps.add("NAMESPACE")
    fake_imap.namespace_response = ((("INBOX.", "."),), None, None)

    with plain_session() as session:
        assert session.namespace_prefix == "INBOX."
        assert normalized(session, "Probe") == "INBOX.Probe"


# ----------------------------------------------------------------------------
def test_without_namespace_the_prefix_is_unknown_and_the_guess_stands(
    fake_imap,
):
    """Not advertised: NAMESPACE is never sent, and the fallback applies."""
    with plain_session() as session:
        assert session.namespace_prefix is None
        assert normalized(session, "Probe") == "INBOX.Probe"

    assert "namespace" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_a_failing_namespace_falls_back_rather_than_failing_open(fake_imap):
    fake_imap.caps.add("NAMESPACE")
    fake_imap.failures["namespace"] = IMAPClientError("BAD")

    with plain_session() as session:
        assert session.namespace_prefix is None
        assert normalized(session, "Probe") == "INBOX.Probe"


# ----------------------------------------------------------------------------
def test_no_personal_namespace_falls_back(fake_imap):
    fake_imap.caps.add("NAMESPACE")
    fake_imap.namespace_response = (None, None, None)

    with plain_session() as session:
        assert session.namespace_prefix is None


# ----------------------------------------------------------------------------
def test_ssl_is_implicit_tls_and_starttls_upgrades_a_plain_socket(fake_imap):
    """Implicit TLS and STARTTLS are different protocols, not port numbers."""
    plain_session().open()

    assert fake_imap.connected_to == ("mail.example.com", 993, True)
    assert fake_imap.starttls_called is False

    plain_session(port=143, tls="starttls").open()

    assert fake_imap.connected_to == ("mail.example.com", 143, False)
    assert fake_imap.starttls_called is True


# ----------------------------------------------------------------------------
def test_a_login_failure_is_its_own_error_and_never_holds_the_password(
    fake_imap,
):
    """The provider adds host advice; the session says only what failed."""
    fake_imap.failures["login"] = LoginError("no")

    with pytest.raises(ImapAuthenticationError) as caught:
        plain_session().open()

    assert caught.value.username == "user@example.com"
    assert caught.value.reason == "no"
    assert "not-a-real-password" not in str(caught.value)


# ----------------------------------------------------------------------------
def test_the_password_is_asked_for_only_as_the_login_is_made(fake_imap):
    """A prompt or a credential command runs when needed, never before."""
    asked = []

    def password():
        asked.append(True)

        return Secret("not-a-real-password")

    session = plain_session(password=password)

    assert asked == []

    session.open()

    assert asked == [True]


# ----------------------------------------------------------------------------
def test_calling_a_method_before_open_fails_clearly():
    session = plain_session()

    with pytest.raises(MailctlError, match="IMAP session is not open"):
        session.search_uids(Criteria(), "INBOX")


# ----------------------------------------------------------------------------
def test_close_is_safe_to_call_twice(imap_session, fake_imap):
    """Cleanup runs on the error path too, where the socket may be gone."""
    imap_session.close()
    imap_session.close()

    assert fake_imap.names().count("logout") == 1


# ----------------------------------------------------------------------------
def test_the_session_works_as_a_context_manager(fake_imap):
    with plain_session() as session:
        assert session.delimiter == "."

    assert "logout" in fake_imap.names()


# ----------------------------------------------------------------------------
def test_the_progress_callback_receives_steps_instead_of_printing(fake_imap):
    """The core returns data and reports through a callback; only the CLI
    prints (CONVENTIONS.md). A second front-end depends on that holding."""
    seen = []

    plain_session(progress=seen.append).open()

    assert any("connecting to" in line for line in seen)
    assert any("delimiter" in line for line in seen)


# ############################################################################
# Search and the post-filter
# ############################################################################


# ----------------------------------------------------------------------------
def test_search_rejects_an_imap_hit_that_fails_the_strict_comparison(
    imap_session, fake_imap
):
    """The gap between IMAP SEARCH and Sieve, closed in Python.

    IMAP can only substring-match, so searching for the longest literal of
    ``*@lists.example.com`` returns both messages below. Only the bare
    address satisfies Sieve's whole-value ``:matches``; the one with a
    display name must be dropped, or the retroactive pass moves mail the
    server-side filter will leave alone.
    """
    fake_imap.messages = {
        1: message("Announce <announce@lists.example.com>"),
        2: message("announce@lists.example.com"),
        3: message("someone@other.example.com"),
    }

    criteria = Criteria(compare="matches")
    criteria.add("from", "*@lists.example.com")

    found = matching(imap_session, criteria, "INBOX")

    assert [item.uid for item in found] == [2]

    # The IMAP side really was broader -- all three were fetched.
    assert ("fetch", (1, 2, 3)) in fake_imap.calls


# ----------------------------------------------------------------------------
def test_search_rejects_a_substring_hit_under_compare_is(
    imap_session, fake_imap
):
    """``--compare is`` is exact; IMAP's SUBJECT key is not."""
    fake_imap.messages = {
        1: message("a@example.com", subject="Re: Weekly Report"),
        2: message("a@example.com", subject="Weekly Report"),
    }

    criteria = Criteria(compare="is")
    criteria.add("subject", "Weekly Report")

    assert [
        item.uid for item in matching(imap_session, criteria, "INBOX")
    ] == [2]


# ----------------------------------------------------------------------------
def test_the_session_searches_by_the_key_alone_and_narrows_nothing(
    imap_session, fake_imap
):
    """The re-check left the component (ADR 0007): it needs only a search
    key, and hands back every candidate the server matched."""
    fake_imap.messages = {
        1: message("a@example.com"),
        2: message("b@example.com"),
    }

    class KeyOnly:
        def imap_search_key(self):
            return [["FROM", "a@example.com"]]

    uids = imap_session.search_uids(KeyOnly(), "INBOX")
    fetched = imap_session.fetch_headers(uids, "INBOX")

    assert uids == [1, 2]
    assert [item.summary.uid for item in fetched] == [1, 2]
    assert fetched[1].headers["From"] == "b@example.com"


# ----------------------------------------------------------------------------
def selects(fake_imap) -> list[str]:
    """Every folder the double was asked to select, in order."""
    return [call[1] for call in fake_imap.calls if call[0] == "select_folder"]


# ----------------------------------------------------------------------------
def test_a_fetch_selects_its_own_folder_rather_than_trusting_the_last(
    imap_session, fake_imap
):
    """A fetch names its folder: after a search elsewhere it selects the
    one it was given, and right after a search of the same folder it
    sends nothing more."""
    fake_imap.messages = {1: message("a@example.com")}

    class KeyOnly:
        def imap_search_key(self):
            return [["ALL"]]

    uids = imap_session.search_uids(KeyOnly(), "INBOX")
    imap_session.fetch_headers(uids, "INBOX")

    assert selects(fake_imap) == ["INBOX"]

    imap_session.fetch_summaries(uids, "INBOX.Lists")
    imap_session.fetch_headers(uids, "INBOX")

    assert selects(fake_imap) == ["INBOX", "INBOX.Lists", "INBOX"]


# ----------------------------------------------------------------------------
def test_a_new_connection_selects_before_it_fetches(imap_session, fake_imap):
    """A reconnect starts with nothing selected, whatever the last
    connection had."""
    fake_imap.messages = {1: message("a@example.com")}
    imap_session.fetch_headers([1], "INBOX")

    imap_session.close()
    imap_session.open()
    imap_session.fetch_headers([1], "INBOX")

    assert selects(fake_imap) == ["INBOX", "INBOX"]


# ----------------------------------------------------------------------------
def test_search_returns_nothing_without_fetching_when_imap_found_nothing(
    imap_session, fake_imap
):
    criteria = Criteria()
    criteria.add("from", "nobody@example.com")

    assert matching(imap_session, criteria, "INBOX") == []
    assert "fetch" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_a_matched_message_carries_its_decoded_summary(
    imap_session, fake_imap
):
    fake_imap.messages = {
        7: b"From: =?utf-8?q?Jos=C3=A9?= <j@example.com>\r\n"
        b"Subject: Hola\r\n\r\n"
    }

    criteria = Criteria()
    criteria.add("from", "j@example.com")

    found = matching(imap_session, criteria, "INBOX")

    assert found[0].sender == "José <j@example.com>"
    assert found[0].subject == "Hola"
    assert found[0].date == "2026-02-03 04:05:06"
    assert found[0].folder == "INBOX"


# ----------------------------------------------------------------------------
def test_a_raw_utf8_header_is_summarized_as_its_text(imap_session, fake_imap):
    """RFC 6532: an 8-bit header is UTF-8, not replacement characters (#97)."""
    fake_imap.messages = {
        7: "From: zoë@exemple.fr\r\nSubject: café\r\n\r\n".encode()
    }

    criteria = Criteria()
    criteria.add("from", "zoë@exemple.fr")

    found = matching(imap_session, criteria, "INBOX")

    assert found[0].sender == "zoë@exemple.fr"
    assert found[0].subject == "café"


# ----------------------------------------------------------------------------
def test_a_header_that_is_not_utf8_is_summarized_as_before(
    imap_session, fake_imap
):
    """Latin-1 bytes still read as replacement characters, and never raise."""
    fake_imap.messages = {7: b"From: Jos\xe9 <j@example.com>\r\n\r\n"}

    criteria = Criteria()
    criteria.add("from", "j@example.com")

    found = matching(imap_session, criteria, "INBOX")

    assert found[0].sender == "Jos\ufffd <j@example.com>"


# ----------------------------------------------------------------------------
def test_a_search_failure_names_the_folder(imap_session, fake_imap):
    fake_imap.messages = {1: message("a@example.com")}
    fake_imap.failures["search"] = IMAPClientError("SEARCH rejected")

    criteria = Criteria()
    criteria.add("from", "a@example.com")

    with pytest.raises(MailctlError, match=r"search in 'INBOX' failed"):
        matching(imap_session, criteria, "INBOX")


# ----------------------------------------------------------------------------
def test_selecting_a_missing_folder_points_at_the_folders_command(
    imap_session, fake_imap
):
    """Folder names are discovered, so the error has to say where to look."""
    fake_imap.failures["select_folder"] = IMAPClientError("no such mailbox")

    criteria = Criteria()
    criteria.add("from", "a@example.com")

    with pytest.raises(MailctlError, match=r"Run 'mailctl folders'"):
        matching(imap_session, criteria, "INBOX.Nope")


# ############################################################################
# Plan / execute
# ############################################################################


# ----------------------------------------------------------------------------
def test_planning_never_writes_anything(imap_session, fake_imap):
    """A dry run is a plan that is not executed, not a flag threaded down.

    The mailbox is opened read-only while planning, so building a plan is
    safe even when the caller then decides against it.
    """
    fake_imap.messages = {1: message("a@example.com")}

    criteria = Criteria()
    criteria.add("from", "a@example.com")

    plan = plan_actions(
        imap_session,
        criteria,
        "INBOX",
        destination="INBOX.Lists",
        flags=["\\Seen"],
    )

    assert plan.count == 1
    assert ("select_folder", "INBOX", True) in fake_imap.calls

    mutations = {"add_flags", "move", "copy", "expunge", "uid_expunge"}
    assert mutations.isdisjoint(fake_imap.names())


# ----------------------------------------------------------------------------
def test_execute_flags_before_it_moves(imap_session, fake_imap):
    """A move invalidates the UIDs, so the order is load-bearing."""
    fake_imap.messages = {1: message("a@example.com")}

    criteria = Criteria()
    criteria.add("from", "a@example.com")

    plan = plan_actions(
        imap_session,
        criteria,
        "INBOX",
        destination="INBOX.Lists",
        flags=["\\Seen"],
    )
    result = imap_session.execute(plan)

    names = fake_imap.names()

    assert names.index("add_flags") < names.index("move")
    assert ("add_flags", (1,), (b"\\Seen",)) in fake_imap.calls
    assert ("move", (1,), "INBOX.Lists") in fake_imap.calls
    assert result.flagged == 1
    assert result.moved == 1
    assert result.deleted == 0


# ----------------------------------------------------------------------------
def test_execute_reopens_the_folder_writable(imap_session, fake_imap):
    fake_imap.messages = {1: message("a@example.com")}

    criteria = Criteria()
    criteria.add("from", "a@example.com")

    imap_session.execute(plan_actions(imap_session, criteria, "INBOX"))

    assert ("select_folder", "INBOX", False) in fake_imap.calls


# ----------------------------------------------------------------------------
def test_execute_on_an_empty_plan_touches_nothing(imap_session, fake_imap):
    plan = MailActionPlan(
        source="INBOX",
        destination="INBOX.Lists",
        flags=["\\Seen"],
        discard=False,
        messages=[],
    )

    result = imap_session.execute(plan)

    assert result == type(result)()
    assert "select_folder" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_discard_deletes_and_never_moves(imap_session, fake_imap):
    fake_imap.messages = {1: message("a@example.com")}

    criteria = Criteria()
    criteria.add("from", "a@example.com")

    plan = plan_actions(
        imap_session,
        criteria,
        "INBOX",
        destination="INBOX.Lists",
        discard=True,
    )
    result = imap_session.execute(plan)

    assert result.deleted == 1
    assert result.moved == 0
    assert "move" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_the_move_falls_back_to_copy_and_expunge_without_the_capability(
    imap_session, fake_imap
):
    """RFC 6851 MOVE is atomic; the fallback has to be exactly equivalent.

    UID EXPUNGE is used when UIDPLUS is advertised so a concurrent client's
    deleted mail is not expunged along with ours.
    """
    fake_imap.caps = {"UIDPLUS"}

    assert imap_session.move([1, 2], "INBOX.Lists") == 2

    assert ("copy", (1, 2), "INBOX.Lists") in fake_imap.calls
    assert ("add_flags", (1, 2), (b"\\Deleted",)) in fake_imap.calls
    assert ("uid_expunge", (1, 2)) in fake_imap.calls
    assert "expunge" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_the_move_fallback_uses_a_plain_expunge_without_uidplus(
    imap_session, fake_imap
):
    fake_imap.caps = set()

    imap_session.move([1], "INBOX.Lists")

    assert "expunge" in fake_imap.names()
    assert "uid_expunge" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_a_move_failure_names_the_destination(imap_session, fake_imap):
    fake_imap.failures["move"] = IMAPClientError("over quota")

    with pytest.raises(MailctlError, match=r"move messages to 'INBOX.Lists'"):
        imap_session.move([1], "INBOX.Lists")


# ----------------------------------------------------------------------------
def test_moving_or_deleting_nothing_is_a_no_op(imap_session, fake_imap):
    assert imap_session.move([], "INBOX.Lists") == 0
    assert imap_session.delete([]) == 0
    assert fake_imap.calls == [("login", "user@example.com")]


# ############################################################################
# Plan reporting
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("source", "destination", "discard", "moves"),
    [
        pytest.param("INBOX", "INBOX.Lists", False, True, id="a-real-move"),
        pytest.param("INBOX", "", False, False, id="no-destination"),
        pytest.param("INBOX", "INBOX", False, False, id="same-folder"),
        pytest.param("INBOX", "inbox", False, False, id="same-folder-cased"),
        pytest.param("INBOX", "INBOX.Lists", True, False, id="discard-wins"),
    ],
)
def test_a_plan_knows_whether_it_relocates_mail(
    source, destination, discard, moves
):
    """The CLI's confirmation prompt escalates on this, so it must be right."""
    plan = MailActionPlan(
        source=source,
        destination=destination,
        flags=[],
        discard=discard,
        messages=[summary(1)],
    )

    assert plan.moves is moves


# ----------------------------------------------------------------------------
def test_a_plan_reports_its_size_and_uids():
    plan = MailActionPlan(
        source="INBOX",
        destination="",
        flags=[],
        discard=False,
        messages=[summary(3), summary(1)],
    )

    assert plan.count == 2
    assert plan.uids == [3, 1]
    assert plan.is_empty is False


# ----------------------------------------------------------------------------
def test_an_empty_plan_reports_itself_as_empty():
    plan = MailActionPlan(
        source="INBOX", destination="", flags=[], discard=False, messages=[]
    )

    assert plan.is_empty is True
    assert plan.count == 0
    assert plan.uids == []


# ############################################################################
# Chunked bulk operations (#24)
# ############################################################################

MANY = 600  # three chunks: 250, 250, 100


# ----------------------------------------------------------------------------
def big_plan(**kwargs) -> MailActionPlan:
    fields = {"source": "INBOX", "destination": "INBOX.Lists", "flags": []}
    fields.update(kwargs)

    return MailActionPlan(
        discard=fields.pop("discard", False),
        messages=[summary(uid) for uid in range(1, MANY + 1)],
        **fields,
    )


# ----------------------------------------------------------------------------
def chunk_sizes(fake_imap, name: str) -> list[int]:
    return [len(call[1]) for call in fake_imap.calls if call[0] == name]


# ----------------------------------------------------------------------------
def test_the_chunk_stays_below_the_default_message_cap():
    """Independent of --max-messages, so raising the cap is never a risk."""
    from mailctl.utilities.mail import DEFAULT_MAX_MESSAGES

    assert BULK_CHUNK < DEFAULT_MAX_MESSAGES


# ----------------------------------------------------------------------------
def test_a_large_move_goes_out_in_chunks_each_flagged_first(
    imap_session, fake_imap
):
    result = imap_session.execute(big_plan(flags=["\\Seen"]))

    assert result.moved == MANY
    assert result.flagged == MANY
    assert chunk_sizes(fake_imap, "move") == [250, 250, 100]
    assert chunk_sizes(fake_imap, "add_flags") == [250, 250, 100]

    writes = [
        name for name in fake_imap.names() if name in ("add_flags", "move")
    ]

    assert writes == ["add_flags", "move"] * 3


# ----------------------------------------------------------------------------
def test_a_large_delete_is_chunked_too(imap_session, fake_imap):
    result = imap_session.execute(big_plan(discard=True, destination=""))

    assert result.deleted == MANY
    assert chunk_sizes(fake_imap, "uid_expunge") == [250, 250, 100]


# ----------------------------------------------------------------------------
def test_the_copy_fallback_is_chunked(imap_session, fake_imap):
    fake_imap.caps = {"UIDPLUS"}

    imap_session.execute(big_plan())

    assert chunk_sizes(fake_imap, "copy") == [250, 250, 100]
    assert chunk_sizes(fake_imap, "uid_expunge") == [250, 250, 100]


# ----------------------------------------------------------------------------
def test_the_header_fetch_for_a_broad_search_is_chunked(
    imap_session, fake_imap
):
    fake_imap.messages = {
        uid: message("list@example.com") for uid in range(1, MANY + 1)
    }
    criteria = Criteria()
    criteria.add("From", "list@example.com")

    found = matching(imap_session, criteria, "INBOX")

    assert len(found) == MANY
    assert chunk_sizes(fake_imap, "fetch") == [250, 250, 100]


# ----------------------------------------------------------------------------
def fail_on_call(fake_imap, name: str, number: int):
    """Make the ``number``th call to ``name`` raise, and only that one."""
    original = getattr(fake_imap, name)
    seen = []

    def flaky(*args, **kwargs):
        seen.append(1)

        if len(seen) == number:
            raise IMAPClientError("line too long")

        return original(*args, **kwargs)

    setattr(fake_imap, name, flaky)


# ----------------------------------------------------------------------------
def test_a_failure_part_way_reports_what_completed_and_stops(
    imap_session, fake_imap
):
    fail_on_call(fake_imap, "move", 2)

    with pytest.raises(PartialExecution) as caught:
        imap_session.execute(big_plan())

    assert caught.value.result.moved == 250
    assert caught.value.total == MANY
    assert chunk_sizes(fake_imap, "move") == [250]

    message = str(caught.value)

    assert "250 of 600" in message
    assert "Re-running the same command is safe" in message
    assert "not yet removed from the source" not in message


# ----------------------------------------------------------------------------
def test_a_failure_in_the_copy_fallback_warns_of_duplicates(
    imap_session, fake_imap
):
    fake_imap.caps = {"UIDPLUS"}
    fail_on_call(fake_imap, "copy", 2)

    with pytest.raises(PartialExecution) as caught:
        imap_session.execute(big_plan())

    message = str(caught.value)

    # Re-running copies the failed batch again, so "safe" would be false.
    assert "not yet removed from the source" in message
    assert "is safe" not in message
    assert "copies them again" in message
    assert "'INBOX.Lists'" in message


# ----------------------------------------------------------------------------
def test_a_partial_delete_says_re_running_is_safe(imap_session, fake_imap):
    fail_on_call(fake_imap, "uid_expunge", 2)

    with pytest.raises(PartialExecution, match="is safe"):
        imap_session.execute(big_plan(discard=True, destination=""))


# ----------------------------------------------------------------------------
def test_a_failure_in_the_first_chunk_is_the_plain_error(
    imap_session, fake_imap
):
    """Nothing completed, so there is no partial state to describe."""
    fail_on_call(fake_imap, "move", 1)

    with pytest.raises(MailctlError) as caught:
        imap_session.execute(big_plan())

    assert not isinstance(caught.value, PartialExecution)


# ############################################################################
# Setting and clearing flags by UID (#150)
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("method", "call"),
    [
        ("add_folder_flags", "add_flags"),
        ("remove_folder_flags", "remove_flags"),
    ],
)
def test_a_flag_store_selects_its_folder_writable_and_sends_one_sign(
    imap_session, fake_imap, method, call
):
    """Red if the folder is opened read-only (the server refuses STORE),
    or if a method sends the other sign's STORE."""
    getattr(imap_session, method)("INBOX.Lists", [3, 4], ["\\Seen", "$Todo"])

    assert fake_imap.calls[-2:] == [
        ("select_folder", "INBOX.Lists", False),
        (call, (3, 4), (b"\\Seen", b"$Todo")),
    ]
    other = "remove_flags" if call == "add_flags" else "add_flags"

    assert other not in fake_imap.names()


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("method", ["add_folder_flags", "remove_folder_flags"])
def test_a_flag_store_with_nothing_to_do_sends_nothing(
    imap_session, fake_imap, method
):
    before = list(fake_imap.calls)

    getattr(imap_session, method)("INBOX", [], ["\\Seen"])
    getattr(imap_session, method)("INBOX", [1], [])

    assert fake_imap.calls == before


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("method", "call", "verb"),
    [
        ("add_folder_flags", "add_flags", "set"),
        ("remove_folder_flags", "remove_flags", "clear"),
    ],
)
def test_a_refused_flag_store_names_the_folder(
    imap_session, fake_imap, method, call, verb
):
    fake_imap.failures[call] = IMAPClientError("STORE failed")

    with pytest.raises(
        MailctlError, match=f"could not {verb} flags in 'INBOX'"
    ):
        getattr(imap_session, method)("INBOX", [1], ["\\Flagged"])


# ----------------------------------------------------------------------------
def test_a_large_flag_store_goes_out_in_chunks(imap_session, fake_imap):
    imap_session.remove_folder_flags(
        "INBOX", list(range(1, MANY + 1)), ["\\Seen"]
    )

    assert chunk_sizes(fake_imap, "remove_flags") == [250, 250, 100]
