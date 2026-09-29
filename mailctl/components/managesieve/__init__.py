"""ManageSieve (RFC 5804) and Sieve script handling, wrapping ``sievelib``.

``client`` is the protocol session, ``script`` the offline parse / merge /
render / diff, ``emit`` what a rule can contain and the extensions it
needs, and ``backup`` where a backup of a script goes. Everything a
caller needs is re-exported here.
"""

from .backup import backup_path, resolve_backup_target
from .client import (
    Revealable,
    SieveAuthenticationError,
    SieveConnectionError,
    SieveSession,
    connection_lost,
)
from .emit import (
    EMIT_TABLE,
    INFORMATIONAL_EXTENSIONS,
    KNOWN_EXTENSIONS,
    REQUIRED_EXTENSIONS,
    emitted_extensions,
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
    "EMIT_TABLE",
    "FILTERSET_NAME",
    "INFORMATIONAL_EXTENSIONS",
    "KNOWN_EXTENSIONS",
    "PLACE_AFTER",
    "PLACE_BEFORE",
    "PLACE_FIRST",
    "PLACE_LAST",
    "REQUIRED_EXTENSIONS",
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
    "connection_lost",
    "display_diff",
    "emitted_extensions",
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
]
