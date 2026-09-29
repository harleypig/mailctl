"""IMAP ``ALERT`` response codes, read out of a server's response lines.

RFC 9051 section 7.1: the text after ``[ALERT]`` *"is presented to the
user in a fashion that calls the user's attention to the message"*, and an
alert received once TLS is up *"MUST be presented to the user"*. An alert
can arrive on any status response -- the greeting, an untagged ``OK``,
``NO``, ``BAD``, or ``BYE``, or a command's tagged completion -- so the
session reads every line for one; this module is the offline half, which
says whether a line carries one and what it says. Offline.
"""

import re
from dataclasses import dataclass

__all__ = ["ServerAlert", "alert_in_line", "alert_in_text"]

# A status response, tagged or untagged, and its resp-text (RFC 9051
# section 9: greeting, resp-cond-state, resp-cond-bye, resp-cond-auth).
# Atoms are case-insensitive.
_STATUS_LINE = re.compile(
    rb"[^ ]+ (?:OK|NO|BAD|BYE|PREAUTH) (.*)", re.IGNORECASE | re.DOTALL
)

# resp-text = ["[" resp-text-code "]" SP] [text]
_ALERT_TEXT = re.compile(rb"\[ALERT\](?: (.*))?", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class ServerAlert:
    """An alert the server sent, in its own words.

    ``text`` is the server's, untrusted: it is shown to a person, so
    whatever shows it neutralises control characters first.
    """

    text: str


# ----------------------------------------------------------------------------
def alert_in_text(text: bytes) -> ServerAlert | None:
    """The alert a status response's resp-text carries, if any.

    An ``[ALERT]`` with no text after it says nothing to show, and is None.
    """
    match = _ALERT_TEXT.fullmatch(text)

    if match is None or not (match[1] or b"").strip():
        return None

    return ServerAlert(match[1].decode("utf-8", "replace"))


# ----------------------------------------------------------------------------
def alert_in_line(line: bytes) -> ServerAlert | None:
    """The alert a whole response line carries, CRLF removed, if any."""
    match = _STATUS_LINE.fullmatch(line)

    return None if match is None else alert_in_text(match[1])
