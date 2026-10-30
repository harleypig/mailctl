"""The API's framing and parameters, in-process.

What crosses the process boundary is ``test_api.py``'s. This is the part
that is easier to pin from inside: where a password is wrapped as a line
is parsed, what a server-to-client request does with the messages that
arrive while it waits, and that no refusal quotes what the client sent.
"""

import io

import pytest

from mailctl.api.protocol import (
    INVALID_PARAMS,
    PARSE_ERROR,
    Channel,
    ClientGone,
    RpcError,
    parse,
)
from mailctl.api.server import Params
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
        b"not json\n",
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

    with pytest.raises(RpcError):
        link.receive()

    assert link.receive() == {"jsonrpc": "2.0", "id": 8, "method": "shutdown"}
    assert link.receive() is None


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
