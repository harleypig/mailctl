"""The utilities: every piece of work mailctl does, for any front-end.

A front-end -- the CLI today -- turns what a person asked for into plain
values, calls a utility against the session ``mailctl.engine`` opened,
and decides what to show and whether to go on. A
utility never learns how it was called: it takes no parsed arguments,
never prints or prompts, and reports through return values, exceptions
(``MailctlError``), and two optional callbacks -- ``progress`` for protocol
chatter and ``on_event`` for the steps of a change as they happen.

Every change is split the same way. A ``plan_*`` function is read-only and
returns what would change; the front-end renders it and decides; an
``execute``-style function carries out the plan it was handed. A dry run is
a plan that is never executed.

The utilities are host-independent. They work in the provider-neutral
model -- criteria, an ``ActionSpec``, folders, messages -- and read what a
host can do from its declared capabilities, never asking which provider
they have. Building and checking are done here: a session's dialect
supplies the host-specific parts, offline, and its transport reads and
stores (ADR 0007). One module per subject:

``rules``      checking, reading, planning, and uploading rules
``scripts``    rule sets as stored: read, chosen, and uploaded
``senders``    who sends the mail in a folder, and how much is unread
``backup``     backing up the active script, and restoring one
``baseline``   a saved probe per host, and drift from it
``backup_files`` writing a backup's exact bytes to disk
``flags``      marking messages read, flagged, or with keywords
``folders``    listing, subscribing, and planning a rule's target folder
``folder_rename`` renaming a folder and repointing the rules filing into it
``mail``       the existing-mail pass, and criteria from a message
``messages``   finding and reading messages
``optimize``   proposing a better arrangement of the rules, and applying it
``migration``  what the rename from ``mxfilter`` left behind
``reports``    probes, provider wording, and extension state
``events``     the steps of a change, as a front-end is told of them
"""

from . import (
    backup,
    backup_files,
    baseline,
    events,
    flags,
    folder_rename,
    folders,
    mail,
    messages,
    migration,
    optimize,
    reports,
    rules,
    scripts,
    senders,
)

__all__ = [
    "backup",
    "backup_files",
    "baseline",
    "events",
    "flags",
    "folder_rename",
    "folders",
    "mail",
    "messages",
    "migration",
    "optimize",
    "reports",
    "rules",
    "scripts",
    "senders",
]
