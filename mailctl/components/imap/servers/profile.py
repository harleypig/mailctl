"""What one server software does beyond or against IMAP4rev1.

A :class:`ServerProfile` carries a server's quirks as data. It has none
yet: :data:`PLAIN` is the protocol as written, and a server module adds a
field here, defaulting to the plain behaviour, the day it has a quirk to
record. A quirk is keyed on behaviour or an advertised capability, never
on a version number (ADR 0006).
"""

from dataclasses import dataclass

__all__ = ["PLAIN", "ServerProfile"]


@dataclass(frozen=True)
class ServerProfile:
    """One server software's deviations from the plain protocol."""

    name: str


PLAIN = ServerProfile("plain")
