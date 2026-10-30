"""The screens past the filter list: searching a folder, reading a
message, and adding a filter like it, through plan, decide, execute.

Each screen's keys come from :data:`.offer.COMMANDS` and pass the same
capability test as the filter list's (:class:`Gated`). Every request goes
to the API through the app's one client, and every answer is laid out by
:mod:`.render`.
"""

import contextlib
from typing import TYPE_CHECKING, ClassVar, cast

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
)

from . import render
from .client import ApiError, ApiGone
from .offer import (
    ADD,
    COMMANDS,
    MESSAGE,
    PLAN,
    SEARCH,
    bindings,
    command,
    offered,
)
from .text import block, one_line

if TYPE_CHECKING:
    from .app import MailctlApp

__all__ = [
    "FILTER_VERSION",
    "AddScreen",
    "Gated",
    "MessageScreen",
    "PlanScreen",
    "SearchScreen",
    "like_filter",
]

# The filter document's format, as the API reads ``filter`` (mailctl's
# filter document, version 1). The TUI writes one for a search or a filter
# the user fills in; one the API hands back keeps its own version.
FILTER_VERSION = 1

# The headers the search and the add form take as fields, in the CLI's
# spelling of them.
SEARCH_HEADERS = ("From", "Subject")
FORM_HEADERS = ("From", "To", "Subject", "List-Id")

# The API's code for parameters it refuses (JSON-RPC's own), and how its
# refusal of a parameter it does not know begins (mailctl/api/server.py,
# ``Params.done``). Only that refusal, naming a ``like`` parameter, means
# an API from before ``like``; any other is a refusal to show.
INVALID_PARAMS = -32602
UNKNOWN_PARAMETERS = "unknown parameter(s) "
LIKE_PARAMETERS = ("like", "build_filter")


# ############################################################################
# Gating
# ############################################################################


class Gated(Screen):
    """A screen whose keys are its commands in ``offer.COMMANDS``, each
    hidden unless the provider's capabilities allow it and the screen's
    own state has a use for it."""

    SCREEN: ClassVar[str]

    # ------------------------------------------------------------------------
    @property
    def mailctl(self) -> "MailctlApp":
        return cast("MailctlApp", self.app)

    # ------------------------------------------------------------------------
    def check_action(
        self, action: str, parameters: tuple[object, ...]
    ) -> bool | None:
        for item in COMMANDS:
            if item.screen == self.SCREEN and item.action == action:
                return offered(item, self.mailctl.capabilities) and (
                    self.wanted(action)
                )

        return True

    # ------------------------------------------------------------------------
    def wanted(self, action: str) -> bool:
        """Whether the screen, as it stands, has a use for ``action``."""
        return True

    # ------------------------------------------------------------------------
    def offers(self, action: str) -> bool:
        """Whether this screen's command or form option ``action`` is
        offered under the provider's capabilities."""
        return any(
            offered(item, self.mailctl.capabilities)
            for item in COMMANDS
            if item.screen == self.SCREEN and item.action == action
        )

    # ------------------------------------------------------------------------
    def action_back(self) -> None:
        self.dismiss()


# ----------------------------------------------------------------------------
def criteria_document(terms: list[dict], match: str, compare: str) -> dict:
    return {
        "version": FILTER_VERSION,
        "criteria": {"match": match, "compare": compare, "terms": terms},
    }


# ############################################################################
# Searching a folder
# ############################################################################


class SearchScreen(Gated):
    """A folder's newest messages, or those whose From or Subject holds
    what is typed; Enter on one reads it."""

    SCREEN = SEARCH
    BINDINGS: ClassVar[list[BindingType]] = [*bindings(SEARCH)]

    DEFAULT_CSS = """
    SearchScreen Horizontal {
        height: auto;
    }
    SearchScreen Input {
        width: 1fr;
    }
    SearchScreen #search-status {
        padding: 0 1;
        color: $text-muted;
    }
    SearchScreen #messages {
        height: 1fr;
    }
    """

    # ------------------------------------------------------------------------
    def __init__(self) -> None:
        super().__init__()
        self.listing: dict = {}

    # ------------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header()

        with Horizontal():
            yield Input(
                placeholder="Folder (the default one if empty)", id="folder"
            )

            for header in SEARCH_HEADERS:
                yield Input(
                    placeholder=f"{header} contains",
                    id=f"search-{header.lower()}",
                )

        yield Static(
            "Enter searches. Enter on a message reads it.",
            id="search-status",
            markup=False,
        )
        yield DataTable(id="messages", cursor_type="row", zebra_stripes=True)
        yield Footer()

    # ------------------------------------------------------------------------
    def on_mount(self) -> None:
        self.query_one("#messages", DataTable).add_columns(
            "UID", "Received", "From", "Subject", "State"
        )
        self.query_one("#folder", Input).focus()

    # ------------------------------------------------------------------------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.search()

    # ------------------------------------------------------------------------
    def params(self) -> dict:
        folder = self.query_one("#folder", Input).value.strip()
        terms = [
            {"header": header, "value": value}
            for header in SEARCH_HEADERS
            if (
                value := self.query_one(
                    f"#search-{header.lower()}", Input
                ).value
            )
        ]
        params: dict = {}

        if folder:
            params["folder"] = folder

        if terms:
            params["filter"] = criteria_document(terms, "all", "contains")

        return params

    # ------------------------------------------------------------------------
    @work(exclusive=True, group="search")
    async def search(self) -> None:
        self.show_status("Searching ...")

        try:
            listing = await self.mailctl.client.request(
                "mail.search", self.params()
            )

        except (ApiError, ApiGone) as exc:
            self.show_status(render.failure(exc))

            return

        self.show_listing(listing)

    # ------------------------------------------------------------------------
    def show_status(self, words: str) -> None:
        self.query_one("#search-status", Static).update(Text(words))

    # ------------------------------------------------------------------------
    def show_listing(self, listing: dict) -> None:
        """Lay out ``mail.search``'s document; the folder named is the one
        the API searched, which may be spelt otherwise than typed."""
        self.listing = listing
        messages = listing.get("messages", [])
        folder = render.quoted(listing.get("folder", ""))
        table = self.query_one("#messages", DataTable)
        table.clear()

        for item in messages:
            table.add_row(
                Text(str(item.get("uid", ""))),
                Text(render.received(item.get("received"))),
                Text(one_line(str(item.get("from") or ""))),
                Text(one_line(str(item.get("subject") or ""))),
                Text(render.message_marks(item)),
                key=str(item.get("uid")),
            )

        if not messages:
            self.show_status(f"No messages found in {folder}.")

        elif listing.get("more"):
            self.show_status(
                f"The {len(messages)} newest message(s) in {folder}; there "
                "may be more."
            )

        else:
            self.show_status(f"{len(messages)} message(s) in {folder}.")

        if messages:
            table.focus()

    # ------------------------------------------------------------------------
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()

        if event.row_key.value is not None:
            self.view(int(event.row_key.value))

    # ------------------------------------------------------------------------
    @work(exclusive=True, group="view")
    async def view(self, uid: int) -> None:
        """Read one message from the folder the listing came from, pinned
        to the UIDVALIDITY the listing reported, so the API refuses it
        should the folder have been renumbered since."""
        params: dict = {"uid": uid, "folder": self.listing.get("folder")}

        if self.listing.get("uidvalidity") is not None:
            params["uidvalidity"] = self.listing["uidvalidity"]

        try:
            document = await self.mailctl.client.request("mail.view", params)

        except (ApiError, ApiGone) as exc:
            self.show_status(render.failure(exc))

            return

        self.app.push_screen(MessageScreen(document))


# ############################################################################
# Reading a message
# ############################################################################


class MessageScreen(Gated):
    """One message's headers, attachments, and text, as ``mail.view``'s
    document has them; ``a`` adds a filter for mail like it."""

    SCREEN = MESSAGE
    BINDINGS: ClassVar[list[BindingType]] = [*bindings(MESSAGE)]

    DEFAULT_CSS = """
    MessageScreen #message-status {
        padding: 0 1;
        color: $text-muted;
    }
    MessageScreen #headers {
        padding: 0 1;
        border-bottom: solid $primary;
    }
    MessageScreen #body {
        padding: 1 1;
    }
    """

    # ------------------------------------------------------------------------
    def __init__(self, document: dict):
        super().__init__()
        self.message: dict = document.get("message", {})

    # ------------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self.heading(), id="message-status", markup=False)

        with VerticalScroll():
            yield Static(Text(self.headers()), id="headers", markup=False)
            yield Static(Text(self.body()), id="body", markup=False)

        yield Footer()

    # ------------------------------------------------------------------------
    def heading(self) -> Text:
        message = self.message

        return Text(
            f"Message {one_line(str(message.get('uid', '')))} in "
            f"{render.quoted(message.get('folder', ''))}"
            + (
                f", {state}"
                if (state := render.message_marks(message))
                else ""
            )
        )

    # ------------------------------------------------------------------------
    def headers(self) -> str:
        lines = [
            f"{one_line(str(item.get('name', '')))}: "
            f"{one_line(str(item.get('value', '')))}"
            for item in self.message.get("headers", [])
        ]

        for item in self.message.get("attachments", []):
            name = render.quoted(item.get("name") or "(no name)")
            lines.append(
                f"Attachment: {name}, "
                f"{one_line(str(item.get('content_type', '')))}, "
                f"{one_line(str(item.get('size', '')))} bytes"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------------
    def body(self) -> str:
        body = self.message.get("body")

        if not isinstance(body, str) or not body.strip():
            return "(no text)"

        note = (
            "(The text is taken from the message's HTML.)\n\n"
            if self.message.get("body_from_html")
            else ""
        )

        return note + block(body)

    # ------------------------------------------------------------------------
    def action_add_like(self) -> None:
        self.add_like()

    # ------------------------------------------------------------------------
    @work(exclusive=True, group="like")
    async def add_like(self) -> None:
        status = self.query_one("#message-status", Static)
        status.update(Text("Reading what makes mail like this one ..."))
        document, note = await like_filter(self.mailctl, self.message)
        status.update(self.heading())
        self.app.push_screen(AddScreen(document=document, note=note))


# ----------------------------------------------------------------------------
async def like_filter(
    app: "MailctlApp", message: dict
) -> tuple[dict | None, str]:
    """The filter document for mail like ``message``, as the API derives
    it, and a line saying where the form's criteria came from.

    The seam for ``--like``: which headers identify a message, and what
    of each one a filter matches, is the core's decision, never this
    client's, and the API offers it as ``mail.search`` with ``like`` and
    ``build_filter``. An older API refuses those parameters; then the form
    opens empty and says so, and nothing is worked out here in its place.
    """
    params: dict = {
        "folder": message.get("folder"),
        "like": {"uid": message.get("uid")},
        "build_filter": True,
    }

    if message.get("uidvalidity") is not None:
        params["like"]["uidvalidity"] = message["uidvalidity"]

    try:
        document = await app.client.request("mail.search", params)

    except ApiError as exc:
        if predates_like(exc):
            return None, (
                "This mailctl cannot fill a filter in from a message yet, "
                "so the criteria are empty."
            )

        return None, render.failure(exc)

    except ApiGone as exc:
        return None, render.failure(exc)

    if not isinstance(document, dict) or not isinstance(
        document.get("criteria"), dict
    ):
        return None, "mailctl sent no filter for this message."

    return document, "Filled in from the message, as mailctl reads it."


# ----------------------------------------------------------------------------
def predates_like(exc: ApiError) -> bool:
    """Whether ``exc`` is an API from before ``like`` refusing it as a
    parameter it does not know, rather than refusing what was sent."""
    if exc.code != INVALID_PARAMS or not exc.message.startswith(
        UNKNOWN_PARAMETERS
    ):
        return False

    named = exc.message.removeprefix(UNKNOWN_PARAMETERS).split(", ")

    return any(name in LIKE_PARAMETERS for name in named)


# ############################################################################
# Adding a filter
# ############################################################################


class AddScreen(Gated):
    """The filter to add: its criteria, filled in from a message, and what
    it does, offering only the options the provider declares.

    Criteria the form has no field for are carried as the API sent them
    and shown, never dropped or re-worded.
    """

    SCREEN = ADD
    BINDINGS: ClassVar[list[BindingType]] = [*bindings(ADD)]

    DEFAULT_CSS = """
    AddScreen VerticalScroll {
        padding: 0 1;
    }
    AddScreen .row {
        height: auto;
        margin-bottom: 1;
    }
    AddScreen .row > Label {
        width: 28;
        padding: 0 1 0 0;
    }
    AddScreen .row > Input, AddScreen .row > Select {
        width: 1fr;
    }
    AddScreen .note {
        color: $text-muted;
        margin-bottom: 1;
    }
    AddScreen .part {
        text-style: bold;
        margin-bottom: 1;
    }
    AddScreen Checkbox {
        margin-left: 28;
    }
    AddScreen #add-status {
        color: $warning;
        padding: 0 1;
    }
    """

    # ------------------------------------------------------------------------
    def __init__(self, document: dict | None, note: str):
        super().__init__()
        self.note = note
        self.version = (document or {}).get("version", FILTER_VERSION)
        given = dict((document or {}).get("criteria", {}))
        self.match = given.pop("match", "any")
        self.compare = given.pop("compare", "contains")
        self.fields, self.carried = split_terms(given.pop("terms", []))
        # Anything else the API put in the criteria goes back as it came.
        self.carried_rest = given

    # ------------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header()

        with VerticalScroll():
            yield Static(Text(self.note), classes="note", markup=False)
            yield from row(
                "Name",
                Input(
                    placeholder="made from the criteria",
                    compact=True,
                    id="name",
                ),
            )
            yield Static("When", classes="part", markup=False)
            yield from row(
                "Match",
                Select(
                    [("any of these", "any"), ("all of these", "all")],
                    value=self.match
                    if self.match in ("any", "all")
                    else "any",
                    allow_blank=False,
                    compact=True,
                    id="match",
                ),
            )
            yield from row(
                "Each value",
                Select(
                    [
                        ("is contained in the header", "contains"),
                        ("is the whole header", "is"),
                        ("is a pattern the header matches", "matches"),
                    ],
                    value=self.compare
                    if self.compare in ("contains", "is", "matches")
                    else "contains",
                    allow_blank=False,
                    compact=True,
                    id="compare",
                ),
            )

            for header in FORM_HEADERS:
                yield from row(
                    header,
                    Input(
                        value=self.fields.get(header, ""),
                        compact=True,
                        id=f"term-{header.lower()}",
                    ),
                )

            if self.carried or self.carried_rest:
                yield from row(
                    "Also, as mailctl sent it",
                    Static(
                        Text(self.carried_words()), id="carried", markup=False
                    ),
                )

            yield Static("Then", classes="part", markup=False)
            yield from self.compose_actions()

        yield Static("", id="add-status", markup=False)
        yield Footer()

    # ------------------------------------------------------------------------
    def on_mount(self) -> None:
        """Start where the user has something to give: the first criterion
        of an empty form, or what to do with mail like the message."""
        target = "#term-from" if not self.fields else "#move_to"
        found = self.query(target) or self.query("#name")
        found.first().focus()

    # ------------------------------------------------------------------------
    def compose_actions(self) -> ComposeResult:
        """The options the provider declares, and no others."""
        if self.offers("move_to"):
            yield from row(
                "Move it to the folder", Input(compact=True, id="move_to")
            )

        for action in ("create_folder", "subscribe"):
            if self.offers(action):
                yield Checkbox(
                    command(ADD, action).description,
                    value=action == "subscribe",
                    compact=True,
                    id=action,
                )

        for action in ("mark_read", "keep", "discard", "no_stop"):
            if self.offers(action):
                yield Checkbox(
                    command(ADD, action).description, compact=True, id=action
                )

        if self.offers("placement"):
            yield from row(
                "Where it goes",
                Select(
                    [
                        ("first", "first"),
                        ("last", "last"),
                        ("before the filter", "before"),
                        ("after the filter", "after"),
                    ],
                    prompt="where mailctl puts it",
                    compact=True,
                    id="where",
                ),
            )
            yield from row(
                "The filter",
                Select(
                    [
                        (Text(one_line(name)), name)
                        for name in self.mailctl.filter_names
                    ],
                    prompt="for before or after",
                    compact=True,
                    id="anchor",
                ),
            )

    # ------------------------------------------------------------------------
    def carried_words(self) -> str:
        return render.criteria(
            {
                **self.carried_rest,
                "match": self.match,
                "compare": self.compare,
                "terms": self.carried,
            }
        )

    # ------------------------------------------------------------------------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_plan()

    # ------------------------------------------------------------------------
    def action_plan(self) -> None:
        self.plan()

    # ------------------------------------------------------------------------
    def value(self, selector: str) -> object:
        """A form control's value, or None where it is not on the form."""
        found = self.query(selector)

        if not found:
            return None

        widget = found.first()

        if isinstance(widget, Select):
            return None if widget.value is Select.NULL else widget.value

        return getattr(widget, "value", None)

    # ------------------------------------------------------------------------
    def params(self) -> dict:
        """``filter.add.plan``'s parameters, from the form as it stands.

        Only what the user gave is sent; the API supplies every default
        and refuses what it will not take.
        """
        terms = [
            {"header": header, "value": value}
            for header in FORM_HEADERS
            if (value := str(self.value(f"#term-{header.lower()}") or ""))
        ] + self.carried
        criteria = {
            **self.carried_rest,
            "match": self.value("#match"),
            "compare": self.value("#compare"),
            "terms": terms,
        }
        actions: dict = {}

        if move_to := str(self.value("#move_to") or "").strip():
            actions["move_to"] = move_to

        if self.value("#mark_read"):
            actions["mark_read"] = True

        if self.value("#keep"):
            actions["keep"] = True

        if self.value("#discard"):
            actions["discard"] = True

        if self.value("#no_stop"):
            actions["stop"] = False

        params: dict = {
            "filter": {"version": self.version, "criteria": criteria},
            "actions": actions,
        }

        if name := str(self.value("#name") or "").strip():
            params["name"] = name

        if self.value("#create_folder"):
            params["create_folder"] = True

            # Showing the folder in webmail only shapes a folder this
            # change creates, so it is sent only beside create_folder.
            if self.value("#subscribe") is False:
                params["subscribe"] = False

        if where := self.value("#where"):
            params["placement"] = {"where": where}

            if where in ("before", "after") and self.value("#anchor"):
                params["placement"]["filter"] = self.value("#anchor")

        return params

    # ------------------------------------------------------------------------
    @work(exclusive=True, group="plan")
    async def plan(self) -> None:
        status = self.query_one("#add-status", Static)
        status.update(Text("Working out what would change ..."))

        try:
            reply = await self.mailctl.client.request(
                "filter.add.plan", self.params()
            )

        except (ApiError, ApiGone) as exc:
            status.update(Text(render.failure(exc)))

            return

        status.update(Text(""))
        self.app.push_screen(
            PlanScreen(
                str(reply.get("plan_id", "")), reply.get("document", {})
            )
        )


# ----------------------------------------------------------------------------
def row(label: str, widget) -> ComposeResult:
    """A form row: the label, then the field it names."""
    with Horizontal(classes="row"):
        yield Label(label)
        yield widget


# ----------------------------------------------------------------------------
def split_terms(terms: list) -> tuple[dict[str, str], list[dict]]:
    """A filter document's terms, split into the form's fields and the
    rest: the first term for each field's header goes in the field, and
    every other term, or one whose value would not show as it is, is
    carried unchanged."""
    fields: dict[str, str] = {}
    carried: list[dict] = []
    known = {header.lower(): header for header in FORM_HEADERS}

    for term in terms:
        header = known.get(str(term.get("header", "")).lower())
        value = term.get("value")

        if (
            header is None
            or header in fields
            or not isinstance(value, str)
            or one_line(value) != value
        ):
            carried.append(term)

        else:
            fields[header] = value

    return fields, carried


# ----------------------------------------------------------------------------
def section(ident: str, words: str) -> Static:
    """A part of the plan, kept off the screen where it has nothing to
    say, so the parts that do sit together."""
    return Static(
        Text(words), id=ident, markup=False, classes="" if words else "empty"
    )


# ############################################################################
# The plan, the decision, and the change
# ############################################################################


class PlanScreen(Gated):
    """What ``filter.add.plan`` says would change, and the decision.

    ``y`` carries the plan out with ``filter.add.execute`` and shows each
    step as it lands; cancelling discards it with ``plan.discard``, so the
    API holds no plan nobody will use.
    """

    SCREEN = PLAN
    BINDINGS: ClassVar[list[BindingType]] = [*bindings(PLAN)]

    DEFAULT_CSS = """
    PlanScreen VerticalScroll {
        padding: 0 1;
    }
    PlanScreen #placement, PlanScreen #missing {
        color: $warning;
        margin-top: 1;
    }
    PlanScreen #diff {
        margin-top: 1;
        border: solid $primary;
        padding: 0 1;
    }
    PlanScreen #activation, PlanScreen #folder-plan {
        margin-top: 1;
    }
    PlanScreen #steps {
        height: auto;
        margin-top: 1;
        border-top: solid $success;
        display: none;
    }
    PlanScreen #steps.shown {
        display: block;
    }
    PlanScreen .empty {
        display: none;
    }
    PlanScreen #plan-status {
        padding: 0 1;
    }
    """

    # States, in the order a plan goes through them.
    WAITING, SAVING, SAVED, FAILED = "waiting", "saving", "saved", "failed"

    # ------------------------------------------------------------------------
    def __init__(self, plan_id: str, document: dict):
        super().__init__()
        self.plan_id = plan_id
        self.plan: dict = document.get("plan", {})
        self.state = self.WAITING
        self.steps: list[str] = []

    # ------------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        plan = self.plan

        yield Header()

        with VerticalScroll():
            yield Static(Text(self.summary()), id="summary", markup=False)
            yield section("placement", render.placement(plan.get("placement")))
            yield section(
                "folder-plan",
                "\n".join(render.folder_lines(plan.get("folder"))),
            )
            yield section(
                "missing",
                render.missing_extensions(plan.get("missing_extensions", [])),
            )
            yield Static(Text(self.diff()), id="diff", markup=False)
            yield section("activation", render.activation(plan))
            yield Static("", id="steps", markup=False)

        yield Static(Text(self.prompt()), id="plan-status", markup=False)
        yield Footer()

    # ------------------------------------------------------------------------
    def summary(self) -> str:
        plan = self.plan
        name = render.quoted(plan.get("filter", ""))

        return "\n".join(
            [
                f"Filter {name} in filter set "
                f"{render.quoted(plan.get('filterset', ''))}:",
                f"  when:  {render.criteria(plan.get('criteria', {}))}",
                f"  then:  {render.actions(plan.get('actions', {}))}",
            ]
        )

    # ------------------------------------------------------------------------
    def diff(self) -> str:
        """The change to the filter set, in its own language and shown as
        it is, with its control characters escaped."""
        diff = self.plan.get("diff")

        if not self.plan.get("changes") or not isinstance(diff, dict):
            return (
                "Nothing would change: the filter set already has this filter."
            )

        note = (
            "The filter set on the server is laid out otherwise than "
            "mailctl lays it out, so saving re-indents all of it. Only the "
            "filter's own change is shown.\n\n"
            if diff.get("reformats")
            else ""
        )
        text = str(diff.get("text", ""))

        return note + (block(text) if text.strip() else "(no change)")

    # ------------------------------------------------------------------------
    def prompt(self) -> str:
        if self.plan.get("changes"):
            return "Save it? y saves, Escape cancels."

        return "Escape goes back."

    # ------------------------------------------------------------------------
    def wanted(self, action: str) -> bool:
        if action == "save":
            return self.state == self.WAITING and bool(
                self.plan.get("changes")
            )

        if action == "cancel":
            return self.state == self.WAITING

        if action == "done":
            return self.state in (self.SAVED, self.FAILED)

        return True

    # ------------------------------------------------------------------------
    def set_state(self, state: str, words: str) -> None:
        self.state = state
        self.query_one("#plan-status", Static).update(Text(words))
        self.refresh_bindings()

    # ------------------------------------------------------------------------
    def action_save(self) -> None:
        self.set_state(self.SAVING, "Saving ...")
        self.save()

    # ------------------------------------------------------------------------
    @work(exclusive=True, group="save")
    async def save(self) -> None:
        self.mailctl.event_sink = self.show_event

        try:
            result = await self.mailctl.client.request(
                "filter.add.execute", {"plan_id": self.plan_id}
            )

        except (ApiError, ApiGone) as exc:
            self.set_state(
                self.FAILED, f"{render.failure(exc)}  Enter goes back."
            )

            return

        finally:
            self.mailctl.event_sink = None

        for line in render.result_lines(result):
            self.add_step(line)

        self.set_state(self.SAVED, "Saved. Enter goes back to the filters.")

    # ------------------------------------------------------------------------
    def show_event(self, document: dict) -> None:
        self.add_step(render.event_line(document))

    # ------------------------------------------------------------------------
    def add_step(self, line: str) -> None:
        """One more line under the plan: a step as it lands, or what the
        change did once it has."""
        self.steps.append(line)
        widget = self.query_one("#steps", Static)
        widget.add_class("shown")
        widget.update(Text("\n".join(self.steps)))

    # ------------------------------------------------------------------------
    def action_cancel(self) -> None:
        self.discard()

    # ------------------------------------------------------------------------
    @work(exclusive=True, group="save")
    async def discard(self) -> None:
        self.state = self.FAILED

        # A plan the API no longer holds needs no discarding.
        with contextlib.suppress(ApiError, ApiGone):
            await self.mailctl.client.request(
                "plan.discard", {"plan_id": self.plan_id}
            )

        self.dismiss()

    # ------------------------------------------------------------------------
    def action_back(self) -> None:
        self.action_cancel()

    # ------------------------------------------------------------------------
    def action_done(self) -> None:
        if self.state == self.SAVED:
            self.mailctl.back_to_filters()

        else:
            self.dismiss()
