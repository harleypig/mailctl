"""The API: mailctl's operations over JSON-RPC 2.0, for a client in any
language (ADR 0008).

An interface beside the CLI, under the same line (ADR 0007): it opens a
session, hands it to the utilities, and returns their records as the
documents ``json_output`` builds, so the API and the CLI's ``--json`` are
one contract. A client starts ``mailctl-api`` with the settings it would
give ``mailctl`` and talks to it over the process's stdin and stdout, one
message per line; stderr is diagnostics only. The process listens on
nothing, so its one peer is the process that started it.

``protocol`` is the framing and the envelope; ``server`` is the session,
the methods, and the plan table.
"""

import contextlib
import os
import sys

from .protocol import Channel
from .server import Server, build_parser

__all__ = ["main"]


# ----------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """Serve one client on stdin and stdout until it shuts down or its
    input ends."""
    args = build_parser().parse_args(argv)
    channel = Channel(sys.stdin.buffer, sys.stdout.buffer)

    # The protocol owns the real stdout. Anything else printed would be a
    # line the client cannot parse, so it goes to stderr instead.
    try:
        with contextlib.redirect_stdout(sys.stderr):
            Server(args, channel).run()

    except KeyboardInterrupt:
        return 130

    except BrokenPipeError:
        # The client stopped reading. Point stdout at nothing, so the
        # interpreter's last flush cannot fail again on the way out.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())

        return 1

    return 0
