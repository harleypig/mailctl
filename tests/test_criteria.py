"""The shared criteria model, and the two translations it drives.

One ``Criteria`` object produces the Sieve conditions for mail that has not
arrived and the IMAP SEARCH key for mail that already has. If those two
drift, ``mailctl add`` files new mail one way and old mail another, and
nothing in the output says so -- which is why the agreement tests below
assert both renderings of the *same* object rather than testing each side
on its own.
"""

import json
import re
from datetime import date

import pytest

from mailctl import MailctlError
from mailctl.cli import error_text
from mailctl.criteria import (
    COMPARE_OPS,
    FILTER_VERSION,
    MATCH_MODES,
    Criteria,
    Term,
    dump_filter,
    escape_sieve_string,
    load_filter,
    longest_literal,
    merge_criteria,
    parse_age,
    parse_date,
    sieve_pattern_to_regex,
)

# ############################################################################
# Construction
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("given", "canonical"),
    [
        pytest.param("from", "From", id="from"),
        pytest.param("FROM", "From", id="upper"),
        pytest.param("  list-id  ", "List-Id", id="padded"),
        pytest.param("reply-to", "Reply-To", id="hyphenated"),
        pytest.param("X-Custom", "X-Custom", id="unknown-kept-as-typed"),
    ],
)
def test_add_canonicalizes_the_header_name(given, canonical):
    """Canonical spellings keep the generated script and dry-run tidy.

    Both Sieve and IMAP treat header names case-insensitively, so this is
    presentation -- but it is presentation the user reads back in a diff
    before approving an upload.
    """
    criteria = Criteria()
    criteria.add(given, "value")

    assert criteria.terms[0].header == canonical


# ----------------------------------------------------------------------------
def test_add_refuses_an_empty_value():
    """An empty value would widen the rule to every message with a header."""
    criteria = Criteria()

    with pytest.raises(MailctlError, match="has an empty value"):
        criteria.add("from", "")


# ----------------------------------------------------------------------------
def test_a_term_refuses_an_empty_header():
    with pytest.raises(MailctlError, match="needs a header name"):
        Term("", "value")


# ----------------------------------------------------------------------------
def test_an_unknown_match_mode_is_refused():
    with pytest.raises(MailctlError, match=r"the match mode must be one of"):
        Criteria(match="either")


# ----------------------------------------------------------------------------
def test_an_unknown_compare_op_is_refused():
    with pytest.raises(MailctlError, match=r"the comparison must be one of"):
        Criteria(compare="regex")


# ----------------------------------------------------------------------------
def test_no_criteria_is_refused_before_anything_is_generated():
    """A rule with no conditions matches every message in the mailbox.

    Both renderings guard this, because either one reaching a server
    unguarded is a mailbox-wide action nobody asked for.
    """
    criteria = Criteria()

    assert not criteria

    with pytest.raises(MailctlError, match="no criteria given"):
        criteria.require_terms()

    with pytest.raises(MailctlError, match="no criteria given"):
        criteria.sieve_conditions()

    with pytest.raises(MailctlError, match="no criteria given"):
        criteria.imap_search_key()


# ----------------------------------------------------------------------------
def test_describe_names_the_comparison_and_the_joiner():
    criteria = Criteria(match="all", compare="is")
    criteria.add("from", "a@example.com")
    criteria.add("subject", "Report")

    described = criteria.describe()

    assert " AND " in described
    assert "From is 'a@example.com'" in described
    assert "Subject is 'Report'" in described


# ----------------------------------------------------------------------------
def test_describe_uses_or_for_match_any():
    criteria = Criteria(match="any")
    criteria.add("from", "a@example.com")
    criteria.add("to", "b@example.com")

    assert " OR " in criteria.describe()


# ############################################################################
# Sieve rendering
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("mode", "matchtype"),
    [
        pytest.param("any", "anyof", id="any"),
        pytest.param("all", "allof", id="all"),
    ],
)
def test_sieve_matchtype_maps_the_match_mode(mode, matchtype):
    assert Criteria(match=mode).sieve_matchtype() == matchtype


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("compare", COMPARE_OPS)
def test_sieve_conditions_carry_the_comparator_tag(compare):
    criteria = Criteria(compare=compare)
    criteria.add("subject", "Report")

    assert criteria.sieve_conditions() == [
        ("Subject", f":{compare}", "Report")
    ]


# ----------------------------------------------------------------------------
def test_sieve_conditions_escape_every_value():
    """Escaping at render time is what keeps a flag or a quote intact."""
    criteria = Criteria()
    criteria.add("subject", 'a "quote" and a \\ backslash')

    ((_header, _tag, value),) = criteria.sieve_conditions()

    assert value == escape_sieve_string('a "quote" and a \\ backslash')
    assert value == 'a \\"quote\\" and a \\\\ backslash'


# ############################################################################
# IMAP rendering
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("header", "key"),
    [
        pytest.param("from", ["FROM", "x"], id="from-shortcut"),
        pytest.param("to", ["TO", "x"], id="to-shortcut"),
        pytest.param("cc", ["CC", "x"], id="cc-shortcut"),
        pytest.param("bcc", ["BCC", "x"], id="bcc-shortcut"),
        pytest.param("subject", ["SUBJECT", "x"], id="subject-shortcut"),
        pytest.param("list-id", ["HEADER", "List-Id", "x"], id="header-form"),
        pytest.param("x-spam", ["HEADER", "x-spam", "x"], id="custom-header"),
    ],
)
def test_imap_uses_the_first_class_key_where_one_exists(header, key):
    """IMAP has dedicated keys for a few headers; they are cheaper.

    The HEADER fallback has to stay correct for everything else, since the
    headers people actually filter on (List-Id above all) have no
    shortcut.
    """
    criteria = Criteria()
    criteria.add(header, "x")

    assert criteria.imap_search_key() == [key]


# ----------------------------------------------------------------------------
def test_match_all_becomes_imaps_implicit_and():
    criteria = Criteria(match="all")
    criteria.add("from", "a")
    criteria.add("subject", "b")

    assert criteria.imap_search_key() == [["FROM", "a"], ["SUBJECT", "b"]]


# ----------------------------------------------------------------------------
def test_match_any_becomes_a_right_nested_or_chain():
    """IMAP's OR is binary, so three terms need nesting, not a flat list.

    A flat ``OR a b c`` is a syntax error the server rejects; getting this
    wrong fails loudly, but getting the *nesting* wrong (left-associative)
    would silently change which messages match.
    """
    criteria = Criteria(match="any")
    criteria.add("from", "a")
    criteria.add("to", "b")
    criteria.add("cc", "c")

    assert criteria.imap_search_key() == [
        ["OR", ["FROM", "a"], ["OR", ["TO", "b"], ["CC", "c"]]]
    ]


# ----------------------------------------------------------------------------
def test_a_single_term_needs_no_or_wrapper():
    criteria = Criteria(match="any")
    criteria.add("from", "a")

    assert criteria.imap_search_key() == [["FROM", "a"]]


# ----------------------------------------------------------------------------
def test_extra_search_keys_are_anded_on_the_end():
    criteria = Criteria(match="any")
    criteria.add("from", "a")

    key = criteria.imap_search_key(extra=["UNSEEN", "NOT", "DELETED"])

    assert key == [["FROM", "a"], "UNSEEN", "NOT", "DELETED"]


# ----------------------------------------------------------------------------
def test_matches_reduces_a_glob_to_its_longest_literal():
    """The search key for a glob must be broad, never narrow.

    IMAP cannot glob, so the derived substring has to be one every real
    match necessarily contains -- otherwise the retroactive pass silently
    skips mail the Sieve rule will catch.
    """
    criteria = Criteria(compare="matches")
    criteria.add("subject", "*[ALERT]*production*")

    assert criteria.imap_search_key() == [["SUBJECT", "production"]]


# ----------------------------------------------------------------------------
def test_a_glob_of_pure_wildcards_degrades_to_a_bare_header_test():
    """Correct-but-broad beats narrow: the post-filter narrows it back."""
    criteria = Criteria(compare="matches")
    criteria.add("list-id", "*")

    assert criteria.imap_search_key() == [["HEADER", "List-Id", ""]]


# ----------------------------------------------------------------------------
def test_header_names_are_distinct_and_keep_their_spelling():
    criteria = Criteria()
    criteria.add("from", "a")
    criteria.add("FROM", "b")
    criteria.add("subject", "c")

    assert criteria.header_names() == ["From", "Subject"]


# ############################################################################
# Sieve and IMAP agree
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("match", MATCH_MODES)
@pytest.mark.parametrize("compare", COMPARE_OPS)
def test_one_object_drives_both_renderings(match, compare):
    """The agreement check, across every match x compare combination.

    Sieve gets the exact comparator; IMAP gets a key that is equal or
    broader and is then re-checked. Both come from this one object, which
    is the whole reason the two halves of ``add`` cannot disagree.
    """
    criteria = Criteria(match=match, compare=compare)
    criteria.add("from", "boss@example.com")
    criteria.add("subject", "Report")

    conditions = criteria.sieve_conditions()
    key = criteria.imap_search_key()

    assert [term[0] for term in conditions] == ["From", "Subject"]
    assert {term[1] for term in conditions} == {f":{compare}"}

    if match == "all":
        assert key == [["FROM", "boss@example.com"], ["SUBJECT", "Report"]]
        assert criteria.sieve_matchtype() == "allof"

    else:
        assert key == [
            ["OR", ["FROM", "boss@example.com"], ["SUBJECT", "Report"]]
        ]
        assert criteria.sieve_matchtype() == "anyof"


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("match", "headers", "expected"),
    [
        pytest.param(
            "any",
            {"FROM": ["boss@example.com"]},
            True,
            id="any-first-term-only",
        ),
        pytest.param(
            "any", {"SUBJECT": ["Report"]}, True, id="any-second-term-only"
        ),
        pytest.param(
            "any", {"FROM": ["someone@else.com"]}, False, id="any-neither"
        ),
        pytest.param(
            "all", {"FROM": ["boss@example.com"]}, False, id="all-needs-both"
        ),
        pytest.param(
            "all",
            {"FROM": ["boss@example.com"], "SUBJECT": ["Report"]},
            True,
            id="all-both-present",
        ),
    ],
)
def test_the_post_filter_honours_the_match_mode(match, headers, expected):
    criteria = Criteria(match=match, compare="contains")
    criteria.add("from", "boss@example.com")
    criteria.add("subject", "Report")

    assert criteria.matches(headers) is expected


# ############################################################################
# The exact re-check
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("compare", "value", "header", "expected"),
    [
        pytest.param(
            "contains",
            "report",
            "Weekly Report",
            True,
            id="contains-substring",
        ),
        pytest.param(
            "contains",
            "REPORT",
            "Weekly Report",
            True,
            id="contains-is-case-insensitive",
        ),
        pytest.param(
            "contains",
            "quarterly",
            "Weekly Report",
            False,
            id="contains-absent",
        ),
        pytest.param(
            "is", "Weekly Report", "Weekly Report", True, id="is-exact"
        ),
        pytest.param(
            "is",
            "weekly report",
            "Weekly Report",
            True,
            id="is-is-case-insensitive",
        ),
        pytest.param(
            "is", "Report", "Weekly Report", False, id="is-rejects-a-substring"
        ),
        pytest.param(
            "is",
            "Weekly Report",
            "  Weekly Report  ",
            True,
            id="is-ignores-surrounding-space",
        ),
        pytest.param(
            "matches",
            "Weekly*",
            "Weekly Report",
            True,
            id="matches-trailing-star",
        ),
        pytest.param(
            "matches",
            "*Report",
            "Weekly Report",
            True,
            id="matches-leading-star",
        ),
        pytest.param(
            "matches",
            "Weekly",
            "Weekly Report",
            False,
            id="matches-needs-the-whole-value",
        ),
        pytest.param(
            "matches",
            "Weekl? Report",
            "Weekly Report",
            True,
            id="matches-single-char-wildcard",
        ),
        pytest.param(
            "matches",
            "Weekl? Report",
            "Weekly  Report",
            False,
            id="matches-question-mark-is-exactly-one",
        ),
    ],
)
def test_each_comparator_means_what_sieve_means(
    compare, value, header, expected
):
    """IMAP SEARCH is substring-only, so this re-check is the real test.

    ``is`` and ``matches`` are both narrower than the search that found the
    candidate. Without this pass the retroactive run would move mail the
    Sieve rule will never touch -- the two halves disagreeing in the one
    direction the user cannot see.
    """
    criteria = Criteria(compare=compare)
    criteria.add("subject", value)

    assert criteria.matches({"SUBJECT": [header]}) is expected


# ----------------------------------------------------------------------------
def test_matches_tests_the_whole_header_value_not_the_address():
    """The case the implementer hit; pinned so nobody 'fixes' it wrong.

    Sieve's ``:matches`` compares the *entire* header value, and a real
    From header carries a display name. So ``*@lists.example.com`` does not
    match ``Announce <announce@lists.example.com>`` -- and it must not,
    because the Sieve rule on the server will not match it either. Making
    this pass would put the retroactive run out of step with the filter it
    was generated alongside.
    """
    criteria = Criteria(compare="matches")
    criteria.add("from", "*@lists.example.com")

    bare = {"FROM": ["announce@lists.example.com"]}
    with_display_name = {"FROM": ["Announce <announce@lists.example.com>"]}

    assert criteria.matches(bare) is True
    assert criteria.matches(with_display_name) is False

    # The glob a user wanting both would have to write.
    forgiving = Criteria(compare="matches")
    forgiving.add("from", "*@lists.example.com*")

    assert forgiving.matches(with_display_name) is True


# ----------------------------------------------------------------------------
def test_a_repeated_header_matches_on_any_occurrence():
    """Received and Delivered-To appear many times in one message."""
    criteria = Criteria(compare="is")
    criteria.add("delivered-to", "me@example.com")

    headers = {"DELIVERED-TO": ["other@example.com", "me@example.com"]}

    assert criteria.matches(headers) is True


# ----------------------------------------------------------------------------
def test_a_missing_header_simply_does_not_match():
    """A message without the header is a normal case, not an error."""
    criteria = Criteria(compare="contains")
    criteria.add("list-id", "github.com")

    assert criteria.matches({}) is False
    assert criteria.matches({"LIST-ID": []}) is False


# ############################################################################
# Glob translation
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_bracket_is_a_literal_not_a_character_class():
    """Sieve globs have exactly ``*`` and ``?`` -- fnmatch would add more.

    Subjects with brackets are ordinary (``[ALERT]``, ``[PATCH]``), so
    treating ``[...]`` as a character class would silently stop matching
    the very subjects people write globs for.
    """
    pattern = sieve_pattern_to_regex("[ALERT]*")

    assert pattern.match("[ALERT] disk full")
    assert not pattern.match("A disk full")


# ----------------------------------------------------------------------------
def test_a_backslash_escapes_a_wildcard():
    pattern = sieve_pattern_to_regex(r"100\* off")

    assert pattern.match("100* off")
    assert not pattern.match("100 percent off")


# ----------------------------------------------------------------------------
def test_a_glob_spans_a_newline():
    """Header values are unfolded, but a stray newline must not truncate."""
    pattern = sieve_pattern_to_regex("start*end")

    assert pattern.match("start\nend")


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("pattern", "literal"),
    [
        pytest.param("*production*", "production", id="between-stars"),
        pytest.param("a*bcdef*gh", "bcdef", id="longest-of-three"),
        pytest.param("plain", "plain", id="no-wildcards"),
        pytest.param("*", "", id="only-a-wildcard"),
        pytest.param(r"esc\*aped", "esc*aped", id="escaped-star-is-literal"),
    ],
)
def test_longest_literal_picks_the_longest_wildcard_free_run(pattern, literal):
    assert longest_literal(pattern) == literal


# ############################################################################
# Merging derived criteria with explicit ones (search --like)
# ############################################################################


# ----------------------------------------------------------------------------
def make(*pairs, match="any", compare="contains") -> Criteria:
    criteria = Criteria(match=match, compare=compare)

    for header, value in pairs:
        criteria.add(header, value)

    return criteria


# ----------------------------------------------------------------------------
def test_a_header_given_outright_replaces_what_was_derived_for_it():
    merged = merge_criteria(
        make(("List-Id", "dev.x.org")), make(("list-id", "other.x.org"))
    )

    assert merged.terms == [Term("List-Id", "other.x.org")]


# ----------------------------------------------------------------------------
def test_other_derived_headers_are_kept_ahead_of_the_explicit_ones():
    merged = merge_criteria(
        make(("From", "a@x.org"), ("Cc", "b@x.org")),
        make(("cc", "c@x.org"), ("Subject", "Hi")),
    )

    assert merged.terms == [
        Term("From", "a@x.org"),
        Term("Cc", "c@x.org"),
        Term("Subject", "Hi"),
    ]


# ----------------------------------------------------------------------------
def test_match_and_compare_are_the_explicit_criteria_s():
    merged = merge_criteria(
        make(("From", "a@x.org")), make(match="all", compare="is")
    )

    assert (merged.match, merged.compare) == ("all", "is")
    assert merged.terms == [Term("From", "a@x.org")]


# ----------------------------------------------------------------------------
def test_merging_changes_neither_input():
    derived = make(("From", "a@x.org"))
    explicit = make(("From", "b@x.org"))

    merge_criteria(derived, explicit)

    assert derived.terms == [Term("From", "a@x.org")]
    assert explicit.terms == [Term("From", "b@x.org")]


# ############################################################################
# The filter document
# ############################################################################


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("match", MATCH_MODES)
@pytest.mark.parametrize("compare", COMPARE_OPS)
def test_a_filter_document_round_trips(match, compare):
    criteria = make(
        ("List-Id", "dev.x.org"),
        ("X-Tag", 'quote " and \\ backslash'),
        ("Subject", "Caf\u00e9"),
        match=match,
        compare=compare,
    )

    assert load_filter(dump_filter(criteria)) == criteria


# ----------------------------------------------------------------------------
def test_the_document_is_versioned_and_carries_criteria_only():
    document = json.loads(dump_filter(make(("From", "a@x.org"))))

    assert document == {
        "version": FILTER_VERSION,
        "criteria": {
            "match": "any",
            "compare": "contains",
            "terms": [{"header": "From", "value": "a@x.org"}],
        },
    }


# ----------------------------------------------------------------------------
def test_a_filter_with_no_criteria_is_never_written():
    with pytest.raises(MailctlError, match="no criteria given"):
        dump_filter(Criteria())


# ----------------------------------------------------------------------------
def test_the_document_holds_nothing_a_terminal_acts_on():
    text = dump_filter(make(("Subject", "a\x1b]0;t\x07b\x7fc\u202ed")))

    assert all(" " <= char <= "~" for char in text.replace("\n", ""))
    assert load_filter(text).terms[0].value == "a\x1b]0;t\x07b\x7fc\u202ed"


# ----------------------------------------------------------------------------
def test_match_and_compare_default_as_their_flags_do():
    text = json.dumps(
        {
            "version": 1,
            "criteria": {"terms": [{"header": "from", "value": "a"}]},
        }
    )

    assert load_filter(text) == make(("From", "a"))


# ----------------------------------------------------------------------------
def document(**criteria) -> str:
    body = {"terms": [{"header": "From", "value": "a"}], **criteria}

    return json.dumps({"version": 1, "criteria": body})


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "error"),
    [
        pytest.param("{", "not valid JSON", id="not-json"),
        pytest.param("[]", "document must be a JSON object", id="not-object"),
        pytest.param('{"criteria": {}}', "lacks version", id="no-version"),
        pytest.param(
            json.dumps({"version": 2, "criteria": {}}),
            "unsupported version 2",
            id="future-version",
        ),
        pytest.param(
            json.dumps({"version": True, "criteria": {}}),
            "unsupported version True",
            id="bool-version",
        ),
        pytest.param(
            json.dumps({"version": 1}), "lacks criteria", id="no-criteria"
        ),
        pytest.param(
            json.dumps({"version": 1, "criteria": {}, "actions": []}),
            "unknown key",
            id="actions-are-not-a-filter",
        ),
        pytest.param(
            json.dumps({"version": 1, "criteria": {"match": "any"}}),
            "holds no terms, body, dates, or state",
            id="no-terms",
        ),
        pytest.param(document(terms=[]), "non-empty list", id="empty-terms"),
        pytest.param(document(comapre="is"), "unknown key", id="misspelt"),
        pytest.param(document(match="either"), "'match'", id="bad-match"),
        pytest.param(document(compare="regex"), "'compare'", id="bad-compare"),
        pytest.param(
            document(terms=[{"header": "From"}]), "lacks value", id="no-value"
        ),
        pytest.param(
            document(terms=[{"header": "From", "value": 3}]),
            "must be strings",
            id="non-string",
        ),
        pytest.param(
            document(terms=[{"header": " ", "value": "a"}]),
            "empty header",
            id="blank-header",
        ),
        pytest.param(
            document(terms=[{"header": "From", "value": ""}]),
            "empty value",
            id="empty-value",
        ),
        pytest.param(
            document(terms=["From"]), "terms[0]", id="term-not-object"
        ),
    ],
)
def test_a_malformed_filter_is_refused_by_name(text, error):
    with pytest.raises(MailctlError, match=re.escape(error)):
        load_filter(text)


# ############################################################################
# Body, dates, and state (#152)
# ############################################################################

TODAY = date(2026, 9, 29)


# ----------------------------------------------------------------------------
def more(**given) -> Criteria:
    """A From term, a body term, and whatever date or state is given."""
    criteria = Criteria(**given)
    criteria.add("from", "a@x.org")
    criteria.add_body("merged")

    return criteria


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-09-01", date(2026, 9, 1)),
        (" 2026-02-28 ", date(2026, 2, 28)),
        ("2024-02-29", date(2024, 2, 29)),
    ],
)
def test_a_date_is_read_as_yyyy_mm_dd(text, expected):
    assert parse_date(text, "--since") == expected


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("1/9/2026", "must be a date as YYYY-MM-DD"),
        ("20260901", "must be a date as YYYY-MM-DD"),
        ("2026-W36-1", "must be a date as YYYY-MM-DD"),
        ("2026-9-1", "must be a date as YYYY-MM-DD"),
        ("yesterday", "must be a date as YYYY-MM-DD"),
        ("", "must be a date as YYYY-MM-DD"),
        ("2026-02-30", "is not a real date"),
        ("2026-13-01", "is not a real date"),
    ],
)
def test_any_other_date_is_refused_naming_the_flag(text, error):
    with pytest.raises(MailctlError, match=error) as caught:
        parse_date(text, "--since")

    assert str(caught.value).startswith("--since")


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "days"), [("1d", 1), ("30d", 30), ("3w", 21), (" 2w ", 14)]
)
def test_an_age_is_days_or_weeks(text, days):
    assert parse_age(text, "--older-than") == days


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("0d", "at least 1d"),
        ("0w", "at least 1d"),
        ("30", "days or weeks"),
        ("1m", "days or weeks"),
        ("3W", "days or weeks"),
        ("-1d", "days or weeks"),
        ("1.5w", "days or weeks"),
    ],
)
def test_any_other_age_is_refused_naming_the_flag(text, error):
    with pytest.raises(MailctlError, match=error) as caught:
        parse_age(text, "--older-than")

    assert str(caught.value).startswith("--older-than")


# ----------------------------------------------------------------------------
def test_a_since_not_before_before_is_refused():
    with pytest.raises(MailctlError, match="since must be earlier"):
        Criteria(since=date(2026, 9, 1), before=date(2026, 9, 1))


# ----------------------------------------------------------------------------
def test_a_zero_age_is_refused_even_when_built_directly():
    with pytest.raises(MailctlError, match="at least one day"):
        Criteria(older_than=0)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("compare", ["is", "matches"])
def test_a_body_test_is_refused_under_a_whole_value_compare(compare):
    """IMAP can only say a body contains a string, so any other body
    comparison would make the rule and the pass disagree."""
    with pytest.raises(MailctlError, match=f"the '{compare}' comparison"):
        Criteria(compare=compare).add_body("x")

    with pytest.raises(MailctlError) as caught:
        Criteria(compare=compare, body=["x"])

    assert caught.value.code == "body_compare"
    assert caught.value.fields == {"compare": compare}


# ----------------------------------------------------------------------------
def test_an_empty_body_value_is_refused():
    with pytest.raises(MailctlError, match="empty value"):
        Criteria().add_body("")


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "given",
    [
        {"body": ["x"]},
        {"since": date(2026, 9, 1)},
        {"before": date(2026, 9, 1)},
        {"older_than": 7},
        {"unread": True},
        {"flagged": True},
    ],
    ids=lambda given: next(iter(given)),
)
def test_each_new_criterion_alone_is_a_criterion(given):
    criteria = Criteria(**given)

    assert criteria
    assert criteria.require_terms() is None


# ----------------------------------------------------------------------------
def test_state_filters_are_named_in_a_fixed_order():
    criteria = Criteria(
        flagged=True,
        unread=True,
        older_than=3,
        before=date(2026, 9, 2),
        since=date(2026, 9, 1),
    )

    assert criteria.state_filters() == [
        "since",
        "before",
        "older-than",
        "unread",
        "flagged",
    ]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "given",
    [
        {"since": date(2026, 9, 1)},
        {"before": date(2026, 9, 1)},
        {"older_than": 7},
        {"unread": True},
        {"flagged": True},
    ],
    ids=lambda given: next(iter(given)),
)
def test_a_saved_rule_refuses_every_date_and_state_filter(given):
    """New mail is unread, unflagged, and zero days old at delivery, so a
    rule testing any of these would not mean what it says."""
    criteria = more(**given)

    with pytest.raises(MailctlError, match="cannot test") as caught:
        criteria.sieve_conditions()

    # The core names the operations; the CLI renders the commands (#183).
    assert "mailctl search" not in str(caught.value)
    assert caught.value.code == "state_in_rule"
    assert caught.value.fields["operations"] == ("search", "apply")

    message = error_text(caught.value)

    assert "'mailctl search' to list it" in message
    assert "'mailctl apply' to act on it" in message

    with pytest.raises(MailctlError, match="cannot test"):
        criteria.check_deliverable()


# ----------------------------------------------------------------------------
def test_body_becomes_the_body_extension_s_contains_test():
    criteria = more()
    criteria.add_body('say "hi" \\ bye')

    assert criteria.sieve_conditions() == [
        ("From", ":contains", "a@x.org"),
        ("body", ":text", ":contains", "merged"),
        ("body", ":text", ":contains", 'say \\"hi\\" \\\\ bye'),
    ]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("match", MATCH_MODES)
def test_body_terms_are_combined_with_header_terms_under_match(match):
    criteria = more(match=match)

    group = [["FROM", "a@x.org"], ["BODY", "merged"]]
    expected = group if match == "all" else [["OR", *group]]

    assert criteria.imap_search_key() == expected
    assert criteria.sieve_matchtype() == f"{match}of"


# ----------------------------------------------------------------------------
def test_dates_and_state_are_anded_after_the_tests_in_imap_s_date_form():
    criteria = more(
        since=date(2026, 9, 1),
        before=date(2026, 10, 12),
        unread=True,
        flagged=True,
    )

    assert criteria.imap_search_key(["NOT", "DELETED"]) == [
        ["OR", ["FROM", "a@x.org"], ["BODY", "merged"]],
        "SINCE",
        "1-Sep-2026",
        "BEFORE",
        "12-Oct-2026",
        "UNSEEN",
        "FLAGGED",
        "NOT",
        "DELETED",
    ]


# ----------------------------------------------------------------------------
def test_older_than_counts_back_from_the_day_it_is_searched():
    """Sugar for BEFORE today-N, worked out when the search is made, so a
    saved filter keeps meaning "older than N days"."""
    criteria = Criteria(older_than=30)

    assert criteria.imap_search_key(today=TODAY) == ["BEFORE", "30-Aug-2026"]
    assert criteria.imap_search_key(today=date(2026, 3, 1)) == [
        "BEFORE",
        "30-Jan-2026",
    ]


# ----------------------------------------------------------------------------
def test_state_alone_searches_without_a_test_group():
    assert Criteria(unread=True).imap_search_key() == ["UNSEEN"]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("match", MATCH_MODES)
def test_the_recheck_takes_the_server_s_word_on_body_and_state(match):
    """Nothing in the headers can confirm a body term or a date, and IMAP
    answers both exactly, so the headers decide only the header terms."""
    criteria = more(match=match, unread=True)
    other = {"FROM": ["b@y.org"]}

    assert criteria.matches(other) is (match == "any")
    assert criteria.matches({"FROM": ["a@x.org"]}) is True
    assert Criteria(flagged=True).matches(other) is True


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("given", "described"),
    [
        (
            {"since": date(2026, 9, 1), "unread": True},
            "(From contains 'a@x.org' OR body contains 'merged') AND "
            "received on or after 2026-09-01 AND unread",
        ),
        (
            {"match": "all", "before": date(2026, 9, 1), "flagged": True},
            "From contains 'a@x.org' AND body contains 'merged' AND "
            "received before 2026-09-01 AND flagged",
        ),
        (
            {"older_than": 1},
            "(From contains 'a@x.org' OR body contains 'merged') AND "
            "older than 1 day",
        ),
    ],
)
def test_describe_names_body_dates_and_state(given, described):
    assert more(**given).describe() == described


# ----------------------------------------------------------------------------
def test_describe_with_state_alone():
    assert Criteria(older_than=14, unread=True).describe() == (
        "older than 14 days AND unread"
    )


# ----------------------------------------------------------------------------
def test_merging_keeps_body_and_takes_the_explicit_state():
    derived = make(("From", "a@x.org"))
    explicit = Criteria(body=["merged"], unread=True, since=date(2026, 9, 1))

    merged = merge_criteria(derived, explicit)

    assert merged.terms == [Term("From", "a@x.org")]
    assert merged.body == ["merged"]
    assert merged.state_filters() == ["since", "unread"]


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "given",
    [
        {},
        {"since": date(2026, 9, 1), "before": date(2026, 9, 28)},
        {"older_than": 21, "unread": True, "flagged": True},
        {"match": "all", "before": date(2026, 1, 31)},
    ],
)
def test_body_dates_and_state_round_trip_through_a_filter(given):
    criteria = more(**given)
    criteria.add_body("Caf\u00e9")

    assert load_filter(dump_filter(criteria)) == criteria


# ----------------------------------------------------------------------------
def test_new_criteria_are_written_only_when_given():
    """A document of header terms reads exactly as it did before #152, and
    a new one names each criterion it carries and nothing else."""
    document = json.loads(
        dump_filter(Criteria(body=["x"], older_than=7, unread=True))
    )

    assert document == {
        "version": 1,
        "criteria": {
            "match": "any",
            "compare": "contains",
            "body": ["x"],
            "older_than_days": 7,
            "unread": True,
        },
    }


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("criteria", "error"),
    [
        pytest.param({"body": []}, "'body' must be a non-empty list", id="b0"),
        pytest.param(
            {"body": "x"}, "'body' must be a non-empty list", id="b1"
        ),
        pytest.param({"body": [""]}, "non-empty string", id="b2"),
        pytest.param({"body": [3]}, "non-empty string", id="b3"),
        pytest.param(
            {"body": ["x"], "compare": "is"},
            "the 'is' comparison",
            id="b-is",
        ),
        pytest.param({"since": "1/9/2026"}, "YYYY-MM-DD", id="since-form"),
        pytest.param({"since": 20260901}, "YYYY-MM-DD string", id="since-int"),
        pytest.param({"before": "2026-02-30"}, "not a real date", id="bef"),
        pytest.param(
            {"since": "2026-09-02", "before": "2026-09-01"},
            "since must be earlier",
            id="range",
        ),
        pytest.param({"older_than_days": 0}, "1 or more", id="age-0"),
        pytest.param({"older_than_days": "7"}, "1 or more", id="age-str"),
        pytest.param({"older_than_days": True}, "1 or more", id="age-bool"),
        pytest.param({"unread": "yes"}, "true or false", id="unread"),
        pytest.param({"flagged": 1}, "true or false", id="flagged"),
        pytest.param({"older_than": 7}, "unknown key", id="misspelt-age"),
        pytest.param({"unread": False}, "holds no terms", id="nothing"),
    ],
)
def test_a_malformed_new_criterion_is_refused_by_name(criteria, error):
    text = json.dumps({"version": 1, "criteria": criteria})

    with pytest.raises(MailctlError, match=re.escape(error)):
        load_filter(text)
