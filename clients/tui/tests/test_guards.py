"""The two lines the TUI is held to (ADR 0009).

1. **It imports nothing from mailctl.** It is a client of the API, so
   every fact reaches it as a document across the process boundary.
   Every module of the package is walked, not listed, and an import of
   ``mailctl`` or anything under it, static or by name at run time, is
   refused. ``mailctl_tui`` is the TUI's own name and is not ``mailctl``.
2. **It offers only what ``initialize``'s capabilities allow.** Every key
   binding comes from ``offer.COMMANDS``, and a command whose capability
   the provider does not declare is hidden and will not run.

Each guard is seen to catch a planted violation, so neither passes for
reading nothing.
"""

import ast
from pathlib import Path

import mailctl_tui
from conftest import FILTERS, SIZE, settle
from mailctl_tui import app as app_module
from mailctl_tui.app import MailctlApp, PasswordScreen
from mailctl_tui.offer import COMMANDS, Command, offered
from textual.binding import Binding

PACKAGE = Path(mailctl_tui.__file__).parent

# What loads a module by its name, given as a string.
DYNAMIC_IMPORTS = {"import_module", "__import__"}


# ############################################################################
# 1. Nothing from mailctl
# ############################################################################


# ----------------------------------------------------------------------------
def is_mailctl(name: str) -> bool:
    return name == "mailctl" or name.startswith("mailctl.")


# ----------------------------------------------------------------------------
def mailctl_imports(source: str) -> list[str]:
    """Every ``mailctl`` module ``source`` imports, by line."""
    found = []

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]

        elif isinstance(node, ast.ImportFrom):
            # A relative import stays inside the TUI's own package.
            names = [node.module or ""] if node.level == 0 else []

        elif (
            isinstance(node, ast.Call)
            and (
                getattr(node.func, "id", None) in DYNAMIC_IMPORTS
                or getattr(node.func, "attr", None) in DYNAMIC_IMPORTS
            )
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names = [node.args[0].value]

        else:
            continue

        found += [
            f"{node.lineno}: {name}" for name in names if is_mailctl(name)
        ]

    return found


# ----------------------------------------------------------------------------
def modules() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


# ----------------------------------------------------------------------------
def test_the_walk_reaches_every_module():
    names = {path.name for path in modules()}

    assert {"__init__.py", "app.py", "client.py", "offer.py"} <= names


# ----------------------------------------------------------------------------
def test_the_tui_imports_nothing_from_mailctl():
    found = {
        str(path.relative_to(PACKAGE)): imports
        for path in modules()
        if (imports := mailctl_imports(path.read_text(encoding="utf-8")))
    }

    assert found == {}


# ----------------------------------------------------------------------------
def test_the_import_guard_catches_each_kind_of_import():
    planted = "\n".join(
        [
            "import mailctl",
            "import os, mailctl.config",
            "from mailctl import api",
            "from mailctl.json_output import dumps",
            "importlib.import_module('mailctl.engine')",
            "__import__('mailctl')",
        ]
    )

    assert mailctl_imports(planted) == [
        "1: mailctl",
        "2: mailctl.config",
        "3: mailctl",
        "4: mailctl.json_output",
        "5: mailctl.engine",
        "6: mailctl",
    ]


# ----------------------------------------------------------------------------
def test_the_import_guard_lets_the_tuis_own_name_through():
    allowed = "\n".join(
        [
            "import mailctl_tui",
            "from mailctl_tui.offer import COMMANDS",
            "from . import client",
            "import mailctlx",
        ]
    )

    assert mailctl_imports(allowed) == []


# ############################################################################
# 2. Only what the capabilities allow
# ############################################################################


# ----------------------------------------------------------------------------
def stray_bindings(*classes) -> list[str]:
    """Every binding on ``classes`` whose action is not an offered
    command's, by class and key."""
    commands = {command.action for command in COMMANDS}
    stray = []

    for widget in classes:
        for binding in widget.BINDINGS:
            assert isinstance(binding, Binding)

            if binding.action not in commands:
                stray.append(f"{widget.__name__}: {binding.key}")

    return stray


# ----------------------------------------------------------------------------
def test_every_binding_is_an_offered_command():
    assert stray_bindings(MailctlApp) == []


# ----------------------------------------------------------------------------
def test_the_binding_guard_catches_a_stray_binding():
    class Planted(MailctlApp):
        BINDINGS = [  # noqa: RUF012 -- a class built to break the guard
            *MailctlApp.BINDINGS,
            Binding("m", "mark", "Mark"),
        ]

    assert stray_bindings(Planted) == ["Planted: m"]


# ----------------------------------------------------------------------------
def test_the_command_palette_is_off():
    """Its system commands would be offered from outside the list."""
    assert MailctlApp.ENABLE_COMMAND_PALETTE is False


# ----------------------------------------------------------------------------
def test_the_password_screen_binds_only_its_own_cancel():
    """The one binding outside the list: a modal's way out, which offers
    no operation."""
    bindings = PasswordScreen.BINDINGS
    actions = [b.action for b in bindings if isinstance(b, Binding)]

    assert len(actions) == len(bindings)
    assert actions == ["decline"]


# ----------------------------------------------------------------------------
def test_offered_reads_the_capability_and_fails_closed():
    command = Command("m", "mark", "Mark", requires="mark")

    assert offered(command, {"mark": True})
    assert not offered(command, {"mark": False})
    assert not offered(command, {})
    assert not offered(command, {"mark": "yes"})
    assert offered(Command("r", "reload", "Reload"), {})


# ----------------------------------------------------------------------------
async def test_a_command_is_hidden_unless_its_capability_is_declared(
    scene, monkeypatch
):
    """A planted command needing ``mark``: absent from the footer and
    refused under a provider without it, present under one with it.

    Break: have ``check_action`` return True for every action and the
    first half goes red."""
    planted = Command("m", "mark", "Mark", requires="mark")
    monkeypatch.setattr(app_module, "COMMANDS", (*COMMANDS, planted))

    class Planted(MailctlApp):
        BINDINGS = [  # noqa: RUF012 -- a class built to break the guard
            *MailctlApp.BINDINGS,
            Binding("m", "mark", "Mark"),
        ]
        marked = 0

        def action_mark(self) -> None:
            self.marked += 1

    for declared in (False, True):
        scene.write(capabilities={"mark": declared}, filters=FILTERS)
        app = scene.app(Planted)

        async with app.run_test(size=SIZE) as pilot:
            await settle(
                pilot,
                lambda app=app: (
                    "filter(s)" in str(app.query_one("#status").render())
                ),
            )
            await pilot.press("m")
            await pilot.pause()

            keys = {key for key, _binding in footer_keys(app)}

            assert ("m" in keys) is declared
            assert app.marked == (1 if declared else 0)


# ----------------------------------------------------------------------------
def footer_keys(app) -> list[tuple[str, object]]:
    """The keys the footer would show now: active and allowed."""
    shown = []

    for key, active in app.active_bindings.items():
        if active.enabled and active.binding.show:
            shown.append((key, active.binding))

    return shown
