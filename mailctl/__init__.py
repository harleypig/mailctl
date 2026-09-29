"""Manage MXRoute email filters over ManageSieve and IMAP.

``mailctl`` builds a Sieve rule from command-line criteria, merges it
non-destructively into the account's active script, and optionally applies
the same criteria to mail that has already been delivered.

The package is deliberately split so the offline logic (criteria
translation, Sieve generation, folder-name normalization) can be exercised
without a server:

``config``       credential and endpoint resolution
``criteria``     the shared criteria model, to Sieve *and* to IMAP SEARCH
``components``   one library per protocol: ``managesieve`` and ``imap``
``providers``    how one host uses them: ``mxroute``
``engine``       the session: the chosen provider, opened and closed
``utilities``    the work itself, for any front-end
``cli``          argument parsing and the subcommand implementations
"""

from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from types import MappingProxyType

__all__ = ["MailctlError", "__version__"]

# pyproject.toml is the one place the version is written; this reads it back
# from the installed metadata. An editable install records the version when
# it is installed, so a bump shows here only after `uv pip install -e .`.
try:
    __version__ = version("mailctl")

except PackageNotFoundError:
    # Run from a bare source tree with nothing installed: no metadata to read.
    __version__ = "0+unknown"


# ############################################################################
# Errors
# ############################################################################


class MailctlError(Exception):
    """An expected failure that should be reported without a traceback.

    Every code path that can fail for a reason the user can act on raises
    this (or a subclass) with a message written for a human. ``cli.main``
    turns it into a one-line diagnostic and a non-zero exit; the raw
    traceback is only shown under ``--debug``.

    The message states the condition in words every front-end can show,
    so it never names one front-end's controls (a flag, a button). Where a
    front-end has advice of its own to add -- which of its flags acts on
    the condition -- ``code`` names the condition and ``fields`` carries
    the values that advice needs; each code's fields are fixed where it is
    raised. An uncoded error is shown as its message alone.
    """

    code: str | None = None
    fields: Mapping[str, object] = MappingProxyType({})

    # ------------------------------------------------------------------------
    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        fields: Mapping[str, object] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.fields = MappingProxyType(dict(fields or {}))
