"""What a search needs from its criteria, and how its key reaches the wire.

The session searches by a caller's criteria without importing mailctl's
criteria model: anything with an IMAPClient search key and a re-check
against a message's headers will do. That keeps this library free of the
model, which belongs to the layer above (ADR 0006).

:func:`encode_search_key` closes ADR 0006's gap I1 (mailctl #89): a
non-ASCII value in a nested key cannot be sent by IMAPClient as given.
"""

from collections.abc import Mapping, Sequence
from typing import Protocol

from ... import MailctlError

__all__ = ["SEARCH_CHARSET", "SearchCriteria", "encode_search_key"]

SEARCH_CHARSET = "UTF-8"

# ANDed onto a group to end it on an atom; matches every message, so the
# group means what it did.
_NEUTRAL_KEY = b"ALL"


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


# ----------------------------------------------------------------------------
def encode_search_key(key: list) -> tuple[list, str | None]:
    """Return ``key`` ready for ``IMAPClient.search`` and the charset to send.

    An all-ASCII key comes back untouched with no charset, so it is sent
    exactly as before -- US-ASCII is the one charset RFC 3501 requires a
    server to support.

    Otherwise every text value is encoded to UTF-8 bytes and the charset
    is ``UTF-8``. Two IMAPClient 4.x defects make both halves necessary:

    - It encodes the items of a nested list with no charset at all
      (upstream mjs/imapclient#645), so a nested non-ASCII ``str`` raises
      ``UnicodeEncodeError`` whatever ``charset`` says. Bytes are passed
      through, which is why every value is pre-encoded.
    - It sends an 8-bit item as a literal after gluing the group's closing
      parenthesis onto it, so ``(SUBJECT café)`` goes out as the literal
      ``café)`` and the group never closes. A group ending on an 8-bit
      value therefore gains a trailing ``ALL``, which leaves the literal
      whole and the meaning unchanged.

    Raises :class:`MailctlError` for a value no charset can encode -- a
    command-line argument that was not valid UTF-8 arrives as a lone
    surrogate.
    """
    if _is_ascii(key):
        return key, None

    return _encode(key, group=False), SEARCH_CHARSET


# ----------------------------------------------------------------------------
def _is_ascii(item) -> bool:
    """Whether a key, or any value nested in it, is pure ASCII."""
    if isinstance(item, (list, tuple)):
        return all(_is_ascii(part) for part in item)

    if isinstance(item, (str, bytes)):
        return item.isascii()

    return True


# ----------------------------------------------------------------------------
def _encode(items, group: bool) -> list:
    """Encode every text value in ``items``, padding a group that needs it."""
    encoded = []

    for item in items:
        if isinstance(item, (list, tuple)):
            encoded.append(_encode(item, group=True))

        elif isinstance(item, str):
            try:
                encoded.append(item.encode(SEARCH_CHARSET))

            except UnicodeEncodeError as exc:
                raise MailctlError(
                    f"cannot search for {item!r}: it is not valid text "
                    f"({exc.reason}); re-type the value"
                ) from exc

        else:
            encoded.append(item)

    last = encoded[-1] if encoded else None

    if group and isinstance(last, bytes) and not last.isascii():
        encoded.append(_NEUTRAL_KEY)

    return encoded
