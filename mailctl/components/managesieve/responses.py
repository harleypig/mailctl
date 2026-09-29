"""A ManageSieve status response's text, and the ``WARNINGS`` it carries.

RFC 5804 section 1.3: an ``OK (WARNINGS)`` says a script is valid but
*"might contain errors not intended by the script writer"*, and a client
seeing one *"SHOULD present the returned warning text to the user"*. It
is typically the answer to PUTSCRIPT or CHECKSCRIPT. The text is a string
(section 4, ``response-ok``): quoted, or a literal, which is how Pigeonhole
sends it -- one warning per line. Offline.
"""

import re
from dataclasses import dataclass

__all__ = [
    "ServerWarning",
    "literal_size",
    "response_text",
    "warning_in",
]

# "(" resp-code ")", then an optional string. Only the code's name is
# kept; WARNINGS takes no argument.
_RESP_CODE = re.compile(rb"\(([^)\s]+)[^)]*\)\s*(.*)")
_QUOTED = re.compile(rb'"((?:[^"\\]|\\.)*)"', re.DOTALL)
_LITERAL = re.compile(rb"\{(\d+)\+?\}")
_ESCAPE = re.compile(rb"\\(.)", re.DOTALL)


@dataclass(frozen=True)
class ServerWarning:
    """A warning the server sent about a script, in its own words.

    ``text`` is the server's, untrusted, and may run to several lines: it
    is shown to a person, so whatever shows it neutralises control
    characters first.
    """

    text: str


# ----------------------------------------------------------------------------
def response_text(rest: bytes) -> tuple[bytes | None, bytes]:
    """Split what follows OK, NO, or BYE into its code and its string.

    The code is upper-cased, or None; the string is returned as sent --
    quoted, a literal's ``{n}``, or empty.
    """
    match = _RESP_CODE.fullmatch(rest.strip())

    if match is None:
        return (None, rest.strip())

    return (match[1].upper(), match[2])


# ----------------------------------------------------------------------------
def literal_size(string: bytes) -> int | None:
    """The size a literal's ``{n}`` announces, or None for any other string."""
    match = _LITERAL.fullmatch(string)

    return None if match is None else int(match[1])


# ----------------------------------------------------------------------------
def warning_in(code: bytes | None, text: bytes) -> ServerWarning | None:
    """The warning an OK carries, given its code and its string's content.

    ``text`` is a quoted string as sent, or a literal's octets already
    read. A ``WARNINGS`` with nothing to say is None, like no warning.
    """
    if code != b"WARNINGS":
        return None

    quoted = _QUOTED.fullmatch(text)

    if quoted is not None:
        text = _ESCAPE.sub(rb"\1", quoted[1])

    if not text.strip():
        return None

    return ServerWarning(text.decode("utf-8", "replace").strip())
