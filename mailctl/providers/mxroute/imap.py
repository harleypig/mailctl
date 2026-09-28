"""MXroute's side of IMAP: its login advice, its folders, its settings.

It also says what the server's advertised capabilities mean for mailctl,
as the facts ``mailctl test`` reports.

The layer-1 ``imap`` library knows the protocol. What lives here is what
MXroute has set up and what mailctl's configuration says: the mapping from
``Config`` to a session (port 143 is STARTTLS, anything else implicit
TLS), and the advice a failure gains -- MXroute expects the full email
address as the username.

The spam folder is the same naming trap on this host: on MXroute it is
``INBOX.spam`` in lower case, and filing into ``Junk`` would silently
create a second folder next to the real one. The layer-1 session matches
folder names against the server's own list rather than taking them on
trust, which is what catches it; nothing here names the folder.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from ... import MailctlError
from ...components.imap import (
    ImapAuthenticationError,
    ImapConnectionError,
    ImapSession,
    ImapTLSError,
)
from ...config import Config
from ..model import Fact

__all__ = ["capability_facts", "imap_session", "new_imap_session"]

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


# ----------------------------------------------------------------------------
def capability_facts(capabilities: list[str]) -> list[Fact]:
    """What MOVE, UIDPLUS, and FILTER=SIEVE mean here, one fact each.

    Dovecot's Pigeonhole ``imap_filter_sieve`` plugin advertises
    ``FILTER=SIEVE``, which lets a client ask the *server* to run a Sieve
    script over messages matching an IMAP search -- the retroactive pass
    done properly, server-side. It is experimental and off by default, so
    it is almost certainly absent here; one CAPABILITY line settles it
    either way, and an answer on the record beats an assumption.
    """
    move = "MOVE" in capabilities
    uidplus = "UIDPLUS" in capabilities
    filter_sieve = any(
        item.upper().startswith("FILTER=SIEVE") for item in capabilities
    )

    if filter_sieve:
        server_side = (
            "yes -- this server can apply a Sieve script to existing mail "
            "itself.\nmailctl still uses its own client-side pass; the "
            "server-side path is not implemented."
        )

    else:
        server_side = (
            "no -- no server-side retroactive filtering (Dovecot "
            "imap_filter_sieve is not enabled).\nmailctl's client-side "
            "search-and-move pass is the only option here."
        )

    return [
        Fact("MOVE", "yes" if move else "no (COPY+EXPUNGE)"),
        Fact("UIDPLUS", "yes" if uidplus else "no"),
        Fact("FILTER=SIEVE", server_side),
    ]
