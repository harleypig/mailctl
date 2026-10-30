"""mailctl-tui: a terminal UI that drives mailctl through its API.

It imports nothing from mailctl. It starts the ``mailctl-api`` process and
speaks JSON-RPC 2.0 to it over the process's stdin and stdout, as a client
in any language would (ADR 0008, ADR 0009).
"""
