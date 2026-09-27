"""ManageSieve (RFC 5804) and Sieve script handling, wrapping ``sievelib``.

``client`` is the protocol session, ``script`` the offline parse / merge /
render / diff, and ``backup`` the byte-exact backup writer. Everything a
caller needs is re-exported here.
"""

from .backup import (
    backup_path,
    backup_script,
    resolve_backup_target,
    write_backup,
)
from .client import (
    Revealable,
    SieveAuthenticationError,
    SieveConnectionError,
    SieveSession,
)
from .script import (
    FILTERSET_NAME,
    PLACE_AFTER,
    PLACE_BEFORE,
    PLACE_FIRST,
    PLACE_LAST,
    SIEVELIB_DIALECT,
    SIEVELIB_NAME_MARKER,
    UNIMPLEMENTED_ACTIONS,
    DisplayDiff,
    NameDialect,
    Placement,
    display_diff,
    merge_rule,
    move_rule,
    parse_script,
    remove_rule,
    render_script,
    resolve_position,
    rewrite_hash_comments,
    rule_names,
    script_diff,
)

__all__ = [
    "FILTERSET_NAME",
    "PLACE_AFTER",
    "PLACE_BEFORE",
    "PLACE_FIRST",
    "PLACE_LAST",
    "SIEVELIB_DIALECT",
    "SIEVELIB_NAME_MARKER",
    "UNIMPLEMENTED_ACTIONS",
    "DisplayDiff",
    "NameDialect",
    "Placement",
    "Revealable",
    "SieveAuthenticationError",
    "SieveConnectionError",
    "SieveSession",
    "backup_path",
    "backup_script",
    "display_diff",
    "merge_rule",
    "move_rule",
    "parse_script",
    "remove_rule",
    "render_script",
    "resolve_backup_target",
    "resolve_position",
    "rewrite_hash_comments",
    "rule_names",
    "script_diff",
    "write_backup",
]
