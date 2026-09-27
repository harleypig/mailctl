"""Layer 1 knows its protocol and nothing about any host (ADR 0006).

A module under ``mailctl/components/`` may import the standard library, the
protocol library its own component wraps, other ``mailctl.components``
modules, and ``MailctlError`` -- and nothing else. Never the engine, the
CLI, mailctl's ``Config``, or a provider: each of those would tie a
protocol library to one host or one front-end, which is the mix the two
layers exist to undo.

The wrapped library is per component: ``imap`` importing ``sievelib`` would
tie the Gmail provider, which has no Sieve, to a library it never uses.

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
