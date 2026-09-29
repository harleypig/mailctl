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

At the top, an interface calls only the utilities (ADR 0007): it opens a
session and hands it to them, and never imports a provider or a component
or reads a session's transport, connection, or dialect. The utilities, in
turn, use only the transport the session guards, and one that only reads
-- anything not carrying out a plan -- never reaches a transport write.

The check resolves relative imports to absolute names first, because
``from ...config import Config`` and ``from mailctl.config import Config``
are the same dependency spelled two ways.
"""

import ast
import re
import sys
from pathlib import Path

import pytest

import mailctl
from mailctl.providers.base import TRANSPORT_KINDS, WRITE

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
        "from .providers.mxroute import MXROUTE\n",
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
    "FetchedMessage",
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


# ############################################################################
# The provider's two halves (ADR 0007)
# ############################################################################

# The dialect is offline and the transport is communication only. Each
# half's modules are derived, not listed: the module defining the half's
# class, and every module of the same provider package it imports, however
# indirectly. A new helper module is held to its half's line the day it is
# imported.

# Modules that build, edit, or check a rule, and the names they define. A
# transport may import none of them: it stores what it is handed.
BUILDING_MODULES = {
    "mailctl.components.managesieve.script",
    "mailctl.components.managesieve.emit",
    "mailctl.rules",
}

# The utilities, where the re-check of a search's candidates lives. A
# transport importing one would be composing -- or narrowing -- what it
# should only fetch.
RECHECK_PACKAGE = "mailctl.utilities"

# Modules that open a connection. A dialect may import none of them, nor
# any name a component's client module defines, however it is spelled: a
# component package re-exports its client's names. This is a guard on
# what a module depends on, not on what Python loads -- importing any
# submodule runs its package's ``__init__`` regardless.
CONNECTION_MODULES = {
    "sievelib.managesieve",
    "mailctl.components.imap.client",
    "mailctl.components.managesieve.client",
}
CONNECTION_LIBRARIES = {"imapclient", "socket", "ssl"}


# ----------------------------------------------------------------------------
def building_names() -> set[str]:
    """Every public name the building modules define."""
    from mailctl.components.managesieve import emit, script

    return {
        name
        for module in (emit, script)
        for name in getattr(module, "__all__", vars(module))
        if not name.startswith("_")
    } - {"Placement", "DisplayDiff"}


# ----------------------------------------------------------------------------
def connection_names() -> set[str]:
    """Every public name the components' client modules define."""
    from mailctl.components.imap import client as imap_client
    from mailctl.components.managesieve import client as sieve_client

    return {
        name
        for module in (imap_client, sieve_client)
        for name in getattr(module, "__all__", vars(module))
        if not name.startswith("_")
    } | {"SieveClient"}


# ----------------------------------------------------------------------------
def module_file(module: str) -> Path | None:
    """The file a ``mailctl`` module name lives in, if it is one."""
    parts = module.split(".")

    if parts[0] != "mailctl":
        return None

    base = PACKAGE.joinpath(*parts[1:])

    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.exists():
            return candidate

    return None


# ----------------------------------------------------------------------------
def half_modules(cls: type) -> set[Path]:
    """The files one half of a provider is made of.

    The file defining ``cls``, and every file of the same provider package
    it imports, followed transitively. ``from . import records`` names a
    module, so an imported name that is a sibling file counts too.
    """
    import inspect

    start = Path(inspect.getfile(cls))
    package = package_of(start)
    seen: set[Path] = set()
    pending = [start]

    while pending:
        path = pending.pop()

        if path in seen:
            continue

        seen.add(path)

        for module, names in imports(path.read_text(), package_of(path)):
            candidates = [module] + [f"{module}.{name}" for name in names]

            for candidate in candidates:
                if candidate != package and not candidate.startswith(
                    f"{package}."
                ):
                    continue

                found = module_file(candidate)

                if found is not None and found.name != "__init__.py":
                    pending.append(found)

    return seen


# ----------------------------------------------------------------------------
def providers_halves() -> dict[str, tuple[set[Path], set[Path]]]:
    """Each registered provider's dialect files and transport files."""
    from mailctl.providers.registry import PROVIDERS

    return {
        name: (half_modules(p.dialect), half_modules(p.transport))
        for name, p in PROVIDERS.items()
    }


# ----------------------------------------------------------------------------
def transport_violations(source: str, package: str, dialect: set[str]):
    """Every import in a transport module of a building helper.

    ``dialect`` is the module names on the provider's dialect side.
    """
    names = building_names()

    return [
        f"{module} {sorted(taken)}".rstrip(" []")
        for module, taken in imports(source, package)
        if module in BUILDING_MODULES
        or module.startswith(RECHECK_PACKAGE)
        or module in dialect
        or any(f"{module}.{name}" in dialect for name in taken)
        or (module.startswith("mailctl.") and taken & names)
    ]


# ----------------------------------------------------------------------------
def dialect_violations(source: str, package: str, transport: set[str]):
    """Every import in a dialect module of something that connects.

    ``transport`` is the module names on the provider's transport side.
    """
    names = connection_names()

    return [
        f"{module} {sorted(taken)}".rstrip(" []")
        for module, taken in imports(source, package)
        if module in CONNECTION_MODULES
        or module.split(".")[0] in CONNECTION_LIBRARIES
        or module in transport
        or any(f"{module}.{name}" in transport for name in taken)
        or (module.startswith("mailctl.components") and taken & names)
    ]


# ----------------------------------------------------------------------------
def dotted(path: Path) -> str:
    """The module name of a file under the package."""
    relative = path.relative_to(PACKAGE.parent).with_suffix("")

    return ".".join(relative.parts)


# ----------------------------------------------------------------------------
def test_the_walk_finds_each_half_of_mxroute():
    """A derivation that found nothing would pass the guards vacuously."""
    dialect, transport = providers_halves()["mxroute"]
    names = {
        side: {path.relative_to(PACKAGE).as_posix() for path in files}
        for side, files in (("dialect", dialect), ("transport", transport))
    }

    assert {
        "providers/mxroute/dialect.py",
        "providers/mxroute/sieve.py",
    } <= names["dialect"]
    assert {
        "providers/mxroute/transport.py",
        "providers/mxroute/imap.py",
        "providers/mxroute/managesieve.py",
    } <= names["transport"]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("provider", sorted(providers_halves()))
def test_no_module_is_on_both_sides(provider):
    dialect, transport = providers_halves()[provider]

    assert dialect & transport == set()


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("provider", sorted(providers_halves()))
def test_a_transport_imports_no_building_helper(provider):
    dialect, transport = providers_halves()[provider]
    dialect_names = {dotted(path) for path in dialect}

    for path in sorted(transport):
        found = transport_violations(
            path.read_text(encoding="utf-8"), package_of(path), dialect_names
        )

        assert found == [], (
            f"{path.relative_to(PACKAGE)} imports {found}; a transport "
            f"stores what it is handed and builds nothing (ADR 0007)"
        )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("provider", sorted(providers_halves()))
def test_a_dialect_opens_no_connection(provider):
    dialect, transport = providers_halves()[provider]
    transport_names = {dotted(path) for path in transport}

    for path in sorted(dialect):
        found = dialect_violations(
            path.read_text(encoding="utf-8"), package_of(path), transport_names
        )

        assert found == [], (
            f"{path.relative_to(PACKAGE)} imports {found}; a dialect is "
            f"offline and opens no connection (ADR 0007)"
        )


MXROUTE_DIALECT = {
    "mailctl.providers.mxroute.dialect",
    "mailctl.providers.mxroute.sieve",
}
MXROUTE_TRANSPORT = {
    "mailctl.providers.mxroute.transport",
    "mailctl.providers.mxroute.imap",
    "mailctl.providers.mxroute.managesieve",
}


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source",
    [
        "from ...components.managesieve.script import merge_rule\n",
        "from ...components.managesieve import render_script\n",
        "from ...components.managesieve.emit import EMIT_TABLE\n",
        "from ...rules import read_rules\n",
        "from . import sieve\n",
        "from .dialect import MxrouteDialect\n",
        "from ...utilities.mail import recheck\n",
    ],
)
def test_the_transport_guard_would_catch_a_building_helper(source):
    assert transport_violations(
        source, "mailctl.providers.mxroute", MXROUTE_DIALECT
    ), source


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source",
    [
        "from ...components.imap.client import ImapSession\n",
        "from ...components.managesieve import SieveSession\n",
        "from ...components.imap import ImapSession\n",
        "from ...components.managesieve.client import SieveClient\n",
        "from imapclient import IMAPClient\n",
        "from sievelib.managesieve import Client\n",
        "import socket\n",
        "from .transport import MxrouteTransport\n",
        "from . import imap\n",
    ],
)
def test_the_dialect_guard_would_catch_a_connection(source):
    assert dialect_violations(
        source, "mailctl.providers.mxroute", MXROUTE_TRANSPORT
    ), source


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("guard", "source"),
    [
        ("transport", "from ...components.imap.client import ImapSession\n"),
        ("transport", "from . import records\n"),
        ("transport", "from ...criteria import Criteria\n"),
        (
            "dialect",
            "from ...components.managesieve.script import rule_names\n",
        ),
        (
            "dialect",
            "from ...components.imap.folders import normalize_folder\n",
        ),
        ("dialect", "from sievelib import factory\n"),
        ("dialect", "from ...components.managesieve import script\n"),
        ("dialect", "from . import sieve as mxroute_sieve\n"),
    ],
)
def test_the_half_guards_allow_what_each_half_may_use(guard, source):
    """Too wide a guard teaches people to ignore it."""
    package = "mailctl.providers.mxroute"

    if guard == "transport":
        assert transport_violations(source, package, MXROUTE_DIALECT) == []

    else:
        assert dialect_violations(source, package, MXROUTE_TRANSPORT) == []


# ############################################################################
# Interfaces call only utilities (ADR 0007)
# ############################################################################

# Interfaces are derived, not listed: every module outside the layers
# below them is one. The CLI is today's only front-end, and a tui.py, a
# web package, or any other front-end is held to this line the day it
# lands, whatever library it parses its input with. A new core module is
# the one that must be named here -- until it is, it is held to the
# interface's line, which is the stricter one.
CORE_MODULES = {"__init__.py", "config.py", "criteria.py", "rules.py"}
CORE_PACKAGES = {"components", "providers", "utilities"}
SESSION_MODULE = "engine.py"

INTERFACE_FILES = {
    path.relative_to(PACKAGE).with_suffix("").as_posix(): path
    for path in sorted(PACKAGE.rglob("*.py"))
    if path.relative_to(PACKAGE).parts[0]
    not in CORE_PACKAGES | CORE_MODULES | {SESSION_MODULE}
}

# What an interface may take from the session: opening one, and asking
# what the configured provider offers before it does. The session goes
# straight to the utilities; its transport, raw connection, and dialect
# are theirs alone, so no front-end reaches a write around the policy.
INTERFACE_ENGINE_NAMES = {
    "Session",
    "ProviderCapabilities",
    "capabilities_for",
    "connect",
}
SESSION_INTERNALS = {"transport", "connection", "dialect"}
BELOW_THE_SESSION = ("mailctl.providers", "mailctl.components")


# ----------------------------------------------------------------------------
def engine_names(source: str, package: str) -> set[str]:
    """Every name ``source`` takes from ``mailctl.engine``, however spelt.

    ``from .engine import x`` names it outright; ``from . import engine``
    or ``import mailctl.engine as e`` binds the module, and each attribute
    read off that binding is a name taken.
    """
    tree = ast.parse(source)
    taken: set[str] = set()
    bindings: set[str] = set()
    dotted_import = False

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            (module, names), *_ = imports(ast.unparse(node), package)

            if module == "mailctl.engine":
                taken |= names

            elif module == "mailctl":
                bindings |= {
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "engine"
                }

        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "mailctl.engine":
                    if alias.asname:
                        bindings.add(alias.asname)

                    else:
                        dotted_import = True

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue

        value = node.value
        bound = isinstance(value, ast.Name) and value.id in bindings
        spelt_out = (
            dotted_import
            and isinstance(value, ast.Attribute)
            and value.attr == "engine"
            and isinstance(value.value, ast.Name)
            and value.value.id == "mailctl"
        )

        if bound or spelt_out:
            taken.add(node.attr)

    return taken


# ----------------------------------------------------------------------------
def below_the_session(source: str, package: str) -> list[str]:
    """Every import of a provider or a component module, however spelt."""
    found = []

    for module, names in imports(source, package):
        for candidate in [module, *(f"{module}.{name}" for name in names)]:
            if any(
                candidate == layer or candidate.startswith(f"{layer}.")
                for layer in BELOW_THE_SESSION
            ):
                found.append(candidate)
                break

    return found


# ----------------------------------------------------------------------------
def session_internals(source: str) -> list[str]:
    """Every read of a session's transport, raw connection, or dialect.

    An AST cannot tell a session from anything else with such an
    attribute, so the names themselves are off limits in an interface.
    """
    return sorted(
        f"line {node.lineno}: .{node.attr}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute) and node.attr in SESSION_INTERNALS
    )


# ----------------------------------------------------------------------------
def test_the_walk_finds_the_interfaces_and_only_them():
    """Vacuous if it found nothing; wrong if it took in a core module."""
    assert "cli" in INTERFACE_FILES
    assert not any(
        name.split("/")[0] in CORE_PACKAGES or name == "engine"
        for name in INTERFACE_FILES
    )


# ----------------------------------------------------------------------------
def test_every_named_core_module_exists():
    """A stale name here would let nothing through, silently."""
    for name in CORE_MODULES | {SESSION_MODULE}:
        assert (PACKAGE / name).is_file(), name

    for name in CORE_PACKAGES:
        assert (PACKAGE / name / "__init__.py").is_file(), name


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("interface", sorted(INTERFACE_FILES))
def test_an_interface_takes_only_the_session_lifecycle_from_the_engine(
    interface,
):
    path = INTERFACE_FILES[interface]
    extra = engine_names(path.read_text(encoding="utf-8"), package_of(path))

    assert extra <= INTERFACE_ENGINE_NAMES, (
        f"{interface}.py uses mailctl.engine."
        f"{sorted(extra - INTERFACE_ENGINE_NAMES)}; an interface opens a "
        f"session and hands it to the utilities (ADR 0007)"
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("interface", sorted(INTERFACE_FILES))
def test_an_interface_imports_no_provider_and_no_component(interface):
    path = INTERFACE_FILES[interface]
    found = below_the_session(
        path.read_text(encoding="utf-8"), package_of(path)
    )

    assert found == [], (
        f"{interface}.py imports {found}; an interface reaches a provider "
        f"only through the utilities, and the neutral types through them "
        f"or the session (ADR 0007)"
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("interface", sorted(INTERFACE_FILES))
def test_an_interface_never_reaches_into_a_session(interface):
    path = INTERFACE_FILES[interface]
    found = session_internals(path.read_text(encoding="utf-8"))

    assert found == [], (
        f"{interface}.py reads {found}; a session's transport, connection, "
        f"and dialect are the utilities' -- an interface passes the session "
        f"to them, so no write bypasses the policy (ADR 0007)"
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("source", "name"),
    [
        (
            "from . import engine\nengine.check_settings(p, c)\n",
            "check_settings",
        ),
        ("from .engine import Session, _Guarded\n", "_Guarded"),
        ("from . import engine as e\ne.provider_for(c)\n", "provider_for"),
        ("import mailctl.engine\nmailctl.engine.Session.call\n", "Session"),
        (
            "import mailctl.engine\nmailctl.engine.check_settings\n",
            "check_settings",
        ),
        ("from mailctl import engine\nengine.MAIL\n", "MAIL"),
    ],
)
def test_the_engine_names_guard_sees_every_spelling(source, name):
    assert name in engine_names(source, "mailctl"), source


# ----------------------------------------------------------------------------
def test_the_engine_names_guard_allows_the_lifecycle():
    source = (
        "from . import engine\n"
        "with engine.connect(c) as s:\n    pass\n"
        "def f(o: engine.ProviderCapabilities):\n"
        "    return engine.capabilities_for(c)\n"
    )

    assert engine_names(source, "mailctl") <= INTERFACE_ENGINE_NAMES


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source",
    [
        "from .providers.base import Provider\n",
        "from .providers.model import ActionSpec\n",
        "from . import providers\n",
        "from .providers import registry\n",
        "import mailctl.providers.mxroute\n",
        "from .components.imap import ImapSession\n",
        "from . import components\n",
    ],
)
def test_the_interface_import_guard_would_catch_a_lower_layer(source):
    assert below_the_session(source, "mailctl"), source


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source",
    [
        "from . import engine, utilities\n",
        "from .utilities.rules import ActionSpec\n",
        "from .config import Config\n",
        "from .criteria import Criteria\n",
    ],
)
def test_the_interface_import_guard_allows_the_utilities(source):
    assert below_the_session(source, "mailctl") == [], source


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source",
    [
        "s.transport.store_rule_set(n, t)\n",
        "sessions.connection.apply_mail(plan)\n",
        "session.dialect.merge_rule(a, b)\n",
        "x = getattr(s, 'q').transport\n",
    ],
)
def test_the_session_guard_would_catch_a_reach_inside(source):
    assert session_internals(source), source


# ----------------------------------------------------------------------------
def test_the_session_guard_allows_asking_what_a_session_has():
    source = "if not sessions.has_mail:\n    utilities.mail.plan(sessions)\n"

    assert session_internals(source) == []


# ############################################################################
# Utilities reach a provider only through the session (ADR 0007)
# ############################################################################

# A utility's transport is the session's guarded one: it connects on first
# use, serialises calls, and re-sends a dropped read and never a write.
# The raw connection skips all of that, and a transport taken off a
# provider rather than a session is one nobody opened. So a utility reads
# ``.transport`` only off a parameter annotated as a ``Session``, and
# never reads ``.connection`` at all.


# ----------------------------------------------------------------------------
def transport_outside_a_session(source: str) -> list[str]:
    """Every ``.connection``, and every ``.transport`` not on a Session.

    A ``Session`` is a parameter whose annotation names one, as every
    utility taking a session declares it.
    """
    found = []

    for function in ast.walk(ast.parse(source)):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue

        arguments = function.args
        sessions = {
            arg.arg
            for arg in [
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
            ]
            if arg.annotation is not None
            and re.search(r"\bSession\b", ast.unparse(arg.annotation))
        }

        for node in ast.walk(function):
            if not isinstance(node, ast.Attribute):
                continue

            if node.attr == "connection" or (
                node.attr == "transport"
                and not (
                    isinstance(node.value, ast.Name)
                    and node.value.id in sessions
                )
            ):
                found.append(f"line {node.lineno}: .{node.attr}")

    tree = ast.parse(source)
    in_functions = {
        id(node)
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
        for node in ast.walk(function)
    }
    found.extend(
        f"line {node.lineno}: .{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in {"transport", "connection"}
        and id(node) not in in_functions
    )

    return sorted(set(found))


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("utility", sorted(UTILITY_FILES))
def test_a_utility_reaches_the_transport_only_through_a_session(utility):
    path = UTILITY_FILES[utility]
    found = transport_outside_a_session(path.read_text(encoding="utf-8"))

    assert found == [], (
        f"{utility}.py reads {found}; a utility uses the transport the "
        f"session guards, never the raw connection or a provider's own "
        f"(ADR 0007)"
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source",
    [
        "def f(session: Session):\n    session.connection.apply_mail(p)\n",
        "def f(config):\n    provider_for(config).transport.open(config)\n",
        "def f(session: Session):\n    session.provider.transport\n",
        "def f(provider: Provider):\n    provider.transport.open(c)\n",
        "def f(s):\n    s.transport.store_rule_set(n, t)\n",
        "TRANSPORT = registry.PROVIDERS['x'].transport\n",
    ],
)
def test_the_utility_guard_would_catch_a_transport_off_the_session(source):
    assert transport_outside_a_session(source), source


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source",
    [
        "def f(session: Session):\n    session.transport.list_folders()\n",
        "def f(live: 'Session | None'):\n    t = live.transport\n",
        "def f(config):\n    provider_for(config).dialect.refuse(a)\n",
    ],
)
def test_the_utility_guard_allows_the_session_transport(source):
    assert transport_outside_a_session(source) == [], source


# ############################################################################
# A read-only utility never reaches a write (#154)
# ############################################################################

# Read and write are kept apart at every layer. The transport interface
# classifies each operation read or write (#135), and the session reads
# that to decide what may be sent again. This guard carries the same line
# up into the utilities: every change is plan -> decide -> execute, so a
# utility that is not carrying out a plan must not store, create,
# subscribe, or move anything.
#
# THE RULE -- which utilities may write. A utility may reach a transport
# write only if it carries out a plan: its name begins ``execute_``, or it
# is one of the write steps an ``execute_`` function is built from
# (``WRITE_STEPS``). Every other function under ``mailctl/utilities/`` --
# every ``plan_*``, ``read_*``, ``list_*``, the probes, the search and view
# of messages, and every private helper -- is read-only. The read-only set
# is the complement of the writers over a walk of the package, never a
# list, so a utility added tomorrow is held read-only the day it lands, and
# has to be named, visibly, to be allowed to write.
#
# THE MECHANISM -- static, not dynamic. A dynamic check would drive each
# read-only utility over a fake transport that fails on a write, but every
# utility needs its own hand-built inputs, which turns a derived set back
# into a hand-written one, and it sees only the branch those inputs take:
# ``plan_folder`` reads the rule half only when there is one. The AST sees
# every branch of every function without running any of them.
#
# WHAT GOES RED. A reference to a write operation on the transport,
# however the utility holds it: straight off ``session.transport``, through
# a local assigned from a ``.transport``, or through a parameter that is a
# transport (annotated ``Transport``, or named ``transport``, as
# ``messages.newest_matches`` takes it). A reference, not only a call:
# handing ``session.transport.subscribe`` to something else to call is
# caught too. And a ``getattr`` whose name is a write operation's, on
# anything. Reach is followed through other utilities -- by bare name
# within a module, by ``from .module import name``, and by
# ``module.name`` after ``from . import module`` -- to a fixed point, so a
# plan that calls a helper that calls ``upload_script`` is as red as one
# that stores.
#
# Why the receiver is checked. A write's name is also an ordinary word:
# ``SubscriptionPlan`` and ``FolderPlan`` each carry a ``subscribe``
# field, and reading ``self.subscribe`` is not subscribing. Matching the
# name on any receiver reported ``SubscriptionPlan.changes`` as a write
# the first time this ran -- too wide, and a guard whose findings are not
# worth chasing stops being read.
#
# WHAT IT DOES NOT SEE. A transport kept somewhere other than a local or
# a parameter (a field of a record, an unpacked tuple) and then written
# through; a write op named by a computed string
# (``getattr(t, "store_" + x)``); and a write reached outside the
# utilities -- through a callback a front-end passes in, or through the
# dialect. The dialect is held to opening no connection by the half guard
# above, and a utility reaches a transport only as ``session.transport``
# by the guard before this one, so none of those is a route today.

WRITE_OPERATIONS = frozenset(
    name
    for name, operation in TRANSPORT_KINDS.items()
    if operation.kind == WRITE
)

# The write steps an ``execute_`` function is built from, which write on
# its behalf. Each must be seen to reach a write (a test below), so an
# entry cannot outlive the reason it is here.
WRITE_STEPS = frozenset(
    {
        "folders.create_folder",
        "folders.realize_folder",
        "scripts.upload_script",
    }
)

# The read-only utilities known by name. The derived set must hold every
# one of them, or the walk has gone narrow.
KNOWN_READERS = (
    "backup.plan_backup",
    "backup.plan_restore",
    "flags.plan_mark",
    "folders.list_folder_counts",
    "folder_rename.plan_folder_rename",
    "folder_rename.verify_folder_rename",
    "folders.list_folders",
    "folders.plan_folder",
    "folders.plan_folder_creation",
    "folders.plan_subscription",
    "mail.criteria_like",
    "mail.plan_mail",
    "messages.list_messages",
    "messages.read_message",
    "reports.probe_rules",
    "reports.probe_servers",
    "rules.plan_rule",
    "rules.read_rules",
    "scripts.read_script",
)


# ----------------------------------------------------------------------------
def is_writer(unit: str) -> bool:
    """Whether the rule lets ``unit`` (``module.function``) write."""
    return (
        unit.rsplit(".", 1)[-1].startswith("execute_") or unit in WRITE_STEPS
    )


# ----------------------------------------------------------------------------
def utility_units(
    sources: dict[str, str],
) -> dict[str, tuple[str, ast.AST]]:
    """Every function in ``sources`` (module name -> source), by name.

    A module-level function is ``module.function``; a method of a
    module-level class is ``module.Class.method``. A function nested inside
    one of those is part of it, since ``ast.walk`` descends into it.
    """
    units = {}

    for module, source in sources.items():
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                units[f"{module}.{node.name}"] = (module, node)

            elif isinstance(node, ast.ClassDef):
                units.update(
                    (f"{module}.{node.name}.{item.name}", (module, item))
                    for item in node.body
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                )

    return units


# ----------------------------------------------------------------------------
def utility_namespace(
    module: str, source: str, modules: set[str]
) -> tuple[dict[str, str], dict[str, str]]:
    """What a bare name means in ``module``: ``(functions, modules)``.

    ``functions`` maps a name to the utility it is -- one defined in the
    module, or taken from a sibling with ``from .x import name`` (or its
    absolute spelling). ``modules`` maps a name to the sibling utility
    module it is bound to by ``from . import x``.
    """
    tree = ast.parse(source)
    functions = {
        node.name: f"{module}.{node.name}"
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    bound = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue

        if node.level == 1:
            origin = node.module

        elif node.module and node.module.startswith("mailctl.utilities"):
            origin = node.module.removeprefix("mailctl.utilities").lstrip(".")
            origin = origin or None

        else:
            continue

        for alias in node.names:
            local = alias.asname or alias.name

            if origin is None and alias.name in modules:
                bound[local] = alias.name

            elif origin in modules:
                functions[local] = f"{origin}.{alias.name}"

    return functions, bound


# ----------------------------------------------------------------------------
def transport_names(function: ast.AST) -> set[str]:
    """The names a function holds a transport under.

    A parameter annotated ``Transport`` or named ``transport``, and a name
    assigned from an expression ending in ``.transport``.
    """
    names = set()
    arguments = function.args  # type: ignore[attr-defined]

    for arg in [
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
    ]:
        if arg.arg == "transport" or (
            arg.annotation is not None
            and re.search(r"\bTransport\b", ast.unparse(arg.annotation))
        ):
            names.add(arg.arg)

    for node in ast.walk(function):
        if isinstance(node, ast.Assign | ast.AnnAssign):
            value = node.value
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )

            if isinstance(value, ast.Attribute) and value.attr == "transport":
                names.update(
                    target.id
                    for target in targets
                    if isinstance(target, ast.Name)
                )

    return names


# ----------------------------------------------------------------------------
def write_reach(sources: dict[str, str]) -> dict[str, set[str]]:
    """The transport writes each utility reaches, directly or through
    another utility, over ``sources`` (module name -> source)."""
    units = utility_units(sources)
    modules = set(sources)
    namespaces = {
        module: utility_namespace(module, source, modules)
        for module, source in sources.items()
    }
    direct: dict[str, set[str]] = {}
    calls: dict[str, set[str]] = {}

    for unit, (module, function) in units.items():
        functions, bound = namespaces[module]
        direct[unit], calls[unit] = set(), set()

        transports = transport_names(function)

        for node in ast.walk(function):
            if isinstance(node, ast.Attribute):
                owner = node.value

                if isinstance(owner, ast.Name) and owner.id in bound:
                    calls[unit].add(f"{bound[owner.id]}.{node.attr}")

                elif node.attr in WRITE_OPERATIONS and (
                    (
                        isinstance(owner, ast.Attribute)
                        and owner.attr == "transport"
                    )
                    or (isinstance(owner, ast.Name) and owner.id in transports)
                ):
                    direct[unit].add(node.attr)

            elif isinstance(node, ast.Name) and node.id in functions:
                calls[unit].add(functions[node.id])

            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in WRITE_OPERATIONS
            ):
                direct[unit].add(node.args[1].value)

    reach = {unit: set(found) for unit, found in direct.items()}
    changed = True

    while changed:
        changed = False

        for unit, callees in calls.items():
            for callee in callees & reach.keys():
                if not reach[callee] <= reach[unit]:
                    reach[unit] |= reach[callee]
                    changed = True

    return reach


# ----------------------------------------------------------------------------
def read_write_violations(sources: dict[str, str]) -> list[str]:
    """Every read-only utility in ``sources`` that reaches a write."""
    return [
        f"{unit} reaches {sorted(found)}"
        for unit, found in sorted(write_reach(sources).items())
        if found and not is_writer(unit)
    ]


UTILITY_SOURCES = {
    path.stem: path.read_text(encoding="utf-8")
    for path in sorted((PACKAGE / "utilities").glob("*.py"))
}

UTILITY_REACH = write_reach(UTILITY_SOURCES)

READ_ONLY_UTILITIES = sorted(
    unit for unit in UTILITY_REACH if not is_writer(unit)
)


# ----------------------------------------------------------------------------
def test_the_write_operations_come_from_the_classification():
    """Derived from the interface: a write added to it is guarded here
    without an edit, and a read is never mistaken for one."""
    assert "store_rule_set" in WRITE_OPERATIONS
    assert "apply_mail" in WRITE_OPERATIONS
    assert {"add_flags", "remove_flags"} <= WRITE_OPERATIONS
    assert "read_rule_set" not in WRITE_OPERATIONS
    assert "check_rule_set" not in WRITE_OPERATIONS


# ----------------------------------------------------------------------------
def test_the_derived_read_only_set_holds_the_known_readers():
    """An empty or narrowed set would pass the guard below vacuously."""
    assert len(READ_ONLY_UTILITIES) > len(KNOWN_READERS)
    assert set(KNOWN_READERS) <= set(READ_ONLY_UTILITIES)


# ----------------------------------------------------------------------------
def test_no_reading_name_is_allowed_to_write():
    """The exemptions cannot swallow a utility the convention calls a
    read: a ``plan_``, ``read_``, or ``list_`` name is never a writer."""
    assert not [
        unit
        for unit in UTILITY_REACH
        if is_writer(unit)
        and unit.rsplit(".", 1)[-1].startswith(("plan_", "read_", "list_"))
    ]


# ----------------------------------------------------------------------------
def test_the_analysis_sees_the_writes_that_exist():
    """The walk sees a direct write, and one reached two utilities away;
    were it blind to either, every read-only utility would pass."""
    assert "store_rule_set" in UTILITY_REACH["scripts.upload_script"]
    assert "apply_mail" in UTILITY_REACH["mail.execute_mail"]
    assert "store_rule_set" in UTILITY_REACH["backup.execute_restore"]
    assert "create_folder" in UTILITY_REACH["rules.execute_script_change"]
    assert UTILITY_REACH["flags.execute_mark"] == {
        "add_flags",
        "remove_flags",
    }


# ----------------------------------------------------------------------------
def test_the_analysis_sees_a_rename_s_three_writes():
    """RENAME and the subscription fixes are made in a nested function
    handed to the upload as its before-put step; the script's store is
    reached through ``upload_script``."""
    assert {
        "rename_folder",
        "subscribe",
        "unsubscribe",
        "store_rule_set",
    } <= UTILITY_REACH["folder_rename.execute_folder_rename"]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("unit", sorted(WRITE_STEPS))
def test_every_write_step_still_writes(unit):
    """An exemption whose utility no longer writes, or no longer exists,
    is one somebody could hide a plan behind."""
    assert UTILITY_REACH.get(unit), f"{unit} is exempt and reaches no write"


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("unit", READ_ONLY_UTILITIES)
def test_a_read_only_utility_never_reaches_a_write(unit):
    assert UTILITY_REACH[unit] == set(), (
        f"{unit} reaches the transport writes {sorted(UTILITY_REACH[unit])}; "
        f"only an execute_ utility or a named write step may write (#154)"
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sources",
    [
        # Straight off the session.
        {
            "a": "def plan_x(session):\n"
            "    session.transport.store_rule_set(n, s)\n"
        },
        # Through a local, as most utilities hold it.
        {
            "a": "def plan_x(session):\n"
            "    t = session.transport\n"
            "    t.apply_mail(p)\n"
        },
        # Through a parameter, as newest_matches takes it.
        {"a": "def read_x(transport):\n    transport.subscribe(f)\n"},
        # Handed on to be called by somebody else.
        {
            "a": "def plan_x(session):\n"
            "    run(session.transport.create_folder)\n"
        },
        # Named by a constant.
        {"a": "def plan_x(t):\n    getattr(t, 'activate_rule_set')(n)\n"},
        # Through a private helper in the same module.
        {
            "a": "def _store(session):\n"
            "    session.transport.store_rule_set(n, s)\n"
            "def plan_x(session):\n    _store(session)\n"
        },
        # Through a write step taken from a sibling module.
        {
            "scripts": "def upload_script(s):\n"
            "    s.transport.store_rule_set(n, t)\n",
            "rules": "from .scripts import upload_script\n"
            "def plan_rule(s):\n    upload_script(s)\n",
        },
        # The same, spelled absolute.
        {
            "scripts": "def upload_script(s):\n"
            "    s.transport.store_rule_set(n, t)\n",
            "rules": "from mailctl.utilities.scripts import upload_script\n"
            "def plan_rule(s):\n    upload_script(s)\n",
        },
        # Through a sibling module bound by name.
        {
            "folders": "def create_folder(s, p):\n"
            "    s.transport.create_folder(f)\n",
            "mail": "from . import folders\n"
            "def plan_mail(s):\n    folders.create_folder(s, p)\n",
        },
        # Three utilities deep, and round a cycle.
        {
            "a": "def execute_x(s):\n    s.transport.apply_mail(p)\n"
            "def _b(s):\n    _c(s)\n    execute_x(s)\n"
            "def _c(s):\n    _b(s)\n"
            "def plan_x(s):\n    _c(s)\n"
        },
        # In a method of a class.
        {
            "a": "class P:\n"
            "    def feed(self):\n"
            "        self.session.transport.unsubscribe(f)\n"
        },
    ],
)
def test_the_read_write_guard_would_catch_a_write(sources):
    """The public reader in each case is the one caught -- not only a
    helper it calls -- so reach really is followed to the reader."""
    caught = {
        found.split(" ")[0].rsplit(".", 1)[-1]
        for found in read_write_violations(sources)
    }

    assert caught & {"plan_x", "read_x", "plan_rule", "plan_mail", "feed"}, (
        sources
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sources",
    [
        # Reads, however held.
        {
            "a": "def plan_x(session):\n"
            "    t = session.transport\n"
            "    t.read_rule_set(t.active_rule_set())\n"
            "    t.check_rule_set(s)\n"
        },
        # An execute_ utility writing is what it is for.
        {
            "a": "def execute_x(session):\n"
            "    session.transport.apply_mail(p)\n"
        },
        # A named write step, and the execute_ utility built on it.
        {
            "scripts": "def upload_script(s):\n"
            "    s.transport.store_rule_set(n, t)\n"
            "def execute_up(s):\n    upload_script(s)\n",
        },
        # A record's own field that shares a write's name.
        {
            "a": "class P:\n"
            "    def changes(self):\n        return self.subscribe\n"
        },
        {
            "a": "def plan_x(plan):\n"
            "    return plan.subscribe and plan.folder\n"
        },
        # A read-only utility that only names a write in prose.
        {"a": 'def plan_x(s):\n    """Never calls store_rule_set."""\n'},
    ],
)
def test_the_read_write_guard_allows_reads_and_execute_steps(sources):
    assert read_write_violations(sources) == [], sources
