"""The in-tree registry: every provider mailctl has, by name (ADR 0006).

A dict, deliberately. Entry points for third-party providers are deferred:
adding them later is backward-compatible, and withdrawing a published
plugin API is not (#26).
"""

from .. import MailctlError
from ..config import DEFAULT_PROVIDER, Config, Source
from .base import Provider
from .mxroute import MxrouteProvider

__all__ = ["PROVIDERS", "provider_for", "provider_named"]

PROVIDERS: dict[str, type[Provider]] = {
    MxrouteProvider.name: MxrouteProvider,
}


# ----------------------------------------------------------------------------
def provider_named(name: str, origin: Source | None = None) -> type[Provider]:
    """Return the provider registered as ``name``; refuse an unknown one.

    The refusal names every known provider, and where the name came from
    when that is known, so a typo is fixed without reading the source.
    """
    provider = PROVIDERS.get(name)

    if provider is None:
        where = f" (from {origin.describe()})" if origin is not None else ""

        raise MailctlError(
            f"provider: {name!r} is not a known provider{where}. Known: "
            f"{', '.join(sorted(PROVIDERS))}"
        )

    return provider


# ----------------------------------------------------------------------------
def provider_for(config: Config) -> type[Provider]:
    """Return the provider ``config`` selects; ``mxroute`` by default."""
    return provider_named(
        config.provider or DEFAULT_PROVIDER, config.sources.get("provider")
    )
