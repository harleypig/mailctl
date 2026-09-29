# mailctl

Manage MXRoute email filters from the command line: build a Sieve rule from
criteria flags, merge it into the account's **active** script without
disturbing the rules already there, and apply the same criteria to mail that
has already been delivered. It also lists and reads the mail already in a
folder, so you can find the message a rule should catch.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
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

Resolution order is **CLI flag > env file > environment > config file >
default**.

The tool was called `mxfilter` until recently, and the rename was a clean
break: the old config directory (`$XDG_CONFIG_HOME/mxfilter/`) and the old
`MXROUTE_*` variables are **not read**. Every command warns while the old
directory exists and the new one does not, and `mailctl migrate-config`
moves its contents — `config.toml`, the backups, anything else — across,
keeping modes and overwriting nothing (`--dry-run` shows what would move).
An old `MXROUTE_*` variable still set, with no `MAILCTL_*` counterpart, is
named in a warning; rename it. [CHANGELOG.md](CHANGELOG.md) lists every old
and new name.

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

`--folder`, on the commands that read mail (`apply`, `search`, `view`,
`mark`, and `add --like`), resolves the same way: `MAILCTL_SOURCE_FOLDER`,
then `source_folder` in the config file, then `INBOX`.

`--provider` names the mail host mailctl talks to, and resolves the same
way: `MAILCTL_PROVIDER`, then `provider` in the config file, then `mxroute`.
`mxroute` is the only provider today, so there is nothing to set yet. An
unknown name is refused before anything connects, and the error lists the
known ones. `mailctl test` shows which provider a run uses and where that
came from.

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
the config file's list, and `mailctl test` names where the `none` came from.
It must stand alone — `none` beside an extension name is refused.

A disabled extension counts as not advertised:

| Disabled | What mailctl does |
|----------|-------------------|
| `mailbox` | Writes plain `fileinto`, never `fileinto :create`, and creates a new folder over IMAP instead. With `--no-imap` a folder that needs creating is refused, as it is on a server without `mailbox`. |
| `fileinto` | Refuses a rule that files mail into a folder. `--discard`, `--keep`, and flag-only rules still work. |
| `imap4flags` | Refuses a rule that sets a flag (`--mark-read`, `--flag`). |
| any other name in the table below | Nothing today — mailctl writes none of them — but it holds if a later version does. |

A refusal names the setting and where it came from. A name mailctl does not
know is an error, naming it, before anything connects — a typo would
otherwise switch off nothing without a word. Disabling an extension the
server does not advertise anyway changes nothing and is not an error.
`mailctl test` shows one table of Sieve extensions — every name the server
lists and every name below — each `available` or `unavailable`, and an
available one `enabled` or `disabled (...)` with the source. An unavailable
one shows nothing more, disabled or not, since there is nothing to turn
off. A `*` marks the ones mailctl's own rules can need.

The names mailctl knows — `mailctl test` always lists these, plus whatever
else the server advertises — and what each one adds to Sieve:

| Extension | Spec | Adds | mailctl writes it |
|-----------|------|------|-------------------|
| `fileinto` | RFC 5228 | `fileinto`: deliver into a named folder instead of INBOX | every rule that files mail |
| `imap4flags` | RFC 5232 | `setflag` / `addflag` / `removeflag`, the `hasflag` test, and `:flags` | flag actions (`addflag`) |
| `mailbox` | RFC 5490 | `fileinto :create`, and the `mailboxexists` test | `:create` |
| `copy` | RFC 3894 | `:copy` on `fileinto` / `redirect`: file a copy and keep the message in INBOX too | no |
| `envelope` | RFC 5228 | the `envelope` test: match the SMTP envelope rather than the headers | no |
| `regex` | draft-ietf-sieve-regex, never an RFC | a `:regex` match type | no |
| `enotify` | RFC 5435 | the `notify` action | no — refused |
| `vacation` | RFC 5230 | the `vacation` autoresponder | no — refused |
| `spamtest` | RFC 5235 | the `spamtest` test on the server's spam score (`virustest` is a separate extension) | no |
| `extlists` | RFC 6134 | `:list` matching against external lists | no |

It covers the rules mailctl writes, not what is already in the script: a
rule you made in webmail that uses a disabled extension is left alone, and
`mailctl restore` puts a backup back exactly as it was.

`mailctl test` says where each setting came from — a flag, the env file,
the environment, the config file, or the default — and which of those
sources it read.

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
fetch one. The config file still never holds the password itself — only
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

## Use

```bash
# Check both services and what they support. Changes nothing.
mailctl test

# The same, taking settings from ./.env rather than the environment.
mailctl test --env-file

# Everything each server says about itself, dated: identity, every
# capability, the active script, delimiter, and namespaces. --json prints
# a versioned document, sorted so two probes can be diffed.
mailctl probe
mailctl probe --json > probe-$(date -u +%F).json

# What does this server call its folders, and which does webmail show?
mailctl folders

# Find a message: the newest 20 in a folder, UID first. Takes the same
# criteria flags as add and apply, or a raw IMAP search.
mailctl search
mailctl search --folder Lists/News --from newsletter@example.com
mailctl search --raw 'UNSEEN SINCE 1-Sep-2026' --limit 50

# Find mail like one you have: criteria taken from message 4127 (its
# List-Id, else its From; --derive picks the headers). A criteria flag
# replaces what was taken for its header, and adds any other header.
mailctl search --like 4127
mailctl search --like 4127 --subject Invoice --match all

# Print the filter those criteria make, and save nothing. --json prints it
# as a filter document (see "Filter documents" below).
mailctl search --like 4127 --build-filter
mailctl search --like 4127 --build-filter --json > news.json

# The same listings as data for a script: one JSON document on stdout
# (see "Output for scripts" below), or just the UIDs, one per line.
mailctl search --from newsletter@example.com --json
mailctl search --from newsletter@example.com --uids-only

# Read one by UID. It stays unread, and no attachment is saved.
mailctl view 4127
mailctl view 4127 --headers-only
mailctl view 4127 --raw
mailctl view 4127 --raw > message.eml   # the exact bytes, to keep

# Mark messages read or unread, flagged or not, or with a keyword. What
# each has now and what would change is shown first, then you confirm.
mailctl mark 4127 4128 --read --flag
mailctl mark 4127 --unread --dry-run
mailctl mark 4127 --folder Lists/News --keyword '$Todo'
mailctl mark 4127 --unflag --no-keyword '$Todo' --yes

# Show a folder in webmail, or hide one (it keeps its mail either way).
mailctl subscribe Lists/News
mailctl unsubscribe Lists/Noisy --dry-run

# Make a folder on its own, subscribed so webmail shows it. Its missing
# parents are named; a folder that exists is left as it is.
mailctl create-folder Lists/News --dry-run
mailctl create-folder Lists/Archive --no-subscribe

# See exactly what would change, without changing it.
mailctl add --from newsletter@example.com --fileinto Lists/News --dry-run

# Save the rule: merge it, back up, upload, activate. It filters new mail
# only; the mail already delivered is left alone.
mailctl add --from newsletter@example.com --fileinto Lists/News

# Then act on the mail already there, with the same criteria and actions.
# It previews the messages and asks before it moves any.
mailctl apply --from newsletter@example.com --fileinto Lists/News

# Take the criteria from a message you already have, for the rule and then
# for the mail: its List-Id, else its From, as with 'search --like'. A
# criteria flag replaces what was taken for its header.
mailctl add --like 4127 --fileinto Lists/News --dry-run
mailctl apply --like 4127 --fileinto Lists/News

# Or from a filter document (see "Filter documents" below); '-' reads it
# from standard input.
mailctl add --filter news.json --fileinto Lists/News
mailctl apply --filter news.json --fileinto Lists/News
mailctl search --like 4127 --build-filter --json \
    | mailctl add --filter - --fileinto Lists/News

# Mail already delivered only, with no rule. --create-folder because the
# target may not exist yet.
mailctl apply --subject '[SPAM]' --fileinto Quarantine --create-folder \
    --mark-read

mailctl list
mailctl show
mailctl remove-rule from-newsletter-example-com

# Reorder a rule without restating it; reports what the move would starve.
mailctl move-rule from-newsletter-example-com --first --dry-run
mailctl move-rule from-newsletter-example-com --after keep-boss

# Switch a rule off without deleting it, and back on. Written the way
# Roundcube writes it, so webmail shows it as disabled too.
mailctl disable-rule from-newsletter-example-com --dry-run
mailctl disable-rule from-newsletter-example-com
mailctl enable-rule from-newsletter-example-com

# Save the active script, byte for byte, before you touch anything.
mailctl backup
mailctl backup --output ~/mailctl-before-first-run.sieve

# Put a backup back over the active script: diff, back up, confirm.
mailctl restore ~/mailctl-before-first-run.sieve --dry-run
mailctl restore ~/mailctl-before-first-run.sieve

# Coming from mxfilter: move the old config directory's contents across.
mailctl migrate-config --dry-run
mailctl migrate-config
```

**Before the first run against a real mailbox, work through
[docs/VERIFYING.md][verify].** It is an ordered ladder from commands that
touch nothing to ones that move mail, and it is where the unconfirmed
assumptions below get settled for your account.

## Filter documents

`mailctl search --build-filter --json` prints a filter's criteria as a
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
    ]
  }
}
```

* `version` is `1`. A document with any other version is refused, not
  guessed at.
* `match` (`any` or `all`) and `compare` (`contains`, `is`, or `matches`)
  mean what the flags of the same name mean, and default the same way when
  left out.
* `terms` is one or more header and value pairs; neither may be empty.
* Any other key is refused, so a misspelt one cannot silently fall back to
  a default.

`add --filter FILE` and `apply --filter FILE` read one back as their
criteria, and `--filter -` reads it from standard input — so one document
can be saved as the rule and applied to the mail already there. It takes
the place of the criteria flags, so giving both is refused, and so is
`--filter` with `--like`; the actions still come from the command line.

## Output for scripts

`--json` prints a command's result as one JSON document on stdout, and
nothing else there: whatever the command says on the way goes to stderr.
It is offered where the output is data — `search`, `view`, `folders`,
`rules`, `list` — and on every write command's plan, where it needs
`--dry-run`. `test` is a report for a person and has none. `probe --json`
prints its own versioned document (above), with the same stdout and error
handling.

| Command | Document |
|---------|----------|
| `list` | `{"version", "scripts": [{"name", "active"}]}` |
| `folders` | `{"version", "delimiter", "prefix", "folders": [{"name", "subscribed"}]}` |
| `search` | `{"version", "folder", "more", "messages": [{"uid", "received", "size", "flags", "has_attachments", "from", "subject", "folder"}]}` |
| `view` | `{"version", "message": {"uid", "folder", "size", "flags", "headers": [{"name", "value"}], "body", "body_from_html", "attachments": [{"name", "content_type", "size"}]}}` |
| `rules` | `{"version", "script", "rules": [{"position", "name", "disabled", "stops", "combinator", "tests", "actions", "unmodelled"}], "findings": [{"certainty", "broad", "narrow", "reason"}]}` |
| a write, `--dry-run` | `{"version", "plan": {"command", "changes", ...}}` — what else a plan holds depends on the command |
| any, failing | `{"version", "error": {"message"}}`, one line, the last on stderr |

* `version` is `1`. A key may be added without changing it; one renamed,
  removed, or given a new meaning changes it.
* Values are whole: nothing is clipped, flags are IMAP's own (`\Seen`),
  and text outside printable ASCII is escaped as `\uXXXX`.
* `received` is ISO 8601 in local time, with no offset, because the
  server's date reaches mailctl without one.
* A plan's `changes` is `false` where the command would do nothing; its
  `diff`, on a script change, is then `null`.
* `search --build-filter --json` prints a filter document instead (below),
  and `search --uids-only` prints the matching UIDs, one per line.

## Safety

* `--dry-run` changes nothing, on every mutating command. It prints whatever
  that command would have changed: the Sieve diff for `add`,
  `remove-rule`, `disable-rule`, and `enable-rule`; the list of matching
  messages for `apply`; the file that would have been written for
  `backup`.
* `add` **never touches mail already delivered**; `apply` is the only
  command that does. After saving a rule, `add` says so and points at
  `apply`.
* `--like`, on `search`, `add`, and `apply`, **shows you the message
  first** — Date, From, To, Subject, and List-Id when it has one — before
  anything is derived from it, and so before a rule is written or mail is
  moved. The UID is something you read out of webmail by hand, and
  the derived criteria look equally plausible whichever message produced
  them, so the headers are the only thing that catches a mistyped digit
  before mail starts moving.
* `search` and `view` **never mark mail read**. The folder is opened
  read-only, and the message is fetched in the form that leaves its read
  flag alone, so either guard alone would be enough.
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
* **Mail content is treated as hostile on the way to your terminal.** A
  sender controls every header, the body, and the attachment names, and
  escape sequences in them can recolour your terminal, retitle it, or plant
  a link whose text lies about where it goes. So `search` and `view` —
  `--raw` on a terminal included — print every control character as a
  visible `\xNN` escape instead of sending it to the terminal, and headers
  are kept to one line so a decoded line break cannot forge another
  header. Unicode direction overrides and isolates, which can make
  `invoice_fdp.exe` read as `invoice_exe.pdf`, print as a visible
  `\u202e`-style escape the same way. Everything printable, tabs and line
  breaks included, comes through as it is. The headers `--like` and
  `apply` show before they act get the same treatment.
* `view --raw` **into a file or a pipe writes the message exactly as the
  server holds it**, byte for byte, with nothing escaped or re-encoded — so
  `mailctl view 4127 --raw > message.eml` saves a copy any mail program
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
  upload, and the path is printed. `mailctl backup` takes the same copy on
  demand, without changing anything on the server.
* Backups land in `$XDG_CONFIG_HOME/mailctl/backups` (usually
  `~/.config/mailctl/backups`) — beside your `config.toml`, one file per
  backup, named `<script>-<UTC timestamp>.sieve`. XDG would call a backup
  *state* rather than config; keeping it here is a deliberate departure from
  that, not something XDG endorses, because a backup you cannot find is not a
  backup. `--backup-dir`, `MAILCTL_BACKUP_DIR`, and `backup_dir` in
  `config.toml` move it, with `~` and `$VAR` expanded in each. The file is
  written mode `0600` in a directory created `0700`: a Sieve script is not a
  password, but it does say who you correspond with and how you sort it.
* **`mailctl restore FILE` puts a backup back.** The backup is the server's
  exact bytes — no banner lines, nothing reformatted — and restore uploads
  them exactly, over the active script — or over the one `--script NAME`
  names, which stays inactive unless `--activate` is given. No other stored
  script is touched. It shows the raw diff against what the server has now,
  backs the current script up first, lets the server validate the file, and
  asks before it replaces anything. It is the one command that **replaces**
  rather than merges: a rule added since the backup was taken is removed, and
  the diff shows it. It works even over a script mailctl cannot parse
  ([ADR 0005][adr5]). An empty FILE would remove every rule, so it is refused
  unless `--allow-empty` is given. FILE is read and checked before mailctl
  connects, and `~` and `$VAR` in it are expanded. If the account has no
  active script, restore refuses rather than guess, and `--script NAME` is
  the way back: NAME is restored and made active.
* Rules are merged into the parsed existing script, never appended blindly,
  so other rules survive. If the existing script cannot be parsed, mailctl
  stops rather than overwrite it.
* `checkscript` runs on the server before `putscript`.
* `apply` **always previews and always confirms** before it
  touches anything — `--dry-run` shortens that path, it is not what creates
  it. `--yes` skips the prompts. Deletion says in as many words that it
  cannot be undone; a move says it can be reversed.
* `--max-messages` (default 500) refuses the whole batch when more matches
  than that come back. It never processes a partial set: silent truncation
  reads as "it handled everything" when it did not. Raising it is safe:
  mailctl talks to the server in batches of 250 regardless, so a large
  pass never becomes one oversized IMAP command. If a batch fails part-way,
  mailctl says how many messages were fully handled. Re-running the same
  command picks up the rest — except on a server without `MOVE`, where the
  failed batch may already have been copied and a re-run copies it again;
  mailctl says so, and names the folder to check.
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
  nothing; `mailctl unsubscribe` hides a folder that already exists. If
  the subscription fails, the folder is **not** torn back down: it exists
  and mail filed there will arrive, so mailctl warns and tells you to run
  `mailctl subscribe` on it.
* On a server that advertises the Sieve `mailbox` extension, the rule says
  `fileinto :create` **as well**, so Sieve recreates the folder if it is
  later deleted. mailctl still creates and subscribes the folder over IMAP
  itself, because Sieve only creates it when the first message arrives,
  when mailctl is not running to subscribe to it. With `--no-imap`, Sieve
  is the only thing that can create the folder, and mailctl says it may
  not appear in webmail until you run `mailctl subscribe` on it.
* The folder is **announced when the change is shown and created only when
  it is applied** — for `add`, once the server has
  accepted the new script and just before it is stored; for `apply`, after
  you confirm the move. A
  dry run, an abort, or a rejected script leaves no stray folder, and an
  `apply` that matches nothing creates nothing and says so.

## MXRoute specifics

MXRoute documents very little of its Sieve surface, so these are grouped by
how much is actually known. Nothing here is promoted a tier to make the
documentation read better.

### Confirmed

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

### Likely, not confirmed

* The folder delimiter is probably `.` (Maildir++, so `INBOX.Lists.News`),
  and the spam folder is probably `INBOX.spam` — **lowercase**, not
  `INBOX.Junk`. The only evidence either way is an incidental filesystem path
  in an MXRoute blog post, not a documented statement, and that post's
  subject is that the "deliver spam to spam folder" option was **removed**,
  so the folder may not exist on your account at all.
* Because of that, neither is assumed. The delimiter is **detected at
  runtime** from the server's folder list, and folder names are matched
  against that list — type `Lists/News` or `INBOX.Lists.News` and the
  server's spelling is used for both the Sieve rule and the move. `mailctl
  folders` is the authority for your account. The one exception is
  `--no-imap`, which has no folder list to consult and falls back to `.` (or
  `--delimiter`), and warns that it did.
* Folder names are **case-sensitive**, except `INBOX` itself (RFC 3501), so
  `INBOX.Lists` and `INBOX.lists` are two folders. When the folder you name
  does not exist but one differing only in case does, mailctl warns and
  names both — and with `--create-folder`, says a second folder will be
  created beside it.

### Unconfirmed — do not read these as MXRoute facts

* **The ManageSieve port and TLS mode.** MXRoute documents neither. The
  defaults here (4190, STARTTLS) are the IANA/RFC 5804 registered port and
  the Dovecot default — that is why they are the defaults, and it is not a
  verified MXRoute setting. `--sieve-port` and `--sieve-tls` exist because of
  that, and a connection failure says so rather than implying you mistyped.
* **Whether `notify` and `vacation` are disabled.** No MXRoute source says
  either way. mailctl refuses both as **our own conservative default**, not
  as a documented MXRoute limitation, and its error says so and points at the
  control panel. `mailctl test` prints what your server actually advertises.
* **ManageSieve script-size, script-count, and rate limits.** Unknown; there
  is no documented ceiling to design against.

The active script's name is read from `LISTSCRIPTS` and written back to, and
is never guessed — the webmail's script name is server-side config, and
MXRoute is mid-migration on both its panel and Dovecot. `--script` overrides
it. With nothing active, a script called `mxfilter` — what the tool created
before it was renamed — is still recognised as its own and reused rather
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

* `mailctl test` reports the Sieve and IMAP side, not the whole path mail
  takes.
* `mailctl rules` works out which rules can never fire from their order in
  the Sieve script. A message dropped before Sieve is outside that analysis.
* A filter set in the panel is invisible here. If a rule never seems to fire,
  the mail may never have reached Sieve: check the panel's filters, if your
  account has them.

## `--compare` tests the whole header value

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

## Known limits

* `is` and `matches` cannot be expressed in IMAP SEARCH, which only does
  case-insensitive substring matching. So the existing-mail pass searches
  deliberately too broadly and then re-checks every candidate's real headers
  in Python against the Sieve semantics above. Correct, but it fetches the
  header block of every candidate the broad search returned, not just the
  ones that end up matching.
* Merging round-trips the script through a parser. Rules that have no
  `# Filter:` name comment are renamed `Unnamed rule N`, and formatting is
  normalized. The diff shows this before anything is uploaded.
* Nothing here evaluates the Sieve script you already have. mailctl can
  apply criteria you give it to old mail; it cannot tell you which of your
  existing rules would have caught a message.

[adr5]: adr/0005-restore-may-replace-an-unparseable-script.md
[verify]: docs/VERIFYING.md
