# 8. A JSON-RPC API interface for clients in any language

- Status: accepted
- Date: 2026-10-28
- Accepted: 2026-10-29, by the operator on [#224][i224]
- Amended: 2026-10-30, for [#258][i258] and again for [#307][i307] (see
  *Amendment*)
- Amends [ADR 0003][adr3] (see its *Amendment*). Builds on [ADR
  0007][adr7]. Recorded for [#224][i224].

## Context

**A UI is coming, and it may not be Python.** The operator's model for it,
on [#224][i224] (2026-10-27):

- the workhorse stays Python: the utilities, the session, and the
  providers do all the work, and only they talk to IMAP and ManageSieve;
- a UI is always presentation only: it shows mail, headers, and filters,
  and lets the user define filters, and every piece of data goes to the
  workhorse;
- the API is a new interface beside `cli.py` in [ADR 0007][adr7]'s
  interface layer, calling only the utilities and the session's opening
  calls;
- the UI that talks to the API may be any language, outside the package,
  as a client of the API.

A TUI comes first and a web UI after (CONVENTIONS *The CLI comes first*:
CLI, then TUI, then GUI and web). The [ICEBOX *TUI front-end*][ice-tui]
entry's trigger, *the operator actually requests it*, has fired.

**What the core already gives an API.** [ADR 0007][adr7] built most of
it. Every change is plan, decide, execute. A plan is a frozen record a
`plan_*` utility returns without writing, and an `execute_*` utility
carries it out. The safety policy runs at execute: the backup in
`scripts.upload_script`, the `--max-messages` ceiling in
`mail.check_message_cap`, and UIDVALIDITY in `uids.check_uidvalidity`.
Nothing below the interface layer prints: the steps of a change leave
through `on_event`, and protocol chatter and server alerts leave through
`progress`. Errors are `MailctlError` with a stable `code` and `fields`.
`json_output.py` maps each neutral record to a versioned document, and
those documents are already a contract. `Config.prompter` is the seam a
front-end uses to ask for the password, so the core never owns a terminal.

**What it does not give one.** A plan lives in the memory of the process
that made it, so a client in another process cannot hold one. Nothing
keeps a session open between commands except inside one CLI run. And one
gap matters more once the decide step can take minutes rather than
seconds. It is set out under *Plan, decide, execute*, below.

## Decision

**mailctl gains a second interface, the API: JSON-RPC 2.0 over the
standard streams of a process the client starts, one newline-delimited
message per line.** It sits beside `cli.py` in the interface layer, under
the same guard (`tests/test_layer_purity.py`): it imports the utilities,
the session's opening calls, the neutral model, and `json_output`, and
nothing below them. The client may be written in any language.

```text
client (any language)            mailctl API process (Python)
  TUI · later a web backend  ->    interface: api  (beside cli)
  stdin / stdout, JSON-RPC         utilities -> session -> provider
```

Each of the seven questions [#224][i224] asks has its own section below.

### 1. Transport

**JSON-RPC 2.0, carried over stdio now and over HTTP later, with the same
messages on both.** The specification is *"transport agnostic in that the
concepts can be used within the same process, over sockets, over http, or
in many various message passing environments"* ([JSON-RPC 2.0][jsonrpc]).
So the question is two questions: which message protocol, and which
carrier. The protocol is decided once, here; the carrier is decided per
client.

- **Stdio for the TUI, now.** The client starts the API process and talks
  to it over the process's stdin and stdout. Nothing listens on a port, so
  nothing on the machine but the parent can reach it (*Authentication*,
  below). The API process lives exactly as long as the client keeps it.
- **One message per line.** Each message is one JSON document on one
  line, ended by `\n`. `json_output.dumps` with `indent=None` already
  writes that: JSON escapes a newline inside a string, and `ensure_ascii`
  escapes everything outside printable ASCII. A client in any language
  reads a line and parses it. This is the framing the Model Context
  Protocol settled on for the same job: its stdio binding is
  *"newline-delimited messages over the standard streams of a
  client-launched subprocess"* ([MCP 2026-07-28][mcp]).
- **Stderr stays a diagnostic stream.** Nothing on it is protocol, and the
  client may show or log it. It carries what `--debug` would, under the
  same credential bar.
- **HTTP for the web UI, later, as its own decision.** The same requests,
  responses, and notifications go over HTTP: a request as a `POST` body,
  and notifications on a stream. Which server library carries it,
  how a browser authenticates, and how one process holds a session per
  user are the web slice's ADR, not this one. Only the message set is
  fixed here, so the TUI's client code and the web backend's speak the
  same calls.

**No runtime dependency is added for this.** The subset used is small:
requests, responses, notifications, the error object, and requests from
the server to the client. A dispatcher over the standard library's `json`
and the process's streams carries it. CONVENTIONS *Toolchain* asks that a
third runtime dependency be argued first, and the argument here is that
the stdio carrier needs none. The HTTP carrier will need one: the standard
library's own server says *"http.server is not recommended for
production. It only implements basic security checks"* ([http.server][hs]).
That argument is owed when the web slice is designed, against the
libraries current then, and is not pre-empted here.

### 2. Plan, decide, execute across the boundary

**The plan stays in the API process; the client holds a handle to it.**

1. **Plan.** `filter.add.plan` (and every write command's `.plan`) calls
   the `plan_*` utility, keeps the frozen plan record in the session's
   plan table under a fresh opaque id, and returns the id with the plan
   document `--dry-run --json` prints today. The client renders the
   document: the diff, the placement findings, the message counts.
2. **Decide.** The user decides in the client. Nothing crosses the
   boundary while they do.
3. **Execute.** `filter.add.execute` takes the plan id and nothing else
   the plan already holds. The API looks it up and calls the `execute_*`
   utility with **that** record. The client never sends a plan's content
   back, so it cannot alter what is carried out; what was shown is what
   runs. A client that wants different settings makes a new plan.
4. **Discard.** `plan.discard` drops one. Unexecuted plans die with the
   session.

A plan id is **single-use**: executed, failed, or discarded, it is gone,
and a second `execute` with it is refused (`plan_unknown`). That matches
the session's rule for writes, *a write is never sent again* ([ADR
0007][adr7]): a client retrying an execute after a lost response cannot
land the change twice.

**The safety policy runs at execute, unchanged, because it already lives
there.** `upload_script` backs up before it uploads; `execute_mail`
re-checks the ceiling and the source folder's UIDVALIDITY;
`execute_mark` re-checks UIDVALIDITY. The API adds no policy of its own
and removes none. A `--max-messages` value is a plan parameter, as it is
on the CLI; *"confirming does not lift it"* (`check_message_cap`) holds
because execute takes no new ceiling.

**One gap must close before any write ships over the API: a filter set
changed between plan and execute.** A script plan carries the text it
was made from (`before`) and the text to upload (`after`). At execute,
`upload_script` writes `before` as the backup and stores `after`, and
nothing reads the server's current script again first. If Roundcube, or
a second client, changed the script in between, the upload silently
overwrites that change, and the backup holds the plan's copy rather than
what the upload destroyed. That is [ADR 0002][adr2]'s failure arriving
by a different road. On the CLI the window is one confirmation prompt.
Over the API it is as long as the user leaves the plan on screen. **The
fix is a utility check, so every interface gets it:** at execute, read
the script, refuse with a coded `MailctlError` if it is not
byte-identical to the plan's `before`, and back up what was read. It is
the first follow-up below, and the API's first write waits on it.

### 3. Sessions

**One API process, one configuration, one account, one `Session`.** The
process resolves its configuration at start exactly as the CLI does,
from its own arguments, the env file, the environment, and `config.toml`,
so a TUI starts it with the arguments a user would give `mailctl`. It
calls `engine.connect` once, after `initialize`, and closes the session
on `shutdown` or at end of input. That is [ADR 0007][adr7]'s *the session
lives as long as the app does* with the app being the client.

- **Lazy per half, as now.** A half connects the first time a utility
  uses it. A TUI that only lists filters never opens IMAP. `initialize`
  connects nothing, so a bad setting is reported before any login, as
  the CLI's lazy open guarantees ([#137][i137]).
- **Requests are handled one at a time, in order.** The session already
  serialises calls per half, and IMAP and ManageSieve are one command
  after another. A second request waits for the first. Cancelling a
  request in flight is not offered in the first slice.
- **Idle connections go stale, and a write must not be the call that
  finds out.** A server may log out an idle client: IMAP allows *"an
  inactivity autologout timer"* of *"at least 30 minutes"* ([RFC
  9051][rfc9051] §5.4). The session already sends a cut-off read once more
  on a fresh connection, and never sends a write again. In a long-running
  process the first call after a long idle could be a write, which would
  then fail for nothing. So before a write on a half that has been idle,
  the session sends one read the transport already has (a capability
  query or `NOOP`, classified read and so retried), and the write follows
  on a connection known to be live. This is a session change, and it
  goes in the first API slice with a test in `test_engine.py`.
- **The plan table belongs to the session**, so plans never cross
  sessions, and a long-running front-end that holds one session per user
  holds one plan table per user too.

### 4. Authentication and the password

**Over stdio, the client is not authenticated, and does not need to be.**
The API process listens on nothing. Its only peer is the process that
started it and holds its streams, which already runs as the same OS
user with the same access to the config directory. Adding a token would
protect nothing that user could not already read. The HTTP carrier is
different, and authenticating to it is the web slice's decision.

**The mailbox password stays behind `Secret`, and enters the API process
by one of two routes only.**

- **The configuration ladder, unchanged.** `--password-file`,
  `--password-cmd`, the env file, the environment, and `config.toml`
  resolve as CONVENTIONS *Credentials* sets out. A TUI that starts the
  API with `--password-cmd` never sees the password.
- **A prompt, asked by the server.** Where no rung supplies it,
  `Config.prompter` is set to a function that sends the client a
  `credentials.prompt` request and waits for the answer. The client asks
  the user and replies. The reply's value is wrapped in `Secret` as the
  line is parsed, before it is dispatched or logged, and is used only
  through `reveal()` at the connection, as today. It is the same seam
  the CLI fills with `getpass`.

**What is refused.** No request method takes a password as a parameter,
so there is no `--password` equivalent and no way to put one in a
request the client might log. No response or notification can carry
one: `json_output.dumps` already refuses a `Secret` (*"a credential is
never serialised"*), and every API message goes through it. The API's
diagnostics on stderr name a request by its id and method, never its
parameters. And the first client is held to the same bar
(*Consequences*): it is code we write, and CONVENTIONS *Credentials*
binds every piece of code that touches the password.

### 5. Data format

**The API's results are the `--json` documents, built by the same
functions in `json_output.py`, at the same `JSON_VERSION`.** A
`filter.list` result is the document `mailctl filter list --json` prints;
a `.plan` result's document is the `--dry-run --json` plan. The CLI's
contract and the API's are one contract, kept in one module, so they
cannot drift. `json_output.py` describes itself as part of the CLI
front-end today; it becomes the interface layer's shared module, still
under the interface guard, and its docstring says so.

- **What the CLI never printed as JSON gets a mapping there too:** the
  events (`ScriptBackedUp`, `ScriptUploaded`, `FolderCreated`, and the
  rest), a `ServerAlert`, and each execute's result. Adding a document
  or a key is compatible under `JSON_VERSION`'s own rule; the CLI is
  unaffected.
- **The JSON-RPC envelope is the API's, and the documents sit inside
  it.** A plan's id is in the result beside its document, not added to
  the document, so the document stays identical to the CLI's.
- **Errors keep their codes.** A `MailctlError` becomes a JSON-RPC error
  in the specification's application range, with `data` holding the
  `--json` error document: the message, the `code`, and the `fields`. A
  client branches on `code`, never the message. The message names no
  front-end's control (CONVENTIONS *A core message names no front-end's
  flag*), so it reads right in a TUI as it does in a terminal.
- **Two versions, for two things.** `initialize` returns the protocol's
  version (the method set and the envelope) and `JSON_VERSION` (the
  documents), and the provider's capabilities from
  `engine.capabilities_for`, so the client offers only what the provider
  can do, as the CLI's parser does.
- **Method names are the command paths.** `filter.add.plan`,
  `mail.search`, `folder.list`: the user-facing nouns of CONVENTIONS *The
  command surface*, so the API and the help name an operation alike.
  *Every command is in every interface* holds for the API as it does for
  a TUI, and a test holds the method set to the CLI's command tree, minus
  what a medium leaves out by decision.

### 6. Events and progress

**Each becomes a JSON-RPC notification, which by the specification gets
no reply** (*"The Server MUST NOT reply to a Notification"*,
[JSON-RPC 2.0][jsonrpc]; the same holds in the other direction).

- **`event`**: the API passes each `execute_*` an `on_event` sink that
  sends the event's document, with the id of the request it belongs to.
  The client shows the backup's path as the CLI does, before the upload.
- **`server.alert`**: a `ServerAlert` arriving on `progress` is sent
  always, as the CLI shows one on stderr with or without `--verbose`,
  with its channel (`rules` or `mail`). The CLI's once-per-run rule
  becomes once per session.
- **`log`**: `progress`'s protocol chatter is sent only when the client
  asked for it in `initialize`, as `--verbose` asks for it on the CLI. It
  is the same text, under the same bar: it never holds the password.

The core changes nothing for this. `on_event` and `progress` are already
callbacks, and the API is one more front-end deciding where they go.

### 7. ADR 0003's wording

[ADR 0003][adr3] said *"every front-end stay[s] Python"*. Under this
model the front-end that matters to that rule is the interface the core
exposes, and that stays Python; a client of it need not. ADR 0003 is
amended to say so, in its own *Amendment* section. Its four tests are
about a component the core calls; a client calls the core, so the tests
do not apply to it, and the core needs nothing from it to run.

## Alternatives rejected

- **HTTP first, for both.** One carrier for both clients is tidy, but it
  makes the TUI run a local server: a port to choose and collide on, and
  a listener every local user can reach, so a token and its storage
  before the first screen. The web needs HTTP; the TUI does not. Fixing
  the message set now and the carrier per client gets the reuse without
  the cost.
- **LSP-style framing (`Content-Length` headers).** The Language Server
  Protocol frames each message with a header naming *"the length of the
  content part in bytes"* ([LSP 3.17][lsp]). It tolerates any content,
  and costs every client a header parser. Our content is always one line
  of ASCII JSON (`ensure_ascii`), so a newline is an unambiguous frame,
  and a line reader is all any language needs.
- **The Model Context Protocol as the UI's API.** MCP *"uses JSON-RPC to
  encode messages"* and has a stdio binding ([MCP 2026-07-28][mcp]), so
  it is close in shape. It is a protocol for model clients, built around
  tools and resources, and revised often. A UI built on it would inherit
  that vocabulary and that cadence for no gain. An MCP interface over the
  same utilities could be another front-end later, and would share these
  documents.
- **Sending the plan back to execute.** A client returning the plan's
  content would let it alter the `after` text, the folder plan, or the
  UIDs between showing and doing. Signing it would stop that and add a
  key to manage. Keeping the plan server-side costs a table per session.
- **A Python library as the API.** Importing the utilities is already
  possible, and is what `cli.py` does. It serves only Python, and the
  operator's model is any language.
- **Binding a library (FFI) for another language.** [ADR 0003][adr3]
  test 2 rejects bindings for a component, and the reasons hold in the
  other direction: a build matrix and an ABI, for speed nothing here
  needs.

## Consequences

- **The first client defines what a client must do,** so it is held to
  the API's bar: it never logs the password it collects for
  `credentials.prompt`, it offers only what `initialize`'s capabilities
  allow, and it renders documents rather than re-deriving them. Its
  language is that issue's choice; the ICEBOX entry chose no framework,
  deliberately.
- **A second process boundary appears, and it goes the other way.** ADR
  0003's boundary is the core calling out to a component; this one is a
  client calling in. Both exchange versioned JSON. Neither shares an
  object.
- **`json_output.py` gains readers and no new rules.** Its versioning
  rule now protects two interfaces, so a renamed key breaks a TUI as
  surely as a script.
- **The CLI gets the staleness check too**, through the utility. A
  filter set edited during a `filter add` confirmation prompt is refused
  instead of overwritten. That changes a snapshot where one exercises it,
  deliberately.
- **Concurrency is serial.** One request at a time is right for one user
  on two sequential protocols. A web backend serving many users holds one
  session each ([ADR 0007][adr7]), and that is the web slice's design.

## Follow-up work

Named here; filed separately, in this order:

1. **Refuse a script plan whose filter set changed before execute** — the
   utility check under *Plan, decide, execute*. Benefits the CLI now; the
   API's first write waits on it.
2. **API slice 1** — the stdio JSON-RPC interface: `initialize` /
   `shutdown`, capabilities, `credentials.prompt`, `filter.list`,
   `mail.search`, `mail.view`, and `filter.add` plan / execute /
   discard, with `event`, `server.alert`, and `log` notifications, the
   idle preflight in the session, and the layer guard covering the new
   interface.
3. **First client: a TUI over the API** — lists filters, searches and
   views mail, and adds a filter through plan, decide, execute. It
   replaces the ICEBOX *TUI front-end* entry.

The web carrier, the remaining commands' methods, and a fuller message
reader (HTML bodies, threading; [#224][i224] *Related gaps*) are later
issues, each with its own spec.

## Amendment

2026-10-30, made with the first API slice, [#258][i258]. The decision
above stands. This corrects three things in it that building the slice
showed to be wrong.

### The idle preflight needed a probe of its own

§3 has the session send *"one read the transport already has (a
capability query or `NOOP`)"* before a write on an idle half. On the mail
half no existing read would do. The capability reads are cached, so they
reach no server. `mail_namespaces` answers empty when the connection has
gone, rather than raising, so the session would never see the drop and
never reconnect. So the transport gained an operation of its own,
`mail_noop`, an IMAP `NOOP`, classified a read and marked as the mail
half's probe (`Operation.probe`). On the rules half the probe is
`list_rule_sets`, an existing read. A half counts as idle after 60 s
without an exchange (`engine.IDLE_PROBE_AFTER`).

### The channels are `filter` and `mail`

§6 names a `server.alert`'s channel `rules` or `mail`. It is `filter` or
`mail`, on `server.alert` and on `log` alike, because the user-facing noun
is *filter* (CONVENTIONS *What the user sees is the same for every
provider*). The session's halves keep their code names, `rules` and
`mail`, and `json_output.CHANNELS` maps one to the other.

### `filter add` has no confirmation prompt

*Consequences* says that, with the staleness check, *"a filter set edited
during a `filter add` confirmation prompt is refused instead of
overwritten."* `filter add` asks nothing (CONVENTIONS *The command
surface*), so there is no such prompt. The check, built in [#257][i257],
sits in `scripts.upload_script` and so covers every command that uploads
a filter set. On the command line the window it closes is widest on the
commands that confirm before they upload: `filter remove`, `filter move`,
`filter rename`, `filter enable` and `filter disable`, `filter optimize`,
`folder rename`, and `filterset restore`.

### `initialize` names what the rename left behind (#307)

Added on 2026-10-30, for [#307][i307]. §5 has `initialize` return the
two versions and the capabilities. Its result also carries `old_name`
(`json_output.old_name`): each old `MXROUTE_*` setting present without
its new name, as `{old, new, where, path}`, and the old `mxfilter`
config directory where the new one does not exist, as `{old, new,
operation}`, else null. That is what the CLI warns about on stderr
before every command (CONVENTIONS *The old name*), and it is names and
places only: no old value is read, so none can be sent.

**A result member, not a notification.** The findings describe the
configuration the session started with, and they stay true for its
life; §5 already has `initialize` describe the session. §6's
notifications are for what happens during a request, and each names the
request it belongs to. And a client must read `initialize`'s answer to
go on, where it may ignore every notification, so a notification is the
one place a client could miss the finding without choosing to.

**The cost: a refused `initialize` carries no findings.** An old name
is ignored, which at worst leaves its setting unset, and `initialize`
refuses nothing for that, so in practice the findings arrive whenever a session does. Where
`initialize` is refused for another reason, the client learns of the
old names only from an `initialize` that succeeds.

## Sources

Fetched 2026-10-28.

- [JSON-RPC 2.0 specification][jsonrpc] — transport agnosticism, the
  notification (*"a Request object without an "id" member"*) and that
  none is replied to, and the error object.
- [Model Context Protocol, 2026-07-28, *Transports*][mcp] — *"MCP uses
  JSON-RPC to encode messages"*; the stdio binding as newline-delimited
  messages over a client-launched subprocess's standard streams.
- [Language Server Protocol 3.17, *Base Protocol*][lsp] — the
  `Content-Length` header framing, and a server sending requests to the
  client (`window/showMessageRequest`), the precedent for
  `credentials.prompt`.
- [Python `http.server`][hs] — *"not recommended for production. It
  only implements basic security checks."*
- [RFC 9051][rfc9051] §5.4 — the IMAP inactivity autologout timer, at
  least 30 minutes after authentication.

## Based on

- [#224][i224] — the research issue, with the operator's model of
  2026-10-27.
- [ADR 0007][adr7] — the interface layer this adds to, the session it
  keeps open, and plan, decide, execute.
- [ADR 0003][adr3] — the Python core, amended for question 7.
- [ADR 0002][adr2] — merge, never overwrite, which the staleness check
  protects across the decide step.

[adr2]: 0002-non-destructive-script-merge.md
[adr3]: 0003-python-core-with-per-component-language-choice.md
[adr7]: 0007-interfaces-utilities-session-provider-layering.md
[i137]: https://github.com/harleypig/mailctl/issues/137
[i224]: https://github.com/harleypig/mailctl/issues/224
[i257]: https://github.com/harleypig/mailctl/issues/257
[i258]: https://github.com/harleypig/mailctl/issues/258
[i307]: https://github.com/harleypig/mailctl/issues/307
[ice-tui]: ../ICEBOX.md#tui-front-end
[jsonrpc]: https://www.jsonrpc.org/specification
[mcp]: https://modelcontextprotocol.io/specification/2026-07-28/basic/transports
[lsp]: https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/
[hs]: https://docs.python.org/3/library/http.server.html
[rfc9051]: https://www.rfc-editor.org/rfc/rfc9051
