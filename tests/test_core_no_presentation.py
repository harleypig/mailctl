"""The core returns data; only the CLI prints.

``config``, ``criteria``, ``rules``, every module under
``components/``, ``providers/``, and ``utilities/``, and the ``engine``
session they run in return structured values and raise
``MailctlError``. Every piece of rendering, prompting, and progress output
lives in ``cli.py`` (CONVENTIONS.md).

The engine and the utilities are held to one bar more: they must not
know how they were called. So they may not import ``argparse`` or the
CLI, and may not reach for the terminal or the environment -- a TUI, GUI,
or web front-end has to be able to drive them unchanged.

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
import re
from pathlib import Path

import pytest

import mailctl
from mailctl import cli

# Anything that puts a value in front of a person, or takes one from them.
BANNED_NAMES = {"print", "input", "breakpoint"}

# getpass is the CLI's to own: a core module that prompts owns a terminal,
# which is what stops a non-terminal front-end reusing it (config.password
# takes a `prompter` callback for exactly this reason).
BANNED_ATTRIBUTES = {"getpass", "getpass_", "print_exc"}

PACKAGE = Path(mailctl.__file__).parent

# The layered packages are walked rather than listed, so a module added to
# one is guarded the day it lands instead of the day somebody remembers.
LAYERED_PACKAGES = ("components", "providers", "utilities")

CORE_MODULES = (
    "config",
    "criteria",
    "rules",
    "engine",
    *sorted(
        path.relative_to(PACKAGE).with_suffix("").as_posix()
        for package in LAYERED_PACKAGES
        for path in (PACKAGE / package).rglob("*.py")
    ),
)

# The session and the utilities: the work, which must not know how it was
# called. Walked like the rest, so a new utility is held to it on arrival.
WORK_MODULES = (
    "engine",
    *(name for name in CORE_MODULES if name.startswith("utilities/")),
)

# What would tie the engine to one front-end. The CLI module is named both
# ways a package-relative import can spell it.
FRONT_END_MODULES = {"argparse", "cli", "mailctl.cli"}

# The terminal and the environment: the CLI's to read, never the engine's.
FRONT_END_ATTRIBUTES = {"stdin", "stdout", "stderr", "environ", "getenv"}

# ############################################################################
# Helpers
# ############################################################################


# ----------------------------------------------------------------------------
def module_path(name: str) -> Path:
    """Return the source file of one mailctl module."""
    return PACKAGE / f"{name}.py"


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
    """Parse one mailctl module."""
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
        f"mailctl/{name}.py calls {sorted(found)}; presentation and "
        f"prompting belong in cli.py (CONVENTIONS.md > The core returns "
        f"data)"
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", CORE_MODULES)
def test_a_core_module_never_imports_a_prompting_module(name):
    """Importing ``getpass`` at all signals the split has been crossed."""
    assert "getpass" not in imported_modules(module_tree(name))


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", WORK_MODULES)
def test_the_engine_does_not_import_a_front_end(name):
    """Parsed arguments are the CLI's; the engine takes plain values."""
    found = imported_modules(module_tree(name)) & FRONT_END_MODULES

    assert found == set(), (
        f"mailctl/{name}.py imports {sorted(found)}; the engine must not "
        f"know how it was called"
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", WORK_MODULES)
def test_the_engine_does_not_touch_the_terminal_or_environment(name):
    """Whether stdin is a tty, and what is exported, are front-end facts."""
    found = touched_attributes(module_tree(name)) & FRONT_END_ATTRIBUTES

    assert found == set(), (
        f"mailctl/{name}.py reads {sorted(found)}; interaction belongs in "
        f"the front-end"
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", CORE_MODULES)
def test_a_core_module_never_exits_the_process(name):
    """Libraries raise; only an executable may exit (code-style.md).

    ``MailctlError`` exists so ``cli.main`` can turn a failure into one
    diagnostic line and a status code. A ``sys.exit`` in the core takes
    that decision away from every caller, including a future front-end.
    """
    source = module_path(name).read_text(encoding="utf-8")
    called = set(called_names(ast.parse(source)))

    assert "exit" not in called
    assert "_exit" not in called


# ----------------------------------------------------------------------------
def test_the_walk_reaches_the_layered_packages():
    """A walk that found nothing would pass every guard above vacuously."""
    assert "components/managesieve/client" in CORE_MODULES
    assert "components/managesieve/script" in CORE_MODULES
    assert "components/imap/client" in CORE_MODULES
    assert "providers/mxroute/sieve" in CORE_MODULES
    assert "providers/mxroute/imap" in CORE_MODULES
    assert "utilities/rules" in CORE_MODULES
    assert "utilities/mail" in CORE_MODULES
    assert "utilities/rules" in WORK_MODULES


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

    assert sites == {
        "components/managesieve/client": 1,
        "components/imap/client": 1,
    }, (
        f"Secret.reveal() call sites changed: {sites}. Each one hands the "
        f"password to a connection method and nothing else; review the "
        f"new one against CONVENTIONS.md > Credentials."
    )


# ############################################################################
# A core message names no front-end's flag (#51)
# ############################################################################

# A flag is one front-end's control: "--max-messages" means nothing to a
# TUI user. So a core message states the condition, and a MailctlError code
# lets the CLI add its flags (cli.ERROR_TEXT). Any string a core module
# builds is checked, an f-string with each interpolated value standing in as
# "{}" -- so f"--{where}" is caught as well as "--after". Docstrings are
# not messages and are skipped.
FLAG = re.compile(r"--(?:[a-z]|\{\})")

# (module, string) -> why it may name a flag.
FLAG_ALLOWED = {
    ("config", "--password-file"): "provenance: the Source of a password "
    "that came from this flag, recorded because it did",
    ("config", "--password-cmd"): "provenance, as --password-file",
    ("config", "--password"): "provenance, as --password-file",
    ("config", "--{}"): "_flag_name: the Source of any setting that came "
    "from a flag, spelt as the flag that was given",
}


# ----------------------------------------------------------------------------
def built_strings(tree: ast.AST) -> list[tuple[int, str]]:
    """Every string a module builds, with its line: literals, and
    f-strings with "{}" for each value, but no docstring."""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node,
            ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    inside = set()
    found = []

    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            inside |= {id(part) for part in node.values}
            found.append(
                (
                    node.lineno,
                    "".join(
                        part.value if isinstance(part, ast.Constant) else "{}"
                        for part in node.values
                    ),
                )
            )

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings | inside
        ):
            found.append((node.lineno, node.value))

    return found


# ----------------------------------------------------------------------------
def flag_strings(name: str, tree: ast.AST) -> list[str]:
    """Every string ``name`` builds that names a flag, less the allowed."""
    return [
        f"line {line}: {text!r}"
        for line, text in built_strings(tree)
        if FLAG.search(text) and (name, text) not in FLAG_ALLOWED
    ]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", CORE_MODULES)
def test_a_core_message_names_no_flag(name):
    found = flag_strings(name, module_tree(name))

    assert found == [], (
        f"mailctl/{name}.py names a CLI flag in {found}; state the "
        f"condition, give the MailctlError a code, and let cli.ERROR_TEXT "
        f"add the flag"
    )


# ----------------------------------------------------------------------------
def test_every_allowed_flag_string_is_still_there():
    """A stale allowance would let the next string with that text in."""
    for name, text in FLAG_ALLOWED:
        built = [found for _, found in built_strings(module_tree(name))]

        assert text in built, text


# ----------------------------------------------------------------------------
def test_the_flag_guard_would_actually_catch_a_violation():
    """Each shape a flag can take in a message is seen; a docstring and a
    bare "--" separator are not."""
    tree = ast.parse(
        "def f(where, n):\n"
        '    """Takes --where, as the CLI spells it."""\n'
        '    a = "nothing to do -- use --fileinto"\n'
        '    b = f"--{where} names the rule"\n'
        '    c = f"over the cap; pass --max-messages {n}"\n'
        '    d = "a -- b"\n'
    )

    assert [text for _, text in built_strings(tree) if FLAG.search(text)] == [
        "--{} names the rule",
        "over the cap; pass --max-messages {}",
        "nothing to do -- use --fileinto",
    ]


# ############################################################################
# Every code the core raises is one the CLI renders (#51)
# ############################################################################


# ----------------------------------------------------------------------------
def raised_codes(tree: ast.AST) -> set[str]:
    """Every literal ``code=`` a module passes to a call."""
    return {
        keyword.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "code"
        and isinstance(keyword.value, ast.Constant)
        and isinstance(keyword.value.value, str)
    }


# ----------------------------------------------------------------------------
def test_every_raised_code_is_rendered_and_every_rendering_raised():
    """A code with no rendering loses its flag hint silently; a rendering
    nobody raises is dead text that drifts."""
    raised = set().union(*(raised_codes(module_tree(n)) for n in CORE_MODULES))

    assert "max_messages" in raised
    assert raised == set(cli.ERROR_TEXT)
