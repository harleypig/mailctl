"""The API's framing and parameters, in-process.

What crosses the process boundary is ``test_api.py``'s. This is the part
that is easier to pin from inside: where a password is wrapped as a line
is parsed, what a server-to-client request does with the messages that
arrive while it waits, and that no refusal quotes what the client sent.
"""

import argparse
import io
import json

import pytest

from mailctl import MailctlError
from mailctl.api import server as server_module
from mailctl.api.protocol import (
    INVALID_PARAMS,
    PARSE_ERROR,
    Channel,
    ClientGone,
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


# ############################################################################
# Parameters
# ############################################################################


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
