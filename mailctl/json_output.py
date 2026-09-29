"""The documents ``--json`` prints, one explicit mapping per record.

Part of the CLI front-end: a document is built from the neutral records
the utilities return, never from the text the CLI renders, so values are
whole, flags are raw, and nothing is clipped or rewritten for a terminal.
Every document is an object carrying ``version`` beside what its command
reports; README.md > *Output for scripts* lists the shapes.

The mappings are a contract a script depends on (CONVENTIONS.md > The
core returns data). Adding a key is compatible; renaming or removing one,
or changing what a value means, raises ``JSON_VERSION``.
"""

import json
from datetime import datetime

from .config import Secret

__all__ = [
    "JSON_VERSION",
    "actions",
    "activation",
    "change",
    "dumps",
    "error",
    "folder_listing",
    "folder_plan",
    "mail_plan",
    "mark_plan",
    "message",
    "message_listing",
    "optimize_plan",
    "placement",
    "plan",
    "rules_report",
    "scripts",
    "sender_report",
]

JSON_VERSION = 1

# How the IMAP component writes a message's INTERNALDATE: local time, the
# zone already dropped, so the ISO form carries no offset either.
RECEIVED_FORMAT = "%Y-%m-%d %H:%M:%S"


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


# ----------------------------------------------------------------------------
def error(message: str, code: str | None = None) -> dict:
    """A failure, as ``main`` reports it on stderr under ``--json``.

    ``code`` names the condition, where the failure has one, so a script
    can tell one refusal from another without reading the message.
    """
    body = {"message": message}

    if code is not None:
        body["code"] = code

    return _document(error=body)


# ############################################################################
# Reading commands
# ############################################################################


# ----------------------------------------------------------------------------
def scripts(active: str | None, others: list[str]) -> dict:
    """``list``: every stored script, the active one first."""
    names = [*([active] if active else []), *others]

    return _document(
        scripts=[{"name": name, "active": name == active} for name in names]
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
    ``sort`` names; ``sort`` is null for newest first."""
    order = listing.order

    return _document(
        folder=listing.folder,
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
def message(content) -> dict:
    """``view``: one message's headers, text, and attachments.

    The source bytes are not carried; ``view --raw`` is the way to them.
    """
    return _document(
        message={
            "uid": content.uid,
            "folder": content.folder,
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
def rules_report(report) -> dict:
    """``rules``: the rules in evaluation order, and which cannot fire."""
    return _document(
        script=report.script,
        rules=[_rule(rule) for rule in report.rules],
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
        "script": record.script,
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
        "destination": record.destination,
        "flags": list(record.flags),
        "discard": record.discard,
        "moves": record.moves,
        "copies": record.copies,
        "count": record.count,
        "messages": [_summary(item) for item in record.messages],
    }


# ----------------------------------------------------------------------------
def mark_plan(record) -> dict:
    """``mark``: the flags asked for, and what each message would gain and
    lose -- only what would really change, so both are empty for a message
    that already looks as asked."""
    return plan(
        "mark",
        changes=not record.is_empty,
        folder=record.folder,
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
def folder_rename_plan(record) -> dict:
    """``rename-folder``: every folder that moves, whether each is
    subscribed now, the messages the folder holds, and each rule's filing
    action that would be repointed. A rename always changes something;
    ``diff`` is null where no rule does, and the script is left alone."""
    return plan(
        "rename-folder",
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
        rules=[
            {"rule": item.rule, "old": item.old, "new": item.new}
            for item in record.retargets
        ],
        **activation(record),
    )


# ----------------------------------------------------------------------------
def optimize_plan(record) -> dict:
    """``optimize-rules``: each rule to remove, move, or merge, and what
    was left alone for want of certainty. ``considered`` names the kinds
    of change looked for."""
    proposals = record.proposals

    return plan(
        "optimize-rules",
        **change(record.changes, record.diff),
        considered=list(record.kinds),
        removals=[
            {"rule": item.rule, "covered_by": item.covered_by}
            for item in proposals.removals
        ],
        reorders=[
            {"rule": item.rule, "before": item.before}
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
                "rules": list(item.rules),
                "reason": item.reason,
            }
            for item in proposals.uncertain
        ],
        **activation(record),
    )


# ############################################################################
# Shared records
# ############################################################################


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
def _rule(rule) -> dict:
    """A rule; ``position`` counts from 1, as ``rules`` and ``move-rule``
    show it."""
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
        "unmodelled": list(rule.unmodelled),
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
