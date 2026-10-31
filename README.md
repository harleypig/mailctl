# mailctl

Read, sort, and filter the mail on your account from the command line: build
a filter from criteria flags, merge it into the account's **active** filter
set without disturbing the filters already there, and apply the same criteria
to the mail that has already been delivered. It also finds, counts, reads,
and marks that mail, and manages the folders it is filed into. MXRoute is
the provider it speaks to today (see *MXRoute specifics*); what it shows you
is the same whichever provider it is.

## What it is for

A server-side filter only ever sees **new** mail. Write a filter in webmail and
every message already in the mailbox stays exactly where it was. mailctl
does both halves, as two commands that take the same criteria: `add` saves
the filter, and `apply` acts on the mail already there.

It exists for a second reason too: the filter and search screens in
Roundcube, the webmail MXRoute ships, are too restricted, and mailctl is
replacing them rather than working within them. mailctl's
criteria go further — any header, text in the body, whole-value and
wildcard comparisons, and, for mail already delivered, dates and read or
flagged state — and it can list, count, sort, and read the mail, so the
message a filter should catch can be found without leaving the terminal. The
filters it writes are in the form webmail uses, so they show up there, and
the filters you made in webmail are kept ([ADR 0002][adr2]).

Its scope is managing the filters and settings on one mailbox, by hand or
from a script: filters and their order, folders and their subscriptions,
message flags. It is not a mail client. It does not compose or send, and it
names an attachment but never saves or opens it. Forwarding is MXRoute's
panel's job, since MXRoute disables Sieve `redirect` (see *MXRoute
specifics*).

[docs/MAIL-READER-REQUIREMENTS.md][reader] lists what a mail reader must,
should, and may do, and where mailctl stands on each.

**Before the first run against a real mailbox, work through
[docs/VERIFYING.md][verify].** It is an ordered ladder from commands that
touch nothing to ones that move mail, and it is where the unconfirmed
assumptions in *MXRoute specifics* get settled for your account.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/mailctl --version
```

### In Docker

The `Dockerfile` at the top of the checkout builds an image you run instead
of installing. It carries all three ways to use mailctl: the command line,
the API, and the terminal UI. Nothing is published, so build it yourself:

```bash
make image                          # or: docker build -t mailctl:dev .
docker run --rm --user "$(id -u):$(id -g)" mailctl:dev --version
```

A run that talks to the server needs two things from the host: your config
directory, and your password in a file. This is the one pattern to use:

```bash
docker run --rm \
  --user "$(id -u):$(id -g)" \
  -v "$HOME/.config/mailctl:/config/mailctl" \
  -v "$HOME/.config/mailctl/password:/run/secrets/mailctl-password:ro" \
  -e MAILCTL_PASSWORD_FILE=/run/secrets/mailctl-password \
  mailctl:dev server test
```

- **The config directory is a mount, and the image refuses to run without
  it.** It holds `config.toml`, the baselines, and the backup mailctl writes
  before every change to a filter set. A backup written inside the
  container is gone when the container is, so any command other than
  `--help`, `--version`, and `help` stops with exit 2 unless
  `/config/mailctl` is a mount; a help word after `--` does not count. That
  holds for `api` and `tui` too (below), where only `--help` or `-h` given
  alone, and `--version` alone for `api`, run without it. A backup anywhere
  outside that mount is refused too, whatever set it: `--backup-dir`,
  `MAILCTL_BACKUP_DIR` in an env file or the environment, `backup_dir` in
  `config.toml`, or `filterset backup --output`. That refusal is mailctl's
  own, through `MAILCTL_BACKUP_ROOT`, which the image sets to
  `/config/mailctl` and a `docker run -e` cannot change. The path is
  resolved first, so `..` or a symlink inside the mount cannot lead out of
  it.
- **`--user "$(id -u):$(id -g)"` is what makes the password file readable.**
  The file's mode survives the mount, so a `0600` file is still `0600`
  inside and passes mailctl's mode check. What breaks without `--user` is
  ownership: the file belongs to your uid, the image's default user has a
  different one, and it cannot read the file at all. The image also refuses
  to run as root, which would let a backup land anywhere.
- **Use the password file, not a command or the prompt.** `--password-cmd`
  runs its command inside the container, where your password manager does
  not exist. The interactive prompt has no terminal under a plain
  `docker run`; `docker run -it` gives it one, which is fine by hand and
  useless from cron. `MAILCTL_PASSWORD_FILE` outranks a `password_file` or
  `password_cmd` in `config.toml`, so one `config.toml` serves both a
  checkout and the image.
- **On WSL or Docker Desktop, keep the password file on the Linux
  filesystem.** A file on a Windows drive (`/mnt/c/...`, or a Windows path
  shared into Docker Desktop) shows mode `0777` whatever you set, and
  mailctl refuses it. That refusal is deliberate and has no exception: put
  the file under your Linux home and `chmod 600` it there.

**The first word picks what runs**: `tui` runs the terminal UI, `api` runs
the API, and anything else goes to the command line, as above. Neither word
is a mailctl command. Each takes the same mounts and settings, and every
refusal above holds for each.

```bash
docker run --rm -it \
  --user "$(id -u):$(id -g)" \
  -v "$HOME/.config/mailctl:/config/mailctl" \
  -v "$HOME/.config/mailctl/password:/run/secrets/mailctl-password:ro" \
  -e MAILCTL_PASSWORD_FILE=/run/secrets/mailctl-password \
  mailctl:dev tui
```

- **The terminal UI needs both `-i` and `-t`.** Without `-t` the image
  refuses it, exit 2. With `-t` alone it opens but takes no keys, so it
  cannot even be quit; the image cannot tell, so give both. It starts the
  API inside the same container and works through it, so it reaches the
  server exactly as the command line does, and its `--api` option is
  refused. Without a password file it asks for the password in a masked
  field. `q` quits.
- **The API is for a program outside the container.** Run the same command
  with `-i` in place of `-it` and `api` in place of `tui`; the API reads
  one JSON-RPC message per line on stdin and answers on stdout until
  `shutdown` or the end of its input.

A checkout stays the way to develop and test; the image is another way to
run it.

## Configure

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

### The env file

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

### Folder and provider

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

### The password

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

### Turning off a Sieve extension

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

### Coming from mxfilter

The tool was called `mxfilter`, and the rename was a clean break: the old
config directory (`$XDG_CONFIG_HOME/mxfilter/`) and the old `MXROUTE_*`
variables are **not read**. Every command warns while the old directory
exists and the new one does not, and `mailctl config migrate` moves its
contents — `config.toml`, the backups, anything else — across, keeping modes
and overwriting nothing (`--dry-run` shows what would move). An old
`MXROUTE_*` variable still set, with no `MAILCTL_*` counterpart, is named in
a warning; rename it. [CHANGELOG.md](CHANGELOG.md) lists every old and new
name. The API tells its client the same in `initialize`'s `old_name` (see
*API*).

```bash
mailctl config migrate --dry-run
mailctl config migrate
```

## Use, by task

Commands are grouped by what they act on, noun first — `mailctl filter add`,
`mailctl mail search`:

```text
mailctl mail      search · view · mark · senders
mailctl folder    list · create · rename · subscribe · unsubscribe
mailctl filter    list · add · apply · remove · move · rename · enable · disable · optimize
mailctl filterset list · show · backup · restore
mailctl server    test · probe · baseline save|show|check
mailctl config    migrate
mailctl help [group [action]]
```

`filterset` is offered only where the provider stores several sets of
filters, and a command the provider cannot carry out is left out of its
group's help. The names from before the grouping are refused, each naming
the command it is now (see the changelog for the table).

Every group and command has `--help`, and `mailctl help filter add` prints
the same as `mailctl filter add --help` for the provider configured, without
contacting a server. Every command that changes something shows what it
would change first, and `--dry-run` stops there (see *Safety*).

A message the mail server marks as an **alert** — a mailbox nearly full,
maintenance tonight — is printed on stderr, on any command that connects to
it, with or without `--verbose`, and once per run however often the server
repeats it:

```text
mailctl: alert from the mail server: <text>
```

One sent before the connection is encrypted is ignored, since anyone on the
network could have written it.

### Check the servers

```bash
# Check both services and what they support. Changes nothing.
mailctl server test

# The same, taking settings from ./.env rather than the environment.
mailctl server test --env-file

# Everything each server says about itself, dated: identity, every
# capability, the active script, delimiter, and namespaces. --json prints
# a versioned document, sorted so two probes can be diffed.
mailctl server probe
mailctl server probe --json > probe-$(date -u +%F).json

# A server mailctl does not recognise? probe and test say so. --report
# prints an issue body to file, with your address, hosts, folder and
# script names, and password left out. It sends nothing; read it first.
mailctl server probe --report > report.md

# Any group's or command's help, the same as --help. Contacts no server.
mailctl help filter apply
```

On servers it recognises, `probe --report` prints nothing and says there is
nothing to report. The body goes to stdout; how to file it goes to stderr.

### Find the mail a filter should catch

```bash
# What does this server call its folders, and which does webmail show?
mailctl folder list

# The same, with each folder's total and unread messages (and size, where
# the server reports it), from one request.
mailctl folder list --counts

# Who sends the most mail, and how much of it is unread: the list to make
# filters from. By address, domain, or List-Id; the same criteria flags
# narrow it. Nothing is marked read. Refused above --max-messages (5000).
mailctl mail senders --since 2026-09-01
mailctl mail senders --folder Lists --by list-id --unread --top 10
mailctl mail senders --by domain --min 5 --json

# Find a message: the newest 20 in a folder, UID first. Takes the same
# criteria flags as add and apply, or a raw IMAP search.
mailctl mail search
mailctl mail search --folder Lists/News --from newsletter@example.com
mailctl mail search --raw 'UNSEEN SINCE 1-Sep-2026' --limit 50

# By body text, arrival date, and read or flagged state (see "Body, dates,
# and state" below). Dates are YYYY-MM-DD; --older-than takes 30d or 3w.
mailctl mail search --body 'build failed' --since 2026-09-01
mailctl mail search --unread --older-than 3w

# What is biggest? (see "Sorting a listing" below)
mailctl mail search --sort size --reverse --limit 10
mailctl mail search --folder Archive --sort sent --reverse --limit 20

# Find mail like one you have: criteria taken from message 4127 (its
# List-Id, else its From; --derive picks the headers). A criteria flag
# replaces what was taken for its header, and adds any other header.
mailctl mail search --like 4127
mailctl mail search --like 4127 --subject Invoice --match all

# Print the filter those criteria make, and save nothing. --json prints it
# as a filter document (see "Filter documents" below).
mailctl mail search --like 4127 --build-filter
mailctl mail search --like 4127 --build-filter --json > news.json

# Read one by UID. It stays unread, and no attachment is saved.
mailctl mail view 4127
mailctl mail view 4127 --headers-only
mailctl mail view 4127 --raw
mailctl mail view 4127 --raw > message.eml   # the exact bytes, to keep
```

`senders` ends its table with the `search --build-filter` line that makes a
filter from the top row. `--top N` (default 20, `0` for every one) and
`--min N` trim the rows, never the totals. `folders --counts` needs a server
advertising `LIST-STATUS`, and is refused, naming it, on one that does not.

#### Sorting a listing

`search --sort KEY` lists matches in another order than newest first, and
`--reverse` turns that order round. There are three keys, named so that
the two dates cannot be confused:

* `size` — the message's size in bytes, as the server counts it (the
  Size column).
* `sent` — the message's own `Date:` header, compared in UTC. A message
  with no readable `Date:` header sorts by when it was received.
* `received` — when the server took the message in (the Received
  column), which is also what `--since` and `--before` test.

Without `--reverse` the smallest or oldest come first. Messages the key
cannot tell apart keep their mailbox order either way, as IMAP's own sort
does.

**`--limit` is taken after sorting.** `--sort size --reverse --limit 10`
is the ten largest messages in the folder, not the ten newest put in size
order. Criteria still narrow the list, and do not change its order.

**Where the server can sort, it does.** A server advertising IMAP `SORT`
(MXRoute's does) is asked for the order in one command, and only the
messages shown are fetched. On a server without it, mailctl fetches every
matching message's headers and size — one command per 250 messages, never
one per message — and sorts them itself, with the same result. On a large
folder that is noticeably slower.

`--sort` cannot be combined with `--build-filter`, which prints a filter
rather than a listing, and `--reverse` needs `--sort`.

### Save a filter, then apply it to the mail already there

```bash
# See exactly what would change, without changing it.
mailctl filter add --from newsletter@example.com --move-to Lists/News --dry-run

# Save the filter: merge it, back up, upload, activate. It filters new mail
# only; the mail already delivered is left alone.
mailctl filter add --from newsletter@example.com --move-to Lists/News

# Then act on the mail already there, with the same criteria and actions.
# It previews the messages and asks before it moves any.
mailctl filter apply --from newsletter@example.com --move-to Lists/News

# Take the criteria from a message you already have, for the filter and then
# for the mail: its List-Id, else its From, as with 'search --like'. A
# criteria flag replaces what was taken for its header.
mailctl filter add --like 4127 --move-to Lists/News --dry-run
mailctl filter apply --like 4127 --move-to Lists/News

# Or from a filter document (see "Filter documents" below); '-' reads it
# from standard input.
mailctl filter add --filter news.json --move-to Lists/News
mailctl filter apply --filter news.json --move-to Lists/News
mailctl mail search --like 4127 --build-filter --json \
    | mailctl filter add --filter - --move-to Lists/News

# Mail already delivered only, with no filter. --create-folder because the
# target may not exist yet.
mailctl filter apply --subject '[SPAM]' --move-to Quarantine --create-folder \
    --mark-read
mailctl filter apply --flagged --before 2026-01-01 --move-to Archive --dry-run

# Copy rather than move: --keep files a copy and leaves each message where
# it is, as the saved filter does. A re-run skips what the folder already
# holds (same Message-ID); a message with no Message-ID cannot be looked
# for, so it is copied each time, and apply says how many.
mailctl filter apply --list-id news.example.com --move-to Lists/News --keep
```

The actions are `--move-to FOLDER`, `--discard`, `--keep`, `--mark-read`,
and `--flag NAME`; `--no-stop` lets later filters run too. A filter's name is
derived from its criteria unless `--name` gives one, and `--replace`
overwrites a filter of the same name. `--first`, `--last` (the default),
`--before RULE`, and `--after RULE` place it in the filter set.

`apply` alone takes `--max-messages` (default 500; see *Safety*) and
`--move-threshold` (default 25), above which the confirmation treats a move
as a large one. `add` asks nothing, so it takes no `--yes`.

#### Body, dates, and state

`search`, `apply`, `senders`, and `add` take four more kinds of criterion
beside the header flags:

* `--body TEXT` — the message body contains `TEXT`, case-insensitively.
  Repeatable. It is combined with the header criteria under `--match`, like
  another header: `--from ci@example.org --body failed` is either one by
  default, both with `--match all`. It is always a substring test, so
  `--body` with `--compare is` or `--compare matches` is refused.
* `--since DATE` and `--before DATE` — received on or after `DATE`, or
  before it, as `YYYY-MM-DD`. `--since 2026-09-01 --before 2026-10-01` is
  September. "Received" is the date the server gave the message when it
  arrived, not its `Date:` header.
* `--older-than AGE` — received more than `AGE` ago, as days (`30d`) or
  weeks (`3w`); `0d` is refused. It is `--before` a date counted back from
  the day the command runs.
* `--unread` and `--flagged` — only messages in that state.

The dates and states are always **ANDed** with everything else, whatever
`--match` says: `--from a --from b --unread` is unread mail from either
sender, never "from a, or unread".

**`add` refuses the dates and states.** A filter runs as each message is
delivered, and every message being delivered is new: unread, unflagged,
and zero days old. A filter testing any of those would be always true or
never true, so `add` says why and points at `search` and `apply`, which is
where they mean something. So does `add --filter` given a document that
carries one. `add` has no date `--before` at all: its `--before RULE`
places the filter in the filter set.

**`--body` on `add` needs the server's Sieve `body` extension.** A filter
with a body test is refused, naming the extension, on a server that does
not advertise it or when `disabled_extensions` turns it off; there is no
fallback for a test. `search` and `apply` never write Sieve, so they take
`--body` either way.

**The two halves read slightly different text.** The Sieve `body` test
reads the message's text parts, decoded. IMAP's search, which `search` and
`apply` use, reads whatever parts the server decodes, which may include an
attachment's text. So `apply --body` can find a word the filter would not.
mailctl takes the server's answer on the body, and on the dates and states,
rather than re-checking it: re-checking a body would mean downloading every
candidate, and IMAP answers the dates and states exactly.

#### `--compare` tests the whole header value

`--compare contains` (the default) is a substring test and behaves the way
you would expect. `--compare is` and `--compare matches` do not: both compare
against the **entire** header value, because that is what Sieve's `:is` and
`:matches` do.

The trap is that a `From` header is rarely just an address. Given:

```text
From: Announce <announce@lists.example.com>
```

the whole value is `Announce <announce@lists.example.com>`, so:

* `--compare matches --from '*@lists.example.com'` does **not** match. The
  pattern is anchored at both ends, and the value does not end at the
  address — there is still a `>` after it.
* `--compare matches --from '*@lists.example.com*'` **does** match. The
  trailing `*` absorbs the `>`.
* `--compare is --from 'announce@lists.example.com'` does **not** match
  either, for the same reason.
* `--from 'announce@lists.example.com'` — the default `contains` — matches,
  and is usually what you actually wanted.

The wildcards in a `matches` pattern are exactly `*` (any run of characters)
and `?` (any single character), with `\` escaping either. `[...]` is not a
character class, so a bracketed subject is safe.

#### Filter documents

`mailctl mail search --build-filter --json` prints a filter's criteria as a
JSON document, for a script to keep, edit, or hand on. It holds criteria
only — what to match, never what to do with it:

```json
{
  "version": 1,
  "criteria": {
    "match": "any",
    "compare": "contains",
    "terms": [
      {"header": "List-Id", "value": "news.example.com"}
    ],
    "body": ["unsubscribe"],
    "since": "2026-09-01",
    "older_than_days": 30,
    "unread": true
  }
}
```

* `version` is `1`. A document with any other version is refused, not
  guessed at.
* `match` (`any` or `all`) and `compare` (`contains`, `is`, or `matches`)
  mean what the flags of the same name mean, and default the same way when
  left out.
* `terms` is one or more header and value pairs; neither may be empty.
* `body` is one or more texts the body must contain, as `--body`.
* `since` and `before` are dates, `YYYY-MM-DD`, as `--since` and
  `--before`. `older_than_days` is a whole number of days, as
  `--older-than` (`3w` is written `21`); it stays an age, so a document
  saved today still means "older than 30 days" when it is used next month.
* `unread` and `flagged` are `true` or `false`, as the flags; `false` is the
  same as leaving the key out.
* Every key but `version` and `criteria` is optional, and only the ones
  given are written, but at least one of `terms`, `body`, the dates, or the
  states must be there.
* Any other key is refused, so a misspelt one cannot silently fall back to
  a default. It is also why a new optional key does not need a new
  version: an older mailctl refuses a document that uses it rather than
  misreading it.

`add --filter FILE` and `apply --filter FILE` read one back as their
criteria, and `--filter -` reads it from standard input — so one document
can be saved as the filter and applied to the mail already there. It takes
the place of the criteria flags, so giving both is refused, and so is
`--filter` with `--like`; the actions still come from the command line.

### Mark messages

```bash
# Mark messages read or unread, flagged or not, or with a keyword. What
# each has now and what would change is shown first, then you confirm.
mailctl mail mark 4127 4128 --read --flag
mailctl mail mark 4127 --unread --dry-run
mailctl mail mark 4127 --folder Lists/News --keyword '$Todo'
mailctl mail mark 4127 --unflag --no-keyword '$Todo' --yes

# UIDs kept for a later run can carry the folder's UIDVALIDITY, which
# 'search' shows: if the server has renumbered the folder since, the
# command is refused and nothing is used or changed. Also on view, and on
# search, add, and apply with --like.
mailctl mail mark 4127 4128 --read --uidvalidity 1727000000
```

A UID names one message only while its folder keeps the same UIDVALIDITY.
A server that renumbers a folder — as one may when a folder is deleted and
made again under the same name — gives it a new value, and a UID kept from
before may then name other mail. A bare UID works unchecked. Within one
run, `mark` and `apply` check the value again just before writing, so a
folder renumbered while the confirmation was open is refused too.

### Manage folders

```bash
# Show a folder in webmail, or hide one (it keeps its mail either way).
mailctl folder subscribe Lists/News
mailctl folder unsubscribe Lists/Noisy --dry-run

# Make a folder on its own, subscribed so webmail shows it. Its missing
# parents are named; a folder that exists is left as it is.
mailctl folder create Lists/News --dry-run
mailctl folder create Lists/Archive --no-subscribe

# Rename a folder, and the folders under it. Its subscription comes with
# it, and every filter filing into it is repointed; nothing else in the
# script changes. The account is read back afterwards to check it landed.
mailctl folder rename Github.Notificaitons Github.Notifications --dry-run
mailctl folder rename Lists/Old Lists/Archive
```

Folder names are normalized against the server's own: `Lists/GitHub` and
`INBOX.Lists.GitHub` name the same folder. `INBOX` cannot be renamed, and
the new name must not exist yet. A name that differs from an existing
folder only in case is refused by `folder create` and `folder rename`.

### Manage the filters

```bash
# The filter sets on the server, and one set's text (the active one by
# default).
mailctl filterset list
mailctl filterset show

# The filters in the order the server runs them, and any an earlier filter
# makes unreachable ('!' is decided, '?' worth checking). Changes nothing.
mailctl filter list

mailctl filter remove from-newsletter-example-com

# Reorder a filter without restating it; reports what the move would starve.
mailctl filter move from-newsletter-example-com --first --dry-run
mailctl filter move from-newsletter-example-com --after keep-boss

# Propose a better arrangement of the whole filter set: remove a filter that
# only repeats an earlier one, move a specific filter ahead of the broader
# one starving it, and merge filters doing the same thing into one filter with
# a key list. Only what the filters decide is changed; guesses are reported.
mailctl filter optimize --dry-run
mailctl filter optimize --skip merge

# Switch a filter off without deleting it, and back on. On MXRoute it is
# written the way Roundcube writes one, so webmail shows it disabled too.
mailctl filter disable from-newsletter-example-com --dry-run
mailctl filter disable from-newsletter-example-com
mailctl filter enable from-newsletter-example-com

# Give a filter a better name. Only the name changes: the filter's conditions,
# actions, position, and disabled state, and every other filter, stay as
# they were.
mailctl filter rename "Herrschners Spam" "Yarn shops" --dry-run
mailctl filter rename "Herrschners Spam" "Yarn shops"
```

`filter optimize` never moves, merges, or removes a disabled filter, and the
filter set stays a flat list of filters. `--skip` takes `redundant`, `reorder`,
or `merge`, and is repeatable.

`filter rename` edits the filter's name line in place, so the diff it shows
is the whole change. It refuses, before anything is uploaded, a filter that
is not there, a new name that is empty or already another filter's, and one
the filter set cannot hold as a name: a line break or other control
character, spaces at the end, or a name marker inside it. A filter with no
name written in the filter set (`filter list` shows it as `Unnamed rule N`)
cannot be renamed. A merged filter keeps the first filter's name, so `filter
rename` after `filter optimize` gives it one that fits.

### Back up and restore

```bash
# Save the active filter set, byte for byte, before you touch anything.
# --output names a new file: one that exists is refused, never replaced.
mailctl filterset backup
mailctl filterset backup --output ~/mailctl-before-first-run.sieve

# Put a backup back over the active filter set: diff, back up, confirm.
mailctl filterset restore ~/mailctl-before-first-run.sieve --dry-run
mailctl filterset restore ~/mailctl-before-first-run.sieve
```

Where backups go, and what `restore` does and refuses, are in *Safety*.

### Watch the servers for change

MXRoute is migrating its servers, and a server that changes underneath a
filter does not announce it: a filter just stops behaving as it did.

```bash
# Save what the servers say now as this host's baseline; later, ask what
# has changed since and what it means for your filters. Saving writes a
# local file only.
mailctl server baseline save
mailctl server baseline check
mailctl server baseline show
```

`mailctl server baseline save` records what both servers say about themselves
— the same things `mailctl server probe` prints — and `mailctl server baseline
check` compares a fresh probe with it, saying what each difference means for
this account:

* **Serious** (`!`): an extension the active script `require`s is gone; the
  folder delimiter or the personal namespace changed; an IMAP capability
  mailctl behaves differently without (`ID`, `LIST-STATUS`, `MOVE`,
  `NAMESPACE`, `SORT`, `STATUS=SIZE`, `UIDPLUS`) came or went; the active
  script is another one.
* **Informational** (`-`): an extension appeared, an identity string
  (ManageSieve `IMPLEMENTATION`, IMAP `ID`) changed — the clearest sign of a
  migration — or any other capability or endpoint changed.

The baseline **records and never decides**. Nothing refuses to run because
of drift; `server baseline check` reports it, and `mailctl server test` adds
one line saying whether there is any. Refreshing the baseline is `server
baseline save` again, which shows what changed and the file's diff and asks
first (`--yes` skips the question, `--dry-run` stops before it).

`server baseline check` exits **0** with no drift, **3** with informational
drift only, and **4** with serious drift, so a scheduled run can alert on
either; **1** is a failure (no baseline saved, one that cannot be read, no
connection). `--json` prints the report as a versioned document, and
`server baseline show`, which does not contact the server, prints the saved
file as it is with `--json`.

The file is `$XDG_CONFIG_HOME/mailctl/baselines/<host>.json`, one per
`host` setting, written mode 0600 in a directory created 0700. What
describes the server is kept once; the active script is kept per account.
It holds no credential. A file mailctl cannot read — damaged, or from
another version — is refused by name and never overwritten; move it aside
and save again.

## Safety

* `--dry-run` changes nothing, on every mutating command. It prints whatever
  that command would have changed: the diff for `filter add`,
  `filter remove`, `filter move`, `filter disable`, `filter enable`, and
  `filter rename`; the list of matching messages for `filter apply`; each
  message's flags and what would change for `mail mark`; the file that
  would have been written for `filterset backup`; the folders that would
  move, and the diff, for `folder rename`; each proposed change, and the
  diff, for `filter optimize`.
* `add` **never touches mail already delivered**; `apply` is the only
  command that does. After saving a filter, `add` says so and points at
  `apply`.
* `--like`, on `search`, `add`, and `apply`, **shows you the message
  first** — Date, From, To, Subject, and List-Id when it has one — before
  anything is derived from it, and so before a filter is written or mail is
  moved. The UID is something you read out of webmail by hand, and
  the derived criteria look equally plausible whichever message produced
  them, so the headers are the only thing that catches a mistyped digit
  before mail starts moving. `--uidvalidity` catches a UID that has gone
  stale (see *Mark messages*).
* `folder rename` **has the server check the rewritten filter set before
  it touches the folder**, then renames, fixes the subscriptions, and
  stores the filter set straight after, so a filter points at a missing folder for as short a
  time as possible. It edits only the folder names in the filters; every
  other byte of the filter set is kept. It does not report success on the
  folder list alone: it reads the account back and checks the new name is
  subscribed where the old one was, holds at least the messages the old
  one did, and that no filter still files into an old name, exiting
  non-zero naming any that failed and, where one exists, the command that
  fixes it. A step that fails part-way is not retried; the error says what
  was done and how to undo it (`mailctl folder rename NEW OLD`). If the
  filter set changed after the rename was planned, or another filter set
  was made active, it is refused and nothing is touched, even when no
  filter files into the folder.
* `search`, `view`, and `senders` **never mark mail read**. The folder
  is opened read-only, and the message is fetched in the form that leaves
  its read flag alone, so either guard alone would be enough.
* `mark` is the command that does change a message's flags, and **only
  the messages you name**. It reads their flags first — without marking
  anything read — and shows, per message, what it has now and what would
  change; `--dry-run` stops there. A UID the folder does not hold stops
  the whole command, naming it, and nothing is marked, rather than marking
  the rest and reporting success. A message that already looks as asked
  is left alone, and when none would change it says so and exits 0.
  `--read` and `--unread`, or `--flag` and `--unflag`, together are
  refused, as is one keyword both set and cleared. A keyword is one word
  of plain ASCII (`$Todo`, `Work-1`); a space, a bracket, or a leading `\`
  is refused, since `\Seen` and `\Flagged` are set by name and no other
  system flag is.
* **Mail content is treated as hostile on the way to your terminal.** A sender
  controls every header, the body, and the attachment names, and escape
  sequences in them can recolour your terminal, retitle it, or plant a link
  whose text lies about where it goes. So `search`, `senders`, and `view` —
  `--raw` on a terminal included — print every control character as a visible
  `\xNN` escape instead of sending it to the terminal, and headers are kept to
  one line so a decoded line break cannot forge another header. Unicode
  direction overrides and isolates, which can make `invoice_fdp.exe` read as
  `invoice_exe.pdf`, print as a visible `‮`-style escape the same way.
  Everything printable, tabs and line breaks included, comes through as it is.
  The headers `--like` and `apply` show before they act, and a server's
  alert text, get the same treatment.
* `view --raw` **into a file or a pipe writes the message exactly as the
  server holds it**, byte for byte, with nothing escaped or re-encoded — so
  `mailctl mail view 4127 --raw > message.eml` saves a copy any mail program
  can open. Only on a terminal is it escaped as above.
* `view` shows the message's plain-text part. A message with only HTML is
  shown as a rough text conversion, and says so above the body; `--raw`
  shows the original. Attachments are listed by name, type, and size, and
  never written anywhere.
* The diff is shown with **both sides in mailctl's own formatting**.
  A merge re-renders the whole filter set, so a diff against the server's
  raw copy would report every re-indented line as a change — on a
  hand-written filter set that is most of the file, and it reads exactly like something
  having gone wrong. The reformat is real, so mailctl says so on a line
  above the diff, and only while the server's copy is still in some other
  formatting. What gets uploaded and what gets backed up are unaffected.
* The current active filter set is backed up to a timestamped file before any
  upload, and the path is printed. `mailctl filterset backup` takes the same
  copy on demand, without changing anything on the server.
* Backups land in `$XDG_CONFIG_HOME/mailctl/backups` (usually
  `~/.config/mailctl/backups`) — beside your `config.toml`, one file per
  backup, named after the filter set and the UTC time to the millisecond
  (on MXRoute, `<name>-<UTC timestamp>.sieve`). A backup never replaces a
  file: where the name mailctl chose is taken, it writes
  `<name>-<UTC timestamp>-2.sieve`, then `-3`, and so on, and
  `filterset backup --output FILE` naming a file that exists is refused,
  `--dry-run` included, leaving the file as it was. XDG would call a backup
  *state* rather than config; keeping it here is a deliberate departure from
  that, not something XDG endorses, because a backup you cannot find is not a
  backup. `--backup-dir`, `MAILCTL_BACKUP_DIR`, and `backup_dir` in
  `config.toml` move it, with `~` and `$VAR` expanded in each. Where
  `MAILCTL_BACKUP_ROOT` is set in the environment, mailctl refuses any
  backup that would land outside that directory; the Docker image sets it.
  It is read from the environment only, never from a flag, an env file, or
  `config.toml`, so none of those can lift it. The file is
  written mode `0600` in a directory created `0700`: a filter set is not a
  password, but it does say who you correspond with and how you sort it.
* **`mailctl filterset restore FILE` puts a backup back.** The backup is the
  server's exact bytes — no banner lines, nothing reformatted — and restore
  uploads them exactly, over the active filter set — or over the one
  `--filterset NAME` names, which stays inactive unless `--activate` is given.
  No other stored filter set is touched. It shows the raw diff against what
  the server has now, backs the current one up first, lets the server check
  the file,
  and asks before it replaces anything. It is the one command that
  **replaces** rather than merges: a filter added since the backup was taken is
  removed, and the diff shows it. It works even over a filter set mailctl
  cannot
  parse ([ADR 0005][adr5]). An empty FILE would remove every filter, so it is
  refused unless `--allow-empty` is given. FILE is read and checked before
  mailctl connects, and `~` and `$VAR` in it are expanded. A FILE this run
  reads the password from, a password file or the env file, is refused
  unopened, since its contents would come back as the diff. If the account
  has no active filter set, restore refuses rather than guess, and
  `--filterset NAME` is the way back: NAME is restored and made active.
* Filters are merged into the parsed existing filter set, never appended
  blindly, so other filters survive. If the existing filter set cannot be
  parsed, mailctl stops rather than overwrite it.
* The server checks every filter set before it is stored (on MXRoute,
  ManageSieve's `CHECKSCRIPT` before `PUTSCRIPT`).
* `apply` **always previews and always confirms** before it
  touches anything — `--dry-run` shortens that path, it is not what creates
  it. `--yes` skips the prompts. Deletion says in as many words that it
  cannot be undone; a move says it can be reversed; a copy says the
  originals stay where they are.
* `apply --move-to FOLDER --keep` **copies rather than moves**, as the
  saved filter does: a filter that files and keeps leaves the message in place
  too. Any `--mark-read` or `--flag` is set on the originals before they
  are copied, so the copies carry it as well. **A re-run copies only what
  the folder lacks**: a match whose Message-ID the folder already holds is
  skipped, and one with no Message-ID, which cannot be looked for, is
  copied every time; the preview says how many of each. When the folder
  holds every match and there is no flag to set, `apply` says so and
  changes nothing.
* `apply --discard --keep` **deletes nothing**. An explicit keep outlives a
  discard in Sieve (RFC 5228, section 4.4), and `apply` does what the filter
  would: with no flag to set it skips the pass, and with one it only sets
  the flag.
* `--max-messages` (default 500) refuses the whole batch when more matches
  than that come back. It never processes a partial set: silent truncation
  reads as "it handled everything" when it did not. Raising it is safe:
  mailctl talks to the server in batches of 250 regardless, so a large
  pass never becomes one oversized IMAP command. If a batch fails part-way,
  mailctl says how many messages were fully handled. Re-running the same
  command picks up the rest — except on a server without `MOVE`, where the
  failed batch may already have been copied and a re-run copies it again;
  mailctl says so, and names the folder to check. `senders` has its own
  `--max-messages` (default 5000), and refuses before reading a header.
* A `--move-to` target that does not exist is a **warning, not an error**,
  unless you pass `--create-folder`. `add` will still write the filter, and
  mail filed there by the server later may be lost. `apply` refuses outright,
  since it would have nowhere to put the messages.
* `--create-folder` also **subscribes** to the folder it creates. Existing
  and visible are different questions on IMAP: webmail draws its folder tree
  from the subscription list (`LSUB`), not from the folder list (`LIST`), so
  a folder that is created but never subscribed to receives mail and never
  appears. `--no-subscribe` skips the subscription on purpose — somewhere to
  file a high-volume list that should leave the inbox without cluttering the
  sidebar — and mailctl says so on the line where it creates the folder,
  because an invisible folder nobody was told about is the bug, not the
  feature. It is refused without `--create-folder`, where it would do
  nothing; `mailctl folder unsubscribe` hides a folder that already exists. If
  the subscription fails, the folder is **not** torn back down: it exists
  and mail filed there will arrive, so mailctl warns and tells you to run
  `mailctl folder subscribe` on it.
* On a server that advertises the Sieve `mailbox` extension, the filter says
  `fileinto :create` **as well**, so Sieve recreates the folder if it is
  later deleted. mailctl still creates and subscribes the folder over IMAP
  itself, because Sieve only creates it when the first message arrives,
  when mailctl is not running to subscribe to it. With `--no-mail`, Sieve
  is the only thing that can create the folder, and mailctl says it may
  not appear in webmail until you run `mailctl folder subscribe` on it.
* The folder is **announced when the change is shown and created only when
  it is applied** — for `add`, once the server has accepted the new filter
  set and just before it is stored; for `apply`, after you confirm the
  move. A dry run, an abort, or a rejected filter set leaves no stray
  folder, and an
  `apply` that matches nothing creates nothing and says so.

## Output for scripts

`--json` prints a command's result as one JSON document on stdout, and nothing
else there: whatever the command says on the way — progress, a server's alert,
the message `--like` read — goes to stderr. It is offered where the output is
data — `mail search`, `mail view`, `mail senders`, `folder list`, `filter
list`, `filterset list`, `server baseline check`, `server baseline show` — and
on every write command's plan, where it needs `--dry-run`, since a document
on stdout leaves no room for a prompt. `filterset backup`, `server baseline
save`, and `config migrate`, which write only local files, have none.
`server test` is a report for a person and has none; its contract is its
exit status. `server probe --json` prints its own versioned document (see
*Check the servers*), with the same stdout and error handling. `mail search
--uids-only` prints the matching UIDs, one per line, and nothing else.

| Command | Document |
|---------|----------|
| `filterset list` | `{"version", "filtersets": [{"name", "active"}]}` |
| `folder list` | `{"version", "delimiter", "prefix", "folders": [{"name", "subscribed"}]}`; with `--counts`, each folder also has `"messages", "unseen", "size"` (`null` where the server gave none) |
| `mail search` | `{"version", "folder", "uidvalidity", "more", "sort", "messages": [{"uid", "received", "size", "flags", "has_attachments", "from", "subject", "folder"}]}` |
| `mail senders` | `{"version", "folder", "by", "messages", "unread", "groups", "senders": [{"key", "name", "total", "unread", "unread_percent"}]}` — busiest first; `messages`, `unread`, and `groups` count everything matched, rows `--top` and `--min` left out included; `key` is `null` for mail with no address or no List-Id |
| `mail view` | `{"version", "message": {"uid", "folder", "uidvalidity", "size", "flags", "headers": [{"name", "value"}], "body", "body_from_html", "attachments": [{"name", "content_type", "size"}]}}` |
| `filter list` | `{"version", "filterset", "filters": [{"position", "name", "disabled", "stops", "combinator", "tests", "actions", "steps": [{"kind", "argument", "creates"}], "unmodelled"}], "findings": [{"certainty", "broad", "narrow", "reason"}]}` |
| a write, `--dry-run` | `{"version", "plan": {"command", "changes", ...}}` — `command` is the command's path, such as `"filter add"`; what else a plan holds depends on the command |
| `server baseline check` | `{"version", "host", "baseline", "baseline_taken", "taken", "account_recorded", "requires_known", "serious", "informational", "drift": [{"severity", "kind", "half", "name", "before", "after"}]}` |
| `server baseline show` | the saved file as stored (see *Watch the servers for change*) |
| any, failing | `{"version", "error": {"message", "code"}}`, one line, the last on stderr; `code` names the refusal where it has one, and is absent otherwise |

* `version` is `2`. A key may be added without changing it; one renamed,
  removed, or given a new meaning changes it.
* Values are whole: nothing is clipped, flags are IMAP's own (`\Seen`),
  and text outside printable ASCII is escaped as `\uXXXX`.
* `received` is ISO 8601 in local time, with no offset, because the
  server's date reaches mailctl without one.
* A plan's `changes` is `false` where the command would do nothing; its
  `diff`, on a filter set change, is then `null`. A `folder rename` plan
  always changes something; its `diff` is `null` when no filter files into
  the folder, and the filter set is then left alone.
* A plan that uploads a filter set names it as `filterset`, beside `active`
  and `activate`, and a plan for one filter names it as `filter`. A
  `filterset restore` plan counts `filters_before` and `filters_after`; a
  `folder rename` plan lists the filters it repoints as `filters`
  (`{"filter", "old", "new"}`). A plan's folder `status` is `none`, `exists`, `missing`,
  `create` (mailctl creates it), `created-on-delivery` (the filter creates
  it as mail arrives), `create-and-on-delivery` (both), or
  `uncreatable`. `diff.label` names the language the diff's text is in
  (`sieve` on MXRoute).
* A `filter optimize` plan lists `removals` (`{"filter", "covered_by"}`),
  `reorders` (`{"filter", "before"}`), `merges` (`{"into", "absorbed",
  "header", "match_type", "keys"}`), and `uncertain` (`{"kind", "filters",
  "reason"}`), what was left alone for want of certainty; `considered`
  names the kinds of change looked for.
* An `apply` plan's `mail` is `null` where the actions leave delivered mail
  as it is. Otherwise it holds `source`, `uidvalidity`, `destination`,
  `flags`, `discard`, `moves`, `copies`, `count`, `held`, `unidentified`,
  and `messages` (each as in `search`). `copies` is `true` for `--keep` with a
  folder; `held` is the UIDs the folder already has by Message-ID, which
  are not copied again, and `unidentified` the UIDs with no Message-ID,
  which are copied regardless.
* A `search` listing's `sort` is `null` for newest first, else
  `{"key", "reverse"}` as `--sort` and `--reverse` gave them, and
  `messages` are in that order. `more` is `true` when there may be
  matches past `--limit`.
* `uidvalidity` is what the UIDs are valid under in that folder, `null`
  where the server reports none; `search`, `view`, and the `mark` and
  `apply` plans carry it. Pass it back as `--uidvalidity` to have a UID
  checked before it is used.
* `search --build-filter --json` prints a filter document instead (see
  *Filter documents*).
* A `filter list` filter's `actions` are the host's own names for what it
  does (`fileinto`, `setflag` on MXRoute); its `steps` say the same in
  words that are the same for every provider, one step per action, in
  order. `kind` is `file`, `discard`, `flag`, `unflag`, `keep`, `stop`, or
  `other`, an action with no such word, whose `argument` is then its name
  as the filter set writes it. `creates` is whether a `file` step also
  creates its folder. In a listing `argument` is otherwise `null` and
  `creates` is `false`, since a filter is read back by its action names
  alone: the same limit as the text listing's *files the message into a
  folder*.

**An error's `code`** is a short name for the condition, so a script can
tell one refusal from another without reading the message: for example
`no_criteria`, `no_action`, `max_messages`, `senders_ceiling`,
`no_such_folder`, `folder_missing`, `state_in_rule`, `missing_settings`,
`credential_file`, `uidvalidity_changed`, `no_such_message`, and
`backup_exists`. Every refusal from the work a command does carries one;
some of the command line's own checks on its arguments have none yet.

**Exit status**, on every command:

| Status | Means |
|--------|-------|
| 0 | done, including "nothing to change" |
| 1 | a failure or a refusal, with the reason on stderr (one JSON line under `--json`) |
| 2 | the argument parser rejected the command line — an unknown command or flag, a value it cannot read, `--no-subscribe` without `--create-folder`; the message is plain text, even under `--json` |
| 3, 4 | `server baseline check` only: informational, or serious, drift |
| 130 | interrupted |

**From v1.0.0 these are a promise.** The documents, the error codes, and
the exit statuses above, with the API below, are kept for every 1.x
release; what is kept, and what may still change, is in
[RELEASING.md › *What v1 promises*][promise].

## API

`mailctl-api` offers mailctl's operations to a program written in any
language. The program starts it as a child process and talks JSON-RPC 2.0
to it over its standard input and output. It is meant for a client such as
a TUI: the client shows things and asks the user, and mailctl does the work
through the same code, and the same safety checks, as the command line. Why
it is built this way is [ADR 0008][adr8].

### Starting it

```bash
mailctl-api --host mail.example.com --user you@example.com
python -m mailctl.api --env-file      # the same program
```

It takes the settings `mailctl` takes, resolved the same way (see
*Configure*): `--provider`, `--env-file`, `--host`, `--user`,
`--password-file` or `--password-cmd`, `--imap-host`, `--imap-port`,
`--sieve-port`, `--sieve-tls`, `--backup-dir`, and `--disable-extension`.
`--password` is left out, since an argument can be read from the process
list. Two settings are its own:

* `--folder` is the folder the mail methods read when a request names
  none. It defaults to `source_folder` from the config, else `INBOX`.
* `--debug` writes a failed request's traceback to stderr. It names the
  request by its id and method, never by its parameters.

`mailctl-api --help` lists them all. The Docker image starts it with `api`
as its first word (see *In Docker*).

### The stream

* Each message is one JSON object on one line, ended by a newline, in both
  directions. mailctl's lines are ASCII; a client's must be UTF-8.
* stdout carries the protocol and nothing else. stderr is diagnostics, for
  the client to show or log.
* Requests are answered one at a time, in the order they came. A batch (a
  JSON array) is refused. A request can be cancelled while it runs or
  waits its turn (see *Cancelling a request*).
* Send `initialize` first. It reads the settings and connects nothing: each
  server is connected the first time a method needs it, so a bad setting is
  reported before any login.
* The process exits 0 once it has answered `shutdown`, or when its input
  ends.

### Methods

Every command of the command line is a method, named by its path with
dots: `filter list` is `filter.list`. A command that changes something is
a pair, `.plan` and `.execute` (see *Plan, decide, execute* below). The one
command left out is `help`, which describes the command line's own
options; a client learns what it can call from `initialize`'s
`capabilities` and from this list. `initialize`, `shutdown`,
`plan.discard`, and `$/cancelRequest` are the protocol's own.

Parameters are passed by name, and one the method does not know is
refused. A `?` marks one that may be left out or sent as `null`. A method
the provider does not offer, by its `capabilities`, is refused before
anything connects, as the command line refuses the option it hides.

| Method | Parameters | Result |
|--------|------------|--------|
| `initialize` | `log?` | `{"protocol", "json_version", "mailctl", "provider", "capabilities", "old_name"}` |
| `shutdown` | | `null` |
| `plan.discard` | `plan_id` | `null` |
| `$/cancelRequest` | `id` | none: a notification (see *Cancelling a request*) |
| `filter.list` | `filterset?` | the `filter list --json` document |
| `filterset.list` | | the `filterset list --json` document |
| `filterset.show` | `name?` | `{"version", "filterset": {"name", "source", "filters"}}` |
| `filterset.backup` | `output?`, `dry_run?` | `{"version", "result": {"command", "filterset", "path", "filters", "written"}}` |
| `folder.list` | `counts?` | the `folder list --json` document, with `counts` as `--counts` gives it |
| `mail.search` | `folder?`, `filter?`, `raw?`, `limit?`, `sort?`, `like?`, `build_filter?` | the `mail search --json` document, or with `build_filter` a filter document |
| `mail.view` | `uid`, `folder?`, `uidvalidity?` | the `mail view --json` document |
| `mail.senders` | `folder?`, `filter?`, `by?`, `top?`, `minimum?`, `max_messages?` | the `mail senders --json` document |
| `server.test` | | `{"version", "test": {…}}` (below) |
| `server.probe` | `report?` | the `server probe --json` document, or with `report` `{"version", "report"}` |
| `server.baseline.show` | | the saved baseline, as `server baseline show --json` prints it |
| `server.baseline.check` | | the `server baseline check --json` document |

The pairs. Every `.plan` returns `{"plan_id", "document"}`, and every
`.execute` takes `plan_id` and nothing else.

| Command | `.plan` takes | `.execute` returns, inside `{"version", "result": …}` |
|---------|---------------|--------------------------------------------------------|
| `filter.add` | `filter`, `actions`, `name?`, `filterset?`, `replace?`, `placement?`, `activate?`, `create_folder?`, `subscribe?` | a filter set change |
| `filter.apply` | `filter`, `actions`, `folder?`, `max_messages?`, `create_folder?`, `subscribe?` | `{"command", "source", "destination", "flagged", "moved", "deleted", "copied"}` |
| `filter.remove` | `name`, `filterset?`, `activate?` | a filter set change |
| `filter.move` | `name`, `placement`, `filterset?`, `activate?` | a filter set change |
| `filter.rename` | `name`, `new_name`, `filterset?`, `activate?` | a filter set change; `filter` is the new name |
| `filter.enable`, `filter.disable` | `name`, `filterset?`, `activate?` | a filter set change |
| `filter.optimize` | `filterset?`, `activate?`, `skip?` | a filter set change; `filter` is `null` |
| `filterset.restore` | `file`, `filterset?`, `activate?`, `allow_empty?` | a filter set change; `filter` is `null` |
| `folder.create` | `folder`, `subscribe?` | `{"command", "folder", "subscribed", "subscribe_error", "by_subscription"}` |
| `folder.rename` | `old`, `new` | `{"command", "old", "new", "filterset", "backup", "subscribed", "unsubscribed", "subscription_errors", "ok", "checks"}` |
| `folder.subscribe`, `folder.unsubscribe` | `folder` | `{"command", "folder", "subscribed"}` |
| `mail.mark` | `uids`, `folder?`, `uidvalidity?`, `read?`, `flagged?`, `keywords?`, `no_keywords?` | `{"command", "folder", "added", "removed"}` |
| `server.baseline.save` | | `{"command", "host", "path", "taken"}` |
| `config.migrate` | | `{"command", "old", "new", "restart"}` |

A filter set change is `{"command", "filter", "filterset", "activated",
"backup"}`: the filter by the name it has once the change is made, the
filter set, whether it is now the active one, and the copy taken before
the upload.

A plan's `document` is the plan `--dry-run --json` prints for the same
command (see *Output for scripts*), with the same options given. `server
baseline save` and `config migrate` print no plan document on the command
line, so theirs are the API's own:

* `server baseline save`: `host`, `path`, `taken`, `previous_taken`,
  `requires_known`, `drift` (as in `server baseline check`), and `diff`,
  the file's diff, `null` where none was saved before. `changes` is
  always `true`, since a save at least moves the date.
* `config migrate`: `old`, `new`, `entries` (`{"path", "mode",
  "directory"}`), and `references` (`{"key", "old", "new"}`, each
  `config.toml` path pointed at the new directory). With no old
  directory, `changes` is `false` and `old` and `new` are `null`.

#### The reads

* **`initialize`.** `protocol` versions the methods and the envelope;
  `json_version` versions the documents inside, as in *Output for
  scripts*. `capabilities` is what the provider declares, so a client
  offers only what it lists, as the command line does. `log: true` asks
  for `log` notifications.
* **`initialize`'s `old_name`** is what the rename from `mxfilter` left
  behind, which the command line warns about on stderr (see *Coming from
  mxfilter*): `{"settings", "config_dir"}`. `settings` lists each old
  `MXROUTE_*` name set without its `MAILCTL_*` name, as `{"old", "new",
  "where", "path"}`. `where` is `env file` or `environment`, and `path`
  is the env file as given (it may be relative), or `null`. mailctl reads
  no old name, so its setting comes from somewhere else or is unset.
  `config_dir` is `null`, or `{"old", "new", "operation"}` while the old
  config directory exists and the new one does not; `operation` is
  `config migrate`, the command that moves it, which is
  `config.migrate.plan` here. Names and places only: no old value is
  read, so none is sent. With nothing left behind, `settings` is `[]`
  and `config_dir` is `null`. It describes the settings the session
  started with, so it holds until `shutdown`.
* **`mail.search`.** `filter` is a filter document (see *Filter
  documents*), and `raw` a query in the host's own search language.
  `limit` is the most messages to list: 20 unless given, and every
  match where it is null. `sort` is `{"key", "reverse"?}`, with the
  keys `mail search --sort` takes.
* **`mail.search`'s `like`** is `{"uid", "uidvalidity"?}`, and does what
  `mail search --like UID --uidvalidity N` does: it adds criteria taken
  from that message in `folder`, its List-Id, else its From, and merges
  them with `filter` (a header `filter` gives replaces the one taken, and
  `filter`'s `match` and `compare` apply). The listing is of the
  message's folder, as the server spells it. There is no counterpart to
  `--derive` yet. With `uidvalidity` given and the folder renumbered
  since, it is refused with `uidvalidity_changed` before the UID is used.
* **`build_filter: true`** returns the filter document the criteria make,
  `{"version": 1, "criteria": {…}}`, in place of the listing: the
  document `filter.add.plan` and `filter.apply.plan` take as `filter`, so
  `like` and `build_filter` together fill a filter in from a message.
  Nothing is listed or saved. With no criteria, or a message with nothing
  to take them from, it is refused with `no_criteria`.
* `mail.search` refuses with `-32602`: a `like` with no `uid` or with a
  key it does not know; `raw` with `like` or `build_filter`; and `sort`
  with `build_filter`. These are the combinations `mail search` refuses.
  A `uid` below 1 is refused with `uid_below_one`, and one the folder does
  not hold with `no_such_message`, as `mail.view` refuses both.
* **`mail.senders`.** `by` is `address` (unless given), `domain`, or
  `list-id`. `top` is the rows kept, 20 unless given and every row where
  it is null; `minimum` leaves out a row counting fewer messages, 1
  unless given; `max_messages` is the ceiling, 5000 unless given, above
  which it is refused whole with `senders_ceiling`. A `top`, `minimum`,
  or `max_messages` below 1 is refused with `at_least_one`.
* **`filterset.show`** returns the filter set's text exactly as the server
  holds it, and its filters' names in order. `filters` is `null` where the
  text does not parse; the command line prints the text and then fails.
* **`server.test`** has a document of its own; the command line lays the
  same probes out for a person and offers none. `test` holds `provider`;
  `sources`, the files and places consulted; `settings`, as `{"name",
  "value", "source"}`; `password`, `{"state", "source"}`, where `state` is
  a fixed word and never the password; `connection`; `rules`, the
  extensions as `{"name", "advertised", "required", "enabled",
  "disabled_by"}` beside the `active` and `others` filter sets; `mail`,
  the `delimiter`, the number of `folders`, the `unsubscribed` ones, and
  the server's `facts`; `unrecognised`, as `filter` or `mail`; `baseline`;
  and `notes`. A `source` is `{"kind", "name", "path"}`, never a value.
  `baseline` is `null` where none is saved, `{"checked": true, "taken",
  "serious", "informational"}` where one is, and `{"checked": false,
  "error"}` where one is and cannot be compared.
* **`server.probe` with `report: true`** returns the redacted issue body
  `server probe --report` prints, as `{"title", "unrecognised", "body"}`,
  or `null` where mailctl recognises every server. Nothing is sent.
* **`server.baseline.show`** connects nothing. Its `version` is the
  baseline file's own, as stored.

#### Plan, decide, execute

**A change is plan, decide, execute.** A `.plan` changes nothing. Show its
`document`, let the user decide, then send the `.execute` with the
`plan_id` and nothing else. The plan's content is never sent back, so what
was shown is what runs.

* A plan id is used once. Once an execute has taken it, whether it then
  worked or was refused, or once `plan.discard` has, the id is refused
  with `plan_unknown`. Plans live in the process and end with it.
* **Only the newest 32 plans wait.** Making another drops the oldest, and
  its id is then refused with `plan_unknown`, as a used one is. Execute or
  discard a plan before making 32 more.
* **An execute refused for its parameters keeps its plan.** The parameters
  are checked before the plan is taken, so an unknown or missing one is
  answered `-32602` and the same id still works. A `plan_id` that belongs
  to another command is refused with `plan_unknown` and left waiting.
* **A plan that changes nothing is refused at execute** with
  `nothing_to_change`, sending nothing: the command line stops after
  showing such a plan. `filter.add.execute` is the exception and carries
  it out, as `filter add` does: a filter replaced by itself is still
  uploaded, with its backup. `folder.rename` and `server.baseline.save`
  always change something.
* If the filter set changed on the server after the plan was made (in
  webmail, say), or another filter set was made active, an execute that
  uploads is refused with `filterset_changed` and nothing is uploaded.
  `folder.rename.execute` with no filter to repoint uploads nothing and is
  refused the same way, before the folder is touched.
  Make a new plan.
* `filter.apply.execute` holds the plan to its own ceiling again, and it
  and `mail.mark.execute` check the folder's UIDVALIDITY again, refusing
  with `max_messages` or `uidvalidity_changed` and touching nothing.
* **`filter.add.plan`** takes `filter`, a filter document, and `actions`,
  both required; then `name?`, `filterset?`, `replace?`, `activate?`,
  `create_folder?`, and `subscribe?` (true unless given, and false only
  with `create_folder`). `placement?` is `{"where", "filter"?}`: `where` is
  `first`, `last`, `before`, or `after`, and `filter` names the filter the
  last two are relative to. `actions` is `{"move_to"?, "discard"?,
  "flags"?, "mark_read"?, "keep"?, "stop"?}`, meaning what the same
  actions mean to `filter add`. `redirect`, `notify`, and `vacation` are
  taken as strings and refused, with the same pointer the command line
  gives.
* **`filter.apply.plan`** acts on mail already delivered and saves no
  filter. Its `actions` are `filter.add.plan`'s without `stop`, which
  means nothing to a message already in a folder. `folder` is the folder
  to search, `--folder`'s default unless given, and `max_messages` the
  ceiling, 500 unless given: a plan over it is returned with `over_limit`
  true, and its execute is refused whole with `max_messages`. A folder
  `move_to` names that does not exist is refused with `folder_missing`
  unless `create_folder` is true; one it creates is sent as a
  `folder_created` event before any message is touched. `--like` has no
  counterpart: `mail.search` with `like` and `build_filter` makes the
  filter document.
* **`filter.move.plan`**'s `placement` is required, in
  `filter.add.plan`'s shape. **`filter.optimize.plan`**'s `skip` lists
  the kinds of change not to propose: `redundant`, `reorder`, or
  `merge`; any other is refused with `unknown_kind`.
* **`filterset.restore.plan`**'s `file` is a path on the API's host. The
  API expands nothing in it, neither `~` nor `$VAR`, and every message
  names the path as given; a relative one is taken from the API's working
  directory. The file is read when the plan is made, so the execute
  uploads what the plan showed. A file the session reads the password
  from, a password file or the env file, is refused unopened with
  `credential_file`.
* **`filterset.backup` is one method, not a pair**: nothing on the server
  changes. `output` is a file or a directory, as `filterset backup
  --output` takes it, and the backup directory unless given; a file that
  exists is refused, since a backup never replaces one. With `dry_run`
  true the file is named and not written, and `written` is `false`.
* **`folder.rename.execute`**'s `checks` are what reading the account back
  found, each `{"label", "ok", "detail", "operation", "arguments"}`;
  `ok` is `false` where any failed, and a failed one's `operation` names
  the command that would set it right. `filterset` and `backup` are
  `null` where no filter files into the folder.
* **`mail.mark.plan`**'s `read` and `flagged` are `true` to set, `false`
  to clear, and absent to leave alone; `keywords` are set and
  `no_keywords` cleared. With none of them it is refused with `no_marks`,
  and a `uid` the folder does not hold with `no_such_message`, marking
  nothing.
* **`server.baseline.save.execute`** writes the configured host's file
  under the config directory, never a path the client gave. It is refused
  with `baseline_changed`, writing nothing, where the file changed after
  the plan was made, since writing over it would lose another save.
* **`config.migrate`** moves files on the API's host only. A plan that
  would overwrite anything is refused with `migration_conflict`. Its
  execute is refused with `migration_changed`, before anything moves,
  where either directory no longer matches the plan, and the plan with
  `migration_reference_unrewritable` where a `config.toml` path reaches
  the old directory through a variable it cannot be rewritten without.
  The result's `restart` is always `true`: the API read its settings
  when it started, so only a process started afterwards reads what
  moved.

### The password

No method takes a password, and a request with a `password` parameter is
refused with `-32602`. The password comes from the settings, as for
`mailctl` (see *The password*); started with `--password-cmd`, the API
never shows the client the password at all. Where no setting gives one,
mailctl asks the client, with a request of its own, the first time a
server needs it:

```text
< {"jsonrpc": "2.0", "id": "mailctl-1", "method": "credentials.prompt", "params": {"request": 2, "prompt": "Password for you@example.com: "}}
> {"jsonrpc":"2.0","id":"mailctl-1","result":{"password":"..."}}
```

* mailctl's request ids are the strings `mailctl-1`, `mailctl-2`, and so
  on, so they cannot be mistaken for the client's. `request` is the id of
  the client's request that needed the password.
* To decline, answer with an error. The request that needed the password
  is then refused with `password_declined`. An answer that does not parse
  is answered `-32700` and counts as declining, and so does input ending.
  So do 64 messages sent while the prompt waits with no answer among
  them, since mailctl reads no further until some are served.
* When a server refuses a prompted password (`auth_failed`), mailctl
  forgets it and asks again on the next request.
* Cancelling the request that needed the password ends the wait (see
  *Cancelling a request*). An answer sent after that answers nothing, and
  the next request that needs the password asks again.
* The client is holding the user's password. Never log it.

### Cancelling a request

Send the `$/cancelRequest` notification, the Language Server Protocol's
name for it, with the id of the request to stop:

```text
> {"jsonrpc":"2.0","method":"$/cancelRequest","params":{"id":7}}
```

* **A request stops between exchanges with the server, never after its
  first write.** A write may have landed, and is never sent again, so a
  request that has sent one runs to its end and is answered as usual: a
  result means it ran. One stopped in time is refused with `-32800` (see
  *Errors*).
* **A cancel names only a request sent before it**: the one in hand, or
  one still waiting its turn ahead of it, which is then refused when its
  turn comes, without running. A cancel for any other id is ignored: one
  that is unknown, one already answered, and one met in its own turn,
  once every request before it has been answered. So a request that
  reuses an answered id is never stopped by a cancel sent before it.
* A cancel is answered with nothing. Sent as a request, with an id, it
  waits its turn like any other and is answered `null`.
* **mailctl reads at most 64 messages ahead of the one it is serving.**
  A cancel sent behind 64 others not yet served is not read, and so not
  seen, until some of them have been answered.
* `filter.apply.execute` is not stopped between batches of messages: it
  hands the whole plan to the server as one write, and its
  `max_messages` ceiling bounds how long that runs.

### Notifications

mailctl sends three notifications, which take no reply. `request` is the
id of the client's request each belongs to, and `channel` says which server
it came from: `filter` or `mail`.

| Method | Parameters | Sent |
|--------|------------|------|
| `event` | `{"request", "document": {"version", "event": {"kind", ...}}}` | for each step of a change as it lands; `kind` and the keys beside it are in the table below |
| `server.alert` | `{"request", "document": {"version", "alert": {"channel", "kind", "text"}}}` | always, for an alert or warning a server sent; the same one once per session |
| `log` | `{"request", "channel", "message"}` | for the protocol exchange `--verbose` shows, only after `initialize` with `log: true` |

| Event `kind` | Keys beside it | Sent by |
|--------------|----------------|---------|
| `filterset_backed_up` | `filterset`, `path` | every execute that uploads a filter set, before it uploads |
| `filterset_uploaded` | `filterset`, `activated` | the same, once it has uploaded |
| `folder_created` | `folder`, `subscribed`, `subscribe_error`, `by_subscription` | `filter.add`, and `filter.apply` before it touches a message |
| `folder_renamed` | `old`, `new`, `children` | `folder.rename` |
| `subscription_changed` | `folder`, `subscribed` | `folder.rename` |
| `file_moved` | `source`, `destination` | `config.migrate` |
| `reference_rewritten` | `key`, `new`, `rewritten` | `config.migrate` |
| `old_dir_removed` | `path` | `config.migrate` |

### Errors

| Code | Means |
|------|-------|
| `-32700` | a line that is not UTF-8 JSON; answered with id `null` |
| `-32600` | not a request: not an object, no `"jsonrpc": "2.0"`, no method name, an id that is not a string or a number, or a batch |
| `-32601` | no such method |
| `-32602` | a parameter missing, unknown, or of the wrong type, or a `password` |
| `-32603` | an unexpected failure; the message names its type only, and `--debug` shows the rest on stderr |
| `-32800` | the request was cancelled, and nothing on the server was changed |
| `1` | mailctl refused the request |

A cancelled request's message is `the request was cancelled; nothing on
the server was changed`, and its `data` is `{"written": false}`: no write
was sent, though a read, such as the server's check of a filter set, may
already have sent it text. Where the request wrote a backup before it
stopped, `data` also carries `backup`, the backup's path.

A refusal's `data` is the error document from *Output for scripts*,
`{"version", "error": {"message", "code", "fields"}}`, where `code` names
the condition. Branch on it, never on the message. The API's own are
`not_initialized`, `already_initialized`, `plan_unknown`, and
`password_declined`. The rest come from the work both interfaces share,
so the command line's `--json` carries the same ones: among them
`nothing_to_change`, `filterset_changed`, `filterset_empty`,
`auth_failed`, `credentials_not_ascii`, `subscribe_needs_create`,
`unknown_sort_key`, `unknown_kind`, `folder_missing`, `folder_exists`,
`no_such_message`, `uid_below_one`, `backup_exists`, `credential_file`,
`baseline_changed`, `baseline_read_failed`, `migration_conflict`,
`migration_changed`, and `migration_reference_unrewritable`.

### An exchange

Adding a filter, with the password left out of the settings so that
mailctl asks for it. Lines the client sends start `>`, lines mailctl sends
start `<`, and `…` marks what is left out.

```text
> {"jsonrpc":"2.0","id":1,"method":"initialize"}
< {"jsonrpc": "2.0", "id": 1, "result": {"protocol": 1, "json_version": 2, "mailctl": "…", "provider": "mxroute", "capabilities": {…}, "old_name": {"settings": [], "config_dir": null}}}
> {"jsonrpc":"2.0","id":2,"method":"filter.add.plan","params":{"filter":{"version":1,"criteria":{"match":"all","compare":"contains","terms":[{"header":"From","value":"noreply@github.com"}]}},"actions":{"move_to":"Lists/GitHub"},"name":"github","create_folder":true}}
< {"jsonrpc": "2.0", "id": "mailctl-1", "method": "credentials.prompt", "params": {"request": 2, "prompt": "Password for you@example.com: "}}
> {"jsonrpc":"2.0","id":"mailctl-1","result":{"password":"…"}}
< {"jsonrpc": "2.0", "id": 2, "result": {"plan_id": "1cf4f027f29155b8", "document": {"version": 2, "plan": {"command": "filter add", …}}}}
> {"jsonrpc":"2.0","id":3,"method":"filter.add.execute","params":{"plan_id":"1cf4f027f29155b8"}}
< {"jsonrpc": "2.0", "method": "event", "params": {"request": 3, "document": {"version": 2, "event": {"kind": "filterset_backed_up", …}}}}
< {"jsonrpc": "2.0", "method": "event", "params": {"request": 3, "document": {"version": 2, "event": {"kind": "folder_created", …}}}}
< {"jsonrpc": "2.0", "method": "event", "params": {"request": 3, "document": {"version": 2, "event": {"kind": "filterset_uploaded", …}}}}
< {"jsonrpc": "2.0", "id": 3, "result": {"version": 2, "result": {"command": "filter add", "filter": "github", …}}}
> {"jsonrpc":"2.0","id":4,"method":"filter.add.execute","params":{"plan_id":"1cf4f027f29155b8"}}
< {"jsonrpc": "2.0", "id": 4, "error": {"code": 1, "message": "no plan with that id is waiting: …", "data": {"version": 2, "error": {"message": "no plan with that id is waiting: …", "code": "plan_unknown", "fields": {"plan_id": "1cf4f027f29155b8"}}}}}
> {"jsonrpc":"2.0","id":5,"method":"shutdown"}
< {"jsonrpc": "2.0", "id": 5, "result": null}
```

## TUI

`mailctl-tui` is a terminal UI for mailctl, and the API's first client. It
starts `mailctl-api` and shows only what the API returns. It is a package
of its own, in `clients/tui/`, so installing mailctl never pulls in its
terminal library; why it is built this way is [ADR 0009][adr9].

It shows **the filters** in the active filter set, in the order they run,
what each one does, and the ones that cannot fire where they are. From
there it can find a message and make a filter for mail like it:

* **Search** (`s`) a folder, or your default one when the field is left
  empty, optionally for mail whose From or Subject holds some text.
* **Read** a message by choosing it with Enter. It stays unread, and an
  attachment is named, never saved.
* **Add a filter like it** (`a`). mailctl fills the criteria in from the
  message, its List-Id, else its From, as `mail search --like` does, and
  you choose what the filter does. The form offers only what your
  provider declares: where the filter goes, letting later filters see the
  mail, showing a new folder in webmail, and each action.
* **See what would change** (`Ctrl+S`): what the filter matches and does,
  any filter that would keep it from firing, the folder it would create,
  and the diff. `y` saves it, backing the filter set up first and saying
  where; Escape cancels, and nothing is saved. The mail already
  delivered is left alone; `mailctl filter apply` acts on that.

`r` reads the filters again, Escape goes back a screen, and `q` quits; the
keys on each screen are in [clients/tui/README.md][tui]. An alert from the
mail server is shown as it arrives, whichever screen is up. Where mailctl
has no password configured, the TUI asks for it in a masked field, hands
it to mailctl, and keeps no copy.

```bash
uv tool install .                   # mailctl, for the mailctl-api command
uv tool install ./clients/tui       # the TUI
mailctl-tui
mailctl-tui --env-file ~/.config/mailctl/work.env
```

The TUI runs the `mailctl-api` it finds on `PATH`, so mailctl has to be
installed as well. It takes mailctl's settings from the same places: any
argument it does not know itself is passed on to `mailctl-api`, as
`--env-file` is above. `--api COMMAND` starts another command in place of
`mailctl-api`.

Installing it for development, and `make testtui`, which type-checks it
and runs its tests, are in [clients/tui/README.md][tui].

## MXRoute specifics

MXRoute documents very little of its Sieve surface, so what mailctl knows
about it is kept in three tiers — documented, observed on one server, and
unknown — in the provider's record,
[mailctl/providers/mxroute/RECORD.md][record], which has the sources and
dates. Nothing is promoted a tier to make the documentation read better,
and nothing observed is built in: mailctl reads the capabilities, the
active script, and the folder delimiter from the server on every run.

### Documented

* The `redirect` action is **disabled server-side** — MXRoute announced this
  on 2024-03-22, saying their own forwarders "are designed to properly handle
  SRS" where a Sieve redirect does not. `--redirect` fails with a pointer to
  the panel's Forwarders (or the `mxroute_forwarder` Terraform resource).
* The username is the **full email address**, on both IMAP and ManageSieve.
* The hostname is **per-account** — the same as your primary MX record, shown
  on the panel's Email Clients page. There is deliberately no default.
* IMAP is **993** (implicit TLS) or **143** (STARTTLS). mailctl picks the
  mode from the port: anything other than 143 is treated as implicit TLS.
* MXRoute's REST API exposes nothing for filters or Sieve, which is why this
  tool speaks ManageSieve rather than an API.

### Observed on one server, not promised

A probe of one account's server saw these. They describe that server on
that day, not MXRoute.

* **ManageSieve on port 4190 with STARTTLS worked.** That is the IANA/RFC
  5804 registered port and the Dovecot default, which is why it is
  mailctl's default; MXRoute documents neither. `--sieve-port` and
  `--sieve-tls` exist because of that, and a connection failure says so
  rather than implying you mistyped.
* **The folder delimiter was `.`** (Maildir++, so `INBOX.Lists.News`), and an
  `INBOX.spam` folder existed — lowercase, not `INBOX.Junk`. Neither is
  assumed. The delimiter is **detected at runtime** from the server's folder
  list, and folder names are matched against that list — type `Lists/News`
  or `INBOX.Lists.News` and the server's spelling is used for both the Sieve
  rule and the move. `mailctl folder list` is the authority for your account.
  The one exception is `--no-mail`, which has no folder list to consult and
  falls back to `.` (or `--delimiter`), and warns that it did.
* **`vacation` was advertised and `enotify` was not.** mailctl refuses both
  `vacation` and `notify` as **our own choice**, not as an MXRoute
  limitation: the panel does autoresponders, and mailctl does not write
  either action. Its error says so and points at the control panel.
  `mailctl server test` prints what your server actually advertises.

Folder names are **case-sensitive**, except `INBOX` itself (RFC 3501), so
`INBOX.Lists` and `INBOX.lists` are two folders. When the folder you name
does not exist but one differing only in case does, mailctl warns and
names both — and with `--create-folder`, says a second folder will be
created beside it.

### Unknown

* **Whether any of the observations above hold for another server**, or for
  the same one after MXRoute's migrations.
* **ManageSieve script-size, script-count, and rate limits.** There is no
  documented ceiling to design against.
* **Whether your account has a filter before Sieve** (below).

The record lists the rest.

### Which script mailctl edits

The active script's name is read from `LISTSCRIPTS` and written back to, and
is never guessed — the webmail's script name is server-side config, and
MXRoute is mid-migration on both its panel and Dovecot. `--filterset` overrides
it. With nothing active, a script called `mxfilter` — what the tool created
under its old name — is still recognised as its own and reused rather
than a second one made beside it; the name `mailctl` is used only when there
is neither.

The server runs one script, and editing a different one does not change
which. `--filterset NAME` on a script that is not the active one stores the
change and leaves NAME inactive, and mailctl says so before it uploads;
`--activate` makes NAME the active script as well. When the account has no
active script at all, the script mailctl writes is activated, since
otherwise nothing would run it.

### A filtering stage mailctl cannot see

mailctl sees **Sieve only**. Mail may pass through an earlier filter first,
and nothing mailctl reports covers it.

DirectAdmin's **Email Filters** panel writes an Exim filter that runs after
the mail server accepts a message and before it is delivered to the mailbox,
which is where Sieve runs. A message that filter matches is dropped (or sent
to a spam folder) there, and Sieve never sees it. DirectAdmin's filter rules
block by domain, address, word, or size; they do not file mail into folders.
MXRoute has provisioned such a filter with `where=delete`, which discards
high-scoring mail before Sieve.

**What is not known** is whether your account has one. MXRoute documents
neither this filter nor Sieve, and is phasing DirectAdmin out — MXRoute has
said that new customers already have no interaction with DirectAdmin — so
whether accounts set up since then still carry the filter is unconfirmed.

mailctl cannot read or change that filter, and will not: DirectAdmin's
filters need domain-owner credentials, and mailctl logs in as a mailbox. So:

* `mailctl server test` reports the Sieve and IMAP side, not the whole path
  mail takes.
* `mailctl filter list` works out which rules can never fire from their order
  in the Sieve script. A message dropped before Sieve is outside that
  analysis.
* A filter set in the panel is invisible here. If a rule never seems to fire,
  the mail may never have reached Sieve: check the panel's filters, if your
  account has them.

## Known limits

* `is` and `matches` cannot be expressed in IMAP SEARCH, which only does
  case-insensitive substring matching. So the existing-mail pass searches
  deliberately too broadly and then re-checks every candidate's real headers
  in Python against the Sieve semantics above. Correct, but it fetches the
  header block of every candidate the broad search returned, not just the
  ones that end up matching.
* Merging round-trips the filter set through a parser. A filter with neither a
  `# rule:[NAME]` nor a `# Filter: NAME` comment above it is renamed
  `Unnamed rule N`, and formatting is normalized. Your own `#` comments are
  kept, each above the filter it preceded; `/* ... */` comments are dropped.
  The diff shows all of this before anything is uploaded.
* `filter list` and `filter optimize` judge the filter set from the filters' own
  conditions and their order. Nothing runs a message through your existing
  filters: mailctl can apply criteria you give it to old mail, but it cannot
  tell you which of your filters would have caught a given message.

[adr2]: adr/0002-non-destructive-script-merge.md
[adr5]: adr/0005-restore-may-replace-an-unparseable-script.md
[promise]: RELEASING.md#what-v1-promises
[adr8]: adr/0008-json-rpc-api-interface-for-out-of-process-clients.md
[adr9]: adr/0009-python-textual-tui-as-a-separate-package.md
[reader]: docs/MAIL-READER-REQUIREMENTS.md
[record]: mailctl/providers/mxroute/RECORD.md
[tui]: clients/tui/README.md
[verify]: docs/VERIFYING.md
