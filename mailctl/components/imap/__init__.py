"""IMAP (RFC 3501), wrapping ``IMAPClient``.

``client`` is the protocol session, ``folders`` the offline folder-name
handling against a reported delimiter, ``messages`` messages and the
existing-mail plan as data, ``search`` what a search needs from its
criteria, and ``servers`` the server-software profiles chosen by probing.
Everything a caller needs is re-exported here.
"""

from .client import (
    ImapAuthenticationError,
    ImapConnectionError,
    ImapSession,
    ImapTLSError,
    Revealable,
)
from .folders import (
    FolderCreation,
    case_variant_hint,
    case_variants,
    normalize_folder,
    same_folder,
    split_path,
)
from .messages import (
    BULK_CHUNK,
    MailActionPlan,
    MailActionResult,
    MessageSummary,
    PartialExecution,
    decode_header_value,
    header_values,
    structure_has_attachment,
)
from .search import SearchCriteria

__all__ = [
    "BULK_CHUNK",
    "FolderCreation",
    "ImapAuthenticationError",
    "ImapConnectionError",
    "ImapSession",
    "ImapTLSError",
    "MailActionPlan",
    "MailActionResult",
    "MessageSummary",
    "PartialExecution",
    "Revealable",
    "SearchCriteria",
    "case_variant_hint",
    "case_variants",
    "decode_header_value",
    "header_values",
    "normalize_folder",
    "same_folder",
    "split_path",
    "structure_has_attachment",
]
