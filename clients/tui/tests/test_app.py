"""The first screen, driven with Textual's Pilot against the stub API.

Each test starts the stub as a real child process, so the TUI's framing,
its reader, and its answers to the API's requests are the ones a user
runs.
"""

import gc

from conftest import FILTERS, SIZE, digest, reach, settle
from mailctl_tui.app import MailctlApp, PasswordScreen
from rich.text import Text
from textual.widgets import DataTable, Input, RichLog, Static

ALERT = {
    "version": 2,
    "alert": {
        "channel": "mail",
        "kind": "alert",
        "text": "Maintenance tonight at 22:00 UTC",
    },
}


# ----------------------------------------------------------------------------
def cells(app) -> list[list[str]]:
    """Each row's cells as the characters shown. A cell must be plain
    ``Text``: a str would be read as markup, and a span is a style the
    server's text put there."""
    table = app.query_one("#filters", DataTable)
    rows = [table.get_row_at(row) for row in range(table.row_count)]

    for row in rows:
        for cell in row:
            assert isinstance(cell, Text) and not cell.spans, repr(cell)

    return [[cell.plain for cell in row] for row in rows]


# ----------------------------------------------------------------------------
def shown(app, selector: str) -> str:
    return str(app.query_one(selector, Static).render())


# ----------------------------------------------------------------------------
async def test_lists_the_filters_as_the_document_has_them(scene):
    scene.write(capabilities={}, filters=FILTERS)
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot, lambda: len(cells(app)) == 2)

        assert cells(app) == [
            [
                "1",
                "all-lists",
                "To contains '@lists.example.com'",
                "files the message into a folder; stops",
                "stop",
            ],
            [
                "2",
                "announce",
                "To contains 'announce@lists.example.com' AND "
                "Subject is 'news' | 'update'",
                "files the message into a folder",
                "disabled",
            ],
        ]
        assert "Filter set 'managesieve': 2 filter(s)" in shown(app, "#status")
        assert "! 'announce' never runs -- 'all-lists' carries stop" in (
            shown(app, "#findings")
        )

    assert scene.seen()["methods"][:2] == ["initialize", "filter.list"]


# ----------------------------------------------------------------------------
async def test_what_a_filter_does_is_its_steps_never_its_actions(scene):
    """``actions`` are the host's own words and never shown; ``steps`` are
    worded here, an unknown action's own name escaped. A document from an
    API without ``steps`` shows the column empty. Break: word the column
    from ``actions`` and this goes red."""
    item = {
        **FILTERS["filters"][0],
        "actions": ["fileinto", "addflag", "vnd.x\x1b"],
        "steps": [
            {"kind": "file", "argument": "INBOX.Lists", "creates": True},
            {"kind": "flag", "argument": "\\Seen", "creates": False},
            {"kind": "other", "argument": "vnd.x\x1b", "creates": False},
        ],
    }
    older = {key: value for key, value in item.items() if key != "steps"}
    scene.write(
        capabilities={},
        filters={**FILTERS, "filters": [item, older], "findings": []},
    )
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot, lambda: len(cells(app)) == 2)

        assert [row[3] for row in cells(app)] == [
            "files the message into 'INBOX.Lists', creating it if it is "
            "missing; adds the flag \\Seen; does 'vnd.x\\x1b'",
            "",
        ]


# ----------------------------------------------------------------------------
async def test_an_empty_filter_set_says_so(scene):
    scene.write(
        capabilities={},
        filters={**FILTERS, "filters": [], "findings": []},
    )
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot, lambda: "no filters" in shown(app, "#status"))

        assert cells(app) == []
        assert shown(app, "#findings") == ""


# ----------------------------------------------------------------------------
async def test_server_text_is_neither_markup_nor_control(scene):
    """A filter named like markup, or holding an escape sequence, is shown
    as the characters it is: neither styles nor reaches the terminal.

    Break: hand the name to the table as a str (markup) or drop
    ``one_line`` (the escape survives) and this goes red."""
    hostile = {**FILTERS["filters"][0], "name": "[bold red]x[/]\x1b[2Jy"}
    scene.write(
        capabilities={},
        filters={**FILTERS, "filters": [hostile], "findings": []},
    )
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot, lambda: len(cells(app)) == 1)

        assert cells(app)[0][1] == "[bold red]x[/]\\x1b[2Jy"


# ----------------------------------------------------------------------------
async def test_a_server_alert_is_shown_as_it_arrives(scene):
    scene.write(capabilities={}, filters=FILTERS, alerts=[ALERT])
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        log = app.query_one("#alerts", RichLog)
        await settle(pilot, lambda: bool(log.lines))

        assert log.has_class("shown")
        assert [line.text for line in log.lines] == [
            "Alert from the mail server: Maintenance tonight at 22:00 UTC"
        ]


# ----------------------------------------------------------------------------
async def test_no_alert_keeps_the_alert_panel_hidden(scene):
    scene.write(capabilities={}, filters=FILTERS)
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot, lambda: len(cells(app)) == 2)

        assert not app.query_one("#alerts", RichLog).has_class("shown")


# ----------------------------------------------------------------------------
async def test_the_password_is_asked_masked_and_kept_nowhere(scene):
    """The prompt is a masked field; the answer reaches the API, and
    afterwards no object the app holds has it.

    The password is typed one key at a time and never held whole by this
    test, so a string found whole afterwards is one the app kept. Break:
    keep it on the app (``self.last = password``) and this goes red."""
    keys = list("tui-Pa55-9f2c")
    scene.write(
        capabilities={},
        filters=FILTERS,
        password_sha256=digest("".join(keys)),
    )
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await reach(pilot, app, PasswordScreen)

        field = app.screen.query_one(Input)

        assert field.password is True

        await pilot.press(*keys)
        await pilot.press("enter")
        await settle(pilot, lambda: len(cells(app)) == 2)

        gc.collect()
        whole = "".join(keys)
        holders = [
            type(obj).__name__
            for obj in gc.get_objects()
            if isinstance(obj, dict | list | tuple | set)
            and obj is not keys
            and _holds(obj, whole)
        ]
        del whole

        assert holders == []

    assert scene.seen()["password"] == "matched"


# ----------------------------------------------------------------------------
def _holds(container, value: str) -> bool:
    items = container.values() if isinstance(container, dict) else container

    try:
        return any(isinstance(item, str) and item == value for item in items)

    except RuntimeError:
        # A container another thread resized mid-scan; not ours.
        return False


# ----------------------------------------------------------------------------
async def test_a_declined_prompt_is_answered_as_declined(scene):
    scene.write(capabilities={}, filters=FILTERS, password_sha256=digest("x"))
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await reach(pilot, app, PasswordScreen)
        await pilot.press("escape")
        await settle(pilot, lambda: "refused" in shown(app, "#status"))

        assert cells(app) == []
        assert shown(app, "#status") == (
            "mailctl refused: the client declined to give a password"
        )

    assert scene.seen()["password"] == "declined"


# ----------------------------------------------------------------------------
async def test_an_api_that_will_not_start_is_reported(tmp_path):
    app = MailctlApp([str(tmp_path / "no-such-api")])

    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot, lambda: "could not start" in shown(app, "#status"))
