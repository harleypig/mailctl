"""The CLI's command tree, read off the parser rather than listed.

Commands are grouped, noun first (#219), so a command is a path such as
``filter add`` or ``server baseline save``. Read off the parser, a command
added later is covered by every test that walks them the day it lands.
"""

import argparse

from mailctl import cli


# ----------------------------------------------------------------------------
def subcommands(parser: argparse.ArgumentParser) -> dict:
    """The parsers one level below ``parser``, by name; empty for a
    command, which has none."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)

    return {}


# ----------------------------------------------------------------------------
def command_parsers(
    parser: argparse.ArgumentParser | None = None,
) -> dict[str, argparse.ArgumentParser]:
    """Every command's parser by its path, hidden ones included. A group
    is not a command; the commands under it are."""
    found = {}

    for name, sub in subcommands(parser or cli.build_parser()).items():
        if subcommands(sub):
            below = command_parsers(sub)
            found.update({f"{name} {path}": p for path, p in below.items()})

        else:
            found[name] = sub

    return found


# ----------------------------------------------------------------------------
def group_paths(parser: argparse.ArgumentParser | None = None) -> list[str]:
    """Every group's path, hidden ones included: ``server`` and ``server
    baseline``."""
    found = []

    for name, sub in subcommands(parser or cli.build_parser()).items():
        if subcommands(sub):
            found.append(name)
            found += [f"{name} {path}" for path in group_paths(sub)]

    return found
