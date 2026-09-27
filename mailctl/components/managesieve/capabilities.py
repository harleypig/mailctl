"""The server's CAPABILITY response, parsed whole (RFC 5804 section 1.7).

sievelib keeps only the seven capabilities it knows and drops the rest --
``MAXREDIRECTS``, ``OWNER``, and ``UNAUTHENTICATE`` among them. This parses
the raw response itself, so every capability the server sent is kept, in
the order it sent them, as data a caller can read, print, or test against.
"""

import re
from dataclasses import dataclass

__all__ = ["Capabilities", "parse_capabilities"]

# A capability line is a quoted name and, optionally, a quoted value. The
# unquoted alternative is a tolerance for servers that skip the quotes,
# which sievelib also tolerated.
_TOKEN = re.compile(rb'"((?:[^"\\]|\\.)*)"|(\S+)')
_ESCAPE = re.compile(rb"\\(.)")


@dataclass(frozen=True)
class Capabilities:
    """Every capability a server advertised, as ``(name, value)`` pairs.

    ``value`` is None for a capability sent bare, such as ``STARTTLS``.
    Names are as the server sent them; lookups ignore case, since RFC 5804
    names are case-insensitive.
    """

    entries: tuple[tuple[str, str | None], ...] = ()

    # ------------------------------------------------------------------------
    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and any(
            key.upper() == name.upper() for key, _value in self.entries
        )

    # ------------------------------------------------------------------------
    def get(self, name: str) -> str | None:
        """Return the value of ``name``, or None if absent or bare."""
        for key, value in self.entries:
            if key.upper() == name.upper():
                return value

        return None

    # ------------------------------------------------------------------------
    @property
    def implementation(self) -> str | None:
        """The server's self-description, e.g. ``Dovecot Pigeonhole``."""
        return self.get("IMPLEMENTATION")

    # ------------------------------------------------------------------------
    @property
    def sieve_extensions(self) -> tuple[str, ...]:
        """The Sieve extensions the server supports, in its order."""
        return tuple((self.get("SIEVE") or "").split())

    # ------------------------------------------------------------------------
    @property
    def max_redirects(self) -> int | None:
        """The most ``redirect`` actions one script run may take, if sent."""
        value = self.get("MAXREDIRECTS")

        return int(value) if value is not None and value.isdigit() else None

    # ------------------------------------------------------------------------
    @property
    def owner(self) -> str | None:
        """The authenticated user whose scripts these are, if sent."""
        return self.get("OWNER")

    # ------------------------------------------------------------------------
    @property
    def unauthenticate(self) -> bool:
        """Whether the server offers UNAUTHENTICATE."""
        return "UNAUTHENTICATE" in self


# ----------------------------------------------------------------------------
def parse_capabilities(raw: bytes) -> Capabilities:
    """Parse the lines of a CAPABILITY response, minus its final ``OK``.

    A line with no recognisable name is skipped rather than refused: the
    response is advisory, and one malformed line should not cost the rest.
    """
    entries = []

    for line in raw.splitlines():
        tokens = []

        for match in _TOKEN.finditer(line):
            quoted, bare = match.groups()
            tokens.append(
                bare if quoted is None else _ESCAPE.sub(rb"\1", quoted)
            )

        if not tokens:
            continue

        name, *rest = (
            token.decode("utf-8", errors="replace") for token in tokens
        )
        entries.append((name, rest[0] if rest else None))

    return Capabilities(tuple(entries))
