# mailctl

Manage MXRoute email filters from the command line, end to end: build a
Sieve rule from criteria flags, merge it into the account's **active**
script without disturbing the rules already there, and apply the same
criteria to the mail that has already been delivered. It also finds,
counts, reads, and marks that mail, and manages the folders it is filed
into.

## What it is for

A Sieve filter only ever sees **new** mail. Write a rule in webmail and
every message already in the mailbox stays exactly where it was. mailctl
does both halves, as two commands that take the same criteria: `add` saves
the rule, and `apply` acts on the mail already there.

It exists for a second reason too: the filter and search screens in
Roundcube, the webmail MXRoute ships, are too restricted. mailctl's
criteria go further — any header, text in the body, whole-value and
wildcard comparisons, and, for mail already delivered, dates and read or
flagged state — and it can list, count, sort, and read the mail, so the
message a rule should catch can be found without leaving the terminal. The
rules it writes are in the form webmail uses, so they show up there, and
the rules you made in webmail are kept ([ADR 0002][adr2]).

Its scope is managing the filters and settings on one mailbox, by hand or
from a script: rules and their order, folders and their subscriptions,
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

`default_folder` is where `add` and `apply` file mail when no `--fileinto`
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
| `mailbox` | Writes plain `fileinto`, never `fileinto :create`, and creates a new folder over IMAP instead. With `--no-imap` a folder that needs creating is refused, as it is on a server without `mailbox`. |
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
name.

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

A message the IMAP server marks as an **alert** — a mailbox nearly full,
maintenance tonight — is printed on stderr, on any command that connects to
IMAP, with or without `--verbose`, and once per run however often the
server repeats it:

```text
mailctl: alert from the imap server: <text>
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

### Find the mail a rule should catch

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

### Save a rule, then apply it to the mail already there

```bash
# See exactly what would change, without changing it.
mailctl filter add --from newsletter@example.com --fileinto Lists/News --dry-run

# Save the rule: merge it, back up, upload, activate. It filters new mail
# only; the mail already delivered is left alone.
mailctl filter add --from newsletter@example.com --fileinto Lists/News

# Then act on the mail already there, with the same criteria and actions.
# It previews the messages and asks before it moves any.
mailctl filter apply --from newsletter@example.com --fileinto Lists/News

# Take the criteria from a message you already have, for the rule and then
# for the mail: its List-Id, else its From, as with 'search --like'. A
# criteria flag replaces what was taken for its header.
mailctl filter add --like 4127 --fileinto Lists/News --dry-run
mailctl filter apply --like 4127 --fileinto Lists/News

# Or from a filter document (see "Filter documents" below); '-' reads it
# from standard input.
mailctl filter add --filter news.json --fileinto Lists/News
mailctl filter apply --filter news.json --fileinto Lists/News
mailctl mail search --like 4127 --build-filter --json \
    | mailctl filter add --filter - --fileinto Lists/News

# Mail already delivered only, with no rule. --create-folder because the
# target may not exist yet.
mailctl filter apply --subject '[SPAM]' --fileinto Quarantine --create-folder \
    --mark-read
mailctl filter apply --flagged --before 2026-01-01 --fileinto Archive --dry-run

# Copy rather than move: --keep files a copy and leaves each message where
# it is, as the saved rule does. A re-run skips what the folder already
# holds (same Message-ID); a message with no Message-ID cannot be looked
# for, so it is copied each time, and apply says how many.
mailctl filter apply --list-id news.example.com --fileinto Lists/News --keep
```

The actions are `--fileinto FOLDER`, `--discard`, `--keep`, `--mark-read`,
and `--flag NAME`; `--no-stop` lets later rules run too. A rule's name is
derived from its criteria unless `--name` gives one, and `--replace`
overwrites a rule of the same name. `--first`, `--last` (the default),
`--before RULE`, and `--after RULE` place it in the script.

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

**`add` refuses the dates and states.** A rule runs as each message is
delivered, and every message being delivered is new: unread, unflagged,
and zero days old. A rule testing any of those would be always true or
never true, so `add` says why and points at `search` and `apply`, which is
where they mean something. So does `add --filter` given a document that
carries one. `add` has no date `--before` at all: its `--before RULE`
places the rule in the script.

**`--body` on `add` needs the server's Sieve `body` extension.** A rule
with a body test is refused, naming the extension, on a server that does
not advertise it or when `disabled_extensions` turns it off; there is no
fallback for a test. `search` and `apply` never write Sieve, so they take
`--body` either way.

**The two halves read slightly different text.** The Sieve `body` test
reads the message's text parts, decoded. IMAP's search, which `search` and
`apply` use, reads whatever parts the server decodes, which may include an
attachment's text. So `apply --body` can find a word the rule would not.
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
can be saved as the rule and applied to the mail already there. It takes
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
# it, and every rule filing into it is repointed; nothing else in the
# script changes. The account is read back afterwards to check it landed.
mailctl folder rename Github.Notificaitons Github.Notifications --dry-run
mailctl folder rename Lists/Old Lists/Archive
```

Folder names are normalized against the server's own: `Lists/GitHub` and
`INBOX.Lists.GitHub` name the same folder. `INBOX` cannot be renamed, and
the new name must not exist yet. A name that differs from an existing
folder only in case is refused by `create-folder` and `rename-folder`.

### Manage the rules

```bash
# The scripts on the server, and one script's text (the active one by
# default).
mailctl filterset list
mailctl filterset show

# The rules in the order the server runs them, and any an earlier rule
# makes unreachable ('!' is decided, '?' worth checking). Changes nothing.
mailctl filter list

mailctl filter remove from-newsletter-example-com

# Reorder a rule without restating it; reports what the move would starve.
mailctl filter move from-newsletter-example-com --first --dry-run
mailctl filter move from-newsletter-example-com --after keep-boss

# Propose a better arrangement of the whole rule set: remove a rule that
# only repeats an earlier one, move a specific rule ahead of the broader
# one starving it, and merge rules doing the same thing into one rule with
# a key list. Only what the rules decide is changed; guesses are reported.
mailctl filter optimize --dry-run
mailctl filter optimize --skip merge

# Switch a rule off without deleting it, and back on. Written the way
# Roundcube writes it, so webmail shows it as disabled too.
mailctl filter disable from-newsletter-example-com --dry-run
mailctl filter disable from-newsletter-example-com
mailctl filter enable from-newsletter-example-com

# Give a rule a better name. Only the name changes: the rule's conditions,
# actions, position, and disabled state, and every other rule, stay as
# they were.
mailctl filter rename "Herrschners Spam" "Yarn shops" --dry-run
mailctl filter rename "Herrschners Spam" "Yarn shops"
```

`optimize-rules` never moves, merges, or removes a disabled rule, and the
script stays a flat list of rules. `--skip` takes `redundant`, `reorder`,
or `merge`, and is repeatable.

`rename-rule` edits the rule's name line in place, so the diff it shows is
the whole change. It refuses, before anything is uploaded, a rule that is
not there, a new name that is empty or already another rule's, and one the
script cannot hold as a name: a line break or other control character,
spaces at the end, or a name marker inside it. A rule with no name written
in the script (`rules` lists it as `Unnamed rule N`) cannot be renamed. A
merged rule keeps the first rule's name, so `rename-rule` after
`optimize-rules` gives it one that fits.

### Back up and restore

```bash
# Save the active script, byte for byte, before you touch anything.
mailctl filterset backup
mailctl filterset backup --output ~/mailctl-before-first-run.sieve

# Put a backup back over the active script: diff, back up, confirm.
mailctl filterset restore ~/mailctl-before-first-run.sieve --dry-run
mailctl filterset restore ~/mailctl-before-first-run.sieve
```

Where backups go, and what `restore` does and refuses, are in *Safety*.

### Watch the servers for change

MXRoute is migrating its servers, and a server that changes underneath a
filter does not announce it: a rule just stops behaving as it did.

```bash
# Save what the servers say now as this host's baseline; later, ask what
# has changed since and what it means for your rules. Saving writes a
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
of drift; `check-baseline` reports it, and `mailctl server test` adds one line
saying whether there is any. Refreshing the baseline is `save-baseline`
again, which shows what changed and the file's diff and asks first
(`--yes` skips the question, `--dry-run` stops before it).

`check-baseline` exits **0** with no drift, **3** with informational drift
only, and **4** with serious drift, so a scheduled run can alert on either;
**1** is a failure (no baseline saved, one that cannot be read, no
connection). `--json` prints the report as a versioned document, and
`show-baseline`, which does not contact the server, prints the saved file
as it is with `--json`.

The file is `$XDG_CONFIG_HOME/mailctl/baselines/<host>.json`, one per
`host` setting, written mode 0600 in a directory created 0700. What
describes the server is kept once; the active script is kept per account.
It holds no credential. A file mailctl cannot read — damaged, or from
another version — is refused by name and never overwritten; move it aside
and save again.

## Safety

* `--dry-run` changes nothing, on every mutating command. It prints whatever
  that command would have changed: the Sieve diff for `add`,
  `remove-rule`, `move-rule`, `disable-rule`, `enable-rule`, and
  `rename-rule`; the list of matching messages for `apply`; each message's
  flags and what would change for `mark`; the file that would have been
  written for `backup`; the folders that would move, and the Sieve diff,
  for `rename-folder`; each proposed change, and the Sieve diff, for
  `optimize-rules`.
* `add` **never touches mail already delivered**; `apply` is the only
  command that does. After saving a rule, `add` says so and points at
  `apply`.
* `--like`, on `search`, `add`, and `apply`, **shows you the message
  first** — Date, From, To, Subject, and List-Id when it has one — before
  anything is derived from it, and so before a rule is written or mail is
  moved. The UID is something you read out of webmail by hand, and
  the derived criteria look equally plausible whichever message produced
  them, so the headers are the only thing that catches a mistyped digit
  before mail starts moving. `--uidvalidity` catches a UID that has gone
  stale (see *Mark messages*).
* `rename-folder` **validates the rewritten script before it touches the
  folder**, then renames, fixes the subscriptions, and stores the script
  straight after, so a rule points at a missing folder for as short a
  time as possible. It edits only the folder names in the rules; every
  other byte of the script is kept. It does not report success on the
  folder list alone: it reads the account back and checks the new name is
  subscribed where the old one was, holds at least the messages the old
  one did, and that no rule still files into an old name, exiting
  non-zero naming any that failed. A step that fails part-way is not
  retried; the error says what was done and how to undo it
  (`mailctl folder rename NEW OLD`).
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
* The Sieve diff is shown with **both sides in mailctl's own formatting**.
  A merge re-renders the whole script, so a diff against the server's raw
  copy would report every re-indented line as a change — on a hand-written
  script that is most of the file, and it reads exactly like something
  having gone wrong. The reformat is real, so mailctl says so on a line
  above the diff, and only while the server's copy is still in some other
  formatting. What gets uploaded and what gets backed up are unaffected.
* The current active script is backed up to a timestamped file before any
  upload, and the path is printed. `mailctl filterset backup` takes the same
  copy on demand, without changing anything on the server.
* Backups land in `$XDG_CONFIG_HOME/mailctl/backups` (usually
  `~/.config/mailctl/backups`) — beside your `config.toml`, one file per
  backup, named `<script>-<UTC timestamp>.sieve`. XDG would call a backup
  *state* rather than config; keeping it here is a deliberate departure from
  that, not something XDG endorses, because a backup you cannot find is not a
  backup. `--backup-dir`, `MAILCTL_BACKUP_DIR`, and `backup_dir` in
  `config.toml` move it, with `~` and `$VAR` expanded in each. The file is
  written mode `0600` in a directory created `0700`: a Sieve script is not a
  password, but it does say who you correspond with and how you sort it.
* **`mailctl filterset restore FILE` puts a backup back.** The backup is the
  server's exact bytes — no banner lines, nothing reformatted — and restore
  uploads them exactly, over the active script — or over the one `--script
  NAME` names, which stays inactive unless `--activate` is given. No other
  stored script is touched. It shows the raw diff against what the server has
  now, backs the current script up first, lets the server validate the file,
  and asks before it replaces anything. It is the one command that
  **replaces** rather than merges: a rule added since the backup was taken is
  removed, and the diff shows it. It works even over a script mailctl cannot
  parse ([ADR 0005][adr5]). An empty FILE would remove every rule, so it is
  refused unless `--allow-empty` is given. FILE is read and checked before
  mailctl connects, and `~` and `$VAR` in it are expanded. If the account has
  no active script, restore refuses rather than guess, and `--script NAME` is
  the way back: NAME is restored and made active.
* Rules are merged into the parsed existing script, never appended blindly,
  so other rules survive. If the existing script cannot be parsed, mailctl
  stops rather than overwrite it.
* `checkscript` runs on the server before `putscript`.
* `apply` **always previews and always confirms** before it
  touches anything — `--dry-run` shortens that path, it is not what creates
  it. `--yes` skips the prompts. Deletion says in as many words that it
  cannot be undone; a move says it can be reversed; a copy says the
  originals stay where they are.
* `apply --fileinto FOLDER --keep` **copies rather than moves**, as the
  saved rule does: a rule that files and keeps leaves the message in place
  too. Any `--mark-read` or `--flag` is set on the originals before they
  are copied, so the copies carry it as well. **A re-run copies only what
  the folder lacks**: a match whose Message-ID the folder already holds is
  skipped, and one with no Message-ID, which cannot be looked for, is
  copied every time; the preview says how many of each. When the folder
  holds every match and there is no flag to set, `apply` says so and
  changes nothing.
* `apply --discard --keep` **deletes nothing**. An explicit keep outlives a
  discard in Sieve (RFC 5228, section 4.4), and `apply` does what the rule
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
* A `--fileinto` target that does not exist is a **warning, not an error**,
  unless you pass `--create-folder`. `add` will still write the rule, and
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
* On a server that advertises the Sieve `mailbox` extension, the rule says
  `fileinto :create` **as well**, so Sieve recreates the folder if it is
  later deleted. mailctl still creates and subscribes the folder over IMAP
  itself, because Sieve only creates it when the first message arrives,
  when mailctl is not running to subscribe to it. With `--no-imap`, Sieve
  is the only thing that can create the folder, and mailctl says it may
  not appear in webmail until you run `mailctl folder subscribe` on it.
* The folder is **announced when the change is shown and created only when
  it is applied** — for `add`, once the server has accepted the new script
  and just before it is stored; for `apply`, after you confirm the move. A
  dry run, an abort, or a rejected script leaves no stray folder, and an
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
| `filterset list` | `{"version", "scripts": [{"name", "active"}]}` |
| `folder list` | `{"version", "delimiter", "prefix", "folders": [{"name", "subscribed"}]}`; with `--counts`, each folder also has `"messages", "unseen", "size"` (`null` where the server gave none) |
| `mail search` | `{"version", "folder", "uidvalidity", "more", "sort", "messages": [{"uid", "received", "size", "flags", "has_attachments", "from", "subject", "folder"}]}` |
| `mail senders` | `{"version", "folder", "by", "messages", "unread", "groups", "senders": [{"key", "name", "total", "unread", "unread_percent"}]}` — busiest first; `messages`, `unread`, and `groups` count everything matched, rows `--top` and `--min` left out included; `key` is `null` for mail with no address or no List-Id |
| `mail view` | `{"version", "message": {"uid", "folder", "uidvalidity", "size", "flags", "headers": [{"name", "value"}], "body", "body_from_html", "attachments": [{"name", "content_type", "size"}]}}` |
| `filter list` | `{"version", "script", "rules": [{"position", "name", "disabled", "stops", "combinator", "tests", "actions", "unmodelled"}], "findings": [{"certainty", "broad", "narrow", "reason"}]}` |
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
  `diff`, on a script change, is then `null`. A `rename-folder` plan always
  changes something; its `diff` is `null` when no rule files into the
  folder, and the script is then left alone.
* An `optimize-rules` plan lists `removals` (`{"rule", "covered_by"}`),
  `reorders` (`{"rule", "before"}`), `merges` (`{"into", "absorbed",
  "header", "match_type", "keys"}`), and `uncertain` (`{"kind", "rules",
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

**An error's `code`** is a short name for the condition, so a script can
tell one refusal from another without reading the message: for example
`no_criteria`, `no_action`, `max_messages`, `senders_ceiling`,
`no_such_folder`, `state_in_rule`, `missing_settings`, and
`uidvalidity_changed`. An error with no condition of its own has no `code`.

**Exit status**, on every command:

| Status | Means |
|--------|-------|
| 0 | done, including "nothing to change" |
| 1 | a failure or a refusal, with the reason on stderr (one JSON line under `--json`) |
| 2 | the argument parser rejected the command line — an unknown command or flag, a value it cannot read, `--no-subscribe` without `--create-folder`; the message is plain text, even under `--json` |
| 3, 4 | `check-baseline` only: informational, or serious, drift |
| 130 | interrupted |

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
  The one exception is `--no-imap`, which has no folder list to consult and
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
MXRoute is mid-migration on both its panel and Dovecot. `--script` overrides
it. With nothing active, a script called `mxfilter` — what the tool created
under its old name — is still recognised as its own and reused rather
than a second one made beside it; the name `mailctl` is used only when there
is neither.

The server runs one script, and editing a different one does not change
which. `--script NAME` on a script that is not the active one stores the
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
* Merging round-trips the script through a parser. A rule with neither a
  `# rule:[NAME]` nor a `# Filter: NAME` comment above it is renamed
  `Unnamed rule N`, and formatting is normalized. Your own `#` comments are
  kept, each above the rule it preceded; `/* ... */` comments are dropped.
  The diff shows all of this before anything is uploaded.
* `rules` and `optimize-rules` judge the script from the rules' own
  conditions and their order. Nothing runs a message through your existing
  rules: mailctl can apply criteria you give it to old mail, but it cannot
  tell you which of your rules would have caught a given message.

[adr2]: adr/0002-non-destructive-script-merge.md
[adr5]: adr/0005-restore-may-replace-an-unparseable-script.md
[reader]: docs/MAIL-READER-REQUIREMENTS.md
[record]: mailctl/providers/mxroute/RECORD.md
[verify]: docs/VERIFYING.md
