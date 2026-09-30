"""How ``add`` and ``apply`` take their criteria (#149).

Criteria flags, or ``--filter FILE|-`` (the document ``search
--build-filter --json`` prints), never both; ``--like UID`` pre-fills them
from a message. Everything here is decided before connecting, so it is
tested on parsed arguments alone. The CLI snapshots show the same cases end
to end, with no server call made.
"""

import io
import sys

import pytest

from mailctl import MailctlError
from mailctl.cli import build_parser, criteria_given
from mailctl.criteria import Criteria, dump_filter


# ----------------------------------------------------------------------------
def given(*argv: str, command: str = "add") -> Criteria:
    return criteria_given(
        build_parser().parse_args(["filter", command, *argv])
    )


# ----------------------------------------------------------------------------
def github() -> Criteria:
    criteria = Criteria(match="all", compare="is")
    criteria.add("From", "noreply@github.com")
    criteria.add("Subject", "Issue")

    return criteria


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("command", ["add", "apply"])
def test_a_filter_file_reads_back_what_build_filter_wrote(command, tmp_path):
    """The round trip the two commands exist for: search writes it, add
    and apply read it, and nothing about the criteria changes -- match
    and compare included."""
    path = tmp_path / "filter.json"
    path.write_text(dump_filter(github()), encoding="utf-8")

    read = given("--filter", str(path), command=command)

    assert read.to_dict() == github().to_dict()


# ----------------------------------------------------------------------------
def test_a_dash_reads_the_filter_from_standard_input(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(dump_filter(github())))

    assert given("--filter", "-").to_dict() == github().to_dict()


# ----------------------------------------------------------------------------
def test_a_filter_file_that_is_not_utf8_is_refused_by_name(tmp_path):
    path = tmp_path / "filter.json"
    path.write_bytes(b'{"version": 1, "criteria": "\xff"}')

    with pytest.raises(MailctlError, match="is not UTF-8 text"):
        given("--filter", str(path))


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("argv", "refusal"),
    [
        pytest.param(
            ["--filter", "-", "--from", "x"], "not both", id="with-flags"
        ),
        pytest.param(
            ["--filter", "-", "--compare", "is"], "not both", id="with-compare"
        ),
        pytest.param(
            ["--filter", "-", "--like", "2"], "give one", id="with-like"
        ),
        pytest.param(
            ["--derive", "from", "--to", "x"], "needs --like", id="derive"
        ),
        pytest.param([], "no criteria given", id="nothing"),
    ],
)
def test_a_conflict_is_refused_without_reading_anything(
    argv, refusal, monkeypatch
):
    """Refused before the filter is read, so a bad pairing never consumes
    standard input -- and never reaches a login."""
    stdin = io.StringIO(dump_filter(github()))
    monkeypatch.setattr(sys, "stdin", stdin)

    with pytest.raises(MailctlError, match=refusal):
        given(*argv, command="apply")

    assert stdin.tell() == 0


# ----------------------------------------------------------------------------
def test_like_alone_leaves_the_criteria_to_the_message():
    """Empty is not an error with --like: the message supplies them."""
    assert not given("--like", "2")


# ----------------------------------------------------------------------------
def test_like_keeps_the_flags_it_is_given():
    """They are merged over what the message gives, as in 'search'."""
    criteria = given("--like", "2", "--subject", "Issue", "--match", "all")

    assert criteria.match == "all"
    assert [term.header for term in criteria.terms] == ["Subject"]
