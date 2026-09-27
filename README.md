# mxfilter

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
(`set -a; . ./.env; set +a`) or point mxfilter at it with `--env-file`. Or
copy `config.toml.example` to `$XDG_CONFIG_HOME/mxfilter/config.toml` — flat
top-level keys, every one explained in the file:

```toml
host = "mail.example-server.mxrouteXX.com"
user = "you@yourdomain.com"
password_cmd = "pass show email/you@yourdomain.com"
default_folder = "Lists"
```

Resolution order is **CLI flag > env file > environment > config file >
default**.

`--env-file PATH` reads the `MXROUTE_*` lines of a dotenv-style file; given
bare, `--env-file` means `.env` in the current directory. The file is read,
not exported, and it outranks the environment because it was named for this
run. The format is the plain one: `KEY=VALUE`, an optional leading
`export`, blank lines and `#` comment lines skipped, and one pair of
matching single or double quotes stripped. Nothing is interpolated, a `#`
after a value is part of the value, and a value cannot span lines — quote a
value whose leading or trailing spaces matter. Keys not starting `MXROUTE_`
are ignored, so a `.env` shared with another tool is fine. A line mxfilter
cannot read is reported by line number, never quoted. Because it takes an
optional value, put `--env-file` after any positional argument, or write
`--env-file=PATH`.

`mxfilter test` says where each setting came from — a flag, the env file,
the environment, the config file, or the default — and which of those
sources it read.

The password has more sources than the other settings, so it has its own
ladder — the same shape, highest first:

| # | Source |
|---|--------|
| 1 | `--password-file`, `--password-cmd`, or `--password` (mutually exclusive) |
| 2 | `MXROUTE_PASSWORD_FILE`, then `_CMD`, then `MXROUTE_PASSWORD`, in the `--env-file` file |
| 3 | `MXROUTE_PASSWORD_FILE` |
| 4 | `MXROUTE_PASSWORD_CMD` |
| 5 | `MXROUTE_PASSWORD` |
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

An env file that sets `MXROUTE_PASSWORD` is held to the same mode rule as a
password file: `0600` or `0400`, or it is refused with the `chmod` that fixes
it. One that only names a password file or command is not.

`--password VALUE` exists and is the **least safe** option: the value is
visible in the process list to every user on the machine and your shell
saves it to history. mxfilter warns when you use it.

## Use

```bash
# Check both services and what they support. Changes nothing.
mxfilter test

# The same, taking settings from ./.env rather than the environment.
mxfilter test --env-file

# What does this server call its folders, and which does webmail show?
mxfilter folders

# Find a message: the newest 20 in a folder, UID first. Takes the same
# criteria flags as add and apply, or a raw IMAP search.
mxfilter messages
mxfilter messages --folder Lists/News --from newsletter@example.com
mxfilter messages --search 'UNSEEN SINCE 1-Sep-2026' --limit 50

# Read one by UID. It stays unread, and no attachment is saved.
mxfilter view 4127
mxfilter view 4127 --headers-only
mxfilter view 4127 --raw
mxfilter view 4127 --raw > message.eml   # the exact bytes, to keep

# Show a folder in webmail, or hide one (it keeps its mail either way).
mxfilter subscribe Lists/News
mxfilter unsubscribe Lists/Noisy --dry-run

# See exactly what would change, without changing it.
mxfilter add --from newsletter@example.com --fileinto Lists/News --dry-run

# Do it: merge the rule, upload, activate, then file existing mail.
mxfilter add --from newsletter@example.com --fileinto Lists/News

# Rule only; leave delivered mail alone.
mxfilter add --list-id python-list.python.org --fileinto Lists/Python \
    --no-apply

# Learn the criteria from a message you already have.
mxfilter from-message --folder INBOX --search 'FROM newsletter@example.com' \
    --fileinto Lists/News --dry-run

# Existing mail only; no Sieve change. --create-folder because the target
# may not exist yet.
mxfilter apply --subject '[SPAM]' --fileinto Quarantine --create-folder \
    --mark-read

mxfilter list
mxfilter show
mxfilter remove-rule from-newsletter-example-com

# Reorder a rule without restating it; reports what the move would starve.
mxfilter move-rule from-newsletter-example-com --first --dry-run
mxfilter move-rule from-newsletter-example-com --after keep-boss

# Save the active script, byte for byte, before you touch anything.
mxfilter backup
mxfilter backup --output ~/mxfilter-before-first-run.sieve

# Put a backup back over the active script: diff, back up, confirm.
mxfilter restore ~/mxfilter-before-first-run.sieve --dry-run
mxfilter restore ~/mxfilter-before-first-run.sieve
```

**Before the first run against a real mailbox, work through
[docs/VERIFYING.md][verify].** It is an ordered ladder from commands that
touch nothing to ones that move mail, and it is where the unconfirmed
assumptions below get settled for your account.

## Safety

* `--dry-run` changes nothing, on every mutating command. It prints whatever
  that command would have changed: the Sieve diff for `add`, `from-message`,
  and `remove-rule`; the list of matching messages for `add`, `from-message`,
  and `apply`; the file that would have been written for `backup`.
* `from-message` **shows you the message first** — Date, From, To, Subject,
  and List-Id when it has one — before it derives anything and before it
  writes a rule. The UID is something you read out of webmail by hand, and
  the derived criteria look equally plausible whichever message produced
  them, so the headers are the only thing that catches a mistyped digit
  before mail starts moving.
* `messages` and `view` **never mark mail read**. The folder is opened
  read-only, and the message is fetched in the form that leaves its read
  flag alone, so either guard alone would be enough.
* **Mail content is treated as hostile on the way to your terminal.** A
  sender controls every header, the body, and the attachment names, and
  escape sequences in them can recolour your terminal, retitle it, or plant
  a link whose text lies about where it goes. So `messages` and `view` —
  `--raw` on a terminal included — print every control character as a
  visible `\xNN` escape instead of sending it to the terminal, and headers
  are kept to one line so a decoded line break cannot forge another
  header. Unicode direction overrides and isolates, which can make
  `invoice_fdp.exe` read as `invoice_exe.pdf`, print as a visible
  `\u202e`-style escape the same way. Everything printable, tabs and line
  breaks included, comes through as it is. The headers `from-message` and
  `apply` show before they act get the same treatment.
* `view --raw` **into a file or a pipe writes the message exactly as the
  server holds it**, byte for byte, with nothing escaped or re-encoded — so
  `mxfilter view 4127 --raw > message.eml` saves a copy any mail program
  can open. Only on a terminal is it escaped as above.
* `view` shows the message's plain-text part. A message with only HTML is
  shown as a rough text conversion, and says so above the body; `--raw`
  shows the original. Attachments are listed by name, type, and size, and
  never written anywhere.
* The Sieve diff is shown with **both sides in mxfilter's own formatting**.
  A merge re-renders the whole script, so a diff against the server's raw
  copy would report every re-indented line as a change — on a hand-written
  script that is most of the file, and it reads exactly like something
  having gone wrong. The reformat is real, so mxfilter says so on a line
  above the diff, and only while the server's copy is still in some other
  formatting. What gets uploaded and what gets backed up are unaffected.
* The current active script is backed up to a timestamped file before any
  upload, and the path is printed. `mxfilter backup` takes the same copy on
  demand, without changing anything on the server.
* Backups land in `$XDG_CONFIG_HOME/mxfilter/backups` (usually
  `~/.config/mxfilter/backups`) — beside your `config.toml`, one file per
  backup, named `<script>-<UTC timestamp>.sieve`. XDG would call a backup
  *state* rather than config; keeping it here is a deliberate departure from
  that, not something XDG endorses, because a backup you cannot find is not a
  backup. `--backup-dir` and `MXROUTE_BACKUP_DIR` move it. The file is written
  mode `0600` in a directory created `0700`: a Sieve script is not a password,
  but it does say who you correspond with and how you sort it.
* **`mxfilter restore FILE` puts a backup back.** The backup is the server's
  exact bytes — no banner lines, nothing reformatted — and restore uploads
  them exactly, over the active script only. It shows the raw diff against
  what the server has now, backs the current script up first, lets the
  server validate the file, and asks before it replaces anything. It is the
  one command that **replaces** rather than merges: a rule added since the
  backup was taken is removed, and the diff shows it. It works even over a
  script mxfilter cannot parse ([ADR 0005][adr5]).
* Rules are merged into the parsed existing script, never appended blindly,
  so other rules survive. If the existing script cannot be parsed, mxfilter
  stops rather than overwrite it.
* `checkscript` runs on the server before `putscript`.
* The existing-mail pass **always previews and always confirms** before it
  touches anything — `--dry-run` shortens that path, it is not what creates
  it. `--yes` skips the prompts. Deletion says in as many words that it
  cannot be undone; a move says it can be reversed.
* `--max-messages` (default 500) refuses the whole batch when more matches
  than that come back. It never processes a partial set: silent truncation
  reads as "it handled everything" when it did not. Raising it is safe:
  mxfilter talks to the server in batches of 250 regardless, so a large
  pass never becomes one oversized IMAP command. If a batch fails part-way,
  mxfilter says how many messages were fully handled. Re-running the same
  command picks up the rest — except on a server without `MOVE`, where the
  failed batch may already have been copied and a re-run copies it again;
  mxfilter says so, and names the folder to check.
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
  sidebar — and mxfilter says so on the line where it creates the folder,
  because an invisible folder nobody was told about is the bug, not the
  feature. It is refused without `--create-folder`, where it would do
  nothing; `mxfilter unsubscribe` hides a folder that already exists. If
  the subscription fails, the folder is **not** torn back down: it exists
  and mail filed there will arrive, so mxfilter warns and tells you to run
  `mxfilter subscribe` on it.
* The folder is **announced when the change is shown and created only when
  it is applied** — for `add` and `from-message`, once the server has
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
  on 2024-03-21, saying their own forwarders "are designed to properly handle
  SRS" where a Sieve redirect does not. `--redirect` fails with a pointer to
  the panel's Forwarders (or the `mxroute_forwarder` Terraform resource).
* The username is the **full email address**, on both IMAP and ManageSieve.
* The hostname is **per-account** — the same as your primary MX record, shown
  on the panel's Email Clients page. There is deliberately no default.
* IMAP is **993** (implicit TLS) or **143** (STARTTLS). mxfilter picks the
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
  against that list case-insensitively — type `Lists/News` or
  `INBOX.Lists.News` and whichever spelling the server reports is used for
  both the Sieve rule and the move. `mxfilter folders` is the authority for
  your account. The one exception is `--no-imap`, which has no folder list to
  consult and falls back to `.` (or `--delimiter`), and warns that it did.

### Unconfirmed — do not read these as MXRoute facts

* **The ManageSieve port and TLS mode.** MXRoute documents neither. The
  defaults here (4190, STARTTLS) are the IANA/RFC 5804 registered port and
  the Dovecot default — that is why they are the defaults, and it is not a
  verified MXRoute setting. `--sieve-port` and `--sieve-tls` exist because of
  that, and a connection failure says so rather than implying you mistyped.
* **Whether `notify` and `vacation` are disabled.** No MXRoute source says
  either way. mxfilter refuses both as **our own conservative default**, not
  as a documented MXRoute limitation, and its error says so and points at the
  control panel. `mxfilter test` prints what your server actually advertises.
* **ManageSieve script-size, script-count, and rate limits.** Unknown; there
  is no documented ceiling to design against.

The active script's name is read from `LISTSCRIPTS` and written back to, and
is never guessed — the webmail's script name is server-side config, and
MXRoute is mid-migration on both its panel and Dovecot. `--script` overrides
it; the name `mxfilter` is used only when the account has no scripts at all.

The server runs one script, and editing a different one does not change
which. `--script NAME` on a script that is not the active one stores the
change and leaves NAME inactive, and mxfilter says so before it uploads;
`--activate` makes NAME the active script as well. When the account has no
active script at all, the script mxfilter writes is activated, since
otherwise nothing would run it.

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
* Nothing here evaluates the Sieve script you already have. mxfilter can
  apply criteria you give it to old mail; it cannot tell you which of your
  existing rules would have caught a message.

[adr5]: adr/0005-restore-may-replace-an-unparseable-script.md
[verify]: docs/VERIFYING.md
