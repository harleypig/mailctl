"""Text from the server, made safe to put on a terminal.

A filter's name, a header value, and a server's alert are the server's
words, or whoever wrote the mail's. Textual draws them through Rich, so
they are handed over as ``Text`` and never as markup, and every control
character and bidi override is shown as its escape rather than sent to the
terminal, as the CLI does.
"""

import re

__all__ = ["block", "one_line"]

UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f‪-‮⁦-⁩\ud800-\udfff]")

# A block keeps its lines and tabs; a lone carriage return would let a
# line overprint the one before it, so it is escaped like the rest.
UNSAFE_IN_BLOCK = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f‪-‮⁦-⁩\ud800-\udfff]"
    r"|\r(?!\n)"
)


# ----------------------------------------------------------------------------
def _escape(match: re.Match) -> str:
    code = ord(match.group())

    return f"\\x{code:02x}" if code <= 0xFF else f"\\u{code:04x}"


# ----------------------------------------------------------------------------
def one_line(value: str) -> str:
    """``value`` on one line: whitespace runs, newlines included, become
    one space, so it cannot forge a row; the rest is escaped."""
    return UNSAFE.sub(_escape, " ".join(value.split()))


# ----------------------------------------------------------------------------
def block(value: str) -> str:
    """``value`` with its lines kept, as a message body is shown: newlines
    and tabs pass, every other control character is escaped, and a line
    break written as CRLF becomes a newline."""
    return UNSAFE_IN_BLOCK.sub(_escape, value).replace("\r\n", "\n")
