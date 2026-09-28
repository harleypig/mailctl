## Unreleased

Entries accumulate here under the usual headings — `BREAKING CHANGES:`,
`FEATURES:`, `ENHANCEMENTS:`, `BUG FIXES:`, `NOTES:` — and move under a
`## X.Y.Z` heading when a tag is cut.

NOTES:

* **The version is written only in `pyproject.toml`** ([#106]).
  `mailctl --version` now reads it from the installed package instead of
  a second copy in the source, so the two can no longer disagree. A
  release bumps one file. After a bump, an editable install shows the new
  version only once it is reinstalled (`uv pip install -e .`). Run from a
  source tree with nothing installed, the version reads `0+unknown`.

## 0.7.0

FEATURES:

* **A `provider` setting names the mail host** ([#92], [ADR 0006]). It is
  `--provider`, `MAILCTL_PROVIDER` (env file or environment), or `provider`
  in `config.toml`, and resolves like every other setting. It defaults to
  `mxroute`, the only provider today, so nothing needs setting. An unknown
  name is refused before anything connects, naming the known ones.
  `mailctl test` now shows the provider and where it came from, as a
  `Provider:` line at the top of its settings.

BUG FIXES:

* **A non-ASCII search value no longer crashes the existing-mail pass**
  ([#89]). `--subject café`, a non-ASCII `--from` address, or a non-ASCII
  `--list-id` stopped the IMAP half with a Python traceback. They now
  search in UTF-8, whether alone or combined with `--match any`. A
  message whose header carries raw UTF-8, which is how a non-ASCII address
  arrives, is now also recognised when mailctl double-checks the server's
  matches. A value that is not valid text, or a non-ASCII raw `--search`
  expression, is refused with a message saying what to do instead. A
  search that is plain ASCII is sent exactly as before.
* **A backup is the server's exact bytes again** ([#90]). sievelib's
  GETSCRIPT reader turned CRLF line endings into LF and dropped the final
  line ending, so `mailctl backup`, the backup taken before every upload,
  and `mailctl restore`'s source were not byte-identical to the script on
  the server. mailctl now reads the script by its declared length. As a
  side effect, the note that the server's script "is not in mailctl's
  formatting" stops appearing once mailctl has uploaded the script: the
  dropped final newline had made mailctl's own output look reformatted on
  every later run.

NOTES:

* **ManageSieve is now a layer-1 component** ([#92], [ADR 0006]). The
  ManageSieve session and the Sieve script handling moved from
  `mailctl/sieve.py` into `mailctl/components/managesieve/`, which knows
  the protocol and nothing about MXroute; MXroute's refusal of `redirect`,
  the Roundcube rule-name dialect, and its connection advice sit in
  `mailctl/providers/mxroute/` until the provider interface lands. The
  wrapper also keeps the whole CAPABILITY response (`MAXREDIRECTS`,
  `OWNER`, `UNAUTHENTICATE`, which sievelib drops), makes the read timeout
  a parameter, and makes sievelib's password-printing debug mode
  impossible to enable. No command's behaviour changes.
* **IMAP is now a layer-1 component** ([#92], [ADR 0006]). The IMAP
  session and the folder and message handling moved from `mailctl/imap.py`
  into `mailctl/components/imap/`, which knows the protocol and nothing
  about MXroute. MXroute's login advice and the mapping from mailctl's
  settings to a session are in `mailctl/providers/mxroute/` until the
  provider interface lands. The component can tell which server software
  it is talking to from the IMAP `ID` response; nothing depends on the
  answer yet. No command's behaviour changes.
* **The engine now talks to a provider, not to the protocols** ([#92],
  [ADR 0006]). A provider translates both ways. mailctl's own model (a
  rule's criteria and actions, folders, messages) goes out in the host's
  terms, Sieve over ManageSieve for MXroute, and the host's answers come
  back in that model. What a host can do is declared as data, and a rule
  it cannot express is refused before anything connects. MXroute's
  policies (the `redirect` refusal, the Roundcube rule names, the login
  and TLS advice) live in the `mxroute` provider, which replaces the
  transitional modules. A test drives a second, fake provider through the
  engine to show that adding a host needs no engine change. No command's
  behaviour changes, apart from the new `Provider:` line in `mailctl
  test`.
* **The runtime dependencies are bounded on their next major** ([#91]).
  `IMAPClient>=4.1,<5` (the suite passes on 4.1) and `sievelib>=1.5.0,<2`,
  so a new major of either no longer lands on a fresh install unreviewed;
  raising a bound is a deliberate, tested change.
* **Each provider keeps a dated record of its host** ([#92],
  [ADR 0006]). What MXroute documents, what a probe saw on one server,
  and what is still unknown now live in
  `mailctl/providers/mxroute/RECORD.md`, along with the capabilities
  mailctl does not use yet and the changes MXroute has announced (the
  in-house webmail replacing Roundcube, Dovecot 2.4). The record says when
  it was last refreshed. A record is refreshed when a provider is built,
  when its host announces a change, before an old observation is relied
  on for a new feature, and at least quarterly. A new provider starts with
  its record. No command's behaviour changes.

## 0.6.0

FEATURES:

* **`disabled_extensions`: tell mailctl not to use a Sieve extension**
  ([#82]). Set it in `config.toml` (a list), as
  `MAILCTL_DISABLED_EXTENSIONS` (comma-separated, env file or environment),
  or per run with `--disable-extension NAME` (repeatable); the highest source
  replaces the lower ones, as every setting does. A disabled extension counts
  as not advertised, whatever the server says: with `mailbox` off the rule
  says plain `fileinto` and the folder is created over IMAP; a rule needing
  `fileinto` or `imap4flags` while it is off is refused, naming the setting.
  Disabling one the server lacks anyway changes nothing, and an unknown name
  is an error naming it.
* **`mailctl test` shows one table of Sieve extensions** ([#82]), replacing
  the raw capability line and the checklist below it. A row per extension
  the server lists or mailctl knows, in alphabetical order: `available` or
  `unavailable`, then — only for an available one — `enabled` or `disabled
  (<source>)`. A `*` marks the extensions mailctl's own rules can need,
  derived from what mailctl actually writes rather than a hand-kept list.

## 0.5.0

BREAKING CHANGES:

* **`mxfilter` is now `mailctl`** ([#45]). The command is `mailctl`
  (`python -m mailctl`), the distribution and the Python package are
  `mailctl`, and the exception every actionable failure raises is
  `MailctlError` rather than `MxFilterError`. Reinstall to get the new
  command. An existing `mxfilter` install is a different distribution and
  stays until removed (`uv tool uninstall mxfilter`, `pipx uninstall
  mxfilter`, or `uv pip uninstall mxfilter` in a venv).

  **The config directory and every setting name moved with it, as a clean
  break: the old names are not read.** Every command warns loudly while the
  old directory exists and the new one does not, and names any old variable
  still set without its new one — by name only, never its value. Every old
  name and its replacement:

  | Old | New |
  |-----|-----|
  | `$XDG_CONFIG_HOME/mxfilter/` (`config.toml`, `backups/`) | `$XDG_CONFIG_HOME/mailctl/` |
  | `MXROUTE_HOST` | `MAILCTL_HOST` |
  | `MXROUTE_USER` | `MAILCTL_USER` |
  | `MXROUTE_PASSWORD` | `MAILCTL_PASSWORD` |
  | `MXROUTE_PASSWORD_FILE` | `MAILCTL_PASSWORD_FILE` |
  | `MXROUTE_PASSWORD_CMD` | `MAILCTL_PASSWORD_CMD` |
  | `MXROUTE_IMAP_HOST` | `MAILCTL_IMAP_HOST` |
  | `MXROUTE_IMAP_PORT` | `MAILCTL_IMAP_PORT` |
  | `MXROUTE_SIEVE_PORT` | `MAILCTL_SIEVE_PORT` |
  | `MXROUTE_SIEVE_TLS` | `MAILCTL_SIEVE_TLS` |
  | `MXROUTE_BACKUP_DIR` | `MAILCTL_BACKUP_DIR` |
  | `MXROUTE_SOURCE_FOLDER` | `MAILCTL_SOURCE_FOLDER` |
  | `MXFILTER_LIVE` (live test gate) | `MAILCTL_LIVE` |
  | `MXFILTER_UPDATE_SNAPSHOTS` (snapshot regeneration) | `MAILCTL_UPDATE_SNAPSHOTS` |
  | default Sieve script `mxfilter` | `mailctl`, for an account with no script of ours |

  The same `MXROUTE_*` → `MAILCTL_*` renames apply to the keys of an
  `--env-file` file. An existing `mxfilter` script is still recognised as
  mailctl's own: with nothing active it is reused, never duplicated, and an
  active one stays the one edited. `sieve.MXROUTE_FORBIDDEN_ACTIONS` keeps
  its name; it is a fact about MXroute, not a setting.

  **To upgrade:** run `mailctl migrate-config` (new — `--dry-run` to preview)
  to move the old directory's contents across, then rename each `MXROUTE_*`
  variable in your shell config and env files to `MAILCTL_*`, including any
  that hold a path inside the old directory.

FEATURES:

* **`mailctl migrate-config`** moves everything in `$XDG_CONFIG_HOME/mxfilter/`
  into `$XDG_CONFIG_HOME/mailctl/` ([#45]). It lists what would move with each
  file's mode, honours `--dry-run` and `--yes`, and moves by rename, so a
  `0600` backup stays `0600` and a directory keeps `0700`. A new directory
  that already exists is merged into; anything that would be overwritten
  stops the whole move before any file is touched. A `password_file` or
  `backup_dir` in `config.toml` that named the old directory is pointed at
  the new one, and the old directory is removed once empty.

## 0.4.0

Released 2026-09-27.

ENHANCEMENTS:

* **`view --raw` into a file or a pipe is byte-exact.** `mxfilter view N
  --raw > msg.eml` now saves the message exactly as the server holds it,
  8-bit and non-UTF-8 mail included; it used to be decoded as UTF-8, with
  anything undecodable replaced, and escaped. On a terminal, `--raw` is
  escaped as before.

* **`mxfilter restore --script NAME` restores over a named script** rather
  than always the active one ([#52]). It follows the same activation rule
  as every other change, and takes `--activate` too.
* **`mxfilter restore` refuses an empty FILE** unless `--allow-empty` is
  given ([#52]). Uploading one removes every rule, and an empty file is more
  often a truncated copy or the wrong path than a deliberate wipe.
* **mxfilter says it sees the Sieve stage only** ([#30]). Mail may first
  pass a DirectAdmin panel filter — an Exim filter that runs before Sieve
  and can drop a message before any Sieve rule sees it. mxfilter logs in as
  a mailbox and cannot read or change that filter. `mxfilter test` now says
  so, and the README's *A filtering stage mxfilter cannot see* explains
  what is known and what is not: whether accounts set up since MXRoute
  began phasing out DirectAdmin still have one is unconfirmed.

BUG FIXES:

* **Unicode direction overrides no longer disguise what a message says.**
  A sender could put an override or isolate (U+202A–U+202E,
  U+2066–U+2069) in a Subject, a From, or an attachment name to make
  `invoice_fdp.exe` display as `invoice_exe.pdf`. `messages`, `view`, and
  every other place that escapes control characters now print these as a
  visible `\u202e`-style escape too. The direction marks U+200E, U+200F,
  and U+061C still pass, since they cannot reorder text and right-to-left
  mail uses them.

* **`--script NAME` no longer switches the active script as a side
  effect** ([#53]). `add`, `from-message`, `remove-rule`, and `move-rule`
  used to activate whatever script they edited, so editing a spare script
  silently changed the one Sieve runs. Now a script is activated only when
  it already was the active one, when the account has no active script, or
  when `--activate` (new on those commands) asks for it. When a change goes
  to a script that stays inactive, mxfilter says so before uploading.
* **`mxfilter restore` on an account with no active script** now names
  `--script` as the way out instead of dead-ending ([#54]). With
  `--script NAME` the backup is restored and NAME activated — the recovery
  case.
* **`mxfilter restore` reads and checks FILE before connecting** ([#54]), so
  a mistyped path or an empty file is reported without a login first.
* **`mxfilter restore` expands `$VAR` / `${VAR}` in FILE** as well as `~`
  ([#54]), the same as the password-file path.
* **`backup_dir` expands `~` and `$VAR` / `${VAR}`** ([#50]) — in
  `config.toml`, in `MXROUTE_BACKUP_DIR`, and in `--backup-dir`. It was taken
  literally, so `backup_dir = "~/backups"` created a directory named `~`.
* **`source_folder` in `config.toml` takes effect** ([#63]). `--folder`
  carried a built-in `INBOX` default that outranked the config file, so the
  key was read and never used. `--folder` on `add`, `apply`,
  `from-message`, `messages`, and `view` now falls back to
  `MXROUTE_SOURCE_FOLDER` (new), then `source_folder`, then `INBOX`, and
  `mxfilter test` shows which one is in play.
* **`mxfilter test` reports the password as it turned out** ([#61]). It
  printed `set (via file)` before reading the password, so a file then
  refused for its mode was shown as set directly above the refusal. The
  password is now read first: an unusable one shows as `not usable`, with
  the reason below the settings, and an answered prompt shows as `set`
  rather than the `unset` it was before asking.
* **Folder names match exactly, except `INBOX`** ([#56]). Checking whether
  a folder exists, resolving the name you typed, and checking subscriptions
  all ignored case, so on a case-sensitive server `INBOX.lists` counted as
  the existing `INBOX.Lists`. They now follow RFC 3501: `INBOX` in any
  case, every other name exactly. When the folder you name is missing but
  one differing only in case exists, mxfilter warns and names both — so
  `--create-folder` no longer makes a second, differently cased folder
  without saying so — and a subscribe or folder-open error names it too.
* **`--create-folder` subscribes to the folder on the usual path too**
  ([#40]). When the server advertises the Sieve `mailbox` extension — as
  MXroute does — the rule used `fileinto :create` and nothing else, so the
  folder appeared only when the first message arrived, and nothing
  subscribed to it. Now mxfilter also creates the folder over IMAP and
  subscribes to it when the change is applied (`--no-subscribe` still
  skips the subscription), and keeps `:create` in the rule so Sieve
  recreates the folder if it is deleted later. With `--no-imap`, nothing
  changes. The plan now also says whether a folder it will create is to be
  subscribed to.

## 0.3.0

Released 2026-09-27.

FEATURES:

* **`mxfilter messages` lists the mail in a folder.** The newest first,
  20 by default (`--limit N`), with the UID first on each line, then when it
  arrived, its size, whether it is unread, flagged, replied to, deleted, or
  has an attachment, the sender, and the subject. `--folder` picks the
  folder; the criteria flags `add` and `apply` take (`--from`, `--subject`,
  `--match`, `--compare`, …) filter it the same way, or `--search` takes a
  raw IMAP search instead.

* **`mxfilter view UID` reads one message.** Its identifying headers, the
  plain-text body, and its attachments by name, type, and size.
  `--headers-only` prints every header; `--raw` prints the full source. A
  message with only HTML is shown as a rough text conversion, flagged as
  one. Viewing never marks a message read and never saves an attachment.
  Both commands, and the engine calls behind them, are available to any
  front-end, not just the command line.

* **Mail content cannot drive the terminal.** Control characters and escape
  sequences in headers, bodies, and attachment names print as visible
  `\xNN` escapes in `messages` and `view`, `--raw` included.

BUG FIXES:

* **`from-message` and `apply` escape control characters in the message
  headers they show**, as `messages` and `view` do. They used to print them
  raw, so a sender could put escape sequences on your terminal.

* **The Sieve diff, `show`, and `rules` escape control characters too.**
  A rule derived from a message carries that message's text into the
  diff, and a stored script can hold the same bytes, so both were a way
  for escape sequences to reach the terminal.

* **One message with a malformed encoded-word header no longer stops the
  whole command.** A Subject such as `=?utf-8?b?G=?=` made `apply` and
  `messages` fail outright; the header is now shown as its raw text.

NOTES:

* **`config.toml.example` documents the config file.** The TOML
  counterpart of `.env.example`: every key that takes effect, its
  default, and the same caveats — the password is never read from it, only
  `password_file` or `password_cmd`, and `backup_dir` does not expand `~`
  (#50).

## 0.2.0

Released 2026-09-27.

FEATURES:

* **`--env-file [PATH]` reads settings from a `.env` file.** Bare, it means
  `.env` in the current directory. Only `MXROUTE_*` lines are read, in the
  plain dotenv form — `KEY=VALUE`, optional `export`, `#` comments, one pair
  of quotes stripped, nothing interpolated. The file ranks between the
  flags and the environment, for the password as well as everything else,
  and the process environment is never modified. A file that sets
  `MXROUTE_PASSWORD` is refused unless it is mode `0600` or `0400`, and a
  line mxfilter cannot read is reported by number, never quoted.

* **`mxfilter test` says where each setting came from.** Host, user,
  password, IMAP, and Sieve each name their source — a flag, the env file,
  the environment, the config file, or the default — and a `Sources:` line
  lists what was read. The password line names its source and nothing
  else. The same record is on the resolved config for any other front-end.

* **`mxfilter move-rule NAME` reorders a rule without restating it** (#36).
  `--first`, `--last`, `--before OTHER`, or `--after OTHER`; only the
  position changes. The move is judged where the rule lands — what would
  stop it running, and what it would now stop — before the diff is shown,
  and the script is backed up and the move confirmed (`--yes`, `--dry-run`)
  like any other change. A move to where the rule already is sends nothing.

* **`mxfilter restore FILE` puts a backup back** (#13). It uploads the file
  byte for byte over the active script, and only that script. It shows the
  raw diff against what the server has now, backs the current script up
  first, has the server validate the file, and asks before replacing
  anything (`--yes`, `--dry-run`). It works over a script mxfilter cannot
  parse — the one deliberate exception to the merge-only rule, recorded in
  [ADR 0005][adr5].

* **Folder subscription is a setting you can see and change** (#42).
  `mxfilter subscribe FOLDER` and `mxfilter unsubscribe FOLDER` show or hide
  an existing folder in webmail, with the usual folder-name normalization
  and `--dry-run`. `folders` marks the folders that exist but are not
  subscribed, and `test` reports how many are subscribed and names the
  rest. Nothing is judged: an unsubscribed folder may be exactly what you
  wanted. Warnings that used to end "subscribe to it in your mail client"
  now name the `mxfilter subscribe` command instead.

ENHANCEMENTS:

* **A password-file path expands `~` and environment variables** (#8).
  `password_file = "~/pw"` used to fail naming the literal `~/pw`, which
  read as a missing file. `~`, `$VAR`, and `${VAR}` now expand in every
  place a password file can be named — the flag, `MXROUTE_PASSWORD_FILE`,
  and the config file. An unset variable is left as written, so the error
  names it.

* **`from-message` shows the message before it derives anything.** Date,
  From, To, Subject, and List-Id when present, decoded. The UID is dug out
  of webmail by hand, so a mistyped digit used to build a filter from the
  wrong message and then move mail on it — with nothing on screen to
  contradict you, since the only output was the derived criteria.

  It earns its place when the UID is right, too: seeing `To:` reveals that
  a message arrived at an alias, which is often the criterion you actually
  wanted rather than the `From:` derived by default.

* **The diff shows the change, not the reformatting.** Merging re-renders
  the whole script through `sievelib`, so the first change against a
  Roundcube-authored script moved almost every line — 29 of them on a
  25-line script, with the one new rule buried inside. The diff is now taken
  against a normalised copy of the original, so only the real change shows.

  Reformatting still happens on upload, so it is **reported rather than
  hidden**: a one-line note appears above the diff the first time, and stops
  once the script is already in this formatting. Rule bodies are unchanged
  either way ([ADR 0002](adr/0002-non-destructive-script-merge.md)), and
  neither the backup nor what is uploaded is affected — normalisation is a
  display concern only.

BUG FIXES:

* **A failed ManageSieve connection no longer calls typed values the
  default** (#55). With `--sieve-port 1 --sieve-tls none` the hint said
  "1 + none is the RFC 5804 / Dovecot default". It now says so only when
  both came from the default, and otherwise names where each came from.
  The snapshot suite also refuses `MXFILTER_UPDATE_SNAPSHOTS=1` under CI,
  where it would have rewritten every snapshot and passed unconditionally.

* **Bulk IMAP operations are chunked** (#24). Every matched UID used to go
  into a single MOVE, COPY, STORE, EXPUNGE, or header FETCH, so a large
  pass was one command line tens of kilobytes long — which servers may
  reject. Only the `--max-messages` default of 500 kept that from
  happening, by accident, so raising the cap for a big cleanup removed a
  protection nobody knew about. Work now goes out 250 UIDs at a time,
  independent of the cap. A failure part-way reports how many messages
  were fully processed, and whether re-running is safe: it is, except on a
  server without `MOVE`, where the failed batch may already have been
  copied and would be copied again.

* **`--no-subscribe` without `--create-folder` is refused** (#43). It only
  ever affected a folder the run created, so on its own it was accepted and
  did nothing. It is now a usage error that points at `mxfilter
  unsubscribe` for hiding a folder that already exists.

* **`--create-folder` no longer creates the folder before showing the
  change.** The folder was made over IMAP while the change was still being
  worked out — before the diff, before any confirmation — so an abort, a
  rejected script, or a failed merge left a stray folder behind. It is now
  announced with the plan and created on execute: for `add` and
  `from-message`, after CHECKSCRIPT accepts the script; for `apply`, after
  you confirm. An `apply` with no
  matching mail no longer creates the folder at all, and says so.

* **A rule that leaves mail where it is no longer offers to change it.**
  `add --keep` alone, or `--fileinto` naming the folder the mail is already
  in, used to search existing mail and then ask "Flag N message(s)?" about a
  pass that flagged nothing. The existing-mail pass is now skipped, with a
  line saying why.

* **The missing-extension warning covers a `default_folder` too.** A rule
  filing into the config file's `default_folder` needs Sieve `fileinto`
  exactly as one given `--fileinto` does, but only the flag was checked, so
  a server without it gave no warning before the upload.

NOTES:

* **The work now lives in an engine, not the CLI.** Everything mxfilter
  does moved out of `cli.py` into `mxfilter/engine.py`, which takes plain
  values, never prints or prompts, and splits every change into a read-only
  plan and a separate execute step. The CLI is now only argument parsing and
  rendering, so a terminal UI or other front-end can drive the same engine.
  No command, flag, message, or exit code changed.

## 0.1.0

Released 2026-08-14. The first tagged version; everything below is the work
up to that point rather than a set of changes against a predecessor.

**`v0.y.z` means alpha.** Per the global `git.md`, a zero major says breakage
is expected and the `y.z` split is deliberately loose. This tag is a marker
on history, not a stability promise, and it **publishes nothing** — there is
no registry and no release pipeline (see [RELEASING.md](RELEASING.md)).

Two names in here are already known to be wrong and are expected to change:
the distribution is called `mxfilter` and the repo `mxroute-email-filters`,
while the tool now manages settings as well as filters and is being pointed
at providers other than MXroute. Tracked as
[#45](https://github.com/harleypig/mailctl/issues/45). Nothing
being published is what keeps that cheap.

FEATURES:

* **Sieve rules over ManageSieve.** Build a rule from criteria flags
  (`--from`, `--to`, `--cc`, `--subject`, `--list-id`, `--header NAME=VALUE`,
  with `--match any|all` and `--compare contains|is|matches`), merge it into
  the account's **active** script, validate it server-side with `CHECKSCRIPT`,
  and activate it. Subcommands: `add`, `from-message`, `apply`, `remove-rule`,
  `list`, `show`, `folders`, `test`.
* **The retroactive pass over mail already delivered.** Sieve only ever runs
  on new incoming mail, so the same criteria are translated to IMAP `SEARCH`
  and applied to messages already in the mailbox — `MOVE` where the server
  advertises it, `COPY` + `\Deleted` + `EXPUNGE` where it does not. One
  criteria model drives both outputs, which is what keeps the Sieve rule and
  the retroactive pass in agreement.
* **Non-destructive merging.** The existing active script is parsed and merged
  into, never overwritten, so filters written in webmail survive. A parse
  failure is a hard stop, never a fall-back to overwrite
  ([ADR 0002](adr/0002-non-destructive-script-merge.md)).
* **Roundcube rule names are preserved.** Both `# rule:[NAME]` (Roundcube's
  managesieve plugin) and `# Filter: NAME` (`sievelib`) are read, and
  Roundcube's dialect is written — so the webmail and this tool co-edit one
  script instead of mangling each other's names.
* **Runtime discovery.** Sieve capabilities, the active script name, and the
  folder hierarchy delimiter are read from the server rather than hardcoded,
  so `Lists/News` and `INBOX.Lists.News` resolve to whichever spelling the
  account actually uses.

ENHANCEMENTS:

* **Placement control on `add` and `from-message` — `--first`, `--last`,
  `--before NAME`, `--after NAME`.** A rule used to be appended and nothing
  else, which is the wrong default once order matters: the first matching
  rule that carries `stop` wins, so where a rule goes decides whether it
  ever fires. The flags are mutually exclusive; `--last` remains the default.

  Two things worth knowing. With `--replace`, a placement flag **moves** the
  rule and no flag leaves it where it was — an explicit placement is an
  instruction, and quietly ignoring it would be the same silent success
  these warnings exist to remove. And `--before` / `--after` match a rule
  name **exactly**: no case folding, no whitespace forgiveness, and an error
  listing the known names if nothing matches. Note that Roundcube's
  `# rule:[Name ]` marker parses with the trailing space stripped, so a name
  copied verbatim out of a script is not always the name the script holds.

* **`mxfilter rules` — see what is already there, in evaluation order.**
  Lists the active script's rules in the order the server runs them, marks
  which carry `stop`, and reports any rule an earlier one makes unreachable.
  Sieve is order-dependent and `stop` ends evaluation, so a rule appended to
  the end can be dead the moment it is written — and `add` reported success
  either way. `add` now warns before the diff, since a diff can show what
  changes but not that the change lands after a rule that stops.

  Findings come in two tiers and the split is deliberate: `!` is decided, `?`
  is worth checking. Only substring containment on one header, with
  comparable match types and Sieve's default comparator, decides anything —
  a glob, a multi-condition `allof`, an explicit `:comparator`, a non-ASCII
  key, or a condition outside the model can never be more than a suspicion.
  Asserting that a live rule is dead is worse than staying quiet.

* **`mxfilter backup` — save the active script on demand.** Fetches the active
  script and writes it to a file, changing nothing on the server. The file is
  the server's **exact bytes**: no banner lines, nothing reformatted, no
  newline translated. That distinction is the point of the command —
  `mxfilter show` decorates its output with `# ---- name ----` and
  `# ---- N rule(s): ...` for a reader, so redirecting `show` to a file
  produces something that looks like a backup and cannot be restored, which is
  what [docs/VERIFYING.md](docs/VERIFYING.md) step 3 used to tell people to
  do. `--output PATH` overrides the location: a PATH ending in `/`, or naming
  a directory that already exists, means "write the default filename in here";
  anything else is the exact file to write. Written mode `0600` in a directory
  created `0700` — a Sieve script is not a credential, but it does say who the
  user corresponds with and how they sort it. `--dry-run` reports the file it
  would write and writes nothing.
* **Backups now default to the config directory** — usually
  `~/.config/mxfilter/backups`, or `$XDG_CONFIG_HOME/mxfilter/backups` — in
  place of `$XDG_STATE_HOME/mxfilter/backups`. XDG would call a backup *state*
  rather than config, and this is a deliberate departure from that rather than
  an XDG-endorsed reading: a backup the user cannot find is not a backup, and
  the config directory is the one mxfilter path they already know, having put
  `config.toml` in it. The automatic pre-upload backup and `mxfilter backup`
  share the one location, so there is no second directory to look in.
  `--backup-dir` and `MXROUTE_BACKUP_DIR` override it exactly as before. **Any
  backups already under `~/.local/state/mxfilter/backups` stay there** —
  nothing moves them.
* **Still no restore command.** The file is the server's exact bytes, and
  putting one back needs another ManageSieve client (`sieve-connect`, or a
  panel filter UI that exposes a raw import). `backup --help` says so, and a
  `restore` subcommand is tracked in [#13](https://github.com/harleypig/mailctl/issues/13): it is a write path
  against a live account and deserves its own confirmation flow and tests.
* **A password file source.** `--password-file PATH`,
  `MXROUTE_PASSWORD_FILE`, and `password_file` in the config file read the
  credential from a file, which — unlike an environment variable — can be
  closed to everyone but its owner. mxfilter **refuses** to read a file any
  group or other bit is set on (so `0600` and `0400` pass, `0644` does not),
  naming the path, the mode, and the `chmod` that fixes it, and never opening
  the file — the rule `libpq` applies to `~/.pgpass`, but said out loud
  rather than by silently ignoring the file. On WSL the file must live on
  the Linux filesystem, since a Windows mount reports `0777` whatever it is
  set to. Exactly one trailing newline is stripped and nothing else is — a
  trailing space can be part of a password. `mxfilter test` reports
  `set (via file)`.
* **`--password VALUE` (`-p`), the least safe source.** Added by request, and
  warned about on stderr the way `mysql` does: an argument is visible in the
  process list to every user on the machine, and the shell has already
  written it to history. Prefer `--password-file` or `MXROUTE_PASSWORD_FILE`.
  This supersedes the earlier note that there is deliberately no
  `--password` flag.
* **The three credential flags are mutually exclusive.** `--password-file`,
  `--password-cmd`, and `--password` are three equally explicit instructions
  with no natural ranking, so argparse rejects a second one rather than
  picking a winner the user would have to have memorised.

BUG FIXES:

* **A folder mxfilter created was not subscribed, so webmail could not see
  it.** IMAP subscription is a separate operation from `CREATE`, and
  Roundcube renders `LSUB` rather than `LIST` — so `--create-folder` made a
  folder that existed, received mail, and was invisible in the client the
  user actually opens. Nothing was lost, which is worse than an error: the
  command printed success and the mail was simply somewhere nobody looks.

  `--create-folder` now subscribes, and verifies it by re-reading the
  subscription list rather than trusting the server's OK. **`--no-subscribe`
  declines**, for the real case of a folder that should take mail out of the
  inbox without appearing in the sidebar — and it says so at the time, since
  a silent opt-out would reproduce the original bug for whoever finds the
  flag in a saved command line later.

  A subscribe that fails is a warning, not a failure: the folder exists and
  mail will arrive there, so aborting would leave an invisible folder with
  no rule and nothing filed, which is strictly worse.

  **Note this does not cover the `fileinto :create` path**, which is what
  `add --create-folder` uses whenever the server advertises the `mailbox`
  extension — there the folder is made by the server at delivery time, and
  whether it is subscribed is unknown. mxfilter now says so; issue #40 is
  where it gets settled.

* **The password resolution order was inverted for flags.**
  `MXROUTE_PASSWORD` beat an explicit `--password-cmd`, contradicting the
  `CLI flag > environment > config file > default` rule the rest of the
  settings follow. The hazard was concrete: with `MXROUTE_PASSWORD` exported
  for one account, running `--password-cmd` against a **different** account
  authenticated as the first one — the wrong mailbox, silently, with no error
  anywhere. The order is now, highest first: an explicit flag →
  `MXROUTE_PASSWORD_FILE` → `MXROUTE_PASSWORD_CMD` → `MXROUTE_PASSWORD` →
  `password_file` → `password_cmd` (config file) → the interactive prompt. A
  flag typed for this run beats an ambient variable, and a literal value
  never beats an instruction about where to fetch one.

NOTES:

* **Nothing here has run against a live MXroute account.** The 347-test suite
  is entirely offline, and the live tier (`tests/live/`) is gated behind
  `MXFILTER_LIVE` and has never been exercised.
  [docs/VERIFYING.md](docs/VERIFYING.md) is the ordered first-run procedure,
  from commands that touch nothing up to ones that move mail.
* **Safety defaults.** `--dry-run` on every mutating command; the active
  script is backed up before every upload; the retroactive pass always
  previews and confirms; `--max-messages` (default 500) refuses an
  over-cap batch outright rather than processing a partial set that would
  read as complete.
* **Credentials never reach output.** The password is wrapped in a `Secret`
  that renders `<redacted>` from both `__str__` and `__repr__`, so no
  `print`, f-string, `%s`, or traceback frame can disclose it; `reveal()` is
  the only way out and its call sites are pinned by a test. `--password`
  exists but is the least safe source and warns for the reason above —
  prefer `--password-file`, `MXROUTE_PASSWORD_FILE`, or
  `MXROUTE_PASSWORD_CMD`.
* **MXroute refusals are tiered by confidence.** `redirect` is refused
  because MXroute documents disabling it; `notify` and `vacation` are refused
  as a conservative choice of ours, and say so rather than implying a
  documented restriction.
* CI runs `ruff check`, `ruff format --check`, and `pytest` on every pull
  request and on pushes to `master`; both checks are required by the branch
  ruleset.

[adr5]: adr/0005-restore-may-replace-an-unparseable-script.md
[#53]: https://github.com/harleypig/mailctl/issues/53
[#52]: https://github.com/harleypig/mailctl/issues/52
[#54]: https://github.com/harleypig/mailctl/issues/54
[#50]: https://github.com/harleypig/mailctl/issues/50
[#63]: https://github.com/harleypig/mailctl/issues/63
[#61]: https://github.com/harleypig/mailctl/issues/61
[#30]: https://github.com/harleypig/mailctl/issues/30
[#56]: https://github.com/harleypig/mailctl/issues/56
[#40]: https://github.com/harleypig/mailctl/issues/40
[#45]: https://github.com/harleypig/mailctl/issues/45
[#82]: https://github.com/harleypig/mailctl/issues/82
[#89]: https://github.com/harleypig/mailctl/issues/89
[#90]: https://github.com/harleypig/mailctl/issues/90
[#91]: https://github.com/harleypig/mailctl/issues/91
[#92]: https://github.com/harleypig/mailctl/issues/92
[#106]: https://github.com/harleypig/mailctl/issues/106
[ADR 0006]: adr/0006-two-layer-component-and-provider-architecture.md
