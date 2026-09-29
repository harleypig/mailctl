"""MXroute's side of the ManageSieve connection: the session and its advice.

The layer-1 ``managesieve`` library speaks the protocol. What lives here is
the mapping from mailctl's ``Config`` to a session, and the advice a failed
connection or login gains -- MXroute documents neither its ManageSieve
port nor its TLS mode, and expects the full email address as the username.
It is the transport's side of the provider: nothing here parses, builds,
or checks a script.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from ... import MailctlError
from ...components.managesieve.client import (
    SieveAuthenticationError,
    SieveConnectionError,
    SieveSession,
)
from ...components.managesieve.responses import ServerWarning
from ...config import DEFAULT, DEFAULT_SIEVE_PORT, DEFAULT_SIEVE_TLS, Config

__all__ = ["sieve_session"]


# ----------------------------------------------------------------------------
@contextmanager
def sieve_session(
    config: Config,
    progress: Callable[[str | ServerWarning], None] | None = None,
) -> Iterator[SieveSession]:
    """Open a ManageSieve session for ``config`` and close it afterwards.

    A failure to connect or log in gains MXroute's advice: it documents
    neither its ManageSieve port nor its TLS mode, and it expects the full
    email address as the username.
    """
    config.require("host", "user")

    session = SieveSession(
        host=config.host,
        port=config.sieve_port,
        username=config.user,
        password=config.password,
        tls=config.sieve_tls,
        progress=progress,
    )

    try:
        session.open()

    except SieveConnectionError as exc:
        raise _advice(config).error(str(exc)) from exc

    except SieveAuthenticationError as exc:
        raise MailctlError(
            f"ManageSieve authentication failed for {config.user!r} "
            f"(password {config.password_state()}). MXRoute expects the "
            f"FULL email address as the username, e.g. "
            f"you@yourdomain.com."
        ) from exc

    try:
        yield session

    finally:
        session.close()


DEFAULT_PORT_AND_TLS = f"{DEFAULT_SIEVE_PORT} + {DEFAULT_SIEVE_TLS}"


# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class _Hint:
    """The advice for a failed connection, around the TLS mode to try.

    Kept in parts so a front-end can put its own way of choosing the mode
    between them; :meth:`text` is the advice with the mode named plainly.
    """

    before: str
    mode: str
    after: str

    # ------------------------------------------------------------------------
    @property
    def note(self) -> str:
        return " (implicit TLS)" if self.mode == "ssl" else ""

    # ------------------------------------------------------------------------
    def text(self) -> str:
        return (
            f"{self.before} The other TLS mode, {self.mode}{self.note}, is "
            f"the alternative. {self.after}"
        )

    # ------------------------------------------------------------------------
    def error(self, reason: str) -> MailctlError:
        """The connection failure ``reason``, with this advice."""
        return MailctlError(
            f"{reason} {self.text()}",
            code="try_setting",
            fields={
                "before": f"{reason} {self.before}",
                "setting": "sieve_tls",
                "value": self.mode,
                "note": self.note,
                "after": self.after,
            },
        )


# ----------------------------------------------------------------------------
def _connection_hint(config: Config) -> str:
    """Return a hint tuned to the port and TLS mode that failed."""
    return _advice(config).text()


# ----------------------------------------------------------------------------
def _advice(config: Config) -> _Hint:
    """The hint for a failed connection, tuned to its port and TLS mode.

    MXRoute documents neither a ManageSieve port nor whether it speaks
    STARTTLS or implicit TLS. 4190 is the IANA-registered port (RFC 5804)
    and the Dovecot default, which makes it the right default and not a
    verified fact -- so a failure has to say that plainly instead of
    implying the user mistyped something.

    It calls the pair the default only when both actually came from the
    built-in default; a value somebody configured is named with where it
    was configured instead.
    """
    origins = [
        config.sources.get(name) for name in ("sieve_port", "sieve_tls")
    ]

    if all(
        origin is not None and origin.kind == DEFAULT for origin in origins
    ):
        used = (
            f"{config.sieve_port} + {config.sieve_tls} is the RFC 5804 / "
            f"Dovecot default, not a documented MXRoute setting."
        )

    else:
        port, tls = (
            f" ({origin.describe()})" if origin is not None else ""
            for origin in origins
        )
        used = (
            f"this attempt used port {config.sieve_port}{port} and TLS mode "
            f"{config.sieve_tls}{tls}; the RFC 5804 / Dovecot default is "
            f"{DEFAULT_PORT_AND_TLS}."
        )

    return _Hint(
        before=f"MXRoute does not publish its ManageSieve port or TLS "
        f"mode; {used}",
        mode="ssl" if config.sieve_tls == "starttls" else "starttls",
        after="Also confirm the hostname (the panel's Email Clients page "
        "shows it; it is per-account, the same as your primary MX record), "
        f"that outbound {config.sieve_port} is not blocked, and if all "
        f"else fails ask MXRoute support which port and TLS mode to use.",
    )
