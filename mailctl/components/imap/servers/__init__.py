"""Server-software modules, chosen by probing, never by configuration.

:func:`select_server` reads the IMAP ``ID`` response (RFC 2971) and
returns the matching module's profile. A server nothing matches -- or one
that sends no ``ID`` at all -- gets :data:`~.profile.PLAIN`: an unknown
server is the normal case for a new host, not an error (ADR 0006).
"""

from . import dovecot
from .profile import PLAIN, ServerProfile

__all__ = ["PLAIN", "ServerProfile", "select_server"]

_KNOWN = (dovecot,)


# ----------------------------------------------------------------------------
def select_server(identity: dict[str, str]) -> ServerProfile:
    """Return the profile of the server that sent ``identity``.

    ``identity`` is the ``ID`` response as a mapping of lower-cased field
    names to values; an empty mapping is a server that sent none.
    """
    for server in _KNOWN:
        if server.matches(identity):
            return server.PROFILE

    return PLAIN
