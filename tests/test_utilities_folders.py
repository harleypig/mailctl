"""Folders -- listed, subscribed, and planned as a target -- driven as a
front-end would drive them.

No argparse and no stdout here: every test builds plain inputs, calls
the utility, and asserts on what comes back and on what the fakes were
asked to do.

The Sieve side is a session-level fake (``FakeSieveSession``); the IMAP
side is the real ``ImapSession`` over the ``FakeIMAPClient`` double from
conftest, so folder normalization and planning run for real.
"""

import pytest
from imapclient.exceptions import IMAPClientError
from utilities_support import NO_MAILBOX, FakeSieveSession, criteria, mxroute

from mailctl import MailctlError, utilities
from mailctl.providers.mxroute.imap import new_imap_session
from mailctl.utilities.mail import MailActionPlan
from mailctl.utilities.rules import (
    ActionSpec,
    RuleRequest,
)

# ############################################################################
# The target folder
# ############################################################################


# ----------------------------------------------------------------------------
def test_no_folder_means_no_folder_plan(sessions, imap_config):
    plan = utilities.folders.plan_folder(sessions, imap_config, None)

    assert plan.status == utilities.folders.FOLDER_NONE


# ----------------------------------------------------------------------------
def test_the_config_default_folder_is_used_when_none_is_given(
    sessions, imap_config
):
    imap_config.default_folder = "Lists"

    plan = utilities.folders.plan_folder(sessions, imap_config, None)

    assert plan.folder == "INBOX.Lists"
    assert plan.status == utilities.folders.FOLDER_EXISTS


# ----------------------------------------------------------------------------
def test_a_missing_folder_is_reported_not_created(
    sessions, imap_config, fake_imap
):
    plan = utilities.folders.plan_folder(sessions, imap_config, "Lists/GitHub")

    assert plan.folder == "INBOX.Lists.GitHub"
    assert plan.status == utilities.folders.FOLDER_MISSING
    assert "create_folder" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_with_mailbox_and_imap_the_folder_is_made_both_ways(
    sessions, imap_config, fake_imap
):
    """Sieve's :create stays as the fallback; IMAP makes it visible now."""
    plan = utilities.folders.plan_folder(
        sessions, imap_config, "New", create=True
    )

    assert plan.status == utilities.folders.FOLDER_BOTH_CREATE
    assert plan.use_create
    assert utilities.folders.folder_pending(sessions, plan)
    assert "create_folder" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_without_imap_only_sieve_creates_the_folder(fake_sieve, imap_config):
    """--no-imap: nothing can create or subscribe it now (#40)."""
    live = mxroute(sieve=fake_sieve, imap=None)

    plan = utilities.folders.plan_folder(live, imap_config, "New", create=True)

    assert plan.status == utilities.folders.FOLDER_SIEVE_CREATES
    assert plan.use_create
    assert not utilities.folders.folder_pending(live, plan)


# ----------------------------------------------------------------------------
def test_without_mailbox_the_folder_is_planned_for_imap_then_created(
    imap_session, imap_config, fake_imap
):
    live = mxroute(FakeSieveSession(caps=NO_MAILBOX), imap_session)

    plan = utilities.folders.plan_folder(
        live, imap_config, "New", create=True, subscribe=False
    )

    assert plan.status == utilities.folders.FOLDER_IMAP_CREATE
    assert "create_folder" not in fake_imap.names()

    result = utilities.folders.create_folder(live, plan)

    assert result.folder == "INBOX.New"
    assert not result.subscribed
    assert ("create_folder", "INBOX.New") in fake_imap.calls
    assert "subscribe_folder" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_a_new_folder_is_planned_under_the_servers_namespace_prefix(
    fake_imap, fake_sieve, imap_config
):
    """#116: an empty personal prefix plans ``X``, not ``INBOX.X``."""
    fake_imap.caps.add("NAMESPACE")
    fake_imap.namespace_response = ((("", "."),), None, None)
    session = new_imap_session(imap_config)
    session.open()
    live = mxroute(sieve=fake_sieve, imap=session)

    plan = utilities.folders.plan_folder(
        live, imap_config, "Probe", create=True
    )

    assert plan.folder == "Probe"


# ----------------------------------------------------------------------------
def test_creating_a_folder_that_was_not_planned_for_it_is_refused(
    sessions, imap_config
):
    plan = utilities.folders.plan_folder(sessions, imap_config, "Lists")

    with pytest.raises(MailctlError, match="not planned for IMAP creation"):
        utilities.folders.create_folder(sessions, plan)


# ----------------------------------------------------------------------------
def test_without_imap_the_delimiter_is_assumed_and_said_so(imap_config):
    live = mxroute(sieve=FakeSieveSession())

    plan = utilities.folders.plan_folder(live, imap_config, "Lists/GitHub")

    assert plan.delimiter_assumed
    assert plan.delimiter == "."
    assert plan.folder == "INBOX.Lists.GitHub"


# ----------------------------------------------------------------------------
def test_a_folder_that_cannot_be_created_is_planned_then_refused(
    imap_config,
):
    live = mxroute(sieve=FakeSieveSession(caps=NO_MAILBOX))

    plan = utilities.folders.plan_folder(live, imap_config, "New", create=True)

    assert plan.status == utilities.folders.FOLDER_UNCREATABLE

    with pytest.raises(MailctlError, match="cannot be created"):
        utilities.folders.check_folder(live, plan)

    request = RuleRequest(criteria(), ActionSpec(fileinto="New"))

    with pytest.raises(MailctlError, match="cannot be created"):
        utilities.rules.plan_rule(live, imap_config, request, plan)


# ############################################################################
# Folder creation happens on execute
# ############################################################################


# ----------------------------------------------------------------------------
def imap_created_folder_plan(imap_session, imap_config):
    live = mxroute(FakeSieveSession(caps=NO_MAILBOX), imap_session)
    folder = utilities.folders.plan_folder(
        live, imap_config, "New", create=True
    )
    request = RuleRequest(criteria(), ActionSpec(fileinto="New"))

    return live, utilities.rules.plan_rule(live, imap_config, request, folder)


# ----------------------------------------------------------------------------
def test_a_rule_plan_creates_its_folder_only_on_execute(
    imap_session, imap_config, fake_imap, tmp_path
):
    imap_config.backup_dir = tmp_path
    live, plan = imap_created_folder_plan(imap_session, imap_config)

    assert "create_folder" not in fake_imap.names()

    events = []
    utilities.rules.execute_script_change(
        live, imap_config, plan, events.append
    )

    assert ("create_folder", "INBOX.New") in fake_imap.calls
    assert [type(event) for event in events] == [
        utilities.events.ScriptBackedUp,
        utilities.events.FolderCreated,
        utilities.events.ScriptUploaded,
    ]


# ----------------------------------------------------------------------------
def test_a_rejected_script_leaves_no_folder_behind(
    imap_session, imap_config, fake_imap, tmp_path
):
    imap_config.backup_dir = tmp_path
    live, plan = imap_created_folder_plan(imap_session, imap_config)
    live.transport.sieve.reject = True

    with pytest.raises(MailctlError, match="rejected"):
        utilities.rules.execute_script_change(live, imap_config, plan)

    assert "create_folder" not in fake_imap.names()


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("subscribe", "expected"), [(True, True), (False, False)]
)
def test_a_sieve_create_rule_also_creates_and_subscribes_on_execute(
    sessions, imap_config, fake_imap, tmp_path, subscribe, expected
):
    """The :create path is the default one here, so it has to subscribe
    too, not only the IMAP-creation path (#40)."""
    imap_config.backup_dir = tmp_path
    folder = utilities.folders.plan_folder(
        sessions, imap_config, "New", create=True, subscribe=subscribe
    )
    plan = utilities.rules.plan_rule(
        sessions,
        imap_config,
        RuleRequest(criteria(), ActionSpec(fileinto="New")),
        folder,
    )

    assert 'fileinto :create "INBOX.New"' in plan.after
    assert "create_folder" not in fake_imap.names()

    events = []
    utilities.rules.execute_script_change(
        sessions, imap_config, plan, events.append
    )

    assert ("create_folder", "INBOX.New") in fake_imap.calls
    assert (("subscribe_folder", "INBOX.New") in fake_imap.calls) is expected
    assert [type(event) for event in events] == [
        utilities.events.ScriptBackedUp,
        utilities.events.FolderCreated,
        utilities.events.ScriptUploaded,
    ]


# ----------------------------------------------------------------------------
def test_a_rejected_sieve_create_rule_leaves_no_folder_behind(
    sessions, imap_config, fake_imap, tmp_path
):
    imap_config.backup_dir = tmp_path
    folder = utilities.folders.plan_folder(
        sessions, imap_config, "New", create=True
    )
    plan = utilities.rules.plan_rule(
        sessions,
        imap_config,
        RuleRequest(criteria(), ActionSpec(fileinto="New")),
        folder,
    )
    sessions.transport.sieve.reject = True

    with pytest.raises(MailctlError, match="rejected"):
        utilities.rules.execute_script_change(sessions, imap_config, plan)

    assert "create_folder" not in fake_imap.names()


# ############################################################################
# Subscription
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_folder_listing_says_which_folders_webmail_shows(sessions):
    listing = utilities.folders.list_folders(sessions)

    assert listing.unsubscribed == ["INBOX.spam"]
    assert listing.is_subscribed("INBOX.Lists")
    assert not listing.is_subscribed("INBOX.lists")  # #56: exact
    assert utilities.reports.probe_mail(sessions).unsubscribed == [
        "INBOX.spam"
    ]


# ----------------------------------------------------------------------------
def test_subscribing_is_planned_then_executed(sessions, fake_imap):
    plan = utilities.folders.plan_subscription(
        sessions, "spam", subscribe=True
    )

    assert plan.folder == "INBOX.spam"
    assert plan.changes
    assert "subscribe_folder" not in fake_imap.names()

    utilities.folders.execute_subscription(sessions, plan)

    assert ("subscribe_folder", "INBOX.spam") in fake_imap.calls
    assert utilities.folders.list_folders(sessions).unsubscribed == []


# ----------------------------------------------------------------------------
def test_unsubscribing_hides_without_deleting(sessions, fake_imap):
    plan = utilities.folders.plan_subscription(
        sessions, "Lists", subscribe=False
    )

    utilities.folders.execute_subscription(sessions, plan)

    listing = utilities.folders.list_folders(sessions)

    assert "INBOX.Lists" in listing.folders
    assert "INBOX.Lists" in listing.unsubscribed


# ----------------------------------------------------------------------------
def test_a_plan_that_changes_nothing_does_nothing(sessions, fake_imap):
    plan = utilities.folders.plan_subscription(
        sessions, "Lists", subscribe=True
    )

    assert not plan.changes

    utilities.folders.execute_subscription(sessions, plan)

    assert "subscribe_folder" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_a_missing_folder_cannot_be_subscribed(sessions):
    with pytest.raises(MailctlError, match="nothing to subscribe to"):
        utilities.folders.plan_subscription(
            sessions, "Nowhere", subscribe=True
        )

    with pytest.raises(MailctlError, match="no folder or subscription"):
        utilities.folders.plan_subscription(
            sessions, "Nowhere", subscribe=False
        )


# ----------------------------------------------------------------------------
def test_a_stale_subscription_to_a_gone_folder_can_be_removed(
    sessions, fake_imap
):
    fake_imap.subscriptions.append(((), b".", b"INBOX.Gone"))
    sessions.transport.imap._read_folders()

    plan = utilities.folders.plan_subscription(
        sessions, "Gone", subscribe=False
    )

    assert plan.changes


# ----------------------------------------------------------------------------
def test_a_subscribe_the_server_ignores_is_an_error(sessions, fake_imap):
    fake_imap.subscribe_takes_effect = False
    plan = utilities.folders.plan_subscription(
        sessions, "spam", subscribe=True
    )

    with pytest.raises(MailctlError, match="still does not list it"):
        utilities.folders.execute_subscription(sessions, plan)


# ############################################################################
# Folder names are case-sensitive, except INBOX (RFC 3501 section 5.1)
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("source", "destination", "noop"),
    [
        ("INBOX.Foo", "INBOX.foo", False),
        ("INBOX.foo", "INBOX.foo", True),
        ("inbox", "INBOX", True),
        ("Inbox", "INBOX", True),
    ],
)
def test_only_inbox_compares_case_insensitively(source, destination, noop):
    """On a case-sensitive server INBOX.Foo and INBOX.foo are two folders.

    Treating them as one skipped a real move as a no-op.
    """
    spec = ActionSpec(fileinto=destination)

    assert utilities.mail.mail_pass_is_noop(spec, source, destination) is noop
    assert MailActionPlan(source, destination, [], False).moves is not noop


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("create", [False, True])
def test_a_case_variant_is_reported_not_taken_for_the_folder(
    sessions, imap_config, create
):
    """#56: 'lists' is not INBOX.Lists, and planning must say that one
    exists rather than silently treat the name as absent -- above all
    when --create-folder is about to make a second, differently cased
    folder beside it."""
    plan = utilities.folders.plan_folder(
        sessions, imap_config, "lists", create=create
    )

    assert plan.folder == "INBOX.lists"
    assert plan.status != utilities.folders.FOLDER_EXISTS
    assert plan.case_variants == ("INBOX.Lists",)


# ----------------------------------------------------------------------------
def test_an_exact_folder_has_no_case_variants(sessions, imap_config):
    plan = utilities.folders.plan_folder(sessions, imap_config, "Lists")

    assert plan.status == utilities.folders.FOLDER_EXISTS
    assert plan.case_variants == ()


# ----------------------------------------------------------------------------
def test_subscribing_a_case_variant_names_the_real_folder(sessions):
    with pytest.raises(MailctlError, match=r"'INBOX\.Lists' exists"):
        utilities.folders.plan_subscription(sessions, "lists", subscribe=True)


# ----------------------------------------------------------------------------
def test_the_source_folder_is_normalized_like_the_destination(
    sessions, fake_imap
):
    """--folder Lists and --fileinto Lists name the same folder."""
    fake_imap.listing.append(((), b".", b"INBOX.Lists.X"))
    sessions.transport.imap._read_folders()

    source = utilities.mail.source_folder(sessions, "Lists/X")

    assert source == "INBOX.Lists.X"
    assert utilities.mail.mail_pass_is_noop(
        ActionSpec(fileinto="Lists/X"), source, "INBOX.Lists.X"
    )


# ############################################################################
# A folder created on its own -- 'mailctl create-folder' (#155)
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_new_folder_is_planned_then_created_and_subscribed(
    sessions, fake_imap
):
    plan = utilities.folders.plan_folder_creation(sessions, "Lists/GitHub")

    assert not plan.exists
    assert plan.target.folder == "INBOX.Lists.GitHub"
    assert plan.missing_parents == ()
    assert "create_folder" not in fake_imap.names()

    result = utilities.folders.execute_folder_creation(sessions, plan)

    assert result.folder == "INBOX.Lists.GitHub"
    assert result.subscribed
    assert ("create_folder", "INBOX.Lists.GitHub") in fake_imap.calls
    assert ("subscribe_folder", "INBOX.Lists.GitHub") in fake_imap.calls


# ----------------------------------------------------------------------------
def test_a_new_folder_can_be_created_unsubscribed(sessions, fake_imap):
    plan = utilities.folders.plan_folder_creation(
        sessions, "New", subscribe=False
    )

    result = utilities.folders.execute_folder_creation(sessions, plan)

    assert not result.subscribed
    assert not result.subscribe_error
    assert "subscribe_folder" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_a_new_folder_goes_under_the_servers_namespace_prefix(
    fake_imap, fake_sieve, imap_config
):
    """#116: an empty personal prefix creates ``X``, not ``INBOX.X``."""
    fake_imap.caps.add("NAMESPACE")
    fake_imap.namespace_response = ((("", "."),), None, None)
    session = new_imap_session(imap_config)
    session.open()
    live = mxroute(sieve=fake_sieve, imap=session)

    plan = utilities.folders.plan_folder_creation(live, "Probe")

    assert plan.target.folder == "Probe"


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "subscribed"), [("Lists", True), ("INBOX.spam", False)]
)
def test_an_existing_folder_is_planned_as_nothing_to_create(
    sessions, fake_imap, name, subscribed
):
    """Its subscription is reported, not changed: subscribing an existing
    folder is 'mailctl subscribe', not a side effect of create-folder."""
    plan = utilities.folders.plan_folder_creation(sessions, name)

    assert plan.exists
    assert plan.subscribed_now is subscribed
    assert utilities.folders.execute_folder_creation(sessions, plan) is None
    assert "create_folder" not in fake_imap.names()
    assert "subscribe_folder" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_inbox_itself_exists_whatever_its_case(sessions):
    assert utilities.folders.plan_folder_creation(sessions, "inbox").exists


# ----------------------------------------------------------------------------
def test_a_case_variant_of_an_existing_folder_is_refused(sessions, fake_imap):
    """#56: 'lists' would be a second folder beside INBOX.Lists."""
    with pytest.raises(
        MailctlError, match=r"not creating 'INBOX\.lists': 'INBOX\.Lists'"
    ):
        utilities.folders.plan_folder_creation(sessions, "lists")

    assert "create_folder" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_missing_parents_are_named_in_the_plan(sessions):
    """IMAP CREATE is expected to make the levels above (RFC 3501 6.3.3);
    the plan says which those are, outermost first."""
    plan = utilities.folders.plan_folder_creation(sessions, "Work/2026/Q3")

    assert plan.target.folder == "INBOX.Work.2026.Q3"
    assert plan.missing_parents == ("INBOX.Work", "INBOX.Work.2026")


# ----------------------------------------------------------------------------
def test_an_empty_name_is_refused(sessions):
    with pytest.raises(MailctlError, match="empty folder name"):
        utilities.folders.plan_folder_creation(sessions, "/")


# ----------------------------------------------------------------------------
def test_a_create_the_server_refuses_is_raised(sessions, fake_imap):
    fake_imap.failures["create_folder"] = IMAPClientError("NO [NOPERM]")
    plan = utilities.folders.plan_folder_creation(sessions, "New")

    with pytest.raises(MailctlError, match="could not create folder"):
        utilities.folders.execute_folder_creation(sessions, plan)

    assert "subscribe_folder" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_a_failed_subscription_keeps_the_created_folder(sessions, fake_imap):
    fake_imap.subscribe_takes_effect = False
    plan = utilities.folders.plan_folder_creation(sessions, "New")

    result = utilities.folders.execute_folder_creation(sessions, plan)

    assert result.folder == "INBOX.New"
    assert not result.subscribed
    assert "still does not list it" in result.subscribe_error


# ############################################################################
# Counting folders (#157)
# ############################################################################

LIST_STATUS = {"LIST-STATUS"}
WITH_SIZE = {"LIST-STATUS", "STATUS=SIZE"}


# ----------------------------------------------------------------------------
def list_commands(fake_imap) -> list[tuple]:
    """The raw LIST commands sent, as recorded by the double."""
    return [call for call in fake_imap.calls if call[0] == "LIST"]


# ----------------------------------------------------------------------------
def test_every_folder_is_counted_in_the_listings_order(sessions, fake_imap):
    fake_imap.caps |= WITH_SIZE

    counts = utilities.folders.list_folder_counts(sessions)

    assert counts.listing.folders == ["INBOX", "INBOX.Lists", "INBOX.spam"]
    assert counts.sizes
    assert [
        (s.folder, s.messages, s.unseen, s.size) for s in counts.statuses
    ] == [
        ("INBOX", 3, 1, 2048),
        ("INBOX.Lists", 0, 0, 0),
        ("INBOX.spam", 12, 12, 30822),
    ]


# ----------------------------------------------------------------------------
def test_it_is_one_request_however_many_folders(sessions, fake_imap):
    """Red if the counts came a STATUS -- or a SELECT -- per folder."""
    fake_imap.caps |= WITH_SIZE
    fake_imap.listing += [((), b".", f"INBOX.f{n}".encode()) for n in range(9)]

    utilities.folders.list_folder_counts(sessions)

    assert list_commands(fake_imap) == [
        ("LIST", '""', '"*"', "RETURN", "(STATUS (MESSAGES UNSEEN SIZE))")
    ]
    assert "select_folder" not in fake_imap.names()


# ----------------------------------------------------------------------------
def test_size_is_neither_asked_for_nor_shown_without_status_size(
    sessions, fake_imap
):
    fake_imap.caps |= LIST_STATUS

    counts = utilities.folders.list_folder_counts(sessions)

    assert not counts.sizes
    assert list_commands(fake_imap)[0][-1] == "(STATUS (MESSAGES UNSEEN))"
    assert {status.size for status in counts.statuses} == {None}
    assert counts.statuses[0].messages == 3


# ----------------------------------------------------------------------------
def test_a_folder_the_server_gives_no_counts_for_has_none(sessions, fake_imap):
    """A \\Noselect folder gets no STATUS line (RFC 5819); it is still
    listed, with nothing counted, rather than dropped or shown as 0."""
    fake_imap.caps |= LIST_STATUS
    del fake_imap.counts["INBOX.Lists"]

    counts = utilities.folders.list_folder_counts(sessions)

    assert counts.listing.folders == ["INBOX", "INBOX.Lists", "INBOX.spam"]
    assert counts.statuses[1] == utilities.folders.FolderStatus("INBOX.Lists")


# ----------------------------------------------------------------------------
def test_inbox_is_matched_whatever_case_the_server_gives_it(
    sessions, fake_imap
):
    """The session read LIST as it opened, so it still says INBOX; the
    STATUS line now says Inbox."""
    fake_imap.caps |= LIST_STATUS
    fake_imap.listing[0] = ((), b".", b"Inbox")
    fake_imap.counts["Inbox"] = fake_imap.counts.pop("INBOX")

    counts = utilities.folders.list_folder_counts(sessions)

    assert counts.statuses[0].folder == "INBOX"
    assert counts.statuses[0].messages == 3


# ----------------------------------------------------------------------------
def test_without_list_status_it_is_refused_naming_it(sessions, fake_imap):
    """Refused rather than counted folder by folder: that would be a
    request per folder, and nothing here loops over the server."""
    with pytest.raises(MailctlError, match="does not advertise LIST-STATUS"):
        utilities.folders.list_folder_counts(sessions)

    assert list_commands(fake_imap) == []


# ----------------------------------------------------------------------------
def test_counting_writes_nothing(sessions, fake_imap):
    fake_imap.caps |= WITH_SIZE

    utilities.folders.list_folder_counts(sessions)

    assert set(fake_imap.names()) <= {"login", "LIST"}


# ----------------------------------------------------------------------------
def test_a_failed_list_status_is_an_error_naming_it(sessions, fake_imap):
    fake_imap.caps |= LIST_STATUS
    fake_imap.failures["list_status"] = IMAPClientError("BAD no such option")

    with pytest.raises(MailctlError, match="LIST-STATUS failed"):
        utilities.folders.list_folder_counts(sessions)
