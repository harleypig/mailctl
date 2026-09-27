"""MXroute's side of ManageSieve: its policy, its webmail, its login advice.

The layer-1 ``managesieve`` library knows the protocol and Sieve. What
lives here is what MXroute has decided or ships: ``redirect`` refused in
favour of its forwarders, rule names in the dialect of Roundcube (the
webmail it runs), connection advice for its undocumented ManageSieve port,
and the mapping from mailctl's ``Config`` to a session.

Transitional: epic #92's step 4 folds this into the ``mxroute`` provider
behind the provider interface.
"""

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sievelib import factory

from ... import MailctlError
from ...components.managesieve import (
    DisplayDiff,
    NameDialect,
    Placement,
    SieveAuthenticationError,
    SieveConnectionError,
    SieveSession,
    rewrite_hash_comments,
)
from ...components.managesieve import script as _script
from ...components.managesieve.script import SIEVELIB_NAME_MARKER
from ...config import DEFAULT, DEFAULT_SIEVE_PORT, DEFAULT_SIEVE_TLS, Config

__all__ = [
    "MXROUTE_FORBIDDEN_ACTIONS",
    "ROUNDCUBE_DIALECT",
    "ROUNDCUBE_NAME_MARKER",
    "display_diff",
    "merge_rule",
    "move_rule",
    "parse_script",
    "remove_rule",
    "render_script",
    "sieve_session",
]

# Confirmed disabled by MXRoute, from MXroute's own blog (2024-03-21):
# they "decided to disable the ability for users to create redirect sieve
# filters" because their real forwarders are designed to handle SRS
# properly. This one is a documented policy, not a capability, so it will
# not show up as a missing extension -- refusing it here is the only way to
# catch it before the server does.
#
# The message names BOTH routes deliberately. The Terraform resource is the
# as-code path and the one that will age best -- MXroute is phasing
# DirectAdmin out as a user interface, so panel-shaped instructions may go
# stale while the API-backed resource will not. But a domain that is not
# under Terraform yet has only the panel, and that reader is exactly the one
# hitting this error. Naming both costs a clause.
MXROUTE_FORBIDDEN_ACTIONS = {
    "redirect": (
        "MXRoute disables the Sieve 'redirect' action server-side (their "
        "2024-03-21 announcement). Their own forwarders are built to handle "
        "SRS correctly, which a Sieve redirect does not -- so set up a "
        "forwarder in the control panel, or with the 'mxroute_forwarder' "
        "Terraform resource, instead."
    ),
}

# Two dialects name a rule in a Sieve script, and mailctl has to read both
# and write one.
#
# `# Filter: NAME` is sievelib's, and the only one its parser recognises.
# `# rule:[NAME]` is Roundcube's managesieve plugin's -- and Roundcube is the
# webmail MXRoute actually ships, so it is the form already sitting in the
# account's active script.
#
# Read: both, because a name that reaches the parser under only one of them
# is a name that gets replaced by "Unnamed rule N" -- and the name is what
# --replace and remove-rule identify a rule by, so losing it turns "update
# the rule I named" into "append a second rule that never fires".
#
# Write: `# rule:[NAME]`. Interoperating with the webmail on the host beats
# matching the library's internal default: rules mailctl writes stay
# visible and editable in the panel's filter UI, and rules the user wrote
# there keep their names through a merge.
ROUNDCUBE_NAME_MARKER = re.compile(r"#\s*rule:\[(?P<name>.+)\]")


# ############################################################################
# The Roundcube name dialect
# ############################################################################


# ----------------------------------------------------------------------------
def _to_sievelib_names(text: str) -> str:
    """Rewrite Roundcube name markers into the form sievelib recognises."""

    def translate(comment: str) -> str | None:
        match = ROUNDCUBE_NAME_MARKER.fullmatch(comment.strip())

        if match is None:
            return None

        return f"{SIEVELIB_NAME_MARKER}{match['name']}"

    return rewrite_hash_comments(text, translate)


# ----------------------------------------------------------------------------
def _to_roundcube_names(text: str) -> str:
    """Rewrite sievelib's name markers into Roundcube's form."""

    def translate(comment: str) -> str | None:
        stripped = comment.strip()

        if not stripped.startswith(SIEVELIB_NAME_MARKER):
            return None

        name = stripped[len(SIEVELIB_NAME_MARKER) :]

        if not name:
            return None

        return f"# rule:[{name}]"

    return rewrite_hash_comments(text, translate)


ROUNDCUBE_DIALECT = NameDialect(
    read=_to_sievelib_names, write=_to_roundcube_names
)


# ############################################################################
# Script handling in MXroute's dialect
# ############################################################################


# ----------------------------------------------------------------------------
def parse_script(text: str) -> factory.FiltersSet:
    """Parse a script, reading Roundcube's rule names as well as sievelib's."""
    return _script.parse_script(text, ROUNDCUBE_DIALECT)


# ----------------------------------------------------------------------------
def render_script(filters: factory.FiltersSet) -> str:
    """Render a filter set with rule names in Roundcube's form."""
    return _script.render_script(filters, ROUNDCUBE_DIALECT)


# ----------------------------------------------------------------------------
def merge_rule(
    existing: str,
    name: str,
    conditions: list[tuple],
    actions: list[tuple],
    matchtype: str = "anyof",
    replace: bool = False,
    placement: Placement | None = None,
) -> str:
    """``managesieve.merge_rule``, with Roundcube's rule names."""
    return _script.merge_rule(
        existing,
        name,
        conditions,
        actions,
        matchtype,
        replace,
        placement,
        ROUNDCUBE_DIALECT,
    )


# ----------------------------------------------------------------------------
def remove_rule(existing: str, name: str) -> str:
    """``managesieve.remove_rule``, with Roundcube's rule names."""
    return _script.remove_rule(existing, name, ROUNDCUBE_DIALECT)


# ----------------------------------------------------------------------------
def move_rule(existing: str, name: str, placement: Placement) -> str:
    """``managesieve.move_rule``, with Roundcube's rule names."""
    return _script.move_rule(existing, name, placement, ROUNDCUBE_DIALECT)


# ----------------------------------------------------------------------------
def display_diff(before: str, after: str, name: str = "sieve") -> DisplayDiff:
    """``managesieve.display_diff``, with Roundcube's rule names."""
    return _script.display_diff(before, after, name, ROUNDCUBE_DIALECT)


# ############################################################################
# The session, from mailctl's configuration
# ############################################################################


# ----------------------------------------------------------------------------
@contextmanager
def sieve_session(
    config: Config,
    progress: Callable[[str], None] | None = None,
) -> Iterator[SieveSession]:
    """Open a ManageSieve session for ``config`` and close it afterwards.

    A failure to connect or log in gains MXroute's advice: it documents
    neither its ManageSieve port nor its TLS mode, and it expects the full
    email address as the username.
    """
    config.require("host", "user")

    session = SieveSession(
        host=config.host,
        port=config.sieve_port,
        username=config.user,
        password=config.password,
        tls=config.sieve_tls,
        progress=progress,
    )

    try:
        session.open()

    except SieveConnectionError as exc:
        raise MailctlError(f"{exc} {_connection_hint(config)}") from exc

    except SieveAuthenticationError as exc:
        raise MailctlError(
            f"ManageSieve authentication failed for {config.user!r} "
            f"(password {config.password_state()}). MXRoute expects the "
            f"FULL email address as the username, e.g. "
            f"you@yourdomain.com."
        ) from exc

    try:
        yield session

    finally:
        session.close()


DEFAULT_PORT_AND_TLS = f"{DEFAULT_SIEVE_PORT} + {DEFAULT_SIEVE_TLS}"


# ----------------------------------------------------------------------------
def _connection_hint(config: Config) -> str:
    """Return a hint tuned to the port and TLS mode that failed.

    MXRoute documents neither a ManageSieve port nor whether it speaks
    STARTTLS or implicit TLS. 4190 is the IANA-registered port (RFC 5804)
    and the Dovecot default, which makes it the right default and not a
    verified fact -- so a failure has to say that plainly instead of
    implying the user mistyped something.

    It calls the pair the default only when both actually came from the
    built-in default; a value somebody configured is named with where it
    was configured instead.
    """
    origins = [
        config.sources.get(name) for name in ("sieve_port", "sieve_tls")
    ]

    if all(
        origin is not None and origin.kind == DEFAULT for origin in origins
    ):
        used = (
            f"{config.sieve_port} + {config.sieve_tls} is the RFC 5804 / "
            f"Dovecot default, not a documented MXRoute setting."
        )

    else:
        port, tls = (
            f" ({origin.describe()})" if origin is not None else ""
            for origin in origins
        )
        used = (
            f"this attempt used port {config.sieve_port}{port} and TLS mode "
            f"{config.sieve_tls}{tls}; the RFC 5804 / Dovecot default is "
            f"{DEFAULT_PORT_AND_TLS}."
        )

    hints = [
        f"MXRoute does not publish its ManageSieve port or TLS mode; {used}"
    ]

    if config.sieve_tls == "starttls":
        hints.append("Try --sieve-tls ssl (implicit TLS) as the alternative.")

    else:
        hints.append("Try --sieve-tls starttls as the alternative.")

    hints.append(
        "Also confirm the hostname (the panel's Email Clients page shows it; "
        "it is per-account, the same as your primary MX record), that "
        f"outbound {config.sieve_port} is not blocked, and if all else "
        f"fails ask MXRoute support which port and TLS mode to use."
    )

    return " ".join(hints)
