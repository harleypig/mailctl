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
where every write path is proved first.

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
     plain parameters. What MXroute and `Config` add — the port-to-TLS
     rule and the login and connection advice — is
     `test_mxroute_imap.py`; server-module selection by `ID` is
     `test_imap_servers.py`.
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
     `--max-messages` ceiling is refused whole. `test_utilities_mail.py`
     also holds the re-check narrowing the host's search to what the rule
     matches, and a rule that keeps its mail copying it rather than moving
     it ([#188][i188]), with a second run copying only what the folder
     lacks, by Message-ID, and reading the folder only while planning
     ([#192][i192]). `test_utilities_server_report.py` pins the redaction:
     servers echo a sentinel address, host, IPv4 address, folder, and
     script name back, and none of them, the password, or the script's
     text may reach the report ([#39][i39]). `test_utilities_uids.py` pins
     the UIDVALIDITY check ([#204][i204]): a stale pin given to `mail mark`
     refused before any write, and before its flags are read; `mail view` and
     `--like` refusing one, a UID lost in the renumbering included; a plan's
     own value checked again at execute; and reading it costing no SELECT
     of its own. The fakes they share are `tests/utilities_support.py`.
   - **The session** (`test_engine.py`) — only what is about the
     connection: a half opens the first time a utility uses it and never if
     the command did not ask for it; a read the server cut off is sent once
     more on a fresh connection, and a second failure or a refusal is
     raised; a write cut off mid-flight is raised and **never sent again**,
     on both halves; calls on one connection wait their turn under the
     session's lock; two sessions share nothing; and every transport
     operation is classified, with each operation's kind pinned so a change
     of kind is a deliberate diff.
   - **Sieve extensions** (`test_extensions.py`) — the emit table checked
     against sievelib's `require` line over every rule shape, and
     `disabled_extensions` refusing, falling back, and doing nothing where
     the server lacks the extension anyway. Its ladder is in
     `test_config.py`.
   - **The old name** (`test_migration.py`) — `config migrate` moving the
     old config directory (modes kept, a clash refused, `--dry-run` inert),
     the old-directory warning and when it stays silent, and old
     `MXROUTE_*` names reported by name — checked against a sentinel value
     that must never appear in any output. The `mxfilter` script's reuse is
     in `test_utilities_rules.py`.
   - **CLI snapshots** (`test_cli_snapshots.py`) — each subcommand run end to
     end through `cli.main`, recording exit code, stdout, stderr, and every
     server call into `tests/snapshots/cli/<name>.txt`. A behaviour change
     shows up as a snapshot diff; regenerate with
     `MAILCTL_UPDATE_SNAPSHOTS=1` and read the diff before committing.
   - **The command tree** (`test_cli_help.py`, over `tests/cli_support.py`,
     which reads the groups and commands off the parser) — `mailctl help` with
     a group, or a group and an action, is the same text and exit as `--help`
     there, for every one; the groups are listed in their decided order; and
     every old flat name is refused, exit 2, naming the command it is now,
     wherever it is the command word and never as an argument ([#219][i219]).
   - **`--json`** (`test_json_output.py`) — a `Secret` refused rather than
     serialised, failures as one JSON line, and which commands offer it.
   - **The ManageSieve wrapper** (`test_managesieve_client.py`) — the real
     `SieveClient` against a scripted socket that replays a server's bytes:
     GETSCRIPT byte-exact whatever the script ends with ([#90][i90]), the
     whole CAPABILITY response kept, the read timeout honoured, and nothing
     printed even with sievelib's debug flag forced on; an OK's `WARNINGS`,
     quoted or literal, reaching `progress`, and a literal read whole
     ([#208][i208]); the CLI's half is the `add-warnings*` snapshots. Its
     server-module selection and capability parsing are
     `test_managesieve_servers.py`.
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
     commands ([#183][i183]). Each guard carries a planted case it is seen
     to catch.
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
     refused; a `filterset` command is refused by the utility, naming the
     provider, before anything is read; and `filter add` and `server test`
     under a fake say nothing about Sieve or MXroute. `mxroute`'s help hides
     nothing but the always-hidden flags.
2. **Live tests** (`MAILCTL_LIVE=1`) — stand up **real** Sieve scripts and
   move **real** mail against a **live MXroute account**. They mutate real
   state; run them manually (`make testlive`), **never** in a default gate.
   A test that writes also needs `MAILCTL_LIVE_WRITE=1` and the write
   guards (*Live-test credentials & safety*).
3. **Container tests** (`MAILCTL_CONTAINER=1`, `tests/container/`) — every
   **write** path against a **throwaway local Dovecot + Pigeonhole** in
   Docker, never a real account. See *The container tier* below.

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
  header, and by body through a `filter add --body` rule.
- **The oracle is not mailctl.** Each test reads the server back with
  sievelib's and IMAPClient's own clients, and a byte-exact claim with the
  script file on the container's disk, so a write that mailctl both gets
  wrong and reads back wrong still fails. Every test's docstring names the
  break that turns it red, and each was seen red under that break.
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
  only the CLI prints*), so this is the check that its commands work against
  a real server.
- **It uses the normal mailctl config** — the same settings, env file, and
  password source the user runs with, not a test-only set.
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
make livecheck         # read-only live check (normal mailctl config)
scripts/live-readonly.sh --list        # the read-only check's test names
scripts/live-readonly.sh list rules    # run only the named checks
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
[i219]: https://github.com/harleypig/mailctl/issues/219
