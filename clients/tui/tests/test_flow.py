"""Searching, reading a message, and adding a filter like it, against the
stub API: plan, decide, execute, and a cancel that discards.

Each test drives the real app with Pilot over the stub as a child
process, and checks both what the screen shows and what the stub was
sent, since "re-derives nothing" is a claim about both: what is shown is
the document's, and what is sent back is what the API handed over.
"""

import copy

from conftest import (
    CAPABILITIES,
    EVENTS,
    LIKE,
    LISTING,
    PLAN,
    SIZE,
    reach,
    settle,
)
from mailctl_tui.screens import (
    AddScreen,
    MessageScreen,
    PlanScreen,
    SearchScreen,
)
from rich.text import Text
from textual.widgets import Checkbox, DataTable, Input, RichLog, Select, Static

ALERT = {
    "version": 2,
    "alert": {"channel": "mail", "kind": "alert", "text": "Quota at 95%"},
}


# ----------------------------------------------------------------------------
def shown(app, selector: str) -> str:
    """A Static's characters, as drawn: markup read as markup would be
    missing from them."""
    return str(app.screen.query_one(selector, Static).render())


# ----------------------------------------------------------------------------
def rows(app) -> list[list[str]]:
    table = app.screen.query_one("#messages", DataTable)
    found = [table.get_row_at(row) for row in range(table.row_count)]

    for row in found:
        for cell in row:
            assert isinstance(cell, Text) and not cell.spans, repr(cell)

    return [[cell.plain for cell in row] for row in found]


# ----------------------------------------------------------------------------
def steps(app) -> list[str]:
    return shown(app, "#steps").splitlines()


# ----------------------------------------------------------------------------
async def ready(pilot, app) -> None:
    await settle(
        pilot, lambda: "filter(s)" in str(app.query_one("#status").render())
    )


# ----------------------------------------------------------------------------
async def search(pilot, app, folder: str = "Lists", sender: str = "") -> None:
    await ready(pilot, app)
    await pilot.press("s")
    await reach(pilot, app, SearchScreen)
    app.screen.query_one("#folder", Input).value = folder
    app.screen.query_one("#search-from", Input).value = sender
    app.screen.query_one("#folder", Input).focus()
    await pilot.press("enter")
    await settle(pilot, lambda: bool(rows(app)))


# ----------------------------------------------------------------------------
async def open_message(pilot, app) -> None:
    await search(pilot, app)
    await pilot.press("enter")
    await reach(pilot, app, MessageScreen)


# ----------------------------------------------------------------------------
async def open_form(pilot, app) -> None:
    await open_message(pilot, app)
    await pilot.press("a")
    await reach(pilot, app, AddScreen)


# ----------------------------------------------------------------------------
async def open_plan(pilot, app) -> None:
    await open_form(pilot, app)
    app.screen.query_one("#move_to", Input).value = "GitHub"
    app.screen.query_one("#mark_read", Checkbox).value = True
    await pilot.press("ctrl+s")
    await reach(pilot, app, PlanScreen)


# ----------------------------------------------------------------------------
def footer_keys(app) -> set[str]:
    return {
        key
        for key, active in app.active_bindings.items()
        if active.enabled and active.binding.show
    }


# ############################################################################
# Searching and reading
# ############################################################################


# ----------------------------------------------------------------------------
async def test_a_search_sends_what_was_typed_and_shows_the_document(scene):
    """The folder named is the one the API searched, spelt as it spells
    it. Break: word the status from the typed folder and this goes red."""
    scene.flow()
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await search(pilot, app, folder="Lists", sender="github")

        assert rows(app) == [
            [
                "41",
                "2026-10-29 08:15",
                "GitHub <noreply@github.com>",
                "[repo] New issue",
                "unread, attachments",
            ],
            [
                "40",
                "2026-10-28 19:01",
                "Boss <boss@example.com>",
                "Lunch",
                "flagged",
            ],
        ]
        assert shown(app, "#search-status") == "2 message(s) in 'INBOX.Lists'."

    assert scene.params("mail.search") == [
        {
            "folder": "Lists",
            "filter": {
                "version": 1,
                "criteria": {
                    "match": "all",
                    "compare": "contains",
                    "terms": [{"header": "From", "value": "github"}],
                },
            },
        }
    ]


# ----------------------------------------------------------------------------
async def test_an_empty_search_leaves_the_folder_to_the_api(scene):
    scene.flow(listing={**LISTING, "messages": [], "folder": "INBOX"})
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot, app)
        await pilot.press("s")
        await reach(pilot, app, SearchScreen)
        await pilot.press("enter")
        await settle(
            pilot, lambda: "No messages" in shown(app, "#search-status")
        )

        assert shown(app, "#search-status") == "No messages found in 'INBOX'."

    assert scene.params("mail.search") == [{}]


# ----------------------------------------------------------------------------
async def test_a_message_is_read_pinned_to_the_listing(scene):
    """The UID is read in the folder and under the UIDVALIDITY the
    listing reported, never the folder as typed. Break: send the typed
    folder, or drop the pin, and this goes red."""
    scene.flow()
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await open_message(pilot, app)

    assert scene.params("mail.view") == [
        {"uid": 41, "folder": "INBOX.Lists", "uidvalidity": 1700}
    ]


# ----------------------------------------------------------------------------
async def test_a_message_body_keeps_its_lines_and_loses_its_controls(scene):
    """Markup in the text is characters; an escape sequence is shown as
    its escape; lines and tabs stay. Break: hand the body over as a str
    (markup) or drop ``block`` and this goes red."""
    scene.flow()
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await open_message(pilot, app)

        assert shown(app, "#body") == (
            "First line\nSecond [bold]line[/]\\x1b[2J\n\tindented"
        )
        assert shown(app, "#headers").splitlines() == [
            "From: GitHub <noreply@github.com>",
            "List-Id: <repo.github.com>",
            "Subject: [repo] New issue",
            "Attachment: 'log.txt', text/plain, 12 bytes",
        ]
        assert shown(app, "#message-status") == (
            "Message 41 in 'INBOX.Lists', unread"
        )


# ----------------------------------------------------------------------------
async def test_an_alert_over_another_screen_is_shown(scene):
    """An alert arriving during a search reaches the alert panel under it,
    and a notice over the search. Break: drop the notice and this goes
    red."""
    scene.flow(alerts=[ALERT], alerts_on="mail.search")
    app = scene.app()
    words = "Alert from the mail server: Quota at 95%"

    async with app.run_test(size=SIZE) as pilot:
        await search(pilot, app)
        log = app.screen_stack[0].query_one("#alerts", RichLog)

        assert [line.text for line in log.lines] == [words]
        assert [str(n.message) for n in app._notifications] == [words]


# ############################################################################
# Adding a filter like a message
# ############################################################################


# ----------------------------------------------------------------------------
async def test_the_form_is_filled_in_from_what_the_api_derived(scene):
    """The like request names the message as listed; the form shows what
    came back, and carries what it has no field for. Break: fill the form
    from the message's own headers and this goes red."""
    scene.flow()
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await open_form(pilot, app)
        form = app.screen

        assert (
            form.query_one("#term-list-id", Input).value == "repo.github.com"
        )
        assert form.query_one("#term-from", Input).value == ""
        assert form.query_one("#match", Select).value == "any"
        assert (
            shown(app, "#carried") == "X-GitHub-Reason contains 'subscribed'"
        )

    assert scene.params("mail.search")[-1] == {
        "folder": "INBOX.Lists",
        "like": {"uid": 41, "uidvalidity": 1700},
        "build_filter": True,
    }


# ----------------------------------------------------------------------------
async def test_keys_while_the_like_request_waits_reach_nothing(scene):
    """A key pressed while ``like`` is on its way sends no second request
    and leaves the message screen where it is, and the form still opens
    once the answer comes (#381). Break: let the message screen's keys
    run during the wait and a second ``a`` asks again, or Escape goes
    back, and this goes red."""
    scene.flow(hold="mail.search", hold_with="like")
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await open_message(pilot, app)
        message = app.screen
        await pilot.press("a")
        await settle(pilot, lambda: scene.seen().get("held") == "mail.search")

        await pilot.press("a", "escape")
        await pilot.pause(0.3)

        assert app.screen is message
        assert footer_keys(app) == set()

        scene.release()
        await reach(pilot, app, AddScreen)
        await pilot.pause(0.3)
        assert isinstance(app.screen, AddScreen)

    assert [p for p in scene.params("mail.search") if "like" in p] == [
        {
            "folder": "INBOX.Lists",
            "like": {"uid": 41, "uidvalidity": 1700},
            "build_filter": True,
        }
    ]


# ----------------------------------------------------------------------------
async def test_an_api_without_like_opens_an_empty_form_and_says_so(scene):
    """Nothing is derived here in the API's place. Break: fill the form
    from the viewed message's headers and this goes red."""
    scene.flow(like=None)
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await open_form(pilot, app)
        form = app.screen

        assert all(
            not field.value
            for field in form.query(Input)
            if (field.id or "").startswith("term-")
        )
        assert "cannot fill a filter in from a message yet" in shown(
            app, ".note"
        )


# ----------------------------------------------------------------------------
async def test_any_other_invalid_parameter_is_shown_as_the_api_said(scene):
    """A -32602 from a current API is a refusal of what was sent, shown in
    its own words, never an empty form passed off as "not yet". Break: take
    every -32602 for an older API and this goes red."""
    scene.flow(like_refusal="'uid' must be at least 1")
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await open_form(pilot, app)

        assert shown(app, ".note") == (
            "mailctl refused: 'uid' must be at least 1"
        )


# ----------------------------------------------------------------------------
async def test_an_unknown_parameter_other_than_like_is_a_refusal(scene):
    """Only an unknown ``like`` or ``build_filter`` means an older API.
    Break: test the message's prefix alone and this goes red."""
    scene.flow(like_refusal="unknown parameter(s) colour")
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await open_form(pilot, app)

        assert shown(app, ".note") == (
            "mailctl refused: unknown parameter(s) colour"
        )


# ----------------------------------------------------------------------------
async def test_plan_decide_execute(scene):
    """The plan is shown as the document has it; saving sends the plan's
    id and nothing else; each step is shown as it arrives, then where the
    backup is; Enter goes back to a re-read filter list."""
    scene.flow()
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await open_plan(pilot, app)

        assert shown(app, "#summary").splitlines() == [
            "Filter 'github' in filter set 'managesieve':",
            "  when:  List-Id contains 'repo.github.com' OR "
            "X-GitHub-Reason contains 'subscribed'",
            "  then:  move it to 'INBOX.GitHub', then mark it read",
        ]
        assert shown(app, "#placement").splitlines() == [
            "Before this filter is reached:",
            "  ! 'github' never runs -- 'all-lists' carries stop",
        ]
        assert shown(app, "#folder-plan") == (
            "'GitHub' is the folder 'INBOX.GitHub'.\n"
            "The folder 'INBOX.GitHub' will be created and shown in webmail."
        )
        assert shown(app, "#diff") == (
            "+# rule:[github]\n+if header :contains ...\\x1b[31m"
        )
        assert footer_keys(app) >= {"y", "escape"}
        assert "enter" not in footer_keys(app)

        await pilot.press("y")
        await settle(pilot, lambda: "Saved." in shown(app, "#plan-status"))

        assert steps(app) == [
            "Backed up the current filter set to /backups/managesieve-1.sieve",
            "Created folder 'INBOX.GitHub'; webmail shows it",
            "Saved filter set 'managesieve'; it was not activated",
            "Filter 'github' is saved in filter set 'managesieve'.",
            "The filter set as it was before is backed up at "
            "/backups/managesieve-1.sieve",
            "Mail already delivered was left alone; the filter acts on new "
            "mail.",
        ]
        assert footer_keys(app) >= {"enter"}
        assert not footer_keys(app) & {"y", "escape"}

        await pilot.press("enter")
        await settle(pilot, lambda: len(app.screen_stack) == 1)
        await settle(
            pilot, lambda: scene.seen()["methods"].count("filter.list") == 2
        )

    assert scene.params("filter.add.plan") == [
        {
            "filter": {
                "version": 1,
                "criteria": {
                    "match": "any",
                    "compare": "contains",
                    "terms": [
                        {"header": "List-Id", "value": "repo.github.com"},
                        {"header": "X-GitHub-Reason", "value": "subscribed"},
                    ],
                },
            },
            "actions": {"move_to": "GitHub", "mark_read": True},
        }
    ]
    assert scene.params("filter.add.execute") == [{"plan_id": "p-1"}]
    assert scene.params("plan.discard") == []


# ----------------------------------------------------------------------------
async def test_cancelling_the_plan_discards_it(scene):
    """Escape on the plan sends ``plan.discard`` with its id and goes back
    to the form. Break: dismiss without discarding and this goes red."""
    scene.flow()
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await open_plan(pilot, app)
        await pilot.press("escape")
        await reach(pilot, app, AddScreen)

    assert scene.params("plan.discard") == [{"plan_id": "p-1"}]
    assert scene.params("filter.add.execute") == []


# ----------------------------------------------------------------------------
async def test_a_plan_that_changes_nothing_offers_no_save(scene):
    plan = copy.deepcopy(PLAN)
    plan["plan"] |= {"changes": False, "diff": None}
    scene.flow(plan=plan)
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await open_plan(pilot, app)

        assert "Nothing would change" in shown(app, "#diff")
        assert "y" not in footer_keys(app)

        await pilot.press("y")
        await pilot.pause()

        assert isinstance(app.screen, PlanScreen)

    assert scene.params("filter.add.execute") == []


# ----------------------------------------------------------------------------
async def test_a_refused_plan_is_worded_as_the_api_sent_it(scene):
    scene.flow(
        refuse="filter.add.plan",
        refusal_message="a filter named 'github' already exists",
    )
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await open_form(pilot, app)
        await pilot.press("ctrl+s")
        await settle(pilot, lambda: "refused" in shown(app, "#add-status"))

        assert shown(app, "#add-status") == (
            "mailctl refused: a filter named 'github' already exists"
        )
        assert isinstance(app.screen, AddScreen)


# ----------------------------------------------------------------------------
async def test_a_failed_save_says_so_and_goes_back(scene):
    scene.flow(
        refuse="filter.add.execute", refusal_message="the filter set changed"
    )
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await open_plan(pilot, app)
        await pilot.press("y")
        await settle(pilot, lambda: "refused" in shown(app, "#plan-status"))

        assert shown(app, "#plan-status").startswith(
            "mailctl refused: the filter set changed"
        )

        await pilot.press("enter")
        await reach(pilot, app, AddScreen)


# ############################################################################
# Only what the capabilities allow
# ############################################################################

OPTIONS = (
    "#move_to",
    "#create_folder",
    "#subscribe",
    "#mark_read",
    "#keep",
    "#discard",
    "#no_stop",
    "#where",
    "#anchor",
)


# ----------------------------------------------------------------------------
async def test_the_form_offers_only_what_the_provider_declares(scene):
    """Under a provider declaring nothing, no gated option is on the form;
    under one declaring all, each is. Break: compose an option without
    asking ``offers`` and the first half goes red."""
    for capabilities, expected in (({}, set()), (CAPABILITIES, set(OPTIONS))):
        scene.flow(capabilities=capabilities)
        app = scene.app()

        async with app.run_test(size=SIZE) as pilot:
            await open_form(pilot, app)
            present = {
                option for option in OPTIONS if app.screen.query(option)
            }

            assert present == expected, capabilities


# ----------------------------------------------------------------------------
async def test_each_option_follows_its_own_capability(scene):
    """One capability withheld at a time removes exactly its options."""
    withheld = {
        "ordering": {"#where", "#anchor"},
        "stop": {"#no_stop"},
        "folder_visibility": {"#subscribe"},
    }

    for name, gone in withheld.items():
        scene.flow(capabilities={**CAPABILITIES, name: False})
        app = scene.app()

        async with app.run_test(size=SIZE) as pilot:
            await open_form(pilot, app)
            present = {
                option for option in OPTIONS if app.screen.query(option)
            }

            assert present == set(OPTIONS) - gone, name

    for action, gone in (
        ("fileinto", {"#move_to", "#create_folder", "#subscribe"}),
        ("flag", {"#mark_read"}),
        ("keep", {"#keep"}),
        ("discard", {"#discard"}),
    ):
        actions = [a for a in CAPABILITIES["actions"] if a != action]
        scene.flow(capabilities={**CAPABILITIES, "actions": actions})
        app = scene.app()

        async with app.run_test(size=SIZE) as pilot:
            await open_form(pilot, app)
            present = {
                option for option in OPTIONS if app.screen.query(option)
            }

            assert present == set(OPTIONS) - gone, action


# ----------------------------------------------------------------------------
async def test_the_filter_lists_keys_are_not_offered_over_it(scene):
    """``s`` on the search screen would push a second search. Break: drop
    the screen-stack test from ``check_action`` and this goes red."""
    scene.flow()
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot, app)

        assert {"r", "s", "q"} <= footer_keys(app)

        await pilot.press("s")
        await reach(pilot, app, SearchScreen)

        assert not footer_keys(app) & {"r", "s", "q"}
        assert app.check_action("search", ()) is False


# ----------------------------------------------------------------------------
async def test_webmail_visibility_is_sent_only_beside_creating(scene):
    """The API refuses ``subscribe`` without ``create_folder``, so it is not
    sent alone. Break: send it whatever ``create_folder`` says and this
    goes red."""
    scene.flow()
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await open_form(pilot, app)
        app.screen.query_one("#subscribe", Checkbox).value = False
        await pilot.press("ctrl+s")
        await reach(pilot, app, PlanScreen)

    sent = scene.params("filter.add.plan")[0]

    assert "subscribe" not in sent
    assert "create_folder" not in sent


# ----------------------------------------------------------------------------
async def test_options_given_are_sent_and_defaults_left_to_the_api(scene):
    """Placement, stop, and folder creation reach the API by the names it
    takes; what the user left alone is not sent at all."""
    scene.flow(events=EVENTS)
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await open_form(pilot, app)
        form = app.screen
        form.query_one("#name", Input).value = "github"
        form.query_one("#move_to", Input).value = "GitHub"
        form.query_one("#create_folder", Checkbox).value = True
        form.query_one("#subscribe", Checkbox).value = False
        form.query_one("#no_stop", Checkbox).value = True
        form.query_one("#where", Select).value = "before"
        form.query_one("#anchor", Select).value = "announce"
        await pilot.press("ctrl+s")
        await reach(pilot, app, PlanScreen)

    sent = scene.params("filter.add.plan")[0]

    assert {key: value for key, value in sent.items() if key != "filter"} == {
        "name": "github",
        "actions": {"move_to": "GitHub", "stop": False},
        "create_folder": True,
        "subscribe": False,
        "placement": {"where": "before", "filter": "announce"},
    }


# ----------------------------------------------------------------------------
async def test_the_form_starts_where_there_is_something_to_give(scene):
    """An empty form starts at its first criterion, a filled-in one at
    what to do with the mail; typing lands there. Break: drop
    ``AddScreen.on_mount`` and nothing has focus, so this goes red."""
    for like, field in ((None, "#term-from"), (LIKE, "#move_to")):
        scene.flow(like=like)
        app = scene.app()

        async with app.run_test(size=SIZE) as pilot:
            await open_form(pilot, app)
            await pilot.press("x")

            assert app.screen.query_one(field, Input).value == "x"
