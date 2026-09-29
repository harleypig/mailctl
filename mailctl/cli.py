"""Command-line interface.

This module is the CLI front-end and nothing else: it parses arguments,
turns them into the utilities' plain inputs, calls ``mailctl.utilities``
against the provider ``mailctl.engine`` opens, and renders what comes
back. Every decision a person makes -- confirming, ``--dry-run``,
``--yes`` -- is taken here, between a utility's plan and its execution.

Every mutating subcommand follows the same shape: work out what would
change, show it, and only then -- after a backup and the server's own
validation -- change it. ``--dry-run`` stops after the "show it" step.
"""

import argparse
import contextlib
import re
import shlex
import sys
import traceback

from . import MailctlError, __version__, engine, json_output, utilities
from .config import (
    CONFIG_FILE,
    DEFAULT,
    ENV_FILE,
    ENVIRONMENT,
    SIEVE_TLS_MODES,
    Config,
    load_config,
)
from .criteria import (
    COMPARE_OPS,
    MATCH_MODES,
    Criteria,
    dump_filter,
    load_filter,
    parse_age,
    parse_date,
)
from .rules import CERTAIN
from .utilities.folders import FolderCreation
from .utilities.mail import DEFAULT_MAX_MESSAGES
from .utilities.messages import (
    DEFAULT_LIST_LIMIT,
    SORT_KEYS,
    SortOrder,
    decode_header_value,
)
from .utilities.reports import Wording
from .utilities.rules import (
    PLACE_AFTER,
    PLACE_BEFORE,
    PLACE_FIRST,
    PLACE_LAST,
    ActionSpec,
    DisplayDiff,
    Placement,
    RuleRequest,
)

__all__ = ["build_parser", "main"]

DEFAULT_MOVE_THRESHOLD = 25

# What 'check-baseline' exits with when it finds drift, so a scheduled run
# can tell "something changed" from "something that matters changed", and
# both from a failure (1) and a usage error (2).
DRIFT_INFO_EXIT = 3
DRIFT_SERIOUS_EXIT = 4

# The version of the document 'check-baseline --json' prints.
DRIFT_VERSION = 1
PREVIEW_LIMIT = 20

# Where 'probe --report' tells the user to file what it printed. mailctl
# only ever prints the body; the user reads it and files it themselves.
ISSUES = "harleypig/mailctl"
NEW_ISSUE_URL = f"https://github.com/{ISSUES}/issues/new"

# Formatted with the provider's Wording, which names the rule language.
ACTIVATE_HELP = (
    "make the script the active one, the one {rule_language} runs. Without "
    "it, a --script other than the active one is stored but left inactive"
)

# --folder has no argparse default: one there would outrank
# MAILCTL_SOURCE_FOLDER and source_folder in the config file (#63).
FOLDER_DEFAULT_HELP = "default: source_folder from the config, else INBOX"

# --json's help: a reading command prints its result, a write command its
# --dry-run plan. The shapes are listed in README.md > Output for scripts.
JSON_HELP = "print the result as a JSON document, and nothing else on stdout"
JSON_PLAN_HELP = (
    "with --dry-run, print the plan as a JSON document, and nothing else on "
    "stdout; refused without --dry-run"
)

# The action flags refused with an explanation rather than an argparse
# "unrecognized arguments" error; see action_parser.
REFUSED_ACTION_FLAGS = ("redirect", "notify", "vacation")

NO_CRITERIA = (
    "no criteria given -- use --from/--to/--cc/--subject/--list-id/"
    "--header/--body, --filter FILE, or --like UID"
)

# Said after every save: the rule filters only new mail, and a user who
# expected the mail already there to move needs pointing at apply (#149).
ADD_LEAVES_MAIL = (
    "\nMail already delivered was not touched; to act on it, run "
    "'mailctl apply' with the same criteria and actions."
)

# The headers a person recognises one of their own emails by, shown by
# '--like' before it derives anything. List-Id earns its place
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
def emit_json(args, document: dict) -> int:
    """Write a --json document to the real stdout; see ``main``."""
    args.stdout.write(json_output.dumps(document))

    return 0


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
        raise MailctlError(
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
    value = utilities.messages.LONE_SURROGATES.sub("\ufffd", value)

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
def progress_from_args(args, stream=None):
    """Return the engine's progress callback, or None without --verbose.

    The engine reports protocol progress by calling back rather than
    printing, so the decision to show it -- and the decoration around it --
    is made once, here. ``stream`` is where it goes; stdout by default.
    """
    if not args.verbose:
        return None

    def emit(channel: str, message: str) -> None:
        print(f"[{channel}] {message}", file=stream or sys.stdout)

    return emit


# ----------------------------------------------------------------------------
def connect(config, args, *, rules: bool = True, mail: bool = False):
    """Open the provider with this invocation's progress reporting.

    Each half connects the first time the command uses it, so a command
    that fails on its own input fails before any login or password prompt.
    """
    return engine.connect(
        config,
        rules=rules,
        mail=mail,
        progress=progress_from_args(args),
    )


# ----------------------------------------------------------------------------
def render_event(event) -> None:
    """Print one step of a change as the engine reports it."""
    if isinstance(event, utilities.events.ScriptBackedUp):
        print(f"Backed up current script to {event.path}")

    elif (
        isinstance(event, utilities.events.ScriptUploaded) and event.activated
    ):
        print(f"Uploaded and activated script {event.script!r}")

    elif isinstance(event, utilities.events.ScriptUploaded):
        print(f"Uploaded script {event.script!r}; it was not activated")

    elif isinstance(event, utilities.events.FolderCreated):
        report_folder_creation(event.result)

    elif isinstance(event, utilities.events.FolderRenamed):
        under = (
            f", with the {event.children} folder"
            f"{'s' if event.children != 1 else ''} under it"
            if event.children
            else ""
        )
        print(f"Renamed IMAP folder {event.old!r} to {event.new!r}{under}")

    elif isinstance(event, utilities.events.SubscriptionChanged):
        if event.subscribed:
            print(f"Subscribed to {event.folder!r}")

        else:
            print(f"Removed {event.folder!r} from the subscription list")


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

    warn_about_legacy_settings(
        utilities.migration.check_legacy_settings(config)
    )
    warn_about_inline_password(args)

    return config


# ----------------------------------------------------------------------------
def warn_about_legacy_settings(found) -> None:
    """Name each old setting this run ignored, and the name to use.

    Names and places only. The old variable's value is never read, so it
    cannot be shown -- one of them is a password.
    """
    for setting in found:
        warn(
            f"{setting.old} ({setting.where.describe()}) is no longer read; "
            f"rename it to {setting.new}"
        )


# ----------------------------------------------------------------------------
def warn_about_inline_password(args) -> None:
    """Say what ``--password`` costs, the way ``mysql`` does.

    The flag exists because it was asked for, and the warning exists
    because what it gives away is not obvious: an argument is readable in
    the process list by every user on the machine for as long as the
    command runs, and the shell has already written it to history by the
    time mailctl starts.

    The message names no part of the value.
    """
    if not getattr(args, "password", None):
        return

    warn(
        "a password given on the command line is visible in the process "
        "list to every user on this machine, and your shell has already "
        "saved it to history. Prefer --password-file or "
        "MAILCTL_PASSWORD_FILE."
    )


# ############################################################################
# Engine inputs from parsed args
# ############################################################################


# ----------------------------------------------------------------------------
def criteria_from_args(args) -> Criteria:
    """Build the Criteria model from the shared criteria flags.

    ``--match`` and ``--compare`` default to None so that giving one can be
    told apart from not; the model's own defaults stand in for them.
    """
    given = {
        name: getattr(args, name)
        for name in ("match", "compare")
        if getattr(args, name) is not None
    }

    if args.since is not None:
        given["since"] = parse_date(args.since, "--since")

    # add has no date --before: its --before places the rule (see
    # criteria_parser).
    if getattr(args, "received_before", None) is not None:
        given["before"] = parse_date(args.received_before, "--before")

    if args.older_than is not None:
        given["older_than"] = parse_age(args.older_than, "--older-than")

    criteria = Criteria(**given, unread=args.unread, flagged=args.flagged)

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
            raise MailctlError(f"--header expects NAME=VALUE, got {item!r}")

        name, value = item.split("=", 1)
        criteria.add(name, value)

    for value in args.body or []:
        criteria.add_body(value)

    return criteria


# ----------------------------------------------------------------------------
def criteria_given(args) -> Criteria:
    """The criteria ``add`` and ``apply`` were given, checked before
    connecting: the criteria flags, or the ``--filter`` document.

    With ``--like`` the result may be empty; the message supplies the rest
    once it has been read (:func:`message_like`).
    """
    flags = criteria_from_args(args)

    if args.derive is not None and args.like is None:
        raise MailctlError("--derive needs --like UID")

    if args.filter_file is None:
        if not flags and args.like is None:
            raise MailctlError(NO_CRITERIA)

        return flags

    if flags or args.match is not None or args.compare is not None:
        raise MailctlError(
            "--filter carries its own criteria; give criteria flags or "
            "--filter, not both"
        )

    if args.like is not None:
        raise MailctlError(
            "--like and --filter both supply the criteria; give one of them"
        )

    return read_filter(args.filter_file)


# ----------------------------------------------------------------------------
def read_filter(path: str) -> Criteria:
    """Read a filter document from ``path``, or standard input for '-'."""
    if path == "-":
        source, text = "standard input", sys.stdin.read()

    else:
        source = path

        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()

        except OSError as exc:
            raise MailctlError(
                f"cannot read filter file {path!r}: {exc.strerror}"
            ) from exc

        except UnicodeDecodeError as exc:
            raise MailctlError(
                f"filter file {path!r} is not UTF-8 text"
            ) from exc

    try:
        return load_filter(text)

    except MailctlError as exc:
        raise MailctlError(f"{source}: {exc}") from exc


# ----------------------------------------------------------------------------
def message_like(sessions, config, args, explicit: Criteria, file=None):
    """Read the ``--like`` message, show it, and return what it makes.

    The message is shown before anything else: it is the answer to "is
    this the message I meant?", which the criteria cannot give. ``file``
    is where that goes, for a caller keeping stdout for data.
    """
    like = utilities.mail.criteria_like(
        sessions,
        config.source_folder,
        args.like,
        explicit,
        args.derive or "auto",
    )

    print_message(
        like.message.headers, like.message.uid, like.message.folder, file
    )

    for header in like.skipped:
        warn(f"message has no {header!r} header; skipping it")

    if not like.criteria:
        raise MailctlError(
            f"nothing to derive from uid {like.message.uid}: it has none of "
            f"the headers asked for ({', '.join(like.skipped)})"
        )

    return like


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
        # None leaves it to the provider: its rules stop where it can.
        stop=False if args.no_stop else None,
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
def reject_forbidden(config, args) -> None:
    """Hand every refused action flag that was given to the engine."""
    utilities.rules.reject_actions(
        config,
        (name for name in REFUSED_ACTION_FLAGS if getattr(args, name, None)),
    )


# ############################################################################
# Rendering plans and results
# ############################################################################


# ----------------------------------------------------------------------------
def show_folder_plan(plan: utilities.folders.FolderPlan) -> None:
    """Say where filed mail goes, before anything is created."""
    if plan.status == utilities.folders.FOLDER_NONE:
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

    if plan.case_variants:
        # Exact matching is right (#56), but a folder beside the target
        # that differs only in case is almost always the one meant, and a
        # create would otherwise make a second one without a word.
        variants = ", ".join(repr(name) for name in plan.case_variants)
        effect = (
            "a second folder will be created beside it"
            if plan.status != utilities.folders.FOLDER_MISSING
            else "it does not count"
        )

        warn(
            f"no folder {plan.folder!r}, but {variants} differs only in "
            f"case; folder names are case-sensitive, so {effect}"
        )


# ----------------------------------------------------------------------------
def prepare_folder(sessions, config, args) -> utilities.folders.FolderPlan:
    """Plan the rule's target folder, say what it is, and settle it."""
    plan = utilities.folders.plan_folder(
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
def settle_folder(sessions, plan: utilities.folders.FolderPlan, args) -> None:
    """Report how the target folder comes to exist, creating it if due.

    Nothing is created here. A folder due for IMAP creation is announced
    now and created by the engine's execute step, after the change has
    been shown and decided on -- so a dry run, an abort, or a rejected
    plan leaves no stray folder behind.
    """
    utilities.folders.check_folder(sessions, plan)
    words = sessions.wording

    if plan.status == utilities.folders.FOLDER_MISSING:
        warn(
            f"target folder {plan.folder!r} does not exist. Mail filed there "
            f"may be lost; pass --create-folder to create it."
        )

    elif plan.status == utilities.folders.FOLDER_SIEVE_CREATES:
        print(
            f"Folder {plan.folder!r} will be created by "
            f"{words.rule_language} ({words.create_action})"
        )

        if plan.subscribe:
            # Only reached under --no-imap: with an IMAP session the folder
            # is created and subscribed over IMAP as well (#40). Here Sieve
            # creates it at delivery time, when mailctl is not running and
            # cannot subscribe to it. Whether the server does so itself is
            # genuinely unknown -- RFC 5490 says :create creates the mailbox
            # and says nothing about subscription, and this account has
            # never been observed doing it either way.
            #
            # So the wording claims only the absence of a promise, not that
            # it will not happen. Asserting the stronger version would be
            # inventing a fact about the server, which is the failure
            # CONVENTIONS.md 'Confidence' exists to prevent.
            print(
                f"  Nothing promises {words.rule_language} will subscribe to "
                "a folder it creates, so it may not appear in webmail until "
                "you subscribe to it: once the first message has created "
                f"it, run 'mailctl subscribe {plan.folder}'."
            )

    elif plan.status == utilities.folders.FOLDER_IMAP_CREATE:
        announce_folder_creation(plan, args.dry_run)

        if plan.mailbox_disabled_by is not None:
            print(
                f"  The rule says plain '{words.file_action}', not "
                f"'{words.create_action}': {words.delivery_create} is "
                f"disabled by mailctl ({plan.mailbox_disabled_by.describe()})."
            )

    elif plan.status == utilities.folders.FOLDER_BOTH_CREATE:
        announce_folder_creation(plan, args.dry_run)

        print(
            f"  The rule also says '{words.create_action}', so "
            f"{words.rule_language} recreates the folder if it is ever "
            f"deleted."
        )


# ----------------------------------------------------------------------------
def announce_folder_creation(
    plan: utilities.folders.FolderPlan, dry_run: bool
) -> None:
    """Say that the target folder will be made over IMAP, before it is."""
    if dry_run:
        then = (
            " and subscribe to it"
            if plan.subscribe
            else ", not subscribed (--no-subscribe)"
        )
        print(f"[dry-run] would create IMAP folder {plan.folder!r}{then}")

    else:
        then = (
            " and subscribed to"
            if plan.subscribe
            else ", not subscribed (--no-subscribe),"
        )
        print(
            f"Folder {plan.folder!r} does not exist; it will be created "
            f"over IMAP{then} when the change is applied"
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
            f"you subscribe to it: run 'mailctl subscribe {folder}'."
        )

        return

    print(
        f"Created IMAP folder {folder!r}; not subscribed (--no-subscribe), "
        f"so it will not appear in webmail ('mailctl subscribe {folder}' "
        f"shows it later)."
    )


# ----------------------------------------------------------------------------
def print_script_diff(report: DisplayDiff) -> None:
    """Show the diff, and say what the diff itself is not showing.

    Both sides are rendered in mailctl's own formatting so the rule
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
            "\nNote: the script on the server is not in mailctl's "
            "formatting, so uploading re-indents the whole file. The diff "
            "below shows only the rule change; no rule body is altered."
        )

    print(f"\n--- {report.label} diff ---")
    # A rule built from a message carries that message's text, and a stored
    # script can hold any bytes: both are untrusted by the time they print.
    print(safe_text(report.text) if report.text.strip() else "(no change)")
    print("--- end diff ---")


# ----------------------------------------------------------------------------
def warn_missing_extensions(missing: list[str], words: Wording) -> None:
    """Warn about extensions the rule needs but the server does not list."""
    if missing:
        warn(
            f"the server does not advertise: {', '.join(missing)}. The "
            f"upload will be validated with {words.validation} and may be "
            f"rejected."
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
    sessions, config, criteria: Criteria, args, spec: ActionSpec, folder
) -> int:
    """Plan the existing-mail pass, show it, and run it if allowed.

    Planning is read-only, so the plan is always built first and shown
    whatever the flags say. ``--dry-run`` is simply the path that stops
    after showing it -- the decision to execute lives here, in the
    front-end, and never inside the engine.
    """
    source = utilities.mail.source_folder(sessions, config.source_folder)

    if args.json:
        return emit_mail_plan(sessions, criteria, args, spec, folder, source)

    if utilities.mail.mail_pass_is_noop(spec, source, folder.folder):
        print(
            f"\nSkipping the existing-mail pass: the rule leaves matching "
            f"mail in {source!r} as it is, so there is nothing to do."
        )

        return 0

    print(f"\nSearching {source!r} for existing matches...")

    plan = utilities.mail.plan_mail(
        sessions, criteria, spec, source, folder.folder
    )

    if plan.is_empty:
        print("No existing messages match.")

        if not args.dry_run and utilities.folders.folder_pending(
            sessions, folder
        ):
            print(
                f"Folder {folder.folder!r} was not created: there is "
                f"nothing to move into it."
            )

        return 0

    print(f"{plan.count} message(s) match:")
    print_preview(plan.messages)
    describe_copy_check(plan)

    if not plan.changes:
        print(
            f"Nothing to do: {plan.destination!r} already holds every match."
        )

        return 0

    if args.dry_run:
        describe_plan(plan, prefix="[dry-run] would ")

        if plan.count > args.max_messages:
            print(
                f"[dry-run] note: {plan.count} matches exceed "
                f"--max-messages {args.max_messages}; a real run would stop "
                f"and ask."
            )

        return 0

    utilities.mail.check_message_cap(plan, args.max_messages)

    if not confirm(action_prompt(plan, args.move_threshold), args.yes):
        print("Aborted; no messages were touched.")

        return 0

    result = utilities.mail.execute_mail(
        sessions, plan, args.max_messages, folder, render_event
    )

    report_result(result, plan)

    return result.moved or result.deleted or result.flagged or result.copied


# ----------------------------------------------------------------------------
def emit_mail_plan(sessions, criteria, args, spec, folder, source) -> int:
    """``apply --dry-run --json``: the existing-mail plan as a document.

    A pass the actions make pointless plans nothing, and says so as
    ``changes`` false with no messages, rather than searching.
    """
    noop = utilities.mail.mail_pass_is_noop(spec, source, folder.folder)
    mail = (
        None
        if noop
        else utilities.mail.plan_mail(
            sessions, criteria, spec, source, folder.folder
        )
    )

    return emit_json(
        args,
        json_output.plan(
            "apply",
            changes=bool(mail and mail.changes),
            criteria=criteria.to_dict(),
            actions=json_output.actions(spec),
            folder=json_output.folder_plan(folder),
            max_messages=args.max_messages,
            over_limit=bool(mail and mail.count > args.max_messages),
            mail=None if mail is None else json_output.mail_plan(mail),
        ),
    )


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

    elif plan.copies and plan.copy_uids:
        print(
            f"{prefix}copy {len(plan.copy_uids)} message(s) to "
            f"{plan.destination!r}, leaving them in {plan.source!r}"
        )


# ----------------------------------------------------------------------------
def describe_copy_check(plan) -> None:
    """Say which matches a copy leaves out, and which it could not check."""
    if plan.held:
        print(
            f"{len(plan.held)} message(s) already in {plan.destination!r} "
            f"(same Message-ID) will not be copied again."
        )

    if plan.unidentified:
        print(
            f"{len(plan.unidentified)} message(s) with no Message-ID cannot "
            f"be looked for in {plan.destination!r}, so they will be copied."
        )


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

    if result.copied:
        print(
            f"Copied {result.copied} message(s) from {plan.source!r} to "
            f"{plan.destination!r}; the originals stay in {plan.source!r}"
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

    if plan.copies and plan.copy_uids:
        return (
            f"Copy {len(plan.copy_uids)} message(s) from {plan.source!r} to "
            f"{plan.destination!r}, leaving them in {plan.source!r}?"
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
        active, others = utilities.scripts.list_scripts(sessions)

        if args.json:
            return emit_json(args, json_output.scripts(active, others))

        if not active and not others:
            print(f"No {sessions.wording.rule_sets} on the server.")

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
        script = utilities.scripts.read_script(sessions, args.name)
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
        marker = "  [disabled]" if rule.disabled else ""
        marker += "  [stop]" if rule.stops else ""

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
        report = utilities.rules.read_rules(sessions, args.script)

        if args.json:
            return emit_json(args, json_output.rules_report(report))

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
        plan = utilities.backup.plan_backup(sessions, config, args.output)

        if args.dry_run:
            count = rule_count_phrase(sessions, plan.source)
            print(f"[dry-run] would write {count} to {plan.target}")

            return 0

        # Written before the script is parsed: counting its rules is a
        # nicety, and a script too broken to parse is exactly the one worth
        # having a copy of.
        target = utilities.backup.execute_backup(plan)

    print(f"wrote {rule_count_phrase(sessions, plan.source)} to {target}")

    return 0


# ----------------------------------------------------------------------------
def cmd_restore(args) -> int:
    """Replace a script with a backup file, after showing it."""
    config = configure(args)
    backup = utilities.backup.read_backup_file(args.file, args.allow_empty)

    with connect(config, args) as sessions:
        plan = utilities.backup.plan_restore(
            sessions, backup, args.script, args.activate
        )

        if args.json:
            count = utilities.backup.count_rules

            return emit_json(
                args,
                json_output.plan(
                    "restore",
                    **json_output.change(plan.changes, plan.diff),
                    source=str(plan.source),
                    rules_before=count(sessions, plan.before),
                    rules_after=count(sessions, plan.after),
                    **json_output.activation(plan),
                ),
            )

        after = rule_count_phrase(sessions, plan.after)
        before = rule_count_phrase(sessions, plan.before)

        print(
            f"Restore {plan.source} ({after}) over script {plan.script!r} "
            f"({before}):"
        )

        if not plan.changes:
            print(
                "\nThe file is identical to the script on the server; "
                "nothing to restore."
            )

            return 0

        print_script_diff(plan.diff)
        print_activation(plan)

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

        utilities.backup.execute_restore(sessions, config, plan, render_event)

    return 0


# ----------------------------------------------------------------------------
def warn_about_config_dir(pending) -> None:
    """Say, loudly, that the config and backups were left behind."""
    if pending is None:
        return

    lines = (
        "*" * 72,
        "mailctl's config directory moved when the tool was renamed.",
        f"  old: {pending.old}  (still there, and NOT read)",
        f"  new: {pending.new}  (does not exist yet)",
        "Your config.toml and script backups are still in the old one.",
        "Run 'mailctl migrate-config' to move them; --dry-run shows what",
        "would move.",
        "*" * 72,
    )

    for line in lines:
        warn(line)


# ----------------------------------------------------------------------------
def render_migration_event(event) -> None:
    """Print one step of a config migration as the engine reports it."""
    if isinstance(event, utilities.migration.FileMoved):
        print(f"Moved {event.source} -> {event.destination}")

    elif (
        isinstance(event, utilities.migration.ReferenceRewritten)
        and event.rewritten
    ):
        print(
            f"Pointed {event.reference.key} in config.toml at "
            f"{event.reference.replacement}"
        )

    elif isinstance(event, utilities.migration.ReferenceRewritten):
        warn(
            f"config.toml's {event.reference.key} still points inside the "
            f"old directory and could not be changed as written; set it to "
            f"{event.reference.replacement}"
        )

    elif isinstance(event, utilities.migration.OldDirRemoved):
        print(f"Removed the now-empty {event.path}")


# ----------------------------------------------------------------------------
def cmd_migrate_config(args) -> int:
    """Move the old-name config directory's contents to the new one."""
    plan = utilities.migration.plan_config_migration()

    if plan is None:
        print("Nothing to migrate: there is no old config directory.")

        return 0

    print(f"Move the contents of {plan.old}\n  into {plan.new}:")

    for entry in plan.entries:
        suffix = "/" if entry.directory else ""
        print(f"  {entry.mode:04o}  {entry.relative}{suffix}")

    if not plan.entries:
        print("  (nothing inside it; the empty directory is removed)")

    for reference in plan.references:
        print(
            f"config.toml's {reference.key} points inside the old "
            f"directory and becomes {reference.replacement}"
        )

    if plan.conflicts:
        for path in plan.conflicts:
            warn(f"already exists, and would be overwritten: {path}")

        raise MailctlError(
            "refusing to migrate: nothing at the destination is "
            "overwritten. Move or remove what is listed above, then re-run."
        )

    if args.dry_run:
        print("\n[dry-run] nothing was moved.")

        return 0

    count = len(plan.files)

    if not confirm(f"Move {count} file(s) into {plan.new}?", args.yes):
        print("Aborted; nothing was moved.")

        return 0

    utilities.migration.execute_config_migration(plan, render_migration_event)

    return 0


# ----------------------------------------------------------------------------
def rule_count_phrase(provider, source: str) -> str:
    """Describe how many rules a script holds, for the summary line."""
    count = utilities.backup.count_rules(provider, source)

    if count is None:
        return "a script mailctl could not parse"

    return f"{count} rule(s)"


# ----------------------------------------------------------------------------
def cmd_folders(args) -> int:
    """List IMAP folders and the detected hierarchy delimiter."""
    config = configure(args)

    with connect(config, args, rules=False, mail=True) as sessions:
        counts = None

        if args.counts:
            counts = utilities.folders.list_folder_counts(sessions)
            listing = counts.listing

        else:
            listing = utilities.folders.list_folders(sessions)

        if args.json:
            return emit_json(
                args,
                json_output.folder_listing(
                    listing, counts.statuses if counts else None
                ),
            )

        print(f"Hierarchy delimiter: {listing.delimiter!r}")
        print(
            f"{len(listing.folders)} folder(s), "
            f"{len(listing.folders) - len(listing.unsubscribed)} subscribed "
            f"(webmail shows only subscribed folders):"
        )

        if counts is not None:
            show_folder_counts(counts)

            return 0

        width = max((len(name) for name in listing.folders), default=0)

        for folder in listing.folders:
            if listing.is_subscribed(folder):
                print(f"  {folder}")

            else:
                print(f"  {folder:<{width}}  (not subscribed)")

    return 0


# ----------------------------------------------------------------------------
def show_folder_counts(counts: utilities.folders.FolderCounts) -> None:
    """The folder list as a table of counts; "-" where the host gave none.

    The size column appears only when the host reports sizes.
    """
    listing = counts.listing
    columns = [("Messages", "messages"), ("Unread", "unseen")]

    if counts.sizes:
        columns.append(("Size", "size"))

    rows = [
        [count_cell(getattr(status, field), field) for _, field in columns]
        for status in counts.statuses
    ]
    widths = [
        max([len(title), *(len(row[index]) for row in rows)])
        for index, (title, _) in enumerate(columns)
    ]
    name_width = max([len("Folder"), *map(len, listing.folders)])

    def line(name: str, cells: list[str]) -> str:
        figures = "  ".join(
            f"{cell:>{width}}"
            for cell, width in zip(cells, widths, strict=True)
        )

        return f"  {name:<{name_width}}  {figures}"

    print(line("Folder", [title for title, _ in columns]))

    for folder, row in zip(listing.folders, rows, strict=True):
        if listing.is_subscribed(folder):
            print(line(folder, row))

        else:
            print(f"{line(folder, row)}  (not subscribed)")


# ----------------------------------------------------------------------------
def count_cell(value: int | None, field: str) -> str:
    """One count as its table cell."""
    if value is None:
        return "-"

    if field == "size":
        return human_size(value)

    return str(value)


# ----------------------------------------------------------------------------
def cmd_subscribe(args) -> int:
    """Subscribe to, or unsubscribe from, an existing folder."""
    config = configure(args)
    subscribe = args.command == "subscribe"

    with connect(config, args, rules=False, mail=True) as sessions:
        plan = utilities.folders.plan_subscription(
            sessions, args.folder, subscribe
        )

        if args.json:
            return emit_json(
                args,
                json_output.plan(
                    args.command,
                    changes=plan.changes,
                    requested=plan.requested,
                    folder=plan.folder,
                    delimiter=plan.delimiter,
                    subscribe=plan.subscribe,
                    subscribed_now=plan.subscribed_now,
                ),
            )

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

        utilities.folders.execute_subscription(sessions, plan)

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
def cmd_create_folder(args) -> int:
    """Create a folder on its own, subscribed unless declined."""
    config = configure(args)

    with connect(config, args, rules=False, mail=True) as sessions:
        plan = utilities.folders.plan_folder_creation(
            sessions, args.folder, args.subscribe
        )
        target = plan.target

        if args.json:
            return emit_json(
                args,
                json_output.plan(
                    "create-folder",
                    changes=not plan.exists,
                    folder=json_output.folder_plan(target),
                    subscribed_now=plan.subscribed_now,
                    missing_parents=list(plan.missing_parents),
                ),
            )

        if target.requested != target.folder:
            print(
                f"Folder {target.requested!r} resolves to {target.folder!r} "
                f"(delimiter {target.delimiter!r})"
            )

        if plan.exists:
            report_existing_folder(plan)

            return 0

        if plan.missing_parents:
            count = len(plan.missing_parents)
            parents = ", ".join(repr(name) for name in plan.missing_parents)
            print(
                f"Parent folder{'s' if count > 1 else ''} {parents} "
                f"{'do' if count > 1 else 'does'} not exist either; the IMAP "
                f"server is expected to create "
                f"{'them' if count > 1 else 'it'} along with "
                f"{target.folder!r}"
                + (
                    f", and only {target.folder!r} is subscribed."
                    if target.subscribe
                    else "."
                )
            )

        then = (
            " and subscribe to it"
            if target.subscribe
            else ", not subscribed (--no-subscribe)"
        )
        lead = "[dry-run] would create" if args.dry_run else "Will create"
        print(f"{lead} IMAP folder {target.folder!r}{then}")

        if args.dry_run:
            return 0

        if not confirm(f"Create folder {target.folder!r}?", args.yes):
            print("Aborted; nothing was created.")

            return 0

        result = utilities.folders.execute_folder_creation(sessions, plan)

    if result is not None:
        report_folder_creation(result)

    return 0


# ----------------------------------------------------------------------------
def report_existing_folder(plan: utilities.folders.FolderCreationPlan) -> None:
    """Say that a folder asked for already exists, and how it is shown.

    Nothing is created or subscribed: an existing folder's subscription is
    a setting of its own, changed with 'subscribe' / 'unsubscribe', so it
    is pointed at rather than changed as a side effect of asking to create.
    """
    folder = plan.target.folder

    if plan.subscribed_now:
        print(
            f"{folder!r} already exists and is subscribed; nothing to change."
        )

        if not plan.target.subscribe:
            print(
                f"  --no-subscribe only shapes a folder this command "
                f"creates; 'mailctl unsubscribe {folder}' hides it."
            )

        return

    if not plan.target.subscribe:
        print(
            f"{folder!r} already exists and is not subscribed; nothing to "
            f"change."
        )

        return

    print(
        f"{folder!r} already exists but is not subscribed, so webmail does "
        f"not show it; nothing was changed. 'mailctl subscribe {folder}' "
        f"shows it."
    )


# ----------------------------------------------------------------------------
def cmd_rename_folder(args) -> int:
    """Rename a folder, carry its subscription, and repoint its rules."""
    config = configure(args)

    with connect(config, args, rules=True, mail=True) as sessions:
        plan = utilities.folder_rename.plan_folder_rename(
            sessions, args.old, args.new
        )

        if args.json:
            return emit_json(args, json_output.folder_rename_plan(plan))

        print_folder_rename(plan, sessions.wording)

        if args.dry_run:
            print(
                "\n[dry-run] nothing was renamed, and the script was NOT "
                "uploaded."
            )

            return 0

        count = len(plan.retargets)
        rules = (
            f" and repoint {count} filing action{'s' if count != 1 else ''}"
            if count
            else ""
        )

        if not confirm(
            f"Rename folder {plan.old!r} to {plan.new!r}{rules}?", args.yes
        ):
            print("Aborted; nothing was changed.")

            return 0

        result = utilities.folder_rename.execute_folder_rename(
            sessions, config, plan, render_event
        )

    return report_folder_rename(result)


# ----------------------------------------------------------------------------
def print_folder_rename(plan, words: Wording) -> None:
    """Show what a rename would move, subscribe, and rewrite."""
    for requested, folder in (
        (plan.requested_old, plan.old),
        (plan.requested_new, plan.new),
    ):
        if requested != folder:
            print(
                f"Folder {requested!r} resolves to {folder!r} "
                f"(delimiter {plan.delimiter!r})"
            )

    count = plan.messages
    print(
        f"Rename {words.mail_service} folder {plan.old!r} to {plan.new!r} "
        f"({count} "
        f"message{'s' if count != 1 else ''})"
    )

    if plan.children:
        count = len(plan.children)
        print(
            f"  The {count} folder{'s' if count != 1 else ''} under it "
            f"move{'' if count != 1 else 's'} with it:"
        )

        for move in plan.children:
            print(f"    {move.old!r} -> {move.new!r}")

    shown = [move.new for move in plan.moves if move.subscribed]
    hidden = [move.new for move in plan.moves if not move.subscribed]

    if shown:
        print(
            f"  Subscribed after the rename, as before: "
            f"{', '.join(repr(name) for name in shown)}"
        )

    if hidden:
        print(
            f"  Left unsubscribed, as before: "
            f"{', '.join(repr(name) for name in hidden)}"
        )

    if plan.missing_parents:
        count = len(plan.missing_parents)
        parents = ", ".join(repr(name) for name in plan.missing_parents)
        print(
            f"  Parent folder{'s' if count > 1 else ''} {parents} "
            f"{'do' if count > 1 else 'does'} not exist; the "
            f"{words.mail_service} server is expected to create "
            f"{'them' if count > 1 else 'it'}."
        )

    if not plan.retargets:
        print(
            f"\nNo rule in {plan.script!r} files into {plan.old!r} or a "
            f"folder under it; the script is left alone."
        )

        return

    print(f"\nRules in {plan.script!r} that file into it:")

    for item in plan.retargets:
        print(f"  {item.rule}: {item.old!r} -> {item.new!r}")

    print_script_diff(plan.diff)
    print_activation(plan)


# ----------------------------------------------------------------------------
def report_folder_rename(result) -> int:
    """Say what a rename changed, then what reading the account back found.

    A check that failed is a non-zero exit: a rename that lands the folder
    and not its subscription looks like success to every other test.
    """
    for error in result.subscription_errors:
        warn(error)

    print("\nChecked after the rename:")

    for check in result.checks:
        mark = "ok  " if check.ok else "FAIL"
        detail = f" -- {check.detail}" if check.detail else ""

        if check.operation:
            fix = command_for(check.operation, check.arguments)
            detail = f"{detail}; '{fix}' does"

        print(f"  {mark}  {check.label}{detail}")

    if result.ok:
        return 0

    print(
        "mailctl: the rename did not fully land; the checks marked FAIL "
        "above say what is wrong",
        file=sys.stderr,
    )

    return 1


# ----------------------------------------------------------------------------
def cmd_test(args) -> int:
    """Connect to both services and report what they support."""
    config = configure(args)
    state, failure = resolve_password(config)

    print(f"Sources:   {', '.join(s.describe() for s in config.consulted)}")
    print(f"Provider:  {config.provider}  ({origin_of(config, 'provider')})")

    # The host setting is the provider's own, so a provider that does not
    # read it gets no row for it.
    if "host" in engine.capabilities_for(config).settings:
        print(f"Host:      {config.host}  ({origin_of(config, 'host')})")

    print(f"User:      {config.user}  ({origin_of(config, 'user')})")
    print(
        f"Password:  {state}{password_origin_suffix(config)}"
        f"{'  -- see the error below' if failure else ''}"
    )

    for fact in utilities.reports.connection_facts(config):
        origin = origin_of(config, **dict(fact.settings))
        print(f"{fact.label + ':':<10} {fact.text}  ({origin})")

    print(
        f"Folder:    {config.source_folder}  "
        f"({origin_of(config, 'source_folder')})  "
        f"-- read by apply, search, view, and add --like"
    )

    if failure is not None:
        raise failure

    words = utilities.reports.wording(config)

    with connect(config, args) as sessions:
        rules = utilities.reports.probe_rules(sessions)
        extensions = utilities.reports.report_extensions(
            sessions, rules, config
        )

        print(f"\n{words.rules_service}: connected")

        # Every row is read from the server's own capability listing, not
        # assumed; a host's policies are not capabilities, so they come
        # from the provider's notes at the end instead.
        if extensions:
            print(
                f"  {words.extensions} (* = mailctl's own rules can need it):"
            )
            print_extension_table(extensions)

            cleared = config.sources.get("disabled_extensions")

            # A set-but-empty list overrode a lower rung (#85); with no
            # row disabled, this line is the only place its source shows.
            if (
                not config.disabled_extensions
                and cleared
                and (cleared.kind != DEFAULT)
            ):
                print(f"  (none disabled: {cleared.describe()})")

        print(f"\n  active script: {rules.active or '(none)'}")
        print(f"  other scripts: {', '.join(rules.others) or '(none)'}")
        print(
            "  (mailctl always edits the ACTIVE script under its own "
            "name; it never guesses one.)"
        )
        unrecognised = utilities.server_report.unrecognised_servers(sessions)

    with connect(config, args, rules=False, mail=True) as sessions:
        mail = utilities.reports.probe_mail(sessions)

        print(f"\n{words.mail_service}: connected")
        print(f"  delimiter: {mail.delimiter!r}")
        print(
            f"  folders:   {mail.folder_count} "
            f"({mail.folder_count - len(mail.unsubscribed)} subscribed)"
        )

        if mail.unsubscribed:
            print(
                f"  not subscribed (exist, but webmail will not show them): "
                f"{', '.join(mail.unsubscribed)}"
            )

        print_facts(mail.facts)
        unrecognised += utilities.server_report.unrecognised_servers(sessions)

    if unrecognised:
        services = " or the ".join(
            service_word(words, half) for half in unrecognised
        )
        print(
            f"\nServers:   mailctl does not recognise the {services} "
            f"server -- 'mailctl probe --report' prints a redacted issue body"
        )

    print_baseline_summary(config, args)

    for note in words.notes:
        print(f"\nNote: {note}")

    return 0


# ----------------------------------------------------------------------------
def print_baseline_summary(config, args) -> None:
    """One line on drift since the saved baseline, where there is one.

    Warn-only: 'test' neither fails nor stops over drift, or over a
    baseline it cannot check; the line says which it was.
    """
    try:
        if utilities.baseline.find_baseline(config) is None:
            return

        with connect(config, args, mail=True) as sessions:
            check = utilities.baseline.check_baseline(sessions, config)

    except MailctlError as exc:
        reason = error_text(exc).split("\n")[0]

        print(f"\nBaseline:  not checked -- {reason}")

        return

    taken = check.stored.taken.strftime(utilities.reports.TIME_FORMAT)

    if not check.drift:
        print(f"\nBaseline:  no drift since {taken}")

        return

    print(
        f"\nBaseline:  {drift_counts(check.drift)} since {taken} -- "
        f"'mailctl check-baseline' lists them"
    )


# ----------------------------------------------------------------------------
def cmd_probe(args) -> int:
    """Print what the servers say about themselves; change nothing."""
    config = configure(args)
    words = utilities.reports.wording(config)

    # With --json or --report only the document goes to stdout, so it can
    # be read or saved whole; --verbose's chatter goes to stderr instead.
    document = args.json or args.report
    progress = progress_from_args(args, sys.stderr if document else None)

    with engine.connect(config, mail=True, progress=progress) as sessions:
        if args.report:
            report = utilities.server_report.build_server_report(
                sessions, config
            )

        else:
            record = utilities.reports.probe_servers(sessions, config)

    if args.report:
        return print_server_report(report, words)

    if args.json:
        args.stdout.write(utilities.reports.dump_probe(record))

        return 0

    print_probe(record, words)
    print("\n--json prints it as a versioned document.")

    unrecognised = utilities.server_report.unrecognised_halves(
        record.rules, record.mail
    )

    if unrecognised:
        services = " or the ".join(
            service_word(words, half) for half in unrecognised
        )
        print(
            f"\nmailctl does not recognise the {services} server, so it "
            f"works with the plain protocol there. --report prints a "
            f"redacted issue body to tell us about it."
        )

    return 0


# ----------------------------------------------------------------------------
def service_word(words, half: str) -> str:
    """The provider's name for one half."""
    if half == utilities.server_report.RULES:
        return words.rules_service

    return words.mail_service


# ----------------------------------------------------------------------------
def print_server_report(report, words) -> int:
    """The redacted issue body on stdout; how to file it on stderr."""
    if report is None:
        print(
            f"mailctl recognises the {words.rules_service} and "
            f"{words.mail_service} servers here; there is nothing to "
            f"report.",
            file=sys.stderr,
        )

        return 0

    body = utilities.server_report.render_server_report(report)

    # The body holds what the servers sent; safe_text keeps it from acting
    # on the terminal it is printed to.
    sys.stdout.write(safe_text(body))
    print(
        f"\nNothing was sent. Read the report above, remove anything you "
        f"would rather not share, then file it at {NEW_ISSUE_URL} titled "
        f"{safe_line(report.title)!r}; with the GitHub CLI and the body "
        f"saved to FILE:\n  gh issue create -R {ISSUES} --title "
        f"{shlex.quote(safe_line(report.title))} --body-file FILE",
        file=sys.stderr,
    )

    return 0


# ----------------------------------------------------------------------------
def print_probe(record, words, *, account_recorded: bool = True) -> None:
    """A probe, one value per line: when, where, and each half."""
    taken = record.taken.strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Probe of {record.provider}, taken {taken}")

    for fact in record.endpoints:
        print(f"{fact.label + ':':<10} {fact.text}")

    if record.rules is not None:
        print_server(words.rules_service, record.rules)

        if record.extensions:
            print(f"  {words.extensions}: {len(record.extensions)}")

            for name in record.extensions:
                print(f"    {safe_line(name)}")

        if account_recorded:
            active = safe_line(record.active_rule_set or "")

        else:
            active = "(not recorded for this account)"

        print(f"  active script: {active}")

    if record.mail is not None:
        print_server(words.mail_service, record.mail)
        print(f"  delimiter: {safe_line(record.delimiter or '')!r}")
        print("  namespaces:")

        for space in record.namespaces:
            print(
                f"    {space.kind:<8}  prefix {safe_line(space.prefix)!r}  "
                f"delimiter {safe_line(space.delimiter or '')!r}"
            )

        if not record.namespaces:
            print("    (none reported)")


# ----------------------------------------------------------------------------
def cmd_save_baseline(args) -> int:
    """Save what the servers say now as this host's baseline file."""
    config = configure(args)

    with connect(config, args, mail=True) as sessions:
        plan = utilities.baseline.plan_save_baseline(sessions, config)
        words = sessions.wording

    taken = plan.record.taken.strftime(utilities.reports.TIME_FORMAT)

    if plan.previous is None:
        if args.dry_run:
            print(
                f"[dry-run] would save the baseline for {plan.host}, taken "
                f"{taken}, to {plan.path}; nothing was written."
            )

            return 0

        utilities.baseline.execute_save_baseline(plan)
        print(
            f"Saved the baseline for {plan.host}, taken {taken}, to "
            f"{plan.path}."
        )

        return 0

    before = saved_on(plan.previous, config.user)

    print(f"The baseline for {plan.host} ({plan.path}) was taken {before}.")

    if plan.drift:
        print("\nSince then:")
        print_drift(plan.drift, words)
        print_unknown_requires(plan.requires_known)

    else:
        print(
            "\nThe servers say what they said then; saving only moves its "
            "date."
        )

    print("\n--- baseline diff ---")
    print(safe_text("\n".join(plan.diff)))
    print("--- end diff ---")

    if args.dry_run:
        print("\n[dry-run] the baseline was NOT replaced.")

        return 0

    if not confirm(
        f"Replace the baseline for {plan.host} taken {before}?", args.yes
    ):
        print("Aborted; the baseline was left as it was.")

        return 0

    utilities.baseline.execute_save_baseline(plan)
    print(
        f"Saved the baseline for {plan.host}, taken {taken}, to {plan.path}."
    )

    return 0


# ----------------------------------------------------------------------------
def cmd_show_baseline(args) -> int:
    """Print the saved baseline for the configured host; connect to nothing."""
    config = configure(args)
    path = utilities.baseline.baseline_path(config)
    baseline = utilities.baseline.read_baseline(path)

    if baseline is None:
        raise MailctlError(
            f"no baseline has been saved for {path.stem} (looked for "
            f"{path}); 'mailctl save-baseline' records one"
        )

    # Written as stored: the file is already one versioned document.
    if args.json:
        args.stdout.write(baseline.text)

        return 0

    recorded = config.user in baseline.accounts

    print(f"Baseline for {baseline.host}: {baseline.path}")
    users = ", ".join(safe_line(user) for user in baseline.accounts)

    print(f"Accounts:  {users}")

    if not recorded:
        print(f"  ({config.user} has no part in it; the server's part only)")

    print()
    print_probe(
        baseline.record_for(config.user),
        utilities.reports.wording(config),
        account_recorded=recorded,
    )
    print("\n--json prints the file as it is stored.")

    return 0


# ----------------------------------------------------------------------------
def cmd_check_baseline(args) -> int:
    """Compare what the servers say now with the saved baseline."""
    config = configure(args)
    progress = progress_from_args(args, sys.stderr if args.json else None)

    with engine.connect(config, mail=True, progress=progress) as sessions:
        check = utilities.baseline.check_baseline(sessions, config)
        words = sessions.wording

    code = drift_exit(check.drift)

    # The exit status still says whether there was drift under --json.
    if args.json:
        emit_json(args, drift_document(check))

        return code

    before = check.stored.taken.strftime(utilities.reports.TIME_FORMAT)

    print(
        f"Baseline for {check.baseline.host}, taken {before}: "
        f"{check.baseline.path}"
    )

    if not check.account_recorded:
        print(
            f"  ({config.user} has no part in it, so the active script was "
            f"not compared)"
        )

    if not check.drift:
        print("\nNo drift: the servers say what they said then.")

        return code

    print(f"\n{drift_counts(check.drift)}:")
    print_drift(check.drift, words)

    print_unknown_requires(check.requires_known)

    print(
        "\nNothing was refused: the baseline records, it does not decide. "
        "'mailctl save-baseline' replaces it, after showing what changed."
    )

    return code


# ----------------------------------------------------------------------------
def print_unknown_requires(known: bool) -> None:
    """Say so where a lost extension was counted serious unread."""
    if not known:
        print(
            "\nThe active script could not be parsed, so every extension "
            "that is gone is counted as one it requires."
        )


# ----------------------------------------------------------------------------
def drift_exit(drift) -> int:
    """The exit status a drift report ends with, for a script to test."""
    if any(item.severity == utilities.baseline.SERIOUS for item in drift):
        return DRIFT_SERIOUS_EXIT

    return DRIFT_INFO_EXIT if drift else 0


# ----------------------------------------------------------------------------
def drift_counts(drift) -> str:
    """``N serious, M informational change(s)``."""
    serious = sum(
        item.severity == utilities.baseline.SERIOUS for item in drift
    )

    return f"{serious} serious, {len(drift) - serious} informational change(s)"


# ----------------------------------------------------------------------------
def saved_on(baseline, user: str) -> str:
    """When ``user``'s part of a baseline was taken, else the server's."""
    record = baseline.record_for(user)

    return record.taken.strftime(utilities.reports.TIME_FORMAT)


# ----------------------------------------------------------------------------
def drift_document(check) -> dict:
    """A drift check as the versioned document ``--json`` prints."""
    taken = utilities.reports.TIME_FORMAT

    return {
        "version": DRIFT_VERSION,
        "host": check.baseline.host,
        "baseline": str(check.baseline.path),
        "baseline_taken": check.stored.taken.strftime(taken),
        "taken": check.record.taken.strftime(taken),
        "account_recorded": check.account_recorded,
        "requires_known": check.requires_known,
        "serious": len(check.serious),
        "informational": len(check.drift) - len(check.serious),
        "drift": [
            {
                "severity": item.severity,
                "kind": item.kind,
                "half": item.half,
                "name": item.name,
                "before": item.before,
                "after": item.after,
            }
            for item in check.drift
        ],
    }


# ----------------------------------------------------------------------------
def print_drift(drift, words) -> None:
    """One line per difference, serious ones marked ``!``."""
    for item in drift:
        marker = "!" if item.severity == utilities.baseline.SERIOUS else "-"

        print(f"  {marker} {describe_drift(item, words)}")


# ----------------------------------------------------------------------------
def describe_drift(item, words) -> str:
    """One difference as a sentence: what changed, and what it means."""
    kinds = utilities.baseline
    serious = item.severity == kinds.SERIOUS
    service = {
        kinds.RULES: words.rules_service,
        kinds.MAIL: words.mail_service,
    }.get(item.half, "")
    name = shown(item.name)
    before, after = shown(item.before), shown(item.after)

    if item.kind == kinds.ACTIVE_RULE_SET:
        return (
            f"the active script is now {after}, not {before} -- mailctl "
            f"edits the active script, so it may no longer be the one "
            f"your rules are in"
        )

    if item.kind == kinds.DELIMITER:
        return (
            f"the folder delimiter is now {after}, not {before} -- every "
            f"folder a rule files into is suspect"
        )

    if item.kind == kinds.EXTENSION_REMOVED:
        why = (
            "the active script requires it, so its rules may now fail"
            if serious
            else "the active script does not require it"
        )

        return f"{words.extensions}: {name} is gone -- {why}"

    if item.kind == kinds.EXTENSION_ADDED:
        return (
            f"{words.extensions}: {name} is new -- possibly something "
            f"mailctl could use"
        )

    if item.kind == kinds.NAMESPACE:
        why = " -- a new folder is placed under it" if serious else ""

        return (
            f"{service} {item.name} namespace is now {after}, not "
            f"{before}{why}"
        )

    relied = " -- mailctl behaves differently without it" if serious else ""

    if item.kind == kinds.CAPABILITY_REMOVED:
        return f"{service} capability {name} is gone{relied}"

    if item.kind == kinds.CAPABILITY_ADDED:
        uses = " -- mailctl uses it where it is offered" if serious else ""

        return f"{service} capability {name} is new{uses}"

    if item.kind == kinds.CAPABILITY_CHANGED:
        return (
            f"{service} capability {name} is now {after}, not {before}{relied}"
        )

    if item.kind == kinds.IDENTITY:
        return (
            f"{service} identity {name} is now {after}, not {before} -- the "
            f"clearest sign of a migration"
        )

    if item.kind == kinds.STAGE:
        then = "after" if item.before else "before"
        now = "after" if item.after else "before"

        return (
            f"{service} capabilities were read {then} login then and "
            f"{now} login now, so they were not compared"
        )

    if item.kind == kinds.HALF:
        then = "probed" if item.before else "not probed"
        now = "probed" if item.after else "not probed"

        return f"{service} was {then} then and is {now} now"

    return (
        f"{item.name} endpoint is now {after}, not {before} -- the "
        f"baseline may describe another server"
    )


# ----------------------------------------------------------------------------
def shown(value) -> str:
    """A drift value for a sentence: quoted, escaped, or ``(none)``."""
    if value is None:
        return "(none)"

    if isinstance(value, list):
        spaces = [
            f"prefix {safe_line(prefix)!r} delimiter "
            f"{safe_line(delimiter or '')!r}"
            for prefix, delimiter in value
        ]

        return "; ".join(spaces) or "(none)"

    return f"'{safe_line(str(value))}'"


# ----------------------------------------------------------------------------
def print_server(service: str, server) -> None:
    """One half's identity and capabilities, one value per line."""
    stage = "after" if server.after_login else "before"

    print(f"\n{service} (capabilities read {stage} login):")
    print("  identity:")

    for name, value in server.identity:
        print(f"    {safe_line(name)}: {safe_line(value)}")

    if not server.identity:
        print("    (none reported)")

    print(f"  capabilities: {len(server.capabilities)}")

    for item in server.capabilities:
        value = "" if item.value is None else f" {safe_line(item.value)}"

        print(f"    {safe_line(item.name)}{value}")


# ----------------------------------------------------------------------------
def print_facts(facts) -> None:
    """One labelled fact per line, a long one's lines under its first."""
    for fact in facts:
        label = f"{fact.label}:"
        first, *rest = fact.text.split("\n")
        indent = " " * (3 + max(len(label), 10))

        print(f"  {label:<10} {first}")

        for line in rest:
            print(f"{indent}{line}")


# ----------------------------------------------------------------------------
def print_extension_table(states) -> None:
    """Name, available or not, and enabled or disabled where available."""
    width = max(len(state.name) for state in states)

    for state in states:
        marker = "*" if state.required else " "
        available = "available" if state.advertised else "unavailable"

        if state.enabled is None:
            shown = ""

        elif state.enabled:
            shown = "enabled"

        else:
            shown = f"disabled ({state.disabled_by.describe()})"

        line = f"    {marker} {state.name:<{width}}  {available:<11}  {shown}"

        print(line.rstrip())


# ----------------------------------------------------------------------------
def resolve_password(config) -> tuple[str, MailctlError | None]:
    """Read the password now, so the report says how reading it went.

    Returns the state to print and the error to raise once the settings
    have been shown. A source that is configured but unusable -- a file
    refused for its mode, a command that fails -- is reported as such
    rather than as "set" above its own refusal (#61).
    """
    state = config.password_state()

    try:
        config.password()

    except MailctlError as exc:
        return "not usable", exc

    # Nothing configured and the prompt answered: the state read before
    # asking was "unset", which is no longer true.
    return ("set" if state == "unset" else state), None


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
def cmd_add(args) -> int:
    """Save a rule into the active script; mail already delivered is left
    alone -- that is ``apply``'s."""
    config = configure(args)
    reject_forbidden(config, args)
    explicit = criteria_given(args)

    if args.folder is not None and args.like is None:
        raise MailctlError("--folder needs --like UID")

    if args.like is not None and args.no_imap:
        raise MailctlError(
            "--like reads the message over IMAP, so it cannot be combined "
            "with --no-imap"
        )

    spec = actions_from_args(args)

    # Before connecting: a rule the provider cannot express costs no login.
    utilities.rules.check_rule(config, rule_request(args, explicit, spec))

    with connect(config, args, mail=not args.no_imap) as sessions:
        criteria = explicit

        if args.like is not None:
            criteria = message_like(sessions, config, args, explicit).criteria

            print()

        folder = prepare_folder(sessions, config, args)

        missing = utilities.rules.missing_extensions(sessions, spec, folder)

        warn_missing_extensions(missing, sessions.wording)

        plan = utilities.rules.plan_rule(
            sessions, config, rule_request(args, criteria, spec), folder
        )

        if args.json:
            return emit_json(
                args,
                json_output.plan(
                    "add",
                    **json_output.change(plan.before != plan.after, plan.diff),
                    rule=plan.name,
                    criteria=criteria.to_dict(),
                    actions=json_output.actions(spec),
                    folder=json_output.folder_plan(folder),
                    missing_extensions=list(missing),
                    placement=json_output.placement(plan.placement),
                    **json_output.activation(plan),
                ),
            )

        print(f"\nRule {plan.name!r} on script {plan.script!r}:")
        print(f"  when:  {criteria.describe()}")
        print(f"  then:  {plan.summary}")

        # The placement findings come before the diff. A diff shows what
        # changes; it cannot show that the change lands after a rule whose
        # stop means it will never be reached.
        print_placement(plan.placement)
        print_script_diff(plan.diff)
        print_activation(plan)

        if args.dry_run:
            print("\n[dry-run] the script was NOT uploaded.")

            return 0

        utilities.rules.execute_script_change(
            sessions, config, plan, render_event
        )

    print(ADD_LEAVES_MAIL)

    return 0


# ----------------------------------------------------------------------------
def rule_request(args, criteria: Criteria, spec: ActionSpec) -> RuleRequest:
    """The rule ``add`` asks for, from its flags and resolved criteria."""
    return RuleRequest(
        criteria=criteria,
        actions=spec,
        name=args.name,
        script=args.script,
        replace=args.replace,
        placement=placement_from_args(args),
        activate=args.activate,
    )


# ----------------------------------------------------------------------------
def cmd_apply(args) -> int:
    """Act on mail already delivered; touch no Sieve script."""
    config = configure(args)
    reject_forbidden(config, args)
    explicit = criteria_given(args)
    spec = actions_from_args(args)

    with connect(config, args, rules=False, mail=True) as sessions:
        folder = utilities.folders.plan_folder(
            sessions,
            config,
            args.fileinto,
            create=args.create_folder,
            subscribe=not args.no_subscribe,
        )

        show_folder_plan(folder)
        utilities.mail.require_mail_action(folder, spec)

        if folder.status == utilities.folders.FOLDER_MISSING:
            raise MailctlError(
                f"target folder {folder.folder!r} does not exist; pass "
                f"--create-folder to create it"
            )

        if folder.status == utilities.folders.FOLDER_IMAP_CREATE:
            announce_folder_creation(folder, args.dry_run)

        criteria = explicit

        if args.like is not None:
            print()

            criteria = message_like(sessions, config, args, explicit).criteria

            print()

        print(f"Criteria: {criteria.describe()}")

        apply_to_existing(sessions, config, criteria, args, spec, folder)

    return 0


# ----------------------------------------------------------------------------
def cmd_remove_rule(args) -> int:
    """Remove a named rule from the active script and re-upload."""
    config = configure(args)

    with connect(config, args) as sessions:
        plan = utilities.rules.plan_removal(
            sessions, args.rule_name, args.script, args.activate
        )

        if args.json:
            return emit_json(
                args,
                json_output.plan(
                    "remove-rule",
                    **json_output.change(plan.before != plan.after, plan.diff),
                    rule=plan.rule,
                    **json_output.activation(plan),
                ),
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

        utilities.rules.execute_script_change(
            sessions, config, plan, render_event
        )

    return 0


# ----------------------------------------------------------------------------
def cmd_move_rule(args) -> int:
    """Move a named rule to a new position, leaving it otherwise unchanged."""
    config = configure(args)
    utilities.rules.check_move(config)

    with connect(config, args) as sessions:
        plan = utilities.rules.plan_move(
            sessions,
            args.rule_name,
            placement_from_args(args),
            args.script,
            args.activate,
        )

        if args.json:
            return emit_json(
                args,
                json_output.plan(
                    "move-rule",
                    **json_output.change(plan.changes, plan.diff),
                    rule=plan.rule,
                    from_position=plan.from_index + 1,
                    to_position=plan.to_index + 1,
                    count=plan.count,
                    placement=json_output.placement(plan.placement),
                    **json_output.activation(plan),
                ),
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

        utilities.rules.execute_script_change(
            sessions, config, plan, render_event
        )

    return 0


# ----------------------------------------------------------------------------
def cmd_optimize_rules(args) -> int:
    """Propose a better arrangement of the rules, and apply it if asked."""
    config = configure(args)
    utilities.optimize.check_optimize(config)
    kinds = [
        kind for kind in utilities.optimize.KINDS if kind not in args.skip
    ]

    with connect(config, args) as sessions:
        plan = utilities.optimize.plan_optimize(
            sessions, args.script, kinds, args.activate
        )

        if args.json:
            return emit_json(args, json_output.optimize_plan(plan))

        print_optimize(plan)

        if not plan.changes:
            return 0

        if args.dry_run:
            print("\n[dry-run] the script was NOT uploaded.")

            return 0

        proposals = plan.proposals
        count = (
            len(proposals.removals)
            + len(proposals.reorders)
            + len(proposals.merges)
        )

        if not confirm(
            f"Apply {count} change{'s' if count != 1 else ''} to "
            f"{plan.script!r}?",
            args.yes,
        ):
            print("Aborted; nothing was changed.")

            return 0

        utilities.optimize.execute_optimize(
            sessions, config, plan, render_event
        )

    return 0


# ----------------------------------------------------------------------------
def print_optimize(plan) -> None:
    """Show what would be removed, moved, and merged, what is left alone
    for want of certainty, and the diff."""
    proposals = plan.proposals
    count = len(plan.rules)
    skipped = [
        kind for kind in utilities.optimize.KINDS if kind not in plan.kinds
    ]

    print(
        f"Script {plan.script!r}: {count} rule{'s' if count != 1 else ''} "
        f"read."
    )

    if skipped:
        print(f"Not considered: {', '.join(skipped)}.")

    if proposals.removals:
        print(
            "\nRemove -- it can never run, and an earlier rule that stops "
            "does exactly the same to all of its mail:"
        )

        for item in proposals.removals:
            print(
                f"  {safe_line(item.rule)!r}  (covered by "
                f"{safe_line(item.covered_by)!r})"
            )

    if proposals.reorders:
        print(
            "\nMove -- a broader rule ahead of it stops all of its mail, so "
            "it never runs. Moved, its mail gets its own actions instead:"
        )

        for item in proposals.reorders:
            print(
                f"  {safe_line(item.rule)!r} to just before "
                f"{safe_line(item.before)!r}"
            )

    if proposals.merges:
        print(
            "\nMerge -- the same test on the same header, with the same "
            "actions; one rule with a key list files exactly the same mail:"
        )

        for item in proposals.merges:
            absorbed = ", ".join(
                repr(safe_line(name)) for name in item.absorbed
            )
            print(
                f"  {absorbed} into {safe_line(item.into)!r}, which keeps its "
                f"name: {safe_line(item.header)} {item.match_type}"
            )

            for key in item.keys:
                print(f"      {safe_line(key)!r}")

    if proposals.uncertain:
        print("\nLeft alone -- mailctl will not change these on a guess:")

        for item in proposals.uncertain:
            print(f"  ? {safe_line(item.reason)}")

    if not plan.changes:
        print(f"\nNothing to change in {plan.script!r}.")

        return

    print_script_diff(plan.diff)
    print_activation(plan)


# ----------------------------------------------------------------------------
def cmd_switch_rule(args) -> int:
    """Switch a named rule off or on, keeping it in the script."""
    config = configure(args)
    utilities.rules.check_switch(config)
    verb = "Enable" if args.enable else "Disable"
    state = "enabled" if args.enable else "disabled"

    with connect(config, args) as sessions:
        plan = utilities.rules.plan_switch(
            sessions, args.rule_name, args.enable, args.script, args.activate
        )

        if args.json:
            return emit_json(
                args,
                json_output.plan(
                    args.command,
                    **json_output.change(plan.changes, plan.diff),
                    rule=plan.rule,
                    enable=plan.enable,
                    **json_output.activation(plan),
                ),
            )

        if not plan.changes:
            print(
                f"Rule {plan.rule!r} is already {state} in "
                f"{plan.script!r}; nothing to change."
            )

            return 0

        print(f"{verb} rule {plan.rule!r} in script {plan.script!r}:")
        print_script_diff(plan.diff)
        print_activation(plan)

        if args.dry_run:
            print("\n[dry-run] the script was NOT uploaded.")

            return 0

        if not confirm(
            f"{verb} rule {plan.rule!r} in {plan.script!r}?", args.yes
        ):
            print("Aborted; nothing was changed.")

            return 0

        utilities.rules.execute_script_change(
            sessions, config, plan, render_event
        )

    return 0


# ----------------------------------------------------------------------------
def print_message(message, uid: int, folder: str, file=None) -> None:
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
    print(f"Message uid {uid} in {folder!r}:", file=file)

    for header in IDENTIFYING_HEADERS:
        raw = message.get(header)

        if not raw:
            continue

        value = clip(decode_header_value(raw), HEADER_WIDTH)

        print(f"  {header + ':':<9}{value}", file=file)


# ----------------------------------------------------------------------------
def status_marks(message) -> str:
    """The listing's one-letter marks for a message; see MARK_LEGEND."""
    flags = {flag.lower() for flag in message.flags}

    marks = "" if "\\seen" in flags else "N"
    marks += "".join(mark for flag, mark in FLAG_MARKS if flag in flags)

    return marks + ("@" if message.has_attachments else "")


# ----------------------------------------------------------------------------
def cmd_search(args) -> int:
    """List the newest messages in a folder, optionally filtered; or, with
    --build-filter, print the filter the criteria make."""
    config = configure(args)
    criteria = criteria_from_args(args)

    if args.derive is not None and args.like is None:
        raise MailctlError("--derive needs --like UID")

    if args.uids_only and (args.json or args.build_filter):
        raise MailctlError(
            "--uids-only prints a listing's UIDs; it cannot be combined with "
            "--json or --build-filter"
        )

    if args.raw and (args.build_filter or args.like is not None):
        raise MailctlError(
            "--raw is a query, not criteria; it cannot be combined with "
            "--like or --build-filter"
        )

    if args.reverse and args.sort is None:
        raise MailctlError("--reverse reverses --sort; give --sort too")

    if args.sort is not None and args.build_filter:
        raise MailctlError(
            "--sort orders a listing; --build-filter prints a filter, which "
            "has no order"
        )

    order = None if args.sort is None else SortOrder(args.sort, args.reverse)

    if args.like is None:
        if args.build_filter:
            return print_filter(args, criteria)

        with connect(config, args, rules=False, mail=True) as sessions:
            listing = utilities.messages.list_messages(
                sessions,
                config.source_folder,
                criteria=criteria,
                raw=args.raw,
                limit=args.limit,
                order=order,
            )

        return show_listing(args, listing)

    # With --json only the document goes to stdout, so what identifies the
    # message goes to stderr, where a person still sees it.
    shown = sys.stderr if args.json else None

    with connect(config, args, rules=False, mail=True) as sessions:
        like = message_like(sessions, config, args, criteria, shown)

        if args.build_filter:
            if not args.json:
                print()

            return print_filter(args, like.criteria)

        print(f"\nCriteria: {like.criteria.describe()}\n")

        listing = utilities.messages.list_messages(
            sessions,
            like.message.folder,
            criteria=like.criteria,
            raw=args.raw,
            limit=args.limit,
            order=order,
        )

    return show_listing(args, listing)


# ----------------------------------------------------------------------------
def print_filter(args, criteria: Criteria) -> int:
    """Print the filter criteria make, and save nothing."""
    if not criteria:
        raise MailctlError(
            "--build-filter needs criteria: give --like UID, or criteria "
            "flags (--from/--to/--cc/--subject/--list-id/--header/--body/"
            "--since/--before/--older-than/--unread/--flagged)"
        )

    if args.json:
        args.stdout.write(dump_filter(criteria))

        return 0

    print("Filter (criteria only; nothing was saved):")
    print(f"  when:  {criteria.describe()}")
    print("\n--json prints it as a filter document.")

    return 0


# ----------------------------------------------------------------------------
def show_listing(args, listing) -> int:
    """Hand a search's listing over in the form asked for."""
    if args.json:
        return emit_json(args, json_output.message_listing(listing))

    if args.uids_only:
        args.stdout.writelines(f"{item.uid}\n" for item in listing.messages)

        return 0

    return print_listing(listing)


# ----------------------------------------------------------------------------
def print_listing(listing) -> int:
    """Print a search's listing, newest first or in the order asked for."""
    if not listing.messages:
        print(f"No messages found in {safe_line(listing.folder)!r}.")

        return 0

    print(
        f"{len(listing.messages)} message(s) in "
        f"{safe_line(listing.folder)!r}, {order_words(listing.order)}:"
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

    if listing.more and listing.order is None:
        print(
            f"Showing the {len(listing.messages)} newest; there may be "
            f"more -- raise --limit to see them."
        )

    elif listing.more:
        print(
            f"Showing the first {len(listing.messages)} in that order; there "
            f"may be more -- raise --limit to see them."
        )

    return 0


# How a listing's order reads, ascending and reversed, by sort key.
ORDER_WORDS = {
    "size": ("by size, smallest first", "by size, largest first"),
    "sent": (
        "by sent date (Date header), oldest first",
        "by sent date (Date header), newest first",
    ),
    "received": (
        "by date received, oldest first",
        "by date received, newest first",
    ),
}


# ----------------------------------------------------------------------------
def order_words(order: SortOrder | None) -> str:
    """How a listing in ``order`` is ordered, for its heading."""
    if order is None:
        return "newest first"

    return ORDER_WORDS[order.key][order.reverse]


# ----------------------------------------------------------------------------
def cmd_view(args) -> int:
    """Show one message: headers, text body, and what is attached."""
    config = configure(args)

    with connect(config, args, rules=False, mail=True) as sessions:
        content = utilities.messages.read_message(
            sessions, config.source_folder, args.uid
        )

    if args.json:
        return emit_json(args, json_output.message(content))

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


# ----------------------------------------------------------------------------
def cmd_mark(args) -> int:
    """Set or clear read, flagged, and keywords on messages by UID."""
    config = configure(args)

    add, remove = utilities.flags.mark_flags(
        read=args.read,
        flagged=args.flagged,
        add_keywords=args.keywords,
        remove_keywords=args.no_keywords,
    )

    with connect(config, args, rules=False, mail=True) as sessions:
        plan = utilities.flags.plan_mark(
            sessions, config.source_folder, args.uids, add, remove
        )

        if args.json:
            return emit_json(args, json_output.mark_plan(plan))

        print_mark_plan(plan)

        if plan.is_empty:
            print("Nothing to change: every message already looks as asked.")

            return 0

        count = len(plan.changing)

        if args.dry_run:
            print(f"[dry-run] would mark {count} message(s); none was marked.")

            return 0

        if not confirm(
            f"Mark {count} message(s) in {plan.folder!r}?", args.yes
        ):
            print("Aborted; no messages were marked.")

            return 0

        utilities.flags.execute_mark(sessions, plan)

    print(f"Marked {count} message(s) in {plan.folder!r}.")

    return 0


# ----------------------------------------------------------------------------
def print_mark_plan(plan) -> None:
    """Print what marking asks for, and what it changes on each message."""
    asked = describe_marks(plan.add, plan.remove)

    print(f"Marking in {safe_line(plan.folder)!r}: {asked}")

    for message in plan.messages:
        change = describe_marks(message.add, message.remove) or "no change"
        now = " ".join(message.flags) or "no flags"

        print(f"  uid {message.uid:<8} {change}  (now: {safe_line(now)})")


# ----------------------------------------------------------------------------
def describe_marks(add, remove) -> str:
    """``set A, B; clear C`` -- the flags a mark adds and removes."""
    parts = []

    if add:
        parts.append(f"set {', '.join(add)}")

    if remove:
        parts.append(f"clear {', '.join(remove)}")

    return "; ".join(parts)


# Rows 'senders' shows unless --top says otherwise; --top 0 shows every one.
DEFAULT_SENDERS_TOP = 20

# How 'senders' names a row with no key, and the search flag, per grouping,
# that builds a filter for a row -- the domain one prefixed with '@'.
NO_SENDER_KEY = {
    "address": "(no address)",
    "domain": "(no domain)",
    "list-id": "(no list-id)",
}
SENDER_FILTER_FLAG = {
    "address": ("--from", ""),
    "domain": ("--from", "@"),
    "list-id": ("--list-id", ""),
}


# ----------------------------------------------------------------------------
def cmd_senders(args) -> int:
    """Count a folder's mail by sender, busiest first, with unread."""
    config = configure(args)
    criteria = criteria_from_args(args)

    with connect(config, args, rules=False, mail=True) as sessions:
        report = utilities.senders.count_senders(
            sessions,
            config.source_folder,
            criteria=criteria,
            by=args.by,
            top=args.top or None,
            minimum=args.minimum,
            max_messages=args.max_messages,
        )

    if args.json:
        return emit_json(args, json_output.sender_report(report))

    return print_senders(report)


# ----------------------------------------------------------------------------
def print_senders(report) -> int:
    """Print a sender report as a table, and how to filter one row."""
    folder = safe_line(report.folder)

    if not report.messages:
        print(f"No messages found in {folder!r}.")

        return 0

    print(
        f"{report.messages} message(s) in {folder!r}, {report.unread} "
        f"unread; {report.groups} group(s) by {report.by}, busiest first:"
    )
    print(f"{'Total':>7}  {'Unread':>7}  {'Unread%':>7}  Sender")

    for row in report.senders:
        label = row.key if row.key is not None else NO_SENDER_KEY[report.by]

        if row.name:
            label = f"{label} ({row.name})"

        print(
            f"{row.total:>7}  {row.unread:>7}  "
            f"{row.unread_percent:>6.0f}%  {clip(label, 70)}"
        )

    if len(report.senders) < report.groups:
        print(
            f"Showing {len(report.senders)} of {report.groups}; --top N "
            f"shows more (0 for every one), --min N hides the small ones."
        )

    example = next((row.key for row in report.senders if row.key), None)

    if example is not None:
        flag, prefix = SENDER_FILTER_FLAG[report.by]
        value = shlex.quote(safe_line(prefix + example))

        print(
            f"To build a filter for a row: mailctl search {flag} {value} "
            f"--build-filter"
        )

    return 0


# ############################################################################
# Argument parsing
# ############################################################################


# ----------------------------------------------------------------------------
def global_parser() -> argparse.ArgumentParser:
    """Flags accepted both before and after the subcommand.

    ``--verbose mailctl add`` and ``mailctl add --verbose`` should both
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
def connection_parser(
    offer: engine.ProviderCapabilities, words: Wording
) -> argparse.ArgumentParser:
    """Flags shared by every subcommand that connects to the server.

    The provider's own connection settings are offered only when ``offer``
    -- its declared capabilities -- names them, with the help it gives.
    """
    parser = argparse.ArgumentParser(add_help=False)

    def setting(name: str) -> dict:
        if name not in offer.settings:
            return {"help": argparse.SUPPRESS}

        return {"help": offer.settings[name]} if offer.settings[name] else {}

    group = parser.add_argument_group("connection")
    group.add_argument(
        "--provider",
        help="the mail host, by provider name; default mxroute",
    )
    group.add_argument("--host", **setting("host"))
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
        help="read MAILCTL_* settings from a dotenv-style file (default "
        ".env in the current directory); they beat the environment and "
        "the config file, and lose to a flag. A file setting "
        "MAILCTL_PASSWORD must be mode 0600 (or 0400)",
    )
    group.add_argument("--imap-host", dest="imap_host", **setting("imap_host"))
    group.add_argument(
        "--imap-port", dest="imap_port", type=int, **setting("imap_port")
    )
    group.add_argument(
        "--sieve-port", dest="sieve_port", type=int, **setting("sieve_port")
    )
    group.add_argument(
        "--sieve-tls",
        dest="sieve_tls",
        choices=SIEVE_TLS_MODES,
        **setting("sieve_tls"),
    )
    group.add_argument(
        "--backup-dir",
        dest="backup_dir",
        help="where script backups are written, both the automatic "
        "pre-upload one and 'mailctl backup'; default "
        "$XDG_CONFIG_HOME/mailctl/backups",
    )
    group.add_argument(
        "--disable-extension",
        dest="disable_extension",
        action="append",
        metavar="NAME",
        help=f"never emit this {words.extension}, even if the server "
        "advertises it; repeatable, and replaces "
        "MAILCTL_DISABLED_EXTENSIONS / disabled_extensions for this run; "
        "'none' disables nothing. 'mailctl test' lists the names"
        if offer.extensions
        else argparse.SUPPRESS,
    )

    return parser


# ----------------------------------------------------------------------------
def criteria_parser(
    words: Wording, dated_before: bool = True
) -> argparse.ArgumentParser:
    """The criteria flags shared by add / apply / search.

    ``dated_before`` False leaves out ``--before DATE``, for ``add``, whose
    ``--before RULE`` places the rule. A date filter is refused on ``add``
    anyway, so nothing is lost but the spelling.
    """
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
        "--body",
        action="append",
        metavar="TEXT",
        help="match text in the message body, always as a substring; "
        "repeatable",
    )
    group.add_argument(
        "--since",
        metavar="DATE",
        help="only mail received on or after DATE (YYYY-MM-DD). Like "
        "every date and state filter, ANDed with the other criteria, and "
        "for mail already delivered only: refused by add",
    )

    if dated_before:
        group.add_argument(
            "--before",
            dest="received_before",
            metavar="DATE",
            help="only mail received before DATE (YYYY-MM-DD)",
        )

    group.add_argument(
        "--older-than",
        dest="older_than",
        metavar="AGE",
        help="only mail received more than AGE ago: days or weeks, as "
        "30d or 3w",
    )
    group.add_argument(
        "--unread", action="store_true", help="only unread mail"
    )
    group.add_argument(
        "--flagged", action="store_true", help="only flagged mail"
    )
    group.add_argument(
        "--match",
        choices=MATCH_MODES,
        help="combine criteria with OR (any) or AND (all); default any",
    )
    group.add_argument(
        "--compare",
        choices=COMPARE_OPS,
        help="comparison used for every criterion; default contains. "
        "Note that 'is' and 'matches' test the WHOLE header value, as "
        f"{words.rule_language} does -- so --compare matches --from "
        "'*@list.org' will not match 'Name <a@list.org>'; write "
        "'*@list.org*'",
    )

    return parser


# ----------------------------------------------------------------------------
def criteria_source_parser() -> argparse.ArgumentParser:
    """The other two ways add and apply take criteria: a filter document,
    or a message to take them from."""
    parser = argparse.ArgumentParser(add_help=False)

    group = parser.add_argument_group("criteria from a filter or a message")
    group.add_argument(
        "--filter",
        dest="filter_file",
        metavar="FILE",
        help="read the criteria from a filter document, as 'search "
        "--build-filter --json' prints it; '-' reads standard input. Not "
        "with criteria flags or --like",
    )
    group.add_argument(
        "--like",
        type=int,
        metavar="UID",
        help="pre-fill the criteria from this message in --folder; a "
        "criteria flag replaces what was derived for its header",
    )
    group.add_argument(
        "--derive",
        help="with --like, headers to derive from, comma separated "
        "(default: auto -- List-Id if present, else From)",
    )

    return parser


# ----------------------------------------------------------------------------
def action_parser(
    offer: engine.ProviderCapabilities,
) -> argparse.ArgumentParser:
    """The action flags shared by add / apply."""
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
        help="let later rules run too (omit the 'stop' action)"
        if offer.stop
        else argparse.SUPPRESS,
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
    group.add_argument("--json", action="store_true", help=JSON_PLAN_HELP)

    return parser


# ----------------------------------------------------------------------------
def mail_safety_parser() -> argparse.ArgumentParser:
    """Safety flags that only mean something for the existing-mail pass.

    Kept apart from ``safety_parser`` so a command that never touches mail
    -- ``add``, ``remove-rule`` -- does not advertise flags that would do
    nothing.
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
def build_parser(
    offer: engine.ProviderCapabilities | None = None,
    words: Wording | None = None,
) -> argparse.ArgumentParser:
    """Construct the full argument parser.

    ``offer`` is the selected provider's declared capabilities
    (:func:`provider_offer`); what it does not declare is not offered --
    left out of help and usage, though still parsed, so that giving it
    anyway reaches the engine's refusal naming the provider rather than
    argparse's "unrecognized arguments". ``words`` is the same provider's
    wording, which the help is written in. None is the default provider.
    """
    offer = offer or engine.capabilities_for(Config())
    words = words or utilities.reports.wording(Config())
    activate_help = ACTIVATE_HELP.format(rule_language=words.rule_language)
    common = global_parser()
    connection = connection_parser(offer, words)
    criteria = criteria_parser(words)
    rule_criteria = criteria_parser(words, dated_before=False)
    sources = criteria_source_parser()
    actions = action_parser(offer)
    safety = safety_parser()
    mail_safety = mail_safety_parser()

    parser = argparse.ArgumentParser(
        prog="mailctl",
        description=f"Manage {words.host} {words.filters} and apply them to "
        "existing mail.",
    )

    parser.add_argument(
        "--version", action="version", version=f"mailctl {__version__}"
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
    listed = []

    def command(name: str, offered: bool = True, **kwargs):
        """Add a subcommand, listed in help only when offered.

        argparse lists a subcommand in help only when it is given
        ``help``, so an unoffered one is added without it and left out of
        the choices shown in usage.
        """
        if offered:
            listed.append(name)

        else:
            kwargs.pop("help", None)

        return subparsers.add_parser(name, **kwargs)

    listing = command(
        "list", parents=[common, connection], help=f"list {words.rule_sets}"
    )
    listing.add_argument("--json", action="store_true", help=JSON_HELP)
    listing.set_defaults(handler=cmd_list)

    show = command(
        "show", parents=[common, connection], help=f"print a {words.rule_set}"
    )
    show.add_argument("name", nargs="?", help="script name; default active")
    show.set_defaults(handler=cmd_show)

    rules = command(
        "rules",
        parents=[common, connection],
        help="show the rules in order, and which cannot fire",
        description="List the active script's rules in the order the server "
        "evaluates them, marking which carry 'stop' and which are disabled, "
        "then report any rule an earlier one makes unreachable. A '!' "
        "finding is decided; a '?' is a suspicion worth checking. Nothing is "
        "changed.",
    )
    rules.add_argument("--script", help="script name; default active")
    rules.add_argument("--json", action="store_true", help=JSON_HELP)
    rules.set_defaults(handler=cmd_rules)

    backup = command(
        "backup",
        parents=[common, connection],
        help="save the active script to a file",
        description=f"Save the active {words.rule_set} to a file, byte for "
        "byte as the server has it -- no banner lines, nothing reformatted "
        "(which is what 'mailctl show' adds, and why it is not a backup). "
        "The file is written mode 0600, in a directory created 0700 if it "
        "was not there. Nothing on the server is touched. 'mailctl "
        "restore FILE' puts a backup back.",
    )
    backup.add_argument(
        "--output",
        "-o",
        metavar="PATH",
        help="where to write it. A PATH ending in '/', or naming a "
        "directory that already exists, means 'put the default filename "
        "in here'; anything else is the exact file to write. Default: "
        f"the backup directory (--backup-dir), named {words.backup_file}",
    )
    backup.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="report the file that would be written; write nothing",
    )
    backup.set_defaults(handler=cmd_backup)

    restore = command(
        "restore",
        parents=[common, connection, safety],
        help="upload a backup file over the active script, or --script",
        description=f"Replace the active {words.rule_set} -- or the one "
        "--script names -- with a backup file, "
        "byte for byte. The difference between the file and what the "
        "server has now is shown first, the current script is backed up "
        "before anything is sent, the server validates the file "
        f"({words.validation}), and you are asked to confirm. No other stored "
        "script is touched. Unlike every other change mailctl makes, "
        "this REPLACES the script rather than merging into it -- any rule "
        "added since the backup was taken is removed, which the diff "
        "shows.",
    )
    restore.add_argument(
        "file", metavar="FILE", help="a file written by 'mailctl backup'"
    )
    restore.add_argument("--script", help="script name; default active")
    restore.add_argument("--activate", action="store_true", help=activate_help)
    restore.add_argument(
        "--allow-empty",
        dest="allow_empty",
        action="store_true",
        help="restore a FILE that is empty, which removes every rule; "
        "refused without this",
    )
    restore.set_defaults(handler=cmd_restore)

    folders = command(
        "folders",
        parents=[common, connection],
        help=f"list {words.mail_service} folders",
    )
    folders.add_argument(
        "--counts",
        action="store_true",
        help="show each folder's total and unread messages, and its size "
        "where the server reports it, from one request"
        if offer.folder_counts
        else argparse.SUPPRESS,
    )
    folders.add_argument("--json", action="store_true", help=JSON_HELP)
    folders.set_defaults(handler=cmd_folders)

    for name, summary in (
        ("subscribe", "show a folder in webmail (IMAP SUBSCRIBE)"),
        ("unsubscribe", "hide a folder from webmail; it keeps its mail"),
    ):
        toggle = command(
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
        toggle.add_argument("--json", action="store_true", help=JSON_PLAN_HELP)
        toggle.set_defaults(handler=cmd_subscribe)

    create_folder = command(
        "create-folder",
        parents=[common, connection, safety],
        help=f"create a folder over {words.mail_service}, and subscribe to it",
        description=f"Create a folder over {words.mail_service} and "
        "subscribe to it, so "
        "webmail shows it. The folder name is normalized like every other: "
        "'Lists/GitHub' and 'INBOX.Lists.GitHub' name the same folder, and "
        "a new one goes where the server says new folders belong. What "
        "would be created is shown first, and you are asked to confirm. A "
        "folder that already exists is left as it is; one that differs "
        "from an existing folder only in case is refused.",
    )
    create_folder.add_argument("folder", metavar="NAME")
    create_folder.add_argument(
        "--no-subscribe",
        dest="subscribe",
        action="store_false",
        help="create the folder without subscribing to it; webmail will not "
        "show it until 'mailctl subscribe NAME'",
    )
    create_folder.set_defaults(handler=cmd_create_folder)

    rename_folder = command(
        "rename-folder",
        parents=[common, connection, safety],
        help="rename a folder, and repoint the rules that file into it",
        description=f"Rename a folder over {words.mail_service}, together "
        "with every folder under it, and repoint every rule in the active "
        f"script that files into any of them. {words.mail_service}'s "
        "RENAME leaves subscriptions behind, so "
        "each moved folder that was subscribed is subscribed under its "
        "new name, and the old name is dropped from the list. Only the "
        "folder names in the rules change; every other byte of the script "
        "is kept. What would change is shown first and you are asked to "
        "confirm; the new script is backed up and validated "
        f"({words.validation}) before the folder is touched, and "
        "afterwards the account is read back to check that everything "
        "landed. INBOX cannot be renamed, "
        "and NEW must not exist yet.",
    )
    rename_folder.add_argument("old", metavar="OLD")
    rename_folder.add_argument("new", metavar="NEW")
    rename_folder.set_defaults(handler=cmd_rename_folder)

    test = command(
        "test",
        parents=[common, connection],
        help="check reachability, change nothing",
    )
    test.set_defaults(handler=cmd_test)

    probe = command(
        "probe",
        parents=[common, connection],
        help="print what the servers say about themselves, change nothing",
        description="Print, dated, everything a provider record's Observed "
        "tier needs: where each half connects, each server's identity and "
        "its full capability list (and whether that list was read before "
        "or after login), the active script, the folder delimiter, and "
        "the namespaces. Lists are sorted, so two probes of an unchanged "
        "server differ only in the time. Nothing is changed, and no "
        "credential is printed.",
    )
    shape = probe.add_mutually_exclusive_group()
    shape.add_argument(
        "--json",
        action="store_true",
        help="print it as a versioned JSON document, for storing and "
        "comparing",
    )
    shape.add_argument(
        "--report",
        action="store_true",
        help="where a server is one mailctl does not recognise, print a "
        "redacted issue body to report it: no address, host, folder or "
        "script name, or credential. Nothing is sent",
    )
    probe.set_defaults(handler=cmd_probe)

    save_baseline = command(
        "save-baseline",
        parents=[common, connection],
        help="record what the servers say now, to compare against later",
        description="Probe both servers, as 'mailctl probe' does, and save "
        "the result as this host's baseline: "
        "$XDG_CONFIG_HOME/mailctl/baselines/<host>.json, written mode 0600 "
        "in a directory created 0700. What describes the server is kept "
        "once per host; the active script is kept per account. The first "
        "save just writes it. Replacing one shows what changed and the "
        "file's diff, then asks. Only this local file is written; nothing "
        "on the server is changed, and no credential is stored.",
    )
    save_baseline.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="show what would be saved; write nothing",
    )
    save_baseline.add_argument(
        "--yes", action="store_true", help="skip the confirmation prompt"
    )
    save_baseline.set_defaults(handler=cmd_save_baseline)

    show_baseline = command(
        "show-baseline",
        parents=[common, connection],
        help="print the saved baseline for this host",
        description="Print the baseline 'mailctl save-baseline' saved for "
        "the configured host, as 'mailctl probe' lays out a probe. The "
        "server is not contacted.",
    )
    show_baseline.add_argument(
        "--json", action="store_true", help="print the file as it is stored"
    )
    show_baseline.set_defaults(handler=cmd_show_baseline)

    check_baseline = command(
        "check-baseline",
        parents=[common, connection],
        help="report what has changed on the servers since the baseline",
        description="Probe both servers and compare what they say with the "
        "saved baseline, saying what each difference means for this "
        "account. Serious (!): an extension the active script requires is "
        "gone; the folder delimiter or the personal namespace changed; an "
        "IMAP capability mailctl behaves differently without came or went; "
        "the active script is another one. Everything else is "
        "informational. Nothing is refused and nothing is changed.",
        epilog=f"Exit status: 0 no drift, {DRIFT_INFO_EXIT} informational "
        f"drift only, {DRIFT_SERIOUS_EXIT} serious drift, 1 on a failure "
        f"(no baseline saved, one that cannot be read, no connection).",
    )
    check_baseline.add_argument(
        "--json",
        action="store_true",
        help="print the report as a versioned JSON document",
    )
    check_baseline.set_defaults(handler=cmd_check_baseline)

    add = command(
        "add",
        parents=[common, connection, rule_criteria, sources, actions],
        help="save a rule; mail already delivered is left alone",
        description="Save a rule into the active script, merged with the "
        "rules already there. The diff is shown, the script is backed up, "
        "and the new one uploaded. Only new mail is filtered by it; "
        "'mailctl apply' with the same criteria acts on mail already "
        "delivered.",
    )
    add.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="show the diff; upload nothing",
    )
    add.add_argument("--json", action="store_true", help=JSON_PLAN_HELP)
    _add_rule_flags(add, offer, words)
    add.set_defaults(handler=cmd_add)

    apply_cmd = command(
        "apply",
        parents=[
            common,
            connection,
            criteria,
            sources,
            actions,
            safety,
            mail_safety,
        ],
        help="act on mail already delivered",
    )
    apply_cmd.add_argument(
        "--folder", help=f"source folder; {FOLDER_DEFAULT_HELP}"
    )
    apply_cmd.add_argument("--delimiter", help=argparse.SUPPRESS)
    apply_cmd.set_defaults(handler=cmd_apply, no_imap=False)

    search = command(
        "search",
        parents=[common, connection, criteria],
        help="list the newest messages in a folder",
    )
    search.add_argument(
        "--folder", help=f"folder to list; {FOLDER_DEFAULT_HELP}"
    )
    search.add_argument(
        "--raw",
        metavar="QUERY",
        help="a query in the host's own search language instead of "
        "criteria flags, e.g. 'UNSEEN' or 'SINCE 1-Sep-2026'"
        if offer.raw_query
        else argparse.SUPPRESS,
    )
    search.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIST_LIMIT,
        help=f"show at most N messages; default {DEFAULT_LIST_LIMIT}",
    )
    search.add_argument(
        "--sort",
        choices=SORT_KEYS,
        help="list in this order instead of newest first: size (in bytes), "
        "sent (the Date header), or received (when the server took it in, "
        "the Received column); smallest or oldest first. --limit is taken "
        "after sorting",
    )
    search.add_argument(
        "--reverse",
        action="store_true",
        help="with --sort, largest or newest first",
    )
    search.add_argument(
        "--like",
        type=int,
        metavar="UID",
        help="pre-fill the criteria from this message in --folder; a "
        "criteria flag replaces what was derived for its header",
    )
    search.add_argument(
        "--derive",
        help="with --like, headers to derive from, comma separated "
        "(default: auto -- List-Id if present, else From)",
    )
    search.add_argument(
        "--build-filter",
        action="store_true",
        help="print the filter the criteria make instead of searching; "
        "saves nothing",
    )
    search.add_argument(
        "--json",
        action="store_true",
        help="print the listing as a JSON document; with --build-filter, "
        "the filter document",
    )
    search.add_argument(
        "--uids-only",
        dest="uids_only",
        action="store_true",
        help="print only the listed messages' UIDs, one per line",
    )
    search.set_defaults(handler=cmd_search)

    view = command(
        "view",
        parents=[common, connection],
        help="show one message, without marking it read",
    )
    view.add_argument("uid", type=int, help="the message UID ('search')")
    view.add_argument(
        "--folder", help=f"folder holding it; {FOLDER_DEFAULT_HELP}"
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
    shape.add_argument("--json", action="store_true", help=JSON_HELP)
    view.set_defaults(handler=cmd_view)

    senders = command(
        "senders",
        parents=[common, connection, criteria],
        help="count a folder's mail by sender, with how much is unread",
        description="Count the mail in a folder by sender -- the address, "
        "its domain, or the mailing list -- busiest first, with how many "
        "are unread: the mail worth a filter. Takes the same criteria "
        "flags as 'search', such as --since or --unread. One search finds "
        "the mail and its headers are read a page at a time; nothing is "
        "marked read, and nothing is changed.",
    )
    senders.add_argument(
        "--folder", help=f"folder to count; {FOLDER_DEFAULT_HELP}"
    )
    senders.add_argument(
        "--by",
        choices=utilities.senders.GROUPINGS,
        default="address",
        help="count by sender address, its domain, or the List-Id header; "
        "default address",
    )
    senders.add_argument(
        "--top",
        type=int,
        default=DEFAULT_SENDERS_TOP,
        metavar="N",
        help=f"show the N busiest; 0 shows every one. Default "
        f"{DEFAULT_SENDERS_TOP}",
    )
    senders.add_argument(
        "--min",
        dest="minimum",
        type=int,
        default=1,
        metavar="N",
        help="leave out any that sent fewer than N messages; default 1",
    )
    senders.add_argument(
        "--max-messages",
        dest="max_messages",
        type=int,
        default=utilities.senders.DEFAULT_MAX_MESSAGES,
        metavar="N",
        help=f"refuse, reading no header, if the search finds more than "
        f"N messages, rather than counting some of them (default "
        f"{utilities.senders.DEFAULT_MAX_MESSAGES})",
    )
    senders.add_argument("--json", action="store_true", help=JSON_HELP)
    senders.set_defaults(handler=cmd_senders)

    mark = command(
        "mark",
        offer.mark,
        parents=[common, connection, safety],
        help="mark messages read or unread, flagged, or with keywords",
        description="Set or clear the read flag, the flagged flag, and "
        "named keywords on messages, by UID ('search' lists them). Several "
        "may be given at once, such as --read --flag. What each message has "
        "now and what would change is shown first, then --dry-run stops, or "
        "you are asked to confirm (--yes skips the question). A UID the "
        "folder does not hold refuses the whole command, naming it; nothing "
        "is marked. 'view' never marks anything read; this is how to.",
    )
    mark.add_argument(
        "uids", nargs="+", type=int, metavar="UID", help="the message UIDs"
    )
    mark.add_argument(
        "--folder", help=f"folder holding them; {FOLDER_DEFAULT_HELP}"
    )
    seen = mark.add_mutually_exclusive_group()
    seen.add_argument(
        "--read",
        dest="read",
        action="store_const",
        const=True,
        help="mark them read (set \\Seen)",
    )
    seen.add_argument(
        "--unread",
        dest="read",
        action="store_const",
        const=False,
        help="mark them unread (clear \\Seen)",
    )
    flagged = mark.add_mutually_exclusive_group()
    flagged.add_argument(
        "--flag",
        dest="flagged",
        action="store_const",
        const=True,
        help="flag them (set \\Flagged)",
    )
    flagged.add_argument(
        "--unflag",
        dest="flagged",
        action="store_const",
        const=False,
        help="unflag them (clear \\Flagged)",
    )
    mark.add_argument(
        "--keyword",
        dest="keywords",
        action="append",
        default=[],
        metavar="K",
        help="set keyword K, one word such as $Important; repeatable",
    )
    mark.add_argument(
        "--no-keyword",
        dest="no_keywords",
        action="append",
        default=[],
        metavar="K",
        help="clear keyword K; repeatable",
    )
    mark.set_defaults(handler=cmd_mark)

    remove = command(
        "remove-rule",
        parents=[common, connection, safety],
        help="remove a named rule from the active script",
    )
    remove.add_argument("rule_name", metavar="NAME")
    remove.add_argument("--script", help="script name; default active")
    remove.add_argument("--activate", action="store_true", help=activate_help)
    remove.set_defaults(handler=cmd_remove_rule)

    move = command(
        "move-rule",
        offer.ordering,
        parents=[common, connection, safety],
        help="move a named rule to a new position, unchanged",
        description="Reorder one rule without restating it: only its "
        f"position changes. {words.rule_language} runs rules in order and "
        "'stop' ends the run, so the move is judged where the rule lands "
        "-- what would stop it running, and what it would now stop -- "
        "before the diff is shown. The script is backed up first and you "
        "are asked to confirm.",
    )
    move.add_argument("rule_name", metavar="NAME")
    move.add_argument("--script", help="script name; default active")
    move.add_argument("--activate", action="store_true", help=activate_help)

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

    optimize = command(
        "optimize-rules",
        offer.ordering,
        parents=[common, connection, safety],
        help="propose a better order for the rules, and merge duplicates",
        description="Read the active script's rules and propose a better "
        "arrangement: remove a rule that can never run and would only "
        "repeat an earlier rule, move a specific rule ahead of a broader "
        "one that starves it, and merge consecutive rules that test the "
        "same header the same way with the same actions into one rule "
        "with a key list. Only what the rules' own conditions decide is "
        "changed; anything less certain is reported and left alone, and "
        "disabled rules are never moved, merged, or removed. The script "
        "stays a flat list of rules. It is backed up first and you are "
        "asked to confirm.",
    )
    optimize.add_argument("--script", help="script name; default active")
    optimize.add_argument(
        "--activate", action="store_true", help=ACTIVATE_HELP
    )
    optimize.add_argument(
        "--skip",
        action="append",
        default=[],
        choices=utilities.optimize.KINDS,
        metavar="KIND",
        help="do not propose this kind of change: redundant, reorder, or "
        "merge; repeatable",
    )
    optimize.set_defaults(handler=cmd_optimize_rules)

    for enable, name in ((False, "disable-rule"), (True, "enable-rule")):
        verb = "enable" if enable else "disable"
        switch = command(
            name,
            offer.disable,
            parents=[common, connection, safety],
            help=f"{verb} a named rule, keeping it in the script",
            description=f"Switch one rule {'on' if enable else 'off'} "
            "without removing it. A disabled rule stays in the script, "
            f"{words.disabled_form}. The script is backed up first and you "
            "are asked to confirm.",
        )
        switch.add_argument("rule_name", metavar="NAME")
        switch.add_argument("--script", help="script name; default active")
        switch.add_argument(
            "--activate", action="store_true", help=activate_help
        )
        switch.set_defaults(handler=cmd_switch_rule, enable=enable)

    migrate = command(
        "migrate-config",
        parents=[common],
        help="move config and backups from the old mxfilter directory",
        description="Move everything in the config directory the tool used "
        "under its old name ($XDG_CONFIG_HOME/mxfilter) into the one it "
        "reads now ($XDG_CONFIG_HOME/mailctl): config.toml, the script "
        "backups, and anything else there. Files are moved, not copied, so "
        "each keeps its mode. Nothing already at the destination is "
        "overwritten -- any clash and nothing moves. A password_file or "
        "backup_dir in config.toml that points inside the old directory is "
        "pointed at the new one. The server is not contacted.",
    )
    migrate.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="list what would move; move nothing",
    )
    migrate.add_argument(
        "--yes", action="store_true", help="skip the confirmation prompt"
    )
    migrate.set_defaults(handler=cmd_migrate_config)

    # Carried out in main(), which holds the parser this run built.
    helping = command(
        "help",
        parents=[common],
        help="show help for mailctl or a command",
        description="Print what 'mailctl --help' prints, or with COMMAND "
        "what 'mailctl COMMAND --help' prints, for the provider --provider "
        "names or the configuration selects. The server is not contacted.",
    )
    helping.add_argument(
        "topic", nargs="?", metavar="COMMAND", help="the command to explain"
    )
    helping.add_argument(
        "--provider",
        help="show what this provider offers; default the configured one",
    )
    helping.add_argument(
        "--env-file",
        dest="env_file",
        nargs="?",
        const=".env",
        metavar="PATH",
        help="read MAILCTL_* settings, the provider among them, from a "
        "dotenv-style file (default .env in the current directory)",
    )

    subparsers.metavar = "{" + ",".join(listed) + "}"

    return parser


# ----------------------------------------------------------------------------
def _add_rule_flags(
    parser: argparse.ArgumentParser,
    offer: engine.ProviderCapabilities,
    words: Wording,
) -> None:
    """Attach add's rule-authoring flags.

    Placement needs a provider declaring ``ordering``, and naming or
    activating a script one declaring ``rule_sets``; unoffered, each is
    hidden from help and still refused by name if given.
    """

    def offered(capability: str, text: str) -> str:
        return text if getattr(offer, capability) else argparse.SUPPRESS

    group = parser.add_argument_group("rule")
    group.add_argument(
        "--name", help="rule name; derived from criteria if omitted"
    )
    group.add_argument(
        "--script", help=offered("rule_sets", "script name; default active")
    )
    group.add_argument(
        "--activate",
        action="store_true",
        help=offered(
            "rule_sets",
            ACTIVATE_HELP.format(rule_language=words.rule_language),
        ),
    )
    group.add_argument(
        "--replace",
        action="store_true",
        help="overwrite an existing rule of the same name",
    )
    group.add_argument(
        "--no-imap",
        dest="no_imap",
        action="store_true",
        help=f"skip {words.mail_service} entirely: the target folder is not "
        f"checked, and the folder delimiter is guessed",
    )
    group.add_argument(
        "--folder",
        help=f"with --like, the folder holding the message; "
        f"{FOLDER_DEFAULT_HELP}",
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
        help=offered("ordering", "put the rule before every existing rule"),
    )
    where.add_argument(
        "--last",
        dest="place_last",
        action="store_true",
        help=offered(
            "ordering",
            "put the rule after every existing rule (the default). With "
            "--replace this MOVES an existing rule to the end; without it, "
            "the rule is simply appended as always",
        ),
    )
    where.add_argument(
        "--before",
        dest="place_before",
        metavar="NAME",
        help=offered(
            "ordering", "put the rule immediately before the rule named NAME"
        ),
    )
    where.add_argument(
        "--after",
        dest="place_after",
        metavar="NAME",
        help=offered(
            "ordering", "put the rule immediately after the rule named NAME"
        ),
    )


# ############################################################################
# Entry point
# ############################################################################


# ----------------------------------------------------------------------------
def provider_offer(
    argv: list[str] | None,
) -> tuple[engine.ProviderCapabilities, Wording] | None:
    """The selected provider's capabilities and wording, read ahead of the
    real parse.

    The subcommand parsers are built from what the provider declares, so
    the provider has to be known first: a first pass reads only
    ``--provider`` and ``--env-file`` and resolves the provider through the
    same ladder as every other setting. Anything that stops it -- an
    unknown provider, an unreadable env file -- is the run's to report,
    not help's, so this falls back to None and the default provider's
    offer, and the real parse and run say what went wrong.
    """
    first = argparse.ArgumentParser(
        add_help=False, allow_abbrev=False, exit_on_error=False
    )
    first.add_argument("--provider")
    first.add_argument("--env-file", dest="env_file", nargs="?", const=".env")

    try:
        known, _rest = first.parse_known_args(argv)
        config = load_config(known)

        return (
            engine.capabilities_for(config),
            utilities.reports.wording(config),
        )

    except (argparse.ArgumentError, SystemExit, MailctlError):
        return None


# ----------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """Parse arguments, dispatch, and turn failures into diagnostics."""
    parser = build_parser(*(provider_offer(argv) or ()))
    args = parser.parse_args(argv)

    # The same parse as 'mailctl [COMMAND] --help', on the parser built for
    # this run's provider, so the two cannot drift; argparse exits from it,
    # 0 with the help or 2 naming the valid commands.
    if args.command == "help":
        topic = [] if args.topic is None else [args.topic]

        parser.parse_args([*topic, "--help"])

    # --no-subscribe only shapes a folder this run creates. Accepting it
    # alone would be a flag that looks like it took effect and did not.
    if getattr(args, "no_subscribe", False) and not args.create_folder:
        parser.error(
            "--no-subscribe only applies with --create-folder; to hide a "
            "folder that already exists, use 'mailctl unsubscribe FOLDER'"
        )

    # --json and --uids-only keep stdout for the data alone: the data goes
    # to args.stdout, the stdout this run started with, and everything said
    # on the way -- progress, a folder's resolution, the message --like read
    # -- is sent to stderr, where a person still sees it.
    args.stdout = sys.stdout
    machine = getattr(args, "json", False) or getattr(args, "uids_only", False)

    try:
        if args.handler is not cmd_migrate_config:
            warn_about_config_dir(utilities.migration.check_config_dir())

        # A document on stdout leaves no room for a confirmation prompt, so
        # a write command prints its plan and stops.
        if (
            getattr(args, "json", False)
            and getattr(args, "dry_run", None) is False
        ):
            raise MailctlError(
                "--json prints a write command's plan, so it needs --dry-run"
            )

        if not machine:
            return args.handler(args)

        with contextlib.redirect_stdout(sys.stderr):
            return args.handler(args)

    except MailctlError as exc:
        if args.debug:
            traceback.print_exc()

        report_failure(args, error_text(exc), exc.code)

        return 1

    except KeyboardInterrupt:
        if getattr(args, "json", False):
            report_failure(args, "interrupted")

        else:
            print("\nInterrupted.", file=sys.stderr)

        return 130

    except Exception as exc:
        if args.debug:
            traceback.print_exc()

        report_failure(
            args,
            f"unexpected {type(exc).__name__}: {exc} "
            f"(re-run with --debug for a traceback)",
        )

        return 1


# ----------------------------------------------------------------------------
def report_failure(args, message: str, code: str | None = None) -> None:
    """Say on stderr why the run failed: as one line of JSON under --json,
    the last line stderr holds."""
    if getattr(args, "json", False):
        document = json_output.error(message, code)

        sys.stderr.write(json_output.dumps(document, indent=None))

        return

    # The core breaks a long message into lines with bare newlines and
    # leaves the layout here; indent them under the prefix.
    message = message.replace("\n", "\n  ")

    print(f"mailctl: {message}", file=sys.stderr)


# ############################################################################
# Errors -- the core's condition, with the flags and commands that act on it
# ############################################################################


# ----------------------------------------------------------------------------
def flag_for(setting: str) -> str:
    """The flag that sets ``setting``: its name, spelt as a flag."""
    return f"--{setting.replace('_', '-')}"


# ----------------------------------------------------------------------------
def command_for(operation: str, arguments: tuple[str, ...] = ()) -> str:
    """The command line that runs ``operation`` with ``arguments``; the
    core names the operation, never the command (#183)."""
    return " ".join(("mailctl", operation, *arguments))


# The flag that sets each of 'senders' settings; --min is not its name.
SENDERS_FLAGS = {
    "top": "--top",
    "minimum": "--min",
    "max_messages": "--max-messages",
}


# What the CLI says for each coded error (MailctlError.code). The core's
# message states the condition in words any front-end can show; each entry
# here adds this front-end's flags and commands, from the message and the
# error's fields ("operation", "operations", and "arguments" name a command
# and what it is given). Most append to the message. The rest rebuild it
# around a flag or command the core could not name, keeping the wording the
# CLI has always printed.
ERROR_TEXT = {
    "no_criteria": lambda message, _: (
        f"{message} -- use --from/--to/--cc/--subject/--list-id/--header/"
        f"--body, or --since/--before/--older-than/--unread/--flagged"
    ),
    "body_compare": lambda _, fields: (
        f"a body criterion is always a substring test, so it cannot be "
        f"combined with --compare {fields['compare']}; use --compare "
        f"contains (the default)"
    ),
    "no_action": lambda message, _: (
        f"{message} -- use --fileinto, --discard, --mark-read, --flag, or "
        f"--keep"
    ),
    "no_mail_action": lambda message, _: (
        f"{message} -- use --fileinto, --discard, --mark-read, or --flag"
    ),
    "no_marks": lambda message, _: (
        f"{message} -- use --read, --unread, --flag, --unflag, --keyword, "
        f"or --no-keyword"
    ),
    "no_password": lambda _, fields: (
        f"no password available -- pass --password-file, --password-cmd, "
        f"or --password, {fields['settings']}"
    ),
    "missing_settings": lambda _, fields: (
        f"missing required setting(s): {', '.join(fields['settings'])}. "
        f"Set {', '.join(flag_for(name) for name in fields['settings'])}, "
        f"the matching MAILCTL_* variable, or add it to {fields['config']}"
    ),
    "check_settings": lambda _, fields: (
        f"{fields['reason']} Check "
        f"{' and '.join(flag_for(name) for name in fields['settings'])}."
    ),
    "try_setting": lambda _, fields: (
        f"{fields['before']} Try {flag_for(fields['setting'])} "
        f"{fields['value']}{fields['note']} as the alternative. "
        f"{fields['after']}"
    ),
    "needs_mail": lambda _, fields: (
        f"{fields['reason']} and --no-imap was given, so "
        f"{fields['folder']!r} cannot be created"
    ),
    "max_messages": lambda _, fields: (
        f"{fields['count']} message(s) match but --max-messages is "
        f"{fields['limit']}. {fields['why']} Re-run with --max-messages "
        f"{fields['count']} (or higher) to process every match. Note that "
        f"--yes does NOT lift this cap: it skips the confirmation prompt, "
        f"whereas the cap is a ceiling you set deliberately."
    ),
    "empty_backup": lambda message, _: (
        f"{message} Pass --allow-empty if that is what you want."
    ),
    "restore_needs_script": lambda _, __: (
        "no active script on the server to restore over. Name the script "
        "to restore with --script NAME; with nothing active it is "
        "activated. 'mailctl list' shows what the account has."
    ),
    "no_host": lambda message, _: f"{message}; set --host or MAILCTL_HOST",
    "rule_exists": lambda message, _: (
        f"{message} Use --replace to overwrite it, or --name to pick another."
    ),
    "self_anchor": lambda _, fields: (
        f"--{fields['where']} {fields['anchor']!r} names the rule being "
        f"{fields['verb']}, which has no position to be relative to. Name "
        f"another rule, or use --first / --last."
    ),
    "unknown_anchor": lambda _, fields: (
        f"no rule named {fields['anchor']!r} in the active script, so "
        f"--{fields['where']} has nothing to place this rule against. "
        f"Known rules: {fields['known']}"
    ),
    "at_least_one": lambda _, fields: (
        f"{SENDERS_FLAGS[fields['setting']]} must be at least 1, not "
        f"{fields['value']}"
    ),
    "senders_ceiling": lambda _, fields: (
        f"the search found {fields['count']} message(s) in "
        f"{fields['folder']!r} but --max-messages is {fields['limit']}, so "
        f"no header was read. {fields['why']} Narrow it (--since, "
        f"--older-than, --unread, --from, ...) or re-run with "
        f"--max-messages {fields['count']} or higher."
    ),
    "raw_non_ascii": lambda _, fields: (
        f"{fields['refused']}; use the criteria flags (e.g. --subject) for "
        f"it, which search in {fields['charset']}."
    ),
    "no_active_script": lambda message, fields: (
        f"{message} '{command_for(fields['operation'])}' shows what the "
        f"account has."
    ),
    "no_such_folder": lambda message, fields: (
        f"{message} '{command_for(fields['operation'])}' lists what exists."
    ),
    "folder_unopenable": lambda message, fields: (
        f"{message} Run '{command_for(fields['operation'])}' to see the exact "
        f"names this server uses."
    ),
    "state_in_rule": lambda _, fields: (
        f"{fields['before']}: use them with "
        f"'{command_for(fields['operations'][0])}' to list it, or "
        f"'{command_for(fields['operations'][1])}' to act on it."
    ),
    "replace_disabled": lambda _, fields: (
        f"{fields['before']} "
        f"({command_for(fields['operation'], fields['arguments'])}), "
        f"then replace it"
    ),
    "unimplemented_action": lambda message, fields: (
        f"{message} To see whether the server advertises the extension at "
        f"all, run '{command_for(fields['operation'])}'."
    ),
    "extension_missing": lambda message, fields: (
        f"{message} '{command_for(fields['operations'][0])}' lists what the "
        f"server advertises; '{command_for(fields['operations'][1])}' and "
        f"'{command_for(fields['operations'][2])}' take the same criteria "
        f"for mail already delivered."
    ),
    "rename_inbox": lambda message, fields: (
        f"{message} '{command_for(fields['operation'])}' moves mail out of "
        f"INBOX by criteria."
    ),
    "rename_not_activated": lambda _, fields: (
        f"{fields['before']} '{command_for(fields['operation'])}' shows which "
        f"script is active.{fields['saved']}"
    ),
    "rename_interrupted": lambda _, fields: (
        f"{fields['before']} To undo it, run "
        f"'{command_for(fields['operation'], fields['arguments'])}'; then the "
        f"rename can be tried again."
    ),
    "baseline_unreadable": lambda _, fields: (
        f"{fields['before']} run '{command_for(fields['operation'])}' to take "
        f"a new one."
    ),
    "no_baseline": lambda message, fields: (
        f"{message}; '{command_for(fields['operation'])}' records one"
    ),
    "baseline_other_provider": lambda message, fields: (
        f"{message} '{command_for(fields['operation'])}' replaces it."
    ),
}


# ----------------------------------------------------------------------------
def error_text(exc: MailctlError) -> str:
    """What the CLI says for ``exc``: its message, with this front-end's
    flags added where the error's code has any."""
    render = ERROR_TEXT.get(exc.code or "")

    return str(exc) if render is None else render(str(exc), exc.fields)
