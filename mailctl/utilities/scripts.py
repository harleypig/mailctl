"""Rule sets as the server stores them: read, chosen, and uploaded.

The one upload path, ``upload_script``, is where the backup before every
upload is taken; every rule change and every restore goes through it. The
transport reads and stores; the backup and the order of the steps are
here.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import MailctlError
from ..config import Config
from ..engine import Session
from ..providers.base import Provider
from .backup_files import write_backup
from .events import EventSink, ScriptBackedUp, ScriptUploaded

DEFAULT_SCRIPT_NAME = "mailctl"

# The script an account got under the tool's old name. It is still ours: an
# account holding it, with nothing active, has it reused rather than a
# second script created beside it.
LEGACY_SCRIPT_NAME = "mxfilter"


# ############################################################################
# Reading scripts
# ############################################################################


@dataclass(frozen=True)
class ScriptText:
    """One script's source, exactly as the server holds it.

    ``provider``'s dialect reads it; it is not part of the value.
    """

    name: str
    source: str
    provider: Provider | Session = field(compare=False, repr=False)

    # ------------------------------------------------------------------------
    def rule_names(self) -> list[str]:
        """Parse on demand, so an unparseable script can still be shown."""
        return self.provider.dialect.rule_names(self.source)


# ----------------------------------------------------------------------------
def list_scripts(session: Session) -> tuple[str | None, list[str]]:
    """Return ``(active, others)``."""
    return session.transport.list_rule_sets()


# ----------------------------------------------------------------------------
def read_script(session: Session, name: str | None = None) -> ScriptText:
    """Return a named script, or the active one."""
    name = name or session.transport.active_rule_set()

    if not name:
        raise MailctlError("no active script; name one explicitly")

    return ScriptText(name, session.transport.read_rule_set(name), session)


# ############################################################################
# Writing a script
# ############################################################################


# ----------------------------------------------------------------------------
def fetch_active(
    session: Session, requested: str | None = None
) -> tuple[str, str, str | None]:
    """Return ``(script_name, source, active)`` for the script to edit.

    ``active`` is the name of the script the server runs now, or None.

    The name always comes from LISTSCRIPTS and is written back to. It is
    never guessed: whatever the webmail's managesieve plugin calls its
    script is a server-side config value (``managesieve_script_name``) that
    nothing about the account exposes, so a guess would create a *second*
    script and quietly leave the real one in charge. MXRoute has also said
    it intends to move off DirectAdmin, Crossbox, and Roundcube, and is
    mid-migration from Dovecot 2.3 to 2.4 -- what is discovered at runtime
    survives that, and a hardcoded name would not.

    With nothing active and nothing requested, a script mailctl wrote under
    its old name (``LEGACY_SCRIPT_NAME``) is picked up again, so an account
    set up before the rename does not grow a duplicate beside it.
    ``DEFAULT_SCRIPT_NAME`` is used only when there is neither.
    """
    transport = session.transport
    active = transport.active_rule_set()
    _active, others = transport.list_rule_sets()

    fallback = (
        LEGACY_SCRIPT_NAME
        if LEGACY_SCRIPT_NAME in others
        else DEFAULT_SCRIPT_NAME
    )
    name = requested or active or fallback

    if name == active or name in others:
        return (name, transport.read_rule_set(name), active)

    return (name, "", active)


# ----------------------------------------------------------------------------
def activates(name: str, active: str | None, requested: bool) -> bool:
    """Whether uploading ``name`` should also make it the active script.

    Only one script runs, so switching it is a change of its own and is
    never a side effect of editing another: ``--script other`` edits
    ``other`` and leaves the running script alone unless activation was
    asked for. With nothing active, activating is the only way the upload
    does anything at all.
    """
    return requested or active is None or name == active


# ----------------------------------------------------------------------------
def upload_script(
    session: Session,
    config: Config,
    name: str,
    before: str,
    after: str,
    on_event: EventSink | None = None,
    before_put: Callable[[], object] | None = None,
    *,
    activate: bool,
) -> Path:
    """Back up, validate, and upload a script; activate it if asked.

    ``activate`` has no default: whether the upload also switches which
    script the server runs is decided by the plan (``activates``), and a
    caller that forgot to pass it would otherwise switch it silently.

    The backup is written, and announced, before the server sees anything,
    so a rejected upload still leaves the user knowing where the copy is.
    ``before_put`` runs once CHECKSCRIPT has accepted the new script and
    before it is stored -- the point where a prerequisite is worth making.
    The transport only reports each step's success or failure; a failed
    step raises and nothing after it runs.
    """
    emit = on_event or (lambda event: None)
    transport = session.transport

    path = write_backup(
        before, session.dialect.backup_path(name, config.backup_dir)
    )
    emit(ScriptBackedUp(name, path))

    transport.check_rule_set(after)

    if before_put:
        before_put()

    transport.store_rule_set(name, after)

    if activate:
        transport.activate_rule_set(name)

    emit(ScriptUploaded(name, activate))

    return path
