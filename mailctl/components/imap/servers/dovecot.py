"""Dovecot, the IMAP server MXroute runs.

No Dovecot quirk is known yet, so its profile carries none; this module
exists so that the first one has a home and its selection is already
tested. Recognised by the ``name`` field of the IMAP ``ID`` response
(RFC 2971), which on MXroute reads ``Dovecot`` with no version (mailctl
#18).

*CREATE does not subscribe* (mailctl #38, #39) is observed on Dovecot but
is not a quirk: RFC 3501 never has CREATE subscribe, so it is the plain
behaviour, and the session verifies the subscription for every server
rather than assuming it for this one.
"""

from .profile import ServerProfile

__all__ = ["PROFILE", "matches"]

PROFILE = ServerProfile("dovecot")


# ----------------------------------------------------------------------------
def matches(identity: dict[str, str]) -> bool:
    """Return whether the server's ``ID`` says it is Dovecot.

    A substring test, so a name that carries a version or a vendor prefix
    is still recognised -- the version is never what decides it.
    """
    return "dovecot" in identity.get("name", "").lower()
