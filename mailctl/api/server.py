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
from pathlib import Path
from typing import Any, TextIO

from .. import MailctlError, __version__, engine, json_output, utilities
from ..config import SIEVE_TLS_MODES, Config, Secret, load_config
from ..criteria import Criteria, dump_filter, load_filter
from ..utilities.events import ServerAlert
from ..utilities.folders import FolderPlan
from ..utilities.mail import DEFAULT_MAX_MESSAGES, MailActionPlan
from ..utilities.messages import DEFAULT_LIST_LIMIT, SortOrder
from ..utilities.optimize import KINDS as OPTIMIZE_KINDS
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


@dataclass(frozen=True)
class _MailPass:
    """A ``filter.apply.plan``: the existing-mail plan (None where the
    actions leave matching mail as it is), the target folder's plan, and
    the ceiling the execute holds it to."""

    mail: MailActionPlan | None
    folder: FolderPlan
    max_messages: int

    # ------------------------------------------------------------------------
    @property
    def changes(self) -> bool:
        return bool(self.mail and self.mail.changes)


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
    ``old_name`` is what the rename from ``mxfilter`` left behind, by name
    and place, as the CLI warns of it before every command.
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
        "old_name": json_output.old_name(
            utilities.migration.check_legacy_settings(config),
            utilities.migration.check_config_dir(),
        ),
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

    report = utilities.rules.read_rules(session, filterset)

    return json_output.rules_report(
        report, utilities.rules.describe_rules(session, report.rules)
    )


# ----------------------------------------------------------------------------
def mail_search(server: Server, params: Params, _ident: object) -> dict:
    """``mail search``: the newest messages in ``folder`` that ``filter``
    (a filter document) or ``raw`` (the host's own query) selects, at most
    ``limit`` of them (null for all), or in the order ``sort`` names.

    ``like`` (``uid``, and ``uidvalidity`` where the UID was listed under
    one) adds criteria derived from that message in ``folder``, merged
    with ``filter`` as ``mail search --like`` merges them. With
    ``build_filter`` true the result is the filter document the criteria
    make, as ``filter.add.plan`` takes it, and nothing is listed or saved.
    """
    session, config = server._open()
    folder = params.take("folder", str, config.source_folder)
    document = params.take("filter", dict, None)
    raw = params.take("raw", str, None)
    limit = params.take("limit", int, DEFAULT_LIST_LIMIT, null=None)
    sort = params.take("sort", dict, None)
    like = params.take("like", dict, None)
    build_filter = params.take("build_filter", bool, False)
    params.done()

    if raw is not None and (build_filter or like is not None):
        raise RpcError(
            INVALID_PARAMS,
            "'raw' is a query, not criteria; it cannot be combined with "
            "'like' or 'build_filter'",
        )

    if sort is not None and build_filter:
        raise RpcError(
            INVALID_PARAMS,
            "'sort' orders a listing; 'build_filter' returns a filter, "
            "which has no order",
        )

    order = None

    if sort is not None:
        given = Params(sort)
        order = SortOrder(
            given.take("key", str), given.take("reverse", bool, False)
        )
        given.done()

    criteria = None if document is None else filter_criteria(document)

    if like is not None:
        given = Params(like)
        uid = given.take("uid", int)
        uidvalidity = given.take("uidvalidity", int, None)
        given.done()

        if uid < 1:
            raise RpcError(INVALID_PARAMS, "'uid' must be at least 1")

        derived = utilities.mail.criteria_like(
            session, folder, uid, criteria, uidvalidity=uidvalidity
        )
        derived.criteria.require_terms()
        folder, criteria = derived.message.folder, derived.criteria

    if build_filter:
        # dump_filter refuses an empty set, coded, as the CLI's does.
        return json.loads(dump_filter(criteria or Criteria()))

    listing = utilities.messages.list_messages(
        session,
        folder,
        criteria=criteria,
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
    spec, refused = action_spec(params.take("actions", dict))

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
    folder it creates, each sent as an ``event`` as it lands.

    A plan that changes nothing is carried out all the same, as ``filter
    add`` does on the CLI: a filter replaced by itself is still uploaded,
    with its backup."""
    session, config = server._open()
    plan_id = params.take("plan_id", str)
    params.done()
    entry = server._take_plan(plan_id, "filter add")

    backup = utilities.rules.execute_script_change(
        session, config, entry.record, server._events(ident)
    )

    return json_output.script_change_result(
        "filter add", entry.record.name, entry.record, backup
    )


# ----------------------------------------------------------------------------
def filter_apply_plan(server: Server, params: Params, _ident: object) -> dict:
    """Plan ``filter apply``: the messages in ``folder`` that ``filter`` (a
    filter document) selects, and what ``actions`` would do to them. No
    filter is saved, and nothing is moved, flagged, or created.

    ``max_messages`` is the ceiling: a plan over it is returned, with
    ``over_limit`` true, and its execute is refused whole. Returns
    ``plan_id`` and ``document``, the plan as ``filter apply --dry-run
    --json`` prints it.
    """
    session, config = server._open()
    criteria = filter_criteria(params.take("filter", dict))
    spec, refused = action_spec(params.take("actions", dict), stop=False)
    folder_name = params.take("folder", str, config.source_folder)
    max_messages = params.take("max_messages", int, DEFAULT_MAX_MESSAGES)
    create = params.take("create_folder", bool, False)
    subscribe = params.take("subscribe", bool, True)
    params.done()

    if not subscribe and not create:
        raise MailctlError(
            "subscribe only shapes a folder this change creates; it needs "
            "create_folder",
            code="subscribe_needs_create",
        )

    if max_messages < 0:
        raise RpcError(INVALID_PARAMS, "'max_messages' cannot be negative")

    utilities.rules.reject_actions(config, refused)

    folder = utilities.folders.plan_folder(
        session,
        config,
        spec.fileinto,
        create=create,
        subscribe=subscribe,
        delivery_route=False,
    )
    utilities.mail.require_mail_action(folder, spec)
    utilities.mail.require_target_folder(folder)

    source = utilities.mail.source_folder(session, folder_name)
    mail = (
        None
        if utilities.mail.mail_pass_is_noop(spec, source, folder.folder)
        else utilities.mail.plan_mail(
            session, criteria, spec, source, folder.folder
        )
    )

    return {
        "plan_id": server._keep(
            "filter apply", _MailPass(mail, folder, max_messages)
        ),
        "document": json_output.apply_plan(
            criteria, spec, folder, max_messages, mail
        ),
    }


# ----------------------------------------------------------------------------
def filter_apply_execute(
    server: Server, params: Params, ident: object
) -> dict:
    """Carry out a ``filter.apply.plan``: any folder it creates, sent as an
    ``event``, then the messages it moves, flags, copies, or deletes.
    Refused, touching nothing, over the plan's ceiling or once the
    folder's UIDs have been renumbered."""
    session, _config = server._open()
    plan_id = params.take("plan_id", str)
    params.done()
    entry = server._take_plan(plan_id, "filter apply")

    record = entry.record
    require_changes(record.changes, "filter apply")

    # changes is only true where a mail plan was made.
    assert record.mail is not None

    result = utilities.mail.execute_mail(
        session,
        record.mail,
        record.max_messages,
        record.folder,
        server._events(ident),
    )

    return json_output.mail_result(result, record.mail)


# ----------------------------------------------------------------------------
def filter_remove_plan(server: Server, params: Params, _ident: object) -> dict:
    """Plan ``filter remove``: the filter ``name`` taken out of the filter
    set, the active one unless ``filterset`` names another; ``activate``
    makes that one active too."""
    session, _config = server._open()
    name = params.take("name", str)
    filterset = params.take("filterset", str, None)
    activate = params.take("activate", bool, False)
    params.done()

    plan = utilities.rules.plan_removal(session, name, filterset, activate)

    return {
        "plan_id": server._keep("filter remove", plan),
        "document": json_output.removal_plan(plan),
    }


# ----------------------------------------------------------------------------
def filter_move_plan(server: Server, params: Params, _ident: object) -> dict:
    """Plan ``filter move``: the filter ``name`` put where ``placement``
    says (as ``filter.add.plan`` takes it), otherwise unchanged."""
    session, config = server._open()
    name = params.take("name", str)
    where = placement(params.take("placement", dict))
    filterset = params.take("filterset", str, None)
    activate = params.take("activate", bool, False)
    params.done()

    # A placement is required, so there is one.
    assert where is not None

    utilities.rules.check_move(config)
    plan = utilities.rules.plan_move(session, name, where, filterset, activate)

    return {
        "plan_id": server._keep("filter move", plan),
        "document": json_output.move_plan(plan),
    }


# ----------------------------------------------------------------------------
def filter_rename_plan(server: Server, params: Params, _ident: object) -> dict:
    """Plan ``filter rename``: the filter ``name`` called ``new_name``, its
    every other byte kept."""
    session, config = server._open()
    name = params.take("name", str)
    new_name = params.take("new_name", str)
    filterset = params.take("filterset", str, None)
    activate = params.take("activate", bool, False)
    params.done()

    utilities.rules.check_rename(config, new_name)
    plan = utilities.rules.plan_rename(
        session, name, new_name, filterset, activate
    )

    return {
        "plan_id": server._keep("filter rename", plan),
        "document": json_output.rename_plan(plan),
    }


# ----------------------------------------------------------------------------
def switch_plan(enable: bool) -> Callable[[Server, Params, object], dict]:
    """Plan ``filter enable`` or ``filter disable``: the filter ``name``
    switched on or off, kept in the filter set."""
    command = "filter enable" if enable else "filter disable"

    def method(server: Server, params: Params, _ident: object) -> dict:
        session, config = server._open()
        name = params.take("name", str)
        filterset = params.take("filterset", str, None)
        activate = params.take("activate", bool, False)
        params.done()

        utilities.rules.check_switch(config)
        plan = utilities.rules.plan_switch(
            session, name, enable, filterset, activate
        )

        return {
            "plan_id": server._keep(command, plan),
            "document": json_output.switch_plan(plan),
        }

    method.__doc__ = switch_plan.__doc__

    return method


# ----------------------------------------------------------------------------
def filter_optimize_plan(
    server: Server, params: Params, _ident: object
) -> dict:
    """Plan ``filter optimize``: a better arrangement of the filter set's
    filters, proposing every kind of change but those ``skip`` names
    (redundant, reorder, merge)."""
    session, config = server._open()
    filterset = params.take("filterset", str, None)
    activate = params.take("activate", bool, False)
    skip = params.take("skip", list, [])
    params.done()

    unknown = sorted(set(skip) - set(OPTIMIZE_KINDS))

    if unknown:
        raise RpcError(
            INVALID_PARAMS,
            f"'skip' takes only {', '.join(OPTIMIZE_KINDS)}",
        )

    utilities.optimize.check_optimize(config)
    kinds = [kind for kind in OPTIMIZE_KINDS if kind not in skip]
    plan = utilities.optimize.plan_optimize(
        session, filterset, kinds, activate
    )

    return {
        "plan_id": server._keep("filter optimize", plan),
        "document": json_output.optimize_plan(plan),
    }


# ----------------------------------------------------------------------------
def script_execute(
    command: str,
    name: Callable[[Any], str | None],
    execute: Callable[..., Path] = utilities.rules.execute_script_change,
) -> Callable[[Server, Params, object], dict]:
    """Carry out a plan of ``command``, one that uploads the filter set:
    the backup, then the upload, each sent as an ``event`` as it lands.
    ``name`` is the filter's name once it is done, from the plan, and
    ``execute`` the utility that carries the plan out.

    Refused, sending nothing, where the plan changes nothing, or the
    filter set changed since the plan was made."""

    def method(server: Server, params: Params, ident: object) -> dict:
        session, config = server._open()
        plan_id = params.take("plan_id", str)
        params.done()
        entry = server._take_plan(plan_id, command)

        record = entry.record
        require_changes(record.changes, command)
        backup = execute(session, config, record, server._events(ident))

        return json_output.script_change_result(
            command, name(record), record, backup
        )

    method.__doc__ = script_execute.__doc__

    return method


# ----------------------------------------------------------------------------
def require_changes(changes: bool, command: str) -> None:
    """Refuse to carry out a plan that changes nothing, as the CLI stops
    after showing one; the plan is used up either way."""
    if not changes:
        raise MailctlError(
            f"the {command} plan changes nothing, so there is nothing to "
            f"carry out",
            code="nothing_to_change",
        )


# ----------------------------------------------------------------------------
def plan_discard(server: Server, params: Params, _ident: object) -> None:
    """Drop a waiting plan, whichever command made it."""
    plan_id = params.take("plan_id", str)
    params.done()
    server._take_plan(plan_id, None)


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
def action_spec(given: dict, *, stop: bool = True) -> tuple[ActionSpec, list]:
    """What a filter does, from ``actions``, and the actions named that
    the CLI refuses by name, for the provider to refuse.

    ``stop`` is whether ``stop`` is taken: it means something to a saved
    filter and nothing to mail already delivered.
    """
    values = Params(given)
    # Each takes an address on the CLI, so a string here too; given one, it
    # is the provider that refuses it, with its pointer.
    refused = [name for name in REFUSED_ACTIONS if values.take(name, str, "")]
    flags = ["\\Seen"] if values.take("mark_read", bool, False) else []

    for flag in values.take("flags", list, []):
        if flag not in flags:
            flags.append(flag)

    spec = ActionSpec(
        fileinto=values.take("move_to", str, None),
        discard=values.take("discard", bool, False),
        flags=tuple(flags),
        keep=values.take("keep", bool, False),
        stop=values.take("stop", bool, None) if stop else None,
    )
    values.done()

    return spec, refused


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
    "filter.apply.plan": filter_apply_plan,
    "filter.apply.execute": filter_apply_execute,
    "filter.remove.plan": filter_remove_plan,
    "filter.remove.execute": script_execute(
        "filter remove", lambda plan: plan.rule
    ),
    "filter.move.plan": filter_move_plan,
    "filter.move.execute": script_execute(
        "filter move", lambda plan: plan.rule
    ),
    "filter.rename.plan": filter_rename_plan,
    "filter.rename.execute": script_execute(
        "filter rename", lambda plan: plan.new_name
    ),
    "filter.enable.plan": switch_plan(True),
    "filter.enable.execute": script_execute(
        "filter enable", lambda plan: plan.rule
    ),
    "filter.disable.plan": switch_plan(False),
    "filter.disable.execute": script_execute(
        "filter disable", lambda plan: plan.rule
    ),
    "filter.optimize.plan": filter_optimize_plan,
    "filter.optimize.execute": script_execute(
        "filter optimize",
        lambda plan: None,
        utilities.optimize.execute_optimize,
    ),
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
