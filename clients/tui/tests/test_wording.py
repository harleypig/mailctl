"""The TUI's own words name no provider's terms (CONVENTIONS *What the
user sees is the same for every provider*).

Every screen of the first scope is visited, and every word on it that the
TUI wrote is collected: labels, statuses, table cells, form options, the
footer's keys, and notices. What the server wrote is left out because it
is shown as it is: the filter set's own text in the diff, and a message's
headers and body. The test data is chosen so that nothing else from the
server holds one of the words, so a hit is the TUI's own.
"""

import re

import pytest
from conftest import SIZE, settle
from mailctl_tui import render
from test_flow import open_plan, shown
from textual.widgets import (
    Checkbox,
    DataTable,
    Input,
    Label,
    RichLog,
    Select,
    Static,
)

# The pattern tests/test_neutral_wording.py holds the CLI to: case and
# word edges matter, so a backup's ``.sieve`` suffix, the filter set named
# ``managesieve``, and an extension such as ``imap4flags`` are content, not
# words the TUI chose. CONVENTIONS also keeps *rule* out of the user's
# view, outside a quoted name.
PROVIDER_WORDS = re.compile(
    r"Sieve|IMAP|MXroute|MXRoute|Roundcube|CHECKSCRIPT|fileinto|Exim"
    r"|DirectAdmin|\bscripts?\b|(?<![.\w])(?:sieve|imap)\b|\b[Rr]ules?\b"
)

# Shown as the server wrote them.
SERVERS_OWN = {"diff", "headers", "body"}


# ----------------------------------------------------------------------------
def words_on(screen) -> list[str]:
    """Every piece of text the TUI put on ``screen``, and its keys."""
    found = []

    for widget in screen.walk_children(with_self=False):
        if widget.id in SERVERS_OWN:
            continue

        if isinstance(widget, Checkbox):
            found.append(str(widget.label))

        elif isinstance(widget, Static | Label):
            found.append(str(widget.render()))

        elif isinstance(widget, Input):
            found += [widget.placeholder, widget.value]

        elif isinstance(widget, Select):
            found += [str(prompt) for prompt, _value in widget._options]

        elif isinstance(widget, DataTable):
            found += [str(column.label) for column in widget.columns.values()]
            found += [
                str(cell)
                for row in range(widget.row_count)
                for cell in widget.get_row_at(row)
            ]

        elif isinstance(widget, RichLog):
            found += [line.text for line in widget.lines]

    bindings = (
        screen.app.BINDINGS if screen is screen.app.screen_stack[0] else []
    )

    return found + [
        binding.description for binding in [*screen.BINDINGS, *bindings]
    ]


# ----------------------------------------------------------------------------
def provider_words(texts: list[str]) -> list[str]:
    return [text for text in texts if PROVIDER_WORDS.search(text)]


# ----------------------------------------------------------------------------
async def collect(scene) -> dict[str, list[str]]:
    """The words on each screen of the first scope, by screen, and the
    notices shown on the way."""
    scene.flow()
    app = scene.app()

    async with app.run_test(size=SIZE) as pilot:
        await open_plan(pilot, app)
        names = ("filters", "search", "message", "add", "plan")
        seen = {
            name: words_on(screen)
            for name, screen in zip(names, app.screen_stack, strict=True)
        }

        await pilot.press("y")
        await settle(pilot, lambda: "Saved." in shown(app, "#plan-status"))
        seen["saved"] = words_on(app.screen)
        seen["notices"] = [
            str(notice.message) for notice in app._notifications
        ]

    return seen


# ----------------------------------------------------------------------------
async def test_no_screen_uses_a_providers_words(scene):
    seen = await collect(scene)

    assert {name: provider_words(texts) for name, texts in seen.items()} == {
        name: [] for name in seen
    }


# ----------------------------------------------------------------------------
async def test_the_walk_reaches_every_kind_of_text(scene):
    """Each screen's walk finds words of every kind it holds, so a clean
    result means the words were read: a status, a table's headings, a
    form's labels and options, the footer, the plan, and what saving did."""
    seen = await collect(scene)
    joined = {name: "\n".join(texts) for name, texts in seen.items()}

    assert "Filter set 'managesieve'" in joined["filters"]
    assert "Search mail" in joined["filters"]
    assert "Subject" in joined["search"]
    assert "Folder (the default one if empty)" in joined["search"]
    assert "Add a filter like this" in joined["message"]
    assert "Mark it read" in joined["add"]
    assert "Move it to the folder" in joined["add"]
    assert "before the filter" in joined["add"]
    assert "Before this filter is reached:" in joined["plan"]
    assert "Save it" in joined["plan"]
    assert "backed up at" in joined["saved"]


# ----------------------------------------------------------------------------
async def test_a_planted_provider_word_is_caught(scene, monkeypatch):
    """Break the wording of what saving did, and the guard goes red."""
    original = render.result_lines

    def planted(document):
        return [*original(document), "The script was uploaded."]

    monkeypatch.setattr(render, "result_lines", planted)
    seen = await collect(scene)

    # One item per widget, so the planted line arrives inside the steps.
    hits = provider_words(seen["saved"])

    assert len(hits) == 1
    assert hits[0].endswith("The script was uploaded.")


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    ["the Sieve script", "IMAP", "a rule", "fileinto", "MXroute's"],
)
def test_the_pattern_catches_each_word(text):
    assert provider_words([text]) == [text]


# ----------------------------------------------------------------------------
def test_the_pattern_leaves_the_users_words_and_content_alone():
    allowed = [
        "a filter in filter set 'managesieve'",
        "webmail",
        "Backed up the current filter set to /backups/x-1.sieve",
    ]

    assert provider_words(allowed) == []
