"""Offline Sieve script handling: parse, merge, move, remove, render, diff.

Nothing here touches the network. A script comes in as text, is parsed
into a ``sievelib`` filter set, edited, and rendered back -- and every rule
the edit did not name survives it (mailctl ADR 0002: merge, never
overwrite).

How a rule's *name* is written in a script is not part of Sieve. sievelib
reads and writes ``# Filter: NAME``; a webmail may use another form. So the
functions that parse or render take a :class:`NameDialect`, and the default
is sievelib's own. A host whose scripts use another form supplies its own
dialect; this module knows none of them.
"""

import difflib
import io
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, TextIO, cast

from sievelib import commands, factory, parser

from ... import MailctlError

__all__ = [
    "FILTERSET_NAME",
    "PLACE_AFTER",
    "PLACE_BEFORE",
    "PLACE_FIRST",
    "PLACE_LAST",
    "SIEVELIB_DIALECT",
    "SIEVELIB_NAME_MARKER",
    "UNIMPLEMENTED_ACTIONS",
    "DisplayDiff",
    "NameDialect",
    "Placement",
    "disable_rule",
    "display_diff",
    "enable_rule",
    "merge_rule",
    "move_rule",
    "parse_script",
    "remove_rule",
    "render_script",
    "resolve_position",
    "rewrite_hash_comments",
    "rule_names",
    "script_diff",
]

# NOT refused because a host disables them -- mailctl simply does not
# generate them. No host *documentation* says anything either way, so
# nothing here should claim they are unavailable.
#
# A live read on one account (2026-08-14) found `vacation` ADVERTISED and
# `enotify` absent, which makes these two refusals different in kind even
# though they share a message: declining to emit an action the server would
# accept is a choice of ours, where declining one it never advertised is
# not. Neither is a documented host restriction, which is the thing the
# message must not imply. What a given server supports is a question its
# CAPABILITY response answers; run 'mailctl test'.
UNIMPLEMENTED_ACTIONS = {
    "notify": "notify (enotify)",
    "vacation": "vacation",
}

# The name given to the in-memory filter set. It is not the script name and
# it never reaches the server -- sievelib only uses it for its own
# bookkeeping.
FILTERSET_NAME = "mailctl"

# The only rule-name form sievelib's parser recognises, and the one its
# renderer writes.
SIEVELIB_NAME_MARKER = "# Filter: "

# Where a rule goes, as the four CLI flags spell it. Sieve evaluates rules in
# order and `stop` ends evaluation, so position is part of what a rule
# *means* -- appending is a choice, not a neutral default, and these are the
# vocabulary for saying so.
PLACE_FIRST = "first"
PLACE_LAST = "last"
PLACE_BEFORE = "before"
PLACE_AFTER = "after"


@dataclass(frozen=True)
class Placement:
    """A request to put a rule somewhere, resolved against a real script.

    ``anchor`` names the existing rule that ``before`` and ``after`` are
    relative to, and is unused by ``first`` and ``last``. Carrying the pair
    as one value rather than as two parallel parameters is what stops a
    caller passing an anchor with nowhere for it to apply.
    """

    where: str
    anchor: str | None = None


@dataclass(frozen=True)
class NameDialect:
    """How rule names are spelled in a script, translated at the edges.

    ``read`` rewrites a script's own name markers into sievelib's
    ``# Filter: NAME`` before parsing, so each rule arrives with its real
    name rather than as "Unnamed rule N". ``write`` rewrites sievelib's
    markers into the script's form after rendering. Both must preserve line
    count, so a parse error still reports the line the user sees.
    """

    read: Callable[[str], str]
    write: Callable[[str], str]


# ----------------------------------------------------------------------------
def _unchanged(text: str) -> str:
    """Return ``text`` as it is."""
    return text


SIEVELIB_DIALECT = NameDialect(read=_unchanged, write=_unchanged)


# ----------------------------------------------------------------------------
def rewrite_hash_comments(
    text: str,
    translate: Callable[[str], str | None],
) -> str:
    """Rewrite the script's hash comments, leaving everything else alone.

    ``translate`` is handed each comment's text and returns a replacement,
    or None to leave it untouched.

    Tokenising with sievelib's own lexer -- rather than scanning lines -- is
    what makes this safe. A ``# rule:[x]`` sequence inside a quoted string,
    a ``/* ... */`` bracket comment, or a ``text:`` multi-line block is a
    different token to that lexer, so it can never be mistaken for a name
    marker. A hand-rolled line scan would have to re-derive Sieve's lexical
    rules and would disagree with the parser the moment it got one wrong.

    The work is done on the utf-8 bytes because that is what the lexer
    reports offsets in; splicing at character offsets would slide out of
    alignment on the first non-ASCII rule name.
    """
    raw = text.encode("utf-8")
    lexer = parser.Lexer(parser.Parser.lrules)
    edits: list[tuple[int, int, bytes]] = []

    try:
        for token_type, value in lexer.scan(raw):
            if token_type != "hash_comment":
                continue

            # The generator is suspended at its yield, so the lexer has not
            # advanced past the token yet and its position is the token's
            # start offset. Confirm that before splicing at it: a lexer
            # change that moved the offset would otherwise rewrite the
            # wrong bytes of the user's script, which is the one outcome
            # worse than not translating the name at all (ADR 0002).
            start = lexer.pos

            if raw[start : start + len(value)] != value:
                raise MailctlError(
                    "cannot locate a comment in the Sieve script safely, so "
                    "rule names cannot be translated without risking the "
                    "script's contents; this is an mailctl/sievelib "
                    "version mismatch, not a problem with your script"
                )

            comment = value.decode("utf-8")

            # ManageSieve is a CRLF protocol and the lexer's `#.*$` takes
            # the carriage return with the comment. Translate the text
            # without it, then put it back, so line endings survive.
            carriage = "\r" if comment.endswith("\r") else ""
            replacement = translate(comment[: len(comment) - len(carriage)])

            if replacement is None:
                continue

            edits.append(
                (
                    start,
                    start + len(value),
                    (replacement + carriage).encode("utf-8"),
                )
            )

    except parser.ParseError:
        # Not this function's error to report. The caller parses the same
        # text next and fails with sievelib's own diagnostic, which is the
        # hard stop ADR 0002 requires and says far more than a comment
        # rewrite could.
        return text

    if not edits:
        return text

    pieces = []
    cursor = 0

    for start, end, replacement in edits:
        pieces.append(raw[cursor:start])
        pieces.append(replacement)
        cursor = end

    pieces.append(raw[cursor:])

    return b"".join(pieces).decode("utf-8")


# ----------------------------------------------------------------------------
def _free_standing(
    comments: Iterable[bytes | str], filters: factory.FiltersSet
) -> list[str]:
    """The comments that are neither a rule's name nor its description."""
    kept = []

    for comment in comments:
        text = (
            comment.decode("utf-8") if isinstance(comment, bytes) else comment
        )

        if not text.startswith(
            (filters.filter_name_pretext, filters.filter_desc_pretext)
        ):
            kept.append(text)

    return kept


# ----------------------------------------------------------------------------
class _CommentedFiltersSet(factory.FiltersSet):
    """sievelib's filter set, keeping the hash comments a person wrote.

    sievelib's parser hands every top-level command the hash comments seen
    since the one before it, body comments included, and its renderer
    writes back only the name and description markers -- so every other
    comment was lost on the first merge (#7). Here each rule entry keeps
    its own under ``comments`` and they are written directly above its
    name marker, so a comment moves with its rule, is kept by
    ``--replace`` (sievelib updates the entry in place), and goes with it
    when the rule is removed. A comment inside a rule's body is kept the
    same way and so moves above the rule: sievelib renders a body from its
    parse tree, which has nowhere to hold one. The exception is Roundcube's
    disabled rule, ``if false # <its test>``: that comment stays on the
    ``if false`` line, where Roundcube looks for it (``_disabled_tests``).

    The two comment runs no rule owns are the script's own: those before
    ``require`` stay at the top, and those after the last rule stay at the
    end.

    ICEBOX: 2026-09-28 -- bracket comments /* ... */ are still dropped on
    merge; keep, preserve, retain multi-line block comments in a Sieve
    script. sievelib's parser discards them without recording where they
    were, so keeping them means re-deriving its command boundaries from
    the lexer. Revisit if a user's script is found to rely on them.
    """

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.leading_comments: list[str] = []
        self.trailing_comments: list[str] = []

    def keep_comments(
        self, script_parser: parser.Parser, disabled_tests: list[str | None]
    ) -> None:
        """Record the parsed script's free-standing comments.

        Call it straight after ``from_parser_result`` on the same parser:
        it pairs the parser's commands with the entries that call just
        made, in order, one entry per command other than ``require``.
        ``disabled_tests`` is :func:`_disabled_tests` of the same text.
        """
        entries = iter(self.filters)
        tests = iter(disabled_tests)

        for command in script_parser.result:
            comments = _free_standing(command.hash_comments, self)

            if isinstance(command, commands.RequireCommand):
                self.leading_comments += comments

                continue

            entry = cast(dict[str, Any], next(entries))
            test = next(tests) if _is_disabled(command) else None

            if test is not None:
                comments.remove(test)
                entry["disabled_test"] = (command, test)

            if _is_disabled(command):
                entry["disabled_condition"] = _disabled_condition(
                    command, test, self.requires
                )

            entry["comments"] = comments

        # Whatever the parser collected after the last command belongs to
        # no command at all.
        self.trailing_comments = _free_standing(
            script_parser.hash_comments, self
        )

    def tosieve(self, target: TextIO = sys.stdout) -> None:
        """sievelib's rendering, with each rule's comments above it."""
        _write_comments(target, self.leading_comments)
        self._plain([], self.requires).tosieve(target)

        for entry in self.filters:
            _write_comments(
                target, cast(dict[str, Any], entry).get("comments", [])
            )
            target.write(_render_entry(self._plain([entry], []), entry))

        _write_comments(target, self.trailing_comments)

    def _plain(self, entries: list, requires: list[str]) -> factory.FiltersSet:
        """A plain filter set over ``entries``, to render with sievelib."""
        plain = factory.FiltersSet(
            self.name, self.filter_name_pretext, self.filter_desc_pretext
        )
        plain.filters = entries
        plain.requires = requires

        return plain


# ----------------------------------------------------------------------------
def _is_disabled(command: commands.Command) -> bool:
    """Whether ``command`` is ``if false``, sievelib's disabled rule."""
    return isinstance(command, commands.IfCommand) and isinstance(
        command["test"], commands.FalseCommand
    )


# ----------------------------------------------------------------------------
def _disabled_tests(text: str) -> list[str | None]:
    """The comment on each top-level ``if false`` line, in script order.

    Roundcube disables a rule by writing ``if false # <its test>`` and
    finds it again by that comment directly after ``false``, on the same
    line. One entry per top-level ``if false``, None where no comment
    follows on that line; a parsed script has exactly as many of these as
    rules for which :func:`_is_disabled` holds.
    """
    raw = text.encode("utf-8")
    lexer = parser.Lexer(parser.Parser.lrules)
    tests: list[str | None] = []
    depth = 0
    previous: tuple[str, bytes] | None = None
    false_end: int | None = None

    for token_type, value in lexer.scan(raw):
        if false_end is not None:
            same_line = b"\n" not in raw[false_end : lexer.pos]
            tests[-1] = (
                value.decode("utf-8").strip()
                if token_type == "hash_comment" and same_line
                else None
            )
            false_end = None

        if token_type == "left_cbracket":
            depth += 1

        elif token_type == "right_cbracket":
            depth -= 1

        elif (
            depth == 0
            and token_type == "identifier"
            and value.lower() == b"false"
            and previous is not None
            and previous[0] == "identifier"
            and previous[1].lower() == b"if"
        ):
            tests.append(None)
            false_end = lexer.pos + len(value)

        if token_type not in ("hash_comment", "bracket_comment"):
            previous = (token_type, value)

    return tests


# ----------------------------------------------------------------------------
def _render_entry(plain: factory.FiltersSet, entry: dict[str, Any]) -> str:
    """One rule as sievelib renders it, Roundcube's disabled test inline.

    sievelib writes ``if false {``; Roundcube needs the comment after
    ``false`` on that line, so the brace moves to the next. The comment
    belongs to the content it was read with: on any other content it
    describes a test the rule no longer has, and is dropped
    (``_replace_rule`` gives replaced content its own).
    """
    buffer = io.StringIO()
    plain.tosieve(buffer)
    text = buffer.getvalue()
    content, test = entry.get("disabled_test", (None, None))

    if test is None or content is not entry["content"]:
        return text

    return text.replace("\nif false {\n", f"\nif false {test}\n{{\n", 1)


# ----------------------------------------------------------------------------
def _disabled_condition(
    command: commands.Command, test: str | None, requires: list[str]
) -> tuple[commands.Command, commands.Command | None, commands.Command]:
    """What a disabled rule would test and do, were it switched back on.

    ``(command, condition, body)``: the rule it was read from, its test
    (None where it cannot be read), and the command whose children are its
    actions. Roundcube keeps the test in the comment after ``false``;
    sievelib's own ``disablefilter`` wraps the whole rule in ``if false``
    instead, and that form carries its test on the rule inside.
    """
    wrapped = _wrapped_rule(command)

    if wrapped is not None:
        return command, wrapped["test"], wrapped

    if test is None:
        return command, None, command

    try:
        return command, _parse_test(test, requires), command

    except MailctlError:
        return command, None, command


# ----------------------------------------------------------------------------
def _wrapped_rule(command: commands.Command) -> commands.Command | None:
    """The rule sievelib's ``if false { ... }`` wraps, or None."""
    children = command.children

    if len(children) == 1 and isinstance(children[0], commands.IfCommand):
        return children[0]

    return None


# ----------------------------------------------------------------------------
def _parse_test(comment: str, requires: list[str]) -> commands.Command:
    """Parse the test Roundcube keeps in a disabled rule's comment.

    The comment is parsed as the test of an empty ``if``, under the
    script's own ``require`` line, so a test needing an extension the
    script never loads is refused here rather than by the server.
    """
    text = comment.lstrip("#").strip()
    loaded = ", ".join('"' + name.strip('"') + '"' for name in requires)
    source = f"require [{loaded}];\n" if requires else ""
    source += f"if {text}\n{{\n}}\n"

    script_parser = parser.Parser()

    if text and script_parser.parse(source):
        result = [
            command
            for command in script_parser.result
            if not isinstance(command, commands.RequireCommand)
        ]

        if len(result) == 1 and isinstance(result[0], commands.IfCommand):
            return result[0]["test"]

    raise MailctlError(
        f"the test kept in the comment ({text!r}) is not a Sieve test "
        f"mailctl can read"
    )


# ----------------------------------------------------------------------------
def _single_line_test(test: commands.Command) -> str | None:
    """A rule's test on one line, as Roundcube writes it after ``if false``.

    sievelib already renders a test on one line -- ``anyof (a, b)`` with
    ``, `` between the tests, which is Roundcube's own form. A multi-line
    string (``text:``) cannot be, and a comment ends at the line's end, so
    such a test is None.
    """
    buffer = io.StringIO()
    test.tosieve(target=buffer)
    line = buffer.getvalue().strip()

    if "\n" in line or "\r" in line:
        return None

    return line


# ----------------------------------------------------------------------------
def _switch_off(entry: dict[str, Any], line: str) -> None:
    """Disable ``entry`` in Roundcube's form, ``line`` its one-line test."""
    command = entry["content"]
    test = command["test"]

    command.arguments["test"] = commands.get_command_instance("false", command)
    entry["enabled"] = False
    entry["disabled_test"] = (command, f"# {line}")
    entry["disabled_condition"] = (command, test, command)


# ----------------------------------------------------------------------------
def _named_entry(filters: factory.FiltersSet, name: str) -> dict[str, Any]:
    """The first rule called ``name``; refused, listing the names, if none."""
    for entry in filters.filters:
        if entry["name"] == name:
            return cast(dict[str, Any], entry)

    known = ", ".join(rule_names(filters)) or "(none)"

    raise MailctlError(
        f"no rule named {name!r} in the active script. Known rules: {known}"
    )


# ----------------------------------------------------------------------------
def disable_rule(
    existing: str, name: str, dialect: NameDialect = SIEVELIB_DIALECT
) -> str:
    """Switch a named rule off, keeping it, and return the new source.

    Written the way Roundcube writes it -- ``if false # <its test>``, the
    body kept -- so the webmail shows the rule as disabled and can switch
    it back on. A rule already disabled returns ``existing`` unchanged.
    """
    filters = parse_script(existing, dialect)
    entry = _named_entry(filters, name)
    command = entry["content"]

    if not isinstance(command, commands.IfCommand):
        raise MailctlError(
            f"rule {name!r} cannot be disabled: it has no 'if' test to "
            f"switch off"
        )

    if _is_disabled(command):
        return existing

    line = _single_line_test(command["test"])

    if line is None:
        raise MailctlError(
            f"rule {name!r} cannot be disabled: its test spans more than "
            f"one line, and a disabled rule keeps its test in a comment on "
            f"the 'if false' line, where Roundcube looks for it"
        )

    _switch_off(entry, line)

    return render_script(filters, dialect)


# ----------------------------------------------------------------------------
def enable_rule(
    existing: str, name: str, dialect: NameDialect = SIEVELIB_DIALECT
) -> str:
    """Switch a disabled rule back on and return the new source.

    Reads either disabled form: Roundcube's test in the comment after
    ``if false``, or sievelib's ``if false { <the rule> }``. A rule whose
    test cannot be read back is refused -- guessing one would change what
    it matches. A rule already enabled returns ``existing`` unchanged.
    """
    filters = parse_script(existing, dialect)
    entry = _named_entry(filters, name)
    command = entry["content"]

    if not _is_disabled(command):
        return existing

    wrapped = _wrapped_rule(command)

    if wrapped is not None:
        entry["content"] = wrapped

    else:
        _, comment = entry.get("disabled_test", (None, None))

        if comment is None:
            raise MailctlError(
                f"rule {name!r} cannot be enabled: it is disabled with no "
                f"test kept after 'if false', so there is nothing to restore"
            )

        try:
            test = _parse_test(comment, filters.requires)

        except MailctlError as error:
            raise MailctlError(
                f"rule {name!r} cannot be enabled: {error}"
            ) from None

        command.arguments["test"] = test

    entry["enabled"] = True
    entry.pop("disabled_test", None)
    entry.pop("disabled_condition", None)

    return render_script(filters, dialect)


# ----------------------------------------------------------------------------
def _write_comments(target: TextIO, comments: Iterable[str]) -> None:
    """Write each comment on its own line."""
    for comment in comments:
        target.write(f"{comment}\n")


# ----------------------------------------------------------------------------
def parse_script(
    text: str, dialect: NameDialect = SIEVELIB_DIALECT
) -> factory.FiltersSet:
    """Parse Sieve source into an editable filter set.

    An empty or missing script is a normal starting state, not an error --
    it just yields an empty set. A script that will not parse is a hard
    stop: merging into it would risk losing rules.
    """
    filters = _CommentedFiltersSet(FILTERSET_NAME)

    if not text or not text.strip():
        return filters

    script_parser = parser.Parser()
    source = dialect.read(text)

    if not script_parser.parse(source):
        raise MailctlError(
            "the existing Sieve script could not be parsed, so merging into "
            "it would risk losing rules: "
            f"{getattr(script_parser, 'error', 'unknown parse error')}"
        )

    filters.from_parser_result(script_parser)
    filters.keep_comments(script_parser, _disabled_tests(source))

    return filters


# ----------------------------------------------------------------------------
def render_script(
    filters: factory.FiltersSet, dialect: NameDialect = SIEVELIB_DIALECT
) -> str:
    """Render a filter set back to Sieve source, names in ``dialect``."""
    buffer = io.StringIO()
    filters.tosieve(buffer)

    return dialect.write(buffer.getvalue())


# ----------------------------------------------------------------------------
def rule_names(filters: factory.FiltersSet) -> list[str]:
    """Return the names of the rules in a filter set, in order."""
    return [entry["name"] for entry in filters.filters]


# ----------------------------------------------------------------------------
def resolve_position(
    names: list[str], placement: Placement | None, name: str
) -> int:
    """Return the index ``name`` should end up at, counting other rules only.

    ``names`` is the script's rules in order, before the merge. The answer
    is an index into that list **with the one copy of ``name`` being
    placed taken out of it**, because the rule being placed cannot be its
    own reference point -- for a ``--replace`` the old copy is on its way
    out, and for a new rule it was never there.

    ``placement`` of None is what every caller got before the flags existed,
    and it keeps that behaviour exactly: a new rule is appended, and an
    existing one stays at the index it already has.

    Failing here rather than at render time is deliberate. A named anchor
    that does not exist is almost always a typo, and quietly appending
    instead would be the same silent-success this whole check exists to
    remove.
    """
    # Exactly ONE copy comes out, because exactly one goes back in:
    # updatefilter rewrites the FIRST rule of that name and _move_rule
    # pops that same first one, so the list this index is measured against
    # is the list it is inserted into. Dropping every same-named rule
    # instead -- what a plain `!= name` filter does -- shortens that list
    # by one per surviving duplicate, and --last then lands the rule that
    # many slots short of the end while reporting success. It is reachable
    # because a hand-edited script can hold two rules mailctl reads as
    # one name: `# rule:[Lists]` and `# rule:[Lists ]` both parse to
    # `Lists`. Do not simplify this back to a comprehension.
    others = list(names)

    if name in others:
        others.remove(name)

    if placement is None:
        return names.index(name) if name in names else len(others)

    if placement.where == PLACE_FIRST:
        return 0

    if placement.where == PLACE_LAST:
        return len(others)

    if placement.anchor == name:
        raise MailctlError(
            f"--{placement.where} {placement.anchor!r} names the rule being "
            f"added, which has no position to be relative to. Name another "
            f"rule, or use --first / --last."
        )

    if placement.anchor not in others:
        known = ", ".join(names) or "(none)"

        raise MailctlError(
            f"no rule named {placement.anchor!r} in the active script, so "
            f"--{placement.where} has nothing to place this rule against. "
            f"Known rules: {known}"
        )

    index = others.index(placement.anchor)

    return index if placement.where == PLACE_BEFORE else index + 1


# ----------------------------------------------------------------------------
def _move_rule(filters: factory.FiltersSet, name: str, position: int) -> None:
    """Move ``name`` to ``position``, which counts the other rules only.

    sievelib has no insert-at API: ``addfilter`` appends, and
    ``updatefilter`` deliberately leaves a rule where it was. Its filters
    are a plain list, though, so placement is a reorder of that list.

    Pulling the entry out before putting it back is what makes ``position``
    mean the same thing here as in :func:`resolve_position` -- after the
    pop, the list is exactly the list the position was measured against.

    That equality rests on both ends agreeing about *which* entry leaves,
    and the agreement is that it is the **first** of that name: the one
    ``updatefilter`` rewrites, the one popped below, and the one
    :func:`resolve_position` leaves out. A script with a duplicated name
    is where the three could disagree, and where they once did.
    """
    entries = filters.filters

    for index, entry in enumerate(entries):
        if entry["name"] == name:
            entries.insert(position, entries.pop(index))

            return


# ----------------------------------------------------------------------------
def merge_rule(
    existing: str,
    name: str,
    conditions: list[tuple],
    actions: list[tuple],
    matchtype: str = "anyof",
    replace: bool = False,
    placement: Placement | None = None,
    dialect: NameDialect = SIEVELIB_DIALECT,
) -> str:
    """Merge one rule into an existing script and return the new source.

    The script is parsed and re-rendered rather than appended to, so the
    ``require`` line stays correct for the union of all rules. Every rule
    already present is carried through untouched, and in its original order
    -- the only rule that moves is the one being placed.

    ``placement`` and ``replace`` interact in the one way that is not
    obvious: replacing a rule **with** a placement flag *moves* it, and
    replacing it **without** one leaves it exactly where it was. An
    explicit placement is an instruction, so honouring it for a new rule and
    ignoring it for an existing one would be a command that reports success
    and changes nothing -- which is the failure mode this argument was added
    to remove, not one to reintroduce at a different address.
    """
    filters = parse_script(existing, dialect)
    exists = filters.filter_exists(name)

    if exists and not replace:
        raise MailctlError(
            f"a rule named {name!r} already exists in the active script. "
            f"Use --replace to overwrite it, or --name to pick another."
        )

    # Resolved against the script as it stands, before the merge changes
    # what the names are.
    position = resolve_position(rule_names(filters), placement, name)

    if exists:
        _replace_rule(filters, name, conditions, actions, matchtype)

    else:
        filters.addfilter(name, conditions, actions, matchtype)

    _move_rule(filters, name, position)

    return render_script(filters, dialect)


# ----------------------------------------------------------------------------
def _replace_rule(
    filters: factory.FiltersSet,
    name: str,
    conditions: list[tuple],
    actions: list[tuple],
    matchtype: str,
) -> None:
    """Give a named rule new content, keeping it disabled if it was.

    sievelib's ``updatefilter`` re-disables a disabled rule by wrapping it,
    ``if false { if <test> { ... } }``, which Roundcube shows as enabled
    (#168). So the rule is updated as though enabled and then switched off
    in Roundcube's own form, the new test after ``false``.
    """
    entry = _named_entry(filters, name)
    disabled = not entry["enabled"]

    entry["enabled"] = True
    filters.updatefilter(name, name, conditions, actions, matchtype)

    if not disabled:
        return

    line = _single_line_test(entry["content"]["test"])

    if line is None:
        raise MailctlError(
            f"rule {name!r} cannot be replaced while disabled: its new test "
            f"spans more than one line, and a disabled rule keeps its test "
            f"in a comment on the 'if false' line, where Roundcube looks "
            f"for it. Enable it first (mailctl enable-rule {name}), then "
            f"replace it"
        )

    _switch_off(entry, line)


# ----------------------------------------------------------------------------
def remove_rule(
    existing: str, name: str, dialect: NameDialect = SIEVELIB_DIALECT
) -> str:
    """Remove a named rule and return the new script source."""
    filters = parse_script(existing, dialect)

    if not filters.removefilter(name):
        known = ", ".join(rule_names(filters)) or "(none)"

        raise MailctlError(
            f"no rule named {name!r} in the active script. Known rules: "
            f"{known}"
        )

    return render_script(filters, dialect)


# ----------------------------------------------------------------------------
def move_rule(
    existing: str,
    name: str,
    placement: Placement,
    dialect: NameDialect = SIEVELIB_DIALECT,
) -> str:
    """Move a named rule, unchanged, and return the new script source.

    Only the position changes. The index comes from
    :func:`resolve_position`, measured against the script with the moved
    rule taken out, so an unknown or self-naming anchor raises the same way
    it does for ``add``.
    """
    filters = parse_script(existing, dialect)

    if not filters.filter_exists(name):
        known = ", ".join(rule_names(filters)) or "(none)"

        raise MailctlError(
            f"no rule named {name!r} in the active script. Known rules: "
            f"{known}"
        )

    position = resolve_position(rule_names(filters), placement, name)
    _move_rule(filters, name, position)

    return render_script(filters, dialect)


# ----------------------------------------------------------------------------
def script_diff(before: str, after: str, name: str = "sieve") -> str:
    """Return a unified diff between two script versions."""
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{name} (current)",
        tofile=f"{name} (proposed)",
        n=3,
    )

    return "".join(lines)


@dataclass(frozen=True)
class DisplayDiff:
    """A diff meant for a person, and the one thing it deliberately hides.

    ``reformats`` is true when the script the server holds is not already
    in the formatting :func:`render_script` produces -- brace placement,
    indentation, the blank line after ``require``. The upload really does
    rewrite all of that, so a reader shown only ``text`` has been told
    less than the whole truth. Carrying the flag beside the diff is what
    lets the front-end say so; dropping it would turn hiding the noise
    into hiding the fact.
    """

    text: str
    reformats: bool


# ----------------------------------------------------------------------------
def display_diff(
    before: str,
    after: str,
    name: str = "sieve",
    dialect: NameDialect = SIEVELIB_DIALECT,
) -> DisplayDiff:
    """Diff two script versions with both sides in the same formatting.

    Every merge re-renders the whole script through sievelib, so a diff
    taken against the *raw* previous source reports the renderer's own
    layout -- tabs to spaces, the brace pulled up onto the ``if`` line --
    as though it were the change being proposed. Measured against a
    Roundcube-authored script, a no-op round trip moves 29 lines of 25.
    At a glance that is indistinguishable from something having gone badly
    wrong, which defeats the whole point of showing a diff before
    changing anything.

    Rendering ``before`` the same way ``after`` was produced leaves only
    the real change. It is a *display* concern and nothing more: what gets
    uploaded and what gets backed up are untouched, and the backup stays
    the server's exact bytes.

    A script that will not parse never reaches here -- ``merge_rule`` and
    ``remove_rule`` both parse first, and both stop rather than risk
    losing rules.
    """
    normalized = render_script(parse_script(before, dialect), dialect)

    return DisplayDiff(
        text=script_diff(normalized, after, name),
        reformats=normalized != before,
    )
