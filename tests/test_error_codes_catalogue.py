"""``docs/ERROR-CODES.md`` lists every code the package raises, and no other.

From v1.0.0 a code is never renamed, removed, or reused (RELEASING.md),
and the catalogue is where a script or an API client learns the codes. It
is written by hand, so a code added without a row, or a row left behind
by a code taken out, would go unnoticed (#375). This test derives the
codes from the source and compares the two sets both ways.

**The set is derived from every module of the package, the interfaces
included**, because a refusal the CLI or the API makes itself reaches a
script or a client as surely as the core's. ``cli.ERROR_TEXT`` is not the
source: it covers the core's codes only, and the API's own --
``plan_unknown``, ``password_declined``, ``prompt_input_full`` -- have no
rendering there.

**What the walk reads as a code**, every one of them a string literal it
can see in the source:

* ``code="..."`` passed to any call;
* ``code=NAME``, where ``NAME`` is a module-level string constant
  (``cli.ARGUMENTS``, the only spelling of ``invalid_arguments``);
* a parameter named ``code`` whose default is either of those
  (``Parser.refuse``, ``json_output.rejection``).

**What it cannot read, and does not pretend to.** A code passed on through
``**`` or a variable, such as a helper forwarding the ``code`` it was
given, is taken on trust: its caller's literal is what the walk sees. A
code handed back positionally, as ``json_output.rejection`` returns
``"setting_invalid"`` in a tuple, is not read either. Every such code is
raised literally somewhere else today, so none escapes; a code that
reached the user *only* that way would. The guard against that is the
reverse check: a catalogue row whose code the walk cannot find fails
here, so a code documented but reached only through a blind spot shows
up as a failure to investigate, never as a silent pass.
"""

import ast
import re
from pathlib import Path

import mailctl

PACKAGE = Path(mailctl.__file__).parent

CATALOGUE = PACKAGE.parent / "docs" / "ERROR-CODES.md"

# Every module of the package, walked rather than listed, so a module
# added later is read the day it lands.
ALL_MODULES = tuple(sorted(PACKAGE.rglob("*.py")))

# A catalogue row: a table line whose first cell is one backticked code.
# A row the pattern does not take is counted separately below, so a row
# spelt another way is a failure rather than a code silently not read.
ROW = re.compile(r"^\| `([a-z][a-z0-9_]*)` \|", re.MULTILINE)
ANY_ROW = re.compile(r"^\| `", re.MULTILINE)

# ############################################################################
# Helpers
# ############################################################################


# ----------------------------------------------------------------------------
def string_constants(tree: ast.Module) -> dict[str, str]:
    """Every module-level ``NAME = "text"``, so a code spelt through a
    constant is read as the code it names."""
    constants = {}

    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value

        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value

        else:
            continue

        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for target in targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = value.value

    return constants


# ----------------------------------------------------------------------------
def raised_codes(tree: ast.Module) -> set[str]:
    """Every code ``tree`` passes as ``code=`` or gives a ``code``
    parameter as its default, a literal or a module-level constant."""
    constants = string_constants(tree)

    def literal(node: ast.expr | None) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value

        if isinstance(node, ast.Name):
            return constants.get(node.id)

        return None

    found: list[str | None] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            found += [
                literal(keyword.value)
                for keyword in node.keywords
                if keyword.arg == "code"
            ]

        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            arguments = node.args
            positional = [*arguments.posonlyargs, *arguments.args]

            # defaults line up with the last positional parameters
            paired = list(
                zip(
                    positional[len(positional) - len(arguments.defaults) :],
                    arguments.defaults,
                    strict=True,
                )
            )
            paired += list(
                zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True)
            )

            found += [
                literal(default)
                for parameter, default in paired
                if parameter.arg == "code"
            ]

    return {code for code in found if code is not None}


# ----------------------------------------------------------------------------
def package_codes() -> set[str]:
    """Every code the package raises, by the walk above."""
    return set().union(
        *(
            raised_codes(ast.parse(path.read_text(encoding="utf-8")))
            for path in ALL_MODULES
        )
    )


# ----------------------------------------------------------------------------
def catalogued(text: str) -> list[str]:
    """Every code the catalogue lists, in order, repeats included."""
    return ROW.findall(text)


# ----------------------------------------------------------------------------
def drift(raised: set[str], listed: list[str]) -> dict[str, list[str]]:
    """What is out of step between the codes raised and those listed;
    empty lists where nothing is."""
    return {
        "raised, not catalogued": sorted(raised - set(listed)),
        "catalogued, not raised": sorted(set(listed) - raised),
        "catalogued twice": sorted(
            {code for code in listed if listed.count(code) > 1}
        ),
    }


IN_STEP = {
    "raised, not catalogued": [],
    "catalogued, not raised": [],
    "catalogued twice": [],
}

# ############################################################################
# The guard
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_catalogue_lists_every_code_raised_and_no_other():
    """A code with no row is one a script cannot look up; a row with no
    code is a promise about a refusal nothing makes."""
    raised = package_codes()
    text = CATALOGUE.read_text(encoding="utf-8")
    listed = catalogued(text)

    # Known positives, one per way in: a literal in the core, the API's
    # own (no ERROR_TEXT entry), a literal in the CLI, and the code only a
    # constant and a default spell. Without them an empty walk would pass
    # against an empty parse.
    assert {
        "max_messages",
        "plan_unknown",
        "gone_command",
        "invalid_arguments",
    } <= raised

    # The first table's first row and the last table's last row, so the
    # parse is seen to reach the whole page; and every table row read.
    assert listed[0] == "invalid_arguments"
    assert listed[-1] == "internal_error"
    assert len(listed) == len(ANY_ROW.findall(text))

    assert drift(raised, listed) == IN_STEP


# ----------------------------------------------------------------------------
def test_a_planted_raised_code_with_no_row_is_caught():
    """Red when the package raises a code the catalogue lacks."""
    planted = raised_codes(
        ast.parse("raise MailctlError('x', code='planted_raised')\n")
    )
    listed = catalogued(CATALOGUE.read_text(encoding="utf-8"))

    assert drift(package_codes() | planted, listed) == {
        **IN_STEP,
        "raised, not catalogued": ["planted_raised"],
    }


# ----------------------------------------------------------------------------
def test_a_planted_row_with_no_code_is_caught():
    """Red when the catalogue names a code nothing raises, and when it
    names one twice."""
    text = CATALOGUE.read_text(encoding="utf-8")
    planted = (
        f"{text}\n| Code | Means |\n|------|-------|\n"
        f"| `planted_listed` | nothing raises it |\n"
        f"| `max_messages` | listed a second time |\n"
    )

    assert drift(package_codes(), catalogued(planted)) == {
        **IN_STEP,
        "catalogued, not raised": ["planted_listed"],
        "catalogued twice": ["max_messages"],
    }


# ----------------------------------------------------------------------------
def test_the_walk_reads_each_spelling_of_a_code():
    """Each shape the walk is for is read, and each it is not is left:
    a variable, ``**``, a ``code`` key in another argument, a ``None``
    default, and a parameter of another name."""
    tree = ast.parse(
        "ARGS = 'by_constant'\n"
        "OTHER: str = 'by_annotated_constant'\n"
        "raise MailctlError('x', code='by_literal')\n"
        "raise MailctlError('x', code=ARGS)\n"
        "raise MailctlError('x', code=OTHER)\n"
        "def refuse(message, code='by_default'): ...\n"
        "def later(message, code=ARGS, *, other='no'): ...\n"
        "def keyword(*, code='by_keyword_default'): ...\n"
        "async def waits(code='by_async_default'): ...\n"
        "def none(code=None): ...\n"
        "def named(reason='not_a_code'): ...\n"
        "raise MailctlError('x', code=variable)\n"
        "raise MailctlError('x', **passed_on)\n"
        "raise MailctlError('x', fields={'code': 'in_fields'})\n"
    )

    assert raised_codes(tree) == {
        "by_literal",
        "by_constant",
        "by_annotated_constant",
        "by_default",
        "by_keyword_default",
        "by_async_default",
    }
