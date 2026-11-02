# Configure

Copy `.env.example` to `.env` and fill it in, then either export it
(`set -a; . ./.env; set +a`) or point mailctl at it with `--env-file`. Or
copy `config.toml.example` to `$XDG_CONFIG_HOME/mailctl/config.toml` — flat
top-level keys, every one explained in the file:

```toml
host = "mail.example-server.mxrouteXX.com"
user = "you@yourdomain.com"
password_cmd = "pass show email/you@yourdomain.com"
default_folder = "Lists"
```

`default_folder` is where `add` and `apply` file mail when no `--move-to`
is given; it has no flag or variable.

Resolution order is **CLI flag > env file > environment > config file >
default**. `mailctl server test` says where each setting came from — a flag,
the env file, the environment, the config file, or the default — and which of
those sources it read.

## The env file

`--env-file PATH` reads the `MAILCTL_*` lines of a dotenv-style file; given
bare, `--env-file` means `.env` in the current directory. The file is read,
not exported, and it outranks the environment because it was named for this
run. The format is the plain one: `KEY=VALUE`, an optional leading
`export`, blank lines and `#` comment lines skipped, and one pair of
matching single or double quotes stripped. Nothing is interpolated, a `#`
after a value is part of the value, and a value cannot span lines — quote a
value whose leading or trailing spaces matter. Keys not starting `MAILCTL_`
are ignored, so a `.env` shared with another tool is fine. A line mailctl
cannot read is reported by line number, never quoted. Because it takes an
optional value, put `--env-file` after any positional argument, or write
`--env-file=PATH`.

`--env-file` and `--provider` may also come before the command, as in
`mailctl --env-file .env filterset backup`, or between its group and its
action, as in `mailctl filterset --env-file .env backup`. Given more than
once, the later one wins. Before the command or its action, `--env-file`
needs its PATH: a bare `--env-file` there would take the next word of the
command as the file, so mailctl refuses it, saying the word is a command,
not a file. `config migrate` reads no settings and takes neither option.

## Folder and provider

`--folder`, on the commands that read mail (`apply`, `search`, `senders`,
`view`, `mark`, and `add --like`), resolves the same way:
`MAILCTL_SOURCE_FOLDER`, then `source_folder` in the config file, then
`INBOX`.

`--provider` names the mail host mailctl talks to, and resolves the same
way: `MAILCTL_PROVIDER`, then `provider` in the config file, then `mxroute`.
`mxroute` is the only provider today, so there is nothing to set yet. An
unknown name is refused before anything connects, and the error lists the
known ones. `mailctl server test` shows which provider a run uses and where
that came from.

## The password

The password has more sources than the other settings, so it has its own
ladder — the same shape, highest first:

| # | Source |
|---|--------|
| 1 | `--password-file`, `--password-cmd`, or `--password` (mutually exclusive) |
| 2 | `MAILCTL_PASSWORD_FILE`, then `_CMD`, then `MAILCTL_PASSWORD`, in the `--env-file` file |
| 3 | `MAILCTL_PASSWORD_FILE` |
| 4 | `MAILCTL_PASSWORD_CMD` |
| 5 | `MAILCTL_PASSWORD` |
| 6 | `password_file` in the config file |
| 7 | `password_cmd` in the config file |
| 8 | an interactive prompt |

A flag typed for this run beats a variable that merely happens to be
exported, and a literal value never beats an instruction about where to
fetch one. The config file never holds the password itself — only
`password_file` or `password_cmd`.

A password command gets nothing on its standard input; only its first line
of output is read. mailctl's own input is never passed to it: that may be
a `--filter -` document, or, under the API, the client's requests.

`--password-file` is refused unless the file's mode is `0600` or `0400`; the
error names the `chmod` that fixes it. Keep that file on the Linux
filesystem — anything under `/mnt/c` or another Windows mount (WSL) reports
mode `0777` whatever you set, so it is always refused. Exactly one trailing
newline is stripped from it, and nothing else, since a trailing space can be
part of a password. The path may start with `~` and may use `$VAR` or
`${VAR}`, wherever it is given — `password_file = "~/.config/mail/pw"`
works. An unset variable is left as written, so the error names it.

An env file that sets `MAILCTL_PASSWORD` is held to the same mode rule as a
password file: `0600` or `0400`, or it is refused with the `chmod` that fixes
it. One that only names a password file or command is not.

`--password VALUE` exists and is the **least safe** option: the value is
visible in the process list to every user on the machine and your shell
saves it to history. mailctl warns when you use it.

A command line mailctl rejects never quotes a word it could not use, so a
password typed after a misspelt option, or given to a command that does
not take one, is not shown back. The error names a long option up to its
`=`, and a short one only when it stands alone, and counts the other
words: `unrecognized arguments: --pasword and 1 other word`. A word
straight after an option, or after `--`, is counted and never named, since
it may be that option's value. An invalid value is dropped from the
message, and an error of any other shape reads `the command line was
refused`. The same holds for the `--json` line and for `mailctl-api`.

## Turning off a Sieve extension

`disabled_extensions` tells mailctl never to write a rule that needs a
given Sieve extension, even when the server advertises it. You cannot turn
on what the server lacks — this only narrows what mailctl writes. Set it as
a list in the config file (`disabled_extensions = ["mailbox"]`), as
`MAILCTL_DISABLED_EXTENSIONS=mailbox,imap4flags` (comma-separated, in the
env file or the environment), or per run with `--disable-extension NAME`,
repeated for more than one. It resolves like every other setting: the
highest source that sets it **replaces** the lower ones rather than adding
to them, so `--disable-extension copy` on one run means only `copy`, whatever
the config file says. Names are case-insensitive.

`none` means disable nothing. An empty value falls through to the next
source, so `none` is how one run clears a list set lower down:
`--disable-extension none` or `MAILCTL_DISABLED_EXTENSIONS=none` overrides
the config file's list, and `mailctl server test` names where the `none` came
from. It must stand alone — `none` beside an extension name is refused.

A disabled extension counts as not advertised:

| Disabled | What mailctl does |
|----------|-------------------|
| `mailbox` | Writes plain `fileinto`, never `fileinto :create`, and creates a new folder over IMAP instead. With `--no-mail` a folder that needs creating is refused, as it is on a server without `mailbox`. |
| `fileinto` | Refuses a rule that files mail into a folder. `--discard`, `--keep`, and flag-only rules still work. |
| `imap4flags` | Refuses a rule that sets a flag (`--mark-read`, `--flag`). |
| `body` | Refuses a rule with a `--body` test. `search --body` and `apply --body` are unaffected: they never write Sieve. |
| any other name in the table below | Nothing today — mailctl writes none of them — but it holds if a later version does. |

A refusal names the setting and where it came from. A name mailctl does not
know is an error, naming it, before anything connects — a typo would
otherwise switch off nothing without a word. Disabling an extension the
server does not advertise anyway changes nothing and is not an error.
`mailctl server test` shows one table of Sieve extensions — every name the
server lists and every name below — each `available` or `unavailable`, and an
available one `enabled` or `disabled (...)` with the source. An unavailable
one shows nothing more, disabled or not, since there is nothing to turn off. A
`*` marks the ones mailctl's own rules can need.

The names mailctl knows — `mailctl server test` always lists these, plus
whatever else the server advertises — and what each one adds to Sieve:

| Extension | Spec | Adds | mailctl writes it |
|-----------|------|------|-------------------|
| `fileinto` | RFC 5228 | `fileinto`: deliver into a named folder instead of INBOX | every rule that files mail |
| `imap4flags` | RFC 5232 | `setflag` / `addflag` / `removeflag`, the `hasflag` test, and `:flags` | flag actions (`addflag`) |
| `mailbox` | RFC 5490 | `fileinto :create`, and the `mailboxexists` test | `:create` |
| `body` | RFC 5173 | the `body` test: match the message's text rather than its headers | `--body` on `add` |
| `copy` | RFC 3894 | `:copy` on `fileinto` / `redirect`: file a copy and keep the message in INBOX too | no |
| `envelope` | RFC 5228 | the `envelope` test: match the SMTP envelope rather than the headers | no |
| `regex` | draft-ietf-sieve-regex, never an RFC | a `:regex` match type | no |
| `enotify` | RFC 5435 | the `notify` action | no — refused |
| `vacation` | RFC 5230 | the `vacation` autoresponder | no — refused |
| `spamtest` | RFC 5235 | the `spamtest` test on the server's spam score (`virustest` is a separate extension) | no |
| `extlists` | RFC 6134 | `:list` matching against external lists | no |

It covers the rules mailctl writes, not what is already in the script: a
rule you made in webmail that uses a disabled extension is left alone, and
`mailctl filterset restore` puts a backup back exactly as it was.

## Coming from mxfilter

The tool was called `mxfilter`, and the rename was a clean break: the old
config directory (`$XDG_CONFIG_HOME/mxfilter/`) and the old `MXROUTE_*`
variables are **not read**. Every command warns while the old directory
exists and the new one does not, and `mailctl config migrate` moves its
contents — `config.toml`, the backups, anything else — across, keeping modes
and overwriting nothing (`--dry-run` shows what would move). An old
`MXROUTE_*` variable still set, with no `MAILCTL_*` counterpart, is named in
a warning; rename it. [CHANGELOG.md][changelog] lists every old and new
name. The API tells its client the same in `initialize`'s `old_name` (see
[*API*][api]).

```bash
mailctl config migrate --dry-run
mailctl config migrate
```

[api]: API.md
[changelog]: ../CHANGELOG.md
