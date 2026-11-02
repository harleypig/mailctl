# Use, by task

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
would change first, and `--dry-run` stops there (see [*Safety*][safety]).

A message the mail server marks as an **alert** — a mailbox nearly full,
maintenance tonight — is printed on stderr, on any command that connects to
it, with or without `--verbose`, and once per run however often the server
repeats it:

```text
mailctl: alert from the mail server: <text>
```

One sent before the connection is encrypted is ignored, since anyone on the
network could have written it.

## Check the servers

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

## Find the mail a filter should catch

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

### Sorting a listing

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

## Save a filter, then apply it to the mail already there

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

`apply` alone takes `--max-messages` (default 500; see [*Safety*][safety]) and
`--move-threshold` (default 25), above which the confirmation treats a move
as a large one. `add` asks nothing, so it takes no `--yes`.

### Body, dates, and state

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

### `--compare` tests the whole header value

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

### Filter documents

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

## Mark messages

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

## Manage folders

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

## Manage the filters

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

## Back up and restore

```bash
# Save the active filter set, byte for byte, before you touch anything.
# --output names a new file: one that exists is refused, never replaced.
mailctl filterset backup
mailctl filterset backup --output ~/mailctl-before-first-run.sieve

# Put a backup back over the active filter set: diff, back up, confirm.
mailctl filterset restore ~/mailctl-before-first-run.sieve --dry-run
mailctl filterset restore ~/mailctl-before-first-run.sieve
```

Where backups go, and what `restore` does and refuses, are in
[*Safety*][safety].

## Watch the servers for change

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

[safety]: SAFETY.md
