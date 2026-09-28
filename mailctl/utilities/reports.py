"""What the account and the configured provider say about themselves.

Read-only: the probes behind ``mailctl test``, the provider's own wording
and connection facts, and the state of each Sieve extension.
"""

from dataclasses import dataclass, field

from ..config import Config
from ..providers.base import ExtensionState, Fact, Provider, Wording
from ..providers.registry import provider_for
from .folders import list_folders

# ############################################################################
# Probing the account
# ############################################################################


@dataclass(frozen=True)
class RulesProbe:
    """What the rule half says about itself, and its rule sets."""

    capabilities: list[str]
    active: str | None
    others: list[str]


@dataclass(frozen=True)
class MailProbe:
    """What the mail half says about itself, and its folder shape.

    ``facts`` is what its advertised capabilities mean for mailctl, in the
    provider's words.
    """

    capabilities: list[str]
    delimiter: str
    folder_count: int
    unsubscribed: list[str] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)


# ----------------------------------------------------------------------------
def probe_rules(provider: Provider) -> RulesProbe:
    """Read what the rule half advertises, and its rule-set listing."""
    capabilities = provider.rules_capabilities()
    active, others = provider.list_rule_sets()

    return RulesProbe(capabilities, active, others)


# ----------------------------------------------------------------------------
def probe_mail(provider: Provider) -> MailProbe:
    """Read what the mail half advertises, and its folder shape."""
    listing = list_folders(provider)
    capabilities = provider.mail_capabilities()

    return MailProbe(
        capabilities,
        listing.delimiter,
        len(listing.folders),
        listing.unsubscribed,
        provider.mail_facts(capabilities),
    )


# ----------------------------------------------------------------------------
def wording(config: Config) -> Wording:
    """The words the configured provider's host is described in.

    Needs no connection. A front-end fills its report from this rather
    than writing one host's names and policies into itself.
    """
    return provider_for(config).wording


# ----------------------------------------------------------------------------
def connection_facts(config: Config) -> list[Fact]:
    """Where the configured provider connects, as ``config`` resolves it."""
    return provider_for(config).connection_facts(config)


# ############################################################################
# Extensions -- what the server advertises, less what is disabled
# ############################################################################


# ----------------------------------------------------------------------------
def report_extensions(
    provider: Provider, probe: RulesProbe, config: Config
) -> list[ExtensionState]:
    """One state per extension mailctl knows or the server lists, by name.

    Empty for a provider that does not declare ``extensions``.
    """
    if not provider.capabilities.extensions:
        return []

    return provider.report_extensions(probe.capabilities, config)
