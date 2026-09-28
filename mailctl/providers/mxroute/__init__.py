"""MXroute: ManageSieve plus IMAP, and the policies MXroute has set.

``provider`` names the pair registered as ``mxroute``: ``dialect`` is its
offline half -- Sieve in its webmail's rule-name dialect, its policies,
its wording -- over ``sieve``; ``transport`` is its communication half,
over the connections ``managesieve`` and ``imap`` open with the advice a
failed login gains.
"""

from .dialect import MxrouteDialect
from .provider import MXROUTE
from .transport import MxrouteTransport

__all__ = ["MXROUTE", "MxrouteDialect", "MxrouteTransport"]
