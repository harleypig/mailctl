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
import re
import sys
import unicodedata
from collections.abc import Callable, Iterable, Mapping
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
    "fileinto_targets",
    "merge_rule",
    "move_rule",
    "parse_script",
    "rearrange_rules",
    "remove_rule",
    "rename_rule",
    "render_script",
    "resolve_position",
    "retarget_fileinto",
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
            extra = cast(dict[str, Any], entry)
            _write_comments(target, extra.get("comments", []))
            target.write(_render_entry(self._plain([entry], []), extra))

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

    if text and script_parser.parse(source.encode("utf-8")):
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
def rename_rule(
    existing: str,
    old: str,
    new: str,
    dialect: NameDialect = SIEVELIB_DIALECT,
) -> str:
    """Give the first rule called ``old`` the name ``new``; return the
    new source.

    Only the rule's name marker is rewritten, where it sits in
    ``existing``; every other byte is kept, so the rule's body, its
    position, whether it is disabled, and every other rule and comment
    survive exactly -- which a parse and re-render would not. The marker
    is written in ``dialect``'s form.

    Refused when ``old`` is not a rule, when ``new`` is empty, is already
    a rule's name, or would not read back as ``new`` in ``dialect``, and
    when ``old`` has no name marker in the script to rewrite. ``new`` the
    same as ``old`` returns ``existing`` unchanged.
    """
    if not new.strip():
        raise MailctlError("a rule's new name cannot be empty")

    filters = parse_script(existing, dialect)
    names = rule_names(filters)

    if old not in names:
        known = ", ".join(names) or "(none)"

        raise MailctlError(
            f"no rule named {old!r} in the active script. Known rules: {known}"
        )

    if new == old:
        return existing

    if new in names:
        raise MailctlError(
            f"a rule named {new!r} already exists in the active script, and "
            f"two rules of one name cannot be told apart.",
            code="rule_name_taken",
            fields={"operation": "rules"},
        )

    _check_rule_name(new, dialect)

    expected = list(names)
    expected[names.index(old)] = new
    marker = dialect.write(f"{SIEVELIB_NAME_MARKER}{new}")

    # Which comment names the rule is sievelib's call -- the last name
    # marker above a command wins, and a duplicated name belongs to the
    # first rule -- so each comment reading as ``old`` is tried in turn,
    # and the first whose rewrite reads back as exactly the one rename is
    # the one.
    for candidate in range(_count_markers(existing, old, dialect)):
        after = _rewrite_marker(existing, old, marker, candidate, dialect)

        if rule_names(parse_script(after, dialect)) == expected:
            return after

    # ICEBOX: 2026-09-29 -- naming an unnamed rule ("Unnamed rule N"):
    # give, add, set, or write a name on a rule with no name marker. It
    # needs a marker line inserted above the rule's first byte, and
    # sievelib records no command offsets to find it by. Revisit if a
    # script with unnamed rules turns up on a real account.
    raise MailctlError(
        f"rule {old!r} has no name written in the script to change, so it "
        f"cannot be renamed in place"
    )


# ----------------------------------------------------------------------------
def _check_rule_name(name: str, dialect: NameDialect) -> None:
    """Refuse a name that would not read back as itself in ``dialect``."""
    if any(unicodedata.category(char) == "Cc" for char in name):
        raise MailctlError(
            f"{name!r} cannot be written as a rule name: it holds a control "
            f"character, and a name is one line of the script"
        )

    probe = dialect.write(f"{SIEVELIB_NAME_MARKER}{name}\nkeep;\n")

    if rule_names(parse_script(probe, dialect)) != [name]:
        raise MailctlError(
            f"{name!r} cannot be written as a rule name: it would not read "
            f"back as the same name. Leading or trailing spaces, or a name "
            f"marker inside it, are the usual cause"
        )


# ----------------------------------------------------------------------------
def _marker_name(comment: str, dialect: NameDialect) -> str | None:
    """The rule name a hash comment carries in ``dialect``, or None."""
    read = dialect.read(comment)

    if not read.startswith(SIEVELIB_NAME_MARKER):
        return None

    # sievelib's own reading, in FiltersSet.from_parser_result.
    return read.replace(SIEVELIB_NAME_MARKER, "")


# ----------------------------------------------------------------------------
def _count_markers(text: str, name: str, dialect: NameDialect) -> int:
    """How many hash comments in ``text`` read as the name ``name``."""
    found = 0

    def count(comment: str) -> None:
        nonlocal found

        if _marker_name(comment, dialect) == name:
            found += 1

    rewrite_hash_comments(text, count)

    return found


# ----------------------------------------------------------------------------
def _rewrite_marker(
    text: str, name: str, marker: str, which: int, dialect: NameDialect
) -> str:
    """``text`` with the ``which``-th comment naming ``name`` replaced by
    ``marker``, every other byte kept."""
    seen = -1

    def translate(comment: str) -> str | None:
        nonlocal seen

        if _marker_name(comment, dialect) != name:
            return None

        seen += 1

        return marker if seen == which else None

    return rewrite_hash_comments(text, translate)


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

    if not script_parser.parse(source.encode("utf-8")):
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
    names: list[str],
    placement: Placement | None,
    name: str,
    *,
    moving: bool = False,
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

    ``moving`` says the rule is an existing one being moved rather than
    one being added, so a refusal names what is happening to it.

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
        verb = "moved" if moving else "added"

        raise MailctlError(
            f"{placement.anchor!r} is the rule being {verb}, which has no "
            f"position to be relative to. Name another rule, or place it "
            f"first or last.",
            code="self_anchor",
            fields={
                "where": placement.where,
                "anchor": placement.anchor,
                "verb": verb,
            },
        )

    if placement.anchor not in others:
        known = ", ".join(names) or "(none)"

        raise MailctlError(
            f"no rule named {placement.anchor!r} in the active script to "
            f"place this rule {placement.where}. Known rules: {known}",
            code="unknown_anchor",
            fields={
                "where": placement.where,
                "anchor": placement.anchor,
                "known": known,
            },
        )

    index = others.index(placement.anchor)

    return index if placement.where == PLACE_BEFORE else index + 1


# ----------------------------------------------------------------------------
def _quoted(value: str) -> str:
    """``value`` as a Sieve quoted string; the caller has escaped it."""
    return f'"{value}"'


# ----------------------------------------------------------------------------
def _condition(condition: tuple, parent: commands.Command) -> commands.Command:
    """Build one test from a condition tuple, told apart by its length.

    ``(header, :matchtype, value)`` is a ``header`` test and ``("body",
    :transform, :matchtype, value)`` a ``body`` test. sievelib's own
    builder reads the first element as a test name before it is a header
    name -- ``notes`` became ``not header "notes"``, ``exists`` an
    ``exists`` test (#175) -- so the header name is never handed to it.
    """
    if len(condition) == 3:
        header, match_type, value = condition
        test = commands.get_command_instance("header", parent)
        test.check_next_arg("tag", match_type)
        test.check_next_arg("string", _quoted(header))
        test.check_next_arg("string", _quoted(value))

        return test

    if len(condition) == 4 and condition[0] == "body":
        _, transform, match_type, value = condition
        test = commands.get_command_instance("body", parent, False)
        test.check_next_arg("tag", transform)
        test.check_next_arg("tag", match_type)
        test.check_next_arg("stringlist", f"[{_quoted(value)}]")

        return test

    raise ValueError(f"not a header or body condition: {condition!r}")


# ----------------------------------------------------------------------------
def _build_rule(
    filters: factory.FiltersSet,
    conditions: list[tuple],
    actions: list[tuple],
    matchtype: str,
) -> commands.Command:
    """Build a rule's ``if`` command, recording what it requires.

    The shape and the ``require`` order are sievelib's ``addfilter``'s --
    tests first, then each action and its tags -- so the script renders
    byte for byte as it did for every header name sievelib read right.
    """
    rule = commands.get_command_instance("if")
    combinator = commands.get_command_instance(matchtype, rule)

    for condition in conditions:
        test = _condition(condition, rule)

        if test.extension is not None:
            filters.require(test.extension)

        # sievelib annotates ``avalue`` as str, but a "test" argument is a
        # Command -- its own factory and parser pass one, as here.
        combinator.check_next_arg(
            "test",
            test,  # pyright: ignore[reportArgumentType]
        )

    rule.check_next_arg(
        "test",
        combinator,  # pyright: ignore[reportArgumentType]
    )

    for name, *arguments in actions:
        action = commands.get_command_instance(name, rule, False)

        if action.extension is not None:
            filters.require(action.extension)

        for argument in arguments:
            filters.check_if_arg_is_extension(argument)

            if argument.startswith(":"):
                kind, value = "tag", argument

            else:
                kind, value = "string", _quoted(argument)

            action.check_next_arg(kind, value, check_extension=False)

        rule.addchild(action)

    return rule


# ----------------------------------------------------------------------------
def _move_rule(filters: factory.FiltersSet, name: str, position: int) -> None:
    """Move ``name`` to ``position``, which counts the other rules only.

    A new rule is appended, and a replaced one is left where it was. The
    filters are a plain list, so placement is a reorder of that list.

    Pulling the entry out before putting it back is what makes ``position``
    mean the same thing here as in :func:`resolve_position` -- after the
    pop, the list is exactly the list the position was measured against.

    That equality rests on both ends agreeing about *which* entry leaves,
    and the agreement is that it is the **first** of that name: the one
    :func:`_replace_rule` rewrites, the one popped below, and the one
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
            f"a rule named {name!r} already exists in the active script.",
            code="rule_exists",
        )

    # Resolved against the script as it stands, before the merge changes
    # what the names are.
    position = resolve_position(rule_names(filters), placement, name)

    if exists:
        _replace_rule(filters, name, conditions, actions, matchtype)

    else:
        filters.filters.append(
            {
                "name": name,
                "content": _build_rule(
                    filters, conditions, actions, matchtype
                ),
                "enabled": True,
            }
        )

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

    The entry keeps its place and its comments; only its content changes.
    A disabled rule is switched off again in Roundcube's own form, the new
    test after ``false`` -- sievelib's ``updatefilter`` wrapped it instead,
    ``if false { if <test> { ... } }``, which Roundcube shows as enabled
    (#168).
    """
    entry = _named_entry(filters, name)
    disabled = not entry["enabled"]

    entry["enabled"] = True
    entry["content"] = _build_rule(filters, conditions, actions, matchtype)

    if not disabled:
        return

    line = _single_line_test(entry["content"]["test"])

    if line is None:
        before = (
            f"rule {name!r} cannot be replaced while disabled: its new test "
            f"spans more than one line, and a disabled rule keeps its test "
            f"in a comment on the 'if false' line, where Roundcube looks "
            f"for it. Enable it first"
        )

        raise MailctlError(
            f"{before}, then replace it",
            code="replace_disabled",
            fields={
                "before": before,
                "operation": "enable-rule",
                "arguments": (name,),
            },
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

    position = resolve_position(
        rule_names(filters), placement, name, moving=True
    )
    _move_rule(filters, name, position)

    return render_script(filters, dialect)


# ----------------------------------------------------------------------------
def rearrange_rules(
    existing: str,
    layout: Iterable[tuple[int, Iterable[int]]],
    dialect: NameDialect = SIEVELIB_DIALECT,
) -> str:
    """Reorder, merge, and drop rules; return the new script source.

    ``layout`` is ``(index, absorbed)`` pairs in the new order, each index
    counting the script's rules as they stand. A rule named by no pair is
    dropped. A rule with ``absorbed`` indexes takes their keys into its
    own key list, in order, and their comments above its own; the rules
    absorbed are dropped. Every rule keeps its own content otherwise, and
    the result stays a flat list of rules -- nothing is nested.

    A merge is refused unless every rule in it is enabled, tests one
    header with one ``header`` test, and matches the survivor's header,
    match type, comparator, and actions exactly: a key list is an OR, so
    only then is one rule the same filter as the several.
    """
    filters = parse_script(existing, dialect)
    entries = cast(list[dict[str, Any]], filters.filters)
    slots = [(index, list(absorbed)) for index, absorbed in layout]
    named = [
        index
        for survivor, absorbed in slots
        for index in (survivor, *absorbed)
    ]

    if len(set(named)) != len(named) or not all(
        0 <= index < len(entries) for index in named
    ):
        raise MailctlError(
            "a rearrangement must name each rule in the script at most once"
        )

    arranged = []

    for survivor, absorbed in slots:
        entry = entries[survivor]

        if absorbed:
            _absorb(entry, [entries[index] for index in absorbed])

        arranged.append(entry)

    filters.filters = arranged

    return render_script(filters, dialect)


# ----------------------------------------------------------------------------
def _absorb(entry: dict[str, Any], others: list[dict[str, Any]]) -> None:
    """Take the keys of ``others`` into ``entry``'s one header test."""
    test = _sole_header(entry)
    shape = _header_shape(test)
    actions = _action_source(entry["content"])
    keys = _key_tokens(test)

    for other in others:
        theirs = _sole_header(other)

        if _header_shape(theirs) != shape or (
            _action_source(other["content"]) != actions
        ):
            raise MailctlError(
                f"rule {other['name']!r} cannot be merged into "
                f"{entry['name']!r}: they do not test the same header the "
                f"same way with the same actions"
            )

        keys += [key for key in _key_tokens(theirs) if key not in keys]
        entry["comments"] = [
            *entry.get("comments", []),
            *other.get("comments", []),
        ]

    test.arguments["key-list"] = keys if len(keys) > 1 else keys[0]


# ----------------------------------------------------------------------------
def _sole_header(entry: dict[str, Any]) -> commands.Command:
    """The one ``header`` test of an enabled rule, or refused."""
    command = entry["content"]
    test = (
        command.arguments.get("test")
        if isinstance(command, commands.IfCommand)
        else None
    )

    if isinstance(test, commands.AnyofCommand | commands.AllofCommand):
        children = test.arguments.get("tests") or []
        test = children[0] if len(children) == 1 else None

    if (
        not isinstance(test, commands.HeaderCommand)
        or len(_string_list(test.arguments.get("header-names"))) != 1
    ):
        raise MailctlError(
            f"rule {entry['name']!r} cannot be merged: only an enabled rule "
            f"with one header test on one header can be"
        )

    return test


# ----------------------------------------------------------------------------
def _header_shape(test: commands.Command) -> tuple[str, str, str]:
    """What a ``header`` test compares, apart from its keys: the header
    (names are case-insensitive), the match type, and the comparator."""
    (header,) = _string_list(test.arguments.get("header-names"))
    match_type = str(test.arguments.get("match-type") or ":is").lower()
    comparator = str(
        (test.extra_arguments or {}).get("comparator") or '"i;ascii-casemap"'
    )

    return _unquote(header).lower(), match_type, _unquote(comparator)


# ----------------------------------------------------------------------------
def _key_tokens(test: commands.Command) -> list[str]:
    """A ``header`` test's keys, as the quoted Sieve source they were."""
    return list(_string_list(test.arguments.get("key-list")))


# ----------------------------------------------------------------------------
def _string_list(argument: Any) -> list[str]:
    """A string or a string list argument, as a list of its tokens."""
    if argument is None:
        return []

    if isinstance(argument, list | tuple):
        return [str(item) for item in argument]

    return [str(argument)]


# ----------------------------------------------------------------------------
def _action_source(command: commands.Command) -> list[str]:
    """A rule's actions, each as Sieve source."""
    rendered = []

    for child in command.children:
        buffer = io.StringIO()
        child.tosieve(target=buffer)
        rendered.append(buffer.getvalue().strip())

    return rendered


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


# ############################################################################
# Filing targets -- read, and repointed in place
# ############################################################################


# ----------------------------------------------------------------------------
def _unquote(token: str) -> str:
    """A quoted string's value: the quotes off, each ``\\x`` read as ``x``
    (RFC 5228 section 2.4.2). Anything else -- a ``text:`` block -- is
    returned as it stands."""
    if len(token) < 2 or not token.startswith('"') or not token.endswith('"'):
        return token

    return re.sub(r"\\(.)", r"\1", token[1:-1], flags=re.DOTALL)


# ----------------------------------------------------------------------------
def _requote(value: str) -> str:
    """``value`` as a Sieve quoted string, its backslashes and quotes
    escaped."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ----------------------------------------------------------------------------
def _commands(command: commands.Command) -> Iterable[commands.Command]:
    """``command`` and every command inside it, in script order."""
    yield command

    for child in command.children:
        yield from _commands(child)


# ----------------------------------------------------------------------------
def fileinto_targets(filters: factory.FiltersSet) -> list[tuple[str, str]]:
    """``(rule name, folder)`` for every ``fileinto``, in script order.

    A disabled rule's are included: switching it back on files there
    again, so a folder it names is still one it depends on.
    """
    found = []

    for entry in filters.filters:
        for command in _commands(entry["content"]):
            if isinstance(command, commands.FileintoCommand):
                found.append(
                    (entry["name"], _unquote(str(command["mailbox"])))
                )

    return found


# ----------------------------------------------------------------------------
def retarget_fileinto(text: str, renames: Mapping[str, str]) -> str:
    """Repoint each ``fileinto`` whose folder is a key of ``renames``.

    Only the folder's string is replaced, where it sits in ``text``; every
    other byte is kept, so a rule that files nowhere renamed -- and every
    comment, and the host's own layout -- survives exactly, which a parse
    and re-render would not. The folder is the last argument before the
    ``fileinto``'s semicolon, whatever tags precede it.

    The result is checked by parsing both scripts: its targets must be the
    old ones with exactly the renames applied, or it is refused rather
    than handed back half-done. So is a script the lexer cannot scan.
    """
    raw = text.encode("utf-8")
    lexer = parser.Lexer(parser.Parser.lrules)
    edits: list[tuple[int, int, bytes]] = []
    inside = False
    last: tuple[str, bytes, int] | None = None

    try:
        for token_type, value in lexer.scan(raw):
            start = lexer.pos

            if token_type in ("hash_comment", "bracket_comment"):
                continue

            if not inside:
                inside = (
                    token_type == "identifier" and value.lower() == b"fileinto"
                )
                last = None

                continue

            if token_type in ("left_cbracket", "right_cbracket"):
                inside, last = False, None

            elif token_type == "semicolon":
                edit = (
                    _retarget_edit(raw, last, renames)
                    if last is not None and last[0] == "string"
                    else None
                )

                if edit is not None:
                    edits.append(edit)

                inside, last = False, None

            else:
                last = (token_type, value, start)

    except parser.ParseError as error:
        raise MailctlError(
            f"cannot scan the Sieve script to rewrite its folders -- {error}"
        ) from error

    pieces = []
    cursor = 0

    for start, end, replacement in edits:
        pieces += [raw[cursor:start], replacement]
        cursor = end

    after = b"".join([*pieces, raw[cursor:]]).decode("utf-8")
    expected = [
        renames.get(folder, folder)
        for _, folder in fileinto_targets(parse_script(text))
    ]

    if [folder for _, folder in fileinto_targets(parse_script(after))] != (
        expected
    ):
        raise MailctlError(
            "a rule's folder could not be rewritten in place, so the "
            "script is left as it is"
        )

    return after


# ----------------------------------------------------------------------------
def _retarget_edit(
    raw: bytes, token: tuple[str, bytes, int], renames: Mapping[str, str]
) -> tuple[int, int, bytes] | None:
    """The splice that repoints one ``fileinto`` string, or None."""
    _, value, start = token

    # The lexer's position is the token's start only while its generator
    # is suspended at the yield; check it, since splicing at a wrong offset
    # would rewrite some other part of the user's script.
    if raw[start : start + len(value)] != value:
        raise MailctlError(
            "cannot locate a folder in the Sieve script safely; this is a "
            "mailctl/sievelib version mismatch, not a problem with your "
            "script"
        )

    new = renames.get(_unquote(value.decode("utf-8")))

    if new is None:
        return None

    return (start, start + len(value), _requote(new).encode("utf-8"))
