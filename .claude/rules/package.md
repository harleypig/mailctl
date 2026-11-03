---
paths:
  - "mailctl/**"
  - "clients/**"
---

# mailctl Conventions — the package

Path-scoped detail moved out of `.claude/CONVENTIONS.md`, which stays always
loaded as the short core; read the core's *Where the detail lives* for
the whole map. Claude Code loads this file only when a file matching
the `paths:` above is read or edited.

## Layout

The package is layered in four, top to bottom — interfaces, utilities, the
session, the provider — over the protocol components ([ADR 0007][adr7],
refining [ADR 0006][adr6]); *The core returns data* below says what each
layer may call.

- `mailctl/__init__.py` — the package docstring, `__version__`, and
  `MailctlError` (the one exception type every actionable failure raises).
- `mailctl/config.py` — endpoint and credential resolution, the `Secret`
  wrapper (see *Credentials* below), and the backup-root guard
  (`check_backup_location`, see *Rule conventions*).
- `mailctl/criteria.py` — the shared criteria model, translated **both** to
  Sieve tests and to IMAP `SEARCH`. One model, two backends — this is what
  keeps the two halves in agreement. It is the **input model** every
  front-end builds, so syntax sugar belongs here (*The core returns data*),
  and so does the filter document, the model as JSON.
- `mailctl/cli.py` — **interface**: the CLI front-end. Argument parsing,
  turning flags into utility inputs, opening a session for the command, and
  rendering and confirming what the utilities return.
- `mailctl/json_output.py` — **interface**: the interface layer's shared
  module. The documents the CLI's `--json` prints and the API returns, one
  explicit mapping per neutral record, built by the same functions so the
  two are one contract.
- `mailctl/api/` — **interface**: the API, mailctl's operations over
  JSON-RPC 2.0 for a client in any language ([ADR 0008][adr8]). A client
  starts `mailctl-api` with the settings it would give `mailctl`.
  - `__init__.py` — `main`: one client served on stdin and stdout until
    `shutdown` or the end of its input, with anything else printed sent to
    stderr.
  - `protocol.py` — the framing, one JSON message per line, and the
    envelope. A client's lines are read here and nowhere else, so this is
    where a `password` it sends is wrapped in `Secret`, as the line is
    parsed. They are read on a thread of their own, so a cancel is in
    hand while a request runs; the handling and every line written stay
    on the thread that serves the requests ([#280][i280]). That thread
    reads ahead at most `INBOX_LIMIT`, 64, messages not yet served, then
    waits, so a client writing faster than it is served is held back by
    its own pipe; it is stopped when the API stops serving, closing the
    session failed or not ([#346][i346]).
  - `server.py` — the session, the methods, and the plan table between a
    `.plan` and its `.execute`.
  - `__main__.py` — `python -m mailctl.api`.
- `mailctl/utilities/` — **utilities**: every piece of work the tool does,
  host-independent, for any front-end and against any provider, one module
  per subject. The safety policy lives here.
  - `rules.py` — checking a rule against what the provider declares
    (`check_rule`, `resolve_stop`), reading the rules in a script, planning
    a rule into it or a change to one (removed, moved, switched, renamed),
    and uploading it; and what each read rule does, in the dialect's
    neutral steps (`describe_rules`).
  - `scripts.py` — rule sets as the server stores them: read, chosen, and
    uploaded. `upload_script` is the one upload path, and takes the backup
    first.
  - `backup.py` — backing up the active script, and restoring one.
  - `backup_files.py` — writing a backup's exact bytes to a new file, and
    a baseline's over its old one.
  - `baseline.py` — a saved probe per host, and drift from it: the
    comparison is a pure function over two probe records and what the
    active script requires.
  - `flags.py` — marking messages read, flagged, or with keywords: a
    read-only plan of what changes per message, then separate add and
    remove writes.
  - `folders.py` — listing and subscribing folders, and planning a rule's
    target folder (created on execute only). The existing-mail pass saves
    no rule, so it plans its folder with `delivery_route=False` and never
    asks the rules half.
  - `folder_rename.py` — a folder renamed with the folders under it, their
    subscriptions carried, and the rules filing into them repointed in
    place; then the account read back.
  - `mail.py` — the existing-mail pass, with `recheck` narrowing the host's
    search to what the rule really matches and the `--max-messages`
    ceiling; and criteria derived from a message.
  - `messages.py` — finding and reading messages, read-only.
  - `optimize.py` — a better arrangement of the rule set proposed from the
    rules' own conditions (redundant rules removed, starved rules moved,
    same-filter rules merged), then uploaded.
  - `senders.py` — a folder's mail counted by sender, read-only.
  - `migration.py` — what the rename from `mxfilter` left behind.
  - `reports.py` — the probes behind `mailctl server test` and `config
    test`, the provider's own words for its reports and its connection
    facts, and each extension's state.
  - `server_report.py` — which servers no module recognised, and the
    redacted issue body `server probe --report` prints for them ([#39][i39]).
  - `server_test.py` — everything `server test` reports, gathered as one
    record from the same probes (`examine_servers`), read-only, for a
    front-end that wants the whole of it at once; the API's `server.test`
    returns it, and `config.test` a cut of it. The CLI does not use it:
    `server test` and `config test` call `reports.py`'s probes as each
    section prints, so a failure stops at its own section ([#439][i439]).
  - `events.py` — the steps of a change, as a front-end is told of them,
    and `ServerAlert` re-exported for a front-end to recognise one.
  - `uids.py` — a UID kept between runs, pinned to the folder's
    UIDVALIDITY and refused, before it is used, once the folder has been
    renumbered ([#204][i204]); a UID the folder does not hold is refused,
    coded `no_such_message`, a pinned one checked for staleness first
    ([#341][i341]).
- `mailctl/engine.py` — **the session**. `connect(config)` resolves the
  provider the configuration selects, refuses a setting it has no use for,
  lets its dialect validate the rest — all before any connection — and
  yields a `Session`, closed when the block ends. `capabilities_for(config)`
  is what that provider declares, with no connection, so a front-end can
  offer only what it can do. The session never touches a protocol; it
  imports no component and never names a provider
  (`tests/test_layer_purity.py`).
- `mailctl/providers/` — **the provider**, layer 2 of [ADR 0006][adr6]: one
  package per host, each composing layer-1 components, in two halves (see
  *Providers* below).
  - `base.py` — the interface: `Dialect` (the offline half), `Transport`
    (the communication half, every operation classified, and one read per
    half marked its probe by `Operation.probe`: `list_rule_sets` on rules,
    `mail_noop`, an IMAP `NOOP`, on mail), `Provider` (the
    record naming a host's capabilities, dialect, and transport), and
    `ProviderCapabilities`; it re-exports the model so one import reaches
    both. `message_headers` and `message_source` return None for a UID
    the folder does not hold, which the `mxroute` transport makes of the
    `imap` component's `NoSuchMessage` ([#341][i341]).
  - `model.py` — the provider-neutral model the utilities speak:
    `ActionSpec`, `Placement`, `DisplayDiff`, the folder and message
    records, the neutral steps a rule's actions take (`ActionStep`), the
    host's own words for its reports as data (`Wording`, `Fact`), and an
    alert or warning the server sent (`ServerAlert`, by `kind`). It and
    `base.py` import nothing from layer 1 (`tests/test_layer_purity.py`).
  - `registry.py` — `PROVIDERS`, every provider by name, and
    `provider_for(config)`.
  - `mxroute/provider.py` — `MXROUTE`, the `Provider` record: MXroute's
    capabilities, `MxrouteDialect`, and `MxrouteTransport`.
  - `mxroute/dialect.py` — `MxrouteDialect`, offline: a neutral rule into
    Sieve merged into the account's script and read back out, MXroute's
    refusals and its words for the server reports, backup paths, folder
    names normalized against the server's list, and what `MOVE`,
    `UIDPLUS`, and `FILTER=SIEVE` mean as the facts `mailctl server test`
    shows.
  - `mxroute/sieve.py` — the dialect's Sieve side, offline:
    `MXROUTE_FORBIDDEN_ACTIONS`, the Roundcube `# rule:[NAME]` name dialect
    and the script functions bound to it, the translation of an
    `ActionSpec` into Sieve actions, the `disabled_extensions` checks, and
    a placement or diff crossing to and from the component's records.
  - `mxroute/transport.py` — `MxrouteTransport`, communication only:
    ManageSieve for rules and IMAP for mail, each half its own connection,
    each operation one exchange handed to a layer-1 session.
  - `mxroute/managesieve.py` — the transport's ManageSieve side:
    `sieve_session()` maps `Config` onto a `SieveSession`, and a failed
    connection or login gains MXroute's advice. A failed connection keeps
    its code, `connection_failed`, `tls_failed`, or `starttls_not_offered`
    for a server offering no STARTTLS, and the advice is also in its
    `fields`, `advice` `try_setting` naming the other TLS mode
    ([#352][i352], [#377][i377]).
  - `mxroute/imap.py` — the same for IMAP: `imap_session()` maps `Config`
    onto an `ImapSession` (port 143 is STARTTLS, any other implicit TLS),
    with the full-address login advice and the hints that name mailctl's
    settings; a connection that fails is `connection_failed`, with
    `advice` `check_settings` and the settings in its `fields`
    ([#352][i352]).
  - `mxroute/records.py` — the translation between the neutral model and
    the `imap` component's own records.
- `mailctl/components/` — **layer 1** ([ADR 0006][adr6]): one library per
  protocol, knowing nothing about any host. It imports only the stdlib, the
  library it wraps, other components, and `MailctlError` — never `config`,
  the session, the utilities, the CLI, or a provider
  (`tests/test_layer_purity.py`).
  - `managesieve/client.py` — `SieveClient`, sievelib's client with ADR
    0006's gaps S1/S4/S5/S8 closed (byte-exact GETSCRIPT, the whole
    CAPABILITY response, a configurable read timeout, debug output
    impossible), an OK's text read whole, a script sent unchanged and every
    name quoted and escaped alike under sievelib 1.5 and 1.6 ([#232][i232]),
    and `SieveSession` on it, taking plain connection parameters. An open
    that fails once the socket is up — a refused login or STARTTLS, a
    greeting cut short — closes the socket without a LOGOUT and keeps its
    own code; `close` sends LOGOUT and then closes the socket, which
    sievelib's LOGOUT leaves open ([#362][i362]). sievelib lets a socket
    error through as an `OSError`, so each command after login raises it
    as its own coded error, the `OSError` its cause ([#402][i402]).
  - `managesieve/capabilities.py` — the CAPABILITY response as data.
  - `managesieve/responses.py` — `ServerWarning`, and the `WARNINGS` a
    status response carries; offline.
  - `managesieve/script.py` — the offline script handling (parse / merge /
    move / remove / rename / rearrange / render / diff), rule names through
    a `NameDialect`, and `UNIMPLEMENTED_ACTIONS`. A rename rewrites the
    name marker in place, as a folder rename rewrites a `fileinto`.
  - `managesieve/emit.py` — `EMIT_TABLE`, every command, test, and tag
    mailctl can put in a rule and the Sieve extension each needs; offline.
  - `managesieve/backup.py` — where a backup of a script goes; writing it
    is `utilities/backup_files.py`'s.
  - `managesieve/servers/` — one module per server software, chosen by the
    `IMPLEMENTATION` capability, with a plain-protocol fallback;
    `profile.py` holds a server's quirks as data, and `pigeonhole.py`
    carries none yet.
  - `imap/client.py` — `ImapSession`, over IMAPClient, taking plain
    connection parameters: `LIST` / `LSUB` and the delimiter, create and
    subscribe (subscription confirmed by re-reading `LSUB`), the search and
    the header fetch its caller re-checks against, flags, the move with its
    COPY + EXPUNGE fallback (ADR 0006's I3), the copy that leaves the
    originals where they are (`copy_messages`, for a rule that keeps its
    mail), `BODY.PEEK` reads under `EXAMINE`, and the UIDVALIDITY each
    `SELECT` or `EXAMINE` reports, kept for the selected folder so reading
    it costs no round trip. Every `ALERT` the server sends once TLS is up
    goes to `progress`, read by wrapping imaplib's response reader. The
    re-check itself is the mail utility's. An open that does not log in —
    refused, or its password prompt stopped — shuts the connection down
    without a LOGOUT ([#349][i349]); one whose folders or namespace then
    cannot be read logs out, and a LOGOUT that fails still shuts the
    socket ([#362][i362]). A socket error in those reads is raised
    `folder_list_failed`, the `OSError` kept as its cause ([#376][i376]),
    and in any later command as that command's own coded error, the
    `OSError` again its cause, so the session reads it as a lost
    connection ([#402][i402]).
  - `imap/alerts.py` — `ServerAlert`, the component's record of an
    `ALERT`, and whether a response line carries one and what it says;
    offline.
  - `imap/capabilities.py` — the capabilities the session behaves
    differently without, derived from its `has_capability` checks and held
    to them by `tests/test_imap.py`; offline.
  - `imap/folders.py` — folder names normalized against a reported
    delimiter, and case variants; offline.
  - `imap/messages.py` — message summaries, the existing-mail plan and its
    result, and header reading; offline.
  - `imap/search.py` — `SearchCriteria`, what the session needs from a
    criteria object, and `encode_search_key`, which gets a non-ASCII value
    to the server intact (I1, [#89][i89]).
  - `imap/status.py` — every folder's counts from one `LIST ... RETURN
    (STATUS ...)` (`LIST-STATUS`, RFC 5819): the command's arguments and a
    parser for the `STATUS` lines imaplib collects, which the session
    sends raw because IMAPClient has no such command; offline.
  - `imap/servers/` — one module per server software, chosen by the IMAP
    `ID` response, with a plain-protocol fallback; `profile.py` holds a
    server's quirks as data, and `dovecot.py` carries none yet.
- `mailctl/rules.py` — reads a parsed script into a flat rule model and
  reports which rules cannot fire where they are (shadowing, in both
  directions); offline.
- `mailctl/__main__.py` — `python -m mailctl`.
- `tests/` — pytest, mirroring the package layout ([TESTS.md](../TESTS.md)).
- `clients/tui/` — **outside the package**: the TUI, the API's first
  client, a distribution of its own (`mailctl-tui`) with its own
  `pyproject.toml` and its package under `src/mailctl_tui/`
  ([ADR 0009][adr9]). It depends on Textual, not on `mailctl`: it starts
  the `mailctl-api` command and imports nothing from the package. The
  layer guards walk `mailctl/` only, so they do not reach it; its own
  guards are `clients/tui/tests/test_guards.py`, and its wording guard
  `test_wording.py` beside it (TESTS.md › *The TUI*).

The split is deliberate: the **offline** logic (criteria translation, Sieve
generation, folder-name normalization, script merging) is exercisable with no
server at all. Keep new logic on the offline side of that line wherever it can
live there.

## The layering in detail

The core keeps *The core returns data; only the interfaces print* and the
four layers; these paragraphs were part of that section.

**Three kinds of operation, and each has one home.**

- **Atomic** — one exchange with the server, or a composite the host does
  natively (`MOVE`, with the transport's `COPY` + `EXPUNGE` fallback where
  the server lacks it). It lives in the provider's transport, and a utility
  passes it straight through.
- **Syntax sugar** — another spelling of an operation that already exists
  (`--unread` as `UNSEEN`, `--older-than 30d` as `BEFORE`). It lives in the
  input model, `criteria.py`, so every front-end gets it at once.
- **Utilities** — host-independent logic that composes atomic operations
  and keeps state on the client: the re-check, the existing-mail pass,
  reading one message after another. It lives in `utilities/`, one module
  per subject.

**The provider side only talks to the server.** The operator, 2026-09-28:
*"any activity on the provider side ... only handles communication with that
provider's server, it doesn't manage loops (unless the protocol supports it),
it doesn't manage the building or validation of a filter, it just tries to
save it and reports success or failure"*. Building, validation, and
presentation are the utilities', which ask the dialect for the host-specific
parts. Connection routines stay apart from the utilities so a failure says
which it is: a transport call that failed, or a utility that composed the
calls wrongly.

**Interfaces call only utilities.** A front-end may open a session —
`engine.connect`, or `engine.capabilities_for` to learn what to offer — and
hand it to a utility. It never reaches the session's transport, connection,
or dialect itself, and imports no provider and no component. That is what
puts every write behind the safety policy, whichever front-end asked for
it. `tests/test_layer_purity.py` holds the lines: a component imports no
layer above it; the session, the utilities, and the interfaces import no
component; the session and the utilities reach a provider only through
`providers.base` and `providers.registry` and never name one; and the
interface guard holds a front-end to utilities, the session's opening
calls, and the neutral model. An interface is whatever module is not the
core, so the API is under the guard without being listed.

**Nothing below the front-end writes to the terminal, progress included.**
`--verbose` protocol chatter leaves `SieveSession` and `ImapSession` through
a `progress` callback, and the steps of a change (backup written, script
uploaded, folder created) leave the utilities through an `on_event`
callback; the CLI decides whether and how to show either. The same
`progress` callback carries a `ServerAlert` when an IMAP server sends an
`ALERT` (`providers.base.Progress`), or a ManageSieve server a PUTSCRIPT
`WARNINGS` (RFC 5804 section 1.3; CHECKSCRIPT's are verbose progress), and
the CLI shows each one on stderr with or without `--verbose`, the same text
once per run. The API sends the same three to its client as JSON-RPC
notifications: each step of a change as `event`, a server's alert as
`server.alert`, always and the same text once per session, and the
protocol chatter as `log`, only where `initialize` asked for it. Do not add
a second output path beside them.

**A core message names no front-end's flag or command.** It states the
condition and carries a `MailctlError` code, which the front-end renders
with its own controls (`cli.ERROR_TEXT`, [#51][i51]). Where it points at
another operation, an error or a record names the operation in a field
(`operation`), never `mailctl <command>`; the CLI renders the command line
(`cli.command_for`, [#183][i183]). The API hands its client the error
document in the JSON-RPC error's `data`, and the client branches on its
`code`, never on the message. So every error in the package carries a
code, the interfaces' own included, since a refusal the CLI makes reaches
a script's `--json` line as surely as the core's; and
`tests/test_core_no_presentation.py` walks every module for one built
without ([#331][i331], [#352][i352]).

## The old name

**The tool was `mxfilter` until #45, and the rename is a clean break.** The
old config directory (`$XDG_CONFIG_HOME/mxfilter/`) and the old `MXROUTE_*`
setting names are **not read** — no dual-reading period, no fallback. What
stops the break stranding anybody silently is detection plus a switch, not
compatibility:

- **The old directory is a finding.** While it exists and the new one does
  not, `utilities.migration.check_config_dir` returns `ConfigDirPending`
  and the CLI warns loudly on stderr before every command, naming `mailctl
  config migrate`. That command is the switch the scoping rule asks for: a
  plan/execute pair in the migration utility (`plan_config_migration` /
  `execute_config_migration`) that lists what moves, honours `--dry-run` and
  `--yes`, moves by rename so every file keeps its mode, merges into a new
  directory that already exists, refuses outright if anything at the
  destination would be overwritten (coded `migration_conflict`), points a
  `config.toml` `password_file` / `backup_dir` that named the old
  directory at the new one, and removes the old directory once empty. A
  path reached through a variable keeps the variable as written; one only
  a variable reaches is refused, coded `migration_reference_unrewritable`,
  rather than its value written into the file. The execute derives the
  plan again and refuses, coded `migration_changed`, before anything moves
  if either directory no longer matches it. The API offers it as
  `config.migrate.plan` / `.execute` ([#279][i279]); its plan carries
  `config.toml`'s path keys, never its text, and its result says
  `restart: true`, because a process reads its settings once and only one
  started after the move reads what moved.
- **An old setting name is a finding, by name only.**
  `config.LEGACY_ENV_NAMES` is the table of every old name and its new one.
  An old name present — in the environment or the env file — without its new
  one comes back as a `LegacySetting` (old, new, where), and the CLI warns
  with exactly that. **No value is read, compared, printed, or kept:**
  presence is tested with `in`, and the env-file parser records the old key
  and drops its value, because one of these is the password. An old
  `MXROUTE_PASSWORD` is therefore also never held to the env-file mode
  check — it is never used.
- **The API reports the same two findings** ([#307][i307]), as `old_name`
  in `initialize`'s result. `settings` lists each old name with its new
  one, where it was found (`environment` or `env file`), and the env
  file's path; `config_dir` is null, or the old directory, the new one,
  and the operation that moves it, `config migrate`. Names and places
  only: no old value is read, so none can be sent.
- **The script created under the old name is still ours.** A new account
  gets a script called `mailctl` (`utilities.scripts.DEFAULT_SCRIPT_NAME`);
  with nothing active, an existing `mxfilter` script
  (`utilities.scripts.LEGACY_SCRIPT_NAME`) is reused rather than a second
  one created beside it.
- **`MXROUTE_FORBIDDEN_ACTIONS` keeps its vendor name.** It is a fact about
  MXroute, not a setting of this tool (*Rule conventions*).

[adr7]: ../../adr/0007-interfaces-utilities-session-provider-layering.md
[adr6]: ../../adr/0006-two-layer-component-and-provider-architecture.md
[adr8]: ../../adr/0008-json-rpc-api-interface-for-out-of-process-clients.md
[i280]: https://github.com/harleypig/mailctl/issues/280
[i346]: https://github.com/harleypig/mailctl/issues/346
[i39]: https://github.com/harleypig/mailctl/issues/39
[i439]: https://github.com/harleypig/mailctl/issues/439
[i204]: https://github.com/harleypig/mailctl/issues/204
[i341]: https://github.com/harleypig/mailctl/issues/341
[i352]: https://github.com/harleypig/mailctl/issues/352
[i377]: https://github.com/harleypig/mailctl/issues/377
[i232]: https://github.com/harleypig/mailctl/issues/232
[i362]: https://github.com/harleypig/mailctl/issues/362
[i402]: https://github.com/harleypig/mailctl/issues/402
[i349]: https://github.com/harleypig/mailctl/issues/349
[i376]: https://github.com/harleypig/mailctl/issues/376
[i89]: https://github.com/harleypig/mailctl/issues/89
[adr9]: ../../adr/0009-python-textual-tui-as-a-separate-package.md
[i51]: https://github.com/harleypig/mailctl/issues/51
[i183]: https://github.com/harleypig/mailctl/issues/183
[i331]: https://github.com/harleypig/mailctl/issues/331
[i279]: https://github.com/harleypig/mailctl/issues/279
[i307]: https://github.com/harleypig/mailctl/issues/307
