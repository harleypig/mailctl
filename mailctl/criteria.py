"""The shared criteria model.

One set of user-supplied criteria has to be expressed twice: as Sieve
conditions for mail that has not arrived yet, and as IMAP SEARCH keys for
mail that already has. Keeping both derivations in one place is what stops
the two halves of ``mailctl add`` from drifting apart.

The two languages are not equally expressive, and the gap is handled
explicitly rather than papered over:

* Sieve's default comparator (``i;ascii-casemap``) is case-insensitive, so
  every comparison here is too.
* IMAP SEARCH only ever does a case-insensitive *substring* match. For
  ``--compare is`` and ``--compare matches`` the search key is therefore
  deliberately too broad, and ``Criteria.matches`` re-checks each candidate
  against the real semantics using its fetched headers.
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from . import MailctlError

__all__ = [
    "COMPARE_OPS",
    "FILTER_VERSION",
    "MATCH_MODES",
    "Criteria",
    "Term",
    "dump_filter",
    "escape_sieve_string",
    "load_filter",
    "merge_criteria",
]

COMPARE_OPS = ("contains", "is", "matches")
MATCH_MODES = ("any", "all")

# Canonical spellings for the headers with a dedicated CLI flag. Sieve and
# IMAP both treat header names case-insensitively; canonicalizing only
# keeps the generated script and the --dry-run output tidy.
CANONICAL_HEADERS = {
    "from": "From",
    "to": "To",
    "cc": "Cc",
    "bcc": "Bcc",
    "subject": "Subject",
    "list-id": "List-Id",
    "sender": "Sender",
    "reply-to": "Reply-To",
    "x-original-to": "X-Original-To",
    "delivered-to": "Delivered-To",
}

# IMAP SEARCH has first-class keys for a handful of headers. They mean the
# same thing as HEADER <name> <value> but are cheaper and more widely
# supported, so prefer them where one exists.
IMAP_SHORTCUTS = {
    "from": "FROM",
    "to": "TO",
    "cc": "CC",
    "bcc": "BCC",
    "subject": "SUBJECT",
}

# The filter document's format version. A reader refuses any other, so a
# change a version 1 reader would misread must raise it.
FILTER_VERSION = 1


# ############################################################################
# Helpers
# ############################################################################


# ----------------------------------------------------------------------------
def canonical_header(name: str) -> str:
    """Return the canonical spelling of a header name."""
    return CANONICAL_HEADERS.get(name.strip().lower(), name.strip())


# ----------------------------------------------------------------------------
def escape_sieve_string(value: str) -> str:
    """Escape a value for a Sieve quoted string.

    RFC 5228 quoted strings escape only backslash and double quote, and
    sievelib quotes without escaping -- so an unescaped ``\\Seen`` would be
    emitted as ``"\\Seen"``, which Sieve reads back as the flag ``Seen``.
    Doing this here is what keeps ``--flag '\\Seen'`` meaning what the user
    typed.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


# ----------------------------------------------------------------------------
def sieve_pattern_to_regex(pattern: str) -> re.Pattern:
    """Compile a Sieve ``:matches`` pattern into a regex.

    Sieve wildcards are exactly ``*`` (any sequence) and ``?`` (any single
    character), with a backslash escaping either. ``fnmatch`` is not used
    because it additionally treats ``[...]`` as a character class, which
    Sieve does not -- a subject containing brackets would silently stop
    matching.
    """
    out = ["(?s)\\A"]
    index = 0

    while index < len(pattern):
        char = pattern[index]

        if char == "\\" and index + 1 < len(pattern):
            out.append(re.escape(pattern[index + 1]))
            index += 2
            continue

        if char == "*":
            out.append(".*")

        elif char == "?":
            out.append(".")

        else:
            out.append(re.escape(char))

        index += 1

    out.append("\\Z")

    return re.compile("".join(out), re.IGNORECASE)


# ----------------------------------------------------------------------------
def longest_literal(pattern: str) -> str:
    """Return the longest wildcard-free run of a ``:matches`` pattern.

    Used to derive an IMAP substring hint from a glob. Any message the glob
    can match must contain this run, so searching for it narrows the
    candidate set without ever excluding a real match.
    """
    literals = []
    current = []
    index = 0

    while index < len(pattern):
        char = pattern[index]

        if char == "\\" and index + 1 < len(pattern):
            current.append(pattern[index + 1])
            index += 2
            continue

        if char in "*?":
            literals.append("".join(current))
            current = []

        else:
            current.append(char)

        index += 1

    literals.append("".join(current))

    return max(literals, key=len) if literals else ""


# ############################################################################
# Model
# ############################################################################


@dataclass(frozen=True)
class Term:
    """One header/value pair to test."""

    header: str
    value: str

    # ------------------------------------------------------------------------
    def __post_init__(self):
        if not self.header:
            raise MailctlError("a criterion needs a header name")


@dataclass
class Criteria:
    """A set of header tests plus how to combine and compare them."""

    terms: list[Term] = field(default_factory=list)
    match: str = "any"
    compare: str = "contains"

    # ------------------------------------------------------------------------
    def __post_init__(self):
        if self.match not in MATCH_MODES:
            raise MailctlError(
                f"--match must be one of {', '.join(MATCH_MODES)}"
            )

        if self.compare not in COMPARE_OPS:
            raise MailctlError(
                f"--compare must be one of {', '.join(COMPARE_OPS)}"
            )

    # ------------------------------------------------------------------------
    def __bool__(self) -> bool:
        return bool(self.terms)

    # ------------------------------------------------------------------------
    def add(self, header: str, value: str) -> None:
        """Append a term, canonicalizing the header name."""
        if value == "":
            raise MailctlError(f"criterion for {header!r} has an empty value")

        self.terms.append(Term(canonical_header(header), value))

    # ------------------------------------------------------------------------
    def require_terms(self) -> None:
        """Fail unless at least one criterion was given.

        A rule with no conditions would match every message, which is never
        what someone meant to type.
        """
        if not self.terms:
            raise MailctlError(
                "no criteria given -- use --from/--to/--cc/--subject/"
                "--list-id/--header"
            )

    # ------------------------------------------------------------------------
    def to_dict(self) -> dict:
        """The criteria as plain data, the filter document's ``criteria``."""
        return {
            "match": self.match,
            "compare": self.compare,
            "terms": [
                {"header": term.header, "value": term.value}
                for term in self.terms
            ],
        }

    # ------------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data) -> "Criteria":
        """Read ``to_dict``'s shape back, refusing anything else.

        ``match`` and ``compare`` default as their flags do; ``terms`` is
        required and may not be empty. An unknown key is refused rather
        than ignored, so a misspelt ``comapre`` cannot quietly fall back
        to the default.
        """
        _require_keys(data, "criteria", {"terms"}, {"match", "compare"})

        match = data.get("match", "any")
        compare = data.get("compare", "contains")

        if match not in MATCH_MODES:
            raise MailctlError(
                f"filter: 'match' must be one of {', '.join(MATCH_MODES)}"
            )

        if compare not in COMPARE_OPS:
            raise MailctlError(
                f"filter: 'compare' must be one of {', '.join(COMPARE_OPS)}"
            )

        terms = data["terms"]

        if not isinstance(terms, list) or not terms:
            raise MailctlError("filter: 'terms' must be a non-empty list")

        criteria = cls(match=match, compare=compare)

        for index, term in enumerate(terms):
            where = f"terms[{index}]"
            _require_keys(term, where, {"header", "value"})

            if not all(isinstance(term[key], str) for key in term):
                raise MailctlError(
                    f"filter: {where} 'header' and 'value' must be strings"
                )

            if not term["header"].strip():
                raise MailctlError(f"filter: {where} has an empty header")

            criteria.add(term["header"], term["value"])

        return criteria

    # ------------------------------------------------------------------------
    def describe(self) -> str:
        """Render the criteria as one human-readable line."""
        joiner = " OR " if self.match == "any" else " AND "

        parts = [
            f"{term.header} {self.compare} {term.value!r}"
            for term in self.terms
        ]

        return joiner.join(parts)

    # ########################################################################
    # Sieve
    # ########################################################################

    # ------------------------------------------------------------------------
    def sieve_matchtype(self) -> str:
        """Return the sievelib matchtype for the ``--match`` mode."""
        return "anyof" if self.match == "any" else "allof"

    # ------------------------------------------------------------------------
    def sieve_conditions(self) -> list[tuple[str, str, str]]:
        """Return sievelib condition tuples for these criteria.

        Each tuple is ``(header, :comparator, value)``, which sievelib turns
        into a ``header`` test. Values are escaped here because sievelib
        quotes but does not escape.
        """
        self.require_terms()

        tag = f":{self.compare}"

        return [
            (term.header, tag, escape_sieve_string(term.value))
            for term in self.terms
        ]

    # ########################################################################
    # IMAP
    # ########################################################################

    # ------------------------------------------------------------------------
    def _imap_term_key(self, term: Term) -> list:
        """Return the IMAP SEARCH key for a single term.

        For ``matches`` the glob is reduced to its longest literal run; a
        pattern of nothing but wildcards degrades to "has this header",
        which is correct-but-broad and gets narrowed by the post-filter.
        """
        needle = term.value

        if self.compare == "matches":
            needle = longest_literal(term.value)

        shortcut = IMAP_SHORTCUTS.get(term.header.lower())

        if shortcut:
            return [shortcut, needle]

        return ["HEADER", term.header, needle]

    # ------------------------------------------------------------------------
    def imap_search_key(self, extra: Sequence = ()) -> list:
        """Return an IMAPClient search key for these criteria.

        ``all`` becomes IMAP's implicit AND (adjacent keys); ``any`` becomes
        a right-nested chain of the binary ``OR`` key. ``extra`` is ANDed on
        top, for callers that want to add ``UNSEEN``, ``NOT DELETED``, and
        the like.
        """
        # This method and sieve_conditions() are the *only* two renderings
        # of a rule, and both read from this one object. That is what keeps
        # the Sieve rule and the retroactive pass in agreement: there is no
        # Sieve interpreter anywhere in this tool, and nothing re-derives
        # what a rule means by reading generated Sieve back. The criteria in
        # hand are the single source of truth for both outputs.
        #
        # The alternative -- pattern-matching generated Sieve text to work
        # out what to search for -- is how the one comparable Python tool
        # (kekzl/mailcow-ai-filter) does it, and it silently mis-files
        # anything outside the single shape its regexes cover (anyof +
        # fileinto). Do not reintroduce that direction here.
        #
        # ICEBOX: sieve evaluation -- "which filters match this email",
        # "show me which rules would catch this message", "match existing
        # rules", "apply my existing sieve script retroactively", "run my
        # current filters over old mail", "backfill existing rules". This is
        # an ANTICIPATED requirement, not a hypothetical one: the intended
        # TUI ("skim a message, show which existing filters would catch
        # it") needs exactly this, and it is a genuinely different problem
        # from the one solved above. Here the criteria are in hand; there
        # they are not, so the stored script has to be *evaluated* against a
        # message.
        #
        # Deliberately not built now, and not to be approximated. What it
        # needs is a real Sieve evaluator. The only Python one is
        # python-sifter/sifter3, roughly three years stale as of 2026-08,
        # with documented gaps in the RFC 5228 base spec alone -- encoded
        # characters, multi-line strings, bracketed comments, and the
        # 'envelope' test are all missing -- so adopting it will most
        # likely mean patching or vendoring it rather than depending on it
        # as published. The alternative, hand-rolled regex matching of
        # generated Sieve, is the failure mode described above and is not
        # an option. Dovecot's own FILTER=SIEVE (see cli.report_filter_
        # sieve) would sidestep the evaluator entirely by doing the
        # evaluation server-side, where it is already implemented -- worth
        # checking for before writing any of this.
        self.require_terms()

        keys = [self._imap_term_key(term) for term in self.terms]

        combined = list(keys) if self.match == "all" else [_or_chain(keys)]

        return [*combined, *extra]

    # ------------------------------------------------------------------------
    def header_names(self) -> list[str]:
        """Return the distinct headers to fetch for post-filtering."""
        seen = {}

        for term in self.terms:
            seen.setdefault(term.header.upper(), term.header)

        return list(seen.values())

    # ########################################################################
    # Exact re-check
    # ########################################################################

    # ------------------------------------------------------------------------
    def _term_matches(self, term: Term, values: Iterable[str]) -> bool:
        """Test one term against every occurrence of its header."""
        if self.compare == "matches":
            pattern = sieve_pattern_to_regex(term.value)

            return any(pattern.match(value or "") for value in values)

        needle = term.value.casefold()

        if self.compare == "is":
            return any(
                (value or "").strip().casefold() == needle for value in values
            )

        return any(needle in (value or "").casefold() for value in values)

    # ------------------------------------------------------------------------
    def matches(self, headers: Mapping[str, Sequence[str]]) -> bool:
        """Re-check a message's headers against the real semantics.

        ``headers`` maps an upper-cased header name to every occurrence of
        that header in the message. This is what makes ``is`` and
        ``matches`` behave the same for existing mail as they will for new
        mail, given that IMAP could only offer a substring search.
        """
        results = [
            self._term_matches(term, headers.get(term.header.upper(), ()))
            for term in self.terms
        ]

        if self.match == "all":
            return all(results)

        return any(results)


# ############################################################################
# Combining criteria
# ############################################################################


# ----------------------------------------------------------------------------
def merge_criteria(derived: Criteria, explicit: Criteria) -> Criteria:
    """Criteria taken from a message, overridden by the ones given outright.

    A header the explicit criteria name replaces every term the derived
    ones hold for it; any other derived term is kept, ahead of the
    explicit terms. ``match`` and ``compare`` are the explicit criteria's,
    since they govern the whole set.
    """
    named = {term.header.upper() for term in explicit.terms}

    kept = [term for term in derived.terms if term.header.upper() not in named]

    return Criteria(
        terms=[*kept, *explicit.terms],
        match=explicit.match,
        compare=explicit.compare,
    )


# ############################################################################
# The filter document
# ############################################################################


# ----------------------------------------------------------------------------
def dump_filter(criteria: Criteria) -> str:
    """The criteria as a filter document: versioned JSON, criteria only.

    Actions are not part of a filter; the command that saves or applies
    one takes them.
    """
    criteria.require_terms()

    document = {"version": FILTER_VERSION, "criteria": criteria.to_dict()}

    # A value comes from mail, so it may hold anything. ensure_ascii (the
    # default) escapes everything outside printable ASCII, which leaves
    # nothing a terminal would act on; do not turn it off.
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"


# ----------------------------------------------------------------------------
def load_filter(text: str) -> Criteria:
    """Read a filter document back into criteria, refusing a malformed one."""
    try:
        document = json.loads(text)

    except json.JSONDecodeError as exc:
        raise MailctlError(
            f"filter is not valid JSON: {exc.msg} (line {exc.lineno}, "
            f"column {exc.colno})"
        ) from exc

    _require_keys(document, "document", {"version", "criteria"})

    version = document["version"]

    # bool is an int in Python, and true is not a version.
    if isinstance(version, bool) or version != FILTER_VERSION:
        raise MailctlError(
            f"filter: unsupported version {version!r}; this mailctl reads "
            f"version {FILTER_VERSION}"
        )

    return Criteria.from_dict(document["criteria"])


# ----------------------------------------------------------------------------
def _require_keys(
    data, where: str, required: set[str], optional: Iterable[str] = ()
) -> None:
    """Refuse ``data`` unless it is an object holding exactly these keys."""
    if not isinstance(data, dict):
        raise MailctlError(f"filter: {where} must be a JSON object")

    missing = sorted(required - data.keys())

    if missing:
        raise MailctlError(f"filter: {where} lacks {', '.join(missing)}")

    unknown = sorted(data.keys() - required - set(optional))

    if unknown:
        raise MailctlError(
            f"filter: {where} has unknown key(s) {', '.join(unknown)}"
        )


# ----------------------------------------------------------------------------
def _or_chain(keys: list) -> list:
    """Fold search keys into IMAP's binary ``OR`` right-associatively.

    IMAP has no n-ary OR, so ``[a, b, c]`` has to become ``OR a (OR b c)``.
    IMAPClient adds the parentheses around each nested list itself.
    """
    if not keys:
        raise MailctlError("cannot build a search key from no criteria")

    if len(keys) == 1:
        return keys[0]

    return ["OR", keys[0], _or_chain(keys[1:])]
