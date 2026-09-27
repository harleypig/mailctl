"""The core returns data; only the CLI prints.

``config``, ``criteria``, ``sieve``, ``imap``, ``rules``, and the
``engine`` that drives them return structured values and raise
``MxFilterError``. Every piece of rendering, prompting, and progress output
lives in ``cli.py`` (CONVENTIONS.md).

The engine is held to one bar more: it must not know how it was called.
So it may not import ``argparse`` or the CLI, and may not reach for the
terminal or the environment -- a TUI, GUI, or web front-end has to be
able to drive it unchanged.

That split is worth a test rather than a convention alone for two reasons,
and the second is the sharp one:

* A core that prints cannot be tested without capturing stdout, and a
  second front-end would have to tear it apart before reusing it.
* ``config`` holds the mailbox password. A ``print`` or a ``getpass``
  added to it later is precisely how a credential reaches a transcript --
  the failure this repo treats as a hard boundary. A convention prevents
  that only until someone is in a hurry; this test prevents it every run.

The check is an AST walk rather than a grep, so the word "print" appearing
in a docstring -- which it does, in ``config.Secret`` -- is not a finding,
and a call written as ``builtins.print`` still is.
"""

import ast
from pathlib import Path

import pytest

import mxfilter

# Anything that puts a value in front of a person, or takes one from them.
BANNED_NAMES = {"print", "input", "breakpoint"}

# getpass is the CLI's to own: a core module that prompts owns a terminal,
# which is what stops a non-terminal front-end reusing it (config.password
# takes a `prompter` callback for exactly this reason).
BANNED_ATTRIBUTES = {"getpass", "getpass_", "print_exc"}

CORE_MODULES = ("config", "criteria", "sieve", "imap", "rules", "engine")

# What would tie the engine to one front-end. The CLI module is named both
# ways a package-relative import can spell it.
FRONT_END_MODULES = {"argparse", "cli", "mxfilter.cli"}

# The terminal and the environment: the CLI's to read, never the engine's.
FRONT_END_ATTRIBUTES = {"stdin", "stdout", "stderr", "environ", "getenv"}

# ############################################################################
# Helpers
# ############################################################################


# ----------------------------------------------------------------------------
def module_path(name: str) -> Path:
    """Return the source file of one mxfilter module."""
    return Path(mxfilter.__file__).parent / f"{name}.py"


# ----------------------------------------------------------------------------
def called_names(tree: ast.AST) -> list[str]:
    """Return the name of every function called anywhere in ``tree``.

    A bare call reports its identifier (``print``); a method or module
    call reports the attribute (``sys.stdout.write`` -> ``write``). That
    is deliberately blunt: over-reporting is a conversation, while
    under-reporting is a credential in a log.
    """
    names = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        function = node.func

        if isinstance(function, ast.Name):
            names.append(function.id)

        elif isinstance(function, ast.Attribute):
            names.append(function.attr)

    return names


# ----------------------------------------------------------------------------
def imported_modules(tree: ast.AST) -> set[str]:
    """Return every module ``tree`` imports, and each name taken from a
    relative import -- so ``from . import cli`` reports ``cli``."""
    imported = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module)

            if node.level:
                imported.update(alias.name for alias in node.names)

    return imported


# ----------------------------------------------------------------------------
def touched_attributes(tree: ast.AST) -> set[str]:
    """Return every attribute name read anywhere in ``tree``."""
    return {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }


# ----------------------------------------------------------------------------
def module_tree(name: str) -> ast.AST:
    """Parse one mxfilter module."""
    return ast.parse(module_path(name).read_text(encoding="utf-8"))


# ############################################################################
# The guard
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", CORE_MODULES)
def test_a_core_module_never_prints_or_prompts(name):
    source = module_path(name).read_text(encoding="utf-8")
    called = set(called_names(ast.parse(source)))

    found = called & (BANNED_NAMES | BANNED_ATTRIBUTES)

    assert found == set(), (
        f"mxfilter/{name}.py calls {sorted(found)}; presentation and "
        f"prompting belong in cli.py (CONVENTIONS.md > The core returns "
        f"data)"
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", CORE_MODULES)
def test_a_core_module_never_imports_a_prompting_module(name):
    """Importing ``getpass`` at all signals the split has been crossed."""
    assert "getpass" not in imported_modules(module_tree(name))


# ----------------------------------------------------------------------------
def test_the_engine_does_not_import_a_front_end():
    """Parsed arguments are the CLI's; the engine takes plain values."""
    found = imported_modules(module_tree("engine")) & FRONT_END_MODULES

    assert found == set(), (
        f"mxfilter/engine.py imports {sorted(found)}; the engine must not "
        f"know how it was called"
    )


# ----------------------------------------------------------------------------
def test_the_engine_does_not_touch_the_terminal_or_environment():
    """Whether stdin is a tty, and what is exported, are front-end facts."""
    found = touched_attributes(module_tree("engine")) & FRONT_END_ATTRIBUTES

    assert found == set(), (
        f"mxfilter/engine.py reads {sorted(found)}; interaction belongs in "
        f"the front-end"
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", CORE_MODULES)
def test_a_core_module_never_exits_the_process(name):
    """Libraries raise; only an executable may exit (code-style.md).

    ``MxFilterError`` exists so ``cli.main`` can turn a failure into one
    diagnostic line and a status code. A ``sys.exit`` in the core takes
    that decision away from every caller, including a future front-end.
    """
    source = module_path(name).read_text(encoding="utf-8")
    called = set(called_names(ast.parse(source)))

    assert "exit" not in called
    assert "_exit" not in called


# ----------------------------------------------------------------------------
def test_the_guard_would_actually_catch_a_violation():
    """A guard nobody has seen fail is a guard nobody should trust."""
    tree = ast.parse("import sys\ndef f(secret):\n    print(secret)\n")

    assert "print" in called_names(tree)


# ----------------------------------------------------------------------------
def test_the_engine_guards_would_actually_catch_a_violation():
    """Each spelling a front-end dependency could take is seen."""
    for source in (
        "import argparse\n",
        "from . import cli\n",
        "from .cli import x\n",
    ):
        assert imported_modules(ast.parse(source)) & FRONT_END_MODULES, source

    tree = ast.parse(
        "import os, sys\n"
        "def f():\n"
        "    return sys.stdin.isatty() or os.environ.get('X')\n"
    )

    assert {"stdin", "environ"} <= touched_attributes(tree)


# ----------------------------------------------------------------------------
def test_reveal_is_called_only_where_a_credential_is_handed_to_a_client():
    """Every real disclosure site, enumerated -- the reviewable list.

    ``Secret.reveal()`` is greppable precisely so this list can exist. A
    new call site is not necessarily wrong, but it is always a decision
    somebody should have made on purpose rather than in passing.
    """
    sites = {}

    for name in CORE_MODULES:
        source = module_path(name).read_text(encoding="utf-8")
        count = called_names(ast.parse(source)).count("reveal")

        if count:
            sites[name] = count

    assert sites == {"sieve": 1, "imap": 1}, (
        f"Secret.reveal() call sites changed: {sites}. Each one hands the "
        f"password to a connection method and nothing else; review the "
        f"new one against CONVENTIONS.md > Credentials."
    )
