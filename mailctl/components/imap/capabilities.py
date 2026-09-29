"""The IMAP capabilities this component behaves differently without.

Offline: a name list, so an offline caller -- a provider's dialect judging
what a changed capability list means -- can read it without importing the
session. ``tests/test_imap.py`` derives the set from every
``has_capability`` call in this package and holds it equal to this one.
"""

__all__ = ["CHECKED_CAPABILITIES"]

# ID picks the server profile, MOVE and UIDPLUS shape the move, and
# NAMESPACE places a new folder; losing one changes what mailctl does.
CHECKED_CAPABILITIES = frozenset({"ID", "MOVE", "NAMESPACE", "UIDPLUS"})
