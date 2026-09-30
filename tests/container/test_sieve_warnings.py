"""A ManageSieve WARNINGS from a real Pigeonhole reaches the user (#208).

Pigeonhole accepts ``addflag "\\\\Bogus"`` -- a system flag IMAP does not
have -- and warns that it will be ignored, on CHECKSCRIPT and again on
PUTSCRIPT. ``add --flag '\\Bogus'`` is mailctl emitting exactly that.
"""

import re

import pytest

pytestmark = pytest.mark.container

# Pigeonhole's wording, naming the stored script and a line of it.
WARNING = re.compile(
    r"mailctl: warning from the filter server: mailctl: line \d+: warning: "
    r"IMAP flag '\\Bogus' specified for the addflag command is invalid and "
    r"will be ignored \(only first invalid is reported\)\.\n"
)


# ----------------------------------------------------------------------------
def test_the_putscript_warning_is_shown_once_on_stderr(account):
    """Red if an OK's WARNINGS text is dropped, or shown only under
    --verbose, or anywhere but stderr, or CHECKSCRIPT's is shown too."""
    result = account.run(
        "filter", "add", "--from", "x@example.test", "--flag", "\\Bogus"
    )

    assert result.code == 0, result.err
    assert WARNING.fullmatch(result.err), result.err
    assert "Bogus" in (account.script("mailctl") or "")


# ----------------------------------------------------------------------------
def test_verbose_also_shows_the_checkscript_warning_quoted(account):
    result = account.run(
        "filter", "add", "--from", "x@example.test", "--flag", "\\Bogus", "-v"
    )

    assert result.code == 0, result.err
    assert WARNING.fullmatch(result.err), result.err
    assert re.search(
        r"^\[filter\] CHECKSCRIPT warned: [\"'].*: line \d+: warning: "
        r"IMAP flag",
        result.out,
        re.MULTILINE,
    ), result.out


# ----------------------------------------------------------------------------
def test_a_script_the_server_does_not_warn_about_shows_nothing(account):
    """The control: a warning shown for every upload would pass above."""
    result = account.run(
        "filter", "add", "--from", "x@example.test", "--flag", "\\Flagged"
    )

    assert result.code == 0, result.err
    assert result.err == ""
