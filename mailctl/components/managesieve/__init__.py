"""ManageSieve (RFC 5804) and Sieve script handling, wrapping ``sievelib``.

``client`` is the protocol session, ``script`` the offline parse / merge /
render / diff, ``emit`` what a rule can contain and the extensions it
needs, ``backup`` where a backup of a script goes, and ``responses`` the
WARNINGS a response carries. Everything a
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
from .responses import ServerWarning
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
    disable_rule,
    display_diff,
    enable_rule,
    fileinto_targets,
    merge_rule,
    move_rule,
    parse_script,
    rearrange_rules,
    remove_rule,
    rename_rule,
    render_script,
    resolve_position,
    retarget_fileinto,
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
    "ServerWarning",
    "SieveAuthenticationError",
    "SieveConnectionError",
    "SieveSession",
    "backup_path",
    "connection_lost",
    "disable_rule",
    "display_diff",
    "emitted_extensions",
    "enable_rule",
    "fileinto_targets",
    "merge_rule",
    "move_rule",
    "parse_script",
    "rearrange_rules",
    "remove_rule",
    "rename_rule",
    "render_script",
    "resolve_backup_target",
    "resolve_position",
    "retarget_fileinto",
    "rewrite_hash_comments",
    "rule_names",
    "script_diff",
]
