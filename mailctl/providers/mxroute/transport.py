"""The ``mxroute`` transport: ManageSieve for rules, IMAP for mail.

Communication only. Each half is its own connection, made when the
session asks. Each operation is one exchange with the server, or a
composite the host does natively, handed to a layer-1 session: a script is
read and stored as the server holds it, CHECKSCRIPT reports the server's
verdict, and a server's answer or refusal comes back as data or a
``MailctlError``. Nothing here parses, builds, or checks a rule -- that is
the dialect's (``dialect.py``) and the utilities'.
"""

from contextlib import ExitStack
from email.message import Message
from functools import partial

from ... import MailctlError
from ...components import imap as imap_component
from ...components import managesieve as sieve_component
from ...components.imap.client import ImapSession
from ...components.managesieve.client import SieveSession
from ...config import Config
from ...criteria import Criteria
from ..base import (
    MAIL,
    RULES,
    FetchedMessage,
    FolderListing,
    FolderStatus,
    MailActionPlan,
    MailActionResult,
    Namespace,
    Progress,
    ServerDescription,
    SortOrder,
    Transport,
)
from . import records
from .imap import imap_session
from .managesieve import sieve_session

__all__ = ["MxrouteTransport"]


class MxrouteTransport(Transport):
    """MXroute's two servers, each half connected when asked.

    Made by :meth:`open` from a configuration, connected to nothing, or
    directly from two layer-1 sessions (or stand-ins for them) already
    open, either of which may be None for a half there is none of.
    """

    name = "mxroute"

    # ------------------------------------------------------------------------
    def __init__(
        self,
        sieve: SieveSession | None = None,
        imap: ImapSession | None = None,
        *,
        config: Config | None = None,
        progress: Progress | None = None,
    ):
        self.sieve = sieve
        self.imap = imap
        self.config = config
        self.progress = progress
        self._stacks: dict[str, ExitStack] = {}

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

    # ########################################################################
    # The connection
    # ########################################################################

    # ------------------------------------------------------------------------
    @classmethod
    def open(
        cls, config: Config, *, progress: Progress | None = None
    ) -> "MxrouteTransport":
        return cls(config=config, progress=progress)

    # ------------------------------------------------------------------------
    def _channel(self, name: str):
        """The progress callback for one server, tagged with its name."""
        return partial(self.progress, name) if self.progress else None

    # ------------------------------------------------------------------------
    def connect(self, half: str) -> None:
        if self.config is None:
            return

        if half == MAIL and self.imap is None:
            stack = ExitStack()
            self.imap = stack.enter_context(
                imap_session(self.config, progress=self._channel("imap"))
            )
            self._stacks[MAIL] = stack

        elif half == RULES and self.sieve is None:
            stack = ExitStack()
            self.sieve = stack.enter_context(
                sieve_session(self.config, progress=self._channel("sieve"))
            )
            self._stacks[RULES] = stack

    # ------------------------------------------------------------------------
    def disconnect(self, half: str) -> None:
        stack = self._stacks.pop(half, None)

        if half == MAIL:
            self.imap = None

        elif half == RULES:
            self.sieve = None

        if stack is not None:
            stack.close()

    # ------------------------------------------------------------------------
    def dropped(self, error: BaseException) -> bool:
        return imap_component.connection_lost(
            error
        ) or sieve_component.connection_lost(error)

    # ########################################################################
    # The rule half, over ManageSieve
    # ########################################################################

    # ------------------------------------------------------------------------
    @property
    def has_rules(self) -> bool:
        return self.sieve is not None or self.config is not None

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

    # ------------------------------------------------------------------------
    def describe_rules_server(self) -> ServerDescription:
        return records.rules_server(self._sieve().server_capabilities())

    # ########################################################################
    # The mail half, over IMAP
    # ########################################################################

    # ------------------------------------------------------------------------
    @property
    def has_mail(self) -> bool:
        return self.imap is not None or self.config is not None

    # ------------------------------------------------------------------------
    def mail_capabilities(self) -> list[str]:
        return self._imap().capabilities()

    # ------------------------------------------------------------------------
    def list_folders(self) -> FolderListing:
        imap = self._imap()

        return FolderListing(
            imap.delimiter,
            imap.folders,
            imap.subscribed_folders,
            imap.namespace_prefix,
        )

    # ------------------------------------------------------------------------
    def create_folder(self, folder: str) -> None:
        self._imap().create_folder(folder)

    # ------------------------------------------------------------------------
    def subscribe(self, folder: str) -> None:
        self._imap().subscribe(folder)

    # ------------------------------------------------------------------------
    def unsubscribe(self, folder: str) -> None:
        self._imap().unsubscribe(folder)

    # ------------------------------------------------------------------------
    def describe_mail_server(self) -> ServerDescription:
        imap = self._imap()

        return records.mail_server(imap.identity(), imap.capabilities())

    # ------------------------------------------------------------------------
    def folder_status(self, sizes: bool) -> list[FolderStatus]:
        return [
            records.folder_status(item)
            for item in self._imap().list_status(sizes)
        ]

    # ------------------------------------------------------------------------
    def mail_namespaces(self) -> list[Namespace]:
        return records.mail_namespaces(self._imap().namespaces())

    # ------------------------------------------------------------------------
    def search(self, folder: str, criteria: Criteria) -> list[int]:
        return self._imap().search_uids(criteria, folder)

    # ------------------------------------------------------------------------
    def search_messages(self, folder: str, expression: str) -> list[int]:
        return self._imap().raw_search(folder, expression)

    # ------------------------------------------------------------------------
    def fetch_headers(
        self, uids: list[int], folder: str
    ) -> list[FetchedMessage]:
        return [
            records.fetched_message(item)
            for item in self._imap().fetch_headers(uids, folder)
        ]

    # ------------------------------------------------------------------------
    def fetch_summaries(
        self, uids: list[int], folder: str
    ) -> list[FetchedMessage]:
        return [
            records.fetched_message(item)
            for item in self._imap().fetch_summaries(uids, folder)
        ]

    # ------------------------------------------------------------------------
    def apply_mail(self, plan: MailActionPlan) -> MailActionResult:
        return records.mail_result(
            self._imap().execute(records.session_plan(plan))
        )

    # ------------------------------------------------------------------------
    def copy_messages(
        self, folder: str, uids: list[int], destination: str
    ) -> int:
        return self._imap().copy_messages(folder, uids, destination)

    # ------------------------------------------------------------------------
    def add_flags(
        self, folder: str, uids: list[int], flags: list[str]
    ) -> None:
        self._imap().add_folder_flags(folder, uids, flags)

    # ------------------------------------------------------------------------
    def remove_flags(
        self, folder: str, uids: list[int], flags: list[str]
    ) -> None:
        self._imap().remove_folder_flags(folder, uids, flags)

    # ------------------------------------------------------------------------
    def message_headers(self, folder: str, uid: int) -> Message:
        return self._imap().fetch_message_headers(folder, uid)

    # ------------------------------------------------------------------------
    def message_source(
        self, folder: str, uid: int
    ) -> tuple[bytes, tuple[str, ...]]:
        return self._imap().fetch_message_source(folder, uid)

    # ------------------------------------------------------------------------
    def sort_messages(
        self,
        folder: str,
        order: SortOrder,
        criteria: Criteria | None,
        expression: str | None,
    ) -> list[int]:
        return self._imap().sort_uids(
            folder, records.sort_criteria(order), criteria, expression
        )

    # ------------------------------------------------------------------------
    def rename_folder(self, old: str, new: str) -> None:
        self._imap().rename_folder(old, new)

    # ------------------------------------------------------------------------
    def message_count(self, folder: str) -> int:
        return self._imap().message_count(folder)
