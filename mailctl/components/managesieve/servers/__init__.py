"""Server-software modules, chosen by probing, never by configuration.

:func:`select_server` reads the ``IMPLEMENTATION`` capability and returns
the matching module's profile. A server nothing matches gets
:data:`~.profile.PLAIN` -- an unknown server is the normal case for a new
host, not an error (ADR 0006).
"""

from ..capabilities import Capabilities
from . import pigeonhole
from .profile import PLAIN, ServerProfile

__all__ = ["PLAIN", "ServerProfile", "select_server"]

_KNOWN = (pigeonhole,)


# ----------------------------------------------------------------------------
def select_server(capabilities: Capabilities) -> ServerProfile:
    """Return the profile of the server that sent ``capabilities``."""
    for server in _KNOWN:
        if server.matches(capabilities):
            return server.PROFILE

    return PLAIN
