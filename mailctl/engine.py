"""The session: the configured provider, chosen, checked, opened, closed.

``connect`` resolves which provider ``config`` selects, refuses a setting
it has no use for, lets its dialect validate the rest, and opens the
halves of its transport a command needs -- all before any work is done, so
a bad configuration costs no connection. It yields a :class:`Session`, and
the work itself is in ``mailctl.utilities``.

The session never touches a protocol. It works against one ``Provider``
(``mailctl.providers.base``), chosen by the ``provider`` setting, and never
asks which provider it has.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from .config import CONNECTION_SETTINGS, FLAG, Config
from .providers.base import (
    Dialect,
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


@dataclass(frozen=True)
class Session:
    """One provider, opened: its offline dialect and its live transport.

    What a utility works against. The dialect, the name, and the
    capabilities are the provider's, so a utility that needs no connection
    takes the ``Provider`` itself instead and reads the same attributes.
    """

    provider: Provider
    transport: Transport

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
        """Whether the transport's rule half is connected."""
        return self.transport.has_rules

    # ------------------------------------------------------------------------
    @property
    def has_mail(self) -> bool:
        """Whether the transport's mail half is connected."""
        return self.transport.has_mail


# ############################################################################
# The provider
# ############################################################################


# ----------------------------------------------------------------------------
@contextmanager
def connect(
    config: Config,
    *,
    rules: bool = True,
    mail: bool = False,
    progress: Progress | None = None,
) -> Iterator[Session]:
    """Open the requested halves of the configured provider, then close them.

    ``rules`` is the half that stores rules, ``mail`` the half that holds
    messages and folders. The configuration is validated first, so an
    unknown provider or a setting it refuses costs no connection.
    """
    provider = provider_for(config)
    check_settings(provider, config)
    provider.dialect.validate(config)

    with provider.transport.open(
        config, rules=rules, mail=mail, progress=progress
    ) as transport:
        yield Session(provider, transport)


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
