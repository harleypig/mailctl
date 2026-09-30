"""Folders: listed, counted, subscribed, and planned as a rule's target.

A folder planned for creation is created on execute only, never while
planning, so a dry run or a rejected script leaves no stray folder.
"""

from dataclasses import dataclass, replace

from .. import MailctlError
from ..config import Config, Source
from ..engine import Session
from ..providers.base import (
    FolderCreation,
    FolderListing,
    FolderStatus,
    refuse,
    same_folder,
)
from .events import EventSink, FolderCreated

# Folder plan outcomes.
FOLDER_NONE = "none"
FOLDER_EXISTS = "exists"
FOLDER_MISSING = "missing"
# The values are in a --json plan's folder "status", so they are worded the
# same for every provider: mailctl creates it ("create"), the filter does
# as mail arrives ("created-on-delivery"), or both.
FOLDER_SIEVE_CREATES = "created-on-delivery"
FOLDER_IMAP_CREATE = "create"
FOLDER_BOTH_CREATE = "create-and-on-delivery"
FOLDER_UNCREATABLE = "uncreatable"

# What lets a rule make its own folder as mail arrives, in words that fit
# every provider.
DELIVERY_CREATE = "folder creation on delivery"


# ############################################################################
# Listing folders
# ############################################################################


# ----------------------------------------------------------------------------
def list_folders(session: Session) -> FolderListing:
    """Return the folder list, sorted, with the delimiter."""
    listing = session.transport.list_folders()

    return replace(listing, folders=sorted(listing.folders))


# ############################################################################
# Counting folders -- 'mailctl folder list --counts'
# ############################################################################


@dataclass(frozen=True)
class FolderCounts:
    """The folder list, with each folder's counts beside it.

    ``statuses`` holds one record per folder in ``listing.folders``, in
    the same order; a folder the host gave no counts for has None in each
    count. ``sizes`` is whether the host reported sizes at all.
    """

    listing: FolderListing
    statuses: tuple[FolderStatus, ...]
    sizes: bool


# ----------------------------------------------------------------------------
def list_folder_counts(session: Session) -> FolderCounts:
    """Every folder with its total, unread, and -- where the host reports
    it -- size, from one request to the host. Read-only.

    Refused before connecting by a provider that does not declare
    ``folder_counts``, and before counting by a server that cannot answer
    in one request: counting folder by folder would be a request per
    folder, and nothing here loops over the server.
    """
    # ``rules`` imports this module at its top, so the import waits until
    # both are loaded, whichever loads first.
    from .rules import CAPABILITY_CONSTRUCTS, require_capability

    require_capability(session, "folder_counts")

    transport = session.transport
    support = session.dialect.count_support(transport.mail_capabilities())

    if support.missing is not None:
        raise refuse(
            session.name,
            CAPABILITY_CONSTRUCTS["folder_counts"],
            f"the mail server does not advertise "
            f"{support.missing}, and without it every folder would be a "
            f"request of its own",
        )

    listing = list_folders(session)
    reported = transport.folder_status(support.sizes)

    return FolderCounts(
        listing,
        tuple(_status_of(folder, reported) for folder in listing.folders),
        support.sizes,
    )


# ----------------------------------------------------------------------------
def _status_of(folder: str, reported: list[FolderStatus]) -> FolderStatus:
    """The host's counts for ``folder``, or a record of none."""
    found = next(
        (item for item in reported if same_folder(item.folder, folder)),
        None,
    )

    return (
        FolderStatus(folder)
        if found is None
        else replace(found, folder=folder)
    )


# ############################################################################
# Folder subscription -- a setting, reported and changed like one
# ############################################################################


@dataclass(frozen=True)
class SubscriptionPlan:
    """A folder's subscription, as it is and as it was asked to be."""

    requested: str
    folder: str
    delimiter: str
    subscribe: bool
    subscribed_now: bool

    # ------------------------------------------------------------------------
    @property
    def changes(self) -> bool:
        """Whether executing the plan would change anything."""
        return self.subscribe != self.subscribed_now


# ----------------------------------------------------------------------------
def case_variant_hint(variants: list[str]) -> str:
    """A sentence naming a missing folder's case variants, or nothing.

    For an error about a folder that is missing: the likeliest reason is
    that the one meant is spelled with different case.
    """
    if not variants:
        return ""

    listed = ", ".join(repr(folder) for folder in variants)

    return (
        f"{listed} {'exists' if len(variants) == 1 else 'exist'}, but "
        f"folder names are case-sensitive. "
    )


# ----------------------------------------------------------------------------
def plan_subscription(
    session: Session, name: str, subscribe: bool
) -> SubscriptionPlan:
    """Work out a subscription change without making it.

    The name is normalized like every other folder name, so one copied out
    of the folder listing works. Subscribing needs the folder to exist;
    unsubscribing does not, since a subscription can outlive its folder and
    removing that stale entry is a legitimate thing to want.
    """
    listing = session.transport.list_folders()
    folder = session.dialect.normalize(name, listing)
    subscribed_now = listing.is_subscribed(folder)

    hint = case_variant_hint(listing.case_variants(folder))

    if subscribe and not listing.exists(folder):
        raise MailctlError(
            (
                f"no folder named {folder!r} on the server, so there is "
                f"nothing to subscribe to. {hint}"
            ).rstrip(),
            code="no_such_folder",
            fields={"operation": "folder list"},
        )

    if not subscribe and not subscribed_now and not listing.exists(folder):
        raise MailctlError(
            (
                f"no folder or subscription named {folder!r} on the "
                f"server. {hint}"
            ).rstrip(),
            code="no_such_folder",
            fields={"operation": "folder list"},
        )

    return SubscriptionPlan(
        name, folder, listing.delimiter, subscribe, subscribed_now
    )


# ----------------------------------------------------------------------------
def execute_subscription(session: Session, plan: SubscriptionPlan) -> None:
    """Apply a subscription plan; a plan that changes nothing does nothing.

    The transport confirms a subscription took effect, so a server that
    answers OK without acting on it raises here.
    """
    if not plan.changes:
        return

    if plan.subscribe:
        session.transport.subscribe(plan.folder)

    else:
        session.transport.unsubscribe(plan.folder)


# ############################################################################
# The target folder
# ############################################################################


@dataclass(frozen=True)
class FolderPlan:
    """Where filed mail goes, and how that folder comes to exist.

    ``delimiter_assumed`` is true when there was no IMAP session to read
    the delimiter from, so the provider's assumed one was used instead.

    ``case_variants`` holds existing folders that differ from ``folder``
    only in case. Folder names are case-sensitive, so none of them is the
    target -- but a missing or to-be-created folder with one beside it is
    most likely a typo, and the front-end should say so (#56).

    ``mailbox_disabled_by`` is set when the server advertises ``mailbox``
    but ``disabled_extensions`` turns it off, so ``:create`` is not used;
    it names where that setting came from.
    """

    requested: str
    folder: str
    delimiter: str
    delimiter_assumed: bool
    status: str
    subscribe: bool
    case_variants: tuple[str, ...] = ()
    mailbox_disabled_by: Source | None = None

    # ------------------------------------------------------------------------
    @property
    def use_create(self) -> bool:
        """Whether the rule should say ``fileinto :create``."""
        return self.status in (FOLDER_SIEVE_CREATES, FOLDER_BOTH_CREATE)

    # ------------------------------------------------------------------------
    @property
    def imap_creates(self) -> bool:
        """Whether the execute step makes the folder over IMAP."""
        return self.status in (FOLDER_IMAP_CREATE, FOLDER_BOTH_CREATE)


# ----------------------------------------------------------------------------
def plan_folder(
    session: Session,
    config: Config,
    requested: str | None,
    *,
    create: bool = False,
    subscribe: bool = True,
    delimiter: str | None = None,
) -> FolderPlan:
    """Normalize the target folder and decide how it gets to exist.

    With an IMAP session the folder is made over IMAP by
    :func:`create_folder` and subscribed unless declined -- Sieve's
    ``:create`` makes it only at delivery time, when nothing is running to
    subscribe to it (#40). When the server also advertises ``mailbox`` the
    rule says ``fileinto :create`` as well, so it recreates the folder if
    it is later deleted; otherwise it stays a plain ``fileinto``. With no
    IMAP session ``:create`` is the only route. Read-only: nothing is
    created here, and a folder that cannot be created is reported as such
    for :func:`check_folder` to refuse, so the front-end can show the plan
    first.

    With no ManageSieve session (the existing-mail pass alone) the Sieve
    route is simply unavailable. ``mailbox`` named in
    ``disabled_extensions`` counts as not advertised: the rule stays a
    plain ``fileinto`` and IMAP, where there is a session, makes the
    folder.
    """
    requested = requested or config.default_folder or ""
    transport = session.transport
    mail = transport.has_mail

    if not requested:
        return FolderPlan("", "", "", False, FOLDER_NONE, subscribe)

    listing = transport.list_folders() if mail else None

    if listing is None:
        folder, assumed = session.dialect.assumed_folder(requested, delimiter)

    else:
        assumed = listing.delimiter
        folder = session.dialect.normalize(requested, listing)

    shape = {
        "requested": requested,
        "folder": folder,
        "delimiter": assumed,
        "delimiter_assumed": not mail,
        "subscribe": subscribe,
    }

    if listing is not None and listing.exists(folder):
        return FolderPlan(status=FOLDER_EXISTS, **shape)

    if listing is not None:
        shape["case_variants"] = tuple(listing.case_variants(folder))

    delivery = session.dialect.delivery_create(
        config,
        transport.rules_capabilities() if transport.has_rules else None,
    )
    has_mailbox = delivery.usable

    if delivery.disabled_by is not None:
        shape["mailbox_disabled_by"] = delivery.disabled_by

    if not create:
        return FolderPlan(status=FOLDER_MISSING, **shape)

    if not mail:
        status = FOLDER_SIEVE_CREATES if has_mailbox else FOLDER_UNCREATABLE

    else:
        status = FOLDER_BOTH_CREATE if has_mailbox else FOLDER_IMAP_CREATE

    return FolderPlan(status=status, **shape)


# ----------------------------------------------------------------------------
def check_folder(session: Session, plan: FolderPlan) -> None:
    """Refuse a folder that was asked to be created and cannot be."""
    if plan.status != FOLDER_UNCREATABLE:
        return

    if plan.mailbox_disabled_by is not None:
        reason = (
            f"{DELIVERY_CREATE} is disabled by mailctl "
            f"(disabled_extensions, from "
            f"{plan.mailbox_disabled_by.describe()})"
        )

    else:
        reason = f"the server does not advertise {DELIVERY_CREATE}"

    raise MailctlError(
        f"{reason}, and without the mail connection "
        f"{plan.folder!r} cannot be created",
        code="needs_mail",
        fields={"reason": reason, "folder": plan.folder},
    )


# ----------------------------------------------------------------------------
def create_folder(session: Session, plan: FolderPlan) -> FolderCreation:
    """Create the planned folder over IMAP, subscribing unless declined.

    Subscribing is the default because a folder made to be a ``fileinto``
    target is by definition one the user is meant to see; an unsubscribed
    one receives mail that never appears in webmail.

    A failed subscription does **not** undo the creation and does not
    raise: the folder exists and mail filed there will arrive, so tearing
    it back down would trade a visibility problem for a data one. The
    outcome is returned instead, for the front-end to say out loud.
    """
    if not plan.imap_creates:
        raise MailctlError(
            f"folder {plan.folder!r} is not planned for IMAP creation "
            f"({plan.status})"
        )

    transport = session.transport
    transport.create_folder(plan.folder)

    if not plan.subscribe:
        return FolderCreation(plan.folder, subscribed=False)

    try:
        transport.subscribe(plan.folder)

    except MailctlError as exc:
        return FolderCreation(
            plan.folder, subscribed=False, subscribe_error=str(exc)
        )

    return FolderCreation(plan.folder, subscribed=True)


# ----------------------------------------------------------------------------
def folder_pending(session: Session, plan: FolderPlan) -> bool:
    """Whether a folder planned for IMAP creation has not been made yet."""
    return plan.imap_creates and not (
        session.transport.list_folders().exists(plan.folder)
    )


# ----------------------------------------------------------------------------
def realize_folder(
    session: Session, plan: FolderPlan, on_event: EventSink | None = None
) -> FolderCreation | None:
    """Create a folder planned for IMAP creation, once.

    Returns None, and does nothing, for any other plan or for a folder an
    earlier execute step already made -- ``add`` creates it before the
    upload, and its existing-mail pass must not try again.
    """
    if not folder_pending(session, plan):
        return None

    result = create_folder(session, plan)

    if on_event:
        on_event(FolderCreated(result))

    return result


# ############################################################################
# A folder created on its own -- 'mailctl folder create'
# ############################################################################


@dataclass(frozen=True)
class FolderCreationPlan:
    """A folder asked for by name, and what creating it would do.

    ``target`` is the folder as :func:`create_folder` takes it: planned
    for IMAP creation, or already there (``FOLDER_EXISTS``), in which case
    there is nothing to create. ``subscribed_now`` is its subscription as
    it stands, meaningful only when it exists.

    ``missing_parents`` are the levels above the folder that do not exist
    either, outermost first. IMAP CREATE is expected to make them too
    (RFC 3501 section 6.3.3, a SHOULD); only the folder itself is
    subscribed.
    """

    target: FolderPlan
    subscribed_now: bool = False
    missing_parents: tuple[str, ...] = ()

    # ------------------------------------------------------------------------
    @property
    def exists(self) -> bool:
        """Whether the folder is already there, so nothing is created."""
        return self.target.status == FOLDER_EXISTS


# ----------------------------------------------------------------------------
def plan_folder_creation(
    session: Session, name: str, subscribe: bool = True
) -> FolderCreationPlan:
    """Work out creating a folder by name, without creating it.

    The name is normalized like every other folder name, and a new one
    goes under the server's namespace prefix. A folder that differs from
    an existing one only in case is refused rather than planned: folder
    names are case-sensitive, so it would be a second folder beside the
    one the user most likely meant (#56).
    """
    listing = session.transport.list_folders()
    folder = session.dialect.normalize(name, listing)
    delimiter = listing.delimiter

    shape = {
        "requested": name,
        "folder": folder,
        "delimiter": delimiter,
        "delimiter_assumed": False,
        "subscribe": subscribe,
    }

    if listing.exists(folder):
        return FolderCreationPlan(
            FolderPlan(status=FOLDER_EXISTS, **shape),
            subscribed_now=listing.is_subscribed(folder),
        )

    variants = listing.case_variants(folder)

    if variants:
        raise MailctlError(
            f"not creating {folder!r}: {case_variant_hint(variants)}"
            f"Creating it would put a second folder beside "
            f"{'it' if len(variants) == 1 else 'them'}. To use the existing "
            f"folder, give its name as listed."
        )

    parts = folder.split(delimiter) if delimiter else [folder]
    parents = [delimiter.join(parts[:depth]) for depth in range(1, len(parts))]

    return FolderCreationPlan(
        FolderPlan(status=FOLDER_IMAP_CREATE, **shape),
        missing_parents=tuple(
            parent for parent in parents if not listing.exists(parent)
        ),
    )


# ----------------------------------------------------------------------------
def execute_folder_creation(
    session: Session, plan: FolderCreationPlan
) -> FolderCreation | None:
    """Create the planned folder; a folder already there is left alone.

    Returns None when there was nothing to create, otherwise what
    :func:`create_folder` achieved -- including a subscription that failed
    after the folder was made.
    """
    if plan.exists:
        return None

    return create_folder(session, plan.target)
