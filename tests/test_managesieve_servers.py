"""Server modules are chosen by what the server says it is, not by config.

``select_server`` reads the ``IMPLEMENTATION`` capability. A server no
module recognises gets the plain protocol, because an unknown server is the
normal case for a new host rather than an error. And the choice never turns
on a version number: MXroute's server reports none (mailctl #18), so a
version-keyed match could never fire there (ADR 0006).
"""

import pytest

from mailctl.components.managesieve.capabilities import (
    Capabilities,
    parse_capabilities,
)
from mailctl.components.managesieve.servers import (
    PLAIN,
    pigeonhole,
    select_server,
)


# ----------------------------------------------------------------------------
def advertising(implementation: str | None) -> Capabilities:
    """Return a capability set carrying ``implementation``, if any."""
    entries = [("SIEVE", "fileinto")]

    if implementation is not None:
        entries.insert(0, ("IMPLEMENTATION", implementation))

    return Capabilities(tuple(entries))


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "implementation",
    [
        pytest.param("Dovecot Pigeonhole", id="as-mxroute-reports-it"),
        pytest.param("Dovecot Pigeonhole 0.5.21", id="with-a-version"),
        pytest.param("dovecot pigeonhole", id="lower-case"),
    ],
)
def test_pigeonhole_is_recognised_by_its_implementation(implementation):
    assert select_server(advertising(implementation)) is pigeonhole.PROFILE


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "implementation",
    [
        pytest.param("Cyrus timsieved v3.8.1", id="another-server"),
        pytest.param("", id="empty"),
        pytest.param(None, id="not-advertised"),
    ],
)
def test_an_unrecognised_server_gets_the_plain_protocol(implementation):
    assert select_server(advertising(implementation)) is PLAIN


# ############################################################################
# The capability parser the selection reads
# ############################################################################


# ----------------------------------------------------------------------------
def test_quoted_values_are_unescaped_and_bare_names_have_no_value():
    capabilities = parse_capabilities(
        b'"OWNER" "a\\"b\\\\c"\r\n"STARTTLS"\r\n"NOTIFY" ""\r\n'
    )

    assert capabilities.entries == (
        ("OWNER", 'a"b\\c'),
        ("STARTTLS", None),
        ("NOTIFY", ""),
    )


# ----------------------------------------------------------------------------
def test_lookups_ignore_case():
    capabilities = parse_capabilities(b'"Implementation" "X"\r\n')

    assert capabilities.implementation == "X"
    assert "IMPLEMENTATION" in capabilities


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(b'"MAXREDIRECTS" "10"\r\n', 10, id="numeric"),
        pytest.param(b'"MAXREDIRECTS" "lots"\r\n', None, id="not-a-number"),
        pytest.param(b"", None, id="absent"),
    ],
)
def test_maxredirects_is_a_number_or_nothing(raw, expected):
    assert parse_capabilities(raw).max_redirects == expected


# ----------------------------------------------------------------------------
def test_an_empty_response_is_an_empty_set():
    capabilities = parse_capabilities(b"")

    assert capabilities.entries == ()
    assert capabilities.sieve_extensions == ()
    assert capabilities.unauthenticate is False
