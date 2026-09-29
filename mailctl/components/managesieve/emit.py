"""What mailctl can put in a Sieve rule, and the extension each piece needs.

Offline and host-free: which ``require`` a command or tag needs is a fact
about the Sieve language (RFC 5228 and its extensions), the same on every
server. What a host advertises, and what a user has disabled, is the
provider's to weigh against this table.
"""

from collections.abc import Iterable

__all__ = [
    "EMIT_TABLE",
    "INFORMATIONAL_EXTENSIONS",
    "KNOWN_EXTENSIONS",
    "REQUIRED_EXTENSIONS",
    "emitted_extensions",
]

# Every command, test, and tag mailctl can put in a rule, and the Sieve
# extension it has to be `require`d under -- None for the base language.
# The required set 'test' reports and the set a rule is checked against are
# both read off this table, so a new emitted feature that is missing from
# it fails at the first plan that emits it, not silently later.
EMIT_TABLE: dict[str, str | None] = {
    # actions
    "addflag": "imap4flags",
    "discard": None,
    "fileinto": "fileinto",
    "keep": None,
    "stop": None,
    # action tags
    ":create": "mailbox",
    # tests, their match types, and the combinators joining them
    "header": None,
    "body": "body",
    ":text": "body",
    ":contains": None,
    ":is": None,
    ":matches": None,
    "anyof": None,
    "allof": None,
}

REQUIRED_EXTENSIONS = tuple(
    sorted({ext for ext in EMIT_TABLE.values() if ext is not None})
)

# Reported by 'test' so the answer comes from the server rather than from
# folklore. mailctl emits none of these; none of them decides anything.
INFORMATIONAL_EXTENSIONS = (
    "copy",
    "envelope",
    "enotify",
    "vacation",
    "regex",
    "spamtest",
    "extlists",
)

# The names disabled_extensions accepts: the ones 'test' always reports,
# whatever the server lists. Disabling an informational one changes nothing
# mailctl emits today, and keeps holding if a later feature starts emitting
# it.
KNOWN_EXTENSIONS = REQUIRED_EXTENSIONS + INFORMATIONAL_EXTENSIONS


# ----------------------------------------------------------------------------
def emitted_extensions(
    actions: Iterable[tuple],
    conditions: Iterable[tuple] = (),
    matchtype: str | None = None,
) -> set[str]:
    """Return the extensions a rule's actions and tests need, by EMIT_TABLE.

    An action tuple is ``(command, *arguments)``; any argument that is a
    ``:tag`` counts. A condition is ``(header, :matchtype, value)`` or
    ``("body", :transform, :matchtype, value)``, the shapes
    ``Criteria.sieve_conditions`` builds, told apart by their length.
    """
    names = []

    for action in actions:
        names.append(action[0])
        names += [
            part
            for part in action[1:]
            if isinstance(part, str) and part.startswith(":")
        ]

    for condition in conditions:
        if len(condition) == 4:
            names += list(condition[:3])

        else:
            names += ["header", condition[1]]

    if matchtype:
        names.append(matchtype)

    return {
        extension
        for name in names
        if (extension := EMIT_TABLE[name]) is not None
    }
