"""What a user sees is the same for every provider (#219).

A provider's own words -- its name, the rule language it speaks, the
protocols it speaks it over, the webmail that writes the same rules --
reach the user in three places only:

* the server reports, ``server test``, ``server probe``, and the
  baselines, whose job is to describe the servers;
* the help of an option only one provider has: ``mail search --raw``,
  ``--disable-extension``, and the connection settings the provider
  declares (``--host``, ``--imap-*``, ``--sieve-*``);
* documentation about the provider, which is not the CLI's.

Everywhere else is generic, and the thing a filter set holds is a
*filter*, never a *rule*: code names such as ``Rule`` stay, and user text
does not use them. ``test_providers.py`` holds a second provider
to that already; this holds **mxroute**, the provider whose words these
are, which is the case that can fail.
"""

import argparse
import json
import re
from pathlib import Path

import pytest
from cli_support import command_parsers, group_paths, subcommands

from mailctl import cli
from mailctl.providers import registry

# What gives a provider away. Case matters, and so do word edges: the
# default script's name, ``managesieve``, a backup's ``.sieve`` suffix, and
# an extension such as ``imap4flags`` are content, not words we chose, and
# ``mxroute`` is how --provider names the default.
PROVIDER_WORDS = re.compile(
    r"Sieve|IMAP|MXroute|MXRoute|Roundcube|CHECKSCRIPT|fileinto|Exim"
    r"|DirectAdmin|\bscripts?\b|(?<![.\w])(?:sieve|imap)\b"
)

# The commands whose job is to describe the servers.
REPORTS = "server"

# Options only one provider has, whose help is about that provider:
# (command, dest), with None for every command the option is on. The
# connection settings are not listed; they are read off the provider.
# 'mail search --raw' is provider-only too, and would join the list the
# day its help names the host's search language; it names none today.
PROVIDER_OPTIONS = {(None, "disable_extension")}

# A command's interface inside prose -- an option such as --script or
# --imap-host, or a metavar such as IMAP_HOST -- is a name the user types,
# not a word we chose, so it is taken out before the words are looked for.
INTERFACE = re.compile(r"--[\w-]+|\b[A-Z]+(?:_[A-Z]+)+\b")

# The user-facing noun is "filter" (operator, 2026-09-29, #219). A quoted
# name -- 'Unnamed rule 1', which the library names a rule with no name --
# is the filter set's content, so quoted text is taken out first.
RULE_WORD = re.compile(r"\b[Rr]ules?\b")
QUOTED = re.compile(r"'[^']*'")

SNAPSHOTS = Path(__file__).parent / "snapshots" / "cli"


# ----------------------------------------------------------------------------
def user_words(text: str) -> list[str]:
    """The provider words, and the word "rule", in text we wrote, less the
    command's interface."""
    text = INTERFACE.sub("", text)

    return [
        *PROVIDER_WORDS.findall(text),
        *RULE_WORD.findall(QUOTED.sub("", text)),
    ]


# ----------------------------------------------------------------------------
def mxroute_parser() -> argparse.ArgumentParser:
    offer = registry.PROVIDERS["mxroute"]

    return cli.build_parser(offer.capabilities, offer.wording)


# ----------------------------------------------------------------------------
def pages(
    parser: argparse.ArgumentParser,
) -> dict[str, argparse.ArgumentParser]:
    """Every help page the parser has, by path: the top, each group, and
    each command, read off the parser rather than listed."""
    found = {"": parser}
    found.update(command_parsers(parser))

    for path in group_paths(parser):
        sub = parser

        for word in path.split():
            sub = subcommands(sub)[word]

        found[path] = sub

    return found


# ----------------------------------------------------------------------------
def prose(parser: argparse.ArgumentParser, path: str, allowed: set):
    """Every piece of text a help page shows that we wrote, as (where,
    text): the description and epilog, group titles, each option's help
    and each subcommand's one line. Option names and metavars are the
    command's interface, not prose, so they are left out."""
    formatter = parser._get_formatter()

    yield "description", parser.description or ""
    yield "epilog", parser.epilog or ""

    for group in parser._action_groups:
        yield f"group {group.title}", group.description or ""

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for choice in action._choices_actions:
                yield f"command {choice.dest}", choice.help or ""

        elif action.help not in (None, argparse.SUPPRESS):
            if (path, action.dest) in allowed or (
                None,
                action.dest,
            ) in allowed:
                continue

            yield f"option {action.dest}", formatter._expand_help(action)


# ----------------------------------------------------------------------------
def provider_words_in_help(
    parser: argparse.ArgumentParser, allowed: set, reports: str | None
) -> dict[str, list[str]]:
    """Each help page, outside ``reports``, where a provider word appears
    in text outside ``allowed``, with the words found."""
    found = {}

    for path, page in pages(parser).items():
        if reports and (path == reports or path.startswith(f"{reports} ")):
            continue

        hits = sorted(
            {
                f"{where}: {word}"
                for where, text in prose(page, path, allowed)
                for word in user_words(text)
            }
        )

        if hits:
            found[path or "(top)"] = hits

    return found


# ----------------------------------------------------------------------------
def allowances() -> set:
    offer = registry.PROVIDERS["mxroute"].capabilities

    return PROVIDER_OPTIONS | {(None, name) for name in offer.settings}


# ############################################################################
# Help
# ############################################################################


# ----------------------------------------------------------------------------
def test_mxroutes_help_says_nothing_about_mxroute_outside_its_places():
    """Every help page under mxroute but the server reports' is generic,
    and so is every option's help but the provider-only ones."""
    assert (
        provider_words_in_help(mxroute_parser(), allowances(), REPORTS) == {}
    )


# ----------------------------------------------------------------------------
def test_the_help_walk_reaches_every_page():
    """Vacuous if it read nothing; too narrow if it missed a level."""
    found = pages(mxroute_parser())

    assert {"", "filter", "server baseline", "filter add"} <= set(found)
    assert "server baseline check" in found
    assert len(found) > 30


# ----------------------------------------------------------------------------
def test_the_help_check_sees_the_places_it_allows():
    """Pointed at mxroute with nothing allowed, the check finds the words
    it lets through: the reports, the provider-only options, and the
    connection settings. So the allowance is what passes them, not a walk
    that never reached them."""
    found = provider_words_in_help(mxroute_parser(), set(), None)

    assert "server probe" in found
    assert any(
        "option disable_extension" in hit for hit in found["filter add"]
    )
    assert "option host: MXRoute" in found["filter add"]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "plant",
    [
        pytest.param(
            lambda parser: setattr(
                command_parsers(parser)["filter add"],
                "description",
                "Save a rule into the active Sieve script.",
            ),
            id="description",
        ),
        pytest.param(
            lambda parser: next(
                action
                for action in command_parsers(parser)["filter list"]._actions
                if action.dest == "json"
            ).__setattr__("help", "print the scripts"),
            id="option",
        ),
        pytest.param(
            lambda parser: setattr(
                subcommands(parser)["filterset"]
                ._actions[-1]
                ._choices_actions[0],
                "help",
                "list IMAP folders",
            ),
            id="command-line",
        ),
        pytest.param(
            lambda parser: setattr(
                command_parsers(parser)["filter remove"],
                "description",
                "Take a named rule out of the active filter set.",
            ),
            id="rule",
        ),
    ],
)
def test_the_help_check_catches_a_planted_provider_word(plant):
    parser = mxroute_parser()
    plant(parser)

    assert provider_words_in_help(parser, allowances(), REPORTS) != {}


# ############################################################################
# Output, through the snapshots
# ############################################################################

# Shown by a snapshot but not text of ours, so not held to generic words:
# a diff's body and a filter set's source are the provider's own language,
# --verbose's lines are the exchange with the server, word for word, and an
# alert or a warning is the server's own text.
CONTENT = (
    (re.compile(r"^--- diff ---$"), re.compile(r"^--- end diff ---$")),
    (re.compile(r"^--- filter set .* ---$"), re.compile(r"^--- \d+ rule")),
)
PROTOCOL_LOG = re.compile(
    r"^\[(mail|filter)\] |^mailctl: (alert|warning) from the \w+ server: "
)

# Snapshots about a provider-only function, which name it as the provider
# does, by what the command was given. Each is checked to still need it.
PROVIDER_FUNCTIONS = {
    # The extensions capability and its --disable-extension setting.
    "extension": re.compile(r"extension"),
    # Actions MXroute refuses, or mailctl does not write for it.
    "--redirect": re.compile(r"'redirect' action"),
    "--vacation": re.compile(r"'vacation' action"),
}


# ----------------------------------------------------------------------------
def shown_text(snapshot: str) -> tuple[list[str], list[str]]:
    """A snapshot's command line, and the lines it printed that are ours:
    stdout and stderr, less content, the protocol log, and any JSON
    document but its error message."""
    lines = snapshot.splitlines()
    argv = lines[0].removeprefix("$ ").split()
    sections: dict[str, list[str]] = {}
    current = None

    for line in lines[1:]:
        if line in ("--- stdout", "--- stderr"):
            current = sections.setdefault(line, [])

        elif re.fullmatch(r"--- \w+ calls", line):
            current = None

        elif current is not None:
            current.append(line)

    shown = []

    for body in sections.values():
        text = "\n".join(body).strip()

        if "--json" in argv and text.startswith("{"):
            with_errors = [json.loads(doc) for doc in _documents(text)]
            shown += [
                doc["error"]["message"]
                for doc in with_errors
                if "error" in doc
            ]

            continue

        closing = None

        for line in body:
            if closing is not None:
                closing = None if closing.match(line) else closing

                continue

            opened = [end for start, end in CONTENT if start.match(line)]

            if opened:
                closing = opened[0]

            elif not PROTOCOL_LOG.match(line):
                shown.append(line)

    return argv, shown


# ----------------------------------------------------------------------------
def _documents(text: str) -> list[str]:
    """One pretty-printed document, or one per line."""
    try:
        json.loads(text)

    except json.JSONDecodeError:
        return [line for line in text.splitlines() if line.strip()]

    return [text]


# ----------------------------------------------------------------------------
def generic_output(name: str) -> bool:
    """Whether a snapshot's command is held to generic words: not a server
    report, and not a help page (the help check above has those)."""
    argv, _ = shown_text((SNAPSHOTS / f"{name}.txt").read_text())

    return (
        argv[1:2] != [REPORTS]
        and argv[1:2] != ["help"]
        and "--help" not in argv
    )


# ----------------------------------------------------------------------------
def provider_words_in_output(snapshot: str) -> list[str]:
    argv, shown = shown_text(snapshot)
    functions = [
        pattern
        for given, pattern in PROVIDER_FUNCTIONS.items()
        if given in argv or given == "extension"
    ]

    return [
        line
        for line in shown
        if user_words(line)
        and not any(pattern.search(line) for pattern in functions)
    ]


SNAPSHOT_NAMES = sorted(path.stem for path in SNAPSHOTS.glob("*.txt"))


# ----------------------------------------------------------------------------
def test_the_snapshot_walk_reads_every_snapshot():
    """The set is the directory's, not a list; and most are held to it."""
    held = [name for name in SNAPSHOT_NAMES if generic_output(name)]

    assert len(SNAPSHOT_NAMES) > 300
    assert {"add-dry-sievecreate", "rules", "show", "restore-yes"} <= set(held)
    assert "test" not in held


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name", [name for name in SNAPSHOT_NAMES if generic_output(name)]
)
def test_what_a_command_prints_says_nothing_about_mxroute(name):
    """Every command's output under mxroute, but the server reports', is
    generic outside the provider's own content (#219)."""
    snapshot = (SNAPSHOTS / f"{name}.txt").read_text(encoding="utf-8")

    assert provider_words_in_output(snapshot) == []


# ----------------------------------------------------------------------------
def test_the_output_check_sees_mxroutes_content_and_reports():
    """Pointed at what it leaves out, the check finds provider words: a
    server report, and a diff once its markers are gone. So it is the
    exclusion that passes them."""
    report = (SNAPSHOTS / "test.txt").read_text(encoding="utf-8")
    diff = (SNAPSHOTS / "add-dry-sievecreate.txt").read_text(encoding="utf-8")

    assert provider_words_in_output(report)
    assert provider_words_in_output(diff.replace("--- diff ---", "-- d --"))


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "line",
    [
        "Rule 'x' on script 'managesieve':",
        "  then:  fileinto INBOX.Lists; stop",
        "--- sieve diff ---",
        "Created IMAP folder 'INBOX.X' and subscribed to it",
        "mailctl: the server rejected the generated script (CHECKSCRIPT)",
        "Rule 'x' in filter set 'managesieve':",
        "2 rule(s), in evaluation order:",
        "mailctl: no rule named 'x' in the active filter set.",
    ],
)
def test_the_output_check_catches_the_old_wording(line):
    """Each line this repo printed before #219, planted in a snapshot the
    check passes, turns it red."""
    snapshot = (SNAPSHOTS / "add-dry-imapcreate.txt").read_text(
        encoding="utf-8"
    )
    planted = snapshot.replace("--- stderr\n", f"{line}\n--- stderr\n", 1)

    assert provider_words_in_output(planted) != []


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("given", sorted(PROVIDER_FUNCTIONS))
def test_each_provider_function_exception_is_still_needed(given):
    """An exception no snapshot needs any more is one that lets a word
    through for nothing: each must match a line some held snapshot
    prints."""
    pattern = PROVIDER_FUNCTIONS[given]
    matched = []

    for name in SNAPSHOT_NAMES:
        if not generic_output(name):
            continue

        argv, shown = shown_text((SNAPSHOTS / f"{name}.txt").read_text())

        if given in argv or given == "extension":
            matched += [
                line
                for line in shown
                if pattern.search(line)
                and PROVIDER_WORDS.search(INTERFACE.sub("", line))
            ]

    assert matched
