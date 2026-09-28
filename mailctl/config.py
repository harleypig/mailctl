"""Endpoint and credential resolution.

Resolution order for every setting, highest priority first:

1. a CLI flag
2. a ``MAILCTL_*`` line in the env file named by ``--env-file``
3. an environment variable (``MAILCTL_*``)
4. the TOML config file (``$XDG_CONFIG_HOME/mailctl/config.toml``)
5. a built-in default

Where each setting came from is recorded on the Config as data
(``Config.sources``), so a front-end can say so and an error message can
avoid calling a typed value "the default".

The password is handled separately -- it has more than one source and its
own ladder (``Config.password``) -- and never lands in a plain string that
could be printed by accident -- see the ``Secret`` class below.
"""

import os
import re
import shlex
import subprocess
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import MailctlError

__all__ = [
    "Config",
    "EnvFile",
    "LegacySetting",
    "Secret",
    "Source",
    "config_dir",
    "config_path",
    "default_backup_dir",
    "legacy_config_dir",
    "legacy_settings",
    "load_config",
    "read_env_file",
]

DEFAULT_SIEVE_PORT = 4190
DEFAULT_IMAP_PORT = 993
DEFAULT_SIEVE_TLS = "starttls"
DEFAULT_SOURCE_FOLDER = "INBOX"

# The host a run talks to, by its registered name (mailctl.providers).
# Only the name lives here: the registry imports this module, so the lookup
# -- and the refusal of an unknown name -- happens there.
DEFAULT_PROVIDER = "mxroute"

SIEVE_TLS_MODES = ("starttls", "ssl", "none")

# The one prefix an env file is read for. Anything else in the file belongs
# to some other program sharing it, and is ignored rather than refused.
ENV_PREFIX = "MAILCTL_"

# Every setting the tool read under its old names, and the name it reads
# now. Only these are detected: the old prefix is a vendor's name, and any
# other MXROUTE_* variable belongs to something else -- the sibling
# Terraform provider, say -- and is none of our business. The old names
# are not read; they are recognised so their presence can be reported.
LEGACY_ENV_NAMES = {
    "MXROUTE_HOST": "MAILCTL_HOST",
    "MXROUTE_USER": "MAILCTL_USER",
    "MXROUTE_PASSWORD": "MAILCTL_PASSWORD",
    "MXROUTE_PASSWORD_FILE": "MAILCTL_PASSWORD_FILE",
    "MXROUTE_PASSWORD_CMD": "MAILCTL_PASSWORD_CMD",
    "MXROUTE_IMAP_HOST": "MAILCTL_IMAP_HOST",
    "MXROUTE_IMAP_PORT": "MAILCTL_IMAP_PORT",
    "MXROUTE_SIEVE_PORT": "MAILCTL_SIEVE_PORT",
    "MXROUTE_SIEVE_TLS": "MAILCTL_SIEVE_TLS",
    "MXROUTE_BACKUP_DIR": "MAILCTL_BACKUP_DIR",
    "MXROUTE_SOURCE_FOLDER": "MAILCTL_SOURCE_FOLDER",
}

# The directory the tool kept its config and backups in under its old
# name. Never read; only looked for, so its contents can be moved.
LEGACY_DIR_NAME = "mxfilter"

# The kinds of place a setting can come from; ``Source.kind`` is one of these.
FLAG = "flag"
ENV_FILE = "env file"
ENVIRONMENT = "environment"
CONFIG_FILE = "config file"
DEFAULT = "default"
DERIVED = "derived"
PROMPT = "prompt"

# Every way of supplying a password, in one message, because a user who
# sees this has just found out that none of them is in place.
NO_PASSWORD_MESSAGE = (
    "no password available -- pass --password-file, --password-cmd, or "
    "--password, set MAILCTL_PASSWORD_FILE, MAILCTL_PASSWORD_CMD, or "
    "MAILCTL_PASSWORD, or put password_file / password_cmd in the config "
    "file"
)

# What password_state() reports for each kind of source. A literal from a
# flag and one from the environment are the same kind of value and are
# resolved identically; they are labelled apart only so `mailctl test` can
# say which one is in play. No label says anything about the value itself.
PASSWORD_STATE_LABELS = {
    "file": "set (via file)",
    "command": "set (via command)",
    "flag": "set (via flag)",
    "env": "set",
}


# ############################################################################
# Secret handling
# ############################################################################


class Secret:
    """A password that refuses to render itself.

    Every accidental path to disclosure -- ``print``, an f-string, ``repr``
    in a traceback frame, ``%s`` in a log line -- goes through ``__str__``
    or ``__repr__``, so overriding both turns the whole class of mistakes
    into a harmless ``<redacted>``. The real value is reachable only by
    calling ``reveal()``, which is greppable and therefore reviewable.
    """

    __slots__ = ("_value",)

    # ------------------------------------------------------------------------
    def __init__(self, value: str):
        """Wrap ``value``; it is never copied anywhere else."""
        self._value = value

    # ------------------------------------------------------------------------
    def reveal(self) -> str:
        """Return the wrapped value. Call this only when handing it to a
        connection method -- never to display, log, or format it."""
        return self._value

    # ------------------------------------------------------------------------
    def __str__(self) -> str:
        return "<redacted>"

    # ------------------------------------------------------------------------
    def __repr__(self) -> str:
        return "<Secret redacted>"

    # ------------------------------------------------------------------------
    def __bool__(self) -> bool:
        return bool(self._value)


# ############################################################################
# Provenance and the env file
# ############################################################################


@dataclass(frozen=True)
class Source:
    """Where one setting's value came from.

    ``kind`` is one of the module's source constants (``FLAG``,
    ``ENV_FILE``, ...). ``name`` is the flag, variable, or config key that
    supplied it -- or, for ``DERIVED``, the setting it was copied from.
    ``path`` is the env file or config file, where there is one.
    """

    kind: str
    name: str = ""
    path: Path | None = None

    # ------------------------------------------------------------------------
    def describe(self) -> str:
        """A short phrase naming the source, for messages and reports.

        Carries no value, only where a value came from, so it is safe
        beside a credential.
        """
        if self.kind == FLAG:
            return f"flag {self.name}"

        if self.kind in (ENV_FILE, CONFIG_FILE):
            return f"{self.kind} {self.path}"

        if self.kind == DERIVED:
            return f"same as {self.name}"

        if self.kind == PROMPT:
            return "interactive prompt"

        return self.kind


@dataclass
class EnvFile:
    """The ``MAILCTL_*`` settings read from one env file.

    ``MAILCTL_PASSWORD`` is held apart, already wrapped, so that nothing
    holding this object -- a repr, a debug dump -- holds the credential as
    a plain string.

    ``legacy_names`` holds the old ``MXROUTE_*`` names the file sets --
    the names only. Their values are never kept, because one of them may
    be a password and none of them is read.
    """

    path: Path
    values: dict[str, str] = field(default_factory=dict)
    password: Secret | None = None
    legacy_names: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class LegacySetting:
    """An old setting name that is present while its new name is not.

    Carries names and a place, never a value: ``where`` is the environment
    or the env file that set ``old``.
    """

    old: str
    new: str
    where: Source


# ############################################################################
# Config
# ############################################################################


@dataclass
class Config:
    """Resolved connection settings for one MXRoute account."""

    provider: str = DEFAULT_PROVIDER
    host: str = ""
    user: str = ""
    imap_host: str = ""
    imap_port: int = DEFAULT_IMAP_PORT
    sieve_port: int = DEFAULT_SIEVE_PORT
    sieve_tls: str = DEFAULT_SIEVE_TLS
    default_folder: str = ""
    source_folder: str = DEFAULT_SOURCE_FOLDER
    backup_dir: Path = field(default_factory=lambda: default_backup_dir())

    # Rule-language extensions mailctl must not emit, lower-cased, whatever
    # the server advertises. The provider owns it: one that declares
    # 'extensions' judges which names are known, and any other refuses the
    # setting outright. This only parses the list.
    disabled_extensions: frozenset[str] = frozenset()

    # The three credential flags. argparse makes them mutually exclusive,
    # so at most one is ever populated from the command line: they are
    # three ways of saying the same explicit thing, and ranking equally
    # explicit instructions against each other would be a rule to
    # remember rather than a rule to apply.
    #
    # ``--password`` lands in ``inline_password`` rather than ``password``
    # because ``password()`` is the method that resolves the credential,
    # and a field of that name would shadow it.
    inline_password: str = field(default="", repr=False)
    password_file: str = ""
    password_cmd: str = ""

    # The same two settings as read from the TOML file, kept apart from
    # the flags because they sit at a different height in the ladder: a
    # flag beats the environment and the config file does not. Merging
    # them into one field is what inverted the order previously.
    toml_password_file: str = ""
    toml_password_cmd: str = ""

    # Supplied by the front-end (the CLI passes getpass). Left None by
    # anything that cannot prompt, which then gets a clean error instead of
    # a process blocked on a terminal read that will never be answered.
    prompter: Callable[[str], str] | None = field(default=None, repr=False)

    # Where each setting came from, keyed by field name, and the places
    # that were read at all, highest priority first. Filled by
    # load_config(); a Config built by hand has none, and anything reading
    # these must not assume a missing entry means "default".
    sources: dict[str, Source] = field(default_factory=dict)
    consulted: list[Source] = field(default_factory=list)

    # The env file named by --env-file, and the environment it outranks.
    # ``environ`` None means os.environ, read when the password is resolved.
    env_file: EnvFile | None = field(default=None, repr=False)
    environ: Mapping[str, str] | None = field(default=None, repr=False)

    # Resolved lazily by password(); never populated from a repr-able place.
    _password: Secret | None = field(default=None, repr=False)
    _password_origin: Source | None = field(default=None, repr=False)

    # ------------------------------------------------------------------------
    def __repr__(self) -> str:
        """Render without the credential.

        The dataclass-generated repr would happily print ``_password``; a
        Config lands in tracebacks and debug dumps, so the credential state
        is reported as a literal instead of a value.
        """
        return (
            f"Config(host={self.host!r}, user={self.user!r}, "
            f"imap_host={self.imap_host!r}, imap_port={self.imap_port!r}, "
            f"sieve_port={self.sieve_port!r}, sieve_tls={self.sieve_tls!r}, "
            f"default_folder={self.default_folder!r}, "
            f"source_folder={self.source_folder!r}, "
            f"password={self.password_state()})"
        )

    # ------------------------------------------------------------------------
    def password_sources(self) -> list[tuple[str, str | Secret, Source]]:
        """Return the configured password sources, highest priority first.

        Each entry is ``(kind, value, origin)``: the kind says how to turn
        the value into a credential -- read a file, run a command, or take
        it literally -- the position says which one wins, and the origin
        says where it was configured.

        The ladder, and why it is in this order:

        1. an explicit flag (``--password-file``, ``--password-cmd``,
           ``--password``; mutually exclusive, so only one can appear)
        2. ``MAILCTL_PASSWORD_FILE``, ``_CMD``, then ``MAILCTL_PASSWORD``
           from the ``--env-file`` file
        3. the same three from the environment, in the same order
        4. ``password_file`` from the config file
        5. ``password_cmd`` from the config file

        A flag outranks an ambient variable because it was typed for this
        run and the variable was not. The failure that ordering prevents
        is not an inconvenience: with ``MAILCTL_PASSWORD`` exported for one
        account, a ``--password-cmd`` naming a *second* account used to be
        ignored, and the command authenticated as the first -- the wrong
        account, with no error anywhere. The env file was named for this
        run too, so all three of its rungs sit above all three ambient
        ones for the same reason, rather than interleaving by variable.

        Within the flags the order is nominal, since argparse rejects more
        than one; it runs safest-first so a Config assembled by hand
        (a test, another front-end) still behaves sensibly.
        """
        env = os.environ if self.environ is None else self.environ
        toml = config_path()

        candidates: list[tuple[str, str | Secret, Source]] = [
            ("file", self.password_file, Source(FLAG, "--password-file")),
            ("command", self.password_cmd, Source(FLAG, "--password-cmd")),
            ("flag", self.inline_password, Source(FLAG, "--password")),
        ]

        if self.env_file is not None:
            values, path = self.env_file.values, self.env_file.path

            candidates += [
                (
                    "file",
                    values.get("MAILCTL_PASSWORD_FILE", ""),
                    Source(ENV_FILE, "MAILCTL_PASSWORD_FILE", path),
                ),
                (
                    "command",
                    values.get("MAILCTL_PASSWORD_CMD", ""),
                    Source(ENV_FILE, "MAILCTL_PASSWORD_CMD", path),
                ),
                (
                    "env",
                    self.env_file.password or "",
                    Source(ENV_FILE, "MAILCTL_PASSWORD", path),
                ),
            ]

        candidates += [
            (
                "file",
                env.get("MAILCTL_PASSWORD_FILE", ""),
                Source(ENVIRONMENT, "MAILCTL_PASSWORD_FILE"),
            ),
            (
                "command",
                env.get("MAILCTL_PASSWORD_CMD", ""),
                Source(ENVIRONMENT, "MAILCTL_PASSWORD_CMD"),
            ),
            (
                "env",
                env.get("MAILCTL_PASSWORD", ""),
                Source(ENVIRONMENT, "MAILCTL_PASSWORD"),
            ),
            (
                "file",
                self.toml_password_file,
                Source(CONFIG_FILE, "password_file", toml),
            ),
            (
                "command",
                self.toml_password_cmd,
                Source(CONFIG_FILE, "password_cmd", toml),
            ),
        ]

        return [entry for entry in candidates if entry[1]]

    # ------------------------------------------------------------------------
    def password_origin(self) -> Source | None:
        """Report where the password comes (or came) from, never what it is.

        The prompt counts as a source only when a prompter is set; with
        none, and nothing configured, there is no source and None says so.
        """
        if self._password_origin is not None:
            return self._password_origin

        sources = self.password_sources()

        if sources:
            return sources[0][2]

        if self.prompter is not None:
            return Source(PROMPT)

        return None

    # ------------------------------------------------------------------------
    def password_state(self) -> str:
        """Report whether a credential is available, never what it is.

        Every return value is a fixed literal chosen from
        ``PASSWORD_STATE_LABELS``; none is derived from the credential, so
        this is safe to print, log, and put in an error message.
        """
        if self._password is not None:
            return "set"

        sources = self.password_sources()

        if not sources:
            return "unset"

        kind, _value, _origin = sources[0]

        return PASSWORD_STATE_LABELS[kind]

    # ------------------------------------------------------------------------
    def password(self) -> Secret:
        """Resolve the password, asking the prompter only as a last resort.

        The order is ``password_sources()``; the prompt is the last rung
        below all of them. Resolution is deferred until a connection is
        actually opened so that ``--help`` and the offline code paths
        never trigger a prompt or run a credential command.

        The interactive prompt itself is *not* implemented here: a core
        module must not own a terminal interaction, or a non-terminal
        front-end could never reuse it. The caller supplies ``prompter``
        (the CLI passes ``getpass``); with none set, the absence of a
        credential is simply an error.
        """
        if self._password is not None:
            return self._password

        sources = self.password_sources()

        if sources:
            kind, value, origin = sources[0]
            self._password = self._resolve_source(kind, value)
            self._password_origin = origin

        elif self.prompter is not None:
            self._password = Secret(
                self.prompter(f"Password for {self.user or 'account'}: ")
            )
            self._password_origin = Source(PROMPT)

        else:
            raise MailctlError(NO_PASSWORD_MESSAGE)

        if not self._password:
            raise MailctlError(NO_PASSWORD_MESSAGE)

        return self._password

    # ------------------------------------------------------------------------
    def _resolve_source(self, kind: str, value: str | Secret) -> Secret:
        """Turn one ``(kind, value)`` source into a credential."""
        if isinstance(value, Secret):
            return value

        if kind == "file":
            return read_password_file(expand_path(value))

        if kind == "command":
            return run_password_command(value)

        return Secret(value)

    # ------------------------------------------------------------------------
    def legacy_settings(self) -> list[LegacySetting]:
        """Report old setting names this run would once have read."""
        env = os.environ if self.environ is None else self.environ

        return legacy_settings(env, self.env_file)

    # ------------------------------------------------------------------------
    def require(self, *names: str) -> None:
        """Fail with a single actionable message if a setting is missing."""
        missing = [name for name in names if not getattr(self, name, None)]

        if not missing:
            return

        hints = ", ".join(f"--{name.replace('_', '-')}" for name in missing)

        raise MailctlError(
            f"missing required setting(s): {', '.join(missing)}. "
            f"Set {hints}, the matching MAILCTL_* variable, or add it to "
            f"{config_path()}"
        )


# ############################################################################
# Loading
# ############################################################################


# ----------------------------------------------------------------------------
def _config_home() -> Path:
    """Return ``$XDG_CONFIG_HOME``, or ``~/.config`` where it is unset."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


# ----------------------------------------------------------------------------
def config_dir() -> Path:
    """Return mailctl's own directory, honouring ``XDG_CONFIG_HOME``."""
    return _config_home() / "mailctl"


# ----------------------------------------------------------------------------
def legacy_config_dir() -> Path:
    """Return the directory the tool used under its old name.

    Nothing is read from it -- the rename was a clean break. It is named
    here so that its presence can be reported and its contents moved.
    """
    return _config_home() / LEGACY_DIR_NAME


# ----------------------------------------------------------------------------
def config_path() -> Path:
    """Return the TOML config location, honouring ``XDG_CONFIG_HOME``."""
    return config_dir() / "config.toml"


# ----------------------------------------------------------------------------
def default_backup_dir() -> Path:
    """Return where script backups are written by default.

    This is the **config** directory, not the state directory. XDG would
    call a backup state -- it is machine-generated data the program can
    recreate, not something the user edits -- and putting it here is a
    deliberate departure from that, not something XDG endorses.

    The reason is that a backup the user cannot find is not a backup. The
    config directory is the one mailctl path a user already knows, having
    put ``config.toml`` there; ``~/.local/state`` is a path most people
    have never opened, and the moment it matters is the moment a script
    has just been mangled and nobody wants to go looking. Co-locating also
    keeps ``mailctl backup`` and the automatic pre-upload backup in one
    place instead of two.

    ``MAILCTL_BACKUP_DIR`` / ``backup_dir`` override it either way.
    """
    return config_dir() / "backups"


# ----------------------------------------------------------------------------
def read_config_file(path: Path) -> dict:
    """Parse the TOML config file, tolerating its absence."""
    if not path.is_file():
        return {}

    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)

    except tomllib.TOMLDecodeError as exc:
        raise MailctlError(f"{path}: invalid TOML -- {exc}") from exc

    except OSError as exc:
        raise MailctlError(f"{path}: cannot read -- {exc}") from exc


# Anything a shell would take as a variable name.
_ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_EXPORT = re.compile(r"export\s+")


# ----------------------------------------------------------------------------
def read_env_file(path: Path) -> EnvFile:
    """Read the ``MAILCTL_*`` settings from a dotenv-style file.

    The accepted form is the plain one people write by hand: ``KEY=VALUE``
    lines, an optional leading ``export``, blank lines and ``#`` comment
    lines skipped, and one pair of matching single or double quotes
    stripped from a value. Nothing is interpolated or unescaped, and a
    value cannot span lines; an unquoted value has its surrounding
    whitespace removed, a quoted one keeps everything inside the quotes.

    A line that is none of those is an error naming the file and the line
    number and **never** the line, because the line may be a password.

    A file that sets ``MAILCTL_PASSWORD`` is held to the password-file
    bar: any group or other permission bit and it is refused. The mode is
    taken from the open handle, so it is the mode of the file just read.
    """
    try:
        with path.open(encoding="utf-8") as handle:
            mode = os.fstat(handle.fileno()).st_mode & 0o777
            lines = handle.read().splitlines()

    except OSError as exc:
        raise MailctlError(
            f"env file {path}: cannot read -- {exc.strerror or exc}"
        ) from exc

    except UnicodeDecodeError as exc:
        raise MailctlError(
            f"env file {path}: not valid UTF-8 -- {exc.reason}"
        ) from exc

    env_file = EnvFile(path=path)

    for number, raw in enumerate(lines, start=1):
        line = raw.strip()

        if not line or line.startswith("#"):
            continue

        key, value = _parse_env_line(line, path, number)

        if key in LEGACY_ENV_NAMES:
            env_file.legacy_names.add(key)

            continue

        if not key.startswith(ENV_PREFIX):
            continue

        if key == "MAILCTL_PASSWORD":
            env_file.password = Secret(value)

        else:
            env_file.values[key] = value

    if env_file.password is not None and mode & 0o077:
        raise MailctlError(
            f"env file {path} sets MAILCTL_PASSWORD and is readable by "
            f"group/other (mode {mode:04o});\n"
            "mailctl refuses to use it.\n"
            f"Fix with: chmod 600 {path}"
        )

    return env_file


# ----------------------------------------------------------------------------
def legacy_settings(
    environ: Mapping[str, str], env_file: EnvFile | None = None
) -> list[LegacySetting]:
    """Find old ``MXROUTE_*`` settings whose new name is set nowhere.

    Only names are looked at: a key's presence is tested with ``in``, and
    no value is read, compared, or copied -- one of these is a password.
    An old name whose new name is set in either place is left out, since
    the new one is what the run reads and nothing was lost.
    """
    file_names: set[str] = set()
    file_current: set[str] = set()

    if env_file is not None:
        file_names = env_file.legacy_names
        file_current = set(env_file.values)

        if env_file.password is not None:
            file_current.add("MAILCTL_PASSWORD")

    found = []

    for old, new in LEGACY_ENV_NAMES.items():
        if new in environ or new in file_current:
            continue

        if env_file is not None and old in file_names:
            found.append(
                LegacySetting(old, new, Source(ENV_FILE, old, env_file.path))
            )

        if old in environ:
            found.append(LegacySetting(old, new, Source(ENVIRONMENT, old)))

    return found


# ----------------------------------------------------------------------------
def _parse_env_line(line: str, path: Path, number: int) -> tuple[str, str]:
    """Split one stripped, non-comment line into ``(key, value)``.

    Every error names the line by number only.
    """
    line = _EXPORT.sub("", line, count=1) if _EXPORT.match(line) else line

    key, sep, value = line.partition("=")
    key, value = key.strip(), value.strip()

    if not sep or not _ENV_KEY.fullmatch(key):
        raise MailctlError(
            f"env file {path}, line {number}: not a KEY=VALUE line "
            f"(the line is not shown, as it may hold a password)"
        )

    if value[:1] in ("'", '"'):
        if len(value) < 2 or value[-1] != value[0]:
            raise MailctlError(
                f"env file {path}, line {number}: unterminated quote "
                f"(values cannot span lines; the line is not shown, as it "
                f"may hold a password)"
            )

        value = value[1:-1]

    return key, value


# ----------------------------------------------------------------------------
def check_password_file_mode(path: Path) -> None:
    """Refuse a password file that group or other can reach.

    Any bit in ``0o077`` means somebody other than the owner can read the
    credential, so ``0600`` and ``0400`` pass and ``0640``, ``0604``,
    ``0644`` and the rest do not. The owner's own bits are not our
    business; only shared access is.

    The check runs before the file is opened, so a file with a bad mode is
    never read at all. ``libpq`` treats ``~/.pgpass`` the same way, except
    that it silently ignores the file; this names it and stops, because
    here the file was asked for by name and ignoring it would send the
    caller down the rest of the ladder without saying so.
    """
    try:
        mode = path.stat().st_mode & 0o777

    except OSError as exc:
        raise MailctlError(
            f"{path}: cannot read password file -- {exc}"
        ) from exc

    if not mode & 0o077:
        return

    raise MailctlError(
        f"password file {path} is readable by group/other "
        f"(mode {mode:04o});\n"
        "mailctl refuses to read it.\n"
        f"Fix with: chmod 600 {path}"
    )


# ----------------------------------------------------------------------------
def expand_path(value: str) -> Path:
    """Expand ``$VAR`` / ``${VAR}`` and a leading ``~`` in a path.

    Both are ordinary things to write in a config file, and neither is
    expanded by anything else there (nor by the shell in
    ``--password-file=~/pw``). An unset variable is left as written, so
    the "cannot read" error names the literal path and the cause shows.
    """
    return Path(os.path.expandvars(value)).expanduser()


# ----------------------------------------------------------------------------
def read_password_file(path: Path) -> Secret:
    """Read a password from a file, checking its mode first.

    Exactly one trailing newline is removed, the one every editor adds,
    and nothing else. Trailing spaces are left alone, because a space can
    be part of a password and eating it silently produces an
    authentication failure with no visible cause.

    A file that holds nothing but whitespace is refused rather than
    treated as a password of spaces: it is the shape an empty or
    half-written file takes, and the same unexplainable auth failure is
    the alternative.

    No error here quotes the file's contents, only its path.
    """
    check_password_file_mode(path)

    try:
        raw = path.read_text(encoding="utf-8")

    except OSError as exc:
        raise MailctlError(
            f"{path}: cannot read password file -- {exc}"
        ) from exc

    except UnicodeDecodeError as exc:
        raise MailctlError(
            f"{path}: password file is not valid UTF-8 -- {exc.reason}"
        ) from exc

    value = strip_one_newline(raw)

    if not value.strip():
        raise MailctlError(f"{path}: password file is empty")

    return Secret(value)


# ----------------------------------------------------------------------------
def strip_one_newline(text: str) -> str:
    """Remove a single trailing ``\\n`` or ``\\r\\n``, and nothing else."""
    if text.endswith("\r\n"):
        return text[:-2]

    if text.endswith("\n"):
        return text[:-1]

    return text


# ----------------------------------------------------------------------------
def run_password_command(command: str) -> Secret:
    """Run a credential command and capture its first stdout line.

    Split with ``shlex`` and run without a shell so the password can never
    be re-expanded by one. On failure only the exit status and the program
    name are reported: a credential helper's stderr is not a safe thing to
    echo, since it may quote the value it was asked for.
    """
    argv = shlex.split(command)

    if not argv:
        raise MailctlError("password command is empty")

    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, check=False
        )

    except OSError as exc:
        raise MailctlError(
            f"password command {argv[0]!r} could not be run -- {exc}"
        ) from exc

    if completed.returncode != 0:
        raise MailctlError(
            f"password command {argv[0]!r} failed with exit "
            f"{completed.returncode} (its output is not shown, as it may "
            f"contain the credential)"
        )

    value = completed.stdout.split("\n", 1)[0].strip()

    if not value:
        raise MailctlError(f"password command {argv[0]!r} produced no output")

    return Secret(value)


# ----------------------------------------------------------------------------
def _pick(*candidates, default=None):
    """Return the first candidate that is neither None nor empty."""
    for candidate in candidates:
        if candidate not in (None, ""):
            return candidate

    return default


# ----------------------------------------------------------------------------
def _as_port(value, label: str) -> int:
    """Coerce a port to int with a message naming which port failed."""
    try:
        return int(value)

    except (TypeError, ValueError) as exc:
        raise MailctlError(f"{label}: {value!r} is not a port number") from exc


# ----------------------------------------------------------------------------
def _as_extension_names(value, origin: Source) -> frozenset[str]:
    """Normalize ``disabled_extensions`` from whichever source won.

    The flag and the config file give a list, and a variable gives one
    comma-separated string -- TOML has lists, so a string there is refused
    rather than guessed at. Names are case-insensitive, as Sieve's own
    are, so they are kept lower-cased; blanks are dropped.
    """
    if isinstance(value, str) and origin.kind != CONFIG_FILE:
        value = value.split(",")

    if not isinstance(value, list | tuple) or not all(
        isinstance(item, str) for item in value
    ):
        raise MailctlError(
            f"disabled_extensions: expected a list of extension names "
            f"(from {origin.describe()})"
        )

    return frozenset(item.strip().lower() for item in value if item.strip())


# ----------------------------------------------------------------------------
def load_config(args, environ: Mapping[str, str] | None = None) -> Config:
    """Build a Config from CLI args, an env file, environment, and TOML.

    ``args`` is the parsed argparse namespace; any of the connection
    attributes may be absent or None, which simply defers to the next
    source in the resolution order. ``args.env_file``, when set, names an
    env file whose ``MAILCTL_*`` lines rank just below the flags.

    ``environ`` is the ambient environment, ``os.environ`` when None. It
    is only read, never written: the env file's values are layered over it
    here rather than exported into the process.
    """
    environ = os.environ if environ is None else environ

    env_file_arg = getattr(args, "env_file", None)
    env_file = (
        read_env_file(expand_path(env_file_arg)) if env_file_arg else None
    )

    toml = config_path()
    file_values = read_config_file(toml)

    sources: dict[str, Source] = {}

    def resolve(field_name, *, flag=None, var=None, key=None, default=None):
        """Take the first source that supplies a value, and record it."""
        candidates = []

        if flag:
            candidates.append(
                (getattr(args, flag, None), Source(FLAG, _flag_name(flag)))
            )

        if var and env_file is not None:
            candidates.append(
                (
                    env_file.values.get(var),
                    Source(ENV_FILE, var, env_file.path),
                )
            )

        if var:
            candidates.append((environ.get(var), Source(ENVIRONMENT, var)))

        if key:
            candidates.append(
                (file_values.get(key), Source(CONFIG_FILE, key, toml))
            )

        for value, origin in candidates:
            if value not in (None, ""):
                sources[field_name] = origin

                return value

        sources[field_name] = default[1] if default else Source(DEFAULT)

        return default[0] if default else None

    def setting(name, default=""):
        """The common shape: a flag, a variable, and a key of one name."""
        return resolve(
            name,
            flag=name,
            var=f"{ENV_PREFIX}{name.upper()}",
            key=name,
            default=(default, Source(DEFAULT)),
        )

    provider = setting("provider", DEFAULT_PROVIDER)
    host = setting("host")
    user = setting("user")

    imap_host = resolve(
        "imap_host",
        flag="imap_host",
        var="MAILCTL_IMAP_HOST",
        key="imap_host",
        default=(host, Source(DERIVED, "host")) if host else None,
    )

    imap_port = _as_port(setting("imap_port", DEFAULT_IMAP_PORT), "imap_port")
    sieve_port = _as_port(
        setting("sieve_port", DEFAULT_SIEVE_PORT), "sieve_port"
    )
    sieve_tls = setting("sieve_tls", DEFAULT_SIEVE_TLS)

    if sieve_tls not in SIEVE_TLS_MODES:
        raise MailctlError(
            f"sieve_tls: {sieve_tls!r} is not one of "
            f"{', '.join(SIEVE_TLS_MODES)} "
            f"(from {sources['sieve_tls'].describe()})"
        )

    backup_dir = setting("backup_dir", None)

    # A list, but it climbs the ladder like any scalar: the highest source
    # that sets it replaces the rest, so a one-run flag can narrow or widen
    # what the config file says without the user having to unset anything.
    disabled_raw = resolve(
        "disabled_extensions",
        flag="disable_extension",
        var="MAILCTL_DISABLED_EXTENSIONS",
        key="disabled_extensions",
        default=((), Source(DEFAULT)),
    )
    disabled_extensions = _as_extension_names(
        disabled_raw, sources["disabled_extensions"]
    )

    default_folder = resolve(
        "default_folder", key="default_folder", default=("", Source(DEFAULT))
    )
    source_folder = resolve(
        "source_folder",
        flag="folder",
        var="MAILCTL_SOURCE_FOLDER",
        key="source_folder",
        default=(DEFAULT_SOURCE_FOLDER, Source(DEFAULT)),
    )

    consulted = [Source(ENVIRONMENT)]

    if env_file is not None:
        consulted.insert(0, Source(ENV_FILE, path=env_file.path))

    if toml.is_file():
        consulted.append(Source(CONFIG_FILE, path=toml))

    return Config(
        provider=provider or DEFAULT_PROVIDER,
        host=host or "",
        user=user or "",
        imap_host=imap_host or "",
        imap_port=imap_port,
        sieve_port=sieve_port,
        sieve_tls=sieve_tls,
        default_folder=default_folder,
        source_folder=source_folder,
        disabled_extensions=disabled_extensions,
        # Four fields rather than two: which source a credential came from
        # is what decides the order, so collapsing a flag and a config-file
        # value into one field would throw the answer away before
        # password_sources() is ever asked the question.
        inline_password=_pick(getattr(args, "password", None), default=""),
        password_file=_pick(getattr(args, "password_file", None), default=""),
        password_cmd=_pick(getattr(args, "password_cmd", None), default=""),
        toml_password_file=_pick(file_values.get("password_file"), default=""),
        toml_password_cmd=_pick(file_values.get("password_cmd"), default=""),
        backup_dir=expand_path(backup_dir)
        if backup_dir
        else default_backup_dir(),
        sources=sources,
        consulted=consulted,
        env_file=env_file,
        environ=environ,
    )


# ----------------------------------------------------------------------------
def _flag_name(dest: str) -> str:
    """Spell an argparse ``dest`` the way the user typed it."""
    return f"--{dest.replace('_', '-')}"
