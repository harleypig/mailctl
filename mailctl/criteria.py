"""The shared criteria model.

One set of user-supplied criteria has to be expressed twice: as Sieve
conditions for mail that has not arrived yet, and as IMAP SEARCH keys for
mail that already has. Keeping both derivations in one place is what stops
the two halves of ``mailctl filter add`` from drifting apart.

The two languages are not equally expressive, and the gap is handled
explicitly rather than papered over:

* Sieve's default comparator (``i;ascii-casemap``) is case-insensitive, so
  every comparison here is too.
* IMAP SEARCH only ever does a case-insensitive *substring* match. For
  ``--compare is`` and ``--compare matches`` the search key is therefore
  deliberately too broad, and ``Criteria.matches`` re-checks each candidate
  against the real semantics using its fetched headers.
* A body test is a substring match on both sides, and the server's answer
  is taken as final: re-checking it would mean fetching every candidate's
  body. So ``--body`` is refused with ``--compare is`` or ``matches``.
  The two sides read slightly different text: Sieve's ``body`` test reads
  the decoded text parts (its default ``:text`` transform), while IMAP
  ``BODY`` searches whatever parts the server decodes, attachments
  possibly among them -- so the retroactive pass can find a word the
  rule would not.
* Dates and read or flagged state mean something only for mail already
  delivered -- a message being delivered is new, unread, and unflagged --
  so they have an IMAP translation and no Sieve one. They are always
  ANDed onto the header and body tests, and IMAP answers them exactly.
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

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
    "parse_age",
    "parse_date",
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
# change a version 1 reader would misread must raise it. A new key is not
# such a change: a version 1 reader refuses a key it does not know, so an
# older mailctl refuses a document using one rather than misreading it.
FILTER_VERSION = 1

# IMAP's date-text month names (RFC 3501 date-month), never the locale's.
IMAP_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)  # fmt: skip

# An age, as --older-than takes it: days or weeks.
AGE_UNITS = {"d": 1, "w": 7}

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_AGE = re.compile(r"(\d+)([dw])")

# Every key a filter document's criteria may hold.
CRITERIA_KEYS = (
    "match",
    "compare",
    "terms",
    "body",
    "since",
    "before",
    "older_than_days",
    "unread",
    "flagged",
)


# ############################################################################
# Helpers
# ############################################################################


# ----------------------------------------------------------------------------
def canonical_header(name: str) -> str:
    """Return the canonical spelling of a header name."""
    return CANONICAL_HEADERS.get(name.strip().lower(), name.strip())


# ----------------------------------------------------------------------------
def parse_date(text: str, what: str = "a date") -> date:
    """Read a calendar date written as ``YYYY-MM-DD``, and only that.

    ``date.fromisoformat`` alone would also take ``20260901`` and week
    dates, which read as typos; one spelling is accepted so a date means
    the same to everyone who reads the command.
    """
    if not _ISO_DATE.fullmatch(text.strip()):
        raise MailctlError(
            f"{what} must be a date as YYYY-MM-DD, not {text!r}"
        )

    try:
        return date.fromisoformat(text.strip())

    except ValueError as exc:
        raise MailctlError(f"{what}: {text!r} is not a real date") from exc


# ----------------------------------------------------------------------------
def parse_age(text: str, what: str = "an age") -> int:
    """Read an age written as ``N`` days (``30d``) or weeks (``3w``), in
    days. Zero is refused: "older than no time at all" is every message."""
    match = _AGE.fullmatch(text.strip())

    if match is None:
        raise MailctlError(
            f"{what} must be a whole number of days or weeks, as 30d or "
            f"3w, not {text!r}"
        )

    days = int(match[1]) * AGE_UNITS[match[2]]

    if days < 1:
        raise MailctlError(f"{what} must be at least 1d, not {text!r}")

    return days


# ----------------------------------------------------------------------------
def imap_date(value: date) -> str:
    """A date as IMAP's search keys take it: ``d-Mon-yyyy``."""
    return f"{value.day}-{IMAP_MONTHS[value.month - 1]}-{value.year}"


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
    """Header and body tests, how to combine and compare them, and the
    date and state filters ANDed on top.

    ``terms`` and ``body`` are the tests ``match`` combines. ``since`` and
    ``before`` are arrival dates (IMAP's internal date, not the Date
    header): on or after ``since``, strictly before ``before``.
    ``older_than`` is an age in days, turned into a ``before`` date when
    the search is made, so a saved filter keeps meaning "older than N
    days" rather than a date that was N days ago once. ``unread`` and
    ``flagged`` require the state; False means "either".
    """

    terms: list[Term] = field(default_factory=list)
    match: str = "any"
    compare: str = "contains"
    body: list[str] = field(default_factory=list)
    since: date | None = None
    before: date | None = None
    older_than: int | None = None
    unread: bool = False
    flagged: bool = False

    # ------------------------------------------------------------------------
    def __post_init__(self):
        if self.match not in MATCH_MODES:
            raise MailctlError(
                f"the match mode must be one of {', '.join(MATCH_MODES)}"
            )

        if self.compare not in COMPARE_OPS:
            raise MailctlError(
                f"the comparison must be one of {', '.join(COMPARE_OPS)}"
            )

        if self.body:
            self._check_body_compare()

        if self.older_than is not None and self.older_than < 1:
            raise MailctlError(
                f"an age must be at least one day, not {self.older_than}"
            )

        if self.since and self.before and self.since >= self.before:
            raise MailctlError(
                f"no message can arrive on or after {self.since} and before "
                f"{self.before}; since must be earlier than before"
            )

    # ------------------------------------------------------------------------
    def __bool__(self) -> bool:
        return bool(self.terms or self.body or self.state_filters())

    # ------------------------------------------------------------------------
    def add(self, header: str, value: str) -> None:
        """Append a term, canonicalizing the header name."""
        if value == "":
            raise MailctlError(f"criterion for {header!r} has an empty value")

        self.terms.append(Term(canonical_header(header), value))

    # ------------------------------------------------------------------------
    def add_body(self, value: str) -> None:
        """Append a body test: the body contains ``value``."""
        if value == "":
            raise MailctlError("a body criterion has an empty value")

        self.body.append(value)
        self._check_body_compare()

    # ------------------------------------------------------------------------
    def _check_body_compare(self) -> None:
        """Refuse a body test under ``is`` or ``matches``.

        IMAP can only say whether a body contains a string, and re-checking
        a whole-body comparison would mean fetching every candidate's body;
        a whole-body ``is`` is never what anyone means anyway. Refusing is
        what keeps the rule and the existing-mail pass in agreement.
        """
        if self.compare != "contains":
            raise MailctlError(
                f"a body criterion is always a substring test, so it "
                f"cannot be combined with the {self.compare!r} comparison; "
                f"only 'contains' (the default) can be used with it",
                code="body_compare",
                fields={"compare": self.compare},
            )

    # ------------------------------------------------------------------------
    def state_filters(self) -> list[str]:
        """The date and state filters given, by name, in a fixed order."""
        given = {
            "since": self.since is not None,
            "before": self.before is not None,
            "older-than": self.older_than is not None,
            "unread": self.unread,
            "flagged": self.flagged,
        }

        return [name for name, present in given.items() if present]

    # ------------------------------------------------------------------------
    def require_terms(self) -> None:
        """Fail unless at least one criterion was given.

        A rule with no conditions would match every message, which is never
        what someone meant to type.
        """
        if not self:
            raise MailctlError("no criteria given", code="no_criteria")

    # ------------------------------------------------------------------------
    def check_deliverable(self) -> None:
        """Refuse date and state filters in a rule run at delivery.

        A message being delivered is new: unread, unflagged, and zero days
        old. A test on any of those is always true or never true there, so
        a saved rule carrying one would not mean what it says.
        """
        given = self.state_filters()

        if given:
            before = (
                f"a saved rule cannot test a message's date or read or "
                f"flagged state (given: {', '.join(given)}): a rule runs "
                f"as mail is delivered, when every message is new, unread, "
                f"and unflagged. They select mail already delivered"
            )

            raise MailctlError(
                f"{before}, for a search to list or an apply to act on.",
                code="state_in_rule",
                fields={
                    "before": before,
                    "operations": ("mail search", "filter apply"),
                },
            )

    # ------------------------------------------------------------------------
    def to_dict(self) -> dict:
        """The criteria as plain data, the filter document's ``criteria``.

        A criterion not given is left out, so a document of header terms
        alone reads exactly as it did before bodies, dates, and state.
        """
        data: dict = {"match": self.match, "compare": self.compare}

        if self.terms:
            data["terms"] = [
                {"header": term.header, "value": term.value}
                for term in self.terms
            ]

        if self.body:
            data["body"] = list(self.body)

        if self.since is not None:
            data["since"] = self.since.isoformat()

        if self.before is not None:
            data["before"] = self.before.isoformat()

        if self.older_than is not None:
            data["older_than_days"] = self.older_than

        if self.unread:
            data["unread"] = True

        if self.flagged:
            data["flagged"] = True

        return data

    # ------------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data) -> "Criteria":
        """Read ``to_dict``'s shape back, refusing anything else.

        ``match`` and ``compare`` default as their flags do. Every other key
        is optional, but at least one criterion must be there, and
        ``terms`` and ``body``, when present, may not be empty. An unknown
        key is refused rather than ignored, so a misspelt ``comapre``
        cannot quietly fall back to the default.
        """
        _require_keys(data, "criteria", set(), CRITERIA_KEYS)

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

        criteria = cls(match=match, compare=compare, **_filters(data))
        terms = data.get("terms")

        if terms is not None and (not isinstance(terms, list) or not terms):
            raise MailctlError("filter: 'terms' must be a non-empty list")

        for index, term in enumerate(terms or []):
            where = f"terms[{index}]"
            _require_keys(term, where, {"header", "value"})

            if not all(isinstance(term[key], str) for key in term):
                raise MailctlError(
                    f"filter: {where} 'header' and 'value' must be strings"
                )

            if not term["header"].strip():
                raise MailctlError(f"filter: {where} has an empty header")

            criteria.add(term["header"], term["value"])

        body = data.get("body")

        if body is not None:
            if not isinstance(body, list) or not body:
                raise MailctlError("filter: 'body' must be a non-empty list")

            if not all(isinstance(value, str) and value for value in body):
                raise MailctlError(
                    "filter: every 'body' value must be a non-empty string"
                )

            for value in body:
                criteria.add_body(value)

        if not criteria:
            raise MailctlError(
                "filter: criteria holds no terms, body, dates, or state; a "
                "filter must test something"
            )

        return criteria

    # ------------------------------------------------------------------------
    def describe(self) -> str:
        """Render the criteria as one human-readable line."""
        joiner = " OR " if self.match == "any" else " AND "

        tests = [
            f"{term.header} {self.compare} {term.value!r}"
            for term in self.terms
        ]
        tests += [f"body contains {value!r}" for value in self.body]
        text = joiner.join(tests)

        state = []

        if self.since is not None:
            state.append(f"received on or after {self.since.isoformat()}")

        if self.before is not None:
            state.append(f"received before {self.before.isoformat()}")

        if self.older_than is not None:
            plural = "" if self.older_than == 1 else "s"
            state.append(f"older than {self.older_than} day{plural}")

        state += [
            name for name in ("unread", "flagged") if getattr(self, name)
        ]

        if not state:
            return text

        if self.match == "any" and len(tests) > 1:
            text = f"({text})"

        return " AND ".join([text, *state] if tests else state)

    # ########################################################################
    # Sieve
    # ########################################################################

    # ------------------------------------------------------------------------
    def sieve_matchtype(self) -> str:
        """Return the sievelib matchtype for the ``--match`` mode."""
        return "anyof" if self.match == "any" else "allof"

    # ------------------------------------------------------------------------
    def sieve_conditions(self) -> list[tuple[str, ...]]:
        """Return the Sieve condition tuples for these criteria.

        A header term is ``(header, :comparator, value)``, a ``header``
        test on that name whatever the name spells (#175); a body term is
        ``("body", ":text", ":contains", value)``, the ``body`` extension's
        test over the message's text parts (RFC 5173's default transform,
        written out). The two are told apart by length. Names and values
        are escaped here, since they are quoted but not escaped on the way
        into the script. Date and state filters have no Sieve form and are
        refused.
        """
        self.check_deliverable()
        self.require_terms()

        tag = f":{self.compare}"

        conditions: list[tuple[str, ...]] = [
            (
                escape_sieve_string(term.header),
                tag,
                escape_sieve_string(term.value),
            )
            for term in self.terms
        ]
        conditions += [
            ("body", ":text", ":contains", escape_sieve_string(value))
            for value in self.body
        ]

        return conditions

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
    def imap_search_key(
        self, extra: Sequence = (), today: date | None = None
    ) -> list:
        """Return an IMAPClient search key for these criteria.

        ``all`` becomes IMAP's implicit AND (adjacent keys); ``any`` becomes
        a right-nested chain of the binary ``OR`` key. The date and state
        filters (``SINCE``, ``BEFORE``, ``UNSEEN``, ``FLAGGED``) are ANDed
        after them, and ``extra`` on top of everything, for callers that
        want to add ``NOT DELETED`` and the like. ``today`` is what an
        ``older_than`` age counts back from; the local date by default.
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
        keys += [["BODY", value] for value in self.body]

        if not keys:
            combined = []

        elif self.match == "all":
            combined = list(keys)

        else:
            combined = [_or_chain(keys)]

        return [*combined, *self._imap_state_keys(today), *extra]

    # ------------------------------------------------------------------------
    def _imap_state_keys(self, today: date | None) -> list:
        """The date and state filters as flat IMAP search keys."""
        keys: list = []

        if self.since is not None:
            keys += ["SINCE", imap_date(self.since)]

        if self.before is not None:
            keys += ["BEFORE", imap_date(self.before)]

        if self.older_than is not None:
            cutoff = (today or date.today()) - timedelta(days=self.older_than)
            keys += ["BEFORE", imap_date(cutoff)]

        if self.unread:
            keys.append("UNSEEN")

        if self.flagged:
            keys.append("FLAGGED")

        return keys

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

        What the headers cannot show is taken from the server's answer. A
        body term counts as matched: under ``all`` the search required it,
        and under ``any`` every term is a substring test (a body term
        forbids any other comparison), which IMAP answers exactly, so the
        server's candidate is already right. The date and state filters
        were ANDed into the search and IMAP answers them exactly too.
        """
        results = [
            self._term_matches(term, headers.get(term.header.upper(), ()))
            for term in self.terms
        ]
        results += [True for _ in self.body]

        if not results:
            return True

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
    since they govern the whole set. Body tests from both are kept, and a
    date or state filter the explicit criteria give wins.
    """
    named = {term.header.upper() for term in explicit.terms}

    kept = [term for term in derived.terms if term.header.upper() not in named]

    return Criteria(
        terms=[*kept, *explicit.terms],
        match=explicit.match,
        compare=explicit.compare,
        body=[*derived.body, *explicit.body],
        since=explicit.since or derived.since,
        before=explicit.before or derived.before,
        older_than=explicit.older_than or derived.older_than,
        unread=explicit.unread or derived.unread,
        flagged=explicit.flagged or derived.flagged,
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
def _filters(data: dict) -> dict:
    """The date and state filters of a document's criteria, as ``Criteria``
    takes them, each checked for its type."""
    filters: dict = {}

    for key in ("since", "before"):
        if key in data:
            value = data[key]

            if not isinstance(value, str):
                raise MailctlError(
                    f"filter: {key!r} must be a YYYY-MM-DD string"
                )

            filters[key] = parse_date(value, f"filter: {key!r}")

    if "older_than_days" in data:
        days = data["older_than_days"]

        # bool is an int in Python, and true is not an age.
        if isinstance(days, bool) or not isinstance(days, int) or days < 1:
            raise MailctlError(
                "filter: 'older_than_days' must be a whole number, 1 or more"
            )

        filters["older_than"] = days

    for key in ("unread", "flagged"):
        if key in data:
            if not isinstance(data[key], bool):
                raise MailctlError(f"filter: {key!r} must be true or false")

            filters[key] = data[key]

    return filters


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
