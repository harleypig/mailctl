"""Everything ``server test`` reports, gathered as one record.

Read-only. The rule half is asked what it supports and which rule sets it
holds, the mail half for its folder shape and what its capabilities mean
for mailctl, and a saved baseline, where there is one, is compared with
both. A front-end that lays the report out as it goes can call the same
probes one by one; this is for one that wants the whole of it at once.
"""

from dataclasses import dataclass

from .. import MailctlError
from ..config import Config
from ..engine import Session
from ..providers.base import ExtensionState, Fact
from . import baseline as baselines
from .reports import (
    MailProbe,
    RulesProbe,
    connection_facts,
    probe_mail,
    probe_rules,
    report_extensions,
    wording,
)
from .server_report import unrecognised_servers

__all__ = ["ServerTest", "examine_servers"]


@dataclass(frozen=True)
class ServerTest:
    """What ``server test`` found.

    ``settings`` names the settings the report shows, in order: the host
    only where the provider reads one. ``password_state`` is a fixed word
    (``Config.password_state``), taken once the password was used.
    ``baseline`` is None where none is saved, and ``baseline_error`` is
    why one that is saved could not be compared.
    """

    config: Config
    settings: tuple[str, ...]
    password_state: str
    endpoints: list[Fact]
    rules: RulesProbe
    extensions: list[ExtensionState]
    mail: MailProbe
    folder_visibility: bool
    unrecognised: list[str]
    baseline: baselines.BaselineCheck | None
    baseline_error: MailctlError | None
    notes: tuple[str, ...]


# ----------------------------------------------------------------------------
def examine_servers(session: Session, config: Config) -> ServerTest:
    """Probe both halves, and compare with a saved baseline if there is
    one. A baseline that cannot be compared is reported, never raised:
    the test neither fails nor stops over drift."""
    rules = probe_rules(session)
    extensions = report_extensions(session, rules, config)
    mail = probe_mail(session)
    unrecognised = unrecognised_servers(session)
    check = failure = None

    try:
        if baselines.find_baseline(config) is not None:
            check = baselines.check_baseline(session, config)

    except MailctlError as exc:
        failure = exc

    settings = ("provider", "host", "user", "source_folder")

    if "host" not in session.capabilities.settings:
        settings = tuple(name for name in settings if name != "host")

    return ServerTest(
        config=config,
        settings=settings,
        password_state=config.password_state(),
        endpoints=connection_facts(config),
        rules=rules,
        extensions=extensions,
        mail=mail,
        folder_visibility=session.capabilities.folder_visibility,
        unrecognised=unrecognised,
        baseline=check,
        baseline_error=failure,
        notes=tuple(wording(config).notes),
    )
