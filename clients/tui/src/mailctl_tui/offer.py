"""Every command the TUI offers, and the capability each needs.

The one list a key binding or a gated form option is built from, so
nothing reaches the screen without passing :func:`offered`. ``initialize``
returns the provider's capabilities; a command whose capability is absent
or false is hidden, as the CLI hides an option its provider does not
declare (CONVENTIONS *Providers*). Before ``initialize`` answers there are
no capabilities, so only the commands that need none are offered.
"""

from dataclasses import dataclass

from textual.binding import Binding

__all__ = [
    "ADD",
    "COMMANDS",
    "FILTERS",
    "MESSAGE",
    "PLAN",
    "SEARCH",
    "Command",
    "bindings",
    "command",
    "offered",
]

# The screens a command belongs to. A command is offered on its own screen
# and nowhere else.
FILTERS = "filters"
SEARCH = "search"
MESSAGE = "message"
ADD = "add"
PLAN = "plan"


@dataclass(frozen=True)
class Command:
    """A command: the screen it is on, its key, the Textual action it
    runs, and the words the footer or the form shows for it.

    A command with no ``key`` is an option on a form, shown or hidden by
    the same test. ``requires`` names a key of ``initialize``'s
    ``capabilities`` that must be true; ``needs_action`` names an entry
    its ``actions`` list must hold. Either may be None.
    """

    screen: str
    key: str | None
    action: str
    description: str
    requires: str | None = None
    needs_action: str | None = None


COMMANDS: tuple[Command, ...] = (
    Command(FILTERS, "r", "reload", "Reload"),
    Command(FILTERS, "s", "search", "Search mail"),
    Command(FILTERS, "q", "quit", "Quit"),
    Command(SEARCH, "escape", "back", "Back"),
    Command(MESSAGE, "a", "add_like", "Add a filter like this"),
    Command(MESSAGE, "escape", "back", "Back"),
    Command(ADD, "ctrl+s", "plan", "Show what would change"),
    Command(ADD, "escape", "back", "Cancel"),
    Command(ADD, None, "move_to", "Move it to", needs_action="fileinto"),
    Command(
        ADD,
        None,
        "create_folder",
        "Create the folder if it is missing",
        needs_action="fileinto",
    ),
    Command(
        ADD,
        None,
        "subscribe",
        "Show a folder it creates in webmail",
        requires="folder_visibility",
        needs_action="fileinto",
    ),
    Command(ADD, None, "mark_read", "Mark it read", needs_action="flag"),
    Command(
        ADD, None, "keep", "Keep it in the inbox too", needs_action="keep"
    ),
    Command(ADD, None, "discard", "Delete it", needs_action="discard"),
    Command(
        ADD, None, "no_stop", "Let later filters see it too", requires="stop"
    ),
    Command(ADD, None, "placement", "Where it goes", requires="ordering"),
    Command(PLAN, "y", "save", "Save it"),
    Command(PLAN, "escape", "cancel", "Cancel"),
    Command(PLAN, "enter", "done", "Done"),
)


# ----------------------------------------------------------------------------
def offered(item: Command, capabilities: dict) -> bool:
    """Whether ``item`` is offered; a capability the provider does not
    name counts as not declared, and so does an ``actions`` that is not a
    list."""
    if item.requires is not None and (
        capabilities.get(item.requires) is not True
    ):
        return False

    if item.needs_action is not None:
        actions = capabilities.get("actions")

        return isinstance(actions, list) and item.needs_action in actions

    return True


# ----------------------------------------------------------------------------
def command(screen: str, action: str) -> Command:
    """The command ``action`` on ``screen``."""
    for item in COMMANDS:
        if item.screen == screen and item.action == action:
            return item

    raise KeyError(f"{screen}: {action}")


# ----------------------------------------------------------------------------
def bindings(screen: str) -> list[Binding]:
    """The key bindings of ``screen``, from the list and nothing else."""
    return [
        Binding(item.key, item.action, item.description)
        for item in COMMANDS
        if item.screen == screen and item.key is not None
    ]
