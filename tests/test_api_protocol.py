"""The API's framing and parameters, in-process.

What crosses the process boundary is ``test_api.py``'s. This is the part
that is easier to pin from inside: where a password is wrapped as a line
is parsed, what a server-to-client request does with the messages that
arrive while it waits, and that no refusal quotes what the client sent.
"""

import argparse
import io
import json
import os
import resource
import threading
import time
from collections.abc import Callable
from typing import TypeGuard

import pytest

from mailctl import MailctlError
from mailctl.api import protocol
from mailctl.api import server as server_module
from mailctl.api.protocol import (
    INVALID_PARAMS,
    PARSE_ERROR,
    Channel,
    ClientGone,
    InputFull,
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
def nothing(_message: object) -> TypeGuard[dict]:
    return False


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

    assert [message["params"]["id"] for message, _ahead in taken] == [7, 7]
    assert [[ident(m) for m in ahead] for _message, ahead in taken] == [
        [8],
        [8, 9],
    ]
    assert ident(link.receive()) == 8

    with pytest.raises(RpcError):
        link.receive()

    assert ident(link.receive()) == 9
    assert link.receive() is None


# ----------------------------------------------------------------------------
def test_a_held_message_the_wait_is_interrupted_by_ends_it_before_asking():
    """A cancel already held when the wait begins -- read while a request
    ran, and passed over for another's sake -- ends it before anything is
    asked. Red if the wait looks only at what arrives after it: the
    question goes out and the input's end answers it."""
    link, out = channel(REQUEST_8, CANCEL_7)
    read_all(link)
    link.take(nothing)

    with pytest.raises(RequestCancelled):
        link.request("credentials.prompt", {}, interrupt=is_cancel)

    assert out.getvalue() == b""
    assert ident(link.receive()) == 8


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


# ----------------------------------------------------------------------------
def numbered(count: int) -> list[bytes]:
    """``count`` request lines, ids 0 up."""
    return [
        b'{"jsonrpc": "2.0", "id": %d, "method": "filter.list"}\n' % n
        for n in range(count)
    ]


# ----------------------------------------------------------------------------
def unserved(link: Channel, count: int) -> None:
    """Wait until ``count`` messages are read and not yet served."""
    deadline = time.monotonic() + 5

    while link._unserved < count:
        assert time.monotonic() < deadline, "the lines were never read"
        time.sleep(0.001)


# ----------------------------------------------------------------------------
def closed_within(link: Channel, seconds: float) -> bool:
    """Whether ``close`` returned within ``seconds``; a close that hangs
    is left to its daemon thread rather than hanging the run."""
    closer = threading.Thread(target=link.close, daemon=True)
    closer.start()
    closer.join(seconds)

    return not closer.is_alive()


# ----------------------------------------------------------------------------
def test_a_client_flooding_input_is_read_no_further_than_the_limit(
    monkeypatch,
):
    """Every byte is in the reader's hands at its first read, so only the
    bound stops it handing over all ten and ending (#346 - bound the input
    queue). Red if the input thread reads past the limit, or if take,
    moving what has arrived into the held messages, makes room for more."""
    monkeypatch.setattr(protocol, "INBOX_LIMIT", 4)
    link, _out = channel(*numbered(10))
    unserved(link, 4)

    # A thread that never waits ends at once; this one is waiting.
    link._pump.join(timeout=0.5)

    assert link._pump.is_alive()
    assert link._inbox.qsize() == 4

    link.take(nothing)
    link._pump.join(timeout=0.5)

    assert link._pump.is_alive()
    assert len(link._held) + link._inbox.qsize() == 4

    assert [ident(link.receive()) for _ in range(10)] == list(range(10))
    assert link.receive() is None


# ----------------------------------------------------------------------------
def test_close_stops_an_input_thread_waiting_for_room(monkeypatch):
    """Red if close leaves the thread waiting on a full inbox, so close
    never returns."""
    monkeypatch.setattr(protocol, "INBOX_LIMIT", 2)
    link, _out = channel(*numbered(5))
    unserved(link, 2)

    assert closed_within(link, 5)

    link._pump.join(timeout=5)

    assert not link._pump.is_alive()


# ----------------------------------------------------------------------------
def test_a_wait_with_the_limit_held_is_declined_rather_than_hung(
    monkeypatch,
):
    """While a password is asked, everything else the client sends is
    held; once the limit is held the input thread reads no further, so
    the answer can never arrive. The wait ends, saying so, and the held
    messages keep their turn. Red if the wait goes on reading: it
    hangs."""
    monkeypatch.setattr(protocol, "INBOX_LIMIT", 3)
    link, _out = channel(*numbered(5))
    declined: list[InputFull] = []

    def ask() -> None:
        try:
            link.request("credentials.prompt", {})

        except InputFull as error:
            declined.append(error)

    asker = threading.Thread(target=ask, daemon=True)
    asker.start()
    asker.join(5)

    assert declined, "the wait never ended"
    assert [ident(link.receive()) for _ in range(5)] == list(range(5))
    assert link.receive() is None


# ----------------------------------------------------------------------------
def test_a_prompt_declined_at_the_limit_carries_its_own_code(monkeypatch):
    """A password prompt the input limit ends is not the user declining:
    its answer could no longer be read. The client is told so by a code
    of its own, naming the limit, and the held messages keep their turn.
    Red if it is coded password_declined, or carries no code (#378)."""
    monkeypatch.setattr(protocol, "INBOX_LIMIT", 3)
    server, _out = served(*numbered(5))
    refused: list[MailctlError] = []

    def ask() -> None:
        try:
            server._prompt("Password: ")

        except MailctlError as error:
            refused.append(error)

    asker = threading.Thread(target=ask, daemon=True)
    asker.start()
    asker.join(5)

    assert refused, "the wait never ended"
    assert refused[0].code == "prompt_input_full"
    assert refused[0].fields == {"limit": 3}
    assert [ident(server.channel.receive()) for _ in range(5)] == list(
        range(5)
    )


# ----------------------------------------------------------------------------
def all_served(link: Channel) -> int:
    """Receive what is left, then the count still taken as unserved: zero
    unless a message left the channel without making room."""
    while True:
        try:
            if link.receive() is None:
                break

        except RpcError:
            pass

    return link._unserved


# ----------------------------------------------------------------------------
def test_a_line_that_does_not_parse_while_an_answer_waits_makes_room():
    """Each message consumed without making room costs a slot for good,
    and after INBOX_LIMIT of them the API reads nothing more. Red if the
    wait's declined bad line keeps its slot."""
    link, _out = channel(b"{bad\n", REQUEST_8)
    read_all(link)

    with pytest.raises(RpcError):
        link.request("credentials.prompt", {})

    assert all_served(link) == 0


# ----------------------------------------------------------------------------
def test_a_cancel_that_ends_a_wait_makes_room():
    """Red if the cancel that ended the wait keeps its slot."""
    link, _out = channel(REQUEST_8, CANCEL_7)
    read_all(link)

    with pytest.raises(RequestCancelled):
        link.request("credentials.prompt", {}, interrupt=is_cancel)

    assert all_served(link) == 0


# ----------------------------------------------------------------------------
def test_a_held_cancel_that_ends_a_wait_makes_room():
    """Red if a cancel already held when the wait begins keeps its slot
    when it is taken."""
    link, _out = channel(REQUEST_8, CANCEL_7)
    read_all(link)
    link.take(nothing)

    with pytest.raises(RequestCancelled):
        link.request("credentials.prompt", {}, interrupt=is_cancel)

    assert all_served(link) == 0


# ----------------------------------------------------------------------------
def test_an_input_descriptor_past_1024_is_read():
    """``select`` refuses a descriptor of 1024 or more, which a process
    holding many files hands out; the input then read as ended. Red if the
    thread waits on the input with select."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)

    if hard != resource.RLIM_INFINITY and hard < 1200:
        pytest.skip("the descriptor limit stops short of 1024")

    resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, 1200), hard))
    filler = [os.open(os.devnull, os.O_RDONLY) for _ in range(1100)]

    try:
        reader, writer = os.pipe()

        assert reader >= 1024

        with open(reader, "rb", buffering=0) as stream:
            link = Channel(stream, io.BytesIO())
            os.write(writer, REQUEST_8)

            assert ident(link.receive()) == 8

            assert closed_within(link, 5)
            os.close(writer)

    finally:
        for fd in filler:
            os.close(fd)

        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


class Blocking(io.RawIOBase):
    """A stream with no descriptor whose read waits until released."""

    # ------------------------------------------------------------------------
    def __init__(self):
        self.release = threading.Event()

    # ------------------------------------------------------------------------
    def readable(self) -> bool:
        return True

    # ------------------------------------------------------------------------
    def readinto(self, buffer) -> int:
        self.release.wait()
        buffer[:1] = b"\n"

        return 1


# ----------------------------------------------------------------------------
def test_close_does_not_wait_on_a_read_it_cannot_wake():
    """With no descriptor there is nothing to wake the read, so close
    leaves the thread to end at its next step, as a daemon. Red if close
    joins it: run would never return."""
    stream = Blocking()
    link = Channel(stream, io.BytesIO())

    assert closed_within(link, 5)

    stream.release.set()
    link._pump.join(timeout=5)

    assert not link._pump.is_alive()


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


# ############################################################################
# Cancelling, in-process
# ############################################################################


def request(ident: object, method: str) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "method": method, "params": {}}


def cancel_of(ident: object) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "$/cancelRequest",
        "params": {"id": ident},
    }


class Piped:
    """A server reading a pipe the test writes to as the client would,
    while it serves; each line is in the channel's hands before
    :meth:`line` returns."""

    # ------------------------------------------------------------------------
    def __init__(self):
        reader, self._writer = os.pipe()
        self.out = io.BytesIO()
        # Outlives this call; the ``piped`` fixture closes it.
        self.reader = open(reader, "rb", buffering=0)  # noqa: SIM115
        self.link = Channel(self.reader, self.out)
        self.server = Server(argparse.Namespace(debug=False), self.link)

    # ------------------------------------------------------------------------
    def line(self, message: dict) -> None:
        before = self.link._inbox.qsize()
        os.write(self._writer, (json.dumps(message) + "\n").encode())
        deadline = time.monotonic() + 5

        while self.link._inbox.qsize() <= before:
            assert time.monotonic() < deadline, "the line was never read"
            time.sleep(0.001)

    # ------------------------------------------------------------------------
    def last(self, message: dict) -> None:
        """Send ``message``, then end the input."""
        self.line(message)
        self.close()

    # ------------------------------------------------------------------------
    def close(self) -> None:
        """End the input, as a client that hangs up does; once."""
        writer, self._writer = self._writer, -1

        if writer != -1:
            os.close(writer)

    # ------------------------------------------------------------------------
    def answers(self) -> list[dict]:
        return [json.loads(line) for line in self.out.getvalue().splitlines()]


# ----------------------------------------------------------------------------
def after_answer(piped: Piped, result: object, then: Callable[[], None]):
    """Run ``then`` once ``result`` has been answered, as a client reacting
    to the answer would."""
    respond = piped.link.respond

    def answering(ident: object, value: object) -> None:
        respond(ident, value)

        if value == result:
            then()

    piped.link.respond = answering  # type: ignore[method-assign]


# ----------------------------------------------------------------------------
@pytest.fixture
def piped(monkeypatch):
    monkeypatch.setitem(server_module.METHODS, "test.again", lambda *_: "ran")
    made: list[Piped] = []

    def make() -> Piped:
        made.append(Piped())

        return made[-1]

    yield make

    for each in made:
        each.link.close()
        each.close()
        each.reader.close()


# ----------------------------------------------------------------------------
def test_no_input_thread_is_left_reading_once_run_returns(piped):
    """Run in-process, as a test or an embedding would: after shutdown the
    client's pipe is still open and empty, and nothing may be left blocked
    reading it (#346 - stop the reader). Red if run returns without
    stopping the input thread."""
    client = piped()
    client.line(request(1, "shutdown"))
    client.server.run()

    assert [(a["id"], a.get("result")) for a in client.answers()] == [
        (1, None)
    ]
    assert not client.link._pump.is_alive()
    assert client.link._pump not in threading.enumerate()


# ----------------------------------------------------------------------------
def test_the_input_thread_is_stopped_even_when_closing_the_session_fails(
    piped, monkeypatch
):
    """Red if a failure closing the session skips stopping the reader.
    ``shutdown`` closes the session itself, so only run's own close, the
    second, fails."""
    client = piped()
    calls: list[None] = []

    def failing() -> None:
        calls.append(None)

        if len(calls) > 1:
            raise RuntimeError("closing failed")

    monkeypatch.setattr(client.server, "_close", failing)
    client.line(request(1, "shutdown"))

    with pytest.raises(RuntimeError):
        client.server.run()

    assert not client.link._pump.is_alive()


# ----------------------------------------------------------------------------
def test_a_late_cancel_met_in_its_turn_spares_a_request_reusing_the_id(
    piped, monkeypatch
):
    """Id 5 is answered, its cancel arrives too late for any checkpoint,
    and the client, having its answer, sends a new request as id 5. The
    cancel, met in its turn, names nothing in hand: the new request runs.
    Red if a cancel in its turn cancels a request queued behind it."""
    client = piped()

    def quick(*_args) -> str:
        client.line(cancel_of(5))

        return "first"

    monkeypatch.setitem(server_module.METHODS, "test.quick", quick)
    after_answer(
        client,
        "first",
        lambda: client.last(request(5, "test.again")),
    )
    client.line(request(5, "test.quick"))
    client.server.run()

    assert [(a["id"], a.get("result")) for a in client.answers()] == [
        (5, "first"),
        (5, "ran"),
    ]


# ----------------------------------------------------------------------------
def test_a_late_cancel_taken_by_a_checkpoint_spares_a_request_after_it(
    piped, monkeypatch
):
    """The same late cancel, taken instead by the next request's checkpoint
    with the reused id 5 already waiting behind it: a request sent after
    the cancel is never the one it meant. Red if a checkpoint cancels a
    waiting request whatever side of the cancel it was sent on."""
    client = piped()

    def quick(*_args) -> str:
        client.line(request(6, "test.again"))
        client.line(cancel_of(5))

        return "first"

    monkeypatch.setitem(server_module.METHODS, "test.quick", quick)
    after_answer(
        client,
        "first",
        lambda: client.last(request(5, "test.again")),
    )
    client.line(request(5, "test.quick"))
    client.server.run()

    assert [(a["id"], a.get("result")) for a in client.answers()] == [
        (5, "first"),
        (6, "ran"),
        (5, "ran"),
    ]


# ----------------------------------------------------------------------------
def test_a_cancel_arriving_while_a_checkpoint_takes_another_ends_the_prompt(
    piped, monkeypatch
):
    """A cancel for another id is taken at a checkpoint; the cancel for the
    request in hand lands just after the checkpoint read what had arrived.
    The password is then asked, and the cancel ends the wait: the answer
    is cancelled, not a declined password once the input ends. Red if the
    cancel is moved where the wait does not look, as the checkpoint once
    did on its way to the other id, and the wait looks only at what
    arrives after it begins."""
    client = piped()
    drain = client.link._drain
    armed: list[bool] = []

    def draining() -> None:
        drain()

        if armed:
            armed.clear()
            client.line(cancel_of(7))

    def login(server, *_args) -> object:
        client.line(cancel_of(99))
        armed.append(True)
        server._checkpoint(False)

        return server._prompt("Password: ")

    client.link._drain = draining  # type: ignore[method-assign]
    monkeypatch.setitem(server_module.METHODS, "test.login", login)
    client.line(request(7, "test.login"))

    # A client that never answers the prompt hangs up in the end, so a
    # wait the cancel did not end is answered declined rather than hung.
    hang_up = threading.Timer(2, client.close)
    hang_up.start()

    try:
        client.server.handle(client.link.receive())

    finally:
        hang_up.cancel()
        client.close()

    # The prompt may or may not have gone out before the cancel was seen;
    # either way the request ends cancelled.
    answer = client.answers()[-1]

    assert answer["id"] == 7
    assert answer["error"]["code"] == -32800
