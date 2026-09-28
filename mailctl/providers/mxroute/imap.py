"""MXroute's side of the IMAP connection: its login advice and its settings.

The layer-1 ``imap`` library knows the protocol. What lives here is what
MXroute has set up and what mailctl's configuration says: the mapping from
``Config`` to a session (port 143 is STARTTLS, anything else implicit
TLS), and the advice a failure gains -- MXroute expects the full email
address as the username.

The spam folder is the same naming trap on this host: on MXroute it is
``INBOX.spam`` in lower case, and filing into ``Junk`` would silently
create a second folder next to the real one. Folder names are matched
against the server's own list rather than taken on trust (the dialect's
``normalize``), which is what catches it; nothing here names the folder.

It is the transport's side of the provider. What the server's advertised
capabilities mean for mailctl is the dialect's (``dialect.py``).
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from ... import MailctlError
from ...components.imap.client import (
    ImapAuthenticationError,
    ImapConnectionError,
    ImapSession,
    ImapTLSError,
)
from ...config import Config

__all__ = ["imap_session", "new_imap_session"]

# The one port that means STARTTLS; every other port is implicit TLS.
STARTTLS_PORT = 143


# ----------------------------------------------------------------------------
def new_imap_session(
    config: Config,
    progress: Callable[[str], None] | None = None,
) -> ImapSession:
    """Build an unopened session from ``config``, naming a missing setting.

    Failing on the settings here is friendlier than failing on the socket.
    """
    config.require("imap_host", "user")

    return ImapSession(
        host=config.imap_host,
        port=config.imap_port,
        username=config.user,
        password=config.password,
        tls="starttls" if config.imap_port == STARTTLS_PORT else "ssl",
        progress=progress,
    )


# ----------------------------------------------------------------------------
@contextmanager
def imap_session(
    config: Config,
    progress: Callable[[str], None] | None = None,
) -> Iterator[ImapSession]:
    """Open an IMAP session for ``config`` and close it afterwards.

    A failure to connect or log in gains the advice that fits it: the
    full-address username MXroute expects, the port-to-TLS rule, or the
    settings that name the server.
    """
    session = new_imap_session(config, progress)

    try:
        session.open()

    except ImapAuthenticationError as exc:
        raise MailctlError(
            f"IMAP authentication failed for {config.user!r} (password "
            f"{config.password_state()}). MXRoute expects the FULL "
            f"email address as the username, e.g. you@yourdomain.com. "
            f"[{exc.reason}]"
        ) from exc

    except ImapTLSError as exc:
        raise MailctlError(
            f"{exc} Port 993 is implicit TLS; port 143 uses STARTTLS."
        ) from exc

    except ImapConnectionError as exc:
        raise MailctlError(
            f"{exc} Check --imap-host and --imap-port."
        ) from exc

    try:
        yield session

    finally:
        session.close()
