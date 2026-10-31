# mailctl Test Layout

The global `testing.md` carries the bar (success **and** failure paths, a
regression test per bug, a manual verification note per feature); `python.md`
carries the layout convention (`tests/` at the repo root, mirroring the
package). This file records what belongs here.

**The offline tier is written and green** (`make test`); the only skips are
the live- and container-gated ones. `pytest -q` reports the current count;
none is kept here, because a count nobody re-derives is only ever stale.
The live tier is scaffolded (`tests/live/`) and skipped by default; it stays
open until it has run against a real account. The write guards it needs
before anything writes to one are built and proved on the container tier
([#9](https://github.com/harleypig/mailctl/issues/9)); nothing has yet
written to the real account. The container tier
(`tests/container/`) is written and green against a local server; it is
where every write path is proved first. The TUI's tests are a separate
suite with their own configuration, run by `make testtui` and never by the
root `pytest` (*The TUI*).

## Three tiers

1. **Unit tests** (`tests/test_*.py`) — **offline**, no network, no
   credentials. The default gate, and the tier that should hold almost
   everything. The package was split specifically so this tier can reach the
   interesting logic:
   - `criteria` — a criteria set translated to Sieve **and** to IMAP `SEARCH`,
     including the cases where the two differ and the client-side re-check
     closes the gap; criteria from a message merged with flags; and the
     filter document round-tripping, holding nothing a terminal acts on,
     and refusing each malformed shape by name.
   - `sieve` (`test_sieve.py`, through the MXroute dialect) — parse an
     existing script, merge a rule into it, render it back, and confirm
     rules the tool did not write survive — their bodies and names, with
     the layout normalized. The overwrite-would-have-destroyed-it case is
     the one that matters
     ([ADR 0002](../adr/0002-non-destructive-script-merge.md)); it deserves a
     test with a hand-written Roundcube-shaped script as its fixture.
   - `sieve` — a refused action (`redirect`, `notify`, `vacation`) raises with
     the pointer message rather than emitting the action.
   - `sieve` — a rule renamed in place ([#216][i216]): the script's bytes
     change only in the name marker, CRLF, comments, and a disabled rule
     included; the marker sievelib reads as the name is the one rewritten;
     and each name the marker cannot carry is refused.
   - `imap` (`test_imap.py`) — folder-name normalization across both
     separators (`Lists/GitHub` ≡ `INBOX.Lists.GitHub`) against a reported
     delimiter, and the session over the `fake_imap` double built from
     plain parameters. A connection that does not log in, its login
     refused or its password prompt stopped, is shut down and not logged out
     ([#349][i349]); one whose `LIST`, `LSUB`, or namespace read fails
     after login is logged out, the failure raised as it was, and a LOGOUT
     that fails still shuts the socket ([#362][i362]). What MXroute and
     `Config` add — the port-to-TLS rule and the login and connection
     advice — is `test_mxroute_imap.py`; server-module selection by `ID`
     is `test_imap_servers.py`.
   - **Non-ASCII search** (`test_imap_search.py`) — a non-ASCII subject,
     from address, and list-id, each flat and inside an `OR`, run through
     IMAPClient's **real** `search` serializer over a recording transport,
     because the double records a key without ever encoding it. Each value
     must reach the wire as one whole UTF-8 literal under `CHARSET UTF-8`
     with its parentheses balanced, and the re-check must still match the
     decoded text ([#89][i89]).
   - **Folder counts** (`test_imap_status.py`) — the `STATUS` lines of one
     `LIST-STATUS` read into counts: quoted, atom, literal, all-digit, and
     modified UTF-7 mailbox names, and a line that does not parse refused.
     Run twice, on imaplib's shapes and from a server's bytes through
     imaplib's **real** reader, since only the reader turns a literal name
     into the pair the parser must handle ([#157][i157]).
   - **Server alerts** (`test_imap_alerts.py`) — an `ALERT` read out of
     every kind of status response, tagged and untagged, and none out of a
     line without one; the session handing each to `progress` during a
     command, at a refused login, and in the greeting on implicit TLS, but
     nothing sent before STARTTLS; and the transport handing over the
     neutral model's `ServerAlert`, not the component's. Run again over
     IMAPClient and imaplib themselves reading a scripted server's bytes,
     since the session wraps a private of imaplib and only the real
     library shows the wrapping still sees every line ([#205][i205]). The
     CLI's half — on stderr with or without `--verbose`, the same text once,
     control characters escaped, stdout left to `--json` — is the
     `folders-alert*` snapshots.
   - `config` — the flag → env → file → default resolution order, and that a
     `Secret` renders `<redacted>` from `str()`, `repr()`, and an f-string.
     `MAILCTL_BACKUP_ROOT` ([#230][i230]): a backup directory outside it
     refused from every rung and one inside accepted, neither `..` nor a
     link leading out, and the root taken from the environment only, never
     from an env file. The utilities' half, `filterset backup --output`
     included, is `test_utilities_backup.py`.
   - **The utilities** (`test_utilities_<module>.py` for `rules`,
     `scripts`, `backup`, `baseline`, `flags`, `folders`,
     `folder_rename`, `mail`, `messages`, `optimize`, `senders`,
     `reports`, `server_report`, and `uids` under `mailctl/utilities/`; a
     backup's bytes on disk are `test_backup.py`, and the migration utility
     `test_migration.py`) — every plan and execute step driven with plain
     inputs over a session, as any front-end would call it. The safety
     policy is pinned here, since it lives here: an upload backs up first
     and still leaves the backup when the server rejects the script, a
     rule is merged rather than written over, and a mail plan over the
     `--max-messages` ceiling is refused whole. `test_utilities_rules.py`
     also holds `describe_rules`: what each read rule does, as the
     dialect's neutral steps, one entry per rule in its order
     ([#259][i259]). `test_utilities_mail.py`
     also holds the re-check narrowing the host's search to what the rule
     matches, and a rule that keeps its mail copying it rather than moving
     it ([#188][i188]), with a second run copying only what the folder
     lacks, by Message-ID, and reading the folder only while planning
     ([#192][i192]). `test_utilities_server_report.py` pins the redaction:
     servers echo a sentinel address, host, IPv4 address, folder, and
     script name back, and none of them, the password, or the script's
     text may reach the report ([#39][i39]). `test_utilities_baseline.py`
     refuses a save, coded `baseline_changed` and writing nothing, where
     the file changed after the plan ([#279][i279]).
     `test_utilities_backup.py` refuses a restore of a password file from
     any rung or of the env file, unopened and links followed, coded
     `credential_file`, and names a file it cannot read as given, never
     expanded. `test_backup.py` holds that a backup never replaces a file
     ([#330][i330]): two uploads in one frozen second leave two files,
     named apart by the millisecond; a file planted at the name is kept
     and the backup goes to `-2`, into the default directory or a named
     one; `--output` onto an existing file is refused, exit 1, the file
     kept; and a parent that is a file still fails as a write, never
     read as a taken name. A baseline still replaces its file.
     `utilities/server_test.py` has no file of its own; *The API* covers
     it through `server.test`. Refusals once uncoded are pinned to their
     codes ([#331][i331]): a named backup file that exists refused by the
     plan, `backup_exists`, so `--dry-run` promises no write the run
     refuses; a mark plan with nothing to set or clear, `no_marks`; what
     is not a probe document, `probe_invalid`; an unsubscribed folder
     that is not created, `subscribe_needs_create`; and a migration whose
     destination appeared mid-move or whose move the filesystem refused,
     `migration_appeared` and `migration_failed`. The folder rename's
     read-back names the operation that fixes a failed check where there
     is one: `folder rename` back for a filter left on the old name
     ([#332][i332]), `folder rename` again for every folder and
     subscription check where the rename never happened, `folder
     subscribe` for each folder left unsubscribed, the new name or one
     under it ([#350][i350]), and `folder unsubscribe` for an old name
     still subscribed; a check mailctl cannot fix says so in its detail,
     and a read that failed carries the server's answer.
     `test_utilities_uids.py` pins the UIDVALIDITY check ([#204][i204]): a
     stale pin given to `mail mark`
     refused before any write, and before its flags are read; `mail view` and
     `--like` refusing one, a UID lost in the renumbering included; a plan's
     own value checked again at execute; and reading it costing no SELECT
     of its own. A UID the folder does not hold is refused coded
     ([#341][i341]): `criteria_like` in `test_utilities_mail.py` and
     `read_message` in `test_utilities_messages.py` each refuse it as
     `no_such_message` with the folder and the UIDs, after one fetch, and
     `test_utilities_uids.py` gives it the same code under a current pin.
     A session's checkpoint stops the work between exchanges
     ([#280][i280]): a mail plan stopped after its search reads no
     headers, and an execute stopped before its first write moves nothing
     (`test_utilities_mail.py`); a listing and a count stopped at a
     checkpoint fetch no further page (`test_utilities_messages.py`,
     `test_utilities_senders.py`); and an upload stopped before its first
     write stores nothing (`test_utilities_scripts.py`).
     A filter set changed since the plan ([#257][i257]):
     `test_utilities_scripts.py` takes every kind of script plan (add,
     remove, move, switch, rename, restore) and refuses each, coded
     `filterset_changed`, with nothing sent and no backup of the plan's
     copy, lets each through unchanged with the server read before the
     upload begins, and refuses a filter set created since a plan made
     from none; `test_utilities_optimize.py` refuses a rearrangement, and
     `test_utilities_folder_rename.py` stops a rename before any write, the
     code passed through unwrapped. An active filter set switched since
     the plan ([#308][i308]) is refused the same way, unsent, for every
     kind of script plan, for a rearrangement, and for a rename; so is a
     set activated since a plan made with none active, and a set planned
     as not running that another client has since activated. A rename
     with no filter to repoint is held to both ([#332][i332]): a set
     changed or switched since stops it before the folder moves, and an
     unchanged one still renames. The fakes they share are
     `tests/utilities_support.py`.
   - **The session** (`test_engine.py`) — only what is about the
     connection: a half opens the first time a utility uses it and never if
     the command did not ask for it; a read the server cut off is sent once
     more on a fresh connection, and a second failure or a refusal is
     raised; a write cut off mid-flight is raised and **never sent again**,
     on both halves; calls on one connection wait their turn under the
     session's lock; two sessions share nothing; and every transport
     operation is classified, with each operation's kind pinned so a change
     of kind is a deliberate diff. The idle preflight ([#258][i258]): each
     half has one probe and it is a read on that half; a write on a half
     idle past `IDLE_PROBE_AFTER` sends the probe first (`NOOP` on mail,
     the script listing on rules), and one soon after an exchange, a half
     not yet connected, or a second write in a row sends none; an idle half
     the server dropped reconnects before the write, and a probe the server
     refused stops it. The checkpoint ([#280][i280]): asked before every
     operation, saying which is a write; an operation it stops is neither
     sent nor connected for, and what it raised reaches the caller; and an
     idle probe before a write is asked about after the write, so a
     front-end that stops nothing once a write is under way does not stop
     its probe either.
   - **Sieve extensions** (`test_extensions.py`) — the emit table checked
     against sievelib's `require` line over every rule shape, and
     `disabled_extensions` refusing, falling back, and doing nothing where
     the server lacks the extension anyway. Its ladder is in
     `test_config.py`.
   - **The old name** (`test_migration.py`) — `config migrate` moving the
     old config directory (modes kept, `--dry-run` inert), refusing with
     nothing moved a clash (`migration_conflict`), a plan the directories
     no longer match (`migration_changed`), and a `config.toml` path only
     a variable reaches (`migration_reference_unrewritable`), while one a
     variable merely spells keeps it as written,
     the old-directory warning and when it stays silent, and old
     `MXROUTE_*` names reported by name — checked against a sentinel value
     that must never appear in any output. The `mxfilter` script's reuse is
     in `test_utilities_rules.py`.
   - **CLI snapshots** (`test_cli_snapshots.py`) — each subcommand run end to
     end through `cli.main`, recording exit code, stdout, stderr, and every
     server call into `tests/snapshots/cli/<name>.txt`. A behaviour change
     shows up as a snapshot diff; regenerate with
     `MAILCTL_UPDATE_SNAPSHOTS=1` and read the diff before committing.
     Both sides are compared after `argparse_neutral()`: a usage block by
     its words rather than where argparse wraps it, and an invalid-choice
     list unquoted, since both vary with the Python version and neither is
     mailctl's text ([#231][i231]). Which options a usage names, and in
     what order, still has to match, and regenerating leaves a snapshot
     alone when only that text differs. `tests/conftest.py` clears
     `PYTHON_COLORS` and `FORCE_COLOR` and sets `NO_COLOR` for every test,
     since argparse colours its output from 3.14 when asked, pipe or not.
     It also pins `COLUMNS` to 80, since argparse wraps help to an exported
     `COLUMNS` and the snapshots are recorded at 80 ([#241][i241]).
   - **The command tree** (`test_cli_help.py`, over `tests/cli_support.py`,
     which reads the groups and commands off the parser) — `mailctl help` with
     a group, or a group and an action, is the same text and exit as `--help`
     there, for every one; the groups are listed in their decided order; and
     every old flat name is refused, exit 2, naming the command it is now,
     wherever it is the command word and never as an argument ([#219][i219]).
     An unknown command or action is refused listing the choices, never
     echoing the word given ([#364][i364]).
   - **`--json`** (`test_json_output.py`) — a `Secret` refused rather than
     serialised, failures as one JSON line, and which commands offer it.
     The mappings the API shares ([#258][i258]): an error carrying its
     fields as plain JSON, and only when given, with a `Secret` among them
     still refused; an unexpected failure named by its type, never its
     text; the capabilities naming every field the provider declares; a
     document for every event type; an alert tagged with its channel in
     the user-facing noun; what the rename left behind as names and
     places, with null for no old directory ([#307][i307]); an
     execute's result naming what it did and where the backup is, with
     `filter` null for a change to the whole filter set ([#279][i279]);
     the existing-mail result counting what was flagged, moved, deleted,
     and copied, with source and destination; a filter set shown as its
     text, `filters` null where it does not parse; a backup result saying
     where, and whether it was written; a folder creation result keeping
     a failed subscription apart; a subscription result naming the folder
     as the server does; a folder rename result carrying each check with
     the `operation` that fixes a failed one, and naming no filter set
     where no filter was repointed; and
     each filter's `steps` beside its unchanged `actions` ([#259][i259]),
     every step with its `kind`, `argument`, and `creates`, the kind in
     the document's word and never the model's, and every kind the
     `mxroute` dialect reports (read off its table, not listed) with a
     word of its own. A command line refused before any command runs
     ([#352][i352]): under `--json`, before or after the fault, the same
     exit 2 and the same lines as without it, then one coded line —
     `invalid_arguments`, `gone_command`, `gone_option`, `setting_invalid`
     for a bad connection flag, and a refusal of the CLI's own; a refusal
     the CLI makes before the core can carrying the core's code; no JSON
     without `--json`, which is read only before `--`; and no option taken
     by a prefix. No refused command line quotes a word argparse could not
     use ([#364][i364]): every shape a password takes into one — an option
     mistyped, spaced, with `=`, or short and attached; a password option
     where none is taken; a word after an option or after `--`; a value
     for an option that takes none or another kind — is on neither the
     error line nor the `--json` line, exit 2 and the usage kept; each
     template that quotes a value drops it and keeps the names; a message
     naming only arguments is kept as argparse wrote it; and one of no
     known shape reads `json_output.REFUSED`, alone and through the CLI on
     both lines.
   - **The API** (`test_api.py`, [#258][i258]) — driven across a real
     process boundary by a plain line-reading client
     (`tests/api_support.py`), as a client in any language would drive
     it. It starts `tests/api_child.py`, the real API with the two protocol
     clients replaced by doubles that keep their state in a file the test
     can change between a plan and its execute, and holding only the
     password's SHA-256. The state file also counts each ManageSieve
     connection (`sieve_connects`) and IMAP login (`imap_connects`), so a
     test sees which halves a method opened, and each IMAP connection
     made (`imap_opened`) and closed (`imap_closed`), so it sees how many
     are still open ([#349][i349]); where its `unreachable` is set, makes
     either server refuse the connection, or fail its TLS handshake where
     it is `"tls"` ([#352][i352]); records what the IMAP double
     is asked to write (`imap_writes`); reads UIDVALIDITY afresh on every
     SELECT, so a folder can be renumbered between plan and execute;
     refuses CHECKSCRIPT where its `checkscript` is false; advertises its
     `imap_caps`; and, where its `hold` counts a double's call (`fetch`,
     `create_folder`, `getscript_bytes`, `checkscript`, `putscript`),
     notes it in `held` and holds it until the API has a line from the
     client it has not yet read, so a test knows a request was mid-way
     when its cancel arrived ([#280][i280]). `MAILCTL_TEST_DECLINE` names
     capabilities the provider is to declare false. Covered: `initialize`
     connecting nothing, refusing a bad setting and a second
     `initialize`, and a method before it;
     a refused setting reaching the client coded (`unknown_provider`, a
     bad port from the environment as `setting_invalid` with its
     setting, `password_file_mode`); a bad argument answering
     `initialize`, coded, rather than ending the input, taking no option
     by a prefix ([#352][i352]), and said on stderr in the same words,
     with a sentinel in neither ([#364][i364]); a server that cannot be
     reached refused on either half as `connection_failed`, or
     `tls_failed`, with the advice in `fields` ([#352][i352]);
     `initialize` naming what the rename left behind ([#307][i307]) — an
     old setting from the env file, one from the environment, and an old
     config directory with no new one — each with a sentinel (the old
     `MXROUTE_PASSWORD`'s value, or text in the old `config.toml`) on no
     line of stdout and nowhere on stderr, under `--debug`;
     `shutdown` and the end of input both ending the process; what is not
     a request answered as the specification says; `filter.list`,
     `mail.search`, and `mail.view` returning their `--json` documents and
     refusing what the utilities refuse, with `filter.list` carrying each
     filter's neutral `steps` and its `actions` unchanged ([#259][i259]);
     `mail.search`'s `like` ([#259][i259]) finding mail like a message,
     merged with a `filter` as the CLI's `--like` merges them, and
     refusing a UID from a renumbered folder, with `build_filter` or
     without, before the UID is used; `build_filter` returning the filter
     document `filter.add.plan` takes, listing and saving nothing; and a
     UID below 1 (`uid_below_one`) or missing (`no_such_message`,
     [#341][i341]), an unknown `like` key, empty criteria, and each
     combination the CLI refuses all refused; a plan that writes
     nothing, runs once, sends its events before its answer, and is
     refused once
     discarded, or at execute once the filter set changed; a plan left
     waiting past `PLANS_KEPT` dropped oldest first, refused
     `plan_unknown`, while the next oldest still runs ([#346][i346]);
     and `log` only where `initialize` asked, `server.alert` always and
     once per session.
     Every other command's method ([#279][i279]), each opening only the
     halves its CLI command opens: the `filter` writes (remove, move,
     rename, enable, disable, optimize) planned, then stored once behind a
     backup, with `optimize`'s `skip` obeyed and a taken name refused;
     `filter.apply` moving the mail once, creating its folder at execute
     with no ManageSieve call and saying so first, and refusing a missing
     folder, a pass over its ceiling, and a folder renumbered since the
     plan — beside it the `apply-create-dry-json` snapshot pins the CLI's
     plan the same way; `filterset.list` and `.show` reading no mail, and
     `.show` returning text that does not parse; `filterset.backup`
     writing the server's bytes, nothing on `dry_run`, and nothing outside
     the backup root; `filterset.restore` backing the set up first, not
     storing what CHECKSCRIPT rejects, expanding nothing in `file`, and
     refusing a credential file unread; the `folder` group listing,
     counting, creating, subscribing, and renaming with its filters
     repointed; `mail.mark` and `mail.senders` on the mail half only;
     `server.test` and `server.probe`, a baseline that cannot be compared
     reported and the test going on, and the report redacted;
     `server.baseline.save` writing once and refusing a file changed since
     the plan, `show` and `check` reading it, `no_baseline` coded; and
     `config.migrate` moving the old directory once, refusing a clash, a
     plan a file or a baseline since clashes with, and a variable expanded
     into a `config.toml` path. The server group never sends the password.
     Across them: an execute of a plan that changes nothing refused,
     `nothing_to_change`, while `filter.add.execute` still carries it out;
     a plan running only under its own command's execute; a bad execute
     parameter refused without using up the plan, for every execute of the
     `folder`, `filterset`, `mail`, and `server` groups; a command the
     provider does not declare refused before anything is read; and the
     stale-plan refusals, a changed filter set and a switched active one,
     for `filter.add` and the `filter` remove, move, rename, enable,
     disable, and optimize, `filterset.restore` over a set that parses and
     one that does not, and `folder.rename` with and without a filter to
     repoint ([#332][i332]).
     The password bar: a sentinel given through `credentials.prompt` logs
     in on both halves and appears on no line of stdout and nowhere on
     stderr, `--debug`'s tracebacks included; a login the server refuses,
     on each half, coded `auth_failed` and quoting nothing; a non-ASCII
     password logging in on ManageSieve and refused on IMAP as
     `credentials_not_ascii`, with neither the password nor the encoding
     error in any output; a declined prompt, a reply that does not parse,
     and an answer holding no password each refused; the prompt asked again
     after a decline or a refused login, never for a refused password from
     a file; and a request sent while the prompt waits answered after it.
     Parameters: an action the provider will not emit, criteria a delivered
     message cannot meet, a malformed parameter, and a plan's own content
     sent to its execute are each refused, every value refusal with a code.
     Each refusal the utilities make reaches the client coded, with the
     fields a client needs ([#331][i331]): the `mail.mark`, `mail.view`,
     `mail.search`, and `mail.senders` refusals, the `folder.rename` and
     `folder.create` ones, an empty filter name, `filter.optimize.plan`'s
     unknown `skip` kind as `unknown_kind` rather than a JSON-RPC
     parameter error, a backup file that exists or cannot be written or
     read, an empty filter set to edit, no active one to show, and a
     baseline that cannot be read or named; and `mail.view`'s and
     `mail.search` `like`'s UID the folder lacks, `no_such_message` with
     `build_filter` or without, and `like`'s UID below 1, `uid_below_one`
     ([#341][i341]).
     Cancelling a request ([#280][i280]): a long search held on its first
     page and cancelled stops, answered `-32800`, and the session goes on;
     requests read while one runs are answered after it, in order; one
     cancelled while it waits its turn never runs; a cancel for an unknown
     or finished id, or naming none, is ignored, and one sent as a request
     answered null; an execute cancelled before its first write sends
     nothing, and one cancelled after its backup, held on CHECKSCRIPT,
     names the backup and stores nothing; an execute or a `filter.apply`
     pass cancelled after its first write runs to its end; a cancel while
     `credentials.prompt` waits ends the wait and closes the IMAP
     connection made before it, a late answer to it answers nothing, and
     the next request asks again on a fresh one ([#349][i349]); and a
     password command gets no input while the client's next request is
     still answered.
     And two structural checks. The method set matches the command tree
     both ways: every method but the protocol's own is a CLI command
     path, a `.plan` with its `.execute`, and every command has a method
     unless `NOT_IN_THE_API` names it with its reason (only `help`). And
     the API takes the CLI's connection settings except `--password`.
   - **The API's framing** (`test_api_protocol.py`) — what is easier to pin
     in-process: every string `password` a `Secret` as the line is parsed,
     a line that does not parse refused without quoting it, each message
     one ASCII line and never carrying a credential, messages arriving
     while a server-to-client request waits received after it in order, a
     line that does not parse while an answer waits taken as a decline,
     no parameter refusal naming the value, no method taking a password,
     another command's plan refused and left waiting, and a result that
     cannot be sent answered as an internal error with serving going
     on. The input thread and the cancel ([#280][i280]): `take` picking
     out each cancel with what is held ahead of it and leaving the rest in
     order, a bad line keeping its turn; a cancel already held, or
     arriving while a server-to-client request waits, ending the wait,
     with what came before it received after; lines split across reads
     arriving whole, a last line with no newline counted; a late cancel,
     met in its turn or taken by the next request's checkpoint, sparing a
     request sent after it that reuses the id; and a cancel landing just
     after a checkpoint read the input still ending the password prompt.
     The input's bound and its end ([#346][i346]): a client flooding the
     input read no further than `INBOX_LIMIT`, `take` included; `close`
     stopping a thread waiting for room, and not waiting on a read it has
     no descriptor to wake; a password prompt waiting with the limit held
     declined rather than hung; a declined bad line, and a cancel ending a
     wait, held or not, each making room; an input descriptor of 1024 or
     more still read, since the wait polls rather than selects; and no
     input thread left once `run` returns, even where closing the session
     failed.
   - **The ManageSieve wrapper** (`test_managesieve_client.py`) — the real
     `SieveClient` against a scripted socket that replays a server's bytes:
     GETSCRIPT byte-exact whatever the script ends with ([#90][i90]), the
     whole CAPABILITY response kept, the read timeout honoured, and nothing
     printed even with sievelib's debug flag forced on; an OK's `WARNINGS`,
     quoted or literal, reaching `progress`, and a literal read whole
     ([#208][i208]); the CLI's half is the `add-warnings*` snapshots. A
     script sent by PUTSCRIPT and CHECKSCRIPT unchanged, a name escaped
     alike by GETSCRIPT and every other command, a name holding CR, LF, or
     NUL refused unsent, and sievelib still having the formatters the
     client replaces — all of it under sievelib 1.5 and 1.6, which CI runs
     both of (`Test on the oldest sievelib allowed`, [#232][i232]). A
     refused login or STARTTLS, a greeting cut short, and a server
     offering no SASL each close the socket once, send no LOGOUT, and
     raise their own code; a connection that cannot be made closes
     nothing; and `close` sends LOGOUT and then closes the socket, whether
     or not the LOGOUT is answered ([#362][i362]). Its
     server-module selection and capability parsing are
     `test_managesieve_servers.py`.
   - **Packaging** (`test_packaging.py`, [#229][i229]) — a wheel built from
     a copy of the source with the declared build backend, unpacked, and
     run with nothing from the checkout on the path, so a subpackage left
     out of it fails here rather than in a user's install. The wheel
     declares both commands, and its `mailctl-api` entry point runs
     ([#258][i258]). The suite otherwise runs under an editable install,
     which reads the checkout.
   - **`make typecheck`** (`test_make_typecheck.py`, [#244][i244]) — the
     target run from a copy of the Makefile alone, with a stand-in `pyright`
     on `PATH`: refused, naming `.venv/bin/pyright`, with no `.venv` and
     with one that lacks pyright; running the venv's pyright rather than
     `PATH`'s; and failing when pyright fails.
   - **`make install` and the test targets** (`test_make_install.py`,
     `test_make_pytest.py`, [#262][i262]) — the same shape, from a copy of
     the Makefile alone. `make install` with a stand-in `uv` that refuses
     `uv venv` over an existing `.venv`, as uv does: installing into one
     that exists, making one where none does, and failing when the install
     fails. `test`, `testlive`, and `testcontainer` with a stand-in
     `pytest` on `PATH`: each refused, naming `.venv/bin/pytest`, with no
     `.venv` and with one that lacks pytest; each running the venv's
     pytest with its own arguments and gate, passing `TESTARGS` through
     ([#294][i294]); and each failing when pytest fails.
   - The **presentation guard** (`test_core_no_presentation.py`) — no core
     module prints, prompts, or exits, and neither the session nor any
     utility imports a front-end or reads the terminal or the environment.
     `components/`, `providers/`, and `utilities/` are walked, not listed.
     No string a core module builds names a CLI flag (f-strings included;
     each allowed exception carries its reason), and every error code the
     core raises has a rendering in `cli.ERROR_TEXT`, and every rendering
     a raiser ([#51][i51]). Nor does one name a CLI command: `mailctl`
     followed by any of the parser's own commands, or by an interpolated
     value, is refused, so the tool's name in prose is not. Every
     operation a core error or record names in its place is one of those
     commands ([#183][i183]). Every call in every module of the package,
     the interfaces included, building a `MailctlError` or a subclass of
     it passes a `code`, and not a written `None` ([#331][i331],
     [#352][i352]); errors planted in a component, a provider, and the
     CLI are each found, so the walk reaches beyond `utilities/`. It reads
     names in the source, so it takes a code passed through `**` or from a
     variable on trust (a variable that is `None` at run time escapes it),
     and does not see one built through an alias of its class. Each guard
     carries a planted case it is seen to catch.
   - The **layer-purity guard** (`test_layer_purity.py`) — the four layers
     of [ADR 0007][adr7] over the components of [ADR 0006][adr6], held line
     by line:
     - a module under `components/` imports only the stdlib, the library
       its own component wraps (`imapclient` for `imap`, `sievelib` for
       `managesieve`), other components, and `MailctlError`;
     - `engine.py`, every module under `utilities/` (walked, not listed),
       and `cli.py` import no component, and the session and the utilities
       reach a provider only through `providers.base` and
       `providers.registry`, with no provider's name in their code;
     - `providers/base.py` and `providers/model.py` import no component
       either, and every model type is the neutral model's own class, not
       one borrowed from layer 1 ([#99][i99]);
     - each provider's two halves are derived from its dialect and
       transport classes by following their imports: no module is on both
       sides, a dialect imports nothing that opens a connection, and a
       transport imports no building helper and no utility;
     - the **interface guard**: a front-end imports only the utilities, the
       session's opening calls, and the neutral model — never a provider, a
       component, or the session's transport, connection, or dialect;
     - a utility reaches the transport only through the session it was
       handed;
     - the **read/write guard** ([#154][i154]): a read-only utility never
       reaches a transport operation classified write ([#135][i135]).
       Read-only is derived, not listed: every function under
       `utilities/` except an `execute_*` one and the named write steps
       an execute is built from (`create_folder`, `realize_folder`,
       `upload_script`), each of which must still be seen to write. The
       check is static, so every branch is seen, and reach is followed
       through other utilities to a fixed point.

     The interfaces are derived as every module outside the core, so the
     API is under the interface guard unlisted; a test pins that the walk
     reaches `api/`, and violations planted in `api/server.py` (a provider
     or a component imported, an engine call beyond the opening ones, the
     session's transport reached) are each caught, its relative imports
     resolved from `mailctl.api` ([#258][i258]).

     The component, session-and-utilities, neutral-model, half, and
     read/write guards each carry a case built to break them, so each is
     seen to fail as well as pass.
   - **Neutral wording** (`test_neutral_wording.py`, [#219][i219]) —
     under `mxroute`, no provider word (Sieve, IMAP, MXroute, Roundcube,
     CHECKSCRIPT, fileinto, Exim, DirectAdmin, script), and no *rule*
     outside a quoted name, in any help page
     read off the parser, outside the `server` commands, the
     `--disable-extension` help, and the connection settings the provider
     declares; and none in what any snapshot's command prints, outside the
     server reports, the filter set's own text (a diff, `filterset show`),
     `--verbose`'s protocol log, a server's alert or warning, a JSON
     document's keys, and a refusal about an extension or a forbidden
     action. Option names and metavars are taken out first. Each walk is
     seen to reach what it allows, each exception to be needed, and a
     planted word, and each line printed before #219, to turn it red.
     A filter's step `kind` is our word inside a JSON document, so it is
     held all the same ([#259][i259]): every kind any snapshot carries is
     free of provider words, the walk is seen to reach `file` and `stop`,
     and a planted `fileinto` kind is caught.
   - **Providers** (`test_providers.py`) — each half of every registered
     provider implements or explicitly declines every operation of its
     interface (`Dialect`, `Transport`), and the two decline exactly what
     the capabilities say. The `provider` setting climbs the ladder with
     provenance; an unknown name is refused before anything connects. A
     fake second provider, registered for the test, is driven through the
     utilities' representative operations; its dialect and transport
     receive the same calls, in the same shape, as `mxroute`'s, and a
     coarse host search is narrowed by the utilities rather than the
     transport. A capability it declines is refused before it is opened.
     `mxroute` hands back the neutral records, never its components'.
     Fakes without a capability pin what that removes ([#99][i99]): without
     `stop` a default rule still plans; without `ordering` the placement
     flags, `filter move`, and `filter optimize` are not offered in help and
     are refused by name if given; without `extensions` `disabled_extensions`
     is refused; without `raw_query` `mail search --raw` is not offered in
     help and is refused by name, by the CLI and by the utility alike; without
     `disable` `filter disable` and `filter enable` are not listed and a
     switch is refused; without `rename` `filter rename` is not listed and a
     rename is refused; without `mark` `mail mark` is not listed and is
     refused; without `folder_counts` `folder list --counts` is not offered
     and is refused; without `uidvalidity` `--uidvalidity` is not offered and
     a pin is refused, while listing and reading still work; without
     `rule_sets` the `filterset` group is not listed, and its commands still
     parse and run; a connection flag the provider does not read is hidden and
     refused, coded `setting_not_read` ([#352][i352]); a `filterset`
     command is refused by the utility, naming the provider, before
     anything is read; without `folder_visibility` `folder
     subscribe` and `folder unsubscribe` are not listed and `--no-subscribe`
     is not offered, each refused by the utility before anything is read, a
     created folder is not subscribed, a rename carries and checks no
     subscription, and `folder list` and `server test` call no folder hidden
     ([#39][i39]); and `filter add` and `server test` under a fake say
     nothing about Sieve or MXroute. `mxroute`'s help hides nothing but the
     always-hidden flags.
2. **Live tests** (`MAILCTL_LIVE=1`) — stand up **real** Sieve scripts and
   move **real** mail against a **live MXroute account**. They mutate real
   state; run them manually (`make testlive`), **never** in a default gate.
   A test that writes also needs `MAILCTL_LIVE_WRITE=1` and the write
   guards (*Live-test credentials & safety*).
3. **Container tests** (`MAILCTL_CONTAINER=1`, `tests/container/`) — every
   **write** path against a **throwaway local Dovecot + Pigeonhole** in
   Docker, never a real account. See *The container tier* below.
   `test_image.py` sits under the same gate and builds mailctl's **own**
   image instead (*The container tier*).

The live tier and the read-only live check below are together this repo's
**end-to-end** pass (`qa.md` dimension 8) against MXroute itself. The
container tier sits between the offline tier and them: a real server, so
it shows what a double cannot (what the server *keeps*, and what a stored
rule *does*), but not MXroute, so it says nothing about MXroute's own
configuration.

## The container tier

`make testcontainer` builds a small image from `tests/container/image/` and
starts one container for the run; each test then gets its own empty mailbox
on it, so no test sees another's scripts, folders, or mail. It needs a
running Docker daemon and skips cleanly without one, or without
`MAILCTL_CONTAINER=1` exactly — its own gate, never `MAILCTL_LIVE`, so a
container run cannot be mistaken for an MXroute one ([#49][i49]).

- **What it proves.** `filter add` merging beside a Roundcube-written rule
  (ADR 0002), `filter remove`, `filter move`, `filter disable` then `filter
  enable` (a replaced disabled rule staying disabled), a fresh account's new
  active script, `filterset backup` then `filterset restore` byte for byte,
  `--create-folder` with and without `--no-subscribe` and the `folder
  subscribe` / `folder unsubscribe` toggles, `filter apply` moving, flagging
  and discarding existing mail, `filter apply --keep` copying it as the saved
  rule does, both copies flagged ([#188][i188]), and run again copying nothing
  twice ([#192][i192]), `--max-messages` refusing the whole pass, one filter
  document from `mail search --build-filter --json` driving both `filter add
  --filter` and `filter apply --filter`, `mail view` and the message listing
  leaving mail unread, `mail mark` setting then clearing read, flagged, and a
  keyword and refusing a UID the folder does not hold, and `mail search` and
  `filter apply --dry-run` selecting appended mail by body (a non-ASCII one
  included), arrival date, `--older-than`, and read or flagged state, alone
  and together ([#152][i152]). Its dates are counted from the day it runs, so
  it means the same whenever it does. Reads are here too: `probe --json`
  checked against what the server says about itself, and against printing the
  password; `server probe --report` finding nothing to report on this
  recognised server, and neither `server probe` nor `server test` calling it
  unrecognised ([#39][i39]); `folder list --counts --json` checked against
  each folder's own `STATUS`, leaving every message unread ([#157][i157]); and
  `mail senders` in `test_senders.py` ([#160][i160]), counting each address
  and its unread exactly, grouping by domain and List-Id, refusing above its
  ceiling, and leaving every message as it was. `mail search --sort` runs
  against Dovecot's own `SORT` in `test_search_sort.py` ([#159][i159]).
  UIDVALIDITY in `test_uidvalidity.py` ([#204][i204]): a folder deleted and
  made again gets a new value from Dovecot (asserted, not assumed), `mail
  search --json` reports the server's, and a UID pinned to the old value is
  refused by `mail mark` and `mail view`, leaving the message that now has it
  unread, while a pin to the current value marks it. An IMAP `ALERT` in
  `test_alerts.py` ([#205][i205]): the image's post-login script
  (`image/postlogin.sh`) sends one to a user whose name starts with `alert-`,
  and it reaches stderr, under `--json` too, with stdout untouched; any other
  user is sent nothing, so no other test meets one. A Sieve `WARNINGS` in
  `test_sieve_warnings.py` ([#208][i208]): Pigeonhole warns on `addflag
  "\\Bogus"`, and PUTSCRIPT's warning reaches stderr once. And baselines: one
  saved from the server checks clean against it, saving writes nothing there,
  and drift made by editing the saved file exits 3 or 4 as documented. `filter
  rename` in `test_rename_rule.py` ([#216][i216]): the script's bytes changed
  only in the two name markers, a disabled rule's included, the renamed rule
  still filing new mail and the disabled one still not, and a taken name
  refused with the script untouched. `folder rename` in
  `test_rename_folder.py` ([#5][i5]): the folder and its subfolder moved, both
  subscribed under the new names and gone from `LSUB` under the old, the
  message count kept, and the script's bytes changed only in the two folder
  names. `filter optimize` in `test_optimize_rules.py` ([#21][i21]): the same
  messages handed to `dovecot-lda` before and after a merge and a removal land
  in the same folders, a reorder moves only the starved rule's mail, and a dry
  run stores nothing. New mail is also handed to `dovecot-lda`, which runs the
  uploaded script, so the going-forward half is seen filing it too — by
  header, and by body through a `filter add --body` rule. A filter set
  edited between plan and execute in `test_filterset_changed.py`
  ([#257][i257]): another client stores an edit while `filter add` runs,
  and the upload is refused naming `filterset show`, the edit kept as that
  client stored it, no backup written, and the target folder not made. In
  the same file ([#308][i308]), another client makes a second filter set
  active while `filter add` runs, and the upload is refused naming
  `filterset list`, the switch kept, the planned set's bytes unchanged,
  no backup written, and the target folder not made. The API in
  `test_api_filter_add.py` ([#258][i258]): started as `python -m
  mailctl.api` with the password left out of its settings, so it asks for
  it through `credentials.prompt`, once; a filter added through plan and
  execute sends its events in order (backup, then folder, then upload),
  the backup holds the script's exact bytes, the stored script keeps the
  Roundcube rule beside the new one, new mail is filed by it on delivery,
  and the password is on no line of stdout and not on stderr.
- **The oracle is not mailctl.** Each test reads the server back with
  sievelib's and IMAPClient's own clients, and a byte-exact claim with the
  script file on the container's disk, so a write that mailctl both gets
  wrong and reads back wrong still fails. Scripts are seeded through
  `live_write.SievelibClient`, sievelib's `Client` with only its literal
  framing replaced, because under sievelib 1.6 the plain `Client` turns
  every LF into CRLF on PUTSCRIPT and the tier would stop testing an LF
  script at all ([#232][i232]); the live tier's script guard restores
  through it for the same reason. Every test's docstring names the
  break that turns it red, and each was seen red under that break.
- **mailctl's own image** (`test_image.py`, [#230][i230]). It builds the
  top-level `Dockerfile` and runs the image as a user would, with no
  network and no Dovecot: every refusal comes before a connection. A
  backup directory or `filterset backup --output` in `/dev/shm` is refused
  from every source, a path through the mount cannot lead out, the run
  cannot lift `MAILCTL_BACKUP_ROOT` with `-e` or `--entrypoint`, a run as
  root or without the config mount is refused, and `--help` and
  `--version` need no mount. The image's `api` and `tui` ([#347][i347])
  are held to the same two refusals: `api` and `api help`, `tui` and
  `tui --version`, a help word after `--`, and one beside other
  arguments for either, since each starts what it is given. `tui`
  without a terminal is refused, and its `--api`, in every spelling its
  parser takes, with the control that after `--` the word is handed on
  rather than refused. `api` answers `initialize` over `docker run -i`
  and is held to the root under `-e MAILCTL_BACKUP_ROOT=/`; `tui` under
  a pty reaches the container's own API, as far as its password prompt;
  and `api --version`, `api -h`, and `tui --help` need no mount.
- **Where the live tier's write guards are proved.** The mail store is
  disposable, so this tier needs no guard of its own; instead
  `test_live_write_guards.py` runs the live tier's guards against it
  through the same `Mailbox` type, as context managers and as fixtures in
  a child pytest run that fails, is interrupted, or passes
  ([#9](https://github.com/harleypig/mailctl/issues/9)).
- **The server.** Debian trixie's own `dovecot-*` packages — **Dovecot 2.4.1
  with Pigeonhole** — rather than the `dovecot/docker` image, whose packaging
  is CC BY-NC-SA 4.0. Debian was the first choice on [#49][i49] and it ships
  2.4, the side of MXroute's 2.3 → 2.4 migration worth testing; Ubuntu 24.04
  and 25.04 ship 2.3.21, and Alpine ships 2.4.5.
- **Shaped after the MXroute record, and where it differs.** Maildir++, a
  `.` separator, an empty personal-namespace prefix (as read on
  2026-09-28), `PLAIN` only, STARTTLS on 4190 and 143, implicit TLS on 993,
  and `enotify` turned off, which leaves 23 Sieve extensions. It differs
  in these ways: Sieve lacks `editheader`, the one extension of the 24 the
  MXroute probe listed on 2026-09-29 that it does not advertise; IMAP lacks
  `METADATA` and `QUOTA` (41 capabilities, not 43); the script is named
  `mailctl` on a fresh account rather than Roundcube's `managesieve` (tests
  seed `managesieve` where it matters); no `INBOX.spam`, `Junk`, or other
  default folders exist; any user name logs in with the run's password;
  a user named `alert-*` is sent an `ALERT` at login, where whether
  MXroute ever sends one is unknown; and `redirect` is **not** refused by
  the server, which MXroute's is (mailctl refuses it itself). IMAP runs on
  implicit TLS only: mailctl treats only port 143 as STARTTLS, and
  Docker's port is not 143.
- **The certificate and the password.** The container makes a fresh
  self-signed certificate at start (`localhost` / `127.0.0.1`), and only
  the certificate is copied out, for `SSL_CERT_FILE`. The password is
  random per run, in a `0600` file mailctl reads through
  `MAILCTL_PASSWORD_FILE`, handed to the container on `docker exec` stdin,
  and never printed or put on a command line.
- **Not in CI, for now.** Building the image fetches Debian packages, so a
  CI run would depend on a mirror as well as on the code; that is not
  reliable enough to gate a merge on without first watching it run.
  Revisit once it has a history of passing locally.

## The TUI

The TUI (`clients/tui/`, [ADR 0009][adr9]) is a distribution of its own,
and its tests are a suite of their own: `clients/tui/tests/`, under
`clients/tui/pyproject.toml`'s pytest configuration (`asyncio_mode =
auto`, for `pytest-asyncio`). **The root `pytest` does not collect
them** — its `testpaths` is `tests` — so `make test` needs nothing of
Textual's, and it blocks pytest-asyncio by name (`-p no:asyncio`): no
root test is async, and the plugin, there when the TUI shares `.venv`,
would warn on every run ([#351][i351]). Run them with **`make
testtui`**, which type-checks the TUI with its own `pyright` and then
runs its `pytest`, both from `clients/tui/`; `TESTARGS` passes through
as for the other targets. It needs the TUI installed into `.venv` beside
mailctl (CONVENTIONS.md › *Toolchain & reproducibility*). CI runs the
same two steps as `TUI (3.11)` and `TUI (3.14)`, and the required `Test`
check waits on them.
A test waits for a pushed screen to mount (`conftest.reach`) before it
presses a key or queries the screen: one on the stack but not yet
mounted has no children and no focus, and a key pressed then is lost
([#348][i348]).

- **Against a stub API** (`test_app.py`) — `stub_api.py` stands in for
  `mailctl-api` as a real child process, so the TUI's framing, its reader,
  and its answers to the API's requests are the ones a user runs. Each
  test writes a scene file (`STUB_SCENE`) saying what `initialize`,
  `filter.list`, `mail.search` (with and without `like`), `mail.view`,
  `filter.add.plan` and `.execute`, and any `server.alert` return, which
  method to refuse, and whether the stub asks for the password; the stub
  writes back the methods called and their parameters, never the
  password itself, only whether one was given and matched. Driven
  headless with Textual's Pilot: the filters listed as the document has
  them, what each does worded from its neutral `steps` and never its
  host-word `actions` ([#259][i259]), an empty
  filter set said so, server text shown as plain characters and never as
  markup or control, an alert shown as it arrives and the panel hidden
  without one, the password asked in a masked field and afterwards held by
  no container the app keeps, a declined prompt answered as declined, and
  an API that will not start reported.
- **The first scope, against the stub** (`test_flow.py`, [#259][i259]) —
  search, read, and add a filter like a message, through plan, decide,
  execute. A search sends what was typed, leaves an empty folder to the
  API, and names the folder as the API spelt it; a message is read in
  the listing's folder under its UIDVALIDITY, its text keeping its lines
  with markup shown as characters and control characters escaped; an
  alert reaches the panel over another screen. The form is filled in by
  `mail.search`'s `like`, never from the message's headers, with criteria
  it has no field for carried as sent; an API refusing `like` or
  `build_filter` as unknown opens an empty form that says so, and any
  other -32602 is shown in the API's own words. The plan is shown as the
  document has it, saving sends `filter.add.execute` the plan's id alone
  and shows each `event` and where the backup went, Escape sends
  `plan.discard`, a plan that changes nothing offers no save, and a
  refused plan or a failed save says so. The form offers only what the
  provider declares, one capability withheld at a time removing exactly
  its options; the filter list's keys are not offered over another
  screen; `subscribe` is sent only beside `create_folder`, and what the
  user left alone is not sent; and the form opens focused on the first
  field there is something to give.
- **Against the real API** (`test_real_api.py`) — the TUI started on
  mailctl's own `tests/api_child.py`, the real API with its two protocol
  clients replaced by doubles, so this is the stub's contract checked
  against what it stands in for: the real `initialize`, the real
  `filter.list` document, and the real `credentials.prompt`. It also
  checks that every capability a command is gated on is one `initialize`
  returns, since a misspelt one would hide its command under every
  provider. And it runs the first scope end to end ([#259][i259]):
  search, read, a form filled in by the real `like`, plan and execute,
  a backup on disk holding the filter set as it was, the new filter
  stored beside the webmail's, the password asked once, and afterwards
  no container the app holds it (a `gc` scan). Only the child process
  imports mailctl.
- **The guards** (`test_guards.py`) — the two lines ADR 0009 holds the TUI
  to. **It imports nothing from mailctl**: every module of the package is
  walked, not listed, and an `import` of `mailctl` or anything under it,
  static or by name at run time (`import_module`, `__import__`), fails;
  `mailctl_tui` is not `mailctl`. **It offers only what the capabilities
  allow**: every key binding is a command from `offer.COMMANDS` on its
  own screen, every screen is gated and named there, the command palette
  is off, the password screen binds only its own cancel, `offered` fails
  closed on a capability or an action not declared, and a command whose
  capability `initialize` did not declare true is hidden and will not
  run. Each guard carries a planted case it is seen to catch — each kind
  of import, a stray binding, another screen's command — so neither
  passes for reading nothing, and the walk is checked to reach every
  module.
- **The wording guard** (`test_wording.py`, [#259][i259]) — no provider
  word, and no *rule* outside a quoted name, in anything the TUI writes
  on any screen of the first scope: labels, statuses, table cells, form
  options, the footer, notices, and what saving did. What the server
  wrote (the diff, a message's headers and body) is left out, and the
  pattern is the CLI's (*Neutral wording* above), so a backup's `.sieve`
  suffix is content. The walk is seen to reach every kind of text on
  every screen, a planted "script" line turns it red, and the pattern is
  checked against each word and against the user's words and content.

## The read-only live check

`scripts/live-readonly.sh`, run by `make livecheck`, is a bash script that
runs read-only CLI commands and `--dry-run` plans against the real account,
then verifies that nothing changed. It reports in TAP. Name tests as
arguments to run only those; `--list` shows the names.

It is **not** the pytest live tier above, and the difference is the point:

- **It never writes.** `tests/live/` writes only behind the write guards and
  a second opt-in ([#9](https://github.com/harleypig/mailctl/issues/9));
  this one never does, so it needs neither and is safe to run any time.
- **It drives the CLI end to end**, as a user or an automation script would —
  the installed `mailctl` command, not the package from inside Python. The
  CLI is the automation surface (CONVENTIONS.md › *The core returns data;
  only the interfaces print*), so this is the check that its commands work
  against a real server.
- **It uses the normal mailctl config** — the same settings, env file, and
  password source the user runs with, not a test-only set. An env file is
  named with `--env-file PATH`, which the script hands to every mailctl
  call as `--env-file PATH`, so mailctl reads it as it always does (the
  mode check, `Secret`, never exported); the script never reads or exports
  it. Through make it rides in `TESTARGS`:
  `make livecheck TESTARGS='--env-file PATH reread-bytes'`. A path that
  does not exist is refused, exit 2, before any test runs ([#325][i325]).
- **It paces itself.** The account is real, so there are no loops over the
  server and there is a pause between tests.

**Every future read-only live check goes here** (operator, 2026-09-28). A
check that only reads, or only plans with `--dry-run`, is added to this
script, not to `tests/live/`. `search-unread` is one: a single `search
--unread --since <30 days ago> --limit 5`, checking the date and state
filters reach MXroute and every row it lists is unread ([#152][i152]). It
does not check each row's date, since a message near midnight can show on
either side of the server's. `filter optimize` is another: one
`filter optimize --dry-run --json`, checking the plan is well formed, has a
diff exactly when it proposes a change, and uploaded nothing
([#21][i21]). `uidvalidity` is a third: the newest UID and its
UIDVALIDITY from one `mail search --json`, a `mail view` pinned to that value,
and a `mail view` pinned to another, which must be refused ([#204][i204]).
`reread-bytes` is a fourth: the active filter set read twice by `filterset
backup --output`, a pause apart, into the run's own temp directory, and the
two files compared byte for byte; any difference fails it ([#299][i299]). It
is skipped where `MAILCTL_BACKUP_ROOT` keeps a backup out of that directory.
Its first run against the real account is [#298][i298].

## Live-test credentials & safety

Live tests touch a real mailbox, so the guards are not optional:

- **Credentials come from the environment** — the `MAILCTL_*` variables
  (`config.py`), with the password via `MAILCTL_PASSWORD_CMD` in preference to
  `MAILCTL_PASSWORD`. `MAILCTL_LIVE=1` is required, so a plain `pytest` can
  never touch the account.
- **The password stays out of every artifact.** A live test's output, a
  captured log, and a failure traceback are all places a credential could
  surface — `Secret` is what prevents it, so a live test must never unwrap a
  password to build a fixture or a diagnostic. See CONVENTIONS.md ›
  *Credentials*.
- **Writing is a second opt-in.** A test that writes uses the fixtures
  below, which reach the account through `write_mailbox`; it skips unless
  `MAILCTL_LIVE_WRITE` is exactly `1` as well (pinned offline by
  `test_live_write_gate.py`). `make testlive` alone reads.
- **Back up and restore every script — `guarded_scripts`.** It captures
  each script's exact bytes and which is active, saves them to a `0600`
  file, and afterwards puts back only what changed, deletes what the test
  created, and confirms the result through two readers. A restore it
  cannot confirm fails the test's teardown, naming the saved copy. It runs
  after a failure and after Ctrl-C.
- **Never touch INBOX — `scratch_folder`.** A new `mailctl-test-…` folder
  in the personal namespace, deleted with its contents afterwards; INBOX,
  any folder that already exists, and any unmarked name are refused before
  anything is written. Append the test's own messages there
  (`scratch_folder.append`).
- **Few writes, never looped.** The guards write nothing when nothing
  changed. `tests/live_write.py` holds both guards and says what each
  sends.
- **Assume nothing about the server's configuration.** The live tier is
  precisely where *Discover, don't hardcode* (CONVENTIONS.md) gets exercised —
  a test that hardcodes the delimiter, the script name, or `INBOX.spam` is
  testing our assumption rather than the server.

## Manual verification

Every shipped feature also carries a plain-language note — where to go, what
to do, what success looks like **concretely**, and what failure looks like —
written in the PR as the first draft of the user-facing docs
(`testing.md` › *The manual verification bar*). This matters more than usual
here: a filter that was written but silently does nothing looks identical, at
the terminal, to one that works. "The rule was added" is not a success
criterion; "`mailctl filter list` shows the rule, and a new message matching
it lands in `Lists/GitHub`" is.

## Running

```sh
pytest                 # unit (offline, credential-free)
make test              # the same, via the Makefile
make testlive          # live (MAILCTL_LIVE=1; needs MAILCTL_* in the env)
MAILCTL_LIVE_WRITE=1 make testlive TESTARGS=tests/live/test_live_write_smoke.py
                       # the one live test that writes, behind the guards
make testcontainer     # write paths against a local Dovecot (needs Docker)
make testtui           # the TUI: its pyright, then its own pytest suite
make livecheck         # read-only live check (normal mailctl config)
scripts/live-readonly.sh --list        # the read-only check's test names
scripts/live-readonly.sh list rules    # run only the named checks
scripts/live-readonly.sh --env-file PATH  # an account set by an env file
```

`TESTARGS` passes extra flags through, e.g. a run filter for a scoped live
pass: `make testlive TESTARGS='-k sieve'`.

[i5]: https://github.com/harleypig/mailctl/issues/5
[i51]: https://github.com/harleypig/mailctl/issues/51
[i21]: https://github.com/harleypig/mailctl/issues/21
[i89]: https://github.com/harleypig/mailctl/issues/89
[i90]: https://github.com/harleypig/mailctl/issues/90
[adr6]: ../adr/0006-two-layer-component-and-provider-architecture.md
[adr7]: ../adr/0007-interfaces-utilities-session-provider-layering.md
[adr9]: ../adr/0009-python-textual-tui-as-a-separate-package.md
[i99]: https://github.com/harleypig/mailctl/issues/99
[i135]: https://github.com/harleypig/mailctl/issues/135
[i152]: https://github.com/harleypig/mailctl/issues/152
[i154]: https://github.com/harleypig/mailctl/issues/154
[i49]: https://github.com/harleypig/mailctl/issues/49
[i157]: https://github.com/harleypig/mailctl/issues/157
[i39]: https://github.com/harleypig/mailctl/issues/39
[i159]: https://github.com/harleypig/mailctl/issues/159
[i183]: https://github.com/harleypig/mailctl/issues/183
[i188]: https://github.com/harleypig/mailctl/issues/188
[i192]: https://github.com/harleypig/mailctl/issues/192
[i204]: https://github.com/harleypig/mailctl/issues/204
[i205]: https://github.com/harleypig/mailctl/issues/205
[i208]: https://github.com/harleypig/mailctl/issues/208
[i160]: https://github.com/harleypig/mailctl/issues/160
[i216]: https://github.com/harleypig/mailctl/issues/216
[i229]: https://github.com/harleypig/mailctl/issues/229
[i230]: https://github.com/harleypig/mailctl/issues/230
[i231]: https://github.com/harleypig/mailctl/issues/231
[i232]: https://github.com/harleypig/mailctl/issues/232
[i219]: https://github.com/harleypig/mailctl/issues/219
[i241]: https://github.com/harleypig/mailctl/issues/241
[i244]: https://github.com/harleypig/mailctl/issues/244
[i262]: https://github.com/harleypig/mailctl/issues/262
[i257]: https://github.com/harleypig/mailctl/issues/257
[i258]: https://github.com/harleypig/mailctl/issues/258
[i259]: https://github.com/harleypig/mailctl/issues/259
[i294]: https://github.com/harleypig/mailctl/issues/294
[i298]: https://github.com/harleypig/mailctl/issues/298
[i299]: https://github.com/harleypig/mailctl/issues/299
[i307]: https://github.com/harleypig/mailctl/issues/307
[i308]: https://github.com/harleypig/mailctl/issues/308
[i279]: https://github.com/harleypig/mailctl/issues/279
[i280]: https://github.com/harleypig/mailctl/issues/280
[i330]: https://github.com/harleypig/mailctl/issues/330
[i331]: https://github.com/harleypig/mailctl/issues/331
[i332]: https://github.com/harleypig/mailctl/issues/332
[i341]: https://github.com/harleypig/mailctl/issues/341
[i346]: https://github.com/harleypig/mailctl/issues/346
[i347]: https://github.com/harleypig/mailctl/issues/347
[i348]: https://github.com/harleypig/mailctl/issues/348
[i349]: https://github.com/harleypig/mailctl/issues/349
[i350]: https://github.com/harleypig/mailctl/issues/350
[i351]: https://github.com/harleypig/mailctl/issues/351
[i352]: https://github.com/harleypig/mailctl/issues/352
[i362]: https://github.com/harleypig/mailctl/issues/362
[i364]: https://github.com/harleypig/mailctl/issues/364
[i325]: https://github.com/harleypig/mailctl/issues/325
