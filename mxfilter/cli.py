"""Command-line interface.

This module is the CLI front-end and nothing else: it parses arguments,
turns them into the engine's plain inputs, calls ``mxfilter.engine``, and
renders what comes back. Every decision a person makes -- confirming,
``--dry-run``, ``--yes`` -- is taken here, between the engine's plan and
its execution.

Every mutating subcommand follows the same shape: work out what would
change, show it, and only then -- after a backup and the server's own
validation -- change it. ``--dry-run`` stops after the "show it" step.
"""

import argparse
import re
import sys
import traceback

from . import MxFilterError, __version__, engine
from .config import (
    CONFIG_FILE,
    ENV_FILE,
    ENVIRONMENT,
    SIEVE_TLS_MODES,
    load_config,
)
from .criteria import COMPARE_OPS, MATCH_MODES, Criteria
from .engine import (
    DEFAULT_LIST_LIMIT,
    DEFAULT_MAX_MESSAGES,
    ActionSpec,
    RuleRequest,
)
from .imap import FolderCreation, decode_header_value
from .rules import CERTAIN
from .sieve import (
    PLACE_AFTER,
    PLACE_BEFORE,
    PLACE_FIRST,
    PLACE_LAST,
    REPORTABLE_EXTENSIONS,
    DisplayDiff,
    Placement,
)

__all__ = ["build_parser", "main"]

DEFAULT_MOVE_THRESHOLD = 25
PREVIEW_LIMIT = 20

ACTIVATE_HELP = (
    "make the script the active one, the one Sieve runs. Without it, a "
    "--script other than the active one is stored but left inactive"
)

# The action flags refused with an explanation rather than an argparse
# "unrecognized arguments" error; see action_parser.
REFUSED_ACTION_FLAGS = ("redirect", "notify", "vacation")

# The headers a person recognises one of their own emails by, shown by
# 'from-message' before it derives anything. List-Id earns its place
# beside the obvious four because '--derive auto' prefers it, so a rule
# derived from a mailing list has to show the header it came from.
IDENTIFYING_HEADERS = ("Date", "From", "To", "Subject", "List-Id")

# Wide enough that a real Subject or List-Id survives whole, capped so a
# pathological header cannot flood the screen. Deliberately wider than the
# preview table's columns: this is the step where the operator confirms
# they picked the right message, and clipping the value being verified
# defeats the purpose.
HEADER_WIDTH = 100

# The headers 'view' shows above a message body.
VIEW_HEADERS = ("Date", "From", "To", "Cc", "Subject", "List-Id")

# What a terminal may act on rather than draw: C0 controls other than tab
# and newline, DEL, and the C1 range. ESC, which opens every ANSI and OSC
# sequence, is among them, so escaping it leaves the rest of a sequence as
# inert visible text. A carriage return passes only as half of a CRLF --
# alone it rewinds the line so later text can overprint it.
#
# Also the bidi embeddings, overrides, and isolates (U+202A-U+202E,
# U+2066-U+2069): not terminal controls, but they reorder what is drawn,
# so 'invoice_<RLO>fdp.exe' displays as 'invoice_exe.pdf'. The marks
# U+200E, U+200F, and U+061C pass: they cannot reorder strong characters,
# and RTL mail uses them legitimately.
UNSAFE_CHARACTERS = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]"
    r"|\r(?!\n)"
)

# IMAP flag -> the mark the listing shows for it. Unread is the absence of
# \Seen, so it is handled apart.
FLAG_MARKS = (("\\flagged", "F"), ("\\answered", "R"), ("\\deleted", "D"))
MARK_LEGEND = "N unread, F flagged, R replied, D deleted, @ attachment"


# ############################################################################
# Small output helpers
# ############################################################################


# ----------------------------------------------------------------------------
def warn(message: str) -> None:
    """Print a warning to stderr so it survives a piped stdout."""
    print(f"warning: {message}", file=sys.stderr)


# ----------------------------------------------------------------------------
def confirm(prompt: str, assume_yes: bool) -> bool:
    """Ask for confirmation, failing closed on a non-interactive stdin.

    A prompt that cannot be answered must not silently proceed, and must not
    hang either -- so a non-tty without ``--yes`` is an error naming the
    flag that would have allowed it.
    """
    if assume_yes:
        return True

    if not sys.stdin.isatty():
        raise MxFilterError(
            f"{prompt} -- refusing to continue without a terminal to ask. "
            f"Re-run with --yes to confirm, or --dry-run to preview."
        )

    answer = input(f"{prompt} [y/N] ").strip().lower()

    return answer in ("y", "yes")


# ----------------------------------------------------------------------------
def print_preview(messages: list, limit: int = PREVIEW_LIMIT) -> None:
    """Print a capped preview table of the messages that matched."""
    for message in messages[:limit]:
        print(format_summary(message))

    if len(messages) > limit:
        print(f"  ... and {len(messages) - limit} more")


# ----------------------------------------------------------------------------
def format_summary(message) -> str:
    """Render one message record as a fixed-width preview line.

    Rendering lives here rather than on the record: a MessageSummary is
    data, and a second front-end would want to lay it out differently.
    """
    return (
        f"  uid {message.uid:<8} {message.date:<20} "
        f"{clip(message.sender, 30):<30} {clip(message.subject, 50)}"
    )


# ----------------------------------------------------------------------------
def clip(value: str, width: int) -> str:
    """Make a display string safe, then truncate it to ``width``."""
    value = safe_line(value)

    if len(value) <= width:
        return value

    return value[: width - 1] + "…"


# ----------------------------------------------------------------------------
def safe_text(value: str) -> str:
    """Neutralize untrusted text -- mail content -- for a terminal.

    Every control character is replaced by its visible ``\\xNN`` escape,
    and every bidi override or isolate by its ``\\uNNNN`` escape, so a
    message cannot recolour, retitle, hyperlink, overprint, or reorder
    the terminal it is shown in. Printable text, tabs, and newlines pass
    as they are. Lone surrogates become U+FFFD, since no stream can
    encode them.
    """
    value = engine.LONE_SURROGATES.sub("\ufffd", value)

    return UNSAFE_CHARACTERS.sub(_escape_character, value)


# ----------------------------------------------------------------------------
def _escape_character(match: re.Match) -> str:
    code = ord(match.group())

    return f"\\x{code:02x}" if code <= 0xFF else f"\\u{code:04x}"


# ----------------------------------------------------------------------------
def safe_line(value: str) -> str:
    """``safe_text`` for a one-line field: whitespace runs, newlines
    included, collapse to one space so a header cannot forge a line."""
    return safe_text(" ".join(value.split()))


# ----------------------------------------------------------------------------
def human_size(size: int) -> str:
    """Render a byte count compactly: 812B, 4.2K, 1.3M."""
    if size < 1024:
        return f"{size}B"

    if size < 1024 * 1024:
        return f"{size / 1024:.1f}K"

    return f"{size / (1024 * 1024):.1f}M"


# ----------------------------------------------------------------------------
def progress_from_args(args):
    """Return the engine's progress callback, or None without --verbose.

    The engine reports protocol progress by calling back rather than
    printing, so the decision to show it -- and the decoration around it --
    is made once, here.
    """
    if not args.verbose:
        return None

    def emit(channel: str, message: str) -> None:
        print(f"[{channel}] {message}")

    return emit


# ----------------------------------------------------------------------------
def connect(config, args, *, sieve: bool = True, imap: bool = False):
    """Open engine sessions with this invocation's progress reporting."""
    return engine.connect(
        config, sieve=sieve, imap=imap, progress=progress_from_args(args)
    )


# ----------------------------------------------------------------------------
def render_event(event) -> None:
    """Print one step of a change as the engine reports it."""
    if isinstance(event, engine.ScriptBackedUp):
        print(f"Backed up current script to {event.path}")

    elif isinstance(event, engine.ScriptUploaded) and event.activated:
        print(f"Uploaded and activated script {event.script!r}")

    elif isinstance(event, engine.ScriptUploaded):
        print(f"Uploaded script {event.script!r}; it was not activated")

    elif isinstance(event, engine.FolderCreated):
        report_folder_creation(event.result)


# ----------------------------------------------------------------------------
def print_activation(plan) -> None:
    """Say when an upload changes, or leaves alone, which script runs.

    Sieve runs one script. Editing another is legitimate, but a user who
    expects the edit to take effect needs telling that it will not -- and
    switching the running script is a change of its own, so it is shown
    before it happens rather than reported after.
    """
    if not plan.activate:
        print(
            f"\nScript {plan.script!r} is not the active script "
            f"({plan.active!r} is), so it is stored but not run. Pass "
            f"--activate to make it the active script."
        )

    elif plan.active is None:
        print(
            f"\nThe account has no active script; {plan.script!r} will "
            f"become the active script."
        )

    elif plan.active != plan.script:
        print(
            f"\nScript {plan.script!r} will become the active script, in "
            f"place of {plan.active!r}."
        )


# ----------------------------------------------------------------------------
def configure(args):
    """Load the config and give it a way to ask for a password.

    ``config.py`` deliberately cannot prompt -- a core module owning a
    terminal interaction is what stops a non-terminal front-end reusing it
    -- so the front-end supplies the prompt. ``getpass`` is imported here,
    at the one place a terminal is assumed.
    """
    from getpass import getpass

    config = load_config(args)
    config.prompter = getpass

    warn_about_inline_password(args)

    return config


# ----------------------------------------------------------------------------
def warn_about_inline_password(args) -> None:
    """Say what ``--password`` costs, the way ``mysql`` does.

    The flag exists because it was asked for, and the warning exists
    because what it gives away is not obvious: an argument is readable in
    the process list by every user on the machine for as long as the
    command runs, and the shell has already written it to history by the
    time mxfilter starts.

    The message names no part of the value.
    """
    if not getattr(args, "password", None):
        return

    warn(
        "a password given on the command line is visible in the process "
        "list to every user on this machine, and your shell has already "
        "saved it to history. Prefer --password-file or "
        "MXROUTE_PASSWORD_FILE."
    )


# ############################################################################
# Engine inputs from parsed args
# ############################################################################


# ----------------------------------------------------------------------------
def criteria_from_args(args) -> Criteria:
    """Build the Criteria model from the shared criteria flags."""
    criteria = Criteria(match=args.match, compare=args.compare)

    for header, values in (
        ("From", getattr(args, "from_addr", None)),
        ("To", args.to),
        ("Cc", args.cc),
        ("Subject", args.subject),
        ("List-Id", args.list_id),
    ):
        for value in values or []:
            criteria.add(header, value)

    for item in args.header or []:
        if "=" not in item:
            raise MxFilterError(f"--header expects NAME=VALUE, got {item!r}")

        name, value = item.split("=", 1)
        criteria.add(name, value)

    return criteria


# ----------------------------------------------------------------------------
def actions_from_args(args) -> ActionSpec:
    """Fold the action flags into the engine's ActionSpec.

    ``--mark-read`` is ``\\Seen`` and comes first; ``--flag`` values follow
    in the order given, without duplicates.
    """
    flags = ["\\Seen"] if args.mark_read else []

    for flag in args.flag or []:
        if flag not in flags:
            flags.append(flag)

    return ActionSpec(
        fileinto=args.fileinto,
        discard=args.discard,
        flags=tuple(flags),
        keep=args.keep,
        stop=not args.no_stop,
    )


# ----------------------------------------------------------------------------
def placement_from_args(args) -> Placement | None:
    """Fold the four placement flags into one value, or None if none given.

    argparse has already refused more than one of them, so the order of the
    branches only decides which attribute is read first, never which flag
    wins. None means no flag was given, which ``resolve_position`` reads as
    "append a new rule, leave an existing one alone".
    """
    if getattr(args, "place_first", False):
        return Placement(PLACE_FIRST)

    elif getattr(args, "place_last", False):
        return Placement(PLACE_LAST)

    elif getattr(args, "place_before", None):
        return Placement(PLACE_BEFORE, args.place_before)

    elif getattr(args, "place_after", None):
        return Placement(PLACE_AFTER, args.place_after)

    return None


# ----------------------------------------------------------------------------
def reject_forbidden(args) -> None:
    """Hand every refused action flag that was given to the engine."""
    engine.reject_actions(
        name for name in REFUSED_ACTION_FLAGS if getattr(args, name, None)
    )


# ############################################################################
# Rendering plans and results
# ############################################################################


# ----------------------------------------------------------------------------
def show_folder_plan(plan: engine.FolderPlan) -> None:
    """Say where filed mail goes, before anything is created."""
    if plan.status == engine.FOLDER_NONE:
        return

    if plan.delimiter_assumed:
        # Without a folder list the Maildir++ heuristic is the best that
        # can be done; --no-imap is opt-in precisely for this trade.
        warn(
            f"no IMAP connection: assuming delimiter {plan.delimiter!r}, "
            f"target folder {plan.folder!r}"
        )

    elif plan.requested != plan.folder:
        print(
            f"Folder {plan.requested!r} resolves to {plan.folder!r} "
            f"(delimiter {plan.delimiter!r})"
        )


# ----------------------------------------------------------------------------
def prepare_folder(sessions, config, args) -> engine.FolderPlan:
    """Plan the rule's target folder, say what it is, and settle it."""
    plan = engine.plan_folder(
        sessions,
        config,
        args.fileinto,
        create=args.create_folder,
        subscribe=not args.no_subscribe,
        delimiter=args.delimiter,
    )

    show_folder_plan(plan)
    settle_folder(sessions, plan, args)

    return plan


# ----------------------------------------------------------------------------
def settle_folder(sessions, plan: engine.FolderPlan, args) -> None:
    """Report how the target folder comes to exist, creating it if due.

    Nothing is created here. A folder due for IMAP creation is announced
    now and created by the engine's execute step, after the change has
    been shown and decided on -- so a dry run, an abort, or a rejected
    plan leaves no stray folder behind.
    """
    engine.check_folder(plan)

    if plan.status == engine.FOLDER_MISSING:
        warn(
            f"target folder {plan.folder!r} does not exist. Mail filed there "
            f"may be lost; pass --create-folder to create it."
        )

    elif plan.status == engine.FOLDER_SIEVE_CREATES:
        print(
            f"Folder {plan.folder!r} will be created by Sieve "
            f"(fileinto :create)"
        )

        if plan.subscribe:
            # Sieve creates the folder at delivery time, when mxfilter is
            # not running and cannot subscribe to it. Whether the server
            # does so itself is genuinely unknown -- RFC 5490 says :create
            # creates the mailbox and says nothing about subscription, and
            # this account has never been observed doing it either way.
            #
            # So the wording claims only the absence of a promise, not that
            # it will not happen. Asserting the stronger version would be
            # inventing a fact about the server, which is the failure
            # CONVENTIONS.md 'Confidence' exists to prevent. Issue #40 is
            # where that gets settled; when it does, replace this line with
            # the real behaviour rather than leaving a caution that never
            # resolves.
            print(
                "  Nothing promises Sieve will subscribe to a folder it "
                "creates, so it may not appear in webmail until you "
                f"subscribe to it: once the first message has created it, "
                f"run 'mxfilter subscribe {plan.folder}'."
            )

    elif plan.status == engine.FOLDER_IMAP_CREATE:
        announce_folder_creation(plan, args.dry_run)


# ----------------------------------------------------------------------------
def announce_folder_creation(plan: engine.FolderPlan, dry_run: bool) -> None:
    """Say that the target folder will be made over IMAP, before it is."""
    if dry_run:
        print(f"[dry-run] would create IMAP folder {plan.folder!r}")

    else:
        print(
            f"Folder {plan.folder!r} does not exist; it will be created "
            f"over IMAP when the change is applied"
        )


# ----------------------------------------------------------------------------
def report_folder_creation(result: FolderCreation) -> None:
    """Say what creating the folder achieved, including what it did not.

    Every branch here says something. An unsubscribed folder is invisible
    in webmail whether that was asked for or not, and the whole failure
    being fixed is that the invisibility arrived silently -- so an opt-out
    that printed nothing would reproduce the bug for whoever passes the
    flag without knowing what it costs.
    """
    folder = result.folder

    if result.subscribed:
        print(f"Created IMAP folder {folder!r} and subscribed to it")

        return

    if result.subscribe_error:
        warn(
            f"created folder {folder!r}, but subscribing to it failed: "
            f"{result.subscribe_error}. The folder exists and mail filed "
            f"there will arrive, but it will not appear in webmail until "
            f"you subscribe to it: run 'mxfilter subscribe {folder}'."
        )

        return

    print(
        f"Created IMAP folder {folder!r}; not subscribed (--no-subscribe), "
        f"so it will not appear in webmail ('mxfilter subscribe {folder}' "
        f"shows it later)."
    )


# ----------------------------------------------------------------------------
def print_script_diff(report: DisplayDiff) -> None:
    """Show the diff, and say what the diff itself is not showing.

    Both sides are rendered in mxfilter's own formatting so the rule
    change is legible rather than buried under the renderer's layout. The
    cost of that is a reformat the reader can no longer see in the diff,
    and hiding it silently would be a worse trade than the noise it
    removes -- so it is reported instead.

    The note only appears while the server's copy is in some other
    formatting, which stops being true from the first upload on. A note
    that never went away would be read as boilerplate and stop being read
    at all.
    """
    if report.reformats:
        print(
            "\nNote: the script on the server is not in mxfilter's "
            "formatting, so uploading re-indents the whole file. The diff "
            "below shows only the rule change; no rule body is altered."
        )

    print("\n--- sieve diff ---")
    # A rule built from a message carries that message's text, and a stored
    # script can hold any bytes: both are untrusted by the time they print.
    print(safe_text(report.text) if report.text.strip() else "(no change)")
    print("--- end diff ---")


# ----------------------------------------------------------------------------
def warn_missing_extensions(missing: list[str]) -> None:
    """Warn about extensions the rule needs but the server does not list."""
    if missing:
        warn(
            f"the server does not advertise: {', '.join(missing)}. The "
            f"upload will be validated with CHECKSCRIPT and may be rejected."
        )


# ----------------------------------------------------------------------------
def print_placement(analysis) -> None:
    """Warn when a rule about to be added would be dead, or would starve.

    The engine judges the rule at the position it will actually occupy,
    so inserting ahead of rules that already work -- the case that starves
    them -- is reported rather than missed.
    """
    print_findings(analysis.dead_on_arrival, "\nBefore this rule is reached:")
    print_findings(analysis.starves, "\nThis rule would come before:")


# ############################################################################
# The existing-mail pass
# ############################################################################


# ----------------------------------------------------------------------------
def apply_to_existing(
    sessions, criteria: Criteria, args, spec: ActionSpec, folder
) -> int:
    """Plan the existing-mail pass, show it, and run it if allowed.

    Planning is read-only, so the plan is always built first and shown
    whatever the flags say. ``--dry-run`` is simply the path that stops
    after showing it -- the decision to execute lives here, in the
    front-end, and never inside the engine.
    """
    source = engine.source_folder(sessions, args.folder)

    if engine.mail_pass_is_noop(spec, source, folder.folder):
        print(
            f"\nSkipping the existing-mail pass: the rule leaves matching "
            f"mail in {source!r} as it is, so there is nothing to do."
        )

        return 0

    print(f"\nSearching {source!r} for existing matches...")

    plan = engine.plan_mail(sessions, criteria, spec, source, folder.folder)

    if plan.is_empty:
        print("No existing messages match.")

        if not args.dry_run and engine.folder_pending(sessions, folder):
            print(
                f"Folder {folder.folder!r} was not created: there is "
                f"nothing to move into it."
            )

        return 0

    print(f"{plan.count} message(s) match:")
    print_preview(plan.messages)

    if args.dry_run:
        describe_plan(plan, prefix="[dry-run] would ")

        if plan.count > args.max_messages:
            print(
                f"[dry-run] note: {plan.count} matches exceed "
                f"--max-messages {args.max_messages}; a real run would stop "
                f"and ask."
            )

        return 0

    engine.check_message_cap(plan, args.max_messages)

    if not confirm(action_prompt(plan, args.move_threshold), args.yes):
        print("Aborted; no messages were touched.")

        return 0

    result = engine.execute_mail(
        sessions, plan, args.max_messages, folder, render_event
    )

    report_result(result, plan)

    return result.moved or result.deleted or result.flagged


# ----------------------------------------------------------------------------
def describe_plan(plan, prefix: str = "") -> None:
    """Print what a plan does, in the caller's tense."""
    if plan.flags:
        print(f"{prefix}flag {plan.count} message(s): {', '.join(plan.flags)}")

    if plan.discard:
        print(
            f"{prefix}PERMANENTLY DELETE {plan.count} message(s) from "
            f"{plan.source!r}"
        )

    elif plan.moves:
        print(f"{prefix}move {plan.count} message(s) to {plan.destination!r}")


# ----------------------------------------------------------------------------
def report_result(result, plan) -> None:
    """Print what actually happened."""
    if result.flagged:
        print(
            f"Flagged {result.flagged} message(s) with {', '.join(plan.flags)}"
        )

    if result.deleted:
        print(f"Deleted {result.deleted} message(s) from {plan.source!r}")

    if result.moved:
        print(
            f"Moved {result.moved} message(s) from {plan.source!r} to "
            f"{plan.destination!r}"
        )


# ----------------------------------------------------------------------------
def action_prompt(plan, move_threshold: int) -> str:
    """Word the confirmation according to how reversible the action is.

    A MOVE is recoverable: the mail still exists, in another folder, and can
    be moved back. An EXPUNGE is not -- once the server drops it there is
    nothing to undo -- so deletion says so in as many words, and a large
    move says how large.
    """
    if plan.discard:
        return (
            f"PERMANENTLY DELETE {plan.count} message(s) from "
            f"{plan.source!r}? This cannot be undone"
        )

    if not plan.moves:
        return f"Flag {plan.count} message(s) in {plan.source!r}?"

    if plan.count > move_threshold:
        return (
            f"Move {plan.count} message(s) -- more than --move-threshold "
            f"{move_threshold} -- from {plan.source!r} to "
            f"{plan.destination!r}? (reversible: they can be moved back)"
        )

    return (
        f"Move {plan.count} message(s) from {plan.source!r} to "
        f"{plan.destination!r}?"
    )


# ############################################################################
# Subcommands
# ############################################################################


# ----------------------------------------------------------------------------
def cmd_list(args) -> int:
    """List the account's Sieve scripts."""
    config = configure(args)

    with connect(config, args) as sessions:
        active, others = engine.list_scripts(sessions)

        if not active and not others:
            print("No Sieve scripts on the server.")

            return 0

        if active:
            print(f"* {active}   (active)")

        for name in others:
            print(f"  {name}")

    return 0


# ----------------------------------------------------------------------------
def cmd_show(args) -> int:
    """Print a script's source."""
    config = configure(args)

    with connect(config, args) as sessions:
        script = engine.read_script(sessions, args.name)
        source = safe_text(script.source)

        print(f"# ---- {safe_line(script.name)} ----")
        print(source, end="" if source.endswith("\n") else "\n")

        names = [safe_line(name) for name in script.rule_names()]

        print(f"# ---- {len(names)} rule(s): {', '.join(names) or '(none)'}")

    return 0


# ----------------------------------------------------------------------------
def describe_rule_condition(rule) -> str:
    """Render a rule's tests as one line, in evaluation terms."""
    if not rule.tests and rule.unmodelled:
        return f"({', '.join(rule.unmodelled)} -- not modelled)"

    joiner = " OR " if rule.combinator == "anyof" else " AND "

    parts = [
        f"{test.header} {test.match_type} "
        f"{' | '.join(repr(key) for key in test.keys)}"
        for test in rule.tests
    ]

    line = joiner.join(parts) or "(no condition)"

    if rule.unmodelled:
        line += f"  [+ {', '.join(rule.unmodelled)}: not modelled]"

    return line


# ----------------------------------------------------------------------------
def print_rules(rules) -> None:
    """Print the rules in the order the server evaluates them.

    Order and ``stop`` are the two facts a diff cannot show, so both are
    given their own column rather than left to be read out of the source.
    """
    if not rules:
        print("The active script has no rules.")

        return

    print(f"{len(rules)} rule(s), in evaluation order:\n")

    for rule in rules:
        marker = "  [stop]" if rule.stops else ""

        actions = safe_line(", ".join(rule.actions))

        print(f"  {rule.index + 1}. {safe_line(rule.name)}{marker}")
        print(f"       when:  {describe_rule_condition(rule)}")
        print(f"       then:  {actions or '(nothing)'}")
        print()


# ----------------------------------------------------------------------------
def print_findings(findings, heading: str) -> None:
    """Print shadow findings, keeping decided ones apart from suspected.

    The marker is the whole point: ``!`` is a fact the reader should act
    on, ``?`` is something to look at. Rendering them the same way would
    throw away the distinction the analysis works to preserve.
    """
    if not findings:
        return

    print(heading)

    for finding in findings:
        mark = "!" if finding.certainty == CERTAIN else "?"
        verb = (
            "never runs" if finding.certainty == CERTAIN else "may never run"
        )

        print(f"  {mark} {finding.narrow!r} {verb} -- {finding.reason}")

    print()


# ----------------------------------------------------------------------------
def cmd_rules(args) -> int:
    """Show the rules already in the active script, and audit their order."""
    config = configure(args)

    with connect(config, args) as sessions:
        report = engine.read_rules(sessions, args.script)

        print(f"Script {report.script!r}:\n")
        print_rules(report.rules)

        if report.findings:
            print_findings(
                report.findings, "Rules that cannot fire where they are:"
            )

        elif report.rules:
            print("No rule is shadowed by an earlier one.")

    # Reading is the whole command, so a finding is information rather than
    # a failure. Exiting non-zero here would make the audit unusable in any
    # pipeline that treats a non-zero exit as an error.
    return 0


# ----------------------------------------------------------------------------
def cmd_backup(args) -> int:
    """Save the active script to a file, exactly as the server has it."""
    config = configure(args)

    with connect(config, args) as sessions:
        plan = engine.plan_backup(sessions, config, args.output)

        if args.dry_run:
            print(
                f"[dry-run] would write {rule_count_phrase(plan.source)} to "
                f"{plan.target}"
            )

            return 0

        # Written before the script is parsed: counting its rules is a
        # nicety, and a script too broken to parse is exactly the one worth
        # having a copy of.
        target = engine.execute_backup(plan)

    print(f"wrote {rule_count_phrase(plan.source)} to {target}")

    return 0


# ----------------------------------------------------------------------------
def cmd_restore(args) -> int:
    """Replace the active script with a backup file, after showing it."""
    config = configure(args)

    with connect(config, args) as sessions:
        plan = engine.plan_restore(sessions, args.file)

        print(
            f"Restore {plan.source} ({rule_count_phrase(plan.after)}) over "
            f"script {plan.script!r} ({rule_count_phrase(plan.before)}):"
        )

        if not plan.changes:
            print(
                "\nThe file is identical to the script on the server; "
                "nothing to restore."
            )

            return 0

        print_script_diff(plan.diff)

        if args.dry_run:
            print("\n[dry-run] the script was NOT uploaded.")

            return 0

        if not confirm(
            f"Replace script {plan.script!r} with {str(plan.source)!r}? The "
            f"current script is backed up first",
            args.yes,
        ):
            print("Aborted; nothing was changed.")

            return 0

        engine.execute_restore(sessions, config, plan, render_event)

    return 0


# ----------------------------------------------------------------------------
def rule_count_phrase(source: str) -> str:
    """Describe how many rules a script holds, for the summary line."""
    count = engine.count_rules(source)

    if count is None:
        return "a script mxfilter could not parse"

    return f"{count} rule(s)"


# ----------------------------------------------------------------------------
def cmd_folders(args) -> int:
    """List IMAP folders and the detected hierarchy delimiter."""
    config = configure(args)

    with connect(config, args, sieve=False, imap=True) as sessions:
        listing = engine.list_folders(sessions)

        print(f"Hierarchy delimiter: {listing.delimiter!r}")
        print(
            f"{len(listing.folders)} folder(s), "
            f"{len(listing.folders) - len(listing.unsubscribed)} subscribed "
            f"(webmail shows only subscribed folders):"
        )

        width = max((len(name) for name in listing.folders), default=0)

        for folder in listing.folders:
            if listing.is_subscribed(folder):
                print(f"  {folder}")

            else:
                print(f"  {folder:<{width}}  (not subscribed)")

    return 0


# ----------------------------------------------------------------------------
def cmd_subscribe(args) -> int:
    """Subscribe to, or unsubscribe from, an existing folder."""
    config = configure(args)
    subscribe = args.command == "subscribe"

    with connect(config, args, sieve=False, imap=True) as sessions:
        plan = engine.plan_subscription(sessions, args.folder, subscribe)

        if plan.requested != plan.folder:
            print(
                f"Folder {plan.requested!r} resolves to {plan.folder!r} "
                f"(delimiter {plan.delimiter!r})"
            )

        if not plan.changes:
            state = "subscribed" if subscribe else "not subscribed"
            print(f"{plan.folder!r} is already {state}; nothing to change.")

            return 0

        verb = "subscribe to" if subscribe else "unsubscribe from"

        if args.dry_run:
            print(f"[dry-run] would {verb} {plan.folder!r}")

            return 0

        engine.execute_subscription(sessions, plan)

    if subscribe:
        print(f"Subscribed to {plan.folder!r}; webmail will show it.")

    else:
        print(
            f"Unsubscribed from {plan.folder!r}. It still exists, keeps its "
            f"mail, and still receives anything filed there, but webmail "
            f"will not show it."
        )

    return 0


# ----------------------------------------------------------------------------
def cmd_test(args) -> int:
    """Connect to both services and report what they support."""
    config = configure(args)

    print(f"Sources:   {', '.join(s.describe() for s in config.consulted)}")
    print(f"Host:      {config.host}  ({origin_of(config, 'host')})")
    print(f"User:      {config.user}  ({origin_of(config, 'user')})")
    print(
        f"Password:  {config.password_state()}{password_origin_suffix(config)}"
    )
    print(
        f"IMAP:      {config.imap_host}:{config.imap_port}  "
        f"({origin_of(config, host='imap_host', port='imap_port')})"
    )
    print(
        f"Sieve:     {config.host}:{config.sieve_port} "
        f"(tls={config.sieve_tls})  "
        f"({origin_of(config, port='sieve_port', tls='sieve_tls')})"
    )

    with connect(config, args) as sessions:
        sieve = engine.probe_sieve(sessions)

        extensions = ", ".join(sorted(sieve.capabilities)) or "(none)"

        print("\nManageSieve: connected")
        print(f"  extensions: {extensions}")

        # Every line below is read from this server's CAPABILITY response.
        # Nothing here asserts what MXRoute does or does not enable -- only
        # 'redirect' is a documented MXRoute policy, and a policy is not a
        # capability, so it would not show up here at all.
        advertised = {name.lower() for name in sieve.capabilities}

        print("\n  advertised extensions (from this server, not assumed):")

        for name in REPORTABLE_EXTENSIONS:
            state = "yes" if name in advertised else "not advertised"
            print(f"    {name:<12} {state}")

        print(f"\n  active script: {sieve.active or '(none)'}")
        print(f"  other scripts: {', '.join(sieve.others) or '(none)'}")
        print(
            "  (mxfilter always edits the ACTIVE script under its own "
            "name; it never guesses one.)"
        )

    with connect(config, args, sieve=False, imap=True) as sessions:
        imap = engine.probe_imap(sessions)

        print("\nIMAP: connected")
        print(f"  delimiter: {imap.delimiter!r}")
        print(
            f"  folders:   {imap.folder_count} "
            f"({imap.folder_count - len(imap.unsubscribed)} subscribed)"
        )

        if imap.unsubscribed:
            print(
                f"  not subscribed (exist, but webmail will not show them): "
                f"{', '.join(imap.unsubscribed)}"
            )
        print(
            f"  MOVE:      {'yes' if imap.has_move else 'no (COPY+EXPUNGE)'}"
        )
        print(f"  UIDPLUS:   {'yes' if imap.has_uidplus else 'no'}")

        report_filter_sieve(imap.has_filter_sieve)

    print(
        "\nNote: MXRoute disables the Sieve 'redirect' action as a matter "
        "of policy (2024-03-21) -- use a panel forwarder, which handles "
        "SRS properly. That is the only MXRoute restriction mxfilter "
        "asserts; everything else above came from the server."
    )

    return 0


# ----------------------------------------------------------------------------
def origin_of(config, *names: str, **labelled: str) -> str:
    """Say where one or more settings came from.

    One source for all of them is said once; differing sources are said
    per setting, under the label given (``port=`` / ``tls=``).
    """
    pairs = [(name, name) for name in names] + list(labelled.items())
    described = [
        (label, source.describe() if source else "unknown")
        for label, name in pairs
        for source in [config.sources.get(name)]
    ]

    if len({text for _label, text in described}) == 1:
        return described[0][1]

    return ", ".join(f"{label}: {text}" for label, text in described)


# ----------------------------------------------------------------------------
def password_origin_suffix(config) -> str:
    """Where the password comes from, with no part of the password.

    The variable or key is named alongside the file it was in, since
    three of them can supply a password from one env file or environment.
    """
    origin = config.password_origin()

    if origin is None:
        return ""

    if origin.kind in (ENV_FILE, ENVIRONMENT, CONFIG_FILE):
        return f"  ({origin.name}, {origin.describe()})"

    return f"  ({origin.describe()})"


# ----------------------------------------------------------------------------
def report_filter_sieve(present: bool) -> None:
    """Report whether the server can run Sieve retroactively itself.

    Dovecot's Pigeonhole ``imap_filter_sieve`` plugin advertises
    ``FILTER=SIEVE``, which lets a client ask the *server* to run a Sieve
    script over messages matching an IMAP search -- the retroactive pass
    done properly, server-side. It is experimental and off by default, so
    it is almost certainly absent here; one CAPABILITY line settles it
    either way, and an answer on the record beats an assumption.
    """
    if present:
        print(
            "  FILTER=SIEVE: yes -- this server can apply a Sieve script "
            "to existing mail itself."
        )
        print(
            "                mxfilter still uses its own client-side pass; "
            "the server-side path is not implemented."
        )

    else:
        print(
            "  FILTER=SIEVE: no -- no server-side retroactive filtering "
            "(Dovecot imap_filter_sieve is not enabled)."
        )
        print(
            "                mxfilter's client-side search-and-move pass "
            "is the only option here."
        )


# ----------------------------------------------------------------------------
def cmd_add(args) -> int:
    """Add a rule to the active script, then apply it to existing mail."""
    reject_forbidden(args)

    config = configure(args)
    criteria = criteria_from_args(args)
    criteria.require_terms()

    return run_add(config, args, criteria)


# ----------------------------------------------------------------------------
def run_add(config, args, criteria: Criteria) -> int:
    """Shared body of ``add`` and ``from-message``."""
    spec = actions_from_args(args)

    with connect(config, args, imap=not args.no_imap) as sessions:
        folder = prepare_folder(sessions, config, args)

        warn_missing_extensions(
            engine.missing_extensions(sessions, spec, folder)
        )

        plan = engine.plan_rule(
            sessions,
            RuleRequest(
                criteria=criteria,
                actions=spec,
                name=args.name,
                script=args.script,
                replace=args.replace,
                placement=placement_from_args(args),
                activate=args.activate,
            ),
            folder,
        )

        print(f"\nRule {plan.name!r} on script {plan.script!r}:")
        print(f"  when:  {criteria.describe()}")
        print(f"  then:  {describe_actions(plan.actions)}")

        # The placement findings come before the diff. A diff shows what
        # changes; it cannot show that the change lands after a rule whose
        # stop means it will never be reached.
        print_placement(plan.placement)
        print_script_diff(plan.diff)
        print_activation(plan)

        if args.dry_run:
            print("\n[dry-run] the script was NOT uploaded.")

        else:
            engine.execute_script_change(sessions, config, plan, render_event)

        if sessions.imap is None or args.no_apply:
            if args.no_apply:
                print("\nSkipping the existing-mail pass (--no-apply).")

            return 0

        apply_to_existing(sessions, criteria, args, spec, folder)

    return 0


# ----------------------------------------------------------------------------
def describe_actions(actions: list[tuple]) -> str:
    """Render action tuples as a readable summary line.

    Sieve escaping is undone for display: the summary should say
    ``addflag \\Seen``, which is the flag the user asked for, rather than
    the ``\\\\Seen`` that has to appear in the script source. The diff
    printed underneath shows the real source, so nothing is hidden.
    """
    return "; ".join(
        " ".join(unescape_sieve_string(str(part)) for part in action)
        for action in actions
    )


# ----------------------------------------------------------------------------
def unescape_sieve_string(value: str) -> str:
    """Reverse ``escape_sieve_string`` for display purposes only."""
    return value.replace('\\"', '"').replace("\\\\", "\\")


# ----------------------------------------------------------------------------
def cmd_apply(args) -> int:
    """Apply criteria to existing mail only; touch no Sieve script."""
    reject_forbidden(args)

    config = configure(args)
    criteria = criteria_from_args(args)
    criteria.require_terms()
    spec = actions_from_args(args)

    with connect(config, args, sieve=False, imap=True) as sessions:
        folder = engine.plan_folder(
            sessions,
            config,
            args.fileinto,
            create=args.create_folder,
            subscribe=not args.no_subscribe,
        )

        show_folder_plan(folder)
        engine.require_mail_action(folder, spec)

        if folder.status == engine.FOLDER_MISSING:
            raise MxFilterError(
                f"target folder {folder.folder!r} does not exist; pass "
                f"--create-folder to create it"
            )

        if folder.status == engine.FOLDER_IMAP_CREATE:
            announce_folder_creation(folder, args.dry_run)

        print(f"Criteria: {criteria.describe()}")

        apply_to_existing(sessions, criteria, args, spec, folder)

    return 0


# ----------------------------------------------------------------------------
def cmd_remove_rule(args) -> int:
    """Remove a named rule from the active script and re-upload."""
    config = configure(args)

    with connect(config, args) as sessions:
        plan = engine.plan_removal(
            sessions, args.rule_name, args.script, args.activate
        )

        print_script_diff(plan.diff)
        print_activation(plan)

        if args.dry_run:
            print("\n[dry-run] the script was NOT uploaded.")

            return 0

        if not confirm(
            f"Remove rule {plan.rule!r} from {plan.script!r}?", args.yes
        ):
            print("Aborted; nothing was changed.")

            return 0

        engine.execute_script_change(sessions, config, plan, render_event)

    return 0


# ----------------------------------------------------------------------------
def cmd_move_rule(args) -> int:
    """Move a named rule to a new position, leaving it otherwise unchanged."""
    config = configure(args)

    with connect(config, args) as sessions:
        plan = engine.plan_move(
            sessions,
            args.rule_name,
            placement_from_args(args),
            args.script,
            args.activate,
        )

        if not plan.changes:
            print(
                f"Rule {plan.rule!r} is already at position "
                f"{plan.to_index + 1} of {plan.count} in {plan.script!r}; "
                f"nothing to change."
            )

            return 0

        print(
            f"Move rule {plan.rule!r} in script {plan.script!r}: position "
            f"{plan.from_index + 1} -> {plan.to_index + 1} of {plan.count}"
        )

        print_placement(plan.placement)
        print_script_diff(plan.diff)
        print_activation(plan)

        if args.dry_run:
            print("\n[dry-run] the script was NOT uploaded.")

            return 0

        if not confirm(
            f"Move rule {plan.rule!r} to position {plan.to_index + 1} in "
            f"{plan.script!r}?",
            args.yes,
        ):
            print("Aborted; nothing was changed.")

            return 0

        engine.execute_script_change(sessions, config, plan, render_event)

    return 0


# ----------------------------------------------------------------------------
def print_message(message, uid: int, folder: str) -> None:
    """Show the message a rule is about to be derived from.

    The UID is dug out of webmail by hand, so a mistyped digit otherwise
    derives a filter from the wrong message -- and then moves mail on it.
    Printing the derived criteria alone cannot catch that: criteria read as
    perfectly plausible whichever message they came from, so the only
    check is showing the message itself.

    A header that is not there is not printed. An absent To or List-Id is
    ordinary, and a row reading "(none)" would add noise to the block whose
    whole job is to be scanned quickly.
    """
    print(f"Message uid {uid} in {folder!r}:")

    for header in IDENTIFYING_HEADERS:
        raw = message.get(header)

        if not raw:
            continue

        value = clip(decode_header_value(raw), HEADER_WIDTH)

        print(f"  {header + ':':<9}{value}")


# ----------------------------------------------------------------------------
def cmd_from_message(args) -> int:
    """Derive criteria from an existing message, then behave like ``add``."""
    reject_forbidden(args)

    config = configure(args)

    if not args.uid and not args.search:
        raise MxFilterError("give either --uid N or --search EXPRESSION")

    with connect(config, args, sieve=False, imap=True) as sessions:
        picked = engine.pick_message(
            sessions, args.folder, uid=args.uid, search=args.search
        )

        if picked.candidates > 1:
            warn(
                f"{picked.candidates} messages matched; using the most "
                f"recent (uid {picked.uid})"
            )

    # Shown before the criteria, and before anything is derived, because
    # this is the answer to "did I pick the right email?" -- the question
    # the criteria below cannot answer.
    print_message(picked.headers, picked.uid, args.folder)

    derived = engine.derive_criteria(
        picked.headers, args.derive, args.match, args.compare
    )

    for header in derived.skipped:
        warn(f"message has no {header!r} header; skipping it")

    criteria = derived.criteria
    criteria.require_terms()

    print("\nDerived criteria:")
    print(f"  {criteria.describe()}")

    return run_add(config, args, criteria)


# ----------------------------------------------------------------------------
def status_marks(message) -> str:
    """The listing's one-letter marks for a message; see MARK_LEGEND."""
    flags = {flag.lower() for flag in message.flags}

    marks = "" if "\\seen" in flags else "N"
    marks += "".join(mark for flag, mark in FLAG_MARKS if flag in flags)

    return marks + ("@" if message.has_attachments else "")


# ----------------------------------------------------------------------------
def cmd_messages(args) -> int:
    """List the newest messages in a folder, optionally filtered."""
    config = configure(args)
    criteria = criteria_from_args(args)

    with connect(config, args, sieve=False, imap=True) as sessions:
        listing = engine.list_messages(
            sessions,
            args.folder,
            criteria=criteria,
            search=args.search,
            limit=args.limit,
        )

    if not listing.messages:
        print(f"No messages found in {safe_line(listing.folder)!r}.")

        return 0

    print(
        f"{len(listing.messages)} message(s) in "
        f"{safe_line(listing.folder)!r}, newest first:"
    )
    print(
        f"{'UID':>8}  {'Received':<19}  {'Size':>6}  {'Mark':<4}  "
        f"{'From':<28}  Subject"
    )

    rows = [(message, status_marks(message)) for message in listing.messages]

    for message, marks in rows:
        print(
            f"{message.uid:>8}  {message.date:<19}  "
            f"{human_size(message.size):>6}  {marks:<4}  "
            f"{clip(message.sender, 28):<28}  {clip(message.subject, 60)}"
        )

    if any(marks for _, marks in rows):
        print(f"Marks: {MARK_LEGEND}")

    if listing.more:
        print(
            f"Showing the {len(listing.messages)} newest; there may be "
            f"more -- raise --limit to see them."
        )

    return 0


# ----------------------------------------------------------------------------
def cmd_view(args) -> int:
    """Show one message: headers, text body, and what is attached."""
    config = configure(args)

    with connect(config, args, sieve=False, imap=True) as sessions:
        content = engine.read_message(sessions, args.folder, args.uid)

    if args.raw and not sys.stdout.isatty():
        # Nothing draws a pipe or a file, so hand over the exact bytes:
        # 'view N --raw > msg.eml' is the message, 8-bit parts included.
        sys.stdout.flush()
        sys.stdout.buffer.write(content.source)
        sys.stdout.buffer.flush()

        return 0

    if args.raw:
        source = safe_text(content.source.decode("utf-8", errors="replace"))

        print(source, end="" if source.endswith("\n") else "\n")

        return 0

    if args.headers_only:
        for name, value in content.headers:
            print(f"{safe_line(name)}: {safe_line(value)}")

        return 0

    flags = " ".join(content.flags) or "no flags"

    print(
        f"Message uid {content.uid} in {safe_line(content.folder)!r} "
        f"({human_size(content.size)}; {safe_line(flags)}):"
    )

    for header in VIEW_HEADERS:
        value = content.header(header)

        if value:
            print(f"  {header + ':':<9}{safe_line(value)}")

    print()

    if content.body_from_html:
        print(
            "[No plain-text part; this is a rough conversion of the HTML. "
            "--raw shows the original.]\n"
        )

    body = content.body.rstrip()

    print(safe_text(body) if body else "(no text body)")

    if content.attachments:
        print(f"\nAttachments ({len(content.attachments)}):")

        for item in content.attachments:
            print(
                f"  {safe_line(item.name) or '(unnamed)'}  "
                f"{safe_line(item.content_type)}  {human_size(item.size)}"
            )

    return 0


# ############################################################################
# Argument parsing
# ############################################################################


# ----------------------------------------------------------------------------
def global_parser() -> argparse.ArgumentParser:
    """Flags accepted both before and after the subcommand.

    ``--verbose mxfilter add`` and ``mxfilter add --verbose`` should both
    work; people type the second. ``SUPPRESS`` is what makes that safe --
    without it the subparser's default would overwrite a value already set
    by the top-level parser, so passing the flag first would silently do
    nothing.
    """
    parser = argparse.ArgumentParser(add_help=False)

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=argparse.SUPPRESS,
        help="step-by-step progress",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=argparse.SUPPRESS,
        help="show a full traceback on failure (never prints credentials)",
    )

    return parser


# ----------------------------------------------------------------------------
def connection_parser() -> argparse.ArgumentParser:
    """Flags shared by every subcommand that connects to the server."""
    parser = argparse.ArgumentParser(add_help=False)

    group = parser.add_argument_group("connection")
    group.add_argument("--host", help="MXRoute server hostname")
    group.add_argument("--user", help="full email address (the username)")

    # One credential, given one way. Three equally explicit instructions
    # about where the password comes from have no natural ranking, so
    # argparse rejects a second one instead of the tool silently picking a
    # winner the user would have to know the order to predict.
    credential = group.add_mutually_exclusive_group()
    credential.add_argument(
        "--password-file",
        dest="password_file",
        metavar="PATH",
        help="file whose contents are the password; must be mode 0600 "
        "(or 0400) and on a Linux filesystem",
    )
    credential.add_argument(
        "--password-cmd",
        dest="password_cmd",
        help="command whose stdout is the password (e.g. 'pass show mail')",
    )
    credential.add_argument(
        "-p",
        "--password",
        help="the password itself; least safe -- visible in the process "
        "list and saved to shell history",
    )
    group.add_argument(
        "--env-file",
        dest="env_file",
        nargs="?",
        const=".env",
        metavar="PATH",
        help="read MXROUTE_* settings from a dotenv-style file (default "
        ".env in the current directory); they beat the environment and "
        "the config file, and lose to a flag. A file setting "
        "MXROUTE_PASSWORD must be mode 0600 (or 0400)",
    )
    group.add_argument("--imap-host", dest="imap_host")
    group.add_argument("--imap-port", dest="imap_port", type=int)
    group.add_argument("--sieve-port", dest="sieve_port", type=int)
    group.add_argument(
        "--sieve-tls", dest="sieve_tls", choices=SIEVE_TLS_MODES
    )
    group.add_argument(
        "--backup-dir",
        dest="backup_dir",
        help="where script backups are written, both the automatic "
        "pre-upload one and 'mxfilter backup'; default "
        "$XDG_CONFIG_HOME/mxfilter/backups",
    )

    return parser


# ----------------------------------------------------------------------------
def criteria_parser() -> argparse.ArgumentParser:
    """The criteria flags shared by add / apply / from-message."""
    parser = argparse.ArgumentParser(add_help=False)

    group = parser.add_argument_group("criteria")
    group.add_argument(
        "--from", dest="from_addr", action="append", metavar="VALUE"
    )
    group.add_argument("--to", action="append", metavar="VALUE")
    group.add_argument("--cc", action="append", metavar="VALUE")
    group.add_argument("--subject", action="append", metavar="VALUE")
    group.add_argument(
        "--list-id", dest="list_id", action="append", metavar="VALUE"
    )
    group.add_argument(
        "--header",
        action="append",
        metavar="NAME=VALUE",
        help="match an arbitrary header; repeatable",
    )
    group.add_argument(
        "--match",
        choices=MATCH_MODES,
        default="any",
        help="combine criteria with OR (any) or AND (all); default any",
    )
    group.add_argument(
        "--compare",
        choices=COMPARE_OPS,
        default="contains",
        help="comparison used for every criterion; default contains. "
        "Note that 'is' and 'matches' test the WHOLE header value, as "
        "Sieve does -- so --compare matches --from '*@list.org' will "
        "not match 'Name <a@list.org>'; write '*@list.org*'",
    )

    return parser


# ----------------------------------------------------------------------------
def action_parser() -> argparse.ArgumentParser:
    """The action flags shared by add / apply / from-message."""
    parser = argparse.ArgumentParser(add_help=False)

    group = parser.add_argument_group("actions")
    group.add_argument("--fileinto", metavar="FOLDER")
    group.add_argument("--discard", action="store_true")
    group.add_argument(
        "--mark-read",
        dest="mark_read",
        action="store_true",
        help="add the \\Seen flag",
    )
    group.add_argument(
        "--flag",
        action="append",
        metavar="NAME",
        help="add an IMAP flag, e.g. '\\Flagged' or a custom keyword",
    )
    group.add_argument("--keep", action="store_true")
    group.add_argument(
        "--no-stop",
        dest="no_stop",
        action="store_true",
        help="let later rules run too (omit the 'stop' action)",
    )
    group.add_argument(
        "--create-folder",
        dest="create_folder",
        action="store_true",
        help="create the target folder if it does not exist, and subscribe "
        "to it so mail clients show it",
    )
    group.add_argument(
        "--no-subscribe",
        dest="no_subscribe",
        action="store_true",
        help="with --create-folder, create the folder without subscribing "
        "to it. Webmail draws its folder tree from the subscription list, "
        "so the folder will receive mail and stay hidden -- which is the "
        "point for a high-volume list you want out of the inbox and out of "
        "the sidebar",
    )

    # Accepted only so the failure is a clear explanation rather than an
    # argparse "unrecognized arguments" message.
    group.add_argument("--redirect", metavar="ADDRESS", help=argparse.SUPPRESS)
    group.add_argument("--notify", metavar="TARGET", help=argparse.SUPPRESS)
    group.add_argument("--vacation", metavar="TEXT", help=argparse.SUPPRESS)

    return parser


# ----------------------------------------------------------------------------
def safety_parser() -> argparse.ArgumentParser:
    """Flags shared by every mutating subcommand."""
    parser = argparse.ArgumentParser(add_help=False)

    group = parser.add_argument_group("safety")
    group.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="show the diff and the affected messages; change nothing",
    )
    group.add_argument(
        "--yes", action="store_true", help="skip confirmation prompts"
    )
    return parser


# ----------------------------------------------------------------------------
def mail_safety_parser() -> argparse.ArgumentParser:
    """Safety flags that only mean something for the existing-mail pass.

    Kept apart from ``safety_parser`` so a command that never touches mail
    -- ``remove-rule`` -- does not advertise flags that would do nothing.
    """
    parser = argparse.ArgumentParser(add_help=False)

    group = parser.add_argument_group("existing mail")
    group.add_argument(
        "--move-threshold",
        dest="move_threshold",
        type=int,
        default=DEFAULT_MOVE_THRESHOLD,
        help=f"treat a move of more than N messages as a large operation "
        f"in the confirmation prompt (default {DEFAULT_MOVE_THRESHOLD})",
    )
    group.add_argument(
        "--max-messages",
        dest="max_messages",
        type=int,
        default=DEFAULT_MAX_MESSAGES,
        help=f"refuse the existing-mail pass if more than N messages match, "
        f"rather than processing a partial batch "
        f"(default {DEFAULT_MAX_MESSAGES})",
    )

    return parser


# ----------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """Construct the full argument parser."""
    common = global_parser()
    connection = connection_parser()
    criteria = criteria_parser()
    actions = action_parser()
    safety = safety_parser()
    mail_safety = mail_safety_parser()

    parser = argparse.ArgumentParser(
        prog="mxfilter",
        description="Manage MXRoute Sieve filters and apply them to "
        "existing mail.",
    )

    parser.add_argument(
        "--version", action="version", version=f"mxfilter {__version__}"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="step-by-step progress"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="show a full traceback on failure (never prints credentials)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    listing = subparsers.add_parser(
        "list", parents=[common, connection], help="list Sieve scripts"
    )
    listing.set_defaults(handler=cmd_list)

    show = subparsers.add_parser(
        "show", parents=[common, connection], help="print a Sieve script"
    )
    show.add_argument("name", nargs="?", help="script name; default active")
    show.set_defaults(handler=cmd_show)

    rules = subparsers.add_parser(
        "rules",
        parents=[common, connection],
        help="show the rules in order, and which cannot fire",
        description="List the active script's rules in the order the server "
        "evaluates them, marking which carry 'stop', then report any rule an "
        "earlier one makes unreachable. A '!' finding is decided; a '?' is a "
        "suspicion worth checking. Nothing is changed.",
    )
    rules.add_argument("--script", help="script name; default active")
    rules.set_defaults(handler=cmd_rules)

    backup = subparsers.add_parser(
        "backup",
        parents=[common, connection],
        help="save the active script to a file",
        description="Save the active Sieve script to a file, byte for byte "
        "as the server has it -- no banner lines, nothing reformatted "
        "(which is what 'mxfilter show' adds, and why it is not a backup). "
        "The file is written mode 0600, in a directory created 0700 if it "
        "was not there. Nothing on the server is touched. 'mxfilter "
        "restore FILE' puts a backup back.",
    )
    backup.add_argument(
        "--output",
        "-o",
        metavar="PATH",
        help="where to write it. A PATH ending in '/', or naming a "
        "directory that already exists, means 'put the default filename "
        "in here'; anything else is the exact file to write. Default: "
        "the backup directory (--backup-dir), named "
        "<script>-<UTC timestamp>.sieve",
    )
    backup.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="report the file that would be written; write nothing",
    )
    backup.set_defaults(handler=cmd_backup)

    restore = subparsers.add_parser(
        "restore",
        parents=[common, connection, safety],
        help="upload a backup file over the active script",
        description="Replace the active Sieve script with a backup file, "
        "byte for byte. The difference between the file and what the "
        "server has now is shown first, the current script is backed up "
        "before anything is sent, the server validates the file "
        "(CHECKSCRIPT), and you are asked to confirm. No other stored "
        "script is touched. Unlike every other change mxfilter makes, "
        "this REPLACES the script rather than merging into it -- any rule "
        "added since the backup was taken is removed, which the diff "
        "shows.",
    )
    restore.add_argument(
        "file", metavar="FILE", help="a file written by 'mxfilter backup'"
    )
    restore.set_defaults(handler=cmd_restore)

    folders = subparsers.add_parser(
        "folders", parents=[common, connection], help="list IMAP folders"
    )
    folders.set_defaults(handler=cmd_folders)

    for name, summary in (
        ("subscribe", "show a folder in webmail (IMAP SUBSCRIBE)"),
        ("unsubscribe", "hide a folder from webmail; it keeps its mail"),
    ):
        toggle = subparsers.add_parser(
            name,
            parents=[common, connection],
            help=summary,
            description=f"{summary[0].upper()}{summary[1:]}. Webmail draws "
            "its folder tree from the subscription list (LSUB), so this is "
            "what decides whether a folder is visible there. The folder "
            "name is normalized like every other: 'Lists/GitHub' and "
            "'INBOX.Lists.GitHub' name the same folder.",
        )
        toggle.add_argument("folder", metavar="FOLDER")
        toggle.add_argument(
            "--dry-run",
            dest="dry_run",
            action="store_true",
            help="say what would change; change nothing",
        )
        toggle.set_defaults(handler=cmd_subscribe)

    test = subparsers.add_parser(
        "test",
        parents=[common, connection],
        help="check reachability, change nothing",
    )
    test.set_defaults(handler=cmd_test)

    add = subparsers.add_parser(
        "add",
        parents=[common, connection, criteria, actions, safety, mail_safety],
        help="add a rule and apply it to existing mail",
    )
    _add_rule_flags(add)
    add.set_defaults(handler=cmd_add)

    from_message = subparsers.add_parser(
        "from-message",
        parents=[common, connection, criteria, actions, safety, mail_safety],
        help="derive criteria from a message, then add the rule",
    )
    _add_rule_flags(from_message)
    from_message.add_argument("--uid", type=int, help="message UID to read")
    from_message.add_argument(
        "--search", help="IMAP search expression selecting one message"
    )
    from_message.add_argument(
        "--derive",
        default="auto",
        help="headers to derive from, comma separated (default: auto -- "
        "List-Id if present, else From)",
    )
    from_message.set_defaults(handler=cmd_from_message)

    apply_cmd = subparsers.add_parser(
        "apply",
        parents=[common, connection, criteria, actions, safety, mail_safety],
        help="apply criteria to existing mail only",
    )
    apply_cmd.add_argument(
        "--folder", default="INBOX", help="source folder; default INBOX"
    )
    apply_cmd.add_argument("--delimiter", help=argparse.SUPPRESS)
    apply_cmd.set_defaults(handler=cmd_apply, no_imap=False)

    messages = subparsers.add_parser(
        "messages",
        parents=[common, connection, criteria],
        help="list the newest messages in a folder",
    )
    messages.add_argument(
        "--folder", default="INBOX", help="folder to list; default INBOX"
    )
    messages.add_argument(
        "--search",
        help="raw IMAP search expression instead of criteria flags, "
        "e.g. 'UNSEEN' or 'SINCE 1-Sep-2026'",
    )
    messages.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIST_LIMIT,
        help=f"show at most N messages; default {DEFAULT_LIST_LIMIT}",
    )
    messages.set_defaults(handler=cmd_messages)

    view = subparsers.add_parser(
        "view",
        parents=[common, connection],
        help="show one message, without marking it read",
    )
    view.add_argument("uid", type=int, help="the message UID ('messages')")
    view.add_argument(
        "--folder", default="INBOX", help="folder holding it; default INBOX"
    )
    shape = view.add_mutually_exclusive_group()
    shape.add_argument(
        "--headers-only",
        dest="headers_only",
        action="store_true",
        help="print every header, and no body",
    )
    shape.add_argument(
        "--raw",
        action="store_true",
        help="print the full RFC 822 source: escaped on a terminal, the "
        "exact bytes into a pipe or file ('--raw > msg.eml')",
    )
    view.set_defaults(handler=cmd_view)

    remove = subparsers.add_parser(
        "remove-rule",
        parents=[common, connection, safety],
        help="remove a named rule from the active script",
    )
    remove.add_argument("rule_name", metavar="NAME")
    remove.add_argument("--script", help="script name; default active")
    remove.add_argument("--activate", action="store_true", help=ACTIVATE_HELP)
    remove.set_defaults(handler=cmd_remove_rule)

    move = subparsers.add_parser(
        "move-rule",
        parents=[common, connection, safety],
        help="move a named rule to a new position, unchanged",
        description="Reorder one rule without restating it: only its "
        "position changes. Sieve runs rules in order and 'stop' ends the "
        "run, so the move is judged where the rule lands -- what would "
        "stop it running, and what it would now stop -- before the diff "
        "is shown. The script is backed up first and you are asked to "
        "confirm.",
    )
    move.add_argument("rule_name", metavar="NAME")
    move.add_argument("--script", help="script name; default active")
    move.add_argument("--activate", action="store_true", help=ACTIVATE_HELP)

    where = move.add_argument_group("position").add_mutually_exclusive_group(
        required=True
    )
    where.add_argument(
        "--first",
        dest="place_first",
        action="store_true",
        help="before every other rule",
    )
    where.add_argument(
        "--last",
        dest="place_last",
        action="store_true",
        help="after every other rule",
    )
    where.add_argument(
        "--before",
        dest="place_before",
        metavar="OTHER",
        help="immediately before the rule named OTHER",
    )
    where.add_argument(
        "--after",
        dest="place_after",
        metavar="OTHER",
        help="immediately after the rule named OTHER",
    )
    move.set_defaults(handler=cmd_move_rule)

    return parser


# ----------------------------------------------------------------------------
def _add_rule_flags(parser: argparse.ArgumentParser) -> None:
    """Attach the rule-authoring flags shared by add and from-message."""
    group = parser.add_argument_group("rule")
    group.add_argument(
        "--name", help="rule name; derived from criteria if omitted"
    )
    group.add_argument("--script", help="script name; default active")
    group.add_argument("--activate", action="store_true", help=ACTIVATE_HELP)
    group.add_argument(
        "--replace",
        action="store_true",
        help="overwrite an existing rule of the same name",
    )
    group.add_argument(
        "--no-apply",
        dest="no_apply",
        action="store_true",
        help="do not touch mail that has already been delivered",
    )
    group.add_argument(
        "--no-imap",
        dest="no_imap",
        action="store_true",
        help="skip IMAP entirely; implies --no-apply and guesses the "
        "folder delimiter",
    )
    group.add_argument(
        "--folder", default="INBOX", help="source folder; default INBOX"
    )
    group.add_argument(
        "--delimiter",
        help="folder delimiter to assume when --no-imap is used",
    )

    # Sieve runs rules in order and 'stop' ends the run, so where a rule
    # goes decides whether it fires at all. Mutually exclusive because the
    # four are four answers to one question; giving none of them keeps the
    # behaviour every earlier version had.
    where = group.add_mutually_exclusive_group()
    where.add_argument(
        "--first",
        dest="place_first",
        action="store_true",
        help="put the rule before every existing rule",
    )
    where.add_argument(
        "--last",
        dest="place_last",
        action="store_true",
        help="put the rule after every existing rule (the default). With "
        "--replace this MOVES an existing rule to the end; without it, "
        "the rule is simply appended as always",
    )
    where.add_argument(
        "--before",
        dest="place_before",
        metavar="NAME",
        help="put the rule immediately before the rule named NAME",
    )
    where.add_argument(
        "--after",
        dest="place_after",
        metavar="NAME",
        help="put the rule immediately after the rule named NAME",
    )


# ############################################################################
# Entry point
# ############################################################################


# ----------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """Parse arguments, dispatch, and turn failures into diagnostics."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # --no-subscribe only shapes a folder this run creates. Accepting it
    # alone would be a flag that looks like it took effect and did not.
    if getattr(args, "no_subscribe", False) and not args.create_folder:
        parser.error(
            "--no-subscribe only applies with --create-folder; to hide a "
            "folder that already exists, use 'mxfilter unsubscribe FOLDER'"
        )

    if getattr(args, "no_imap", False):
        args.no_apply = True

    try:
        return args.handler(args)

    except MxFilterError as exc:
        if args.debug:
            traceback.print_exc()

        # The core breaks a long message into lines with bare newlines and
        # leaves the layout here; indent them under the prefix.
        message = str(exc).replace("\n", "\n  ")

        print(f"mxfilter: {message}", file=sys.stderr)

        return 1

    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)

        return 130

    except Exception as exc:
        if args.debug:
            traceback.print_exc()

        print(
            f"mxfilter: unexpected {type(exc).__name__}: {exc} "
            f"(re-run with --debug for a traceback)",
            file=sys.stderr,
        )

        return 1
