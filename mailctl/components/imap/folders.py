"""Folder names against the delimiter a server reports, offline.

A Maildir++ layout spells a subfolder ``INBOX.Lists.GitHub`` while other
layouts spell the same thing ``Lists/GitHub``. Users should be able to
type either, so every folder name is normalized against the delimiter the
server actually reports and matched against the server's own folder list
rather than taken on trust. That detection is mandatory, not an
optimization: guessing wrong files mail into a folder nobody reads.
"""

from ... import MailctlError

__all__ = [
    "case_variant_hint",
    "case_variants",
    "normalize_folder",
    "same_folder",
    "split_path",
]


# ----------------------------------------------------------------------------
def split_path(name: str, delimiter: str) -> list[str]:
    """Split a user-supplied folder name into its components.

    Both ``/`` and the server's own delimiter are accepted as separators so
    that ``Lists/GitHub`` and ``INBOX.Lists.GitHub`` describe the same
    folder on a Maildir++ server.
    """
    separators = {"/", delimiter}
    parts = [name]

    for separator in separators:
        if not separator:
            continue

        parts = [piece for part in parts for piece in part.split(separator)]

    return [part for part in parts if part]


# ----------------------------------------------------------------------------
def same_folder(left: str, right: str) -> bool:
    """Whether two mailbox names name the same folder.

    Mailbox names are case-sensitive except ``INBOX`` itself (RFC 3501
    section 5.1), and Dovecot honours that: ``INBOX.Foo`` and ``INBOX.foo``
    are two folders. Folding case everywhere would treat a move between
    them as a no-op and silently skip it.
    """
    if left.upper() == "INBOX" and right.upper() == "INBOX":
        return True

    return left == right


# ----------------------------------------------------------------------------
def case_variants(name: str, known: list[str]) -> list[str]:
    """The known folders that differ from ``name`` only in case.

    Name matching is exact (``same_folder``), which makes these the
    folders a user most likely meant: ones the lookup does not resolve to,
    and ones a create would put a second folder beside.
    """
    return [
        folder
        for folder in known
        if folder.casefold() == name.casefold()
        and not same_folder(folder, name)
    ]


# ----------------------------------------------------------------------------
def case_variant_hint(name: str, known: list[str]) -> str:
    """A sentence naming the case variants of ``name``, or nothing.

    For an error about a folder that is missing: the likeliest reason is
    that the one meant is spelled with different case.
    """
    variants = case_variants(name, known)

    if not variants:
        return ""

    listed = ", ".join(repr(folder) for folder in variants)

    return (
        f"{listed} {'exists' if len(variants) == 1 else 'exist'}, but "
        f"folder names are case-sensitive. "
    )


# ----------------------------------------------------------------------------
def normalize_folder(
    name: str,
    delimiter: str,
    known: list[str] | None = None,
    prefix: str | None = None,
) -> str:
    """Return the server's spelling of a user-supplied folder name.

    When the folder list is available the answer is looked up rather than
    guessed. The lookup is exact except for ``INBOX`` (``same_folder``),
    so a folder whose case differs is not a match -- ``case_variants``
    finds those.

    A folder that does not exist yet goes under ``prefix``, the personal
    namespace prefix the server reports (RFC 2342): ``""`` puts it at the
    root, ``INBOX.`` under ``INBOX``. ``None`` means the server did not
    say, and only then is the layout guessed: under ``INBOX`` when the
    delimiter is ``.`` (Maildir++), a top-level sibling otherwise.
    """
    components = split_path(name, delimiter)

    if not components:
        raise MailctlError("empty folder name")

    if components[0].upper() == "INBOX":
        components = ["INBOX", *components[1:]]

    candidate = delimiter.join(components)

    for option in (candidate, f"INBOX{delimiter}{candidate}"):
        for folder in known or ():
            if same_folder(folder, option):
                return folder

    if prefix is None:
        parent = ["INBOX"] if delimiter == "." else []

    else:
        parent = split_path(prefix, delimiter)

        if parent and parent[0].upper() == "INBOX":
            parent = ["INBOX", *parent[1:]]

    if components[: len(parent)] == parent:
        return candidate

    return delimiter.join([*parent, *components])
