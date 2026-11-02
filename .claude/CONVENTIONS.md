# mailctl Conventions

Repo-specific conventions. The global `~/.claude/` config carries everything
generic (git/gh, code style, the Python toolchain via `python.md` +
`ruff.md`, the QA dimensions). This file records only what is specific to
**this** repo.

## What this is

`mailctl` — an MIT-licensed Python CLI that manages MXroute email filters end
to end. It does two things, and the second is the reason it exists:

1. **Creates server-side Sieve filters over ManageSieve**, merging the new
   rule **non-destructively** into the account's existing active script (see
   *Rule conventions* below and [ADR 0002][adr2]).
2. **Applies the same rule retroactively over IMAP** to mail already sitting
   in the mailbox — because Sieve only ever runs on **new incoming** mail.
   Writing the Sieve rule alone leaves every message already delivered exactly
   where it was.

They are two commands, `filter add` then `filter apply` (*The command
surface*).

Distribution name and package are both `mailctl`; the console entry point is
`mailctl = "mailctl.cli:main"`. It is **not published anywhere** — see
[RELEASING.md](../RELEASING.md).

### The old name

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

### The scoping rule

Stated by the operator, 2026-08-14, and recorded because it settles scope
questions rather than merely describing the tool:

> The general purpose of this app is to create ways of managing filters and
> settings, either by switch or automation.

Read it as a **test to apply**, not a mission statement. "Is this in scope?"
becomes *is it a filter or a setting, and is there no way to manage it here?*
— and if so, the answer is yes, without needing a decision.

Two words carry the weight:

- **Settings, not just filters.** A folder's subscription state, a rule's
  position, a capability the account has — anything the account holds that a
  user might want to change is in scope. The two halves in *What this is*
  above are what the tool was **built** for; they are not its boundary.
- **By switch or automation.** Both, and neither is the poor relation. A
  thing you can only do by hand and a thing you can only get as a side effect
  of something else are each half-built.

**What this rules out** matters as much: reading a setting, warning about it,
and telling the user to go fix it somewhere else. That is the shape a
management tool should not end on — it is a report wearing a command's
clothes.

**Finding and viewing messages is in scope** — listing the messages a set of
criteria matches, with their UIDs, and showing one message's headers, text,
and attachment names. Apply the test to it: a filter is built from what the
mail says, so seeing the mail is part of managing filters, and a tool that
cannot show it sends the user to another client to learn what to match on.
The operator, 2026-09-27, wanting it in every interface: *"I want the same
functionality for all interfaces"* — see *Every command is in every
interface* below.

**Roundcube's filter and search UI is not a limit on what mailctl writes or
offers.** The operator, 2026-09-29: *"one of the reasons I'm writing this
app is because the search and filter functions are so restricted."* And
the same day, on [#219][i219]: *"we aren't using roundcube, we're replacing
it and taking ideas for functionality from it."* So a rule mailctl writes
need not be one Roundcube's UI can edit, and nothing mailctl shows the user
is worded the way Roundcube words it (*What the user sees is the same for
every provider*, below). The merge still holds ([ADR 0002][adr2]): Roundcube
may still write the same script, so the rules it wrote are kept. Rules also
stay a flat list of `# rule:[NAME]` blocks, on grounds of their own: the
name is how every command finds a rule, and `mailctl/rules.py` reads the
rules as one ordered list, so it cannot judge a nested rule.

It cuts the other way too. Scope is not *everything about email*: composing
and sending, an address book, account provisioning, and saving or opening
attachments are not filters or settings on this account, and the sibling
[terraform-provider-mxroute][provider] owns account state as code (see *The
sibling repository* below). Viewing a message stops at what building a filter
needs: an attachment is named, never saved or opened.

**A worked example**, so this reads as a rule rather than a slogan. Four
observations came out of the subscription work
([#38](https://github.com/harleypig/mailctl/issues/38)) and
were surfaced as open questions: no CLI way to subscribe an existing folder;
`folder list` and `server test` silent about subscription; an unsubscribed
folder left alone; a flag with no effect. Applying the rule, three are not
questions at all — subscription is a setting, so exposing it is in scope by
definition ([#42](https://github.com/harleypig/mailctl/issues/42)) — and the
fourth is a plain bug ([#43](https://github.com/harleypig/mailctl/issues/43)).
Asking was the error.

Built on two libraries, both of which the code wraps rather than exposes:

- **`sievelib` (≥ 1.5.0, < 2)** — the ManageSieve client
  (`sievelib.managesieve`), the Sieve parser, and the `factory.FiltersSet`
  script builder.
- **`IMAPClient` (≥ 4.1, < 5)** — the IMAP half.

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
  - `reports.py` — the probes behind `mailctl server test`, the provider's
    own words for its reports and its connection facts, and each
    extension's state.
  - `server_report.py` — which servers no module recognised, and the
    redacted issue body `server probe --report` prints for them ([#39][i39]).
  - `server_test.py` — everything `server test` reports, gathered as one
    record from the same probes (`examine_servers`), read-only, for a
    front-end that wants the whole of it at once; the API's `server.test`
    returns it.
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
- `tests/` — pytest, mirroring the package layout ([TESTS.md](TESTS.md)).
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

## The core returns data; only the interfaces print

**`config`, `criteria`, `rules`, `components/`, `providers/`, the session
in `engine.py`, and the `utilities/` that do the work return structured
values and raise `MailctlError`. Every piece of rendering, prompting,
confirmation, and progress output lives in an interface: `cli.py`, or the
API in `mailctl/api/`.** Two reasons, both cashing out now: the core stays
testable without capturing stdout, and a second front-end sits on the same
core instead of requiring it to be torn apart first — the API did
([ADR 0008][adr8]).

**Four layers, and each calls only the one below it** (operator,
2026-09-28, [#137][i137]; [ADR 0007][adr7] holds the reasoning):

```text
interfaces  cli · api               parse input, render output, ask
utilities   mailctl/utilities/      the work, and the safety policy
session     engine.py               config → provider → open, keep, close
provider    dialect                 offline: neutral model ⇄ host language
            transport               communication only
```

**The API is the second interface, and a UI is its client.** A TUI, GUI,
or web UI is presentation only, may be written in any language, and lives
outside the package, calling the API rather than the utilities
([ADR 0008][adr8]). Over stdio its one peer is the process that started
it, so the client is not authenticated: it already runs as the same user.
**The first client is the TUI** in `clients/tui/`, in Python on Textual
([ADR 0009][adr9]): it starts `mailctl-api`, imports nothing from
`mailctl`, offers only what `initialize`'s capabilities allow, and
renders the API's documents rather than re-deriving them.

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

**The utilities do not know how they were called.** The operator's
instruction, 2026-09-27: *"the engine, the code that does the actual work,
should not know nor care how the app was called (cli, tui, gui, web)"* —
the work named there is the utilities' now, and the session's under them.
So neither takes an `argparse` namespace, imports the CLI, or reads the
terminal or the environment; `tests/test_core_no_presentation.py` enforces
all three. Every change is **plan → decide → execute**: a `plan_*`
function is read-only and returns what would change (a diff, placement
findings, a message preview, counts); the front-end renders it and makes
the decision (`--dry-run`, `--yes`, a confirmation prompt); an
execute-style call carries the plan out. New work goes into the utilities
first; an interface should only gain parsing and rendering.

**Across the API the plan stays in the API process.** A `.plan` method
keeps the plan record in the session's plan table under a fresh id and
returns the id beside the plan's document; the `.execute` method takes the
id and nothing the plan already holds, so what was shown is what runs. An
id is used once: executed, failed, or discarded (`plan.discard`), it is
gone, and using it again is refused, coded `plan_unknown`. Only the
newest `PLANS_KEPT`, 32, plans wait: making another drops the oldest,
whose id is refused the same way ([#346][i346]). That is the
session's *a write is never sent again* held across a lost response. An
execute checks its parameters before it takes the plan, so a request
refused as malformed leaves the plan to run. And an execute of a plan that
changes nothing is refused, coded `nothing_to_change`, as the CLI stops
after showing one; `filter.add.execute` is the exception and carries it
out, as `filter add` does. The refusal is the utilities'
(`events.require_changes`, [#331][i331]), so both interfaces give the one
code.

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

**Safety policy lives in the utilities, not the front-end or the
provider**: the backup before every upload (`scripts.upload_script`, the
one upload path) and the refusal of a filter set changed or switched
since the plan (*Rule conventions*), merge-never-overwrite, the
`--max-messages` ceiling (re-checked when a mail plan is executed), and
the UIDVALIDITY check on a UID before it is used (checked again when a
mark or mail plan is executed) hold whichever front-end calls them.

**Every command is in every interface.** The operator, 2026-09-27:

> I want the same functionality for all interfaces. I honestly don't remember
> if I just didn't explain myself well, or something got lost along the way.
> There may be some parameters differences between cli, tui, gui, and web but
> the commands should all be available in all environments.

So every operation lives in the utilities and is callable by every
front-end — CLI, TUI, GUI, and web. **Parameters may differ per front-end;
availability may not.** A capability wanted for one front-end is built into
the utilities and exposed in all of them; a feature only one front-end has
is a gap in the others, not a design choice.

**An API method is named by its command path, dotted** — `filter.list`,
`mail.search`, and a write command as a pair, `filter.add.plan` and
`filter.add.execute` — so the API and the help name an operation alike;
`initialize`, `shutdown`, `plan.discard`, and `$/cancelRequest` are the
protocol's own.
**The API offers every command but `help`** ([#258][i258],
[#279][i279]). `help` is the CLI's help for its own command line; a client
learns what it can call from `initialize`'s capabilities and the method
set. `tests/test_api.py` holds this in both directions: every other method
is a command of the CLI, and every command has a method unless
`NOT_IN_THE_API` names it with its reason, so a command added later fails
until it has one or the other. A command that changes something is a
pair, without the CLI's `--dry-run`, `--yes`, and `--json`, since the plan
is the dry run and calling execute is the confirmation. `filterset.backup`
is one method, with `dry_run`: nothing on the server changes and the CLI
asks nothing, so there is no decision for a plan to wait on. Each method
opens the halves its CLI command opens. `mail.search` takes `like` and
`build_filter`, the CLI's `--like` and `--build-filter`, and each filter in
`filter.list` carries its neutral `steps` beside its `actions`
([#259][i259]), so a client fills a filter in from a message and words
what a filter does without deriving either itself. `like`'s UID is
refused as `mail.view`'s is: below 1, coded `uid_below_one`, and one the
folder does not hold, `no_such_message` with the folder and the UIDs
([#341][i341]).

**An interface may leave out an operation that does not fit its medium**
(operator, 2026-09-28, [#145][i145]). "Next message" is a stateless
utility: a TUI stepping through a folder calls it, and the one-shot CLI
does not expose it. What is left out is left out for the medium, never
because the work was built into one front-end. The CLI is the one
front-end that offers every operation today, so today this means the CLI
exposes every utility operation that fits a command line.

**The CLI comes first, and it is the baseline.** The operator, 2026-09-28:

> I don't think I've said this before, even though it's been my plan all
> along. I think I thought it was implied. The cli version of mailctl is
> mainly for automating tasks. Focusing on it first is the baseline. It's
> easier to build cli -> tui -> {gui,web} than to backfill.

So the order is **CLI, then TUI, then GUI and web**. The CLI is the
automation surface, and it is built first; a later front-end is built on
what the CLI already exposes, never backfilled into it. Read with the
paragraph above, this is why the CLI exposing every utility operation that
fits it is the standing state rather than a stopgap.

**Per-folder retention is the shape of automation this means**
([ICEBOX.md][icebox-retention] › *Per-folder retention — expire mail after N
days*). Retention only works if it runs repeatedly, and mailctl stays a
one-shot CLI: a scheduler (cron or a systemd timer) runs it, rather than the
tool growing a daemon.

One consequence, because automation is the CLI's job: **its output and exit
codes are a contract a script depends on** — every exit code, and every
output that is data (*The command surface* › `--json`). Change them
deliberately, never as a side effect. The CLI snapshots (TESTS.md) are what
make such a change visible.

## The command surface

**The decisions are the operator's**, 2026-09-28 ([#145][i145]), built in
[#147][i147] through [#155][i155]. `filter add` saves the rule only, `filter
apply` acts on mail already delivered, and `filter add --like` and `filter
apply --like` build the criteria from a message ([#149][i149], as decided on
[#148][i148]).

**Read and write are separate at every layer.** A command, a utility, and a
transport operation each either changes the server or does not, even where
one server command could do both. A new write that has a read twin is a
separate operation, never a mode switch on the read. The transport's
classification (*Providers*) already names each operation's kind;
[#154][i154] guards that a read-only utility never calls a write.

**Commands are grouped, noun first: `mailctl <group> <action>`**
([#219][i219]). The operator, 2026-09-29, replacing the flat commands of
[#145][i145]: *"yes, change the decision, and the groups as above is much
better."* The groups, in the order the top-level help lists them:

```text
mailctl mail      search · view · mark · senders
mailctl folder    list · create · rename · subscribe · unsubscribe
mailctl filter    list · add · apply · remove · move · rename · enable · disable · optimize
mailctl filterset list · show · backup · restore
mailctl server    test · probe · baseline save|show|check
mailctl config    migrate
mailctl help [group [action]]
```

The user-facing noun is **filter**, for every provider; code may keep
`Rule` and `rules.py`. A command is named by its path (`filter add`)
everywhere a command is named: an error's `operation` field, a plan's
`--json` `command`, and the help. Capability gating stays per command, and
a group none of whose commands is offered is hidden whole: `filterset` goes
with `rule_sets`. A hidden command still parses and runs, as a hidden
option does. The old
flat names are a clean break, never aliases: each is refused, exit 2,
naming the command it is now (`cli.GONE_COMMANDS`), coded `gone_command`.
`mailctl help [group [action]]` prints the same as `--help` there
([#153][i153]).

**No option is taken by a prefix of its name** (`allow_abbrev=False`, in
the CLI's parsers and the API's): a script's `--jso` working today would
stop the day an option sharing the prefix is added ([#352][i352]).

**A command line is refused through `cli.Parser`.** mailctl's own
refusals go through `Parser.refuse`, in words that quote nothing typed
but a command or an option's name; argparse's go through `Parser.error`,
which words them by `json_output.rejection` (*Credentials*). Either way
the exit is 2 after argparse's usage block, and the refusal carries a
code: `invalid_arguments` unless it has one of its own, such as
`gone_command`, `gone_option`, or `setting_invalid` for a bad value given
to a connection flag ([#352][i352], [#364][i364]).

**`--provider` and `--env-file` may come before the command, between a
group and its action, or after it** ([#243][i243], [#265][i265]); given
more than once, the later wins. Before the command or its action,
`--env-file` needs its PATH: a bare one would take the next word of the
command as its file, so that is refused, naming it. `config migrate` reads
no settings, and refuses either given anywhere before `migrate`.

**`filter add` saves a filter; `filter apply` acts on mail already
delivered.** `filter add` writes the rule into the active script and touches
no message; `filter apply` is the only command that acts on existing mail
([#149][i149]). So `--max-messages` and `--move-threshold` are `filter
apply`'s and not `filter add`'s, `filter add` asks nothing and takes no
`--yes`, and `filter add` ends by saying the mail already there was left alone
and pointing at `filter apply`. Both take their criteria as flags **or** as
`--filter FILE|-` (a filter file, or `-` for stdin), never both — `--match`
and `--compare` count as flags, since the document carries its own — and both
take `--like UID`, which pre-fills the criteria from a message in `--folder`.
`--like` merges with criteria flags exactly as `mail search --like` does, and
is refused with `--filter`, since each supplies the whole set. Every one of
these refusals is made before connecting.

**Dates and message state select delivered mail only** ([#152][i152]).
`--since`, `--before`, `--older-than`, `--unread`, and `--flagged` are
`mail search`, `mail senders`, and `filter apply` criteria, always ANDed with
the rest. `filter add` refuses them before connecting, from flags or a
`--filter` document, because a message being delivered is new, unread, and
unflagged; and `filter add` has no date `--before`, since its `--before NAME`
places the rule. `--body` is in all four, and is always a substring test.

**The split was decided over a recorded objection** (operator, 2026-09-28):
it puts the tool's two halves, the rule and the existing mail, in two
commands. `filter apply` taking the same `--like` and `--filter` as `filter
add` is what keeps the two in step.

**`mail search` finds messages and never writes.** It replaces `messages`
([#147][i147]):

- `--like UID` pre-fills the criteria from a message, and `--build-filter
  [--json]` prints the filter those criteria make and saves nothing
  ([#148][i148]). A criteria flag given with `--like` replaces what was
  derived for its own header and adds any other; `--match` and
  `--compare` govern the whole set (`criteria.merge_criteria`). The
  `--json` form is the filter document `filter add --filter` and `filter
  apply --filter` read: versioned, criteria only, written and read by
  `criteria.dump_filter` / `load_filter`, which refuse a version or a key
  they do not know.
- `--raw QUERY` is the escape hatch, a query in the host's own search
  language. Each provider declares it, and it is offered only where
  declared, like every other capability-gated option (*Providers*).

**`filter rename OLD NEW` changes a rule's name and no other byte**
([#216][i216]). The dialect rewrites the name marker where it sits and
reads the result back, so the rule's body, position, and disabled state,
and every other rule and comment, are kept exactly, and the diff is of the
script's own bytes. An unknown OLD, an empty or taken NEW (coded
`rule_name_taken`), and a NEW the dialect cannot read back as itself are
refused before any upload.

**`mail view` stays read-only; `mail mark` is its write twin.** `mail mark
UID...` takes `--read`, `--unread`, `--flag`, `--unflag`, `--keyword K`, and
`--no-keyword K`, several at once, over separate add-flag and remove-flag
transport operations ([#150][i150]).

**A UID from an earlier run can carry its folder's UIDVALIDITY**
([#204][i204]). `mail search` shows the value, and `mail view`, `mail mark`,
and `--like` take it as `--uidvalidity N`, one per command since every UID a
command takes is in its one folder. A mismatch refuses the command, coded
`uidvalidity_changed`, before any UID is used; a bare UID is still taken
unchecked.

**`mail senders` counts a folder's mail by address, domain, or List-Id and
never writes** ([#160][i160]): one search, then headers a page per FETCH,
refused above `--max-messages` (default 5000) before any header is read.

**`--json` is offered where the output is data a script consumes**
([#151][i151]). A report for a person, such as `server test`, offers none, and
its layout is not a contract; the contract is its exit code. The API's
`server.test` returns a document of its own instead, built from the same
probes by `utilities.server_test`, never from the CLI's text. On a write
command `--json` prints the `--dry-run` plan and is refused without
`--dry-run`, since a document on stdout leaves no room for a prompt; stdout
holds the document alone, and a failure is one JSON line, the last on
stderr, carrying the refusal's code where it is one, and its `fields`
where it has any: the error document the API answers with, built by
`json_output.error` (`cli.failure_line`). A field that cannot be
serialised drops the fields, never the line or its code ([#374][i374]).
A command line
refused before any command runs is the same: under `--json`, wherever it
stands, exit 2's usage block and error line end with a coded JSON line
([#352][i352]).

## Providers

**A provider is a two-way translator** ([ADR 0006][adr6] *Amendment*).
The utilities speak one provider-neutral model: a rule is criteria plus an
`ActionSpec`, and the model also covers folders, messages, capabilities,
results, and `MailctlError`. The provider converts that model into its
host's terms on the way out, and converts the host's answers back on the
way in. For `mxroute`, outbound is Sieve over ManageSieve, and inbound a
parsed script becomes `Rule` values.

**It does so in two halves** ([ADR 0007][adr7]):

- **The dialect is offline and host-specific.** It translates, parses, and
  edits (for `mxroute`, Sieve in Roundcube's `# rule:[NAME]` form), holds
  the host's refusals and its words for the server reports as data, and
  never touches the network.
  `tests/test_layer_purity.py` holds a dialect's modules — derived by
  following its imports, not listed — to opening no connection.
- **The transport is communication only.** Each operation is one exchange
  with the server, or a composite the host performs natively. It does not
  loop unless the protocol does, and it builds and validates nothing: it
  tries to store what it is handed and reports success, or raises
  `MailctlError` with the server's answer. The same guard holds a
  transport's modules to importing no building helper and no utility.

**Every transport operation is classified**, once, on the `Transport`
interface in `base.py`: which half it uses (`rules` or `mail`) and whether
it is a **read**, a **write**, or a **connection** operation (`open`,
`connect`, `disconnect`, `dropped`, the `has_*` answers). One read per half
is also marked that half's **probe** (`Operation.probe`): `list_rule_sets`
on rules and `mail_noop`, an IMAP `NOOP`, on mail. A probe must be a real
exchange with the server, never an answer cached at login, which is why
neither capability read serves and the mail half has `mail_noop`. The
session reads that classification and never asks which provider it has.

**The session is lazy, and what it does around each call it reads from the
classification** (`engine.Session`):

- **Each half connects the first time a utility uses it**, and only if the
  command asked for it; a command that only reads rules never opens IMAP.
  The session closes what it opened, last first, when it ends.
- **A read the server cut off is sent once more on a fresh connection.** A
  second failure is raised, and so is a refusal — `dropped` tells the two
  apart.
- **A write is never sent again.** It may have landed before the
  connection went; a retry could land it twice, and would hide the failure
  the user needs to see. A failed write is final for that command.
- **A write on a half idle for a minute is preceded by its probe**
  (`engine.IDLE_PROBE_AFTER`, 60 seconds). A server may log an idle client
  out, and a long-lived front-end's first call after a while may be a
  write, which would then fail for nothing. The probe is a read, so a
  connection dropped while idle is found by a call that may be sent again,
  and the write goes out on a connection known to be live. A half not yet
  connected, or used within the minute, sends none; a probe the server
  refuses stops the write.
- **One call at a time per half**, under a lock, since IMAP and
  ManageSieve are both one command after another; and each mail call names
  its folder rather than trusting the one a previous call left selected.
- **A front-end may stop a call before it is sent.** `engine.connect` and
  `Session` take a `checkpoint`, a third callback beside `progress` and
  `on_event`, called before every transport operation, True for a write,
  and before the half is connected; what it raises stops the operation
  unsent and reaches the caller as raised. The utilities never know it is
  there, so every loop in them stops between exchanges, never during one
  ([#280][i280]).
- **A session owns its connections, and nothing is shared between
  sessions.** There is no module-level connection state: a long-running
  front-end keeps one session per user.

**The CLI keeps its shape**: one command opens a session with the halves it
needs, runs, and closes it. It uses the session's lazy open, so bad input
fails before any login or password prompt (decided on [#137][i137]).
**The API holds one session for its process**: `initialize` resolves the
configuration from the process's own arguments, as the CLI does, and opens
the session, connecting nothing; `shutdown` or the end of input closes it.
A bad argument does not end the process, which a client would read as the
end of its input: it is said on stderr and answers `initialize`, coded,
in the CLI's words for it (*Credentials*; [#352][i352], [#364][i364]).
Requests are handled one at a time, in order. **A request can be cancelled
between exchanges, never after its first write** ([#280][i280]). The
client sends `$/cancelRequest`, the Language Server Protocol's
notification, naming the request's id, and the API's checkpoint stops it
before its next exchange, answered `-32800` with `written` false and the
path of any backup it wrote first. Once a write is sent the request runs
to its end and answers as it would have, since that write may have landed
and is never sent again. A cancel names only a request sent before it,
the one in hand or one waiting its turn ahead of it; any other id is
ignored, so a late cancel never stops a later request reusing the id.
A cancel sent behind 64 messages not yet served is not seen until some
are served, and a `credentials.prompt` waiting while that many are held
is refused, coded `prompt_input_full`, since its answer could no longer be
read ([#346][i346], [#378][i378]).

**How a provider is chosen, declared, and held to its interface:**

- **Selected by the `provider` setting**, default `mxroute`, resolved like
  every other setting: `--provider`, then `MAILCTL_PROVIDER` in the env
  file or the environment, then `provider` in `config.toml`. An unknown
  name is refused, naming the known ones, before anything connects.
- **In-tree and registered in-tree.** A provider is a `Provider` record —
  its name, its `ProviderCapabilities`, its dialect class, and its
  transport class — listed in `providers/registry.py`. There are no entry
  points; ADR 0006 defers them.
- **Differences are data, never a branch.** A provider declares
  `ProviderCapabilities`: `ordering`, `stop`, `rule_sets`, `disable`,
  `rename`, its `actions`, `extensions`, `raw_query`, `mark`,
  `folder_counts`, `uidvalidity`, `folder_visibility`, its namespaced
  `specifics` with their schema, the connection `settings` it reads, and
  the operations it `declined`. The utilities read those and never ask
  which provider they have.
- **Refused before any network work.** `utilities.rules.check_rule`
  refuses a rule the provider cannot express, through one error naming the
  provider, the construct, and why (`providers.base.refuse`). It takes a
  code: `unsupported` for what the provider declares it cannot do, which a
  front-end can learn before asking, or one of its own where the server
  or a setting is the cause ([#352][i352]).
- **Every provider answers every operation.** Each half implements or
  explicitly declines (`@declined`) every operation of its interface —
  `Dialect` or `Transport`, `mail_noop` among them — and what the two
  decline matches the
  provider's capabilities. `tests/test_providers.py` holds this. It also
  drives a fake second provider through the utilities to show the calls
  match `mxroute`'s.
- **The neutral model is the provider layer's own.** `providers/model.py`
  defines every record the utilities and a front-end build or read, and it
  and `base.py` import nothing from a component. A component keeps its
  own records, and the provider translates (`mxroute/records.py`,
  `mxroute/sieve.py`), so a provider with no Sieve never imports the Sieve
  component ([#99][i99]).
- **What the user sees is the same for every provider** (operator,
  2026-09-29, [#219][i219]). The user-facing nouns are *filter* and *filter
  set*, whatever the host calls them, and never *rule* or *script*; code
  names such as `Rule`, `rules.py`, `RulePlan`, the `rule_sets` capability,
  and `script` may stay. The options follow: `--move-to` (was
  `--fileinto`), `--filterset` (was `--script`), and `--no-mail` (was
  `--no-imap`), each old name refused naming the new one
  (`cli.GONE_OPTIONS`, beside `cli.GONE_COMMANDS`); and a `--json`
  document's `filter` / `filters` / `filterset` / `filtersets` keys and its
  folder `status` values (`create`, `created-on-delivery`,
  `create-and-on-delivery`). A provider's own words reach the user in three
  places only:
  1. documentation about the provider, such as its `RECORD.md`;
  2. the help of an option only it has — `--disable-extension`, and the
     connection settings it declares (`--host`, `--imap-*`, `--sieve-*`,
     which keep their names) — and a refusal of a provider-only function,
     such as an extension it lacks or an action it forbids;
  3. `server test`, `server probe`, and the baselines, whose job is to
     describe the servers.

  Everywhere else — every other help page, prompt, progress and event
  line, error, the diff's heading, a rule's actions, and the frame
  `filterset show` puts round the source — is fixed text in the front-end.
  The filter set's own text, in a diff or `filterset show`, is the host's
  language and is shown as it is; so is a server's alert or warning, and
  `--verbose`'s record of the exchange. `tests/test_neutral_wording.py`
  holds this under `mxroute`, over every help page read off the parser and
  every snapshot but the server reports'.
- **The host's words for its reports are the dialect's data.** A
  front-end lays out one report for every provider and fills it from
  `Provider.wording` — its service names, what its extensions are called,
  what it calls a rule set (for the unrecognised-server report), and its
  closing notes such as MXroute's redirect and Exim/DirectAdmin notes —
  and from `connection_facts` and `mail_facts` (labelled lines), each
  reached through a utility; `Wording` holds nothing else. What a rule's
  actions do comes back from `describe_actions` as neutral `ActionStep`
  records the front-end words. `DisplayDiff.label` names the diff's
  language for a `--json` plan, and is never a heading. Nothing about one
  host is written into `cli.py`.
- **A default comes from the capabilities.** `ActionSpec.stop` None is the
  provider's default, which `utilities.rules.resolve_stop` settles from the
  `stop` capability: a host that cannot stop is never asked to, so its
  default rules are not refused. `--no-stop` sends False; an explicit True
  is still refused where `stop` is not declared.
- **Offered only what it declares** ([#26][i26] constraint 4). The CLI parses
  twice: a first pass reads only `--provider` and `--env-file` and resolves
  the provider, then the parsers are built from its capabilities. Without
  `ordering`, the placement flags, `filter move`, and `filter optimize` are
  not offered; without `stop`, `--no-stop`; without `rule_sets`, `filter
  add`'s `--filterset` and `--activate`; without `disable`, `filter
  disable` and `filter enable`; without `rename`, `filter rename`; without
  `extensions`, `--disable-extension`; without `raw_query`, `mail search`'s
  `--raw`; without `mark`, `mail mark`; without `folder_counts`, `folder
  list`'s `--counts`;
  without `uidvalidity`, `--uidvalidity`; without `folder_visibility`,
  `folder subscribe`, `folder unsubscribe`, and `--no-subscribe`, and a
  folder is created and renamed with no subscription step and listed with
  no subscription wording ([#39][i39]). The connection flags (`--host`,
  `--imap-*`, `--sieve-*`) are offered only where
  `ProviderCapabilities.settings` names them, with the help it gives — they
  keep their names and their `MAILCTL_*` variables, being `mxroute`'s
  connection options. An unoffered option is **hidden, not removed**: given
  anyway it still parses and meets the refusal below the front-end naming the
  provider, which stays the backstop for every front-end. A first pass that
  cannot resolve a provider falls back to the default provider's offer, and
  the run reports the problem. `mxroute` declares everything, so its help is
  what it always was.
- **A setting belongs to the provider that can use it.**
  `disabled_extensions` keeps its name and its ladder, but only a provider
  declaring `extensions` takes it; any other refuses it, before
  connecting, naming the provider and where the setting came from. A
  connection flag a provider does not read is refused the same way. An
  ambient connection setting from the environment or the config file may
  serve another provider, so only a flag is refused.
- **Adding one is a record, a package, and a registry line**:
  `providers/<name>/RECORD.md` first (*Providers are probed and read*),
  then `providers/<name>/` — a dialect, a transport, and the `Provider`
  record naming them — composing the layer-1 components it needs, with no
  change to the session or the utilities.

## The protocols

There is no vendor API here — the tool speaks two standard protocols.

- **ManageSieve** — RFC 5804. IANA reserved **TCP 4190** for it, and the RFC
  requires both ends to implement **STARTTLS** (*"Client and server
  implementations MUST implement the STARTTLS extension"*). That is the
  tool's default, and it is a **default, not a documented fact about
  MXroute**. A probe observed it working on one server; see the MXroute
  [record][rec-mxroute].
- **IMAP** — port **993** (implicit TLS) or **143** (STARTTLS). The username
  is the **full email address**, and the hostname is **per-account**
  (MXroute's panel gives it as "the same as your primary MX record"), so it is
  always configuration and never a built-in default.

Every setting resolves highest-priority-first: a CLI flag → a `MAILCTL_*`
line in the `--env-file` file → a `MAILCTL_*` environment variable → the
TOML config file (`$XDG_CONFIG_HOME/mailctl/config.toml`) → a built-in
default. The env file is read, never exported: `load_config` takes the
environment as a mapping and layers the file over it, so `os.environ` is
never written. Where each setting came from is recorded on the resolved
`Config` (`sources`, `consulted`) as data, so a front-end can report it and
an error message can say "default" only about a default. The password is the
exception and is handled separately (*Credentials*).

**`MAILCTL_BACKUP_ROOT` is the one other exception** ([#230][i230]). It
limits `backup_dir` rather than supplying a value, so it is read from the
process environment only: a rung that could set it could lift it. Where it
is set, a backup outside it is refused, coded `backup_outside_root`, before
anything connects and again where each backup is written (*Rule
conventions*).

### Confidence — documented, observed, and unknown

Every fact mailctl relies on about a host sits in one of three tiers. The
tiers are **documented** (the host, or the software it runs, says so),
**observed** (a probe saw it on one server on one day), and **unknown**.
Mark each fact honestly, and do **not** quietly promote one tier to
another. An observation is stronger than a guess and weaker than
documentation. It describes *that server*, not the host. Unknowns are stated
plainly rather than filled.

MXroute documents very little of its Sieve surface. That gap is itself a
design driver: it is why *Discover, don't hardcode* below is a rule rather
than a preference. **The facts themselves live with the provider**, one
record per provider, and each record is kept in these three tiers:

| Provider | Record |
|---|---|
| `mxroute` | [`mailctl/providers/mxroute/RECORD.md`][rec-mxroute] |

Code and docs that need a host fact cite the provider's record, not this
section. This section owns the discipline.

### Providers are probed and read

The operator's requirement, 2026-09-27:

> during development and maintenance the provider, if possible, needs to be
> probed as well as documentation read to make sure we're able to take full
> advantage of the service.

So a provider's record has two jobs. It says **what the provider can rely
on**. It also says **what the host offers that mailctl does not yet use**,
in a *Not yet used* section, so that "take full advantage" has somewhere to
land. **Both halves are refreshed together**: read the documentation again,
and probe where a probe is possible.

**A record is keyed on observed capabilities and behaviour, with dates,
never on a version number.** Hosts run different server versions at
different times, and servers hide their version: MXroute's reports none on
either protocol ([#18][i18]). A version a server does report is a hint.

**Refresh a provider's record:**

- **when the provider is built.** A new provider, Gmail for example,
  **starts with its record, written before its code**. What the host
  documents and what a probe shows are the inputs to its capability
  declaration, so they come first.
- **when the host announces a change.** For MXroute, the standing cases are
  the Dovecot 2.3 → 2.4 migration and the replacement of Roundcube,
  Crossbox, and DirectAdmin. Each record lists the announcements it has
  seen.
- **before relying on an old observation for a new feature.** Re-probe
  before building on an observation older than the cadence below. It
  describes one server on one day.
- **quarterly, at the latest.** Refresh any record whose newest observation
  is more than 90 days old.

**Why quarterly, and not each release.** Tags here are cheap and publish
nothing ([RELEASING.md](../RELEASING.md)), so they come in bursts and
droughts that have nothing to do with the host. A release-keyed refresh
would run three times in a busy week and then not for months. The host's
own rhythm is the one that matters, and MXroute's releases have come a few
months apart: 3.2 in June 2025, 3.9 in December 2025, 4.0 in January 2026,
and 4.0.1 in May 2026. So a quarter catches a change nobody announced
within about one host release. The trigger is the date on the record, which
anyone can check. Nothing automates it yet.

**What a probe is, today.** A probe is a read-only live session against one
account, recorded with the command, the date, and the server's hostname:

- **`mailctl server probe`** prints, dated and with the hosts configured, each
  server's identity (IMAP `ID`, ManageSieve `IMPLEMENTATION`) and its whole
  capability list — saying whether it was read before or after login — the
  Sieve extensions, the active script, the delimiter, and the namespaces
  ([#101][i101]). `--json` is the same as a versioned document with sorted
  lists, so two probes of an unchanged server differ only in the time.
- **`mailctl server test`** adds the folder and subscription counts and what
  `MOVE`, `UIDPLUS`, and `FILTER=SIEVE` mean for mailctl, and one line on
  drift where a baseline is saved.
- **`mailctl server baseline save`** stores the probe as the host's baseline,
  and **`mailctl server baseline check`** reports drift from it (below).
- **`make testlive`** is the live tier's read-only smoke tests. It
  confirms the port, the TLS mode, and the delimiter.
- **`make livecheck`** runs the read-only CLI checks against the account
  and confirms nothing changed (TESTS.md › *The read-only live check*).

A probe never prints the password. It is held to the same bar as a debug
shim (*Credentials*).

**The automated form is built** ([#18][i18], [#19][i19]).
`server baseline save` writes the probe to
`$XDG_CONFIG_HOME/mailctl/baselines/<host>.json`, the server's part once
and the active script per account; `server baseline check` compares a fresh
probe with it and says what each difference means for the account, exiting 0,
3 (informational drift), or 4 (serious drift). The file is on one machine and
is not versioned, so the record still transcribes what a probe saw; a refresh
is *save a baseline, then copy what changed into the record*. A save is
refused, coded `baseline_changed`, with nothing written, when the file is
no longer what its plan read ([#279][i279]): a plan carries every other
account's part of the file as it read them, so writing over a save made
since would lose that account's part.

### Discover, don't hardcode

**Server capabilities, the active script name, and the folder delimiter are
discovered from the server at runtime — never hardcoded.** This is a named
convention, not a style preference, and the reason is concrete rather than
abstract: MXroute has publicly stated it intends to migrate away from
DirectAdmin, Crossbox, and **Roundcube** this year, and is mid-migration from
**Dovecot 2.3 to 2.4**. Runtime discovery survives both migrations; a baked-in
constant does not.

In practice:

- Take the active script from the server's own listing, not a constant.
- Take the folder delimiter from the server's folder list, and normalize
  user-supplied names against it — so `Lists/GitHub` and `INBOX.Lists.GitHub`
  name the same folder (`components.imap.normalize_folder`).
- Read the advertised Sieve extensions rather than assuming a capability is
  present.

A default is fine where the protocol supplies one (port 4190); an **assumption
about MXroute's configuration** is not.

**Drift from a stored baseline is warned about, never refused**
([#19][i19]): the live server wins, the difference is reported, and
refreshing the baseline is an explicit command that shows the diff and asks.

## Rule conventions

- **Merge, never overwrite.** A new rule is merged into the parsed existing
  script; rules the tool did not write survive untouched. Roundcube's filter
  UI writes the same script, so overwriting silently destroys a user's
  hand-made filters. A parse failure is a **hard stop**, never a
  fall-back-to-overwrite. See [ADR 0002][adr2].
- **Back up before every upload.** The previous script is written to the
  backup directory before the new one is sent, and `mailctl filterset backup`
  takes the same copy on demand. **One location, and it is the config
  directory** — `$XDG_CONFIG_HOME/mailctl/backups`, beside `config.toml`
  (`config.default_backup_dir`), overridable by `--backup-dir` /
  `MAILCTL_BACKUP_DIR`. XDG would call a backup *state*; co-locating it with
  the config is a deliberate departure from XDG, not an XDG-endorsed reading,
  and the reason is that a backup the user cannot find is not a backup. Two
  defaults for one kind of file is how somebody ends up looking in the
  directory that does not have their backup in it. **Where
  `MAILCTL_BACKUP_ROOT` is set, every backup must resolve inside it** —
  the Docker image sets `/config/mailctl` — whichever chose the location:
  `--backup-dir`, `MAILCTL_BACKUP_DIR`, `backup_dir` in `config.toml`, or
  `filterset backup --output`. Paths are resolved first, symlinks included,
  so neither `..` nor a link leads out ([#230][i230]).
- **An upload is refused if the filter set changed since the plan**
  ([#257][i257]). Before the backup, `upload_script` reads the server's
  copy again, and on any byte difference from what the plan was made from
  it sends nothing and refuses, coded `filterset_changed`; what it read is
  what it backs up. Otherwise an edit made in between — the webmail's
  filter UI, another run, a second client — would be overwritten and
  missing from the backup, which is ADR 0002's failure by another road.
  Every execute that uploads goes through it, `filterset restore` and
  `folder rename` included, and a byte compare needs no parse, so a
  restore over an unparseable script ([ADR 0005][adr5]) is held to it too.
  A `folder rename` with no filter to repoint uploads nothing, and still
  reads the filter set and which one is active again before the folder
  moves, refusing the same way before any write ([#332][i332]): a filter
  filing into the old name, added since, would be left pointing at a
  folder that no longer exists.
  **So is an upload once the active filter set is not the one the plan
  was made against** ([#308][i308]): the same code, nothing backed up or
  sent, and the refusal points at `filterset list` rather than at one
  set's text. The plan decided from the active set whether the upload
  activates its own, so a switch made since would be undone, or an edit
  planned as not live would go live, and the plan shown said neither.
- **A backup is the server's exact bytes.** `SieveSession` reads GETSCRIPT
  by the literal's declared length, CRLF and final newline included
  ([#90][i90]), and `write_backup` writes what it was handed, with newline
  translation off, mode `0600` in a directory
  created `0700`. **A backup never replaces a file** ([#330][i330]). It
  is named `<set>-<YYYYmmddTHHMMSS.mmmZ>.sieve`, UTC to the millisecond,
  and created exclusively. Where mailctl chose the name, a clash takes the
  next free one, `<stem>-2.sieve`, `-3`, and on; where the user named the
  file (`filterset backup --output FILE`), a clash is refused and the file
  kept, at the plan too, so `--dry-run` never promises a write the run
  refuses ([#331][i331]). A baseline is the exception: refreshing one is
  an explicit command, so it replaces its file. Nothing decorates a backup —
  `mailctl filterset show` adds banner lines for a reader and is therefore
  *not* a backup, which is exactly the
  trap redirecting `filterset show` to a file used to set. `mailctl filterset
  restore` puts one back over the active script only: it shows a raw diff,
  asks for confirmation, backs up the current script, and runs CHECKSCRIPT
  before sending. It is the one write path that replaces instead of merging,
  and it may replace a script mailctl cannot parse ([ADR 0005][adr5],
  [#13][i13]). It refuses, unopened, a file this run reads the password
  from — a password file from any rung, or the env file, links followed —
  coded `credential_file`, since its contents would come back as the diff
  (*Credentials*).
- **Show, then change.** Every mutating subcommand works out what would
  change, shows it (a diff for the script, a preview for the messages), and
  only then applies it. `--dry-run` stops after the "show it" step.
- **Refuse the actions we will not emit, with a pointer** — and keep the two
  reasons for refusing apart, because the distinction is exactly the
  confidence tiering above:
  - `MXROUTE_FORBIDDEN_ACTIONS` (`providers/mxroute/sieve.py`) holds
    **`redirect` alone**. It is the only action refused because MXroute is
    *confirmed* to disable it, and its message points at forwarders (the
    panel, or the `mxroute_forwarder` Terraform resource).
  - `UNIMPLEMENTED_ACTIONS` (`components/managesieve/script.py`) holds
    **`notify` and `vacation`**. These are refused because *we* do not
    generate them, and their message says so explicitly rather than implying
    an MXroute restriction — no source confirms or refutes their
    availability.

  Collapsing the two would restate an unverified assumption as a server fact,
  which is the failure this repo's *Confidence* discipline exists to prevent.
- **Supported and used:** `fileinto`, `discard`, `stop`, `keep`, and flag
  actions.
- **Every emitted feature declares its extension, in one table.**
  `EMIT_TABLE` (`components/managesieve/emit.py`) maps each command, test,
  and tag a rule can contain to
  the Sieve extension it needs (None for the base language). The required
  set `mailctl server test` reports and the check a rule is held to are both
  read off it, and `tests/test_extensions.py` checks it against sievelib's own
  `require` line over every rule shape — so a new action is added to the
  table, never listed by hand anywhere else.
- **`disabled_extensions` narrows what we emit; it never widens it.** A
  disabled extension counts as not advertised, in plan and in execute: a
  rule that needs it is refused naming the setting, and a fallback is used
  where one exists (`mailbox` off → plain `fileinto`, folder created over
  IMAP). It governs what mailctl writes, not rules already in the script.
- **One criteria model, two translations.** A new matching capability is added
  to `criteria.py` and translated to *both* Sieve and IMAP `SEARCH` — never to
  one side only. Where IMAP `SEARCH` is coarser than the Sieve comparator, the
  results are re-checked client-side against the real Sieve semantics so the
  retroactive pass matches what the filter will do going forward.
- **A further Sieve extension is adopted only when it passes three tests**
  ([#17][i17]), so each candidate is judged against these rather than
  argued from scratch:
  1. **The retroactive pass can reproduce it.** The IMAP half must be able
     to apply the same effect to mail already delivered. An extension with
     no IMAP equivalent makes the two halves disagree, which is the failure
     this tool exists to prevent — that alone is grounds to decline.
  2. **It is registered in `EMIT_TABLE` with a decided absence path.** The
     extension it needs is declared there like every other emitted feature,
     and the rule says what happens on a server that does not advertise it:
     a named fallback, or a refusal before anything is written. Never an
     unconditional `require` that fails on the server.
  3. **The provider's own panel does not already do it better.** Duplicating
     the webmail badly is worse than not doing it; this is why `vacation`
     (autoresponders) stays refused although the server may advertise it.

## Credentials

**The mailbox password never reaches stdout, stderr, a log, or a transcript.**
This is the hard boundary of this repo, the analogue of a write-only secret,
and it is enforced by construction rather than by care:

- `config.Secret` wraps the password and overrides **both** `__str__` and
  `__repr__` to `<redacted>`, which covers every accidental disclosure path —
  `print`, an f-string, `%s` in a log line, and a `repr` in a traceback frame.
- The real value is reachable **only** through `Secret.reveal()`, which is
  greppable and therefore reviewable. Call it only when handing the password
  to a connection method — never to display, log, or format it.
- The password resolves through its own ladder, highest first: **an explicit
  flag** (`--password-file`, `--password-cmd`, `--password` — argparse makes
  them mutually exclusive) → the `--env-file` file's `MAILCTL_PASSWORD_FILE`
  → `MAILCTL_PASSWORD_CMD` → `MAILCTL_PASSWORD` → the same three from the
  environment → `password_file` → `password_cmd` (config file) → an
  interactive `getpass` prompt. Two rules produce that order, and both are
  load-bearing:
  - **A flag beats an ambient variable.** It was typed for *this* run; the
    variable merely happens to be exported. The inverse — which is what the
    code did until the ladder was fixed — silently authenticates as the
    wrong account when `MAILCTL_PASSWORD` is exported for one mailbox and
    `--password-cmd` names another. An env file was named for this run too,
    so **all three** of its rungs sit above all three ambient ones rather
    than interleaving by variable — otherwise an exported
    `MAILCTL_PASSWORD_FILE` for one mailbox would beat the file's
    `MAILCTL_PASSWORD` for another.
  - **A literal value never beats an instruction about where to fetch one.**
- The **literal password is never read from the TOML config file** — only
  `password_file` and `password_cmd` are. That is unchanged.
- **A password file is refused, not warned about, when its mode lets anyone
  else read it.** Any bit in `0o077` is a refusal naming the path, the mode,
  and the `chmod` that fixes it; the file is not opened at all. `libpq`
  applies the same rule to `~/.pgpass`, except that it ignores the file
  silently — here the file was named explicitly, so falling through the rest
  of the ladder without saying so would be worse than stopping. Note for
  WSL: a file on a Windows mount reports
  `0777` regardless of intent, so the file has to live on the Linux
  filesystem — do **not** add a filesystem exception to the check.
- **An env file that sets `MAILCTL_PASSWORD` is held to the password-file
  bar** — any bit in `0o077` is a refusal naming the path, the mode, and the
  `chmod`. The mode is taken from the open handle, so it is the mode of the
  file actually read. The password is wrapped in `Secret` as it is parsed
  and never sits in the file's plain value mapping. A line the parser
  cannot read is reported by **line number only**, never quoted, because it
  may be the password.
- **`--password` is deliberately the least safe rung and says so.** It exists
  because it was asked for; `cli.py` warns on stderr that an argument is
  visible in the process list and saved to shell history. The warning is
  presentation and stays in the CLI; the mode refusal is behaviour and stays
  in the core.
- **The API takes the same ladder without `--password`, and its prompt is
  a request to its client.** Where no rung supplies the password, the API
  sends `credentials.prompt`, and the reply's `password` is wrapped in
  `Secret` as its line is parsed (`api/protocol.py`), before anything
  dispatches or logs it. No method takes a password. A login the server
  refuses, coded `auth_failed`, makes `Config.forget_prompted_password`
  drop a prompted password so the next request asks again; one from a
  file, a command, or a setting is kept. On either interface, a password
  IMAP cannot send, since imaplib sends `LOGIN` as ASCII, is refused as
  `credentials_not_ascii`, quoting nothing. A cancel of the request ends
  the prompt's wait; a late answer to that prompt answers nothing, and the
  next request asks again ([#280][i280]). The half connects before it
  asks, so the cancel also closes that connection ([#349][i349]).
- **A refused command line quotes no word argparse could not use.** That
  word may be a password that lost its option, so `json_output.rejection`
  words every argparse refusal before it is shown — on the CLI's error
  line and its `--json` line, and on the API's stderr and in its answer
  to `initialize`. A long option is named up to its `=`, a short one only
  alone; a word straight after an option, or after `--`, with no `=` may
  be that option's value, so it is counted, never named; and a message of
  no shape it knows reads *the command line was refused*
  (`json_output.REFUSED`), never argparse's text ([#364][i364]). Two
  values are refused below argparse, so the core words them itself, on
  either interface: an unknown provider, `unknown_provider`, and an env
  file that cannot be read or is not UTF-8, `env_file_unreadable` and
  `env_file_not_utf8`, name neither the value nor the path
  ([#373][i373]). The env file's mode and line refusals still name its
  path: the file exists, and the `chmod` needs it.
- **A password command reads nothing of mailctl's stdin.** It runs with
  stdin from `/dev/null` on either interface: under the API stdin is the
  client's pipe, and under the CLI it may be a `--filter -` document
  ([#280][i280]).
- **The API expands nothing in a path a client sends.** The client has no
  shell on the API's host, so `filterset.restore`'s `file` is read as
  given, and every message names it as given: expanded,
  `$MAILCTL_PASSWORD` would be the password. A restore of a password file
  or the env file is refused, coded `credential_file`, on either interface
  (*Rule conventions*).
- The same bar binds test doubles and throwaway debug shims. To tell two
  credentials apart, emit a non-reversible discriminator (a literal
  `set`/`unset`, a length, a short hash prefix) — never the value. See the
  global `CLAUDE.md` *Secret Handling*.

## The sibling repository

[`terraform-provider-mxroute`][provider] and this tool are **complementary,
not overlapping**, and the boundary is clean because the API draws it for us.
(That repository is a *Terraform* provider; it is unrelated to mailctl's own
`mxroute` provider, which is this tool's layer 2 — see *Providers*.)

| Repo | Owns |
|------|------|
| `terraform-provider-mxroute` | account/domain state as code — domains, mailboxes, **forwarders**, catch-all, spam lists, pointers |
| `mailctl` (here) | filter **rules** (Sieve) and retroactive mail sorting (IMAP) |

Two practical consequences:

- **Forwarding belongs there, not here.** It is the substitute for the
  disabled Sieve `redirect`, and it is already implemented as the
  `mxroute_forwarder` resource. Point users at it; do not reimplement
  forwarding in this tool.
- **Filters cannot belong there.** The REST API the provider is built on has
  no Sieve surface at all ([ADR 0001][adr1]).

## Where work is tracked

Work is tracked as **GitHub issues**, not in a planning file. The sentinel
the global `todo.md` reads:

tracker: github

`TODO.md` is gone, and every open item it held was migrated to an issue. A
repo carrying both grows two answers to "what is left to do", and the file is
always the stale one — it has no assignee, no labels, no cross-references, and
nothing closes it when a PR lands.

Two things follow, which is why this is a declared sentinel rather than a
habit:

- **A captured follow-up becomes an issue**, reconciled against the existing
  ones rather than appended blindly.
- **Every issue carries a `role:*` label.** An open issue without one has not
  been triaged (`labels.md`), and that absence is the signal a sweep keys on.

**Deferred work is still not an issue.** A considered "not now" belongs in
[ICEBOX.md](../ICEBOX.md) with its trigger, or as an `ICEBOX:` marker at the
relevant code. Issues are for work someone intends to do; the icebox is for
decisions taken and parked. Filing a deferral as an issue is how a tracker
stops being readable.

### The board

**Every open issue and pull request is on the [Mail Control][board]
board**, user project 4, private (decided on [#277][i277] and
[#278][i278]). It shows what is next and what state it is in; the issue
stays the record.

**Workflows add and move items, so an agent never adds one or sets Status
by hand.** A new issue, pull request, or sub-issue is added as Todo;
linking a pull request to an issue sets it In Progress; closing an issue
or merging a pull request sets it Done, and the merge closes the issue.
What an agent does set, on every issue it files:

- **Size** — `XS`, `S`, `M`, or `L`;
- **Layer** — `interface`, `utilities`, `session`, `provider`, or
  `component` (the layers of [ADR 0007][adr7] and the components under
  them), or `infra/CI` or `docs`;
- **the milestone.**

**Two kinds of issue carry fewer, by design** ([#287][i287]):

- **A milestone's introspection issue carries no Layer.** It is about how
  the work went, not about a layer of the code, and no Layer option
  describes it; it still carries a Size and its milestone.
- **An epic carries no Size or Layer.** Its sub-issues carry them, so the
  epic's own would only repeat theirs or contradict them. It carries a
  milestone only when every sub-issue sits in one, as [#418][i418] does
  in M8: the *Milestone* view draws sub-issues under their parent, so an
  epic with no milestone pulls them into the *No milestone* group. Where
  its sub-issues span milestones, it carries none.

So an issue missing a field is a gap unless it is one of these. A new
Layer option for the introspection issues was weighed and declined: it
would describe nothing else.

Role and type stay labels, and the board does not repeat them.

**Four views, each for one question.** *Milestone* is a table of open
items grouped by milestone, with Size and Layer: what each milestone still
holds. *Status* is a board by Status: what is in flight. *Needs You* is
the issues labelled `needs-answers`, `needs-shaping`, or `needs-action`:
what waits on the operator. *Bugs* is the open bugs. There is no Roadmap
layout ([dotagents#2322][da2322]).

**`needs-action` is waiting on the operator to do something** — a run, a
setting, an approval — where `needs-answers` waits on an answer and
`needs-shaping` on requirements not yet settled. The operator asked for it
([dotagents#2358][da2358]).

**Milestones are numbered `M0`, `M1`, and on.** Each description is a
one-line goal and a *Done when:* line, and each milestone carries an
introspection issue reporting to [dotagents#2312][da2312].

**Not on the board:** closed history from before v0.12.0, and ICEBOX
entries ever, since a deferral is not an issue (above).

**Workflows are changed in the web UI.** The API can read them and delete
one, but cannot turn one on or set what it does. **Views can be changed
over the API**: GraphQL's `updateProjectV2View` takes a view's name,
layout, filter, and configuration, and set the *Needs You* filter on
2026-10-30.

**Set Size and Layer over REST, not GraphQL.** Projects v2 has REST
endpoints for a user's project too: `/users/harleypig/projectsV2/4/items`
and `.../fields` read the board, and a `PATCH` on one item sets its
fields, a single-select value by its option's ID. REST spends the core
budget; GraphQL's has run out four times in M1 and M2, so where GraphQL is
needed, send several mutations as one aliased request
([dotagents#2359][da2359]).

## Toolchain & reproducibility

- **Python ≥ 3.11** (`requires-python` in `pyproject.toml`), tested on 3.11
  through 3.14 ([#231][i231]), and developed inside a **`.venv`** at the
  repo root — never against the system Python.
- **`uv` provisions it** (the isolated-app rung of the global
  `toolchain-provisioning.md` ladder):

  ```sh
  make install    # create .venv if it is missing, then install into it
  ```

  That runs `uv venv` only where no `.venv` exists, since `uv` refuses to
  create one over an existing one, and then `uv pip install -e '.[dev]'`,
  the package plus the dev tools ([#262][i262]). Typed by hand, the bare
  `uv venv` is right on a fresh checkout only.

- **Runtime dependencies are `sievelib` and `IMAPClient`, and that is
  deliberate.** Both are pinned by lower bound in `pyproject.toml`, and
  both now carry an upper bound on the next major, so raising a bound is a
  deliberate, tested change rather than something a fresh install does
  unreviewed. Adding a third runtime dependency to a tool whose whole job is
  two protocol conversations deserves an argument first.
- Dev tooling (`ruff`, `pytest`, `pyright`, and `setuptools`, so
  `tests/test_packaging.py` can build a wheel offline) is an **optional
  dependency group**, so a user installing the CLI never pulls the linter
  in.
- **Every subpackage is found, not listed**
  (`[tool.setuptools.packages.find]`, `include = ["mailctl*"]`), so a
  non-editable install — a wheel, `uv tool install .`, `pipx install .` —
  carries the whole package ([#229][i229]).
- **The TUI is installed for development into the same `.venv`**, beside
  mailctl, and `make install` does not do it:

  ```sh
  make install                            # mailctl and its dev tools
  uv pip install -e './clients/tui[dev]'  # the TUI and its test tools
  ```

  Its dependencies — `textual` and `rich`, and in its dev extra `pytest`
  and `pytest-asyncio` — are in `clients/tui/pyproject.toml`, so mailctl's
  own runtime dependencies stay the two above ([ADR 0009][adr9]). For use
  rather than development, `uv tool install ./clients/tui` gives it an
  environment of its own; it needs `mailctl-api` on `PATH`, not mailctl
  in that environment.
- **A build-it-yourself Docker image** is the top-level `Dockerfile`, built
  with `make image` and published nowhere; [docs/DOCKER.md][docker]'s *In
  Docker* says how to run it. It sets `MAILCTL_BACKUP_ROOT` (*Rule
  conventions*).
  It carries the CLI, the API, and the TUI in one venv, so the TUI finds
  `mailctl-api` on `PATH`: `api` runs the API, `tui` the TUI, and
  anything else is the CLI's arguments ([#347][i347]). Its guards — the
  config directory a mount, the user not root, and a terminal for the
  TUI — hold whichever runs, and the TUI's `--api` is refused, since the
  command it names need not keep them.

## QA

The global `qa.md` owns the pipeline and its ordering; this section is the
concrete toolchain and the **status of every dimension** for this repo.

- **Format + lint:** `ruff` — `ruff format` and `ruff check`, configured under
  `[tool.ruff]` in `pyproject.toml`. Pre-commit gates both, in the two-file
  fix-then-check split (`.pre-commit-config-fix.yaml` runs the auto-fixers
  once as a prep step; `.pre-commit-config.yaml` is the non-modifying gate).
  Do **not** wire black/isort/flake8 alongside ruff (`python.md`).
  `clients/tui/` has no `[tool.ruff]` of its own, deliberately, so the
  root's configuration covers it and the two packages share one bar.
- **Code smell / complexity:** ruff's `B` (bugbear), `C4`, `SIM`, `UP`, and
  `RUF` rule sets, inside the same `ruff check`.
- **Type-check:** `pyright` in `standard` mode over `mailctl` and `tests`,
  configured under `[tool.pyright]` in `pyproject.toml`; `make typecheck`
  runs it. It resolves imports from `.venv`, so it runs where the package
  is installed, and the target runs `.venv/bin/pyright`, never `PATH`'s,
  refusing with the reason where it is missing: without the venv, pyright
  checks against whatever Python is on `PATH` and can pass there
  ([#244][i244]). That is also why it is in CI's Lint job and **not** in
  pre-commit: a pre-commit hook's own environment lacks `sievelib`,
  `IMAPClient`, and `pytest`, and supplying them would mean a second copy
  of the dependency list. Its version is pinned exactly in the dev extra,
  because each pyright release adds checks. **The TUI is type-checked
  separately**: the root run covers `mailctl` and `tests` only, and
  `clients/tui/pyproject.toml` has its own `[tool.pyright]` over the TUI's
  `src` and `tests`, standard mode, resolving from the same root `.venv`.
  `make testtui` and CI's *TUI* job run it from `clients/tui/`.
- **Security:** `gitleaks` + `detect-private-key` in pre-commit (secrets).
  The built image is scanned by `trivy` for HIGH and CRITICAL findings in
  the *Image scan* workflow, **report only**: it never fails and is not a
  required check, because the base image carries HIGH findings with no fix
  released, so a failing scan would be red for good ([#238][i238]).
  Note that the *most* important security property of this repo — the password
  never being emitted — is a code-structure guarantee (`Secret`), not
  something a scanner checks; review it by reading `Secret.reveal()` call
  sites.
- **Prose:** `markdownlint` and `yamllint` in pre-commit.
- **Dockerfile:** `hadolint` in pre-commit, a local hook pinned by tag and
  digest, in the check config only since it has no fixer. `.hadolint.yaml`
  fails on warnings and errors and trusts base images from `docker.io` and
  `ghcr.io` only ([#238][i238]).

Full dimension status:

| Dimension | Status |
|-----------|--------|
| 1. Format | **Active** — `ruff format` |
| 2. Lint | **Active** — `ruff check` |
| 3. Type-check | **Active** — `pyright` (standard mode) over the package and the tests, in CI's Lint job and `make typecheck` ([#10][i10]); over the TUI by its own configuration, in CI's *TUI* job and `make testtui` ([ADR 0009][adr9]) |
| 4. Code smell / complexity | **Active** — ruff `B`/`C4`/`SIM`/`UP`/`RUF` |
| 5. Security | **Active (secrets, Dockerfile, image report-only)** — `gitleaks`, `detect-private-key`; `hadolint` on the Dockerfile; `trivy` on the built image, report only and not a required check, since the base image carries HIGH findings with no fix ([#238][i238]). SAST is **Off**: the attack surface is two outbound TLS client sessions and no untrusted input parsing beyond the user's own Sieve script |
| 6. Tests | **Active** — the offline tier is green (`make test` / `pytest`), and the TUI's suite (`make testtui`) beside it; see [TESTS.md](TESTS.md) |
| 7. UI/UX & accessibility | **Active (TUI behaviour); accessibility Missing** — the TUI's screen is driven headless with Textual's Pilot (`clients/tui/tests/`): the masked password field, server text shown as plain characters, an alert shown as it arrives, and a command hidden without its capability. Nothing checks accessibility (contrast, colour-only meaning, screen-reader output), and no decision to build that check has been taken. The CLI's terminal output is covered by the *show, then change* convention |
| 8. End-to-end | **Active (manual, gated)** — `tests/live/` skips unless `MAILCTL_LIVE=1`, and writes only with `MAILCTL_LIVE_WRITE=1` as well; run by hand, never in CI. Its write guards were proved on the container tier ([#9][i9]), and every live test that writes has passed against the real account, the guards restoring everything: the smoke test on 2026-10-31 ([#283][i283]), and on 2026-11-01 it again and the tests for `filter apply`, `folder create`, `filter remove`, and `filterset restore` ([#388][i388]) |
| 9. Compatibility | **Active** — CPython 3.11 through 3.14, every one tested in CI, and sievelib at both ends of its bound (1.5.0 and the newest below 2); the TUI on 3.11 and 3.14 only, the oldest and newest; no external contract we publish ([#231][i231], [#232][i232]) |
| 10. Performance & load | **N/A** — interactive, single-mailbox, human-scale. Revisit only if a retroactive pass over a very large folder proves slow |
| 11. Reliability & observability | **N/A** — a one-shot CLI, not a service. Its reliability property is the backup-before-upload convention |
| 12. Build | **Active** — pure Python, no compile step. `tests/test_packaging.py` builds a wheel with the declared backend and runs it with nothing from the checkout on the path ([#229][i229]); the Docker image (`make image`), which installs the TUI beside mailctl ([#347][i347]), is built by `tests/container/test_image.py`, and in CI by the *Image scan* workflow on a PR or push touching its inputs (`Dockerfile`, `.dockerignore`, `docker/**`, `pyproject.toml`, `clients/tui/pyproject.toml`, `clients/tui/src/**`, and the workflow file itself), weekly on a schedule until 60 quiet days turn it off ([ICEBOX.md][icebox-scan]), and on manual dispatch; `test_image.py` itself is not in CI |
| 13. Documentation | **Active** — this file, `README.md`, `docs/`, and `adr/`; markdownlint gates the prose |
| 14. Code review | **Informal** — solo repo; `master` is PR-only, 0 required reviewers |
| 15. CI | **Active** — `.github/workflows/test.yml`, on every PR and on pushes to `master`. `Lint` runs `ruff check`, `ruff format --check`, and `pyright` once, on 3.12. `pytest` runs on 3.11 through 3.14 as `Test (3.x)`, each a second time on `sievelib==1.5.0`. `TUI (3.x)` runs on 3.11 and 3.14, with mailctl installed beside the TUI so its real-API test runs: the TUI's `pyright`, then its `pytest`, both from `clients/tui/` ([ADR 0009][adr9]). One job named `Test` waits on both matrices and passes only if every version of each did, since that is the check the ruleset requires, so a red TUI blocks a merge ([#231][i231], [#232][i232]). The live and container tiers are deliberately excluded — the one needs real credentials, the other a Docker daemon and a Debian mirror. `.github/workflows/image-scan.yml` (*Image scan*, job `Trivy (report only)`) runs when the image's inputs change, weekly on a schedule (Monday 06:17 UTC) so a CVE published against the unchanged base image is still reported, and on manual dispatch. GitHub turns that schedule off after 60 days with no activity in the repository, and nothing here warns when it does ([ICEBOX.md][icebox-scan]). The scan never fails on a finding and is not a required check ([#238][i238], [#264][i264]) |

## How work is dispatched

This repo is **team-managed**. The sentinel the global
`team-managed-delegation.md` reads:

team-managed: enabled

So the main-thread agent is the **Project-Manager seat**: it plans, routes,
and integrates, and **substantive edits are dispatched to the role that owns
them** — the Developer for package source and its tests, QA for the test
tier, the Writer for prose, the Config Engineer for anything under `.claude/`.
Orchestration glue stays with the main thread: planning notes, CI wiring,
`.claude/settings*.json`, and scratch files.

**Enabled deliberately, on 2026-08-14, not at scaffold time.** The rule is
default-off and says an agent must not self-enable it; the operator asked
for it after watching a module and its whole test suite get authored on the
main thread. Recording the date matters because the norm is what makes
*"since the role last touched these files"* a meaningful boundary, and before
this date there is no such boundary to compute.

The nudge is a `PreToolUse` hook, and it is **advisory by construction** — it
injects a reminder and allows the edit. Two consequences worth having
written down rather than rediscovered:

- **An explicit instruction outranks it.** If the operator says to edit
  directly, or subagents are unavailable, edit directly — hold the change to
  the **owning role's standard** and say which role's standard you applied.
  Acknowledge the reminder once per session, not per edit.
- **Standing in for a role is not the same as the role having seen it.** Work
  the main thread authored directly should be handed back for that role's
  review once delegation is available again. The mechanism for that is not
  built yet ([dotagents#495][da495]), so until it is this is a discipline
  rather than a gate.

## How much we ask

The section above is how the team routes work internally; this is how it
engages the person the work is for. Recorded because the operator set it
deliberately on 2026-08-16, in these words:

> Record in this repos convention that I am testing the minimal involvement
> aspect of dotagents. I want to test the condition of a client coming to me
> with an idea, but no knowledge of how to accomplish it, little desire to
> answer questions beyond the minimal needed. This is going to be a little
> weird because the end product will be a docker image someone has to setup,
> but that's the nature of testing. :shrug:

So **this repo is a live test bed for the involvement dial**, held near its
minimum. The operator is standing in as the client they describe, and
"customer" throughout means them.

**Involvement is how many questions we ask versus how many defaults we take,
and it is the Project Manager's dial** (`customer-communication.md` *Length is
a separate dial from depth*; `agents/project-manager.md` *Discovery before
dispatch*). It is **not** the register dials — depth and length — which are
the Product Owner's. Keeping the two apart is the entire point of the
distinction, and this is precisely the setting that invites collapsing them:
this one says **ask less**, not **say less**. Answer a complaint about how
much we ask by writing shorter answers and we have tuned the wrong dial, left
the real one untouched, and guaranteed the complaint comes back.

**The client being simulated has an idea and no route to it.** They do not
know how it would be accomplished and have little appetite for finding out.
Working out the *how* is therefore the job rather than the thing to ask about
— a question is a cost charged to them, so the default posture is to resolve
the unknown and bring back a result.

In practice:

- **Apply the scoping rule above instead of asking whether something is in
  scope.** *Is it a filter or a setting, and is there no way to manage it
  here?* — if so, the answer is yes and no decision is needed. That rule
  exists because a batch of questions should have been an answer; the #42/#43
  worked example under *The scoping rule* is the case that produced it.
- **Run the decide-now gate hard** (`issue-evaluation.md`). If a rule, the
  code, or a documented default settles it, it is not a question — answer it,
  and record the answer where it belongs.
- **Report decisions; do not request permission** (`departure-reporting.md`).
  Proceed on the better judgment and say what was done and why. Asking first
  suppresses the departures that were right, which are the majority, in order
  to catch the few that were not.
- **Batch what genuinely must be asked** — few at a time, each answerable in a
  sentence. `AskUserQuestion` is the shape that has worked here.

**The escalate bar does not move with the dial** (`issue-evaluation.md`
*escalate*). Ask when they know something we cannot work out — a fact about
their accounts, their mail, or what they actually want — or when the answer
gets expensive to change. And a **Tier 3** change (`change-cost-tiers.md`)
stops and asks regardless of the dial: real risk is the one tier where being
wrong is not recoverable by more work, and a preference for fewer questions is
never authority to take that risk on their behalf.

**The test has a known flaw, and it is accepted rather than compensated for.**
The end product is a Docker image somebody has to set up, which is not what a
genuinely minimal-involvement client wants handed to them; the operator named
this themselves in the same breath as setting the dial. It is not a reason to
abandon the test, and it is specifically not a reason to quietly start asking
more — raising involvement because the deliverable is awkward destroys the
thing being measured.

**Being a test, it is supposed to produce findings.** Where the setting causes
a wrong turn — a default taken that should have been a question, an assumption
that sent work down the wrong path — that *is* the result the test exists to
produce, and it gets recorded rather than silently corrected, because a
quietly-fixed wrong turn is a data point destroyed. When the operator says
anything about how much we ask, update this section with the date and what
they said: the same recording discipline `customer-communication.md` applies
to the register setting (*The setting is recorded, with its date and its
evidence*),
applied here to the dial actually under test. It is a live fact about this
engagement rather than a style preference, so it belongs where the next agent
will read it.

## Merge policy & versioning

- **`master` is PR-only, enforced server-side.** The *Protect Master Branch*
  ruleset is active on `master` with no bypass actors: it blocks deletion and
  non-fast-forward pushes, requires a pull request (squash-only, 0 approvals,
  review threads resolved), and requires the `Lint` and `Test` status checks.
  The local `no-commit-to-branch` pre-commit hook and the global
  `branch-protection.py` edit-time hook sit in front of it as earlier layers
  (`git.md` *Protecting the Default Branch*).
- **Auto-merge is declared** (operator, 2026-09-27,
  [#11](https://github.com/harleypig/mailctl/issues/11)). The
  ruleset is the server-side guardrail the opt-in (`gh.md`) rests on, so
  invoking push-pr is consent through merge once the required checks are
  green. This is the agent workflow's opt-in, **not** GitHub's own auto-merge
  feature; the merge still obeys the ruleset, and the repo deletes head
  branches on merge. The sentinel push-pr reads:

  auto-merge: enabled

- **`merge-finalization` is not declared.** Its hook guards `TODO.md` /
  `ROADMAP.md` finalization, and this repo has neither — work is tracked as
  issues (*Where work is tracked*).
- **Versioning:** semver `vX.Y.Z`, `repo` scope (one version for the whole
  tool — the `git.md` *Versioning & tags* method). See below.

## Versioning & tagging

`repo`-scope semver, tagged `vX.Y.Z`, annotated, cut at the merge commit on
`master` with the `release-tag` skill.

- **`v1.y.z`, strict semver from `1.0.0` on.** The version is a
  compatibility promise: a break ships only in a new major version, `y`
  marks an addition, and `z` a fix. What the promise covers, and what stays
  free to change, is [RELEASING.md][promise] *What v1 promises*; read it
  there rather than here. `v1.0.0` was tagged on 2026-11-01, and the gates
  it waited on are recorded in [RELEASING.md](../RELEASING.md) *Cutting a
  tag*.
- **No API-major alignment.** The sibling provider aligns its MAJOR to the
  MXroute REST API's major, because it is a client of a versioned API. That
  does **not** carry over: this tool speaks ManageSieve (RFC 5804) and IMAP,
  standardized protocols with no vendor version to track. Do not import the
  provider's bump policy.
- **A tag publishes nothing.** There is no release pipeline and no registry —
  a tag is a marker on history, so it is cheap and carries no
  cannot-be-unpublished risk. See [RELEASING.md](../RELEASING.md).
- **The version is written in one place: `pyproject.toml`.** A release
  bumps that file and nothing else. `mailctl/__init__.py` reads
  `__version__` back from the installed metadata with
  `importlib.metadata.version("mailctl")`, falling back to `0+unknown`
  when nothing is installed, and `tests/test_version.py` fails if the two
  ever disagree ([#106][i106]). An editable install records the version at
  install time, so a bump shows in `mailctl --version` only after
  `uv pip install -e .` is run again.

[adr1]: ../adr/0001-standalone-cli-over-provider-resource.md
[docker]: ../docs/DOCKER.md
[promise]: ../RELEASING.md#what-v1-promises
[adr2]: ../adr/0002-non-destructive-script-merge.md
[provider]: https://github.com/harleypig/terraform-provider-mxroute
[i9]: https://github.com/harleypig/mailctl/issues/9
[i283]: https://github.com/harleypig/mailctl/issues/283
[adr5]: ../adr/0005-restore-may-replace-an-unparseable-script.md
[adr8]: ../adr/0008-json-rpc-api-interface-for-out-of-process-clients.md
[adr9]: ../adr/0009-python-textual-tui-as-a-separate-package.md
[i257]: https://github.com/harleypig/mailctl/issues/257
[i258]: https://github.com/harleypig/mailctl/issues/258
[i259]: https://github.com/harleypig/mailctl/issues/259
[adr6]: ../adr/0006-two-layer-component-and-provider-architecture.md
[adr7]: ../adr/0007-interfaces-utilities-session-provider-layering.md
[i137]: https://github.com/harleypig/mailctl/issues/137
[i51]: https://github.com/harleypig/mailctl/issues/51
[i99]: https://github.com/harleypig/mailctl/issues/99
[i26]: https://github.com/harleypig/mailctl/issues/26
[rec-mxroute]: ../mailctl/providers/mxroute/RECORD.md
[i17]: https://github.com/harleypig/mailctl/issues/17
[i18]: https://github.com/harleypig/mailctl/issues/18
[i101]: https://github.com/harleypig/mailctl/issues/101
[i19]: https://github.com/harleypig/mailctl/issues/19
[i90]: https://github.com/harleypig/mailctl/issues/90
[i13]: https://github.com/harleypig/mailctl/issues/13
[i89]: https://github.com/harleypig/mailctl/issues/89
[i10]: https://github.com/harleypig/mailctl/issues/10
[i39]: https://github.com/harleypig/mailctl/issues/39
[i229]: https://github.com/harleypig/mailctl/issues/229
[i230]: https://github.com/harleypig/mailctl/issues/230
[i231]: https://github.com/harleypig/mailctl/issues/231
[i232]: https://github.com/harleypig/mailctl/issues/232
[i183]: https://github.com/harleypig/mailctl/issues/183
[i204]: https://github.com/harleypig/mailctl/issues/204
[i216]: https://github.com/harleypig/mailctl/issues/216
[i219]: https://github.com/harleypig/mailctl/issues/219
[i238]: https://github.com/harleypig/mailctl/issues/238
[i243]: https://github.com/harleypig/mailctl/issues/243
[i244]: https://github.com/harleypig/mailctl/issues/244
[i262]: https://github.com/harleypig/mailctl/issues/262
[i264]: https://github.com/harleypig/mailctl/issues/264
[i287]: https://github.com/harleypig/mailctl/issues/287
[i418]: https://github.com/harleypig/mailctl/issues/418
[i265]: https://github.com/harleypig/mailctl/issues/265
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
[i349]: https://github.com/harleypig/mailctl/issues/349
[i352]: https://github.com/harleypig/mailctl/issues/352
[i362]: https://github.com/harleypig/mailctl/issues/362
[i364]: https://github.com/harleypig/mailctl/issues/364
[i373]: https://github.com/harleypig/mailctl/issues/373
[i374]: https://github.com/harleypig/mailctl/issues/374
[i376]: https://github.com/harleypig/mailctl/issues/376
[i402]: https://github.com/harleypig/mailctl/issues/402
[i388]: https://github.com/harleypig/mailctl/issues/388
[i377]: https://github.com/harleypig/mailctl/issues/377
[i378]: https://github.com/harleypig/mailctl/issues/378
[i106]: https://github.com/harleypig/mailctl/issues/106
[i145]: https://github.com/harleypig/mailctl/issues/145
[i147]: https://github.com/harleypig/mailctl/issues/147
[i148]: https://github.com/harleypig/mailctl/issues/148
[i149]: https://github.com/harleypig/mailctl/issues/149
[i150]: https://github.com/harleypig/mailctl/issues/150
[i151]: https://github.com/harleypig/mailctl/issues/151
[i152]: https://github.com/harleypig/mailctl/issues/152
[i153]: https://github.com/harleypig/mailctl/issues/153
[i154]: https://github.com/harleypig/mailctl/issues/154
[i155]: https://github.com/harleypig/mailctl/issues/155
[i160]: https://github.com/harleypig/mailctl/issues/160
[icebox-retention]: ../ICEBOX.md#per-folder-retention--expire-mail-after-n-days
[icebox-scan]: ../ICEBOX.md#the-weekly-image-scan-stopping-after-60-quiet-days
[da495]: https://github.com/harleypig/dotagents/issues/495
[board]: https://github.com/users/harleypig/projects/4
[i277]: https://github.com/harleypig/mailctl/issues/277
[i278]: https://github.com/harleypig/mailctl/issues/278
[da2312]: https://github.com/Harleypig-LLC/dotagents/issues/2312
[da2322]: https://github.com/Harleypig-LLC/dotagents/issues/2322
[da2358]: https://github.com/Harleypig-LLC/dotagents/issues/2358
[da2359]: https://github.com/Harleypig-LLC/dotagents/issues/2359
