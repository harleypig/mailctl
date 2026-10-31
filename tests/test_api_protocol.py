"""The API's framing and parameters, in-process.

What crosses the process boundary is ``test_api.py``'s. This is the part
that is easier to pin from inside: where a password is wrapped as a line
is parsed, what a server-to-client request does with the messages that
arrive while it waits, and that no refusal quotes what the client sent.
"""

import argparse
import io
import json
from typing import TypeGuard

import pytest

from mailctl import MailctlError
from mailctl.api import server as server_module
from mailctl.api.protocol import (
    INVALID_PARAMS,
    PARSE_ERROR,
    Channel,
    ClientGone,
    RequestCancelled,
    RpcError,
    parse,
)
from mailctl.api.server import Params, Server
from mailctl.config import Secret

SENTINEL = "sentinel-value-3b81d0"


# ----------------------------------------------------------------------------
def channel(*lines: bytes) -> tuple[Channel, io.BytesIO]:
    out = io.BytesIO()

    return Channel(io.BytesIO(b"".join(lines)), out), out


# ############################################################################
# Parsing a line
# ############################################################################


# ----------------------------------------------------------------------------
def test_every_string_password_is_a_secret_as_the_line_is_parsed():
    """Wherever it sits: a client that put one in the wrong place still
    cannot have it echoed."""
    message = parse(
        b'{"result": {"password": "%s"}, "params": [{"password": "%s"}]}'
        % (SENTINEL.encode(), SENTINEL.encode())
    )

    assert isinstance(message, dict)

    outer = message["result"]["password"]
    inner = message["params"][0]["password"]

    assert isinstance(outer, Secret) and outer.reveal() == SENTINEL
    assert isinstance(inner, Secret)
    assert SENTINEL not in repr(message)


# ----------------------------------------------------------------------------
def test_a_password_that_is_not_a_string_is_left_as_it_is():
    message = parse(b'{"password": 5}')

    assert message == {"password": 5}


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "line",
    [
        b'{"password": "%s"' % SENTINEL.encode(),
        b"\xff" + SENTINEL.encode(),
        b"[" * 100_000,
    ],
)
def test_a_line_that_does_not_parse_is_refused_without_quoting_it(line):
    with pytest.raises(RpcError) as caught:
        parse(line)

    assert caught.value.code == PARSE_ERROR
    assert SENTINEL not in caught.value.message


# ############################################################################
# The channel
# ############################################################################


# ----------------------------------------------------------------------------
def test_a_message_is_one_ascii_line():
    link, out = channel()
    link.notify("log", {"message": "café\nnext"})

    line = out.getvalue()

    assert line.count(b"\n") == 1
    assert line.endswith(b"\n")
    assert line.isascii()


# ----------------------------------------------------------------------------
def test_a_credential_is_never_sent():
    link, out = channel()

    with pytest.raises(TypeError, match="never serialised"):
        link.respond(1, {"password": Secret(SENTINEL)})

    assert SENTINEL.encode() not in out.getvalue()


# ----------------------------------------------------------------------------
def test_what_arrives_while_a_request_waits_is_received_after_in_order():
    link, out = channel(
        b'{"jsonrpc": "2.0", "id": 7, "method": "filter.list"}\n',
        b"\n",
        b'{"jsonrpc": "2.0", "id": "mailctl-1", "result": {"ok": true}}\n',
        b'{"jsonrpc": "2.0", "id": 8, "method": "shutdown"}\n',
    )

    assert link.request("credentials.prompt", {}) == {"ok": True}
    assert b'"id": "mailctl-1"' in out.getvalue()
    assert link.receive() == {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "filter.list",
    }
    assert link.receive() == {"jsonrpc": "2.0", "id": 8, "method": "shutdown"}
    assert link.receive() is None


# ----------------------------------------------------------------------------
def test_a_line_that_does_not_parse_while_an_answer_waits_declines_it():
    """It may be the answer, mistyped, so holding it would leave both
    sides waiting. It is answered as any bad line is, with a null id, and
    the wait ends as a declined answer; what follows is received next."""
    link, out = channel(
        b'{"jsonrpc": "2.0", "id": "mailctl-1", "result": {"password": "x"\n',
        b'{"jsonrpc": "2.0", "id": 8, "method": "shutdown"}\n',
    )

    with pytest.raises(RpcError) as caught:
        link.request("credentials.prompt", {})

    assert caught.value.code == PARSE_ERROR

    sent = [json.loads(line) for line in out.getvalue().splitlines()]

    assert sent[-1] == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {
            "code": PARSE_ERROR,
            "message": "a message must be one JSON value on one line",
        },
    }
    assert link.receive() == {"jsonrpc": "2.0", "id": 8, "method": "shutdown"}


# ----------------------------------------------------------------------------
def test_an_answer_to_another_id_is_not_taken_for_the_awaited_one():
    link, _out = channel(
        b'{"jsonrpc": "2.0", "id": "mailctl-9", "result": 1}\n',
        b'{"jsonrpc": "2.0", "id": "mailctl-1", "result": 2}\n',
    )

    assert link.request("credentials.prompt", {}) == 2


# ----------------------------------------------------------------------------
def test_an_error_answer_raises_and_the_end_of_input_is_named():
    declined, _ = channel(
        b'{"jsonrpc": "2.0", "id": "mailctl-1", "error": {"code": 5}}\n'
    )

    with pytest.raises(RpcError) as caught:
        declined.request("credentials.prompt", {})

    assert caught.value.code == 5

    gone, _ = channel()

    with pytest.raises(ClientGone):
        gone.request("credentials.prompt", {})


# ----------------------------------------------------------------------------
def read_all(link: Channel) -> Channel:
    """``link``, once its input thread has read every line there is."""
    link._pump.join(timeout=5)

    assert not link._pump.is_alive()

    return link


# ----------------------------------------------------------------------------
def is_cancel(message: object) -> TypeGuard[dict]:
    return isinstance(message, dict) and message.get("method") == CANCEL


# ----------------------------------------------------------------------------
def ident(message: object) -> object:
    assert isinstance(message, dict)

    return message["id"]


CANCEL = "$/cancelRequest"
CANCEL_7 = (
    b'{"jsonrpc": "2.0", "method": "$/cancelRequest", "params": {"id": 7}}\n'
)
REQUEST_8 = b'{"jsonrpc": "2.0", "id": 8, "method": "filter.list"}\n'
SHUTDOWN_9 = b'{"jsonrpc": "2.0", "id": 9, "method": "shutdown"}\n'


# ----------------------------------------------------------------------------
def test_take_picks_out_what_it_selects_and_leaves_the_rest_in_order():
    """What a running request takes must not cost the requests behind it
    their place. Red if take drops, reorders, or takes what it was not
    asked for, or if a bad line loses its turn."""
    link, _out = channel(REQUEST_8, CANCEL_7, b"{bad\n", SHUTDOWN_9, CANCEL_7)
    read_all(link)

    taken = link.take(is_cancel)

    assert [message["params"]["id"] for message in taken] == [7, 7]
    assert [ident(message) for message in link.held()] == [8, 9]
    assert ident(link.receive()) == 8

    with pytest.raises(RpcError):
        link.receive()

    assert ident(link.receive()) == 9
    assert link.receive() is None


# ----------------------------------------------------------------------------
def test_a_message_the_wait_is_interrupted_by_ends_it_and_the_rest_wait():
    """A cancel while the server waits on the client ends the wait; it is
    taken, and what came before it is received after, in order. Red if the
    wait outlasts the cancel."""
    link, _out = channel(REQUEST_8, CANCEL_7, SHUTDOWN_9)

    with pytest.raises(RequestCancelled):
        link.request("credentials.prompt", {}, interrupt=is_cancel)

    assert ident(link.receive()) == 8
    assert ident(link.receive()) == 9


class Trickle(io.RawIOBase):
    """A stream that hands over a few bytes per read, as a pipe may."""

    # ------------------------------------------------------------------------
    def __init__(self, data: bytes, size: int):
        self._data = data
        self._size = size

    # ------------------------------------------------------------------------
    def readable(self) -> bool:
        return True

    # ------------------------------------------------------------------------
    def readinto(self, buffer) -> int:
        piece, self._data = self._data[: self._size], self._data[self._size :]
        buffer[: len(piece)] = piece

        return len(piece)


# ----------------------------------------------------------------------------
@pytest.mark.parametrize("size", [1, 7, 4096])
def test_lines_split_across_reads_arrive_whole(size):
    """However the input arrives, a message is a line; a last one with no
    newline still counts. Red if a line is cut where a read ends."""
    link = Channel(
        Trickle(REQUEST_8 + b"\n" + SHUTDOWN_9.rstrip(b"\n"), size),
        io.BytesIO(),
    )

    assert ident(link.receive()) == 8
    assert ident(link.receive()) == 9
    assert link.receive() is None


# ############################################################################
# Parameters
# ############################################################################


# ----------------------------------------------------------------------------
def test_null_has_its_own_meaning_only_where_one_is_given():
    assert Params({"x": None}).take("x", int, 4, null=None) is None
    assert Params({}).take("x", int, 4, null=None) == 4
    assert Params({"x": 7}).take("x", int, 4, null=None) == 7


# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("kind", "value"),
    [(int, SENTINEL), (int, True), (str, 3), (bool, SENTINEL), (list, [1])],
)
def test_a_wrong_type_is_named_and_the_value_is_not(kind, value):
    with pytest.raises(RpcError) as caught:
        Params({"x": value}).take("x", kind)

    assert caught.value.code == INVALID_PARAMS
    assert caught.value.message.startswith("'x' must be")
    assert SENTINEL not in caught.value.message


# ----------------------------------------------------------------------------
def test_null_is_absent_and_a_missing_requirement_is_refused():
    assert Params({"x": None}).take("x", int, 4) == 4

    with pytest.raises(RpcError, match="'x' is required"):
        Params({}).take("x", int)


# ----------------------------------------------------------------------------
def test_a_parameter_not_taken_is_refused_by_name():
    params = Params({"kept": 1, "extra": SENTINEL})
    params.take("kept", int)

    with pytest.raises(RpcError) as caught:
        params.done()

    assert caught.value.message == "unknown parameter(s) extra"


# ----------------------------------------------------------------------------
def test_no_method_takes_a_password():
    with pytest.raises(RpcError, match="no method takes a password"):
        Params({"password": Secret(SENTINEL)})


# ############################################################################
# The server, in-process
# ############################################################################


# ----------------------------------------------------------------------------
def served(*lines: bytes) -> tuple[Server, io.BytesIO]:
    link, out = channel(*lines)

    return Server(argparse.Namespace(debug=False), link), out


# ----------------------------------------------------------------------------
def test_a_plan_of_another_command_is_refused_and_left_waiting():
    """Checked before it is taken: an execute of the wrong command must
    not cost the plan its right one."""
    server, _out = served()
    plan_id = server._keep("filter add", object())

    with pytest.raises(MailctlError) as caught:
        server._take_plan(plan_id, "filter remove")

    assert caught.value.code == "plan_unknown"
    assert server._take_plan(plan_id, "filter add").command == "filter add"


# ----------------------------------------------------------------------------
def test_a_result_that_cannot_be_sent_is_an_internal_error_and_serving_goes_on(
    monkeypatch,
):
    """The response is built outside the method's own handling; a value
    no document maps -- a credential above all -- must not end the loop."""
    monkeypatch.setitem(
        server_module.METHODS, "plan.discard", lambda *_: Secret(SENTINEL)
    )
    server, out = served(
        b'{"jsonrpc": "2.0", "id": 1, "method": "plan.discard"}\n',
        b'{"jsonrpc": "2.0", "id": 2, "method": "shutdown"}\n',
    )

    server.run()

    answers = [json.loads(line) for line in out.getvalue().splitlines()]

    assert answers == [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32603, "message": "unexpected TypeError"},
        },
        {"jsonrpc": "2.0", "id": 2, "result": None},
    ]
    assert SENTINEL.encode() not in out.getvalue()
