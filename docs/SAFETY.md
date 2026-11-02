# Safety

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
  stale (see [*Mark messages*][mark]).
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

[adr5]: ../adr/0005-restore-may-replace-an-unparseable-script.md
[mark]: USAGE.md#mark-messages
