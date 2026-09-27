"""Layer 1: one library per protocol, knowing nothing about any host.

Each package here is named for a protocol and wraps an existing Python
library for it (ADR 0006). None of them imports mailctl's configuration,
engine, front-end, or any provider; ``tests/test_layer_purity.py`` holds
that line.
"""
