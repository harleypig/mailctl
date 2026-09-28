"""Layer 2: how one host uses the layer-1 protocol libraries (ADR 0006).

``base`` is the interface every provider presents; ``model`` is the
neutral model it translates to and from; ``registry`` maps a provider's
name to its class; each package beside them is one host.
"""
