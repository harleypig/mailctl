"""MXroute: ManageSieve plus IMAP, and the policies MXroute has set.

``provider`` is the provider-interface class; ``sieve`` and ``imap`` hold
MXroute's side of each protocol -- its policies, its webmail's rule-name
dialect, and the advice a failed login gains.
"""

from .provider import MxrouteProvider

__all__ = ["MxrouteProvider"]
