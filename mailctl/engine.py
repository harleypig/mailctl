"""The session: the configured provider, chosen, checked, opened, kept.

``connect`` resolves which provider ``config`` selects, refuses a setting
it has no use for, and lets its dialect validate the rest -- all before
anything connects, so a bad configuration costs no connection. It yields a
:class:`Session`, and the work itself is in ``mailctl.utilities``.

A session connects each half of the transport the first time a utility
uses it, keeps it for as long as the session lives, and closes what it
opened when it ends. The CLI's session lives for one command; a front-end
that keeps running keeps one open, one per user, since a session owns its
connections and nothing here is shared between sessions.

The session never touches a protocol. It works against one ``Provider``
(``mailctl.providers.base``), chosen by the ``provider`` setting, and never
asks which provider it has: what it does around each call -- connect,
serialise, re-connect -- it reads from the transport interface's own
classification of that call.
"""

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, cast

from .config import CONNECTION_SETTINGS, FLAG, Config
from .providers.base import (
    CONNECTION,
    MAIL,
    READ,
    RULES,
    TRANSPORT_KINDS,
    Dialect,
    Operation,
    Progress,
    Provider,
    ProviderCapabilities,
    Transport,
    Wording,
    refuse,
)
from .providers.registry import provider_for

# ############################################################################
# The session
# ############################################################################


class Session:
    """One provider, in use: its offline dialect and its live transport.

    What a utility works against. ``transport`` is the provider's
    transport as the session guards it: each call connects its half first
    if need be, waits its turn on that half's connection, and -- a read
    only -- is sent once more on a fresh connection if the server had
    dropped the old one. A write that fails is never sent again, because
    it may have landed; the failure is the caller's to report.

    ``rules`` and ``mail`` say which halves this session may connect; a
    half it may not behaves as one that is not there.
    """

    # ------------------------------------------------------------------------
    def __init__(
        self,
        provider: Provider,
        transport: Transport,
        *,
        rules: bool = True,
        mail: bool = True,
    ):
        self.provider = provider
        self.connection = transport
        self.transport = cast(Transport, _Guarded(self))
        self._allowed = {RULES: rules, MAIL: mail}
        self._locks = {RULES: threading.RLock(), MAIL: threading.RLock()}
        self._opened: list[str] = []

    # ------------------------------------------------------------------------
    @property
    def name(self) -> str:
        return self.provider.name

    # ------------------------------------------------------------------------
    @property
    def capabilities(self) -> ProviderCapabilities:
        return self.provider.capabilities

    # ------------------------------------------------------------------------
    @property
    def dialect(self) -> type[Dialect]:
        return self.provider.dialect

    # ------------------------------------------------------------------------
    @property
    def wording(self) -> Wording:
        return self.provider.wording

    # ------------------------------------------------------------------------
    @property
    def has_rules(self) -> bool:
        """Whether this session has a rule half, connected or not yet."""
        return self._allowed[RULES] and self.connection.has_rules

    # ------------------------------------------------------------------------
    @property
    def has_mail(self) -> bool:
        """Whether this session has a mail half, connected or not yet."""
        return self._allowed[MAIL] and self.connection.has_mail

    # ------------------------------------------------------------------------
    @property
    def opened(self) -> tuple[str, ...]:
        """The halves this session connected, in the order it did."""
        return tuple(self._opened)

    # ------------------------------------------------------------------------
    def open_all(self) -> None:
        """Connect every half this session may, mail first, now rather
        than on first use.

        Mail goes first so a login failure there is reported before any
        rule traffic.
        """
        for half in (MAIL, RULES):
            with self._locks[half]:
                self._open(half)

    # ------------------------------------------------------------------------
    def _open(self, half: str) -> None:
        """Connect ``half`` the first time it is used, if it may be."""
        if half in self._opened or not self._allowed[half]:
            return

        self.connection.connect(half)
        self._opened.append(half)

    # ------------------------------------------------------------------------
    def _has(self, half: str) -> bool:
        """Whether ``half`` can be connected (again) in this session."""
        return self.has_rules if half == RULES else self.has_mail

    # ------------------------------------------------------------------------
    def _forget(self, half: str) -> None:
        """Let go of a connection the server has dropped."""
        if half in self._opened:
            self._opened.remove(half)

        self.connection.disconnect(half)

    # ------------------------------------------------------------------------
    def call(self, name: str, operation: Operation, *args, **kwargs) -> Any:
        """Run one transport operation the way its kind requires.

        One call at a time per half: a front-end that serves several
        requests at once shares one connection per half, and IMAP and
        ManageSieve are both one command after another. A read the server
        cut off is re-sent once on a fresh connection; a second failure,
        like any write's, is raised.
        """
        half = cast(str, operation.half)

        with self._locks[half]:
            for attempt in (1, 2):
                self._open(half)

                try:
                    return getattr(self.connection, name)(*args, **kwargs)

                except Exception as error:
                    if not self.connection.dropped(error):
                        raise

                    self._forget(half)

                    if (
                        operation.kind != READ
                        or attempt == 2
                        or not self._has(half)
                    ):
                        raise

        raise AssertionError("unreachable")

    # ------------------------------------------------------------------------
    def close(self) -> None:
        """Close every half this session connected, last opened first."""
        while self._opened:
            half = self._opened.pop()

            with self._locks[half]:
                self.connection.disconnect(half)


class _Guarded:
    """A transport as a session hands it to the utilities.

    Every classified read or write goes through :meth:`Session.call`; the
    rest -- the ``has_*`` answers, which are the session's, and anything
    that is not an operation -- is the transport's own.
    """

    _session: Session

    # ------------------------------------------------------------------------
    def __init__(self, session: Session):
        object.__setattr__(self, "_session", session)

    # ------------------------------------------------------------------------
    @property
    def has_rules(self) -> bool:
        return self._session.has_rules

    # ------------------------------------------------------------------------
    @property
    def has_mail(self) -> bool:
        return self._session.has_mail

    # ------------------------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        session = self._session
        operation = TRANSPORT_KINDS.get(name)

        if operation is None or operation.kind == CONNECTION:
            return getattr(session.connection, name)

        run: Callable[..., Any] = session.call

        def guarded(*args, **kwargs):
            return run(name, operation, *args, **kwargs)

        guarded.__name__ = name

        return guarded

    # ------------------------------------------------------------------------
    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._session.connection, name, value)


# ############################################################################
# The provider
# ############################################################################


# ----------------------------------------------------------------------------
@contextmanager
def connect(
    config: Config,
    *,
    rules: bool = True,
    mail: bool = True,
    progress: Progress | None = None,
) -> Iterator[Session]:
    """A session on the configured provider, closed when the block ends.

    ``rules`` is the half that stores rules, ``mail`` the half that holds
    messages and folders; each says whether the session may connect it,
    and it connects only when first used; :meth:`Session.open_all`
    connects both up front. The configuration is validated first, so an
    unknown provider or a setting it refuses costs no connection.
    """
    provider = provider_for(config)
    check_settings(provider, config)
    provider.dialect.validate(config)

    session = Session(
        provider,
        provider.transport.open(config, progress=progress),
        rules=rules,
        mail=mail,
    )

    try:
        yield session

    finally:
        session.close()


# ----------------------------------------------------------------------------
def check_settings(provider: Provider, config: Config) -> None:
    """Refuse a setting the provider has no use for, rather than ignore it.

    A connection flag the provider does not read would be a switch that
    looks like it took effect and did not. So would ``disabled_extensions``
    anywhere but a provider that declares ``extensions``, since it narrows
    the extensions a provider emits. An ambient connection setting -- one
    from the environment or the config file -- may serve another provider,
    so only a flag is refused.
    """
    for name in CONNECTION_SETTINGS:
        origin = config.sources.get(name)

        if (
            origin is not None
            and origin.kind == FLAG
            and name not in provider.capabilities.settings
        ):
            raise refuse(
                provider.name,
                f"take {origin.describe()}",
                f"it does not read the {name!r} setting",
            )

    if config.disabled_extensions and not provider.capabilities.extensions:
        origin = config.sources.get("disabled_extensions")
        where = f" (from {origin.describe()})" if origin is not None else ""

        raise refuse(
            provider.name,
            f"take disabled_extensions{where}",
            "it does not declare the 'extensions' capability",
        )


# ----------------------------------------------------------------------------
def capabilities_for(config: Config) -> ProviderCapabilities:
    """What the provider ``config`` selects declares; needs no connection.

    A front-end reads this to offer only what that provider can do.
    """
    return provider_for(config).capabilities
