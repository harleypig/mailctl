"""Every command the TUI offers, and the capability each needs.

The one list a key binding is built from, so nothing reaches the screen
without passing :func:`offered`. ``initialize`` returns the provider's
capabilities; a command whose capability is absent or false is hidden,
as the CLI hides an option its provider does not declare (CONVENTIONS
*Providers*). Before ``initialize`` answers there are no capabilities,
so only the commands that need none are offered.
"""

from dataclasses import dataclass

__all__ = ["COMMANDS", "Command", "offered"]


@dataclass(frozen=True)
class Command:
    """A key, the Textual action it runs, and the footer's word for it.

    ``requires`` names a key of ``initialize``'s ``capabilities``, or is
    None for a command every provider can serve.
    """

    key: str
    action: str
    description: str
    requires: str | None = None


COMMANDS: tuple[Command, ...] = (
    Command("r", "reload", "Reload"),
    Command("q", "quit", "Quit"),
)


# ----------------------------------------------------------------------------
def offered(command: Command, capabilities: dict) -> bool:
    """Whether ``command`` is offered; a capability the provider does not
    name counts as not declared."""
    if command.requires is None:
        return True

    return capabilities.get(command.requires) is True
