"""The TUI: mailctl's filters on one screen, read through its API.

Presentation only (ADR 0008, ADR 0009). It starts ``mailctl-api`` with
whatever arguments it was given beyond its own, calls ``initialize``, and
shows the document ``filter.list`` returns. Every fact on the screen is
one of the API's documents rendered, never worked out here.
"""

import argparse
import asyncio
import shlex
import sys
from typing import ClassVar

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
)

from .client import ApiClient, ApiError, ApiGone, Declined
from .offer import COMMANDS, offered
from .text import one_line

__all__ = ["MailctlApp", "PasswordScreen", "main"]

DEFAULT_API = "mailctl-api"


# ############################################################################
# The password prompt
# ############################################################################


class PasswordScreen(ModalScreen[str | None]):
    """Asks for the password the API needs, masked.

    It hands the value to whoever pushed it and keeps nothing: the field
    is cleared as it is dismissed, so the screen holds no copy after it.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "decline", "Cancel")
    ]

    DEFAULT_CSS = """
    PasswordScreen {
        align: center middle;
    }
    PasswordScreen > Vertical {
        width: 60;
        height: auto;
        padding: 1 2;
        border: tall $accent;
        background: $panel;
    }
    PasswordScreen Label {
        margin-bottom: 1;
    }
    """

    # ------------------------------------------------------------------------
    def __init__(self, prompt: str):
        super().__init__()
        self.prompt = prompt

    # ------------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(Text(one_line(self.prompt) or "Password:"))
            yield Input(password=True, id="password")
            yield Label(Text("Enter to send it, Escape to cancel."))

    # ------------------------------------------------------------------------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        value = event.value
        event.input.value = ""
        self.dismiss(value)

    # ------------------------------------------------------------------------
    def action_decline(self) -> None:
        self.query_one(Input).value = ""
        self.dismiss(None)


# ############################################################################
# The app
# ############################################################################


class MailctlApp(App[None]):
    """The filter list, the server's alerts, and a password prompt."""

    TITLE = "mailctl"
    # The palette's own commands are not ours to gate, so it is off: every
    # command reaches the user through offer.COMMANDS.
    ENABLE_COMMAND_PALETTE = False

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(command.key, command.action, command.description)
        for command in COMMANDS
    ]

    DEFAULT_CSS = """
    #status {
        padding: 0 1;
        color: $text-muted;
    }
    #filters {
        height: 1fr;
    }
    #findings {
        padding: 0 1;
        height: auto;
        max-height: 8;
        color: $warning;
    }
    #alerts {
        height: auto;
        max-height: 6;
        border-top: solid $warning;
        display: none;
    }
    #alerts.shown {
        display: block;
    }
    """

    # ------------------------------------------------------------------------
    def __init__(self, command: list[str], env: dict[str, str] | None = None):
        super().__init__()
        self.capabilities: dict = {}
        self.client = ApiClient(
            command,
            on_notification=self.on_api_notification,
            on_request=self.on_api_request,
            env=env,
        )

    # ------------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Starting mailctl ...", id="status", markup=False)
        yield DataTable(id="filters", cursor_type="row", zebra_stripes=True)
        yield Static("", id="findings", markup=False)
        yield RichLog(id="alerts", markup=False, wrap=True)
        yield Footer()

    # ------------------------------------------------------------------------
    def on_mount(self) -> None:
        table = self.query_one("#filters", DataTable)
        table.add_columns("#", "Filter", "When", "Marks")
        self.start()

    # ------------------------------------------------------------------------
    def check_action(
        self, action: str, parameters: tuple[object, ...]
    ) -> bool | None:
        """Hide a command the provider's capabilities do not allow."""
        for command in COMMANDS:
            if command.action == action:
                return offered(command, self.capabilities)

        return True

    # ------------------------------------------------------------------------
    @work(group="start")
    async def start(self) -> None:
        try:
            await self.client.start()
            reply = await self.client.request("initialize")

        except (ApiError, ApiGone) as exc:
            self.show_status(failure(exc))

            return

        self.capabilities = reply.get("capabilities", {})
        self.sub_title = one_line(str(reply.get("provider", "")))
        self.refresh_bindings()

        await self.load_filters()

    # ------------------------------------------------------------------------
    async def load_filters(self) -> None:
        self.show_status("Reading the filters ...")

        try:
            document = await self.client.request("filter.list")

        except (ApiError, ApiGone) as exc:
            self.show_status(failure(exc))

            return

        self.show_filters(document)

    # ------------------------------------------------------------------------
    def show_status(self, words: str) -> None:
        self.query_one("#status", Static).update(Text(words))

    # ------------------------------------------------------------------------
    def show_filters(self, document: dict) -> None:
        """Lay out ``filter.list``'s document, as it is."""
        filters = document.get("filters", [])
        name = one_line(str(document.get("filterset") or ""))
        table = self.query_one("#filters", DataTable)
        table.clear()

        for item in filters:
            table.add_row(
                Text(str(item.get("position", ""))),
                Text(one_line(str(item.get("name", "")))),
                Text(condition(item)),
                Text(marks(item)),
            )

        if not filters:
            self.show_status(f"Filter set {name!r} has no filters.")

        else:
            self.show_status(
                f"Filter set {name!r}: {len(filters)} filter(s), in the "
                "order they run."
            )

        self.query_one("#findings", Static).update(
            Text(findings(document.get("findings", [])))
        )

    # ------------------------------------------------------------------------
    def action_reload(self) -> None:
        self.reload()

    # ------------------------------------------------------------------------
    @work(exclusive=True, group="reload")
    async def reload(self) -> None:
        await self.load_filters()

    # ------------------------------------------------------------------------
    async def action_quit(self) -> None:
        await self.client.close()
        self.exit()

    # ------------------------------------------------------------------------
    def on_api_notification(self, method: str, params: dict) -> None:
        """``server.alert`` is shown as it arrives; the API sends each
        once per session, so each is shown once."""
        if method != "server.alert":
            return

        alert = params.get("document", {}).get("alert", {})
        words = alert_line(alert)
        log = self.query_one("#alerts", RichLog)
        log.add_class("shown")
        log.write(Text(words))
        self.notify(words, severity="warning", markup=False)

    # ------------------------------------------------------------------------
    async def on_api_request(self, method: str, params: dict) -> dict:
        """Answer what the API asks: only ``credentials.prompt``.

        The password goes from the masked field into the reply and nowhere
        else: not logged, not printed, not kept on this object.
        """
        if method != "credentials.prompt":
            raise Declined

        answer: asyncio.Future[str | None] = (
            asyncio.get_running_loop().create_future()
        )
        self.push_screen(
            PasswordScreen(str(params.get("prompt", ""))),
            callback=answer.set_result,
        )
        password = await answer

        if password is None:
            raise Declined

        return {"password": password}

    # ------------------------------------------------------------------------
    async def on_unmount(self) -> None:
        await self.client.close()


# ############################################################################
# Rendering a document's fields
# ############################################################################


# ----------------------------------------------------------------------------
def condition(item: dict) -> str:
    """A filter's tests on one line, as the CLI's ``filter list`` words
    them: header, match type, keys, joined by the filter's combinator."""
    tests = item.get("tests", [])
    unmodelled = [one_line(str(name)) for name in item.get("unmodelled", [])]

    if not tests and unmodelled:
        return f"({', '.join(unmodelled)} -- not modelled)"

    joiner = " OR " if item.get("combinator") == "anyof" else " AND "
    parts = [
        f"{one_line(str(test.get('header', '')))} "
        f"{one_line(str(test.get('match_type', '')))} "
        + " | ".join(repr(one_line(str(key))) for key in test.get("keys", []))
        for test in tests
    ]
    line = joiner.join(parts) or "(no condition)"

    if unmodelled:
        line += f"  [+ {', '.join(unmodelled)}: not modelled]"

    return line


# ----------------------------------------------------------------------------
def marks(item: dict) -> str:
    words = []

    if item.get("disabled"):
        words.append("disabled")

    if item.get("stops"):
        words.append("stop")

    return ", ".join(words)


# ----------------------------------------------------------------------------
def findings(items: list) -> str:
    """The filters that cannot fire, decided ones marked ``!`` and
    suspected ones ``?``, as the CLI shows them."""
    if not items:
        return ""

    lines = ["Filters that cannot fire where they are:"]

    for item in items:
        certain = item.get("certainty") == "certain"
        mark, verb = ("!", "never runs") if certain else ("?", "may never run")
        narrow = one_line(str(item.get("narrow", "")))
        reason = one_line(str(item.get("reason", "")))
        lines.append(f"  {mark} {narrow!r} {verb} -- {reason}")

    return "\n".join(lines)


# ----------------------------------------------------------------------------
def alert_line(alert: dict) -> str:
    kind = one_line(str(alert.get("kind", "alert")))
    channel = one_line(str(alert.get("channel", "mail")))
    text = one_line(str(alert.get("text", "")))

    return f"{kind.capitalize()} from the {channel} server: {text}"


# ----------------------------------------------------------------------------
def failure(exc: ApiError | ApiGone) -> str:
    """An error as the API worded it; its message names no front-end's
    control, so it reads right here (ADR 0008 *Errors keep their codes*)."""
    if isinstance(exc, ApiError):
        message = exc.data.get("message") or exc.message

        return f"mailctl refused: {one_line(str(message))}"

    return one_line(str(exc))


# ############################################################################
# Starting it
# ############################################################################


# ----------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mailctl-tui",
        description="A terminal UI for mailctl. Arguments it does not "
        "know are passed to mailctl-api, which takes mailctl's connection "
        "settings.",
    )
    parser.add_argument(
        "--api",
        default=DEFAULT_API,
        metavar="COMMAND",
        help=f"the command that starts the API; default {DEFAULT_API}",
    )

    return parser


# ----------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    args, passed = build_parser().parse_known_args(argv)
    MailctlApp([*shlex.split(args.api), *passed]).run()

    return 0


if __name__ == "__main__":
    sys.exit(main())
