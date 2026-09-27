"""Dovecot Pigeonhole, the ManageSieve server MXroute runs.

No Pigeonhole quirk is known yet, so its profile carries none; this module
exists so that the first one has a home and its selection is already
tested. Recognised by the ``IMPLEMENTATION`` capability, which on MXroute
reads ``Dovecot Pigeonhole`` with no version (mailctl #18).
"""

from ..capabilities import Capabilities
from .profile import ServerProfile

__all__ = ["PROFILE", "matches"]

PROFILE = ServerProfile("pigeonhole")


# ----------------------------------------------------------------------------
def matches(capabilities: Capabilities) -> bool:
    """Return whether the server says it is Pigeonhole.

    A substring test, so a server that appends a version is still
    recognised -- the version is never what decides it.
    """
    return "pigeonhole" in (capabilities.implementation or "").lower()
