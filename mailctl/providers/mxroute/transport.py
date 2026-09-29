"""The ``mxroute`` transport: ManageSieve for rules, IMAP for mail.

Communication only. Each operation is one exchange with the server, or a
composite the host does natively, handed to a layer-1 session: a script is
read and stored as the server holds it, CHECKSCRIPT reports the server's
verdict, and a server's answer or refusal comes back as data or a
``MailctlError``. Nothing here parses, builds, or checks a rule -- that is
the dialect's (``dialect.py``) and the utilities'.
"""

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from email.message import Message
from functools import partial

from ... import MailctlError
from ...components.imap.client import ImapSession
from ...components.managesieve.client import SieveSession
from ...config import Config
from ...criteria import Criteria
from ..base import (
    FolderCreation,
    FolderListing,
    MailActionPlan,
    MailActionResult,
    MessageSummary,
    Progress,
    Transport,
)
from . import records
from .imap import imap_session
from .managesieve import sieve_session

__all__ = ["MxrouteTransport"]


class MxrouteTransport(Transport):
    """MXroute's two servers, as one open connection.

    Built open by :meth:`open`, or directly from two layer-1 sessions (or
    stand-ins for them), either of which may be None when that half was
    not asked for.
    """

    name = "mxroute"

    # ------------------------------------------------------------------------
    def __init__(
        self,
        sieve: SieveSession | None = None,
        imap: ImapSession | None = None,
    ):
        self.sieve = sieve
        self.imap = imap

    # ------------------------------------------------------------------------
    def _sieve(self) -> SieveSession:
        """Return the ManageSieve session, or raise if none was opened."""
        if self.sieve is None:
            raise MailctlError("no ManageSieve session is open")

        return self.sieve

    # ------------------------------------------------------------------------
    def _imap(self) -> ImapSession:
        """Return the IMAP session, or raise if none was opened."""
        if self.imap is None:
            raise MailctlError("no IMAP session is open")

        return self.imap

    # ------------------------------------------------------------------------
    @classmethod
    @contextmanager
    def open(
        cls,
        config: Config,
        *,
        rules: bool,
        mail: bool,
        progress: Progress | None = None,
    ) -> Iterator["MxrouteTransport"]:
        """Open the requested sessions and close them on the way out.

        IMAP is opened first, so a login failure there is reported before
        any ManageSieve traffic.
        """

        def channel(name: str):
            return partial(progress, name) if progress else None

        with ExitStack() as stack:
            transport = cls()

            if mail:
                transport.imap = stack.enter_context(
                    imap_session(config, progress=channel("imap"))
                )

            if rules:
                transport.sieve = stack.enter_context(
                    sieve_session(config, progress=channel("sieve"))
                )

            yield transport

    # ########################################################################
    # The rule half, over ManageSieve
    # ########################################################################

    # ------------------------------------------------------------------------
    @property
    def has_rules(self) -> bool:
        return self.sieve is not None

    # ------------------------------------------------------------------------
    def rules_capabilities(self) -> list[str]:
        return self._sieve().capabilities()

    # ------------------------------------------------------------------------
    def list_rule_sets(self) -> tuple[str | None, list[str]]:
        return self._sieve().list_scripts()

    # ------------------------------------------------------------------------
    def active_rule_set(self) -> str | None:
        return self._sieve().active_script_name()

    # ------------------------------------------------------------------------
    def read_rule_set(self, name: str) -> str:
        return self._sieve().get_script(name)

    # ------------------------------------------------------------------------
    def check_rule_set(self, source: str) -> None:
        self._sieve().check_script(source)

    # ------------------------------------------------------------------------
    def store_rule_set(self, name: str, source: str) -> None:
        self._sieve().put_script(name, source)

    # ------------------------------------------------------------------------
    def activate_rule_set(self, name: str) -> None:
        self._sieve().set_active(name)

    # ########################################################################
    # The mail half, over IMAP
    # ########################################################################

    # ------------------------------------------------------------------------
    @property
    def has_mail(self) -> bool:
        return self.imap is not None

    # ------------------------------------------------------------------------
    def mail_capabilities(self) -> list[str]:
        return self._imap().capabilities()

    # ------------------------------------------------------------------------
    def list_folders(self) -> FolderListing:
        imap = self._imap()

        return FolderListing(
            imap.delimiter, sorted(imap.folders), list(imap.subscribed_folders)
        )

    # ------------------------------------------------------------------------
    def delimiter(self) -> str:
        return self._imap().delimiter

    # ------------------------------------------------------------------------
    def normalize(self, name: str) -> str:
        return self._imap().normalize(name)

    # ------------------------------------------------------------------------
    def exists(self, folder: str) -> bool:
        return self._imap().exists(folder)

    # ------------------------------------------------------------------------
    def case_variants(self, folder: str) -> list[str]:
        return list(self._imap().case_variants(folder))

    # ------------------------------------------------------------------------
    def is_subscribed(self, folder: str) -> bool:
        return self._imap().is_subscribed(folder)

    # ------------------------------------------------------------------------
    def create_folder(self, folder: str, subscribe: bool) -> FolderCreation:
        return records.folder_creation(
            self._imap().create_folder(folder, subscribe=subscribe)
        )

    # ------------------------------------------------------------------------
    def subscribe(self, folder: str) -> None:
        self._imap().subscribe(folder)

    # ------------------------------------------------------------------------
    def unsubscribe(self, folder: str) -> None:
        self._imap().unsubscribe(folder)

    # ------------------------------------------------------------------------
    def select_mail(
        self,
        criteria: Criteria,
        source: str,
        destination: str,
        flags: list[str],
        discard: bool,
    ) -> MailActionPlan:
        return records.mail_plan(
            self._imap().plan_actions(
                criteria,
                source=source,
                destination=destination,
                flags=flags,
                discard=discard,
            )
        )

    # ------------------------------------------------------------------------
    def apply_mail(self, plan: MailActionPlan) -> MailActionResult:
        return records.mail_result(
            self._imap().execute(records.session_plan(plan))
        )

    # ------------------------------------------------------------------------
    def search_messages(self, folder: str, expression: str) -> list[int]:
        return self._imap().raw_search(folder, expression)

    # ------------------------------------------------------------------------
    def message_headers(self, folder: str, uid: int) -> Message:
        return self._imap().fetch_message_headers(folder, uid)

    # ------------------------------------------------------------------------
    def list_messages(
        self,
        folder: str,
        *,
        criteria: Criteria | None,
        expression: str | None,
        limit: int | None,
    ) -> tuple[list[MessageSummary], bool]:
        summaries, more = self._imap().list_messages(
            folder, criteria=criteria, expression=expression, limit=limit
        )

        return [records.message_summary(item) for item in summaries], more

    # ------------------------------------------------------------------------
    def message_source(
        self, folder: str, uid: int
    ) -> tuple[bytes, tuple[str, ...]]:
        return self._imap().fetch_message_source(folder, uid)
