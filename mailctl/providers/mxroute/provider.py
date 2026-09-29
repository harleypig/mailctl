"""The ``mxroute`` provider: what MXroute can do, and the halves that do it.

ManageSieve for rules and IMAP for mail. The dialect (``dialect.py``)
speaks Sieve in Roundcube's rule-name form, offline; the transport
(``transport.py``) talks to the two servers.

Selection -- which delivered messages a rule matches -- is the transport's
IMAP ``SEARCH``, re-checked by the utilities against the real Sieve
semantics. It moves to the Sieve interpreter of ADR 0004 once that exists.
"""

from ..base import (
    DISCARD,
    FILEINTO,
    FLAG,
    KEEP,
    Provider,
    ProviderCapabilities,
)
from .dialect import MxrouteDialect
from .transport import MxrouteTransport

__all__ = ["MXROUTE"]

MXROUTE = Provider(
    name="mxroute",
    capabilities=ProviderCapabilities(
        ordering=True,
        stop=True,
        rule_sets=True,
        actions=frozenset((FILEINTO, DISCARD, FLAG, KEEP)),
        extensions=True,
        settings={
            "host": "MXRoute server hostname",
            "imap_host": "",
            "imap_port": "",
            "sieve_port": "",
            "sieve_tls": "",
        },
    ),
    dialect=MxrouteDialect,
    transport=MxrouteTransport,
)
