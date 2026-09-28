"""Layer 1 knows its protocol and nothing about any host (ADR 0006).

A module under ``mailctl/components/`` may import the standard library, the
protocol library its own component wraps, other ``mailctl.components``
modules, and ``MailctlError`` -- and nothing else. Never the engine, the
CLI, mailctl's ``Config``, or a provider: each of those would tie a
protocol library to one host or one front-end, which is the mix the two
layers exist to undo.

The wrapped library is per component: ``imap`` importing ``sievelib`` would
tie the Gmail provider, which has no Sieve, to a library it never uses.

The line runs the other way too. The engine, the utilities, and the CLI
import no layer-1 module at all, and the engine and the utilities reach a
provider only through the interface and the registry, never naming one --
which is what lets a second provider be added with no engine change.

The check resolves relative imports to absolute names first, because
``from ...config import Config`` and ``from mailctl.config import Config``
are the same dependency spelled two ways.
"""

import ast
import sys
from pathlib import Path

import pytest

import mailctl

PACKAGE = Path(mailctl.__file__).parent

# The protocol library each layer-1 component wraps, by import name.
WRAPPED_LIBRARIES = {"imap": "imapclient", "managesieve": "sievelib"}

# The one name layer 1 may take from mailctl's own top level.
ALLOWED_FROM_MAILCTL = {"MailctlError"}

COMPONENT_FILES = sorted((PACKAGE / "components").rglob("*.py"))


# ############################################################################
# Helpers
# ############################################################################


# ----------------------------------------------------------------------------
def package_of(path: Path) -> str:
    """Return the dotted package a module file's relative imports start in.

    Dropping the last part is right for both shapes: ``pkg/mod.py`` is in
    ``pkg``, and ``pkg/__init__.py`` *is* ``pkg``.
    """
    relative = path.relative_to(PACKAGE.parent).with_suffix("")

    return ".".join(relative.parts[:-1])


# ----------------------------------------------------------------------------
def imports(source: str, package: str) -> list[tuple[str, set[str]]]:
    """Return ``(absolute module, names taken)`` for every import."""
    found = []

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend((alias.name, set()) for alias in node.names)

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""

            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                module = ".".join([*base, module] if module else base)

            found.append((module, {alias.name for alias in node.names}))

    return found


# ----------------------------------------------------------------------------
def violations(source: str, package: str) -> list[str]:
    """Return every import in ``source`` that layer 1 may not make.

    ``package`` names the component, as ``mailctl.components.<name>...``,
    and so which wrapped library the source may use.
    """
    component = package.split(".")[2] if package.count(".") >= 2 else ""
    wrapped = WRAPPED_LIBRARIES.get(component)
    bad = []

    for module, names in imports(source, package):
        top = module.split(".")[0]

        if top in sys.stdlib_module_names or top == wrapped:
            continue

        if module == "mailctl.components" or module.startswith(
            "mailctl.components."
        ):
            continue

        if module == "mailctl" and names <= ALLOWED_FROM_MAILCTL:
            continue

        bad.append(f"{module} {sorted(names)}".rstrip(" []"))

    return bad


# ############################################################################
# The guard
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_walk_finds_the_component_modules():
    """A walk that found nothing would pass the guard below vacuously."""
    names = {path.relative_to(PACKAGE).as_posix() for path in COMPONENT_FILES}

    assert "components/managesieve/client.py" in names
    assert "components/managesieve/script.py" in names
    assert "components/managesieve/servers/pigeonhole.py" in names
    assert "components/imap/client.py" in names
    assert "components/imap/servers/dovecot.py" in names


# ----------------------------------------------------------------------------
def test_every_component_names_the_library_it_wraps():
    """A component missing from the table could import no library at all,
    and one that should not be there would be allowed one."""
    components = {
        path.name
        for path in (PACKAGE / "components").iterdir()
        if (path / "__init__.py").exists()
    }

    assert components == set(WRAPPED_LIBRARIES)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    COMPONENT_FILES,
    ids=[path.relative_to(PACKAGE).as_posix() for path in COMPONENT_FILES],
)
def test_a_component_imports_only_its_own_layer(path):
    found = violations(path.read_text(encoding="utf-8"), package_of(path))

    assert found == [], (
        f"{path.relative_to(PACKAGE)} imports {found}; layer 1 may import "
        f"only the stdlib, the library it wraps, mailctl.components, and "
        f"MailctlError (ADR 0006)"
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source",
    [
        "from ...config import Config\n",
        "from mailctl.config import Secret\n",
        "from ... import engine\n",
        "from ...engine import connect\n",
        "from ... import cli\n",
        "from ...providers.mxroute.sieve import ROUNDCUBE_DIALECT\n",
        "import mailctl.providers\n",
        "import imapclient\n",
    ],
)
def test_the_guard_would_actually_catch_a_violation(source):
    """Each spelling a forbidden dependency could take is seen."""
    assert violations(source, "mailctl.components.managesieve"), source


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source",
    [
        "from ...config import Config\n",
        "from ...providers.mxroute.imap import imap_session\n",
        "from ...criteria import Criteria\n",
        "import sievelib\n",
        "from sievelib.managesieve import Client\n",
    ],
)
def test_the_guard_catches_a_violation_in_the_imap_component(source):
    """The same line for ``imap``, whose library is a different one."""
    assert violations(source, "mailctl.components.imap"), source


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source",
    [
        "from ... import MailctlError\n",
        "from .script import parse_script\n",
        "from ..managesieve import client\n",
        "from sievelib.managesieve import Client\n",
        "import socket\n",
    ],
)
def test_the_guard_allows_what_layer_1_may_use(source):
    """Too wide a guard teaches people to ignore it."""
    assert violations(source, "mailctl.components.managesieve") == [], source


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source",
    [
        "from ... import MailctlError\n",
        "from .folders import same_folder\n",
        "from imapclient import IMAPClient\n",
        "from imapclient.exceptions import LoginError\n",
        "import email\n",
    ],
)
def test_the_guard_allows_what_the_imap_component_may_use(source):
    assert violations(source, "mailctl.components.imap") == [], source


# ############################################################################
# The engine and the CLI talk to a provider, never to a component
# ############################################################################

# The utilities, walked rather than listed, so a new one is held to the
# engine's line the day it lands.
UTILITY_FILES = {
    f"utilities/{path.stem}": path
    for path in sorted((PACKAGE / "utilities").glob("*.py"))
}

# The work: the session and the utilities. Each may reach a provider only
# through the interface and the registry, so it can never name one.
WORK_FILES = {"engine": PACKAGE / "engine.py", **UTILITY_FILES}

# The front of the tree. Each may reach the protocols only through a
# provider.
FRONT_FILES = {**WORK_FILES, "cli": PACKAGE / "cli.py"}

ENGINE_PROVIDER_MODULES = {
    "mailctl.providers.base",
    "mailctl.providers.registry",
}


# ----------------------------------------------------------------------------
def component_imports(source: str, package: str) -> list[str]:
    """Every import of a layer-1 module in ``source``."""
    return [
        module
        for module, _names in imports(source, package)
        if module == "mailctl.components"
        or module.startswith("mailctl.components.")
    ]


# ----------------------------------------------------------------------------
def provider_imports(source: str, package: str) -> list[str]:
    """Every import of a provider module other than the interface's own."""
    return [
        module
        for module, _names in imports(source, package)
        if module.startswith("mailctl.providers")
        and module not in ENGINE_PROVIDER_MODULES
    ]


# ----------------------------------------------------------------------------
def provider_name_literals(source: str) -> list[str]:
    """String literals in code that name a registered provider.

    Docstrings are prose and are skipped; a literal anywhere else is the
    engine comparing against, or choosing, one provider by name.
    """
    from mailctl.providers.registry import PROVIDERS

    tree = ast.parse(source)
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

    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and any(name in node.value.lower() for name in PROVIDERS)
    ]


# ----------------------------------------------------------------------------
def test_the_walk_finds_the_utility_modules():
    """A walk that found nothing would pass the guards below vacuously."""
    assert "utilities/rules" in UTILITY_FILES
    assert "utilities/mail" in UTILITY_FILES
    assert "utilities/__init__" in UTILITY_FILES


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("front", sorted(FRONT_FILES))
def test_the_front_imports_no_component(front):
    path = FRONT_FILES[front]
    found = component_imports(
        path.read_text(encoding="utf-8"), package_of(path)
    )

    assert found == [], (
        f"{front}.py imports {found}; the engine, the utilities, and the "
        f"CLI reach a protocol only through a provider (ADR 0006)"
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("work", sorted(WORK_FILES))
def test_the_engine_reaches_providers_only_through_the_interface(work):
    path = WORK_FILES[work]
    source = path.read_text(encoding="utf-8")

    assert provider_imports(source, package_of(path)) == []
    assert provider_name_literals(source) == []


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source",
    [
        "from .components.imap import ImapSession\n",
        "from mailctl.components.managesieve import script\n",
        "import mailctl.components\n",
    ],
)
def test_the_front_guard_would_catch_a_component_import(source):
    assert component_imports(source, "mailctl"), source


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source",
    [
        "from .providers.mxroute import MxrouteProvider\n",
        "from .providers.mxroute.sieve import merge_rule\n",
    ],
)
def test_the_engine_guard_would_catch_a_named_provider(source):
    assert provider_imports(source, "mailctl"), source


# ----------------------------------------------------------------------------
def test_the_engine_guard_would_catch_a_provider_named_in_code():
    source = (
        'def f(p):\n    """Mentions mxroute in prose, which is fine."""\n'
        '    return p.name == "mxroute"\n'
    )

    assert provider_name_literals(source) == ["mxroute"]


# ----------------------------------------------------------------------------
def test_the_engine_guard_allows_the_interface_and_the_registry():
    source = (
        "from .providers.base import Provider\n"
        "from .providers.registry import provider_for\n"
    )

    assert provider_imports(source, "mailctl") == []


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source",
    [
        "from ...providers.base import Provider\n",
        "from mailctl.providers import registry\n",
    ],
)
def test_a_component_may_not_import_a_provider(source):
    """Layer 1 never reaches up: the interface included."""
    assert violations(source, "mailctl.components.imap"), source


# ############################################################################
# The neutral model is layer 2's own, borrowed from no component (#99)
# ############################################################################

# The interface and the model it speaks. A second provider imports these,
# so a component import here would tie that provider to a protocol it may
# not use -- the Gmail provider to the Sieve component, for one.
NEUTRAL_FILES = {
    "base": PACKAGE / "providers" / "base.py",
    "model": PACKAGE / "providers" / "model.py",
}

# The records the engine and a front-end build and read. Each must be
# defined in the neutral model, not merely re-exported from somewhere.
MODEL_TYPES = (
    "ActionSpec",
    "DeliveryCreate",
    "DisplayDiff",
    "ExtensionState",
    "FolderCreation",
    "FolderListing",
    "MailActionPlan",
    "MailActionResult",
    "MessageSummary",
    "Placement",
)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("neutral", sorted(NEUTRAL_FILES))
def test_the_neutral_model_imports_no_component(neutral):
    path = NEUTRAL_FILES[neutral]
    found = component_imports(
        path.read_text(encoding="utf-8"), "mailctl.providers"
    )

    assert found == [], (
        f"providers/{neutral}.py imports {found}; the provider interface "
        f"and its model borrow nothing from layer 1 (#99)"
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", MODEL_TYPES)
def test_every_model_type_is_defined_in_the_neutral_model(name):
    """The engine's, the utilities', and the interface's copy is the
    model's own class."""
    from mailctl import engine, utilities
    from mailctl.providers import base, model

    record = getattr(model, name)

    assert record.__module__ == "mailctl.providers.model"
    assert getattr(base, name) is record

    for module in (
        engine,
        *(getattr(utilities, n) for n in utilities.__all__),
    ):
        if hasattr(module, name):
            assert getattr(module, name) is record


# ----------------------------------------------------------------------------
def test_the_neutral_guard_would_catch_a_borrowed_type():
    """Known positive: the import base.py carried before #99."""
    source = "from ..components.managesieve import DisplayDiff, Placement\n"

    assert component_imports(source, "mailctl.providers") == [
        "mailctl.components.managesieve"
    ]
