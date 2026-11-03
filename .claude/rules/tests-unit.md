---
paths:
  - "tests/**"
---

# mailctl Tests — the unit tier

Path-scoped detail moved out of `.claude/TESTS.md`, which stays always
loaded as the short core; read the core's *Where the detail lives* for
the whole map. Claude Code loads this file only when a file matching
the `paths:` above is read or edited.

## The unit tier, file by file

The tier itself is described in the core's *Three tiers*; *The API* and
*The API's framing* are [tests-api.md](tests-api.md).

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
  ([ADR 0002](../../adr/0002-non-destructive-script-merge.md)); it deserves a
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
  that fails still shuts the socket ([#362][i362]). A socket error in
  any of the three is raised coded, `folder_list_failed`, with the
  `OSError` kept as its cause, and closes the connection the same way
  ([#376][i376]). Every command after login, read or write, raises a
  socket error as its own coded error, the `OSError` its cause, so the
  session still reads it as a lost connection ([#402][i402]). What
  MXroute and
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
  of kind is a deliberate diff. A socket error, which neither library
  turns into its own error, is held to the same policy ([#402][i402]):
  on mail, a read sent once more on a fresh connection and a second
  reset raised as the command's coded error, and a write raised coded,
  never sent again, its connection let go; on ManageSieve, the listing,
  read, and check sent once more and the store and activate never, each
  raised coded. The idle preflight ([#258][i258]): each
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
  So `server test`'s own wrapping is run at 50 columns instead: its
  notes under one `Note:` heading, the long `FILTER=SIEVE` fact, the
  line on a server mailctl does not recognise, and the line on the
  active script are each filled to the width and continued under their
  own text ([#439][i439], [#455][i455]). `config test` has a snapshot
  for success and one for each half refused, `config-test-mail-refused`
  and `config-test-filter-refused` ([#440][i440]).
- **The command tree** (`test_cli_help.py`, over `tests/cli_support.py`,
  which reads the groups and commands off the parser) — `mailctl help` with
  a group, or a group and an action, is the same text and exit as `--help`
  there, for every one; the groups are listed in their decided order; and
  every old flat name is refused, exit 2, naming the command it is now,
  wherever it is the command word and never as an argument ([#219][i219]).
  An unknown command or action is refused listing the choices, never
  echoing the word given ([#364][i364]). `mailctl help config` lists
  `config test` ([#440][i440]).
- **`--json`** (`test_json_output.py`) — a `Secret` refused rather than
  serialised, failures as one JSON line, and which commands offer it.
  The failure line carries the error's `fields` where it has any, and a
  field that cannot be serialised costs the fields, never the line, its
  code, or the value ([#374][i374]). A value refused for `--provider`
  or `--env-file`, given in each place either is taken and as
  `--opt=VALUE`, is quoted on neither stream, with or without
  `--json` ([#373][i373]).
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
- **The error-code catalogue** (`test_error_codes_catalogue.py`,
  [#375][i375]) — `docs/ERROR-CODES.md` lists every code the package
  raises, and no other, and none twice. The codes are derived from
  every module, the interfaces included, since `cli.ERROR_TEXT` lacks
  the API's own: a `code=` literal, a module-level constant
  (`cli.ARGUMENTS`), or a `code` parameter's default. A code passed on
  through `**` or a variable, or handed back positionally, is not read;
  a row whose code the walk cannot find fails, so such a code shows as
  a failure, never a pass. A planted raised code with no row, a planted
  row nothing raises, a repeated row, and a row the parse cannot read
  each turn it red.
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
  server reports and `config test`, which prints `server test`'s own
  settings and mail sections ([#440][i440]), the filter set's own text
  (a diff, `filterset show`), `--verbose`'s protocol log, a server's
  alert or warning, a JSON document's keys, and a refusal about an
  extension or a forbidden action. Option names and metavars are taken out first. Each walk is
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

[i216]: https://github.com/harleypig/mailctl/issues/216
[i349]: https://github.com/harleypig/mailctl/issues/349
[i362]: https://github.com/harleypig/mailctl/issues/362
[i376]: https://github.com/harleypig/mailctl/issues/376
[i402]: https://github.com/harleypig/mailctl/issues/402
[i89]: https://github.com/harleypig/mailctl/issues/89
[i157]: https://github.com/harleypig/mailctl/issues/157
[i205]: https://github.com/harleypig/mailctl/issues/205
[i230]: https://github.com/harleypig/mailctl/issues/230
[i259]: https://github.com/harleypig/mailctl/issues/259
[i188]: https://github.com/harleypig/mailctl/issues/188
[i192]: https://github.com/harleypig/mailctl/issues/192
[i39]: https://github.com/harleypig/mailctl/issues/39
[i279]: https://github.com/harleypig/mailctl/issues/279
[i330]: https://github.com/harleypig/mailctl/issues/330
[i331]: https://github.com/harleypig/mailctl/issues/331
[i332]: https://github.com/harleypig/mailctl/issues/332
[i350]: https://github.com/harleypig/mailctl/issues/350
[i204]: https://github.com/harleypig/mailctl/issues/204
[i341]: https://github.com/harleypig/mailctl/issues/341
[i280]: https://github.com/harleypig/mailctl/issues/280
[i257]: https://github.com/harleypig/mailctl/issues/257
[i308]: https://github.com/harleypig/mailctl/issues/308
[i258]: https://github.com/harleypig/mailctl/issues/258
[i231]: https://github.com/harleypig/mailctl/issues/231
[i241]: https://github.com/harleypig/mailctl/issues/241
[i439]: https://github.com/harleypig/mailctl/issues/439
[i455]: https://github.com/harleypig/mailctl/issues/455
[i440]: https://github.com/harleypig/mailctl/issues/440
[i219]: https://github.com/harleypig/mailctl/issues/219
[i364]: https://github.com/harleypig/mailctl/issues/364
[i374]: https://github.com/harleypig/mailctl/issues/374
[i373]: https://github.com/harleypig/mailctl/issues/373
[i307]: https://github.com/harleypig/mailctl/issues/307
[i352]: https://github.com/harleypig/mailctl/issues/352
[i229]: https://github.com/harleypig/mailctl/issues/229
[i244]: https://github.com/harleypig/mailctl/issues/244
[i262]: https://github.com/harleypig/mailctl/issues/262
[i294]: https://github.com/harleypig/mailctl/issues/294
[i51]: https://github.com/harleypig/mailctl/issues/51
[i183]: https://github.com/harleypig/mailctl/issues/183
[i375]: https://github.com/harleypig/mailctl/issues/375
[adr7]: ../../adr/0007-interfaces-utilities-session-provider-layering.md
[adr6]: ../../adr/0006-two-layer-component-and-provider-architecture.md
[i99]: https://github.com/harleypig/mailctl/issues/99
[i154]: https://github.com/harleypig/mailctl/issues/154
[i135]: https://github.com/harleypig/mailctl/issues/135
