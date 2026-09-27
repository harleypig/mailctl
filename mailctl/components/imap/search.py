"""What a search needs from its criteria, as a protocol.

The session searches by a caller's criteria without importing mailctl's
criteria model: anything with an IMAPClient search key and a re-check
against a message's headers will do. That keeps this library free of the
model, which belongs to the layer above (ADR 0006).
"""

from collections.abc import Mapping, Sequence
from typing import Protocol

__all__ = ["SearchCriteria"]


class SearchCriteria(Protocol):
    """Criteria the session can search by and re-check."""

    def imap_search_key(self) -> list:
        """Return the IMAPClient search key that narrows the mailbox."""
        ...

    def matches(self, headers: Mapping[str, Sequence[str]]) -> bool:
        """Return whether decoded headers really match.

        ``headers`` maps an upper-cased header name to every occurrence.
        """
        ...
