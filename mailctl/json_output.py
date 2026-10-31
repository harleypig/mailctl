"""The documents the interfaces hand over, one explicit mapping per record.

The interface layer's shared module: what the CLI's ``--json`` prints and
what the API returns are built here by the same functions, so the two are
one contract and cannot drift. A document is built from the neutral
records the utilities return, never from the text a front-end renders, so
values are whole, flags are raw, and nothing is clipped or rewritten for a
terminal. Every document is an object carrying ``version`` beside what its
command reports; README.md > *Output for scripts* lists the shapes.

The mappings are a contract a script depends on (CONVENTIONS.md > The
core returns data). Adding a document or a key is compatible; renaming or
removing one, or changing what a value means, raises ``JSON_VERSION``.
"""

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from .config import CONNECTION_SETTINGS, Secret
from .utilities import events, migration, rules
from .utilities.reports import TIME_FORMAT

__all__ = [
    "CHANNELS",
    "DRIFT_VERSION",
    "JSON_VERSION",
    "STEP_KINDS",
    "actions",
    "activation",
    "add_plan",
    "alert",
    "apply_plan",
    "backup_result",
    "baseline_save_plan",
    "baseline_save_result",
    "capabilities",
    "change",
    "drift_check",
    "dumps",
    "error",
    "event",
    "filterset_source",
    "folder_creation_plan",
    "folder_creation_result",
    "folder_listing",
    "folder_plan",
    "folder_rename_result",
    "mail_plan",
    "mail_result",
    "mark_plan",
    "mark_result",
    "message",
    "message_listing",
    "migration_plan",
    "migration_result",
    "move_plan",
    "old_name",
    "optimize_plan",
    "placement",
    "plan",
    "removal_plan",
    "rename_plan",
    "restore_plan",
    "rules_report",
    "script_change_result",
    "scripts",
    "sender_report",
    "server_report",
    "server_test",
    "source",
    "step",
    "subscription_plan",
    "subscription_result",
    "switch_plan",
]

JSON_VERSION = 2

# The version of ``server baseline check``'s document, which predates the
# shared ``version`` and keeps its own.
DRIFT_VERSION = 1

# How the IMAP component writes a message's INTERNALDATE: local time, the
# zone already dropped, so the ISO form carries no offset either.
RECEIVED_FORMAT = "%Y-%m-%d %H:%M:%S"

# What a message from each half is tagged with, the same for every
# provider: a user-facing noun, as the CLI's progress lines tag them.
CHANNELS = {events.RULES: "filter", events.MAIL: "mail"}

# What each neutral step is called in a document, the same for every
# provider: a filter's ``steps`` say what it does in these words, never in
# the host's own, so a client words them without knowing the host.
STEP_KINDS = {
    rules.FILEINTO: "file",
    rules.DISCARD: "discard",
    rules.FLAG: "flag",
    rules.UNFLAG: "unflag",
    rules.KEEP: "keep",
    rules.STOP: "stop",
    rules.OTHER: "other",
}


# ############################################################################
# Serialising
# ############################################################################


# ----------------------------------------------------------------------------
def dumps(document: dict, *, indent: int | None = 2) -> str:
    """Serialise a document, refusing anything no mapping here produced.

    A value comes from mail or a stored script, so it may hold anything;
    ``ensure_ascii`` escapes all of it outside printable ASCII, which
    leaves nothing a terminal would act on (as ``criteria.dump_filter``
    does). Do not turn it off.
    """
    text = json.dumps(
        document, indent=indent, ensure_ascii=True, default=_unmapped
    )

    return text + "\n"


# ----------------------------------------------------------------------------
def _unmapped(value):
    """Refuse a value no mapping converted -- a credential above all.

    ``json`` calls this only for a type it cannot write itself, so a
    ``Secret`` reaching a document is refused here whatever its wrapper
    renders; the message names the type and never the value.
    """
    if isinstance(value, Secret):
        raise TypeError("a credential is never serialised")

    raise TypeError(f"no JSON mapping for {type(value).__name__}")


# ----------------------------------------------------------------------------
def _document(**body) -> dict:
    return {"version": JSON_VERSION, **body}


# What a refused command line is called when argparse's message is not one
# of the shapes below. A template argparse rewords in a later Python lands
# here rather than being shown, since it may quote what the user typed.
REFUSED = "the command line was refused"

# An action's name as argparse writes it into a message: its option strings
# joined by '/', its metavar or dest, or its choices in braces. Never a
# quote, so no value fits.
_NAME = r"[\w{},/-]+"

# A choice as argparse lists it, quoted before 3.14 and bare from it.
_CHOICE = r"(?:'[\w.-]+'|[\w.-]+)"

# A value argparse could not take, up to the value itself: "argument
# --imap-port: invalid int value", "argument group: invalid choice", and
# the choices it lists last. The value it quotes between them may be a
# password typed into the wrong place. The choices are read only from the
# message's end and only as words, so no part of a value can pass as one.
_INVALID = re.compile(
    rf"(argument ({_NAME}): invalid (?:choice|[\w<>]+ value))(?:[:,].*?)?"
    rf"(?: \(choose from ({_CHOICE}(?:, {_CHOICE})*)\))?",
    re.DOTALL,
)

# A flag that takes no value, given one with '=': the value is quoted.
_IGNORED = re.compile(
    rf"(argument {_NAME}: ignored explicit argument)(?: .*)?", re.DOTALL
)

_AMBIGUOUS = re.compile(
    rf"ambiguous option: (\S+) could match ({_NAME}(?:, {_NAME})*)"
)

# argparse's messages with no room for anything the user typed: every slot
# is an action's name.
_NAMES_ONLY = re.compile(
    rf"(?:argument {_NAME}: )?(?:"
    r"expected (?:one|at most one|at least one|\d+) arguments?"
    rf"|not allowed with argument {_NAME}"
    rf"|the following arguments are required: {_NAME}(?:, {_NAME})*"
    rf"|one of the arguments {_NAME}(?: {_NAME})* is required"
    r")"
)

_UNRECOGNIZED = "unrecognized arguments: "


# ----------------------------------------------------------------------------
def _option_name(word: str) -> str | None:
    """The option ``word`` names, or None where it may be a value: a long
    option up to its '=', a short one only alone, since anything after
    ``-x`` is a value attached to it."""
    if word.startswith("--"):
        name = word.split("=", 1)[0]

        return name if len(name) > 2 else None

    if len(word) == 2 and word[0] == "-" and word[1] != "-":
        return word

    return None


# ----------------------------------------------------------------------------
def rejection(
    message: str, code: str = "invalid_arguments"
) -> tuple[str, str, dict[str, object] | None]:
    """The words, code, and fields for a command line argparse refused
    with ``message``: the CLI's error line and its --json line, and the
    API's on stderr and in its answer to ``initialize``.

    Built from the kind of refusal rather than copied, since argparse
    quotes what it could not use, and a word it did not recognise may be
    a password that lost its option. What is kept is what names the
    mistake: an option's name, never a value. A message of no known shape
    is ``REFUSED``, never argparse's text.
    """
    if message.startswith(_UNRECOGNIZED):
        words = message[len(_UNRECOGNIZED) :].split()
        options = [
            name for name in map(_option_name, words) if name is not None
        ]
        others = len(words) - len(options)
        said = ", ".join(options)

        if others:
            noun = "word" if others == 1 else "words"
            said = (
                f"{said} and {others} other {noun}"
                if said
                else (f"{others} {noun}")
            )

        return f"{_UNRECOGNIZED}{said}", code, None

    invalid = _INVALID.fullmatch(message)

    if invalid is not None:
        said = invalid.group(1)

        if invalid.group(3) is not None:
            choices = invalid.group(3).replace("'", "")
            said += f" (choose from {choices})"

        flag = invalid.group(2).split("/")[-1]
        setting = flag.removeprefix("--").replace("-", "_")

        if flag.startswith("--") and setting in CONNECTION_SETTINGS:
            return said, "setting_invalid", {"setting": setting}

        return said, code, None

    ignored = _IGNORED.fullmatch(message)

    if ignored is not None:
        return ignored.group(1), code, None

    ambiguous = _AMBIGUOUS.fullmatch(message)

    if ambiguous is not None:
        option = _option_name(ambiguous.group(1))

        if option is not None:
            return (
                f"ambiguous option: {option} could match {ambiguous.group(2)}",
                code,
                None,
            )

    if _NAMES_ONLY.fullmatch(message):
        return message, code, None

    return REFUSED, code, None


# ----------------------------------------------------------------------------
def error(
    message: str,
    code: str | None = None,
    fields: Mapping[str, object] | None = None,
) -> dict:
    """A failure, as ``main`` reports it on stderr under ``--json``, and
    as the API's error ``data``.

    ``code`` names the condition, where the failure has one, so a script
    can tell one refusal from another without reading the message.
    ``fields`` are the values a coded error carries (``MailctlError``),
    given by the API; the CLI renders its advice from them instead.
    """
    body: dict = {"message": message}

    if code is not None:
        body["code"] = code

    if fields is not None:
        body["fields"] = {key: _plain(value) for key, value in fields.items()}

    return _document(error=body)


# ----------------------------------------------------------------------------
def _plain(value):
    """A field's value as JSON holds it: a sequence as a list, a path as
    its text. Anything else is left for ``dumps`` to write or refuse."""
    if isinstance(value, tuple | list | frozenset | set):
        items = [_plain(item) for item in value]

        return items if isinstance(value, tuple | list) else sorted(items)

    if isinstance(value, Path):
        return str(value)

    return value


# ############################################################################
# What a provider offers
# ############################################################################


# ----------------------------------------------------------------------------
def capabilities(offer) -> dict:
    """``ProviderCapabilities``, by the names the provider declares them
    under, so a front-end offers only what the provider can do.

    ``settings`` names the connection settings it reads; ``specifics``
    gives each provider-only parameter's type name and description.
    """
    return {
        "ordering": offer.ordering,
        "stop": offer.stop,
        "rule_sets": offer.rule_sets,
        "disable": offer.disable,
        "rename": offer.rename,
        "actions": sorted(offer.actions),
        "extensions": offer.extensions,
        "raw_query": offer.raw_query,
        "mark": offer.mark,
        "folder_counts": offer.folder_counts,
        "uidvalidity": offer.uidvalidity,
        "folder_visibility": offer.folder_visibility,
        "specifics": {
            key: {"type": item.kind.__name__, "description": item.description}
            for key, item in sorted(offer.specifics.items())
        },
        "settings": sorted(offer.settings),
        "declined": sorted(offer.declined),
    }


# ----------------------------------------------------------------------------
def old_name(settings, pending) -> dict:
    """What the rename from ``mxfilter`` left behind: each old setting name
    present without its new one, and the old config directory where the new
    one does not exist (null where it does, or neither is there).

    Names and places only. An old setting's value is never read, so it
    cannot be here; one of them is the password's.
    """
    return {
        "settings": [
            {
                "old": item.old,
                "new": item.new,
                "where": item.where.kind,
                "path": None
                if item.where.path is None
                else str(item.where.path),
            }
            for item in settings
        ],
        "config_dir": None
        if pending is None
        else {
            "old": str(pending.old),
            "new": str(pending.new),
            "operation": "config migrate",
        },
    }


# ############################################################################
# Reading commands
# ############################################################################


# ----------------------------------------------------------------------------
def scripts(active: str | None, others: list[str]) -> dict:
    """``filterset list``: every stored filter set, the active one first."""
    names = [*([active] if active else []), *others]

    return _document(
        filtersets=[{"name": name, "active": name == active} for name in names]
    )


# ----------------------------------------------------------------------------
def filterset_source(
    name: str, source: str, filters: list[str] | None
) -> dict:
    """``filterset show``: a filter set's text exactly as the server holds
    it, and its filters' names in order; ``filters`` is null where the
    text will not parse, which is the filter set most worth seeing."""
    return _document(
        filterset={"name": name, "source": source, "filters": filters}
    )


# ----------------------------------------------------------------------------
def folder_listing(listing, statuses=None) -> dict:
    """``folders``: every folder, and whether webmail shows it.

    With ``--counts``, ``statuses`` holds one record per folder, in the
    listing's order, and each folder gains ``messages``, ``unseen``, and
    ``size`` (bytes); a count the host did not report is null.
    """
    folders = [
        {"name": name, "subscribed": listing.is_subscribed(name)}
        for name in listing.folders
    ]

    for entry, status in zip(folders, statuses or (), strict=False):
        entry.update(
            messages=status.messages, unseen=status.unseen, size=status.size
        )

    return _document(
        delimiter=listing.delimiter, prefix=listing.prefix, folders=folders
    )


# ----------------------------------------------------------------------------
def message_listing(listing) -> dict:
    """``search``: the matched messages, newest first, or in the order
    ``sort`` names; ``sort`` is null for newest first. ``uidvalidity`` is
    what the UIDs are valid under, null where the host reports none."""
    order = listing.order

    return _document(
        folder=listing.folder,
        uidvalidity=listing.uidvalidity,
        more=listing.more,
        sort=None
        if order is None
        else {"key": order.key, "reverse": order.reverse},
        messages=[_summary(item) for item in listing.messages],
    )


# ----------------------------------------------------------------------------
def sender_report(report) -> dict:
    """``senders``: the mail counted by ``by``, busiest first.

    ``messages`` and ``unread`` cover every message that matched, and
    ``groups`` every distinct key, including rows ``--top`` and ``--min``
    left out of ``senders``. A row's ``key`` is null for mail with none.
    """
    return _document(
        folder=report.folder,
        by=report.by,
        messages=report.messages,
        unread=report.unread,
        groups=report.groups,
        senders=[
            {
                "key": row.key,
                "name": row.name,
                "total": row.total,
                "unread": row.unread,
                "unread_percent": row.unread_percent,
            }
            for row in report.senders
        ],
    )


# ----------------------------------------------------------------------------
def source(record) -> dict | None:
    """Where a setting's value came from: ``kind`` is flag, env file,
    environment, config file, default, derived, or prompt; ``name`` the
    flag, variable, or key, and ``path`` the file, where there is one.
    Null where nothing is recorded. Never a value."""
    if record is None:
        return None

    return {
        "kind": record.kind,
        "name": record.name or None,
        "path": None if record.path is None else str(record.path),
    }


# ----------------------------------------------------------------------------
def server_test(test) -> dict:
    """``server test``: the settings and where each came from, then what
    each server says it supports and the account's shape, the servers
    mailctl does not recognise, and drift since a saved baseline.

    ``baseline`` is null where none is saved; ``checked`` is false where
    one is and could not be compared, and ``error`` says why. The
    password's ``state`` is a fixed word, never derived from it.
    """
    config, rules_probe, mail_probe = test.config, test.rules, test.mail
    baseline = None

    if test.baseline_error is not None:
        baseline = {
            "checked": False,
            "error": error(
                str(test.baseline_error),
                test.baseline_error.code,
                test.baseline_error.fields,
            )["error"],
        }

    elif test.baseline is not None:
        check = test.baseline
        baseline = {
            "checked": True,
            "taken": check.stored.taken.strftime(TIME_FORMAT),
            "serious": len(check.serious),
            "informational": len(check.drift) - len(check.serious),
        }

    return _document(
        test={
            "provider": config.provider,
            "sources": [source(item) for item in config.consulted],
            "settings": [
                {
                    "name": name,
                    "value": getattr(config, name),
                    "source": source(config.sources.get(name)),
                }
                for name in test.settings
            ],
            "password": {
                "state": test.password_state,
                "source": source(config.password_origin()),
            },
            "connection": [
                {
                    "label": fact.label,
                    "value": fact.text,
                    "sources": {
                        label: source(config.sources.get(setting))
                        for label, setting in fact.settings
                    },
                }
                for fact in test.endpoints
            ],
            "rules": {
                "extensions": [
                    {
                        "name": state.name,
                        "advertised": state.advertised,
                        "required": state.required,
                        "enabled": state.enabled,
                        "disabled_by": source(state.disabled_by),
                    }
                    for state in test.extensions
                ],
                "disabled_extensions_source": source(
                    config.sources.get("disabled_extensions")
                ),
                "active": rules_probe.active,
                "others": list(rules_probe.others),
            },
            "mail": {
                "delimiter": mail_probe.delimiter,
                "folders": mail_probe.folder_count,
                "unsubscribed": list(mail_probe.unsubscribed)
                if test.folder_visibility
                else None,
                "facts": [
                    {"label": fact.label, "value": fact.text}
                    for fact in mail_probe.facts
                ],
            },
            "unrecognised": [
                CHANNELS.get(half, half) for half in test.unrecognised
            ],
            "baseline": baseline,
            "notes": list(test.notes),
        }
    )


# ----------------------------------------------------------------------------
def server_report(report, body: str | None) -> dict:
    """``server probe --report``: the redacted issue body for the servers
    mailctl does not recognise, and its title; null where it recognises
    them all. Nothing is sent anywhere."""
    return _document(
        report=None
        if report is None
        else {
            "title": report.title,
            "unrecognised": list(report.unrecognised),
            "body": body,
        }
    )


# ----------------------------------------------------------------------------
def drift_check(check) -> dict:
    """``server baseline check``: what changed since the baseline, each
    difference ``serious`` or ``info``."""
    return {
        "version": DRIFT_VERSION,
        "host": check.baseline.host,
        "baseline": str(check.baseline.path),
        "baseline_taken": check.stored.taken.strftime(TIME_FORMAT),
        "taken": check.record.taken.strftime(TIME_FORMAT),
        "account_recorded": check.account_recorded,
        "requires_known": check.requires_known,
        "serious": len(check.serious),
        "informational": len(check.drift) - len(check.serious),
        "drift": [_drift(item) for item in check.drift],
    }


# ----------------------------------------------------------------------------
def message(content) -> dict:
    """``view``: one message's headers, text, and attachments.

    The source bytes are not carried; ``view --raw`` is the way to them.
    """
    return _document(
        message={
            "uid": content.uid,
            "folder": content.folder,
            "uidvalidity": content.uidvalidity,
            "size": content.size,
            "flags": list(content.flags),
            "headers": [
                {"name": name, "value": value}
                for name, value in content.headers
            ],
            "body": content.body,
            "body_from_html": content.body_from_html,
            "attachments": [
                {
                    "name": item.name,
                    "content_type": item.content_type,
                    "size": item.size,
                }
                for item in content.attachments
            ],
        }
    )


# ----------------------------------------------------------------------------
def rules_report(report, steps) -> dict:
    """``rules``: the rules in evaluation order, and which cannot fire.
    ``steps`` is what each rule does, one entry per rule in the same
    order, as ``utilities.rules.describe_rules`` returns it."""
    return _document(
        filterset=report.script,
        filters=[
            _rule(rule, steps)
            for rule, steps in zip(report.rules, steps, strict=True)
        ],
        findings=[_shadow(finding) for finding in report.findings],
    )


# ############################################################################
# Plans
# ############################################################################


# ----------------------------------------------------------------------------
def plan(command: str, **body) -> dict:
    """A write command's ``--dry-run`` plan: what would change.

    ``changes`` is false where the command would find nothing to do.
    The helpers below map the records a plan is built from.
    """
    return _document(plan={"command": command, **body})


# ----------------------------------------------------------------------------
def change(changes: bool, report) -> dict:
    """Whether a script would change, and its diff; null where it would
    not, as the CLI then shows none. ``reformats`` means the upload
    re-indents more than ``text`` shows."""
    return {
        "changes": changes,
        "diff": {
            "label": report.label,
            "text": report.text,
            "reformats": report.reformats,
        }
        if changes
        else None,
    }


# ----------------------------------------------------------------------------
def activation(record) -> dict:
    """Which script runs. ``activate`` is whether this script is the active
    one once the change is made -- true where it already is."""
    return {
        "filterset": record.script,
        "active": record.active,
        "activate": record.activate,
    }


# ----------------------------------------------------------------------------
def placement(analysis) -> dict:
    """What would stop the rule running, and what it would stop."""
    return {
        "dead_on_arrival": [
            _shadow(item) for item in analysis.dead_on_arrival
        ],
        "starves": [_shadow(item) for item in analysis.starves],
    }


# ----------------------------------------------------------------------------
def actions(spec) -> dict:
    """An ``ActionSpec``; ``stop`` null is the provider's default."""
    return {
        "fileinto": spec.fileinto,
        "discard": spec.discard,
        "flags": list(spec.flags),
        "keep": spec.keep,
        "stop": spec.stop,
    }


# ----------------------------------------------------------------------------
def folder_plan(record) -> dict:
    """Where filed mail goes and how that folder comes to exist."""
    source = record.mailbox_disabled_by

    return {
        "requested": record.requested,
        "folder": record.folder,
        "delimiter": record.delimiter,
        "delimiter_assumed": record.delimiter_assumed,
        "status": record.status,
        "subscribe": record.subscribe,
        "case_variants": list(record.case_variants),
        "mailbox_disabled_by": None if source is None else source.describe(),
    }


# ----------------------------------------------------------------------------
def mail_plan(record) -> dict:
    """What the existing-mail pass would do to which messages."""
    return {
        "source": record.source,
        "uidvalidity": record.uidvalidity,
        "destination": record.destination,
        "flags": list(record.flags),
        "discard": record.discard,
        "moves": record.moves,
        "copies": record.copies,
        "count": record.count,
        "held": list(record.held),
        "unidentified": list(record.unidentified),
        "messages": [_summary(item) for item in record.messages],
    }


# ----------------------------------------------------------------------------
def add_plan(record, spec, missing) -> dict:
    """``filter add``: the rule merged into the filter set, not yet
    uploaded. ``actions`` is ``spec``, the actions as asked for;
    ``missing_extensions`` is what the rule needs that the server does
    not list."""
    return plan(
        "filter add",
        **change(record.before != record.after, record.diff),
        filter=record.name,
        criteria=record.criteria.to_dict(),
        actions=actions(spec),
        folder=folder_plan(record.folder),
        missing_extensions=list(missing),
        placement=placement(record.placement),
        **activation(record),
    )


# ----------------------------------------------------------------------------
def apply_plan(criteria, spec, folder, max_messages: int, mail) -> dict:
    """``filter apply``: what the existing-mail pass would do. ``mail`` is
    null, and ``changes`` false, where the actions leave matching mail as
    it is, so nothing was searched; ``over_limit`` is whether more match
    than ``max_messages``, which refuses the whole pass."""
    return plan(
        "filter apply",
        changes=bool(mail and mail.changes),
        criteria=criteria.to_dict(),
        actions=actions(spec),
        folder=folder_plan(folder),
        max_messages=max_messages,
        over_limit=bool(mail and mail.count > max_messages),
        mail=None if mail is None else mail_plan(mail),
    )


# ----------------------------------------------------------------------------
def removal_plan(record) -> dict:
    """``filter remove``: the filter taken out, not yet uploaded."""
    return plan(
        "filter remove",
        **change(record.changes, record.diff),
        filter=record.rule,
        **activation(record),
    )


# ----------------------------------------------------------------------------
def move_plan(record) -> dict:
    """``filter move``: where the filter is and where it would be, both
    counting from 1 over ``count`` filters, and what the new place would
    stop or be stopped by."""
    return plan(
        "filter move",
        **change(record.changes, record.diff),
        filter=record.rule,
        from_position=record.from_index + 1,
        to_position=record.to_index + 1,
        count=record.count,
        placement=placement(record.placement),
        **activation(record),
    )


# ----------------------------------------------------------------------------
def switch_plan(record) -> dict:
    """``filter enable`` or ``filter disable``, as ``enable`` says."""
    return plan(
        "filter enable" if record.enable else "filter disable",
        **change(record.changes, record.diff),
        filter=record.rule,
        enable=record.enable,
        **activation(record),
    )


# ----------------------------------------------------------------------------
def rename_plan(record) -> dict:
    """``filter rename``: the filter's name now, and the one asked for."""
    return plan(
        "filter rename",
        **change(record.changes, record.diff),
        filter=record.rule,
        new_name=record.new_name,
        **activation(record),
    )


# ----------------------------------------------------------------------------
def mark_plan(record) -> dict:
    """``mark``: the flags asked for, and what each message would gain and
    lose -- only what would really change, so both are empty for a message
    that already looks as asked."""
    return plan(
        "mail mark",
        changes=not record.is_empty,
        folder=record.folder,
        uidvalidity=record.uidvalidity,
        set=list(record.add),
        clear=list(record.remove),
        messages=[
            {
                "uid": item.uid,
                "flags": list(item.flags),
                "set": list(item.add),
                "clear": list(item.remove),
            }
            for item in record.messages
        ],
    )


# ----------------------------------------------------------------------------
def baseline_save_plan(record, user: str) -> dict:
    """``server baseline save``: the file that would be written, and,
    where one is saved already, when ``user``'s part of it was taken,
    what has changed since, and the file's diff. A save always writes:
    with nothing else changed it moves the date."""
    previous = record.previous

    return plan(
        "server baseline save",
        changes=True,
        host=record.host,
        path=str(record.path),
        taken=record.record.taken.strftime(TIME_FORMAT),
        previous_taken=None
        if previous is None
        else previous.record_for(user).taken.strftime(TIME_FORMAT),
        requires_known=record.requires_known,
        drift=[_drift(item) for item in record.drift],
        diff=None if previous is None else "\n".join(record.diff),
    )


# ----------------------------------------------------------------------------
def migration_plan(record) -> dict:
    """``config migrate``: what would move from the old directory to the
    new one, with each entry's mode, and each config.toml path pointed
    at the new directory. ``changes`` is false, and ``old`` null, where
    there is no old directory."""
    if record is None:
        return plan(
            "config migrate",
            changes=False,
            old=None,
            new=None,
            entries=[],
            references=[],
        )

    return plan(
        "config migrate",
        changes=True,
        old=str(record.old),
        new=str(record.new),
        entries=[
            {
                "path": str(entry.relative),
                "mode": entry.mode,
                "directory": entry.directory,
            }
            for entry in record.entries
        ],
        references=[
            {"key": item.key, "old": item.written, "new": item.replacement}
            for item in record.references
        ],
    )


# ----------------------------------------------------------------------------
def folder_rename_plan(record) -> dict:
    """``rename-folder``: every folder that moves, whether each is
    subscribed now, the messages the folder holds, and each rule's filing
    action that would be repointed. A rename always changes something;
    ``diff`` is null where no rule does, and the script is left alone."""
    return plan(
        "folder rename",
        changes=True,
        diff=change(record.rules_change, record.diff)["diff"],
        requested={"old": record.requested_old, "new": record.requested_new},
        old=record.old,
        new=record.new,
        delimiter=record.delimiter,
        messages=record.messages,
        folders=[
            {"old": move.old, "new": move.new, "subscribed": move.subscribed}
            for move in record.moves
        ],
        missing_parents=list(record.missing_parents),
        filters=[
            {"filter": item.rule, "old": item.old, "new": item.new}
            for item in record.retargets
        ],
        **activation(record),
    )


# ----------------------------------------------------------------------------
def restore_plan(
    record, filters_before: int | None, filters_after: int | None
) -> dict:
    """``filterset restore``: a backup file put whole over a filter set,
    the raw diff, and how many filters each side holds (null where one
    will not parse)."""
    return plan(
        "filterset restore",
        **change(record.changes, record.diff),
        source=str(record.source),
        filters_before=filters_before,
        filters_after=filters_after,
        **activation(record),
    )


# ----------------------------------------------------------------------------
def subscription_plan(command: str, record) -> dict:
    """``folder subscribe`` or ``folder unsubscribe``: the folder as asked
    for and as the server names it, and its subscription now."""
    return plan(
        command,
        changes=record.changes,
        requested=record.requested,
        folder=record.folder,
        delimiter=record.delimiter,
        subscribe=record.subscribe,
        subscribed_now=record.subscribed_now,
    )


# ----------------------------------------------------------------------------
def folder_creation_plan(record) -> dict:
    """``folder create``: the folder, how it comes to exist, and the
    levels above it the server is expected to make with it."""
    return plan(
        "folder create",
        changes=record.changes,
        folder=folder_plan(record.target),
        subscribed_now=record.subscribed_now,
        missing_parents=list(record.missing_parents),
    )


# ----------------------------------------------------------------------------
def optimize_plan(record) -> dict:
    """``optimize-rules``: each rule to remove, move, or merge, and what
    was left alone for want of certainty. ``considered`` names the kinds
    of change looked for."""
    proposals = record.proposals

    return plan(
        "filter optimize",
        **change(record.changes, record.diff),
        considered=list(record.kinds),
        removals=[
            {"filter": item.rule, "covered_by": item.covered_by}
            for item in proposals.removals
        ],
        reorders=[
            {"filter": item.rule, "before": item.before}
            for item in proposals.reorders
        ],
        merges=[
            {
                "into": item.into,
                "absorbed": list(item.absorbed),
                "header": item.header,
                "match_type": item.match_type,
                "keys": list(item.keys),
            }
            for item in proposals.merges
        ],
        uncertain=[
            {
                "kind": item.kind,
                "filters": list(item.rules),
                "reason": item.reason,
            }
            for item in proposals.uncertain
        ],
        **activation(record),
    )


# ############################################################################
# Carrying a plan out
# ############################################################################


# ----------------------------------------------------------------------------
def script_change_result(
    command: str, name: str | None, record, backup: Path
) -> dict:
    """What a filter-set change did once carried out: the filter it
    changed, by the name it has once the change is made (null for a
    change to the whole set), the filter set, whether it is now the
    active one, and where the copy taken before the upload was
    written."""
    return _document(
        result={
            "command": command,
            "filter": name,
            "filterset": record.script,
            "activated": record.activate,
            "backup": str(backup),
        }
    )


# ----------------------------------------------------------------------------
def mail_result(record, mail) -> dict:
    """What ``filter apply`` did once carried out: how many messages were
    flagged, moved, deleted, and copied, from ``source`` to
    ``destination`` (empty where nothing is filed)."""
    return _document(
        result={
            "command": "filter apply",
            "source": mail.source,
            "destination": mail.destination,
            "flagged": record.flagged,
            "moved": record.moved,
            "deleted": record.deleted,
            "copied": record.copied,
        }
    )


# ----------------------------------------------------------------------------
def mark_result(record, result) -> dict:
    """What ``mail mark`` did once carried out: how many messages gained
    a flag and how many lost one, in ``folder``."""
    return _document(
        result={
            "command": "mail mark",
            "folder": record.folder,
            "added": result.added,
            "removed": result.removed,
        }
    )


# ----------------------------------------------------------------------------
def baseline_save_result(record, path: Path) -> dict:
    """What ``server baseline save`` wrote: the host and the file."""
    return _document(
        result={
            "command": "server baseline save",
            "host": record.host,
            "path": str(path),
            "taken": record.record.taken.strftime(TIME_FORMAT),
        }
    )


# ----------------------------------------------------------------------------
def migration_result(record) -> dict:
    """What ``config migrate`` did: the two directories; each step was
    sent as it landed. ``restart`` is always true: a process reads its
    settings once, so only one started after the move reads what moved."""
    return _document(
        result={
            "command": "config migrate",
            "old": str(record.old),
            "new": str(record.new),
            "restart": True,
        }
    )


# ----------------------------------------------------------------------------
def backup_result(
    record, path: Path, filters: int | None, written: bool
) -> dict:
    """What ``filterset backup`` wrote, or would write where ``written``
    is false: the filter set, the file, and how many filters it holds
    (null where it will not parse)."""
    return _document(
        result={
            "command": "filterset backup",
            "filterset": record.script,
            "path": str(path),
            "filters": filters,
            "written": written,
        }
    )


# ----------------------------------------------------------------------------
def folder_creation_result(record) -> dict:
    """What ``folder create`` achieved: the folder exists either way, and
    ``subscribe_error`` is why subscribing to it failed, where it did."""
    return _document(
        result={
            "command": "folder create",
            "folder": record.folder,
            "subscribed": record.subscribed,
            "subscribe_error": record.subscribe_error or None,
            "by_subscription": record.by_subscription,
        }
    )


# ----------------------------------------------------------------------------
def subscription_result(command: str, record) -> dict:
    """What ``folder subscribe`` or ``folder unsubscribe`` left: the
    folder, and whether it is subscribed now."""
    return _document(
        result={
            "command": command,
            "folder": record.folder,
            "subscribed": record.subscribe,
        }
    )


# ----------------------------------------------------------------------------
def folder_rename_result(record) -> dict:
    """What ``folder rename`` did, then what reading the account back
    found. ``ok`` is false where any check failed; a failed check's
    ``operation`` names the command that would set it right, given
    ``arguments``. ``backup`` is null where no filter changed."""
    plan_record = record.plan

    return _document(
        result={
            "command": "folder rename",
            "old": plan_record.old,
            "new": plan_record.new,
            "filterset": plan_record.script
            if plan_record.rules_change
            else None,
            "backup": None if record.backup is None else str(record.backup),
            "subscribed": list(record.subscribed),
            "unsubscribed": list(record.unsubscribed),
            "subscription_errors": list(record.subscription_errors),
            "ok": record.ok,
            "checks": [
                {
                    "label": check.label,
                    "ok": check.ok,
                    "detail": check.detail or None,
                    "operation": check.operation or None,
                    "arguments": list(check.arguments),
                }
                for check in record.checks
            ],
        }
    )


# ----------------------------------------------------------------------------
def event(record) -> dict:
    """One step of a change, as it lands; ``kind`` says which, and the
    rest of the keys are that kind's."""
    if isinstance(record, events.ScriptBackedUp):
        body = {
            "kind": "filterset_backed_up",
            "filterset": record.script,
            "path": str(record.path),
        }

    elif isinstance(record, events.ScriptUploaded):
        body = {
            "kind": "filterset_uploaded",
            "filterset": record.script,
            "activated": record.activated,
        }

    elif isinstance(record, events.FolderCreated):
        result = record.result
        body = {
            "kind": "folder_created",
            "folder": result.folder,
            "subscribed": result.subscribed,
            "subscribe_error": result.subscribe_error or None,
            "by_subscription": result.by_subscription,
        }

    elif isinstance(record, events.FolderRenamed):
        body = {
            "kind": "folder_renamed",
            "old": record.old,
            "new": record.new,
            "children": record.children,
        }

    elif isinstance(record, events.SubscriptionChanged):
        body = {
            "kind": "subscription_changed",
            "folder": record.folder,
            "subscribed": record.subscribed,
        }

    elif isinstance(record, migration.FileMoved):
        body = {
            "kind": "file_moved",
            "source": str(record.source),
            "destination": str(record.destination),
        }

    elif isinstance(record, migration.ReferenceRewritten):
        body = {
            "kind": "reference_rewritten",
            "key": record.reference.key,
            "new": record.reference.replacement,
            "rewritten": record.rewritten,
        }

    elif isinstance(record, migration.OldDirRemoved):
        body = {"kind": "old_dir_removed", "path": str(record.path)}

    else:
        raise TypeError(f"no JSON mapping for {type(record).__name__}")

    return _document(event=body)


# ----------------------------------------------------------------------------
def alert(half: str, record: events.ServerAlert) -> dict:
    """An alert or a warning a server sent; ``channel`` is the half it
    came from, and ``text`` the server's own words."""
    return _document(
        alert={
            "channel": CHANNELS.get(half, half),
            "kind": record.kind,
            "text": record.text,
        }
    )


# ############################################################################
# Shared records
# ############################################################################


# ----------------------------------------------------------------------------
def _drift(item) -> dict:
    return {
        "severity": item.severity,
        "kind": item.kind,
        "half": item.half,
        "name": item.name,
        "before": item.before,
        "after": item.after,
    }


# ----------------------------------------------------------------------------
def _summary(item) -> dict:
    return {
        "uid": item.uid,
        "received": _received(item.date),
        "size": item.size,
        "flags": list(item.flags),
        "has_attachments": item.has_attachments,
        "from": item.sender,
        "subject": item.subject,
        "folder": item.folder,
    }


# ----------------------------------------------------------------------------
def _received(value: str) -> str | None:
    """ISO 8601, local time; null where the server gave no date.

    A provider writing some other form has it passed through unchanged
    rather than dropped.
    """
    if not value:
        return None

    try:
        return datetime.strptime(value, RECEIVED_FORMAT).isoformat()

    except ValueError:
        return value


# ----------------------------------------------------------------------------
def _rule(rule, steps) -> dict:
    """A rule; ``position`` counts from 1, as ``rules`` and ``move-rule``
    show it. ``actions`` are the host's own names for what it does, and
    ``steps`` the same in neutral terms (``step``)."""
    return {
        "position": rule.index + 1,
        "name": rule.name,
        "disabled": rule.disabled,
        "stops": rule.stops,
        "combinator": rule.combinator,
        "tests": [
            {
                "header": test.header,
                "match_type": test.match_type,
                "keys": list(test.keys),
                "comparator": test.comparator,
            }
            for test in rule.tests
        ],
        "actions": list(rule.actions),
        "steps": [step(item) for item in steps],
        "unmodelled": list(rule.unmodelled),
    }


# ----------------------------------------------------------------------------
def step(record) -> dict:
    """One thing a filter does: ``kind`` is a ``STEP_KINDS`` value.
    ``argument`` is the folder filed into or the flag added or removed,
    null where the host's listing does not carry it; for ``other`` it is
    the action's name as the host writes it, the filter's own text, since
    no neutral word exists for it. ``creates`` is whether a ``file`` step
    also creates its folder as mail arrives."""
    return {
        "kind": STEP_KINDS[record.kind],
        "argument": record.argument,
        "creates": record.creates,
    }


# ----------------------------------------------------------------------------
def _shadow(finding) -> dict:
    """``certainty`` is ``certain`` (decided) or ``possible`` (suspected)."""
    return {
        "certainty": finding.certainty,
        "broad": finding.broad,
        "narrow": finding.narrow,
        "reason": finding.reason,
    }
