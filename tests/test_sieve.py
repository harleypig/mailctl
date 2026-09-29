"""Offline script editing: parse, merge, remove, render, diff, back up.

The merge tests are the reason this file exists. An MXroute account's
active script is the same file Roundcube's filter UI writes, so a merge
that loses a rule destroys work the user did by hand, and does it
silently -- PUTSCRIPT succeeds and the loss only surfaces days later
(ADR 0002). Everything below either proves that cannot happen or proves
the failure is loud.
"""

import argparse
import re

import pytest

from mailctl import MailctlError
from mailctl.components.managesieve import (
    PLACE_AFTER,
    PLACE_BEFORE,
    PLACE_FIRST,
    PLACE_LAST,
    Placement,
    backup_path,
    rule_names,
    script_diff,
)
from mailctl.components.managesieve import script as script_module
from mailctl.config import Config, load_config
from mailctl.criteria import Criteria, escape_sieve_string
from mailctl.providers.mxroute import MxrouteDialect
from mailctl.providers.mxroute import managesieve as mxroute_managesieve
from mailctl.providers.mxroute.sieve import (
    disable_rule,
    display_diff,
    enable_rule,
    merge_rule,
    move_rule,
    parse_script,
    remove_rule,
    render_script,
)
from mailctl.utilities.backup_files import write_backup

# ############################################################################
# Helpers
# ############################################################################


# ----------------------------------------------------------------------------
def simple_criteria(header: str = "from", value: str = "a@example.com"):
    """Return a one-term Criteria, for tests that only need conditions."""
    criteria = Criteria()
    criteria.add(header, value)

    return criteria


# ----------------------------------------------------------------------------
def merge_simple(existing: str, name: str, folder: str, **kwargs) -> str:
    """Merge a plain 'file it here and stop' rule into ``existing``."""
    criteria = simple_criteria()

    return merge_rule(
        existing,
        name,
        criteria.sieve_conditions(),
        [("fileinto", folder), ("stop",)],
        criteria.sieve_matchtype(),
        **kwargs,
    )


# ############################################################################
# The data-loss guard
# ############################################################################


# ----------------------------------------------------------------------------
def test_merge_keeps_every_pre_existing_rule(roundcube_script):
    """The single most important assertion in the suite.

    A merge must carry through rules mailctl did not write. The check is
    on the rule *bodies* -- the conditions and the actions -- because that
    is what actually sorts the user's mail; a rule whose fileinto target
    survived is a rule that still works.
    """
    merged = merge_simple(roundcube_script, "new-rule", "INBOX.New")

    # The hand-made rules' conditions.
    assert 'header :contains "from" "boss@example.com"' in merged
    assert 'header :contains "subject" "newsletter"' in merged

    # And their actions, unchanged -- including the flag, which is where a
    # naive re-render would drop a backslash.
    assert 'fileinto "INBOX.Boss";' in merged
    assert 'setflag "\\\\Flagged";' in merged
    assert 'fileinto "INBOX.Noise";' in merged

    # The new rule landed as well, so this is a merge and not a no-op.
    assert 'fileinto "INBOX.New";' in merged


# ----------------------------------------------------------------------------
def test_merged_script_reparses_with_sievelib(roundcube_script, reparse):
    """Emitting text that sievelib cannot read back would strand the user.

    The next run parses the active script before merging into it, so an
    unparseable emission turns every later ``mailctl add`` into a hard
    stop against a script only mailctl could have written.
    """
    merged = merge_simple(roundcube_script, "new-rule", "INBOX.New")

    reparse(merged)

    assert rule_names(parse_script(merged))[-1] == "new-rule"


# ----------------------------------------------------------------------------
def test_merge_does_not_reorder_the_existing_rules(roundcube_script):
    """Sieve is order-sensitive: the first matching rule with ``stop`` wins.

    Reordering would silently change which rule fires for a message that
    two rules both match, so appending at the end is part of the contract,
    not an implementation detail.
    """
    merged = merge_simple(roundcube_script, "new-rule", "INBOX.New")

    boss = merged.index("boss@example.com")
    noise = merged.index("newsletter")
    new = merged.index("INBOX.New")

    assert boss < noise < new


# ----------------------------------------------------------------------------
def test_merge_keeps_the_require_line_correct(roundcube_script):
    """The union of every rule's needs, not just the new rule's.

    ``require`` is re-derived on each render. Losing an extension another
    rule depends on makes the whole script fail to load server-side.
    """
    merged = merge_simple(roundcube_script, "new-rule", "INBOX.New")
    require_line = merged.splitlines()[0]

    assert "fileinto" in require_line
    assert "imap4flags" in require_line


# ############################################################################
# Foreign rule identity
# ############################################################################


# ----------------------------------------------------------------------------
def test_merge_preserves_a_roundcube_rule_name(roundcube_script):
    """Rule *names* are part of what has to survive, not just bodies.

    Roundcube's UI keys on ``# rule:[name]``; after one merge the user's
    filter list shows 'Unnamed rule 1'. Worse, the name is what mailctl
    itself uses for identity, so the rename also breaks --replace and
    remove-rule against that rule (see the next test).
    """
    merged = merge_simple(roundcube_script, "new-rule", "INBOX.New")

    assert "keep-boss" in rule_names(parse_script(merged))


# ----------------------------------------------------------------------------
def test_replace_updates_a_roundcube_named_rule_in_place(roundcube_script):
    """The silent half of the identity defect.

    ``--replace`` on a name Roundcube wrote used to report success and leave
    two rules matching the same mail. The earlier one carries ``stop``, so
    the replacement never fired and the user saw a command that "worked" and
    changed nothing.
    """
    merged = merge_simple(
        roundcube_script, "keep-boss", "INBOX.Elsewhere", replace=True
    )

    assert merged.count("# rule:[") == 2
    assert 'fileinto "INBOX.Boss";' not in merged
    assert 'fileinto "INBOX.Elsewhere";' in merged


# ############################################################################
# Name-marker translation
# ############################################################################


# ----------------------------------------------------------------------------
def test_render_writes_roundcube_name_markers(roundcube_script):
    """The panel is the other editor of this file, so it sets the dialect.

    Emitting sievelib's ``# Filter:`` would leave every rule mailctl writes
    nameless in the webmail UI -- the same identity loss as the parse bug,
    pointed the other way.
    """
    merged = merge_simple(roundcube_script, "new-rule", "INBOX.New")

    assert "# rule:[new-rule]" in merged
    assert "# rule:[keep-boss]" in merged
    assert "# Filter:" not in merged


# ----------------------------------------------------------------------------
def test_names_do_not_drift_over_two_render_cycles(roundcube_script):
    """Translation has to be a fixed point, not a one-way conversion.

    Every run re-parses what the previous run rendered, so a name that
    shifts by one cycle -- gaining a bracket, losing a prefix -- diverges a
    little further on each ``mailctl add`` until identity is lost anyway.
    """
    first = render_script(parse_script(roundcube_script))
    second = render_script(parse_script(first))
    third = render_script(parse_script(second))

    assert second == first
    assert third == first
    assert rule_names(parse_script(third)) == ["keep-boss", "bin-the-noise"]


# ----------------------------------------------------------------------------
def test_both_dialects_are_read_from_one_script(reparse):
    """A script mailctl and Roundcube have both edited carries both forms."""
    script = (
        'require ["fileinto"];\n'
        "# rule:[from-the-panel]\n"
        'if header :contains "from" "a@example.com" {\n'
        '    fileinto "INBOX.A";\n'
        "}\n"
        "# Filter: from-mailctl\n"
        'if header :contains "from" "b@example.com" {\n'
        '    fileinto "INBOX.B";\n'
        "}\n"
    )

    reparse(script)

    assert rule_names(parse_script(script)) == [
        "from-the-panel",
        "from-mailctl",
    ]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        pytest.param("# rule:[plain]", "plain", id="one-space"),
        pytest.param("#rule:[tight]", "tight", id="no-space"),
        pytest.param("#   rule:[roomy]", "roomy", id="several-spaces"),
        pytest.param("#\trule:[tabbed]", "tabbed", id="tab"),
        pytest.param("# rule:[trailing]   ", "trailing", id="trailing-space"),
        pytest.param("# rule:[a]b]", "a]b", id="bracket-in-name"),
        pytest.param("# rule:[rule:[z]]", "rule:[z]", id="marker-in-name"),
        pytest.param("# rule:[Filter: x]", "Filter: x", id="other-dialect"),
        pytest.param("# rule:[naïve]", "naïve", id="non-ascii"),
    ],
)
def test_a_name_marker_variant_is_read_and_round_trips(marker, expected):
    """The name has to survive whatever Roundcube or a human wrote.

    A ``]`` inside the name is the interesting one: reading has to run to
    the *last* bracket, or the name comes back truncated and the rule
    answers to a name nobody typed. The non-ASCII case pins the byte-offset
    arithmetic the rewrite splices on.
    """
    script = (
        'require ["fileinto"];\n'
        f"{marker}\n"
        'if header :contains "from" "a@example.com" {\n'
        '    fileinto "INBOX.A";\n'
        "}\n"
    )

    assert rule_names(parse_script(script)) == [expected]

    rendered = render_script(parse_script(script))

    assert rule_names(parse_script(rendered)) == [expected]


# ----------------------------------------------------------------------------
def test_a_rule_with_no_name_marker_stays_unnamed():
    """Inventing a name for an unnamed rule would be a different bug."""
    script = (
        'require ["fileinto"];\n'
        "# just a note, not a name\n"
        'if header :contains "from" "a@example.com" {\n'
        '    fileinto "INBOX.A";\n'
        "}\n"
    )

    assert rule_names(parse_script(script)) == ["Unnamed rule 1"]


# ----------------------------------------------------------------------------
def test_a_crlf_script_keeps_its_names(roundcube_script, reparse):
    """ManageSieve is a CRLF protocol, so this is the wire form.

    The lexer hands the carriage return over as part of the comment, so a
    rewrite that forgets it either drops the line ending or reads the name
    as ``keep-boss\\r``.
    """
    script = roundcube_script.replace("\n", "\r\n")

    reparse(script)

    assert rule_names(parse_script(script)) == ["keep-boss", "bin-the-noise"]


# ----------------------------------------------------------------------------
NOT_A_MARKER = [
    pytest.param(
        'if header :contains "subject" "# rule:[in-a-string]" {\n'
        '    fileinto "INBOX.A";\n'
        "}\n",
        id="string-literal",
    ),
    pytest.param(
        "# a note about # rule:[mid-comment] and what it does\n"
        'if header :contains "from" "a@example.com" {\n'
        '    fileinto "INBOX.A";\n'
        "}\n",
        id="comment-body",
    ),
    pytest.param(
        "/* # rule:[in-a-bracket-comment] */\n"
        'if header :contains "from" "a@example.com" {\n'
        '    fileinto "INBOX.A";\n'
        "}\n",
        id="bracket-comment",
    ),
]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("body", NOT_A_MARKER)
def test_a_marker_that_is_not_a_marker_names_nothing(body, reparse):
    """Only a comment that *is* the marker names a rule.

    Reading the name off any line that happens to contain the text would
    rename a rule from inside its own subject test -- and, worse, rewrite
    the user's string when the script is rendered back.
    """
    script = f'require ["fileinto"];\n{body}'

    reparse(script)

    assert rule_names(parse_script(script)) == ["Unnamed rule 1"]


# ----------------------------------------------------------------------------
def test_a_marker_inside_a_multiline_block_is_left_verbatim(reparse):
    """A ``text:`` block is message content, not script structure."""
    script = (
        'require ["reject"];\n'
        "# rule:[bounce-it]\n"
        'if header :contains "subject" "x" {\n'
        "    reject text:\n"
        "# rule:[part-of-the-message]\n"
        ".\n"
        ";\n"
        "}\n"
    )

    assert rule_names(parse_script(script)) == ["bounce-it"]

    rendered = render_script(parse_script(script))

    reparse(rendered)

    assert "# rule:[part-of-the-message]\n" in rendered
    assert rule_names(parse_script(rendered)) == ["bounce-it"]


# ----------------------------------------------------------------------------
def test_a_roundcube_name_collides_without_replace(roundcube_script):
    """Collision detection is the same identity, seen from the add path.

    Before the name survived parsing this raised nothing and appended a
    second rule -- the silent failure, since the first one carries ``stop``.
    """
    with pytest.raises(MailctlError, match=r"already exists.*--replace"):
        merge_simple(roundcube_script, "keep-boss", "INBOX.Elsewhere")


# ----------------------------------------------------------------------------
def test_a_roundcube_named_rule_can_be_removed(roundcube_script, reparse):
    """``remove-rule keep-boss`` used to fail with 'Known rules: Unnamed…'."""
    after = remove_rule(roundcube_script, "keep-boss")

    reparse(after)

    assert rule_names(parse_script(after)) == ["bin-the-noise"]
    assert 'fileinto "INBOX.Boss";' not in after
    assert 'fileinto "INBOX.Noise";' in after


# ----------------------------------------------------------------------------
def test_a_misplaced_comment_offset_fails_loudly(
    monkeypatch, roundcube_script
):
    """The name rewrite splices bytes, so a wrong offset corrupts a script.

    The offsets come from sievelib's lexer. If a future version moved them,
    silently rewriting the wrong bytes would be worse than any name loss --
    so the splice is checked and the failure is named, the same posture as
    ADR 0002's parse hard stop.
    """

    class DriftingLexer:
        """A lexer that reports an offset the token is not actually at."""

        def __init__(self, definitions):
            self.pos = 0

        def scan(self, text):
            self.pos = 1

            yield ("hash_comment", b"# rule:[somewhere-else]")

    monkeypatch.setattr(script_module.parser, "Lexer", DriftingLexer)

    with pytest.raises(MailctlError, match="version mismatch"):
        parse_script(roundcube_script)


# ----------------------------------------------------------------------------
def test_a_roundcube_name_survives_repeated_merges(roundcube_script):
    """The defect only showed up on the *first* merge; prove it stays fixed."""
    script = roundcube_script

    for index in range(3):
        script = merge_simple(script, f"added-{index}", f"INBOX.Add{index}")

    assert rule_names(parse_script(script)) == [
        "keep-boss",
        "bin-the-noise",
        "added-0",
        "added-1",
        "added-2",
    ]


# ############################################################################
# Parse failure is a hard stop
# ############################################################################

UNPARSEABLE = [
    pytest.param("this is not sieve at all ;;;", id="not-sieve"),
    pytest.param(
        'if header :contains "from" "x" {\n    fileinto "A";\n',
        id="unterminated-block",
    ),
    pytest.param(
        'if header :contains "from" "x" {\n    fileinto "A";\n}\n',
        id="missing-require",
    ),
    pytest.param(
        'if header :contains "from" {\n    stop;\n}\n', id="malformed-test"
    ),
]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("script", UNPARSEABLE)
def test_merge_refuses_an_unparseable_script(script):
    """ADR 0002's load-bearing clause: never fall back to overwriting.

    The tempting recovery from "sievelib cannot parse this" is "then just
    write ours", which converts a visible error into exactly the silent
    data loss the merge exists to prevent -- and does so precisely when the
    script is unusual, hand-written, and most valuable.
    """
    with pytest.raises(MailctlError, match="could not be parsed"):
        merge_simple(script, "new-rule", "INBOX.New")


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("script", UNPARSEABLE)
def test_remove_refuses_an_unparseable_script(script):
    """The same hard stop on the removal path, which also re-renders."""
    with pytest.raises(MailctlError, match="could not be parsed"):
        remove_rule(script, "anything")


# ----------------------------------------------------------------------------
def test_the_parse_failure_names_the_reason():
    """A hard stop the user cannot act on is only half a hard stop.

    The message has to carry sievelib's own diagnostic, or the user is
    told their script is unparseable with no way to find out why.
    """
    with pytest.raises(MailctlError, match=r"line 1.*unknown command"):
        parse_script("garbage garbage;")


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param("   \n\n\t", id="whitespace-only"),
    ],
)
def test_parse_treats_an_absent_script_as_an_empty_set(text):
    """An account with no filters yet is a normal starting state."""
    assert rule_names(parse_script(text)) == []


# ############################################################################
# Merge and replace
# ############################################################################


# ----------------------------------------------------------------------------
def test_merge_into_an_empty_script_yields_one_rule(reparse):
    merged = merge_simple("", "first", "INBOX.First")

    reparse(merged)

    assert rule_names(parse_script(merged)) == ["first"]


# ----------------------------------------------------------------------------
def test_merge_refuses_a_duplicate_name_without_replace():
    """Silently overwriting a rule the user named is a data loss too."""
    existing = merge_simple("", "shared", "INBOX.First")

    with pytest.raises(MailctlError, match=r"already exists.*--replace"):
        merge_simple(existing, "shared", "INBOX.Second")


# ----------------------------------------------------------------------------
def test_replace_updates_the_named_rule_and_leaves_the_others(reparse):
    existing = merge_simple("", "keeper", "INBOX.Keeper")
    existing = merge_simple(existing, "target", "INBOX.Old")

    merged = merge_simple(existing, "target", "INBOX.New", replace=True)

    reparse(merged)

    assert rule_names(parse_script(merged)) == ["keeper", "target"]
    assert 'fileinto "INBOX.Keeper";' in merged
    assert 'fileinto "INBOX.New";' in merged
    assert 'fileinto "INBOX.Old";' not in merged


# ############################################################################
# Placement
# ############################################################################


# ----------------------------------------------------------------------------
def three_rules() -> str:
    """Return a script holding 'one', 'two', 'three', in that order."""
    script = merge_simple("", "one", "INBOX.One")
    script = merge_simple(script, "two", "INBOX.Two")

    return merge_simple(script, "three", "INBOX.Three")


# ----------------------------------------------------------------------------
def test_no_placement_flag_still_appends(reparse):
    """The default is unchanged, and that is the point of pinning it.

    Every mailctl version before the placement flags appended, and the
    flags were added to make position sayable rather than to change what
    happens when nobody says anything. A default that quietly moved would
    reorder scripts on accounts whose owner never asked for any of this.
    """
    merged = merge_simple(three_rules(), "new", "INBOX.New")

    reparse(merged)

    assert rule_names(parse_script(merged)) == ["one", "two", "three", "new"]


# ----------------------------------------------------------------------------
def test_first_puts_the_rule_ahead_of_everything(reparse):
    """``--first`` is the flag with teeth: it can starve working rules.

    Order is read back out of the rendered script rather than off the
    filter set, because what the server runs is the text -- a reorder that
    only happened in memory would pass an internals check and change
    nothing about the mail.
    """
    merged = merge_simple(
        three_rules(), "new", "INBOX.New", placement=Placement(PLACE_FIRST)
    )

    reparse(merged)

    assert rule_names(parse_script(merged)) == ["new", "one", "two", "three"]


# ----------------------------------------------------------------------------
def test_last_is_spelled_out_and_appends(reparse):
    """``--last`` says explicitly what omitting every flag does implicitly.

    It earns its place by being sayable -- and, under ``--replace``, by
    being the only way to move a rule to the end (see the replace tests
    below).
    """
    merged = merge_simple(
        three_rules(), "new", "INBOX.New", placement=Placement(PLACE_LAST)
    )

    reparse(merged)

    assert rule_names(parse_script(merged)) == ["one", "two", "three", "new"]


# ----------------------------------------------------------------------------
def test_before_lands_immediately_ahead_of_the_named_rule(reparse):
    """Immediately before, not merely somewhere earlier.

    Sieve stops at the first rule that matches and says ``stop``, so "ahead
    of 'two'" and "ahead of 'three'" are different filings for any message
    both rules match.
    """
    merged = merge_simple(
        three_rules(),
        "new",
        "INBOX.New",
        placement=Placement(PLACE_BEFORE, "two"),
    )

    reparse(merged)

    assert rule_names(parse_script(merged)) == ["one", "new", "two", "three"]


# ----------------------------------------------------------------------------
def test_after_lands_immediately_behind_the_named_rule(reparse):
    """The mirror of ``--before``, including at the end of the script."""
    merged = merge_simple(
        three_rules(),
        "new",
        "INBOX.New",
        placement=Placement(PLACE_AFTER, "three"),
    )

    reparse(merged)

    assert rule_names(parse_script(merged)) == ["one", "two", "three", "new"]


# ----------------------------------------------------------------------------
def test_placement_into_an_empty_script_works():
    """A first rule has nothing to be relative to, and that is not an error.

    An empty script is the ordinary starting state (``parse_script`` treats
    it as one), so a user who always passes ``--first`` out of habit must
    not be stopped on the run that creates the script.
    """
    for placement in (Placement(PLACE_FIRST), Placement(PLACE_LAST)):
        merged = merge_simple("", "only", "INBOX.Only", placement=placement)

        assert rule_names(parse_script(merged)) == ["only"]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("where", [PLACE_BEFORE, PLACE_AFTER])
def test_an_unknown_anchor_is_refused_and_the_known_names_listed(where):
    """A mistyped anchor is a typo, and appending anyway would hide it.

    The message lists what the script does contain, because the whole
    reason to name an anchor is that the user is holding a mental model of
    the script -- and a mismatch means that model is wrong somewhere.
    """
    with pytest.raises(MailctlError) as raised:
        merge_simple(
            three_rules(),
            "new",
            "INBOX.New",
            placement=Placement(where, "typo"),
        )

    message = str(raised.value)

    assert "'typo'" in message
    assert f"--{where}" in message
    assert "one, two, three" in message


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("where", [PLACE_BEFORE, PLACE_AFTER])
def test_a_rule_cannot_be_placed_relative_to_itself(where):
    """``--before`` naming the rule being added has no coherent meaning.

    Resolving it would need the rule's position to already exist in order
    to work out its position, and the realistic cause is a user who meant
    to name a different rule.
    """
    with pytest.raises(MailctlError, match="names the rule being added"):
        merge_simple(
            three_rules(),
            "two",
            "INBOX.New",
            replace=True,
            placement=Placement(where, "two"),
        )


# ----------------------------------------------------------------------------
def test_replace_without_a_placement_flag_leaves_the_rule_where_it_was():
    """The behaviour ``sievelib.updatefilter`` was chosen for.

    Updating a rule in place is what keeps a ``--replace`` from silently
    refiling mail that two rules both match, so no-flag replace must not
    become a remove-and-append.
    """
    merged = merge_simple(three_rules(), "two", "INBOX.Changed", replace=True)

    assert rule_names(parse_script(merged)) == ["one", "two", "three"]
    assert 'fileinto "INBOX.Changed";' in merged


# ----------------------------------------------------------------------------
def test_replace_with_a_placement_flag_moves_the_rule(reparse):
    """An explicit placement is an instruction, even on an existing rule.

    Honouring it for a new rule and ignoring it for a replacement would be
    a command that reports success and changes nothing -- the exact
    silent-success failure the placement work exists to remove, so it must
    not be reintroduced here.
    """
    merged = merge_simple(
        three_rules(),
        "three",
        "INBOX.Changed",
        replace=True,
        placement=Placement(PLACE_FIRST),
    )

    reparse(merged)

    assert rule_names(parse_script(merged)) == ["three", "one", "two"]
    assert 'fileinto "INBOX.Changed";' in merged


# ----------------------------------------------------------------------------
def test_replace_last_moves_the_rule_to_the_end(reparse):
    """``--last`` is a placement, not a synonym for saying nothing.

    Omitting every flag means "leave an existing rule alone"; saying
    ``--last`` means "put it at the end". They coincide for a new rule,
    which is the common case, and they must not coincide here -- otherwise
    there is no way to demote a rule at all.
    """
    merged = merge_simple(
        three_rules(),
        "one",
        "INBOX.Changed",
        replace=True,
        placement=Placement(PLACE_LAST),
    )

    reparse(merged)

    assert rule_names(parse_script(merged)) == ["two", "three", "one"]


# ----------------------------------------------------------------------------
def test_reordering_keeps_every_pre_existing_rule_verbatim(
    roundcube_script, reparse
):
    """ADR 0002's invariant, against the newest way to break it.

    A reorder rewrites the list the rules live in, which is a fresh chance
    to drop one of the hand-made rules the user wrote in Roundcube. The
    check is the same as the merge guard's -- bodies, actions, names -- plus
    the one thing only a reorder can get wrong: the *relative* order of the
    rules that were not asked to move.
    """
    merged = merge_simple(
        roundcube_script,
        "new-rule",
        "INBOX.New",
        placement=Placement(PLACE_FIRST),
    )

    reparse(merged)

    assert rule_names(parse_script(merged)) == [
        "new-rule",
        "keep-boss",
        "bin-the-noise",
    ]

    assert 'header :contains "from" "boss@example.com"' in merged
    assert 'header :contains "subject" "newsletter"' in merged
    assert 'fileinto "INBOX.Boss";' in merged
    assert 'setflag "\\\\Flagged";' in merged
    assert 'fileinto "INBOX.Noise";' in merged


# ############################################################################
# Placement against a duplicated name
# ############################################################################
#
# `resolve_position` answers with an index into the script *minus the copy
# being replaced*, and `_move_rule` pops exactly one entry. When a name
# appears more than once those two lists have to stay the same length, and
# the count of duplicates is what the arithmetic is measured in -- so two
# copies is not the general case, it is the smallest one.


# ----------------------------------------------------------------------------
def duplicated_script(copies: int) -> str:
    """Return a script whose name ``Dup`` appears ``copies`` times.

    Every marker is spelled differently and every one parses to ``Dup``:
    the marker pattern tolerates any run of whitespace after the ``#``,
    and trailing space inside the brackets is stripped off the name. So a
    user editing the panel's own output can produce these by hand -- which
    is why placement has to survive them rather than assume them away.

    ``Mid`` and ``Tail`` are the distinct rules an anchor can be named on.
    From two copies up a surviving duplicate sits between them, which is
    what makes anchoring on ``Tail`` resolve correctly only if that
    duplicate was counted.
    """
    order = ["Dup", "Mid"]

    if copies >= 2:
        order.append("Dup")

    order.append("Tail")
    order += ["Dup"] * max(0, copies - 2)

    blocks = ['require ["fileinto"];']
    seen = 0

    for position, name in enumerate(order):
        if name == "Dup":
            seen += 1
            marker = f"#{' ' * seen}rule:[Dup{' ' * (seen - 1)}]"
            folder = f"INBOX.Dup{seen}"

        else:
            marker = f"# rule:[{name}]"
            folder = f"INBOX.{name}"

        blocks.append(
            f'{marker}\nif header :contains "to" "{position}@example.com"\n'
            f'{{\n\tfileinto "{folder}";\n\tstop;\n}}'
        )

    return "\n".join(blocks) + "\n"


# ----------------------------------------------------------------------------
def filed_folders(text: str) -> list[str]:
    """Return the folders the rendered script files into, in rule order.

    ``rule_names`` cannot answer "which copy moved" when three rules read
    as one name, so the folder is what identifies them. It is also what
    the server actually acts on.
    """
    return [
        line.strip()[len('fileinto "') : -len('";')]
        for line in text.splitlines()
        if line.strip().startswith("fileinto ")
    ]


# ----------------------------------------------------------------------------
def test_three_markers_can_parse_to_one_rule_name():
    """The premise of everything below, pinned rather than assumed.

    If sievelib ever stopped folding these three spellings together the
    tests after this one would keep passing while testing nothing, because
    a script with three distinct names exercises none of the arithmetic.
    """
    assert rule_names(parse_script(duplicated_script(3))) == [
        "Dup",
        "Mid",
        "Dup",
        "Tail",
        "Dup",
    ]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("placement", "expected"),
    [
        # No flag: the replaced copy is the first one, and it stays where
        # it was -- a duplicate elsewhere in the script is not a reason to
        # shuffle a rule the user only asked to edit.
        (None, "Changed Mid Dup2 Tail Dup3"),
        (Placement(PLACE_FIRST), "Changed Mid Dup2 Tail Dup3"),
        # The regression: behind BOTH surviving duplicates, not one slot
        # short per copy.
        (Placement(PLACE_LAST), "Mid Dup2 Tail Dup3 Changed"),
        (Placement(PLACE_BEFORE, "Mid"), "Changed Mid Dup2 Tail Dup3"),
        (Placement(PLACE_AFTER, "Mid"), "Mid Changed Dup2 Tail Dup3"),
        # These two anchor across a surviving duplicate, so "immediately"
        # is only true if that duplicate was counted.
        (Placement(PLACE_BEFORE, "Tail"), "Mid Dup2 Changed Tail Dup3"),
        (Placement(PLACE_AFTER, "Tail"), "Mid Dup2 Tail Changed Dup3"),
    ],
)
def test_every_flag_places_correctly_among_three_same_named_rules(
    placement, expected, reparse
):
    """Every flag, against the shape that breaks a two-rule fix.

    The full order is asserted, not just where the moved rule ended up,
    because the two ways to be wrong here look nothing alike: a miscount
    files the rule short of where it was asked for, and a mismatch between
    the resolver's list and the reorder's list loses or duplicates a rule
    outright (ADR 0002).

    The folders identify the copies -- all three rules read as ``Dup``, so
    an order assertion on the names could not tell which one moved, or
    notice if the wrong one had been rewritten.
    """
    merged = merge_simple(
        duplicated_script(3),
        "Dup",
        "INBOX.Changed",
        replace=True,
        placement=placement,
    )

    reparse(merged)

    assert filed_folders(merged) == [
        f"INBOX.{name}" for name in expected.split()
    ]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("copies", [1, 2, 3, 4, 5])
def test_last_stays_last_however_many_copies_of_the_name_there_are(
    copies, reparse
):
    """``--last`` means last, and the duplicate count must not enter into it.

    The defect this pins was arithmetic: the resolver measured its index
    against a list with *every* same-named rule removed while the reorder
    removed one, so the rule landed one slot short of the end per surviving
    duplicate. One copy hid it entirely and two made it look like an
    off-by-one, so the count is swept.

    It matters because these rules carry ``stop``. Demoting a broad rule so
    a narrow one can run is the whole reason to reach for ``--last``, and
    landing in front of a copy of itself leaves the narrow rule still
    starved -- with a success message.
    """
    merged = merge_simple(
        duplicated_script(copies),
        "Dup",
        "INBOX.Changed",
        replace=True,
        placement=Placement(PLACE_LAST),
    )

    reparse(merged)

    folders = filed_folders(merged)

    assert folders[-1] == "INBOX.Changed"
    assert len(folders) == copies + 2


# ############################################################################
# Removal
# ############################################################################


# ----------------------------------------------------------------------------
def test_remove_takes_out_only_the_named_rule(reparse):
    """The survivors must be identical in meaning, not merely present.

    Comparing against the same script rendered from scratch with only the
    surviving rule is a stronger check than substring assertions: it
    catches a changed comparator, a dropped action, or a mangled require
    line, all of which would leave the substrings intact.
    """
    existing = merge_simple("", "doomed", "INBOX.Doomed")
    existing = merge_simple(existing, "survivor", "INBOX.Survivor")

    after = remove_rule(existing, "doomed")
    expected = merge_simple("", "survivor", "INBOX.Survivor")

    reparse(after)

    assert after == expected


# ----------------------------------------------------------------------------
def test_remove_the_last_rule_leaves_a_parseable_script():
    existing = merge_simple("", "only", "INBOX.Only")

    after = remove_rule(existing, "only")

    assert rule_names(parse_script(after)) == []


# ----------------------------------------------------------------------------
def test_remove_an_unknown_rule_raises_and_lists_the_real_names():
    """The failure has to be actionable, since rule names are discovered."""
    existing = merge_simple("", "real-one", "INBOX.Real")

    with pytest.raises(MailctlError, match=r"no rule named 'ghost'"):
        remove_rule(existing, "ghost")

    with pytest.raises(MailctlError, match=r"Known rules: real-one"):
        remove_rule(existing, "ghost")


# ----------------------------------------------------------------------------
def test_remove_from_an_empty_script_says_none_are_known():
    with pytest.raises(MailctlError, match=r"Known rules: \(none\)"):
        remove_rule("", "ghost")


# ############################################################################
# String escaping
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        pytest.param("\\Seen", "\\\\Seen", id="imap-flag"),
        pytest.param('say "hi"', 'say \\"hi\\"', id="double-quote"),
        pytest.param("C:\\path", "C:\\\\path", id="backslash-mid-value"),
        pytest.param("plain", "plain", id="nothing-to-escape"),
    ],
)
def test_escape_sieve_string(raw, escaped):
    assert escape_sieve_string(raw) == escaped


# ----------------------------------------------------------------------------
def test_a_flag_survives_the_round_trip_as_two_backslashes(reparse):
    """``--flag '\\Seen'`` must reach the server as the flag the user typed.

    RFC 5228 quoted strings escape backslash, and sievelib quotes without
    escaping -- so an unescaped value is emitted as ``"\\Seen"``, which the
    server reads back as the flag ``Seen``. That flag does not exist, so
    the rule quietly does nothing.
    """
    criteria = Criteria()
    criteria.add("subject", "anything")

    merged = merge_rule(
        "",
        "flagger",
        criteria.sieve_conditions(),
        [("addflag", escape_sieve_string("\\Seen")), ("stop",)],
        criteria.sieve_matchtype(),
    )

    assert 'addflag "\\\\Seen";' in merged

    reparse(merged)


# ----------------------------------------------------------------------------
def test_a_quoted_value_survives_the_round_trip(reparse):
    criteria = Criteria()
    criteria.add("subject", 'the "quoted" one')

    merged = merge_rule(
        "",
        "quoted",
        criteria.sieve_conditions(),
        [("stop",)],
        criteria.sieve_matchtype(),
    )

    assert 'the \\"quoted\\" one' in merged

    reparse(merged)


# ############################################################################
# Free-standing comments (#7)
# ############################################################################

# A Roundcube-shaped script carrying the comments a person writes by hand:
# one above the require line, a two-line note above a rule, one inside a
# rule's body, and one after the last rule. Before #7 every one of them was
# dropped by the first merge.
COMMENTED_SCRIPT = """# my filters -- edit in Roundcube or mailctl
require ["fileinto","imap4flags"];
# this one is for the accountant
# (she asked for it on 2026-01-05)
# rule:[keep-boss]
if header :contains "from" "boss@example.com"
{
\t# flag it so it shows on the phone
\tfileinto "INBOX.Boss";
\tsetflag "\\\\Flagged";
\tstop;
}
# rule:[bin-the-noise]
if header :contains "subject" "newsletter"
{
\tfileinto "INBOX.Noise";
\tstop;
}
# end of hand-made rules
"""

# COMMENTED_SCRIPT as mailctl renders it: each comment a rule owns sits
# directly above that rule's name marker, the body comment hoisted with
# them, and the header and trailer kept where they were.
COMMENTED_RENDERED = """# my filters -- edit in Roundcube or mailctl
require ["fileinto", "imap4flags"];

# this one is for the accountant
# (she asked for it on 2026-01-05)
# flag it so it shows on the phone
# rule:[keep-boss]
if header :contains "from" "boss@example.com" {
    fileinto "INBOX.Boss";
    setflag "\\\\Flagged";
    stop;
}
# rule:[bin-the-noise]
if header :contains "subject" "newsletter" {
    fileinto "INBOX.Noise";
    stop;
}
# end of hand-made rules
"""


# ----------------------------------------------------------------------------
def test_a_comment_above_a_rule_survives_a_merge(reparse):
    """The fixture from #7: a person's note must outlive ``mailctl add``."""
    merged = merge_simple(COMMENTED_SCRIPT, "new-rule", "INBOX.New")

    assert "# this one is for the accountant\n" in merged
    assert "# (she asked for it on 2026-01-05)\n" in merged

    reparse(merged)


# ----------------------------------------------------------------------------
def test_a_multi_line_comment_stays_in_order_above_its_rule():
    merged = merge_simple(COMMENTED_SCRIPT, "new-rule", "INBOX.New")

    assert (
        "# this one is for the accountant\n"
        "# (she asked for it on 2026-01-05)\n"
        "# flag it so it shows on the phone\n"
        "# rule:[keep-boss]\n"
    ) in merged


# ----------------------------------------------------------------------------
def test_the_header_and_trailing_comments_survive_a_merge():
    """No rule follows the trailer, so it is kept at the end of the script.

    The new rule is appended, so it lands between the last hand-made rule
    and the trailer -- the trailer is the script's, not a rule's.
    """
    merged = merge_simple(COMMENTED_SCRIPT, "new-rule", "INBOX.New")

    assert merged.startswith("# my filters -- edit in Roundcube or mailctl\n")
    assert merged.endswith("}\n# end of hand-made rules\n")
    assert merged.count("# end of hand-made rules") == 1


# ----------------------------------------------------------------------------
def test_a_commented_script_renders_to_the_expected_form():
    assert render_script(parse_script(COMMENTED_SCRIPT)) == COMMENTED_RENDERED


# ----------------------------------------------------------------------------
def test_a_commented_script_is_a_fixed_point_of_render(reparse):
    """Nothing is duplicated or drifts on the next run's re-parse."""
    assert (
        render_script(parse_script(COMMENTED_RENDERED)) == COMMENTED_RENDERED
    )

    reparse(COMMENTED_RENDERED)


# ----------------------------------------------------------------------------
def test_a_rendered_commented_script_shows_no_reformatting():
    """The ``add --dry-run`` diff no longer shows the comments removed."""
    merged = merge_simple(COMMENTED_RENDERED, "new-rule", "INBOX.New")
    diff = display_diff(COMMENTED_RENDERED, merged)

    assert not diff.reformats
    assert "\n-#" not in diff.text


# ----------------------------------------------------------------------------
def test_a_roundcube_script_without_comments_renders_as_before(
    roundcube_script,
):
    """The comment handling adds nothing to a script that has none."""
    assert render_script(parse_script(roundcube_script)) == (
        'require ["fileinto", "imap4flags"];\n'
        "\n"
        "# rule:[keep-boss]\n"
        'if header :contains "from" "boss@example.com" {\n'
        '    fileinto "INBOX.Boss";\n'
        '    setflag "\\\\Flagged";\n'
        "    stop;\n"
        "}\n"
        "# rule:[bin-the-noise]\n"
        'if header :contains "subject" "newsletter" {\n'
        '    fileinto "INBOX.Noise";\n'
        "    stop;\n"
        "}\n"
    )


# ----------------------------------------------------------------------------
def test_a_moved_rule_carries_its_comments_with_it():
    moved = move_rule(COMMENTED_SCRIPT, "keep-boss", Placement(PLACE_LAST))

    assert moved.index("# rule:[bin-the-noise]") < moved.index(
        "# this one is for the accountant"
    )
    assert (
        "# this one is for the accountant\n"
        "# (she asked for it on 2026-01-05)\n"
        "# flag it so it shows on the phone\n"
        "# rule:[keep-boss]\n"
    ) in moved
    assert moved.endswith("}\n# end of hand-made rules\n")


# ----------------------------------------------------------------------------
def test_a_replaced_rule_keeps_its_comments():
    """``--replace`` changes what the rule does, not what it is for."""
    merged = merge_simple(
        COMMENTED_SCRIPT, "keep-boss", "INBOX.Boss2", replace=True
    )

    assert (
        "# this one is for the accountant\n"
        "# (she asked for it on 2026-01-05)\n"
        "# flag it so it shows on the phone\n"
        "# rule:[keep-boss]\n"
    ) in merged


# ----------------------------------------------------------------------------
def test_a_removed_rule_takes_its_comments_with_it(reparse):
    """A comment above a rule is about that rule, so it goes with it.

    Every other comment -- the header, the trailer, and the notes on the
    rules that stay -- is left where it was.
    """
    removed = remove_rule(COMMENTED_SCRIPT, "keep-boss")

    assert "accountant" not in removed
    assert "on the phone" not in removed
    assert removed.startswith("# my filters -- edit in Roundcube or mailctl\n")
    assert removed.endswith("}\n# end of hand-made rules\n")

    reparse(removed)


# A Roundcube-shaped script with one rule switched off in the webmail UI.
# Roundcube disables a rule by writing ``if false # <its test>`` and reads it
# back as disabled only while that comment follows ``false`` on the same
# line; moved anywhere else the rule stops showing as disabled and its test
# is orphaned.
DISABLED_SCRIPT = """require ["fileinto"];
# rule:[paused]
if false # anyof (header :contains "subject" "invoice")
{
\tfileinto "INBOX.Bills";
\tstop;
}
# rule:[bin-the-noise]
if header :contains "subject" "newsletter"
{
\tfileinto "INBOX.Noise";
\tstop;
}
"""

DISABLED_RENDERED = """require ["fileinto"];

# rule:[paused]
if false # anyof (header :contains "subject" "invoice")
{
    fileinto "INBOX.Bills";
    stop;
}
# rule:[bin-the-noise]
if header :contains "subject" "newsletter" {
    fileinto "INBOX.Noise";
    stop;
}
"""

# What Roundcube's managesieve plugin matches, right after ``if``, to decide
# a rule is disabled (rcube_sieve_script.php).
ROUNDCUBE_DISABLED = re.compile(r"^\s*false\s+#\s*", re.IGNORECASE)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_a_disabled_rule_keeps_its_test_inline_through_a_merge(
    newline, reparse
):
    merged = merge_simple(
        DISABLED_SCRIPT.replace("\n", newline), "new-rule", "INBOX.New"
    )

    assert merged.startswith(DISABLED_RENDERED)
    assert merged.count("invoice") == 1

    reparse(merged)


# ----------------------------------------------------------------------------
def test_a_disabled_rule_still_reads_as_disabled_to_roundcube():
    rendered = render_script(parse_script(DISABLED_SCRIPT))
    after_if = rendered.split("# rule:[paused]\nif", 1)[1]

    assert ROUNDCUBE_DISABLED.match(after_if)


# ----------------------------------------------------------------------------
def test_a_disabled_rule_is_a_fixed_point_of_render():
    assert render_script(parse_script(DISABLED_RENDERED)) == DISABLED_RENDERED


# ----------------------------------------------------------------------------
def test_a_moved_disabled_rule_keeps_its_test_inline():
    moved = move_rule(DISABLED_SCRIPT, "paused", Placement(PLACE_LAST))

    assert (
        "# rule:[paused]\n"
        'if false # anyof (header :contains "subject" "invoice")\n'
        "{\n"
    ) in moved
    assert moved.index("# rule:[bin-the-noise]") < moved.index(
        "# rule:[paused]"
    )


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_a_replaced_disabled_rule_drops_the_stale_test(newline):
    """The comment describes the old test, so it does not outlive it."""
    merged = merge_simple(
        DISABLED_SCRIPT.replace("\n", newline),
        "paused",
        "INBOX.Other",
        replace=True,
    )

    assert "invoice" not in merged
    assert 'fileinto "INBOX.Bills"' not in merged


# The paused rule after `add --replace` gives it merge_simple's test and
# body: still disabled, in Roundcube's form, with the new test after
# `false` (#168).
REPLACED_DISABLED = (
    "# rule:[paused]\n"
    'if false # anyof (header :contains "From" "a@example.com")\n'
    "{\n"
    '    fileinto "INBOX.Other";\n'
    "    stop;\n"
    "}\n"
    "# rule:[bin-the-noise]\n"
)

# The same paused rule as sievelib's own disablefilter writes it, the
# whole rule wrapped in `if false { ... }`.
SIEVELIB_DISABLED_SCRIPT = """require ["fileinto"];
# rule:[paused]
if false {
    if anyof (header :contains "subject" "invoice") {
        fileinto "INBOX.Bills";
        stop;
    }
}
# rule:[bin-the-noise]
if header :contains "subject" "newsletter" {
    fileinto "INBOX.Noise";
    stop;
}
"""


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "script",
    [
        DISABLED_SCRIPT,
        DISABLED_SCRIPT.replace("\n", "\r\n"),
        SIEVELIB_DISABLED_SCRIPT,
    ],
    ids=["roundcube-lf", "roundcube-crlf", "sievelib"],
)
def test_a_replaced_disabled_rule_stays_disabled_in_roundcubes_form(
    script, reparse
):
    """Red while sievelib's updatefilter wraps the replacement in
    ``if false { if <test> { ... } }``, which Roundcube shows as enabled
    (#168)."""
    merged = merge_simple(script, "paused", "INBOX.Other", replace=True)

    assert REPLACED_DISABLED in merged

    after_if = merged.split("# rule:[paused]\nif", 1)[1]

    assert ROUNDCUBE_DISABLED.match(after_if)
    assert merged.count("if false") == 1
    assert [rule.disabled for rule in MxrouteDialect.read_rules(merged)] == [
        True,
        False,
    ]
    reparse(merged)


# ----------------------------------------------------------------------------
def test_a_replaced_disabled_rule_is_read_with_its_new_test():
    """What ``mailctl rules`` lists: still disabled, with the new test."""
    merged = merge_simple(
        DISABLED_SCRIPT, "paused", "INBOX.Other", replace=True
    )
    fresh = merge_simple("", "paused", "INBOX.Other")

    paused = MxrouteDialect.read_rules(merged)[0]

    assert paused.disabled
    assert paused.modelled
    assert paused.tests == MxrouteDialect.read_rules(fresh)[0].tests
    assert paused.actions == ("fileinto", "stop")


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_a_replaced_disabled_rule_enables_to_the_new_test(newline, reparse):
    merged = merge_simple(
        DISABLED_SCRIPT.replace("\n", newline),
        "paused",
        "INBOX.Other",
        replace=True,
    )

    enabled = enable_rule(merged, "paused")

    assert (
        "# rule:[paused]\n"
        'if anyof (header :contains "From" "a@example.com") {\n'
        '    fileinto "INBOX.Other";\n'
        "    stop;\n"
        "}\n"
    ) in enabled
    assert "if false" not in enabled
    reparse(enabled)


# ----------------------------------------------------------------------------
def test_a_multi_line_replacement_of_a_disabled_rule_is_refused():
    """A comment ends at the line's end, so the test cannot be kept after
    ``if false``; enabling the rule first is the way through."""
    with pytest.raises(
        MailctlError, match=r"'paused' cannot be replaced while disabled"
    ) as error:
        merge_rule(
            DISABLED_SCRIPT,
            "paused",
            [("Subject", ":contains", "two\nlines")],
            [("fileinto", "INBOX.Other")],
            replace=True,
        )

    assert "enable-rule" in str(error.value)


# ----------------------------------------------------------------------------
def test_a_multi_line_replacement_of_an_enabled_rule_still_goes_ahead(
    reparse,
):
    """The control: only the disabled form needs the test on one line."""
    merged = merge_rule(
        DISABLED_SCRIPT,
        "bin-the-noise",
        [("Subject", ":contains", "two\nlines")],
        [("fileinto", "INBOX.Other")],
        replace=True,
    )

    assert '"two\nlines"' in merged
    reparse(merged)


# ############################################################################
# Switching a rule off and on (#158)
# ############################################################################

# A rule of several tests. Roundcube writes it on the `if false` line as
# `allof (a, b)` -- the tests joined by ", " -- and so does sievelib.
MULTI_TEST_SCRIPT = """require ["fileinto", "body"];
# rule:[invoices]
if allof (header :contains ["from", "sender"] "billing@example.com",
          not header :is "subject" "receipt",
          body :contains "invoice")
{
\tfileinto "INBOX.Bills";
\tstop;
}
# rule:[bin-the-noise]
if header :contains "subject" "newsletter"
{
\tfileinto "INBOX.Noise";
\tstop;
}
"""

MULTI_TEST_LINE = (
    'if false # allof (header :contains ["from", "sender"] '
    '"billing@example.com", not header :is "subject" "receipt", '
    'body :contains "invoice")\n{\n'
)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_disable_writes_the_test_where_roundcube_reads_it(newline, reparse):
    disabled = disable_rule(
        MULTI_TEST_SCRIPT.replace("\n", newline), "invoices"
    )

    assert "# rule:[invoices]\n" + MULTI_TEST_LINE in disabled

    after_if = disabled.split("# rule:[invoices]\nif", 1)[1]

    assert ROUNDCUBE_DISABLED.match(after_if)
    assert 'fileinto "INBOX.Bills";' in disabled
    assert 'if header :contains "subject" "newsletter" {' in disabled
    reparse(disabled)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_disable_then_enable_is_the_rule_it_started_as(newline, reparse):
    start = MULTI_TEST_SCRIPT.replace("\n", newline)

    restored = enable_rule(disable_rule(start, "invoices"), "invoices")

    assert restored == render_script(parse_script(start))
    reparse(restored)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_a_rule_roundcube_disabled_is_enabled(newline, reparse):
    enabled = enable_rule(DISABLED_SCRIPT.replace("\n", newline), "paused")

    assert (
        "# rule:[paused]\n"
        'if anyof (header :contains "subject" "invoice") {\n'
        '    fileinto "INBOX.Bills";\n'
    ) in enabled
    assert "if false" not in enabled
    reparse(enabled)


# ----------------------------------------------------------------------------
def test_a_rule_sievelib_disabled_is_enabled_too(reparse):
    """sievelib's disablefilter wraps the rule in ``if false { ... }``,
    which a --replace of a disabled rule wrote before #168."""
    enabled = enable_rule(SIEVELIB_DISABLED_SCRIPT, "paused")

    assert "if false" not in enabled
    assert (
        'if anyof (header :contains "subject" "invoice") {\n'
        '    fileinto "INBOX.Bills";\n'
    ) in enabled
    reparse(enabled)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("switch", "script", "name"),
    [
        (disable_rule, DISABLED_SCRIPT, "paused"),
        (enable_rule, DISABLED_SCRIPT, "bin-the-noise"),
    ],
    ids=["disable", "enable"],
)
def test_a_rule_already_switched_returns_the_script_untouched(
    switch, script, name
):
    assert switch(script, name) is script


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("switch", [disable_rule, enable_rule])
def test_switching_an_unknown_rule_names_the_real_ones(switch):
    with pytest.raises(
        MailctlError,
        match=r"no rule named 'phantom'.*Known rules: paused, bin-the-noise",
    ):
        switch(DISABLED_SCRIPT, "phantom")


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("kept", "reason"),
    [
        ("", "nothing to restore"),
        ("# was a subject test", "not a Sieve test"),
        ('# anyof (header :contains "subject"', "not a Sieve test"),
        ('# header :contains "a" "b" {} if true', "not a Sieve test"),
        ('# body :contains "invoice"', "not a Sieve test"),
    ],
    ids=["nothing", "prose", "truncated", "trailing-block", "not-required"],
)
def test_a_test_that_cannot_be_read_back_is_refused_naming_the_rule(
    kept, reason
):
    script = DISABLED_SCRIPT.replace(
        '# anyof (header :contains "subject" "invoice")', kept
    )

    with pytest.raises(MailctlError, match=r"'paused' cannot be enabled") as e:
        enable_rule(script, "paused")

    assert reason in str(e.value)


# ----------------------------------------------------------------------------
def test_a_test_on_several_lines_cannot_be_disabled():
    script = """require ["fileinto"];
# rule:[long]
if header :contains "subject" text:
one
.
{
    fileinto "INBOX.Long";
}
"""

    with pytest.raises(MailctlError, match="'long' cannot be disabled"):
        disable_rule(script, "long")


# ----------------------------------------------------------------------------
def test_a_rule_with_no_if_cannot_be_disabled():
    with pytest.raises(MailctlError, match="no 'if' test"):
        disable_rule(
            'require ["fileinto"];\n# rule:[all]\nfileinto "INBOX.All";\n',
            "all",
        )


# ----------------------------------------------------------------------------
def test_a_script_of_only_comments_keeps_them():
    text = "# nothing here yet\n# but soon\n"

    assert render_script(parse_script(text)) == text


# ############################################################################
# Diff, naming, backups
# ############################################################################


# ----------------------------------------------------------------------------
def test_script_diff_is_empty_when_nothing_changed():
    """The dry-run diff is the user-facing proof that a merge merged.

    An empty diff for an unchanged script is what lets the CLI say "no
    change" instead of showing a confusing zero-line patch.
    """
    script = merge_simple("", "one", "INBOX.One")

    assert script_diff(script, script) == ""


# ----------------------------------------------------------------------------
def test_script_diff_shows_the_added_rule():
    before = merge_simple("", "one", "INBOX.One")
    after = merge_simple(before, "two", "INBOX.Two")

    diff = script_diff(before, after, name="active")

    assert "active (current)" in diff
    assert "active (proposed)" in diff
    assert "+# rule:[two]" in diff


# ----------------------------------------------------------------------------
def test_rule_names_reports_the_rules_in_order():
    script = merge_simple("", "first", "INBOX.A")
    script = merge_simple(script, "second", "INBOX.B")

    assert rule_names(parse_script(script)) == ["first", "second"]


# ----------------------------------------------------------------------------
def test_render_script_round_trips_through_parse():
    script = merge_simple("", "one", "INBOX.One")

    assert render_script(parse_script(script)) == script


# ----------------------------------------------------------------------------
def test_backup_writes_the_exact_bytes_the_server_had(tmp_path):
    """Restoring must not need mailctl, so the file is a plain copy."""
    text = 'require ["fileinto"];\n# untouched\n'

    target = write_backup(text, backup_path("roundcube", tmp_path / "backups"))

    assert target.parent == tmp_path / "backups"
    assert target.name.startswith("roundcube-")
    assert target.suffix == ".sieve"
    assert target.read_text(encoding="utf-8") == text


# ----------------------------------------------------------------------------
def test_backup_sanitizes_a_script_name_with_path_separators(tmp_path):
    """A server-supplied script name must not be able to escape the dir."""
    target = write_backup(
        "x", backup_path("../../etc/passwd", tmp_path / "backups")
    )

    assert target.parent == tmp_path / "backups"
    assert "/" not in target.name


# ----------------------------------------------------------------------------
def test_backup_failure_raises_rather_than_losing_the_upload_guard(tmp_path):
    """A backup that cannot be written must stop the upload, not proceed.

    The backup is the only thing standing between a bad merge and an
    unrecoverable script, so its failure has to be fatal and named.
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")

    with pytest.raises(MailctlError, match="could not write backup"):
        write_backup("x", backup_path("active", blocker / "backups"))


# ############################################################################
# The connection-failure hint
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_hint_calls_the_port_the_default_only_when_it_is():
    """4190 + starttls from the built-in default is described as such."""
    hint = mxroute_managesieve._connection_hint(
        load_config(argparse.Namespace())
    )

    assert "4190 + starttls is the RFC 5804 / Dovecot default" in hint


# ----------------------------------------------------------------------------
def test_the_hint_names_where_a_typed_port_and_mode_came_from():
    """#55: typed values were reported as "the RFC 5804 / Dovecot default"."""
    config = load_config(argparse.Namespace(sieve_port=1, sieve_tls="none"))
    hint = mxroute_managesieve._connection_hint(config)

    assert "is the RFC 5804 / Dovecot default" not in hint
    assert "port 1 (flag --sieve-port)" in hint
    assert "TLS mode none (flag --sieve-tls)" in hint
    assert "the RFC 5804 / Dovecot default is 4190 + starttls" in hint


# ----------------------------------------------------------------------------
def test_the_hint_does_not_guess_for_a_config_built_by_hand():
    """No recorded source is not the same as a default one."""
    hint = mxroute_managesieve._connection_hint(
        Config(sieve_port=1, sieve_tls="none")
    )

    assert "is the RFC 5804 / Dovecot default" not in hint
    assert "port 1 and TLS mode none" in hint
