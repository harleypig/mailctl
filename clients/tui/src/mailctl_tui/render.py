"""The API's documents, worded for the screen.

Each function takes a document, or one of its records, as the API sent it
and returns the words to show. None works anything out the core decides:
what a filter matches, which filters cannot fire, what a plan changes and
where a backup went all arrive in the document, and are only laid out
here. Every value from the server goes through :mod:`.text` first.

The words follow CONVENTIONS *What the user sees is the same for every
provider*: a filter and a filter set, never the host's own terms.
"""

from .client import ApiError, ApiGone
from .text import one_line

__all__ = [
    "actions",
    "activation",
    "alert_line",
    "condition",
    "criteria",
    "event_line",
    "failure",
    "findings",
    "folder_lines",
    "marks",
    "message_marks",
    "missing_extensions",
    "placement",
    "quoted",
    "received",
    "result_lines",
    "steps",
]

# A message's flags as the listing and the message screen word them.
SEEN = "\\seen"
FLAGGED = "\\flagged"
ANSWERED = "\\answered"


# ----------------------------------------------------------------------------
def quoted(value: object) -> str:
    """A server's value, quoted, on one line. ``repr`` comes first, as the
    CLI's does, so a control character shows as one escape, not as the
    escape of an escape."""
    return one_line(repr(str(value)))


# ############################################################################
# filter.list
# ############################################################################


# ----------------------------------------------------------------------------
def condition(item: dict) -> str:
    """A filter's tests on one line, as the CLI's ``filter list`` words
    them: header, match type, keys, joined by the filter's combinator."""
    tests = item.get("tests", [])
    unmodelled = [one_line(str(name)) for name in item.get("unmodelled", [])]

    if not tests and unmodelled:
        return f"({', '.join(unmodelled)} -- not modelled)"

    joiner = " OR " if item.get("combinator") == "anyof" else " AND "
    parts = [
        f"{one_line(str(test.get('header', '')))} "
        f"{one_line(str(test.get('match_type', '')))} "
        + " | ".join(quoted(key) for key in test.get("keys", []))
        for test in tests
    ]
    line = joiner.join(parts) or "(no condition)"

    if unmodelled:
        line += f"  [+ {', '.join(unmodelled)}: not modelled]"

    return line


# ----------------------------------------------------------------------------
def steps(item: dict) -> str:
    """What a filter does, from its ``steps``, as the CLI's ``filter
    list`` words them. A filter's ``actions`` are the host's own words and
    are never shown; a document without ``steps`` shows nothing here."""
    words = []

    for step in item.get("steps", []):
        kind = step.get("kind")
        argument = step.get("argument")

        if kind == "file":
            where = quoted(argument) if argument else "a folder"
            creating = (
                ", creating it if it is missing" if step.get("creates") else ""
            )
            words.append(f"files the message into {where}{creating}")

        elif kind in ("flag", "unflag"):
            verb = "adds" if kind == "flag" else "removes"
            flag = (
                f"the flag {one_line(str(argument))}" if argument else "a flag"
            )
            words.append(f"{verb} {flag}")

        elif kind == "discard":
            words.append("discards the message")

        elif kind == "keep":
            words.append("keeps the message in the inbox")

        elif kind == "stop":
            words.append("stops")

        else:
            # No neutral word exists for it, so it is named as the filter
            # set names it: the filter's own text, like the diff.
            words.append(f"does {quoted(argument)}")

    return "; ".join(words)


# ----------------------------------------------------------------------------
def marks(item: dict) -> str:
    words = []

    if item.get("disabled"):
        words.append("disabled")

    if item.get("stops"):
        words.append("stop")

    return ", ".join(words)


# ----------------------------------------------------------------------------
def findings(items: list, heading: str) -> str:
    """Filters that cannot fire, decided ones marked ``!`` and suspected
    ones ``?``, as the CLI shows them; empty where there are none."""
    if not items:
        return ""

    lines = [heading]

    for item in items:
        certain = item.get("certainty") == "certain"
        mark, verb = ("!", "never runs") if certain else ("?", "may never run")
        reason = one_line(str(item.get("reason", "")))
        lines.append(
            f"  {mark} {quoted(item.get('narrow', ''))} {verb} -- {reason}"
        )

    return "\n".join(lines)


# ############################################################################
# mail.search and mail.view
# ############################################################################


# ----------------------------------------------------------------------------
def message_marks(item: dict) -> str:
    """A message's state in words: unread, flagged, answered, and whether
    it has attachments, from the flags the document lists."""
    flags = {str(flag).lower() for flag in item.get("flags", [])}
    words = []

    if SEEN not in flags:
        words.append("unread")

    if FLAGGED in flags:
        words.append("flagged")

    if ANSWERED in flags:
        words.append("answered")

    if item.get("has_attachments"):
        words.append("attachments")

    return ", ".join(words)


# ----------------------------------------------------------------------------
def received(value: object) -> str:
    """The document's ISO 8601 time, shown to the minute; a value in some
    other form is shown as it came."""
    if not isinstance(value, str):
        return ""

    text = one_line(value)

    return text[:16].replace("T", " ") if "T" in text else text


# ############################################################################
# filter.add.plan
# ############################################################################


# ----------------------------------------------------------------------------
def criteria(document: dict) -> str:
    """A filter document's ``criteria``: each header term, joined by
    ``match``, then the body terms."""
    compare = one_line(str(document.get("compare", "contains")))
    joiner = " OR " if document.get("match") == "any" else " AND "
    parts = [
        f"{one_line(str(term.get('header', '')))} {compare} "
        f"{quoted(term.get('value', ''))}"
        for term in document.get("terms", [])
    ]
    parts += [
        f"the text contains {quoted(text)}"
        for text in document.get("body", [])
    ]

    return joiner.join(parts) or "(no condition)"


# ----------------------------------------------------------------------------
def actions(document: dict) -> str:
    """A plan's ``actions``, the actions as they were asked for. ``stop``
    null is the provider's own default, so nothing is said of it."""
    words = []
    folder = document.get("fileinto")

    if folder:
        words.append(f"move it to {quoted(folder)}")

    flags = [str(flag) for flag in document.get("flags", [])]

    for flag in flags:
        if flag.lower() == SEEN:
            words.append("mark it read")

        elif flag.lower() == FLAGGED:
            words.append("flag it")

        else:
            words.append(f"tag it {quoted(flag)}")

    if document.get("keep"):
        words.append("keep it in the inbox too")

    if document.get("discard"):
        words.append("delete it")

    if document.get("stop") is False:
        words.append("let later filters see it too")

    elif document.get("stop") is True:
        words.append("stop there")

    return ", then ".join(words) or "(nothing)"


# ----------------------------------------------------------------------------
def folder_lines(document: dict | None) -> list[str]:
    """What the plan says of the folder mail is moved to."""
    if not document or document.get("status") in (None, "none"):
        return []

    folder = quoted(document.get("folder", ""))
    lines = []

    if document.get("delimiter_assumed"):
        lines.append(
            f"The folder list could not be read, so the folder is taken "
            f"to be {folder}."
        )

    elif document.get("requested") != document.get("folder"):
        lines.append(
            f"{quoted(document.get('requested', ''))} is the folder {folder}."
        )

    status = document.get("status")
    shown = " and shown in webmail" if document.get("subscribe") else ""

    if status == "missing":
        lines.append(f"There is no folder {folder}.")

    elif status == "create":
        lines.append(f"The folder {folder} will be created{shown}.")

    elif status == "created-on-delivery":
        lines.append(
            f"The folder {folder} will be created when the first message "
            "is moved there."
        )

    elif status == "create-and-on-delivery":
        lines.append(f"The folder {folder} will be created now{shown}.")

    elif status == "uncreatable":
        lines.append(f"The folder {folder} cannot be created here.")

    variants = [quoted(name) for name in document.get("case_variants", [])]

    if variants:
        lines.append(
            f"No folder is named exactly {folder}, but {', '.join(variants)} "
            "differs only in case; folder names are case-sensitive."
        )

    return lines


# ----------------------------------------------------------------------------
def placement(document: dict | None) -> str:
    """A plan's placement findings, the dead-on-arrival ones first; empty
    where there are none."""
    document = document or {}
    parts = [
        findings(
            document.get("dead_on_arrival", []),
            "Before this filter is reached:",
        ),
        findings(
            document.get("starves", []), "This filter would come before:"
        ),
    ]

    return "\n\n".join(part for part in parts if part)


# ----------------------------------------------------------------------------
def missing_extensions(names: list) -> str:
    if not names:
        return ""

    listed = ", ".join(one_line(str(name)) for name in names)

    return (
        f"The server does not list what this filter needs: {listed}. It "
        "checks the filter set before storing it, and may refuse it."
    )


# ----------------------------------------------------------------------------
def activation(plan: dict) -> str:
    """Whether saving changes which filter set runs, as the plan says."""
    filterset = quoted(plan.get("filterset", ""))
    active = plan.get("active")

    if not plan.get("activate"):
        return (
            f"Filter set {filterset} is not the active one ({quoted(active)} "
            "is), so it is stored but not run."
        )

    if active is None:
        return (
            f"The account has no active filter set; {filterset} will "
            "become the active filter set."
        )

    if active != plan.get("filterset"):
        return (
            f"Filter set {filterset} will become the active one, in place "
            f"of {quoted(active)}."
        )

    return ""


# ############################################################################
# filter.add.execute
# ############################################################################


# ----------------------------------------------------------------------------
def event_line(document: dict) -> str:
    """One ``event`` notification's document, as the CLI words the step."""
    event = document.get("event", {})
    kind = event.get("kind")
    filterset = quoted(event.get("filterset", ""))

    if kind == "filterset_backed_up":
        path = one_line(str(event.get("path", "")))

        return f"Backed up the current filter set to {path}"

    if kind == "filterset_uploaded" and event.get("activated"):
        return f"Saved and activated filter set {filterset}"

    if kind == "filterset_uploaded":
        return f"Saved filter set {filterset}; it was not activated"

    if kind == "folder_created":
        return _folder_created(event)

    if kind == "subscription_changed":
        folder = quoted(event.get("folder", ""))

        return (
            f"Showing {folder} in webmail"
            if event.get("subscribed")
            else f"Hid {folder} from webmail"
        )

    # A kind added after this client was written: say that a step landed
    # rather than drop it.
    return f"Done: {one_line(str(kind))}"


# ----------------------------------------------------------------------------
def _folder_created(event: dict) -> str:
    folder = quoted(event.get("folder", ""))

    if not event.get("by_subscription"):
        return f"Created folder {folder}"

    if event.get("subscribed"):
        return f"Created folder {folder}; webmail shows it"

    if event.get("subscribe_error"):
        return (
            f"Created folder {folder}, but it could not be shown in "
            f"webmail: {one_line(str(event['subscribe_error']))}. Mail moved "
            "there arrives all the same."
        )

    return f"Created folder {folder}; webmail does not show it"


# ----------------------------------------------------------------------------
def result_lines(document: dict) -> list[str]:
    """What ``filter.add.execute`` did, from its result document."""
    result = document.get("result", {})

    return [
        f"Filter {quoted(result.get('filter', ''))} is saved in filter set "
        f"{quoted(result.get('filterset', ''))}.",
        "The filter set as it was before is backed up at "
        f"{one_line(str(result.get('backup', '')))}",
        "Mail already delivered was left alone; the filter acts on new mail.",
    ]


# ############################################################################
# Notifications and failures
# ############################################################################


# ----------------------------------------------------------------------------
def alert_line(alert: dict) -> str:
    kind = one_line(str(alert.get("kind", "alert")))
    channel = one_line(str(alert.get("channel", "mail")))
    text = one_line(str(alert.get("text", "")))

    return f"{kind.capitalize()} from the {channel} server: {text}"


# ----------------------------------------------------------------------------
def failure(exc: ApiError | ApiGone) -> str:
    """An error as the API worded it; its message names no front-end's
    control, so it reads right here (ADR 0008 *Errors keep their codes*)."""
    if isinstance(exc, ApiError):
        error = exc.data.get("error")
        message = (
            (error.get("message") if isinstance(error, dict) else None)
            or exc.data.get("message")
            or exc.message
        )

        return f"mailctl refused: {one_line(str(message))}"

    return one_line(str(exc))
