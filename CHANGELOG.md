## Unreleased

Entries accumulate here under the usual headings — `BREAKING CHANGES:`,
`FEATURES:`, `ENHANCEMENTS:`, `BUG FIXES:`, `NOTES:` — and move under a
`## X.Y.Z` heading when a tag is cut.

## 0.11.0

BREAKING CHANGES:

* **The commands are grouped by what they act on, noun first: `mailctl
  <group> <action>`** ([#219]). `mailctl filter add` replaces `mailctl
  add`, `mailctl mail search` replaces `mailctl search`, and so on for
  every command; each takes the same flags and does the same thing as
  before under its new name. The top-level help lists the groups — `mail`,
  `folder`, `filter`, `filterset`, `server`, `config` — and each group's
  help lists its commands; `mailctl help GROUP [ACTION]` prints the same as
  `--help` there. `filterset` is offered only where the provider stores
  several sets of filters, as `mxroute` does. The old names are a clean
  break, with no aliases: each is refused with exit status 2 and a message
  naming the command it is now, such as `'rules' is now 'mailctl filter
  list'`; `from-message` and `messages`, gone since 0.9.0, now point at
  `filter add --like` and `mail search` the same way. A `--dry-run --json`
  plan's `command` is the new path, such as `"filter add"`, so every
  `--json` document's `version` is now `2`; the filter document, the probe,
  a saved baseline, and the drift report keep their own versions, which
  are unchanged. A message that points at another command names the new
  one. A script calling mailctl needs the new names:

  | Old | New |
  |-----|-----|
  | `search` | `mail search` |
  | `view` | `mail view` |
  | `mark` | `mail mark` |
  | `senders` | `mail senders` |
  | `folders` | `folder list` |
  | `create-folder` | `folder create` |
  | `rename-folder` | `folder rename` |
  | `subscribe` | `folder subscribe` |
  | `unsubscribe` | `folder unsubscribe` |
  | `rules` | `filter list` |
  | `add` | `filter add` |
  | `apply` | `filter apply` |
  | `remove-rule` | `filter remove` |
  | `move-rule` | `filter move` |
  | `rename-rule` | `filter rename` |
  | `enable-rule` | `filter enable` |
  | `disable-rule` | `filter disable` |
  | `optimize-rules` | `filter optimize` |
  | `list` | `filterset list` |
  | `show` | `filterset show` |
  | `backup` | `filterset backup` |
  | `restore` | `filterset restore` |
  | `test` | `server test` |
  | `probe` | `server probe` |
  | `save-baseline` | `server baseline save` |
  | `show-baseline` | `server baseline show` |
  | `check-baseline` | `server baseline check` |
  | `migrate-config` | `config migrate` |

* **What mailctl prints is worded the same for every provider** ([#219]).
  Every command's help and output but the server reports now speaks of
  filters and filter sets, not rules, Sieve scripts, ManageSieve, or IMAP:
  `Filter 'x' in filter set 'y'`, `2 filter(s), in evaluation order`, and
  `no filter named 'x' in the active filter set`; the diff's heading is
  `--- diff ---` rather than `--- sieve diff ---`; a filter's actions read `files the message into 'INBOX.Lists'; stops`
  rather than `fileinto INBOX.Lists; stop`, in a plan and in `filter
  list`; `filterset show` frames the source as `--- filter set 'NAME'
  ---` and `--- N filter(s): ... ---` rather than `# ---- NAME ----`; and
  `Rule 'x' on script 'y'`, `Backed up current script`, `Created IMAP
  folder`, and the rest now say `filter set` and `folder`. A server's
  alert or warning is introduced as `mailctl: alert from the mail server:`
  or `mailctl: warning from the filter server:` rather than naming `imap`
  or `sieve`, and `--verbose` lines are tagged `[mail]` and `[filter]`.
  The server's own words stay where they belong: `server test`, `server
  probe`, and the baselines, the help of `--disable-extension` and of the
  connection settings (`--host`, `--imap-*`, `--sieve-*`, which keep their
  names), a refusal about an extension or an action MXRoute does not
  allow, and the filter set's own text in a diff or `filterset show`.
  A script that matches on a line of mailctl's output needs the new text,
  and a JSON error's `message` carries it too.

* **Three options are renamed, with no aliases** ([#219]). Each old name
  is refused with exit status 2 and a message naming the new one, such as
  `--fileinto is now --move-to`:

  | Old | New |
  |-----|-----|
  | `--fileinto FOLDER` | `--move-to FOLDER` (with `--keep` it copies) |
  | `--script NAME` | `--filterset NAME` |
  | `--no-imap` | `--no-mail` |

  None had a `MAILCTL_*` variable or a `config.toml` key. The connection
  settings (`--host`, `--imap-*`, `--sieve-*`), `--disable-extension`, and
  `--raw` keep their names.

* **`--json` documents name a filter set `filterset`** ([#219]), within
  the version 2 this release already brings. `filterset list` prints
  `"filtersets": [{"name", "active"}]` where it printed `"scripts"`; `filter
  list` and every plan that uploads print `"filterset"` where they printed
  `"script"`. A plan's folder `status` is `create`, `created-on-delivery`,
  or `create-and-on-delivery` where it was `imap-create`, `sieve-creates`,
  or `imap-and-sieve-create`; `none`, `exists`, `missing`, and
  `uncreatable` are unchanged. `diff.label` stays, naming the language of
  the diff's text (`sieve`). A filter is `filter` too: `rules`,
  `rule`, `rules_before`, and `rules_after` are now `filters`, `filter`,
  `filters_before`, and `filters_after`, in `filter list`, every plan for
  one filter, `filterset restore`, `folder rename`, and `filter optimize`.
  The probe, a saved baseline, and the drift report keep their `rules`
  half, being the server's own report.

BUG FIXES:

* **A `filterset` command is refused by a provider that keeps one filter
  set, whichever front-end asks** ([#219]). Hiding the group from help was
  the CLI's; listing, showing, backing up, or restoring a filter set is
  now refused below it too, naming the provider, before anything is read.

## 0.10.0

FEATURES:

* **`mailctl rename-rule OLD NEW` changes a rule's name, and nothing else**
  ([#216]). Only the rule's name line in the script is rewritten: its
  conditions, actions, position, and disabled state, every other rule, and
  every comment stay byte for byte as they were, and the diff shown first
  is the whole change. It works like the other rule commands: the diff,
  then a confirmation (`--yes` skips it, `--dry-run` stops before it, and
  `--dry-run --json` prints the plan), and a backup of the script before
  the upload. It is refused before anything is uploaded when OLD is not a
  rule, when NEW is empty or already another rule's name (under `--json`
  the error's `code` is `rule_name_taken`), and when NEW cannot be written
  as a rule name — a line break or other control character, spaces at the
  end, or a name marker inside it. A rule with no name written in the
  script cannot be renamed. It is offered only where the provider can
  rename a rule; `mxroute` can.

## 0.9.1

BUG FIXES:

* **A warning the ManageSieve server gives about a stored script is now
  shown** ([#208]). A server can accept a script while warning that part
  of it may not do what was meant — an IMAP flag it will ignore, say —
  and the ManageSieve standard says a client should show that warning.
  mailctl dropped it. It now prints each line of it on standard error, as
  `mailctl: warning from the sieve server: <text>`, whether or not
  `--verbose` is given, once per run; the text is the server's, so
  control characters in it are shown escaped. The same warnings from the
  check made just before the upload, which name a temporary file rather
  than the script, are shown only under `--verbose`.

* **A warning the IMAP server marks as an alert is now shown** ([#205]).
  An IMAP server can flag a message for the user's attention — a mailbox
  nearly full, maintenance tonight — and the IMAP standard says a client
  must show it. mailctl dropped every one. It now prints each on standard
  error, as `mailctl: alert from the imap server: <text>`, whether or not
  `--verbose` is given, and once per run however often the server repeats
  it; under `--json` standard output still holds the document alone. The
  text is the server's, so control characters in it are shown escaped,
  as in mail. An alert the server sends before the connection is
  encrypted is ignored, as the standard advises, since anyone on the
  network could have written it. ManageSieve has no alerts to show: its
  standard defines no such response code.

* **A UID from an earlier run can be checked against the folder's
  UIDVALIDITY, and a stale one is refused before anything is used or
  changed** ([#204]). A UID names one message only while the folder keeps
  its UIDVALIDITY (RFC 9051 section 2.3.1.1); a server that renumbers a
  folder, as Dovecot does when one is deleted and made again under the
  same name, gives it a new one, and a UID kept from before may then name
  other mail. `search` now shows the value in its listing heading and as
  `uidvalidity` in `--json`, and `view` and the `mark` and `apply`
  `--dry-run --json` plans carry it too. `view`, `mark`, and `--like` on
  `search`, `add`, and `apply` take `--uidvalidity N`: if the folder's is
  now another, the command exits 1 naming both values, using none of the
  UIDs and changing nothing; under `--json` the error's `code` is
  `uidvalidity_changed`. A bare UID works as before, unchecked.
  `--uidvalidity` without `--like` on `search`, `add`, or `apply`, and a
  value outside 1 to 4294967295, are refused before connecting. Within one
  run, `mark` and `apply` also check the value again just before writing,
  so a folder renumbered while the confirmation was open is refused too.
  Reading the value costs no request of its own: the `SELECT` or `EXAMINE`
  each command already sends reports it. A provider that has no such
  value does not offer `--uidvalidity` and refuses it by name.

* **References left stale by 0.9.0 are corrected** ([#212]). The
  `source_folder` notes in `config.toml.example` and `.env.example` named
  `from-message` and `messages`, which 0.9.0 replaced; they now name the
  commands that read that folder. `mailctl test`'s Folder line now lists
  `mark`, `senders`, and `--like` on every command that takes it, not only
  `add`. `mark --uidvalidity`'s help reads "the UIDs were listed under".
  `apply --help` and `search --help` now open with a paragraph saying
  what the command does, as the other commands' do. `docs/VERIFYING.md`
  no longer says nothing has run against a live MXroute server: reads and
  dry runs have, and no write has.

## 0.9.0

BREAKING CHANGES:

* **`mailctl add` saves the rule and nothing else; `mailctl apply` is the
  only command that touches mail already delivered, and `from-message` is
  gone** ([#149]). `add` no longer searches for, previews, or moves
  existing mail after uploading: it ends by saying that mail was left
  alone and pointing at `apply`. Its `--no-apply`, `--max-messages`,
  `--move-threshold`, and `--yes` are removed — `add` asks no question, so
  `--yes` had nothing left to answer — and giving any of them is an error.
  To get what `add` used to do, run `add` and then `apply` with the same
  criteria and actions. Both now take their criteria three ways: criteria
  flags; `--filter FILE`, the document `search --build-filter --json`
  prints (`--filter -` reads standard input); or `--like UID [--folder
  FOLDER] [--derive HEADERS]`, which takes them from a message, shown
  first, as `search --like` does. A criteria flag given with `--like`
  replaces what was derived for its header. `--filter` with criteria flags
  (`--match` and `--compare` included) or with `--like` is refused before
  connecting. `mailctl from-message --uid N` becomes `mailctl add --like N`
  (or `apply --like N` for the mail already there); its `--search` has no
  replacement — find the UID with `mailctl search` first. On `add`,
  `--folder` now only says where the `--like` message is, and is refused
  without `--like`.

* **`mailctl messages` is now `mailctl search`, and its `--search` is now
  `--raw`** ([#147]). No alias is kept for either name: `mailctl messages`
  and `mailctl search --search` are rejected as unknown. Everything else is
  unchanged — the same criteria flags, `--folder`, `--limit`, the same
  listing, and it still never writes. `--raw QUERY` takes a query in the
  host's own search language, which for MXroute is IMAP `SEARCH` syntax
  (`--raw 'UNSEEN SINCE 1-Sep-2026'`). Offering it is now up to the
  provider: one that does not declare raw queries leaves `--raw` out of
  `search --help`, and refuses it by name, before connecting, if it is
  given anyway.

FEATURES:

* **`mailctl probe --report` prints an issue body for a server mailctl does
  not recognise** ([#39]). Each connection picks a server module from the
  IMAP `ID` and ManageSieve `IMPLEMENTATION` answers; where one matches
  none, mailctl works with the plain protocol, and now says so: `probe`
  ends with a line naming the unrecognised server, and `test` adds one
  `Servers:` line. `probe --report` prints a Markdown issue body with each
  server's identity and capabilities, the extensions, the delimiter and
  namespaces, the number of folders and scripts, and the mailctl version.
  The address, its domain and local part, the configured hosts, folder and
  script names, and anything shaped like an address or IPv4 address are
  replaced wherever they appear, and `OWNER`'s value is redacted; the
  password, script text, and messages are never read. Nothing is sent:
  the body goes to stdout, and stderr says where and how to file it. On a
  recognised server it prints nothing and says there is nothing to
  report.

* **`mailctl senders` shows who sends the most mail, and how much of it is
  left unread** ([#160]) — the list to make filters from. It counts a
  folder's mail by sender address (lower-cased), by `--by domain`, or by
  `--by list-id` (the bracketed List-Id; mail with none is one row),
  busiest first, with the unread count and share; ties go to the most
  unread. It takes `search`'s criteria flags, so `--since 2026-09-01` or
  `--unread` narrows what is counted. `--top N` (default 20, `0` for all)
  and `--min N` trim the rows, never the totals. It is one search and then
  the headers of what it found, a page of 250 per request, and nothing is
  marked read. Above `--max-messages` (default 5000) it refuses before
  reading a header, rather than counting part of the mailbox. The table
  ends with the `search --build-filter` line that makes a filter from the
  top row; `--json` prints the report as a versioned document instead.

* **`mailctl folders --counts` shows each folder's total and unread
  messages** ([#157]), for a morning digest or a cron job. The counts come
  from one IMAP `LIST ... RETURN (STATUS ...)` (`LIST-STATUS`, RFC 5819) —
  one request however many folders there are — and nothing is read or
  marked. Where the server also advertises `STATUS=SIZE` a Size column is
  added. `--counts --json` gives each folder `messages`, `unseen`, and
  `size` (bytes, or `null` where the server does not report sizes); a
  folder the server gives no counts for, one that cannot hold mail, shows
  `-` and `null`. A server that does not advertise `LIST-STATUS` is
  refused, naming it, rather than asked once per folder. A provider that
  cannot count folders leaves `--counts` out of `folders --help`, and
  refuses it by name, before connecting, if it is given anyway. MXroute
  advertises both capabilities.

* **`mailctl search --sort size|sent|received [--reverse]`** ([#159]).
  Lists matches by size, by their `Date:` header (`sent`), or by when the
  server received them (`received`, the Received column), instead of
  newest first; `--reverse` puts the largest or newest first. `--limit` is
  taken after sorting, so `search --sort size --reverse --limit 10` is the
  ten largest messages in the folder. A server advertising IMAP `SORT`
  sorts in one command and only the messages shown are fetched; otherwise
  mailctl fetches every candidate a page at a time and sorts them itself.
  `--json` and `--uids-only` keep the order, and the `search` document
  gains a `sort` key: `null` for newest first, else `{"key", "reverse"}`.
  `--reverse` without `--sort`, and `--sort` with `--build-filter`, are
  refused.

* **`mailctl save-baseline` and `mailctl show-baseline` record what the
  servers say, per host** ([#18]). `save-baseline` probes both servers, as
  `mailctl probe` does, and saves the result to
  `$XDG_CONFIG_HOME/mailctl/baselines/<host>.json`, mode 0600: what
  describes the server once, and the active script per account. The first
  save just writes it; replacing one shows what changed and the file's
  diff, then asks (`--yes`, `--dry-run`). Only the local file is written,
  and it holds no credential. `show-baseline` prints it; `--json` prints the
  file as stored. A file that is damaged or from another version is refused
  by name and left alone.

* **`mailctl check-baseline` reports drift from the baseline, in terms of
  what it means for this account** ([#19]). Serious: an extension the active
  script requires is gone, the folder delimiter or personal namespace
  changed, an IMAP capability mailctl relies on came or went, or the active
  script is another one. Informational: everything else, an identity change
  (the clearest migration signal) and a new extension among it. It refuses
  nothing. It exits 0 with no drift, 3 with informational drift only, and 4
  with serious drift; `--json` prints the report as a versioned document.
  `mailctl test` adds one line saying whether there is drift, and never
  fails over it.

* **`mailctl rename-folder OLD NEW` renames a folder and repoints the
  rules that file into it** ([#5]). The folders under it move with it. A
  rename is three changes, and mailctl makes all three: IMAP `RENAME`;
  the subscription, which `RENAME` leaves behind (RFC 3501 section 6.3.5),
  so each moved folder that was subscribed is subscribed under its new
  name and its old name dropped — without it the folder vanishes from
  webmail; and every rule in the active script filing into an old name.
  Only those folder names change in the script: every other byte,
  comments and layout included, is kept, rather than the whole script
  being re-rendered. The plan — the folders, their message count, the
  rules, and the diff — is shown first, then `--dry-run` stops (`--json`
  prints it as a document) or you are asked to confirm. The new script is
  backed up and validated (`CHECKSCRIPT`) before the folder is touched,
  and stored straight after the rename, so the time a rule points at a
  missing folder is as short as it can be. Afterwards the account is read
  back: the old name gone, the new one there, subscribed where the old one
  was, holding at least the messages it did, the folders under it moved,
  and no rule still filing into an old name. Any of those failing is a
  non-zero exit that names it. A failure part-way is never retried; the
  error says what state the account is in and how to undo it. `INBOX`
  cannot be renamed, and a `NEW` that exists — or differs from an existing
  folder only in case — is refused.

* **`mailctl optimize-rules` proposes a better arrangement of the rule
  set, and applies it when asked** ([#21]). Three kinds of change, each
  made only where the rules' own conditions decide it — one header, one
  comparator, one key set containing the other: a rule that can never run
  because an earlier rule with `stop` catches all of its mail and does the
  same thing is removed; a specific rule starved by a broader one ahead of
  it is moved to just before that rule, so its mail gets its own actions;
  and consecutive rules testing the same header the same way, with the
  same actions and `stop`, become one rule with a key list, keeping the
  first rule's name. A rule it cannot fully read, or a doubt it cannot
  settle — a glob, rules that test the same mail and do different things,
  a live rule between two that would merge — is reported and left alone.
  Disabled rules are never moved, merged, or removed. The script stays a
  flat list of `# rule:[NAME]` blocks. The proposals and the diff are
  shown first, then `--dry-run` stops (`--json` prints them as a document)
  or you are asked to confirm; the upload is backed up first. `--skip
  redundant|reorder|merge` leaves a kind out. It needs a provider that
  orders its rules, and is not offered otherwise.

* **`mailctl probe` prints what a provider record needs** ([#101]). It
  reads both servers and changes nothing: the date and time in UTC, where
  each half connects, each server's identity (IMAP `ID`, ManageSieve
  `IMPLEMENTATION`), its whole capability list and whether that list was
  read before or after login, the Sieve extensions, the active script, the
  folder delimiter, and the IMAP namespaces. `--json` prints it as a
  versioned document (`"version": 1`) with every list sorted, so two probes
  of an unchanged server differ only in the time. No part of the password
  is printed; with `--json`, `--verbose` output goes to stderr so stdout
  stays one document.

* **`--json` on the commands whose output is data, and `search --uids-only`**
  ([#151]). `search`, `view`, `folders`, `rules`, and `list` print their
  result as one JSON document, and every write command that changes the server
  — `add`, `apply`, `remove-rule`, `move-rule`, `disable-rule`, `enable-rule`,
  `create-folder`, `subscribe`, `unsubscribe`, `restore`, `mark` — prints its
  `--dry-run` plan as one. On a write command `--json` needs `--dry-run`, and
  is refused without it before connecting: a document on stdout leaves no room
  for a confirmation prompt. Each document carries `"version": 1` and is built
  from the values themselves, not the table a person reads — whole subjects,
  raw IMAP flags, dates in ISO 8601 — and the README lists the shape each
  command prints. Only the document goes to stdout; anything said on the way,
  `--verbose` progress included, goes to stderr, and a failure is one line of
  JSON, the last on stderr, with the usual non-zero exit. `search --uids-only`
  prints the matching UIDs, one per line, and nothing else. `search --json`
  was refused without `--build-filter` and now prints the listing;
  `--build-filter --json` still prints the filter document. `test` offers no
  `--json`: it is a report for a person.

* **Search by body, date, and read or flagged state** ([#152]). `search`,
  `apply`, and `add` take `--body TEXT` (the body contains it; repeatable),
  `--since DATE` and `--before DATE` (received on or after, or before, as
  `YYYY-MM-DD`), `--older-than AGE` (`30d` or `3w`: `--before` a date
  counted back from today), `--unread`, and `--flagged`. `--body` is
  combined with the header criteria under `--match` and is always a
  substring test, so it is refused with `--compare is` or `matches`; the
  dates and states are always ANDed with the rest. `add` refuses the dates
  and states, saying why — a message being delivered is new, unread, and
  unflagged — and pointing at `search` and `apply`; so does `add --filter`
  with a document carrying one, and `add` keeps `--before` for placing the
  rule. `add --body` writes the Sieve `body` test and is refused, naming
  the extension, on a server that does not advertise `body` or when
  `disabled_extensions` turns it off; `mailctl test` now marks `body` as
  one mailctl's rules can need. The filter document gains optional
  `body`, `since`, `before`, `older_than_days`, `unread`, and `flagged`
  keys, still under `version: 1` — an older mailctl refuses a document
  using them rather than misreading it — and `search --build-filter` shows
  them. A bad date or age is refused naming the flag, before connecting.
  The README's *Body, dates, and state* says how the Sieve and IMAP sides
  read a body differently.

* **`mailctl help [COMMAND]`** ([#153]). `mailctl help` prints what
  `mailctl --help` prints, and `mailctl help add` what `mailctl add --help`
  prints, with the same exit code. It follows the provider as a command's
  own `--help` does: `mailctl help add --provider NAME`, or a provider set
  in the environment, the env file, or the config file, shows what that
  provider offers. An unknown command is refused as `mailctl nosuch` is,
  naming the valid ones. It never connects or asks for the password.

* **`mailctl create-folder NAME` makes a folder on its own** ([#155]).
  Until now a folder was only created as a side effect of
  `--create-folder` on `add` or `apply`. The name is normalized like every
  other (`Lists/News` and `INBOX.Lists.News` are the same folder), a new
  one goes where the server says new folders belong, and it is subscribed
  so webmail shows it unless `--no-subscribe` is given. The plan is shown
  first — including any parent folders that do not exist yet, which the
  server is expected to create along with it — then `--dry-run` stops, or
  you are asked to confirm (`--yes` skips the question). A folder that
  already exists is left exactly as it is, and if it is not subscribed
  mailctl says so and points at `mailctl subscribe`. A name that differs
  from an existing folder only in case is refused, naming that folder.

* **`mailctl search --like UID` finds mail like a message you have, and
  `--build-filter` prints the filter** ([#148]). `--like` reads the
  message in `--folder`, shows its identifying headers, and takes criteria
  from it — its List-Id, else its From; `--derive` names the headers
  instead, as for `from-message`. Criteria flags given as well are merged
  in: a flag replaces what was derived for its own header, any other
  header is added, and `--match` and `--compare` govern the whole set as
  always. `--build-filter` prints the filter the criteria make — flags,
  `--like`, or both — and saves nothing; without `--like` it needs no
  server at all. `--build-filter --json` prints it as a versioned filter
  document (`{"version": 1, "criteria": {…}}`, criteria only, no actions)
  with nothing else on stdout; the README describes the format, and `add
  --filter` and `apply --filter` read it ([#149]). `--build-filter` with no
  criteria is refused, and so is a `--like` UID the folder does not hold,
  naming both.

* **Switch a rule off without deleting it: `mailctl disable-rule NAME` and
  `mailctl enable-rule NAME`** ([#158]). A disabled rule stays in the
  script, written the way Roundcube's filter screen writes one -- `if
  false # <its test>`, with the rule's actions kept -- so a rule switched
  off in mailctl shows as off in webmail, and one switched off in webmail
  can be switched back on with `enable-rule`. Like every change, each
  shows the diff first, backs up the script, and asks before uploading;
  `--dry-run` stops after the diff and `--yes` skips the question. A rule
  already in the state asked for is left alone, with a message saying
  so. `enable-rule` refuses a rule whose kept test it cannot read back,
  rather than guessing one. `mailctl rules` marks a disabled rule
  `[disabled]` and shows the test it would have, and a disabled rule is
  never reported as blocking, or blocked by, another.

* **`mailctl mark UID...` sets and clears read, flagged, and keywords**
  ([#150]). `--read` / `--unread`, `--flag` / `--unflag`, and
  `--keyword K` / `--no-keyword K` (each repeatable) may be combined, as in
  `mailctl mark 4127 4128 --read --flag`; `--folder` names the folder as
  for `view`. The messages' flags are read first, without marking anything
  read, and what each has and what would change is shown; then `--dry-run`
  stops, or you are asked to confirm (`--yes` skips the question). A
  message already as asked is left alone, and if none would change mailctl
  says so and exits 0. A UID the folder does not hold stops the command,
  naming it, before anything is marked. `--read` with `--unread`, `--flag`
  with `--unflag`, one keyword both set and cleared, and a keyword that is
  not one plain word (or starts with `\`) are refused before connecting.
  Setting and clearing are separate writes to the server, and `view` stays
  read-only. A provider that cannot set flags does not offer `mark`, and
  refuses it by name, before connecting, if it is given anyway.

ENHANCEMENTS:

* **A command given bad input fails before logging in** ([#137]). The CLI
  now connects to each server the first time a command needs it, rather
  than to every server it might need before starting, and `mailctl add`
  refuses a rule with no action before connecting. So `mailctl messages`
  given both criteria and `--search`, or `mailctl apply` or `mailctl add`
  with no action, stops with its error before logging in to either server
  or asking for the password. What each command prints is unchanged.

* **Errors from below the command line no longer name its flags, and
  another host's help and messages no longer name MXroute's** ([#51],
  [#109]). A refusal from the work itself -- no criteria, no action, a
  message ceiling, a rule name already taken -- now states the condition
  in words any front-end can show, and carries a code; the command line
  adds its own flags (`--max-messages`, `--replace`, ...) when it prints
  it, so what `mailctl` says is unchanged. One sentence goes: `apply`'s
  `--max-messages` refusal no longer ends by saying a rule in the command
  was already uploaded, which since [#149] `apply` never does. Under `--json` the error
  document carries that code beside the message, as
  `{"version", "error": {"message", "code"}}`, so a script can tell one
  refusal from another without reading the text. `move-rule --before
  NAME` naming the rule being moved now says "the rule being moved"
  rather than "the rule being added". The words that describe the host
  -- its name, its rule language, how it validates and disables a rule,
  what a backup file is called -- are the provider's own, so a provider
  other than `mxroute` gets help and messages in its terms; `mxroute`'s
  are unchanged, and `mailctl test` shows its `Host:` row only for a
  provider that reads a host setting.

* **Errors from below the command line no longer name its commands**
  ([#183]). A refusal that points somewhere else -- no active script, no
  such folder, a missing or unreadable baseline, a date filter in a saved
  rule, an interrupted folder rename -- now names the operation to use as
  data, and the command line adds `'mailctl list'`, `'mailctl folders'`,
  `'mailctl save-baseline'`, ... when it prints it, so what `mailctl` says
  is unchanged. Under `--json` each of these errors now carries a `code`
  like the refusals of [#51].

BUG FIXES:

* **`mailctl add --before ""` and `--after ""` are refused instead of
  appending the rule** ([#196]). An empty rule name was read as no
  placement at all, so the rule quietly went to the end. `add` now refuses
  it before connecting, with the same message `move-rule` gives:
  `--before and --after need a rule name`.
  Also fixed alongside it: `mailctl help optimize-rules` printed
  `{rule_language}` literally in its `--activate` help; it now names the
  provider's rule language, as every other `--activate` does.

* **Running `apply --keep` again no longer copies the same mail twice**
  ([#192]). `apply --fileinto X --keep` leaves the originals where they
  are, so they still match next time, and every re-run -- from cron, or
  after a failure part-way -- put another copy of each into X. Before
  copying, `apply` now looks in X, reading only, and leaves out every
  match X already holds with the same Message-ID, including copies the
  saved rule filed there itself. The preview says how many are left out,
  a run with nothing left to copy says so and changes nothing, and the
  `--json` mail plan gains `"held"` and `"unidentified"`, the UIDs of
  each. A message with no Message-ID cannot be looked for, so it is still
  copied every time, and the preview counts those too. Any flag asked
  for is still set on every match.

* **`apply --keep` with a folder copies the mail there, as the rule
  does** ([#188]). The rule `add` saves for `--fileinto X --keep` files a
  copy into X and leaves the message in the inbox, but `apply` with the
  same actions moved the mail already there into X. It now copies: each
  message stays where it is, a copy lands in X, and both carry any flag
  asked for. The dry run says "would copy ... leaving them in", the
  prompt asks to copy, and `--json` marks the plan `"copies": true` (a
  new key, false for every other plan). `apply --discard --keep`
  permanently deleted the matching mail, while the rule keeps it, since an
  explicit keep outlasts a discard. It now deletes nothing and files
  nothing, and with no flag to add it skips the existing-mail pass.

* **Your own comments in the filter script are kept** ([#7]). A comment
  you wrote in the script -- `# this one is for the accountant` -- was
  dropped the first time mailctl added, moved, or removed a rule, while
  the command reported success. Each comment now stays with the rule
  that follows it and is written directly above that rule: it moves when
  the rule moves, stays when the rule is replaced, and goes when that
  rule is removed. A comment above the `require` line stays at the top,
  and one after the last rule stays at the end. A comment inside a rule
  is kept too, but moves up to sit above the rule. A rule switched off in
  Roundcube keeps its `if false # ...` line as Roundcube wrote it, so it
  still shows as disabled in webmail; before, that comment was dropped
  and the rule no longer did. `/* ... */` comments are still dropped.

* **Replacing a disabled rule keeps it disabled, the way webmail shows
  it** ([#168]). `mailctl add --replace` on a rule switched off in
  Roundcube wrote the new rule inside an `if false { ... }` block, which
  never runs but which webmail shows as enabled. The replacement is now
  written as Roundcube writes a disabled rule -- `if false # <the new
  test>` -- so it stays switched off, shows as disabled in webmail and in
  `mailctl rules`, and `mailctl enable-rule` turns on the new test. The
  old rule's test goes with it. A rule left in the `if false { ... }`
  form by an earlier replace is rewritten the same way the next time it
  is replaced. A new test that cannot fit on one line is refused while
  the rule is disabled: enable it first, then replace it.

* **A header's name is only ever a header name** ([#175]). A `--header`
  (or a filter document's header, or one derived by `--like`) whose name
  looked like a Sieve test was written as that test. `--header notes=x`,
  and any name starting with `not`, became `not header ...`, which the
  server accepted and which filed exactly the mail it should have left;
  `true` matched every message and `false` none; `exists` and `address`
  wrote the wrong test; `body`, `size`, `envelope`, and `currentdate`
  failed. Each is now a `header` test on that name. Two smaller faults in
  the same step are fixed with it: a header name containing `"` is
  escaped rather than ending the string early, and a value or folder
  starting with `'` is quoted rather than written bare. Rules on any other
  header are written exactly as before.

* **`mailctl move-rule` refuses an empty `--before` or `--after` name**
  ([#10]). `--before ""` counted as a position given, so the command
  connected and went on to plan a move that left the rule where it was.
  It now stops before connecting and says the flag needs a rule name.

NOTES:

* **Type checking now gates every pull request; no change to the tool**
  ([#10]). `pyright`, in its standard mode, checks the package and the
  tests in CI's Lint job, and locally with `make typecheck`. Its version
  is pinned in the dev dependencies, so a new pyright release cannot turn
  CI red on its own.

* **The engine is split into the session and the utilities; no behaviour
  change** ([#133]). `mailctl.engine` now holds only the session: choosing
  the configured provider, checking its settings, and opening and closing
  it. The work itself moved, unchanged, into `mailctl.utilities`, one
  module per subject (rules, scripts, backup, folders, mail, messages,
  migration, reports), and the CLI calls the utilities. Every command's
  output is byte-for-byte what it was.

* **Each provider is split into a dialect and a transport; no behaviour
  change** ([#134]). The dialect is the offline half: it turns a rule into
  the host's language and back, edits the stored rule set, and holds the
  host's refusals and wording. The transport is the communication half:
  it reads and stores what it is handed and reports whether the server
  accepted it, without building or checking anything. The utilities now
  do the building, using the dialect for the host-specific parts, and
  write backup files themselves. The check that keeps the existing-mail
  pass to exactly what the Sieve rule matches -- IMAP's search is only a
  rough first cut -- moved from the IMAP code into the mail utility. Every
  command's output is byte-for-byte what it was.

* **The session can stay open, and survives the server dropping it**
  ([#135]). A session now connects each server the first time it is
  needed rather than all at once, so a front-end that keeps running can
  hold one open. If the server has closed an idle connection, a read
  reconnects and tries once more on its own. A write that fails is
  reported and never sent again, because it may already have taken
  effect. Calls on one connection take turns, and fetching messages
  always selects the folder it names instead of relying on the last one
  selected. The CLI's output is byte-for-byte what it was.

* **A test now guarantees that looking never changes anything** ([#154]).
  Every step that only reads -- planning a change, listing rules or
  folders, searching and viewing messages, the probes behind `mailctl
  test` -- is checked on every run never to store a rule, create or
  subscribe a folder, or move mail. Only the steps that carry out a plan
  already shown may. No behaviour change.

* **A test tier that runs every write against a local mail server; no
  change to the tool** ([#49]). `make testcontainer` starts a throwaway
  Dovecot with Sieve support in Docker, built from Debian's own packages,
  and runs mailctl's changes against it: adding a rule beside one
  Roundcube wrote, removing and moving rules, backing up and restoring a
  script byte for byte, creating and subscribing folders, and moving,
  flagging and deleting existing mail. It also delivers new mail through
  the server so the uploaded rule is seen filing it. It never touches a
  real account, runs only when `MAILCTL_CONTAINER=1` is set, and is not
  part of the default `pytest` run or of CI.

* **Live tests can now write to a real account safely; no change to the
  tool** ([#9]). A test that writes first saves every Sieve script exactly
  as the server holds it, and afterwards puts back whatever it changed,
  removes any script it added, and checks the result, failing loudly --
  and naming the saved copy -- if it cannot confirm it. This happens
  whether the test passes, fails, or is interrupted with Ctrl-C. Mail is
  only ever handled in a new folder the test makes and deletes afterwards;
  INBOX and any folder that already exists are refused. Writing needs
  `MAILCTL_LIVE_WRITE=1` on top of `MAILCTL_LIVE=1`, so `make testlive`
  on its own still only reads. All of it was proved against the local
  test server first; nothing has yet been run against a real account.

## 0.8.4

NOTES:

* **Documentation only; no change to the tool.** The icebox records the
  open question of one Sieve evaluator per provider (Dovecot's own
  `sieve-test` for MXroute, and what Gmail would even need) ([#129]). The
  CLI-first convention now names per-folder retention as the kind of
  automation the CLI exists for, run by cron or a timer rather than a
  daemon ([#130]).

## 0.8.3

FEATURES:

* **Read-only live checks as one script** — `scripts/live-readonly.sh`
  (or `make livecheck`) runs read-only checks against the account mailctl
  is configured for and reports them as TAP: both services connect, the
  script and rule counts agree, the folder list is consistent, viewing a
  message leaves it unread, and every change command previews without
  changing anything. Name checks to run just those; `--list` names them.
  Only read-only subcommands and `--dry-run` are ever sent, `--yes` never
  is, and the script refuses any change command that lacks `--dry-run`.

## 0.8.2

BUG FIXES:

* **A new folder goes where the server keeps folders** ([#116]). On a
  server using `.` between folder names, mailctl guessed that a folder
  that does not exist yet belonged under `INBOX`, so `--create-folder`
  planned `INBOX.Receipts` on an account whose folders all sit at the top
  level. It now asks the server where personal folders live (IMAP
  `NAMESPACE`) and puts a new folder there. The guess is kept only for a
  server that does not answer. Folders that already exist are found
  exactly as before.

* **The "may never run" warning no longer fires on every rule that shares
  a header with an earlier `allof`** ([#117]). An earlier rule that needs
  several conditions at once was reported against any new rule testing
  one of its headers, whatever the values. It is now reported only when
  every one of its conditions could hold on the same message: a new rule
  on a different From address than the earlier rule's is no longer
  flagged. The definite "never runs" finding is unchanged.
* **A default rule name keeps its accented and non-Latin letters**
  ([#118]). `mailctl add --subject "Café"` named the rule `subject-caf`;
  it is now `subject-café`, and a CJK subject keeps its characters too.
  The name reads back unchanged from the script's `# rule:[...]` marker.
* **`mailctl list` no longer prints a blank line under the only script**
  ([#119]). The ManageSieve library read a stray line break in the
  server's script listing as a script with an empty name; `list` showed
  it as a line of two spaces while `mailctl test` said there were no
  other scripts. An empty name is now dropped where the listing is read,
  so both commands agree.

## 0.8.1

ENHANCEMENTS:

* **`none` clears `disabled_extensions` for one run** ([#85]). An empty
  value falls through to the next source, so a config-file list could not
  be overridden with nothing. `--disable-extension none`, or
  `MAILCTL_DISABLED_EXTENSIONS=none` in the env file or the environment,
  now means disable nothing, and `mailctl test` names where it came from.
  `none` beside an extension name is refused.

BUG FIXES:

* **The `redirect` refusal and `mailctl test`'s note date MXroute's
  announcement correctly** ([#102]). MXroute's post *"Why we disabled
  redirect sieve filters on MXroute"* is dated 2024-03-22; mailctl said a
  day earlier.

* **A connection that drops mid-response no longer hangs `mailctl`**
  ([#95]). If the ManageSieve server closed the connection partway
  through an answer, every command except fetching a script waited
  forever with no output. It now stops with an error saying the server
  closed the connection.
* **A sender or subject written in raw UTF-8 is shown as sent** ([#97]).
  `mailctl messages`, `mailctl view`, and the message preview before a
  filter is derived showed a non-ASCII address such as `zoë@exemple.fr`
  as replacement characters. They now show the text, still escaping any
  control character in it. A header that is not valid UTF-8 reads as
  before.

## 0.8.0

NOTES:

* **The provider layer no longer assumes Sieve on MXroute** ([#99]). The
  records the engine speaks are the provider layer's own rather than the
  Sieve and IMAP libraries'. What `mailctl test`, a diff heading, and a
  rule summary say about the host now comes from the provider. A rule's
  `stop` default follows what the provider can do. Help offers only the
  options the selected provider supports, and `disabled_extensions` is
  accepted only by a provider that has extensions. For MXroute nothing
  changes: every command prints, accepts, and helps exactly as before.

## 0.7.1

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
[#5]: https://github.com/harleypig/mailctl/issues/5
[#158]: https://github.com/harleypig/mailctl/issues/158
[#159]: https://github.com/harleypig/mailctl/issues/159
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
[#99]: https://github.com/harleypig/mailctl/issues/99
[#85]: https://github.com/harleypig/mailctl/issues/85
[#102]: https://github.com/harleypig/mailctl/issues/102
[ADR 0006]: adr/0006-two-layer-component-and-provider-architecture.md
[#95]: https://github.com/harleypig/mailctl/issues/95
[#97]: https://github.com/harleypig/mailctl/issues/97
[#116]: https://github.com/harleypig/mailctl/issues/116

[#117]: https://github.com/harleypig/mailctl/issues/117
[#118]: https://github.com/harleypig/mailctl/issues/118
[#119]: https://github.com/harleypig/mailctl/issues/119
[#129]: https://github.com/harleypig/mailctl/pull/129
[#130]: https://github.com/harleypig/mailctl/pull/130
[#133]: https://github.com/harleypig/mailctl/issues/133
[#134]: https://github.com/harleypig/mailctl/issues/134
[#135]: https://github.com/harleypig/mailctl/issues/135
[#137]: https://github.com/harleypig/mailctl/issues/137
[#154]: https://github.com/harleypig/mailctl/issues/154
[#150]: https://github.com/harleypig/mailctl/issues/150
[#101]: https://github.com/harleypig/mailctl/issues/101
[#152]: https://github.com/harleypig/mailctl/issues/152
[#18]: https://github.com/harleypig/mailctl/issues/18
[#19]: https://github.com/harleypig/mailctl/issues/19
[#153]: https://github.com/harleypig/mailctl/issues/153
[#155]: https://github.com/harleypig/mailctl/issues/155
[#147]: https://github.com/harleypig/mailctl/issues/147
[#7]: https://github.com/harleypig/mailctl/issues/7
[#49]: https://github.com/harleypig/mailctl/issues/49
[#9]: https://github.com/harleypig/mailctl/issues/9
[#148]: https://github.com/harleypig/mailctl/issues/148
[#149]: https://github.com/harleypig/mailctl/issues/149
[#151]: https://github.com/harleypig/mailctl/issues/151
[#168]: https://github.com/harleypig/mailctl/issues/168
[#175]: https://github.com/harleypig/mailctl/issues/175
[#157]: https://github.com/harleypig/mailctl/issues/157
[#160]: https://github.com/harleypig/mailctl/issues/160
[#51]: https://github.com/harleypig/mailctl/issues/51
[#109]: https://github.com/harleypig/mailctl/issues/109
[#183]: https://github.com/harleypig/mailctl/issues/183
[#21]: https://github.com/harleypig/mailctl/issues/21
[#39]: https://github.com/harleypig/mailctl/issues/39
[#188]: https://github.com/harleypig/mailctl/issues/188
[#192]: https://github.com/harleypig/mailctl/issues/192
[#196]: https://github.com/harleypig/mailctl/issues/196
[#10]: https://github.com/harleypig/mailctl/issues/10
[#219]: https://github.com/harleypig/mailctl/issues/219
[#216]: https://github.com/harleypig/mailctl/issues/216
[#205]: https://github.com/harleypig/mailctl/issues/205
[#208]: https://github.com/harleypig/mailctl/issues/208
[#212]: https://github.com/harleypig/mailctl/issues/212
[#204]: https://github.com/harleypig/mailctl/issues/204
