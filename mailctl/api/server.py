"""The API's session, its methods, and the plan table between them.

One process holds one configuration, one account, and one session
(ADR 0008). ``initialize`` resolves the configuration from the process's
own arguments as the CLI does and opens the session, which connects
nothing until a method needs a half; ``shutdown``, or the end of input,
closes it. Requests are handled one at a time, in order.

A change is plan, decide, execute across the boundary. A ``.plan`` method
keeps the plan record here under a fresh id and returns the id beside the
plan's document; the ``.execute`` method takes the id and nothing the plan
already holds, so what was shown is what runs. An id is used once:
executed, failed, or discarded, it is gone.
"""

import argparse
import json
import secrets
import sys
import traceback
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any, TextIO

from .. import MailctlError, __version__, engine, json_output, utilities
from ..config import SIEVE_TLS_MODES, Config, Secret, load_config
from ..criteria import Criteria, load_filter
from ..utilities.events import ServerAlert
from ..utilities.messages import DEFAULT_LIST_LIMIT, SortOrder
from ..utilities.rules import (
    PLACE_AFTER,
    PLACE_BEFORE,
    PLACE_FIRST,
    PLACE_LAST,
    ActionSpec,
    Placement,
    RuleRequest,
)
from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    REFUSED,
    Channel,
    ClientGone,
    RpcError,
    is_response,
)

__all__ = ["METHODS", "PROTOCOL_METHODS", "PROTOCOL_VERSION", "Server"]

# The method set and the envelope. Adding a method or a parameter is
# compatible; renaming or removing one, or changing what it means, raises
# this. The documents inside are versioned by ``JSON_VERSION``.
PROTOCOL_VERSION = 1

# What a client cannot have asked for: an action the CLI refuses by name,
# refused here the same way, by the provider.
REFUSED_ACTIONS = ("redirect", "notify", "vacation")

PLACEMENTS = (PLACE_FIRST, PLACE_LAST, PLACE_BEFORE, PLACE_AFTER)

# A parameter's type, as an error names it.
KINDS = {
    str: "a string",
    int: "a whole number",
    bool: "true or false",
    dict: "an object",
    list: "a list of strings",
}

_ABSENT = object()


# ############################################################################
# Parameters
# ############################################################################


class Params:
    """A request's named parameters, taken one at a time and checked.

    Every refusal names the parameter and the type it needs, never the
    value given, which may be anything a client chose to send.
    """

    # ------------------------------------------------------------------------
    def __init__(self, values: dict):
        if "password" in values:
            raise RpcError(
                INVALID_PARAMS,
                "no method takes a password: the API reads it from its "
                "configuration, or asks for it with credentials.prompt",
            )

        self._values = dict(values)

    # ------------------------------------------------------------------------
    def take(
        self,
        name: str,
        kind: type,
        default: Any = _ABSENT,
        *,
        null: Any = _ABSENT,
    ) -> Any:
        """``name``'s value, of ``kind``; absent is ``default``, and refused
        where there is none. An explicit null is ``null`` where one is
        given, and otherwise the same as absent."""
        value = self._values.pop(name, _ABSENT)

        if value is None and null is not _ABSENT:
            return null

        if value is None or value is _ABSENT:
            if default is _ABSENT:
                raise RpcError(INVALID_PARAMS, f"{name!r} is required")

            return default

        # bool is an int in Python, and true is not a number.
        wrong = not isinstance(value, kind) or (
            kind is int and isinstance(value, bool)
        )

        if kind is list and isinstance(value, list) and not wrong:
            wrong = not all(isinstance(item, str) for item in value)

        if wrong:
            raise RpcError(INVALID_PARAMS, f"{name!r} must be {KINDS[kind]}")

        return value

    # ------------------------------------------------------------------------
    def done(self) -> None:
        """Refuse whatever was given and not taken."""
        if self._values:
            raise RpcError(
                INVALID_PARAMS,
                f"unknown parameter(s) {', '.join(sorted(self._values))}",
            )


# ############################################################################
# The server
# ############################################################################


@dataclass(frozen=True)
class _Plan:
    """A plan waiting in the table: the command that made it, and it."""

    command: str
    record: Any


class Server:
    """One client's session, served from its requests in order."""

    # ------------------------------------------------------------------------
    def __init__(
        self,
        args: argparse.Namespace,
        channel: Channel,
        *,
        stderr: TextIO | None = None,
    ):
        self.args = args
        self.channel = channel
        self.stderr = stderr or sys.stderr
        self.config: Config | None = None
        self.session: engine.Session | None = None
        self._stack = ExitStack()
        self._plans: dict[str, _Plan] = {}
        self._alerts_sent: set[tuple[str, str, str]] = set()
        self._log = False
        self._current: object = None

    # ------------------------------------------------------------------------
    def run(self) -> None:
        """Serve requests until ``shutdown`` or the end of input, then
        close the session."""
        try:
            while True:
                try:
                    message = self.channel.receive()

                except RpcError as error:
                    self.channel.fail(None, error)

                    continue

                if message is None or self.handle(message):
                    return

        finally:
            self._close()

    # ------------------------------------------------------------------------
    def handle(self, message: object) -> bool:
        """Answer one message; True once it was a ``shutdown``."""
        if is_response(message):
            # An answer to nothing this server is waiting on.
            return False

        ident = message.get("id") if isinstance(message, dict) else None
        valid_id = ident is None or (
            isinstance(ident, str | int) and not isinstance(ident, bool)
        )

        if (
            not isinstance(message, dict)
            or message.get("jsonrpc") != "2.0"
            or not isinstance(message.get("method"), str)
            or not valid_id
        ):
            self.channel.fail(
                ident if valid_id else None,
                RpcError(
                    INVALID_REQUEST,
                    'a request is an object with jsonrpc "2.0", a method '
                    "name, and a string or number id; batches are not "
                    "taken",
                ),
            )

            return False

        name = message["method"]
        notification = "id" not in message
        method = METHODS.get(name)

        if method is None:
            if not notification:
                self.channel.fail(
                    ident,
                    RpcError(METHOD_NOT_FOUND, f"no method {name!r}"),
                )

            return False

        self._current = ident

        try:
            params = message.get("params", {})

            if not isinstance(params, dict):
                raise RpcError(
                    INVALID_PARAMS, "params must be an object, by name"
                )

            result = method(self, Params(params), ident)

        except RpcError as error:
            self._diagnose(ident, name)

            return self._answer(notification, ident, error)

        except MailctlError as exc:
            self._diagnose(ident, name)

            # A server that refused a prompted password refuses it on every
            # retry, so the next connection asks again.
            if exc.code == "auth_failed" and self.config is not None:
                self.config.forget_prompted_password()

            data = json_output.error(str(exc), exc.code, exc.fields)

            return self._answer(
                notification, ident, RpcError(REFUSED, str(exc), data)
            )

        except Exception as exc:
            self._diagnose(ident, name)

            return self._answer(notification, ident, unexpected(exc))

        finally:
            self._current = None

        if not notification:
            try:
                self.channel.respond(ident, result)

            except (TypeError, ValueError) as exc:
                # Nothing was written: the line is built whole before it
                # is sent, and a value no document maps is refused first.
                self._diagnose(ident, name)
                self.channel.fail(ident, unexpected(exc))

        return name == "shutdown"

    # ------------------------------------------------------------------------
    def _answer(
        self, notification: bool, ident: object, error: RpcError
    ) -> bool:
        if not notification:
            self.channel.fail(ident, error)

        return False

    # ------------------------------------------------------------------------
    def _diagnose(self, ident: object, name: str) -> None:
        """Under ``--debug``, the failure's traceback on stderr, naming the
        request by its id and method, never its parameters."""
        if not self.args.debug:
            return

        print(
            f"mailctl api: request {ident!r} ({name}) failed:",
            file=self.stderr,
        )
        traceback.print_exc(file=self.stderr)

    # ------------------------------------------------------------------------
    def _close(self) -> None:
        self._stack.close()
        self._plans.clear()
        self.session = None

    # ------------------------------------------------------------------------
    def _open(self) -> tuple[engine.Session, Config]:
        """The session and its configuration, once ``initialize`` has
        opened them."""
        if self.session is None or self.config is None:
            raise MailctlError(
                "the session is not initialized; call initialize first",
                code="not_initialized",
            )

        return self.session, self.config

    # ------------------------------------------------------------------------
    def _progress(self, half: str, message: str | ServerAlert) -> None:
        """The session's ``progress``: a server's alert always, once per
        session, and protocol chatter only where ``initialize`` asked."""
        if isinstance(message, ServerAlert):
            key = (half, message.kind, message.text)

            if key in self._alerts_sent:
                return

            self._alerts_sent.add(key)
            self.channel.notify(
                "server.alert",
                {
                    "request": self._current,
                    "document": json_output.alert(half, message),
                },
            )

        elif self._log:
            self.channel.notify(
                "log",
                {
                    "request": self._current,
                    "channel": json_output.CHANNELS.get(half, half),
                    "message": message,
                },
            )

    # ------------------------------------------------------------------------
    def _events(self, ident: object) -> Callable[[object], None]:
        """An ``on_event`` sink sending each step as it lands."""

        def sink(record: object) -> None:
            self.channel.notify(
                "event",
                {"request": ident, "document": json_output.event(record)},
            )

        return sink

    # ------------------------------------------------------------------------
    def _prompt(self, text: str) -> Secret:
        """``Config.prompter``: ask the client, and take its answer as the
        ``Secret`` the line was parsed into."""
        try:
            answer = self.channel.request(
                "credentials.prompt",
                {"request": self._current, "prompt": text},
            )

        except ClientGone as exc:
            raise MailctlError(
                "the client's input ended before it gave a password",
                code="password_declined",
            ) from exc

        except RpcError as exc:
            raise MailctlError(
                "the client declined to give a password",
                code="password_declined",
            ) from exc

        secret = answer.get("password") if isinstance(answer, dict) else None

        if not isinstance(secret, Secret):
            raise MailctlError(
                "the client's answer held no password",
                code="password_declined",
            )

        return secret

    # ------------------------------------------------------------------------
    def _keep(self, command: str, record: Any) -> str:
        plan_id = secrets.token_hex(8)
        self._plans[plan_id] = _Plan(command, record)

        return plan_id

    # ------------------------------------------------------------------------
    def _take_plan(self, plan_id: str, command: str | None) -> _Plan:
        """Remove a waiting plan from the table and return it.

        Refused, coded ``plan_unknown``, where no plan has the id -- it was
        never made, or was executed, failed, or discarded already -- or
        where ``command`` is given and the plan is another command's.
        """
        entry = self._plans.get(plan_id)

        if entry is None or (command is not None and entry.command != command):
            raise MailctlError(
                "no plan with that id is waiting: a plan is used once, "
                "whether it was carried out, failed, or was discarded",
                code="plan_unknown",
                fields={"plan_id": plan_id},
            )

        del self._plans[plan_id]

        return entry


# ############################################################################
# Methods
# ############################################################################


# ----------------------------------------------------------------------------
def initialize(server: Server, params: Params, _ident: object) -> dict:
    """Resolve the configuration and open the session; connect nothing.

    ``log`` asks for the protocol chatter as ``log`` notifications.
    """
    log = params.take("log", bool, False)
    params.done()

    if server.session is not None:
        raise MailctlError(
            "the session is already initialized",
            code="already_initialized",
        )

    config = load_config(server.args)
    config.prompter = server._prompt
    offer = engine.capabilities_for(config)

    session = server._stack.enter_context(
        engine.connect(config, progress=server._progress)
    )
    server.config, server.session, server._log = config, session, log

    return {
        "protocol": PROTOCOL_VERSION,
        "json_version": json_output.JSON_VERSION,
        "mailctl": __version__,
        "provider": session.name,
        "capabilities": json_output.capabilities(offer),
    }


# ----------------------------------------------------------------------------
def shutdown(server: Server, params: Params, _ident: object) -> None:
    """Close the session; the process ends once this is answered."""
    params.done()
    server._close()


# ----------------------------------------------------------------------------
def filter_list(server: Server, params: Params, _ident: object) -> dict:
    """``filter list``: the filters in a filter set, the active one unless
    ``filterset`` names another, and which cannot fire."""
    session, _config = server._open()
    filterset = params.take("filterset", str, None)
    params.done()

    return json_output.rules_report(
        utilities.rules.read_rules(session, filterset)
    )


# ----------------------------------------------------------------------------
def mail_search(server: Server, params: Params, _ident: object) -> dict:
    """``mail search``: the newest messages in ``folder`` that ``filter``
    (a filter document) or ``raw`` (the host's own query) selects, at most
    ``limit`` of them (null for all), or in the order ``sort`` names."""
    session, config = server._open()
    folder = params.take("folder", str, config.source_folder)
    document = params.take("filter", dict, None)
    raw = params.take("raw", str, None)
    limit = params.take("limit", int, DEFAULT_LIST_LIMIT, null=None)
    sort = params.take("sort", dict, None)
    params.done()

    order = None

    if sort is not None:
        given = Params(sort)
        order = SortOrder(
            given.take("key", str), given.take("reverse", bool, False)
        )
        given.done()

    listing = utilities.messages.list_messages(
        session,
        folder,
        criteria=None if document is None else filter_criteria(document),
        raw=raw,
        limit=limit,
        order=order,
    )

    return json_output.message_listing(listing)


# ----------------------------------------------------------------------------
def mail_view(server: Server, params: Params, _ident: object) -> dict:
    """``mail view``: one message's headers, text, and attachments, read
    without marking it read; refused where ``uidvalidity`` is given and
    the folder's is now another."""
    session, config = server._open()
    uid = params.take("uid", int)
    folder = params.take("folder", str, config.source_folder)
    uidvalidity = params.take("uidvalidity", int, None)
    params.done()

    return json_output.message(
        utilities.messages.read_message(session, folder, uid, uidvalidity)
    )


# ----------------------------------------------------------------------------
def filter_add_plan(server: Server, params: Params, _ident: object) -> dict:
    """Plan ``filter add``: the rule ``filter`` (a filter document) and
    ``actions`` make, merged into the filter set and not uploaded.

    Returns ``plan_id``, for ``filter.add.execute`` or ``plan.discard``,
    and ``document``, the plan as ``filter add --dry-run --json`` prints
    it.
    """
    session, config = server._open()
    criteria = filter_criteria(params.take("filter", dict))
    given = Params(params.take("actions", dict))
    # Each takes an address on the CLI, so a string here too; given one, it
    # is the provider that refuses it, with its pointer.
    refused = [name for name in REFUSED_ACTIONS if given.take(name, str, "")]
    flags = ["\\Seen"] if given.take("mark_read", bool, False) else []

    for flag in given.take("flags", list, []):
        if flag not in flags:
            flags.append(flag)

    spec = ActionSpec(
        fileinto=given.take("move_to", str, None),
        discard=given.take("discard", bool, False),
        flags=tuple(flags),
        keep=given.take("keep", bool, False),
        stop=given.take("stop", bool, None),
    )
    given.done()

    request = RuleRequest(
        criteria=criteria,
        actions=spec,
        name=params.take("name", str, None),
        script=params.take("filterset", str, None),
        replace=params.take("replace", bool, False),
        placement=placement(params.take("placement", dict, None)),
        activate=params.take("activate", bool, False),
    )
    create = params.take("create_folder", bool, False)
    subscribe = params.take("subscribe", bool, True)
    params.done()

    if not subscribe and not create:
        raise MailctlError(
            "subscribe only shapes a folder this change creates; it needs "
            "create_folder",
            code="subscribe_needs_create",
        )

    utilities.rules.reject_actions(config, refused)
    utilities.rules.check_rule(config, request)

    folder = utilities.folders.plan_folder(
        session, config, spec.fileinto, create=create, subscribe=subscribe
    )
    utilities.folders.check_folder(session, folder)
    missing = utilities.rules.missing_extensions(session, spec, folder)
    plan = utilities.rules.plan_rule(session, config, request, folder)

    return {
        "plan_id": server._keep("filter add", plan),
        "document": json_output.add_plan(plan, spec, missing),
    }


# ----------------------------------------------------------------------------
def filter_add_execute(server: Server, params: Params, ident: object) -> dict:
    """Carry out a ``filter.add.plan``: the backup, the upload, and any
    folder it creates, each sent as an ``event`` as it lands."""
    session, config = server._open()
    entry = server._take_plan(params.take("plan_id", str), "filter add")
    params.done()

    backup = utilities.rules.execute_script_change(
        session, config, entry.record, server._events(ident)
    )

    return json_output.script_change_result("filter add", entry.record, backup)


# ----------------------------------------------------------------------------
def plan_discard(server: Server, params: Params, _ident: object) -> None:
    """Drop a waiting plan, whichever command made it."""
    server._take_plan(params.take("plan_id", str), None)
    params.done()


# ----------------------------------------------------------------------------
def filter_criteria(document: dict) -> Criteria:
    """A filter document's criteria, as ``filter add --filter`` reads one.

    Re-serialised for the one reader there is, so the API and a filter
    file are refused for the same things in the same words. A ``password``
    member was wrapped as the line was parsed, and no filter has one.
    """
    try:
        text = json.dumps(document)

    except TypeError as exc:
        raise RpcError(
            INVALID_PARAMS, "'filter' holds a password, which no filter has"
        ) from exc

    return load_filter(text)


# ----------------------------------------------------------------------------
def placement(given: dict | None) -> Placement | None:
    """Where the rule goes: ``where`` is first, last, before, or after,
    and ``filter`` names the filter the last two are relative to."""
    if given is None:
        return None

    values = Params(given)
    where = values.take("where", str)
    anchor = values.take("filter", str, None)
    values.done()

    if where not in PLACEMENTS:
        raise RpcError(
            INVALID_PARAMS,
            f"placement 'where' must be one of {', '.join(PLACEMENTS)}",
        )

    if where in (PLACE_BEFORE, PLACE_AFTER) and not anchor:
        raise RpcError(
            INVALID_PARAMS, f"placement {where!r} needs a 'filter' to name"
        )

    if where in (PLACE_FIRST, PLACE_LAST) and anchor is not None:
        raise RpcError(
            INVALID_PARAMS, f"placement {where!r} takes no 'filter'"
        )

    return Placement(where, anchor)


# ----------------------------------------------------------------------------
def unexpected(exc: Exception) -> RpcError:
    """A failure nothing expected, named by its type alone: its text may
    carry anything, a credential included. ``--debug`` shows the rest."""
    return RpcError(INTERNAL_ERROR, f"unexpected {type(exc).__name__}")


# Every method, by name. A command's name is its path in the CLI's tree,
# dotted; a write command is a ``.plan`` and an ``.execute``. The rest are
# the protocol's own (``PROTOCOL_METHODS``).
METHODS: dict[str, Callable[[Server, Params, object], Any]] = {
    "initialize": initialize,
    "shutdown": shutdown,
    "plan.discard": plan_discard,
    "filter.list": filter_list,
    "mail.search": mail_search,
    "mail.view": mail_view,
    "filter.add.plan": filter_add_plan,
    "filter.add.execute": filter_add_execute,
}

PROTOCOL_METHODS = frozenset({"initialize", "shutdown", "plan.discard"})


# ############################################################################
# Starting it
# ############################################################################


# ----------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """The process's arguments: the CLI's connection settings, so a
    client starts the API with what a user would give ``mailctl``.

    The one left out is ``--password``: an argument is readable in the
    process list, and the API has its own way to ask (credentials.prompt).
    """
    parser = argparse.ArgumentParser(
        prog="mailctl-api",
        description="Serve mailctl's operations as JSON-RPC 2.0 over "
        "standard input and output, one message per line.",
    )
    parser.add_argument(
        "--version", action="version", version=f"mailctl {__version__}"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="a failed request's traceback on stderr (never credentials)",
    )
    parser.add_argument(
        "--folder",
        help="the folder mail methods read when a request names none; "
        "default source_folder from the config, else INBOX",
    )

    group = parser.add_argument_group("connection")
    group.add_argument(
        "--provider", help="the mail host, by provider name; default mxroute"
    )
    group.add_argument("--host", help="the mail server")
    group.add_argument("--user", help="full email address (the username)")

    credential = group.add_mutually_exclusive_group()
    credential.add_argument(
        "--password-file",
        dest="password_file",
        metavar="PATH",
        help="file whose contents are the password; must be mode 0600 "
        "(or 0400)",
    )
    credential.add_argument(
        "--password-cmd",
        dest="password_cmd",
        help="command whose stdout is the password",
    )
    group.add_argument(
        "--env-file",
        dest="env_file",
        nargs="?",
        const=".env",
        metavar="PATH",
        help="read MAILCTL_* settings from a dotenv-style file (default "
        ".env in the current directory)",
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
        help="where filter set backups are written",
    )
    group.add_argument(
        "--disable-extension",
        dest="disable_extension",
        action="append",
        metavar="NAME",
        help="never emit this extension; repeatable",
    )

    return parser
