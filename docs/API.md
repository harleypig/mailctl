# API

`mailctl-api` offers mailctl's operations to a program written in any
language. The program starts it as a child process and talks JSON-RPC 2.0
to it over its standard input and output. It is meant for a client such as
a TUI: the client shows things and asks the user, and mailctl does the work
through the same code, and the same safety checks, as the command line. Why
it is built this way is [ADR 0008][adr8].

## Starting it

```bash
mailctl-api --host mail.example.com --user you@example.com
python -m mailctl.api --env-file      # the same program
```

It takes the settings `mailctl` takes, resolved the same way (see
[*Configure*][configure]): `--provider`, `--env-file`, `--host`, `--user`,
`--password-file` or `--password-cmd`, `--imap-host`, `--imap-port`,
`--sieve-port`, `--sieve-tls`, `--backup-dir`, and `--disable-extension`.
`--password` is left out, since an argument can be read from the process
list. Two settings are its own:

* `--folder` is the folder the mail methods read when a request names
  none. It defaults to `source_folder` from the config, else `INBOX`.
* `--debug` writes a failed request's traceback to stderr. It names the
  request by its id and method, never by its parameters.

`mailctl-api --help` lists them all. The Docker image starts it with `api`
as its first word (see [*In Docker*][docker]).

A bad argument does not end the process, since the client would see only
the end of its output. mailctl says it on stderr, quoting no value given,
and answers `initialize` with the refusal, such as `invalid_arguments`, or
`setting_invalid` with `fields.setting` naming the setting. Every other
method is then refused with `not_initialized`. As on the command line, an
option is matched by its whole name, never by a prefix.

## The stream

* Each message is one JSON object on one line, ended by a newline, in both
  directions. mailctl's lines are ASCII; a client's must be UTF-8.
* stdout carries the protocol and nothing else. stderr is diagnostics, for
  the client to show or log.
* Requests are answered one at a time, in the order they came. A batch (a
  JSON array) is refused. A request can be cancelled while it runs or
  waits its turn (see *Cancelling a request*).
* Send `initialize` first. It reads the settings and connects nothing: each
  server is connected the first time a method needs it, so a bad setting is
  reported before any login.
* The process exits 0 once it has answered `shutdown`, or when its input
  ends.

## Methods

Every command of the command line is a method, named by its path with
dots: `filter list` is `filter.list`. A command that changes something is
a pair, `.plan` and `.execute` (see *Plan, decide, execute* below). The one
command left out is `help`, which describes the command line's own
options; a client learns what it can call from `initialize`'s
`capabilities` and from this list. `initialize`, `shutdown`,
`plan.discard`, and `$/cancelRequest` are the protocol's own.

Parameters are passed by name, and one the method does not know is
refused. A `?` marks one that may be left out or sent as `null`. A method
the provider does not offer, by its `capabilities`, is refused before
anything connects, as the command line refuses the option it hides.

| Method | Parameters | Result |
|--------|------------|--------|
| `initialize` | `log?` | `{"protocol", "json_version", "mailctl", "provider", "capabilities", "old_name"}` |
| `shutdown` | | `null` |
| `plan.discard` | `plan_id` | `null` |
| `$/cancelRequest` | `id` | none: a notification (see *Cancelling a request*) |
| `filter.list` | `filterset?` | the `filter list --json` document |
| `filterset.list` | | the `filterset list --json` document |
| `filterset.show` | `name?` | `{"version", "filterset": {"name", "source", "filters"}}` |
| `filterset.backup` | `output?`, `dry_run?` | `{"version", "result": {"command", "filterset", "path", "filters", "written"}}` |
| `folder.list` | `counts?` | the `folder list --json` document, with `counts` as `--counts` gives it |
| `mail.search` | `folder?`, `filter?`, `raw?`, `limit?`, `sort?`, `like?`, `build_filter?` | the `mail search --json` document, or with `build_filter` a filter document |
| `mail.view` | `uid`, `folder?`, `uidvalidity?` | the `mail view --json` document |
| `mail.senders` | `folder?`, `filter?`, `by?`, `top?`, `minimum?`, `max_messages?` | the `mail senders --json` document |
| `server.test` | | `{"version", "test": {…}}` (below) |
| `server.probe` | `report?` | the `server probe --json` document, or with `report` `{"version", "report"}` |
| `server.baseline.show` | | the saved baseline, as `server baseline show --json` prints it |
| `server.baseline.check` | | the `server baseline check --json` document |

The pairs. Every `.plan` returns `{"plan_id", "document"}`, and every
`.execute` takes `plan_id` and nothing else.

| Command | `.plan` takes | `.execute` returns, inside `{"version", "result": …}` |
|---------|---------------|--------------------------------------------------------|
| `filter.add` | `filter`, `actions`, `name?`, `filterset?`, `replace?`, `placement?`, `activate?`, `create_folder?`, `subscribe?` | a filter set change |
| `filter.apply` | `filter`, `actions`, `folder?`, `max_messages?`, `create_folder?`, `subscribe?` | `{"command", "source", "destination", "flagged", "moved", "deleted", "copied"}` |
| `filter.remove` | `name`, `filterset?`, `activate?` | a filter set change |
| `filter.move` | `name`, `placement`, `filterset?`, `activate?` | a filter set change |
| `filter.rename` | `name`, `new_name`, `filterset?`, `activate?` | a filter set change; `filter` is the new name |
| `filter.enable`, `filter.disable` | `name`, `filterset?`, `activate?` | a filter set change |
| `filter.optimize` | `filterset?`, `activate?`, `skip?` | a filter set change; `filter` is `null` |
| `filterset.restore` | `file`, `filterset?`, `activate?`, `allow_empty?` | a filter set change; `filter` is `null` |
| `folder.create` | `folder`, `subscribe?` | `{"command", "folder", "subscribed", "subscribe_error", "by_subscription"}` |
| `folder.rename` | `old`, `new` | `{"command", "old", "new", "filterset", "backup", "subscribed", "unsubscribed", "subscription_errors", "ok", "checks"}` |
| `folder.subscribe`, `folder.unsubscribe` | `folder` | `{"command", "folder", "subscribed"}` |
| `mail.mark` | `uids`, `folder?`, `uidvalidity?`, `read?`, `flagged?`, `keywords?`, `no_keywords?` | `{"command", "folder", "added", "removed"}` |
| `server.baseline.save` | | `{"command", "host", "path", "taken"}` |
| `config.migrate` | | `{"command", "old", "new", "restart"}` |

A filter set change is `{"command", "filter", "filterset", "activated",
"backup"}`: the filter by the name it has once the change is made, the
filter set, whether it is now the active one, and the copy taken before
the upload.

A plan's `document` is the plan `--dry-run --json` prints for the same command
(see [*Output for scripts*][scripting]), with the same options given. `server
baseline save` and `config migrate` print no plan document on the command
line, so theirs are the API's own:

* `server baseline save`: `host`, `path`, `taken`, `previous_taken`,
  `requires_known`, `drift` (as in `server baseline check`), and `diff`,
  the file's diff, `null` where none was saved before. `changes` is
  always `true`, since a save at least moves the date.
* `config migrate`: `old`, `new`, `entries` (`{"path", "mode",
  "directory"}`), and `references` (`{"key", "old", "new"}`, each
  `config.toml` path pointed at the new directory). With no old
  directory, `changes` is `false` and `old` and `new` are `null`.

### The reads

* **`initialize`.** `protocol` versions the methods and the envelope;
  `json_version` versions the documents inside, as in [*Output for
  scripts*][scripting]. `capabilities` is what the provider declares, so a
  client offers only what it lists, as the command line does. `log: true` asks
  for `log` notifications.
* **`initialize`'s `old_name`** is what the rename from `mxfilter` left
  behind, which the command line warns about on stderr (see [*Coming from
  mxfilter*][mxfilter]): `{"settings", "config_dir"}`. `settings` lists each
  old `MXROUTE_*` name set without its `MAILCTL_*` name, as `{"old", "new",
  "where", "path"}`. `where` is `env file` or `environment`, and `path` is the
  env file as given (it may be relative), or `null`. mailctl reads no old
  name, so its setting comes from somewhere else or is unset. `config_dir` is
  `null`, or `{"old", "new", "operation"}` while the old config directory
  exists and the new one does not; `operation` is `config migrate`, the
  command that moves it, which is `config.migrate.plan` here. Names and places
  only: no old value is read, so none is sent. With nothing left behind,
  `settings` is `[]` and `config_dir` is `null`. It describes the settings the
  session started with, so it holds until `shutdown`.
* **`mail.search`.** `filter` is a filter document (see [*Filter
  documents*][filterdoc]), and `raw` a query in the host's own search
  language. `limit` is the most messages to list: 20 unless given, and every
  match where it is null. `sort` is `{"key", "reverse"?}`, with the keys `mail
  search --sort` takes.
* **`mail.search`'s `like`** is `{"uid", "uidvalidity"?}`, and does what
  `mail search --like UID --uidvalidity N` does: it adds criteria taken
  from that message in `folder`, its List-Id, else its From, and merges
  them with `filter` (a header `filter` gives replaces the one taken, and
  `filter`'s `match` and `compare` apply). The listing is of the
  message's folder, as the server spells it. There is no counterpart to
  `--derive` yet. With `uidvalidity` given and the folder renumbered
  since, it is refused with `uidvalidity_changed` before the UID is used.
* **`build_filter: true`** returns the filter document the criteria make,
  `{"version": 1, "criteria": {…}}`, in place of the listing: the
  document `filter.add.plan` and `filter.apply.plan` take as `filter`, so
  `like` and `build_filter` together fill a filter in from a message.
  Nothing is listed or saved. With no criteria, or a message with nothing
  to take them from, it is refused with `no_criteria`.
* `mail.search` refuses with `-32602`: a `like` with no `uid` or with a
  key it does not know; `raw` with `like` or `build_filter`; and `sort`
  with `build_filter`. These are the combinations `mail search` refuses.
  A `uid` below 1 is refused with `uid_below_one`, and one the folder does
  not hold with `no_such_message`, as `mail.view` refuses both.
* **`mail.senders`.** `by` is `address` (unless given), `domain`, or
  `list-id`. `top` is the rows kept, 20 unless given and every row where
  it is null; `minimum` leaves out a row counting fewer messages, 1
  unless given; `max_messages` is the ceiling, 5000 unless given, above
  which it is refused whole with `senders_ceiling`. A `top`, `minimum`,
  or `max_messages` below 1 is refused with `at_least_one`.
* **`filterset.show`** returns the filter set's text exactly as the server
  holds it, and its filters' names in order. `filters` is `null` where the
  text does not parse; the command line prints the text and then fails.
* **`server.test`** has a document of its own; the command line lays the
  same probes out for a person and offers none. `test` holds `provider`;
  `sources`, the files and places consulted; `settings`, as `{"name",
  "value", "source"}`; `password`, `{"state", "source"}`, where `state` is
  a fixed word and never the password; `connection`; `rules`, the
  extensions as `{"name", "advertised", "required", "enabled",
  "disabled_by"}` beside the `active` and `others` filter sets; `mail`,
  the `delimiter`, the number of `folders`, the `unsubscribed` ones, and
  the server's `facts`; `unrecognised`, as `filter` or `mail`; `baseline`;
  and `notes`. A `source` is `{"kind", "name", "path"}`, never a value.
  `baseline` is `null` where none is saved, `{"checked": true, "taken",
  "serious", "informational"}` where one is, and `{"checked": false,
  "error"}` where one is and cannot be compared.
* **`server.probe` with `report: true`** returns the redacted issue body
  `server probe --report` prints, as `{"title", "unrecognised", "body"}`,
  or `null` where mailctl recognises every server. Nothing is sent.
* **`server.baseline.show`** connects nothing. Its `version` is the
  baseline file's own, as stored.

### Plan, decide, execute

**A change is plan, decide, execute.** A `.plan` changes nothing. Show its
`document`, let the user decide, then send the `.execute` with the
`plan_id` and nothing else. The plan's content is never sent back, so what
was shown is what runs.

* A plan id is used once. Once an execute has taken it, whether it then
  worked or was refused, or once `plan.discard` has, the id is refused
  with `plan_unknown`. Plans live in the process and end with it.
* **Only the newest 32 plans wait.** Making another drops the oldest, and
  its id is then refused with `plan_unknown`, as a used one is. Execute or
  discard a plan before making 32 more.
* **An execute refused for its parameters keeps its plan.** The parameters
  are checked before the plan is taken, so an unknown or missing one is
  answered `-32602` and the same id still works. A `plan_id` that belongs
  to another command is refused with `plan_unknown` and left waiting.
* **A plan that changes nothing is refused at execute** with
  `nothing_to_change`, sending nothing: the command line stops after
  showing such a plan. `filter.add.execute` is the exception and carries
  it out, as `filter add` does: a filter replaced by itself is still
  uploaded, with its backup. `folder.rename` and `server.baseline.save`
  always change something.
* If the filter set changed on the server after the plan was made (in
  webmail, say), or another filter set was made active, an execute that
  uploads is refused with `filterset_changed` and nothing is uploaded.
  `folder.rename.execute` with no filter to repoint uploads nothing and is
  refused the same way, before the folder is touched.
  Make a new plan.
* `filter.apply.execute` holds the plan to its own ceiling again, and it
  and `mail.mark.execute` check the folder's UIDVALIDITY again, refusing
  with `max_messages` or `uidvalidity_changed` and touching nothing.
* **`filter.add.plan`** takes `filter`, a filter document, and `actions`,
  both required; then `name?`, `filterset?`, `replace?`, `activate?`,
  `create_folder?`, and `subscribe?` (true unless given, and false only
  with `create_folder`). `placement?` is `{"where", "filter"?}`: `where` is
  `first`, `last`, `before`, or `after`, and `filter` names the filter the
  last two are relative to. `actions` is `{"move_to"?, "discard"?,
  "flags"?, "mark_read"?, "keep"?, "stop"?}`, meaning what the same
  actions mean to `filter add`. `redirect`, `notify`, and `vacation` are
  taken as strings and refused, with the same pointer the command line
  gives.
* **`filter.apply.plan`** acts on mail already delivered and saves no
  filter. Its `actions` are `filter.add.plan`'s without `stop`, which
  means nothing to a message already in a folder. `folder` is the folder
  to search, `--folder`'s default unless given, and `max_messages` the
  ceiling, 500 unless given: a plan over it is returned with `over_limit`
  true, and its execute is refused whole with `max_messages`. A folder
  `move_to` names that does not exist is refused with `folder_missing`
  unless `create_folder` is true; one it creates is sent as a
  `folder_created` event before any message is touched. `--like` has no
  counterpart: `mail.search` with `like` and `build_filter` makes the
  filter document.
* **`filter.move.plan`**'s `placement` is required, in
  `filter.add.plan`'s shape. **`filter.optimize.plan`**'s `skip` lists
  the kinds of change not to propose: `redundant`, `reorder`, or
  `merge`; any other is refused with `unknown_kind`.
* **`filterset.restore.plan`**'s `file` is a path on the API's host. The
  API expands nothing in it, neither `~` nor `$VAR`, and every message
  names the path as given; a relative one is taken from the API's working
  directory. The file is read when the plan is made, so the execute
  uploads what the plan showed. A file the session reads the password
  from, a password file or the env file, is refused unopened with
  `credential_file`.
* **`filterset.backup` is one method, not a pair**: nothing on the server
  changes. `output` is a file or a directory, as `filterset backup
  --output` takes it, and the backup directory unless given; a file that
  exists is refused, since a backup never replaces one. With `dry_run`
  true the file is named and not written, and `written` is `false`.
* **`folder.rename.execute`**'s `checks` are what reading the account back
  found, each `{"label", "ok", "detail", "operation", "arguments"}`;
  `ok` is `false` where any failed, and a failed one's `operation` names
  the command that would set it right. `filterset` and `backup` are
  `null` where no filter files into the folder.
* **`mail.mark.plan`**'s `read` and `flagged` are `true` to set, `false`
  to clear, and absent to leave alone; `keywords` are set and
  `no_keywords` cleared. With none of them it is refused with `no_marks`,
  and a `uid` the folder does not hold with `no_such_message`, marking
  nothing.
* **`server.baseline.save.execute`** writes the configured host's file
  under the config directory, never a path the client gave. It is refused
  with `baseline_changed`, writing nothing, where the file changed after
  the plan was made, since writing over it would lose another save.
* **`config.migrate`** moves files on the API's host only. A plan that
  would overwrite anything is refused with `migration_conflict`. Its
  execute is refused with `migration_changed`, before anything moves,
  where either directory no longer matches the plan, and the plan with
  `migration_reference_unrewritable` where a `config.toml` path reaches
  the old directory through a variable it cannot be rewritten without.
  The result's `restart` is always `true`: the API read its settings
  when it started, so only a process started afterwards reads what
  moved.

## The password

No method takes a password, and a request with a `password` parameter is
refused with `-32602`. The password comes from the settings, as for `mailctl`
(see [*The password*][password]); started with `--password-cmd`, the API never
shows the client the password at all. Where no setting gives one, mailctl asks
the client, with a request of its own, the first time a server needs it:

```text
< {"jsonrpc": "2.0", "id": "mailctl-1", "method": "credentials.prompt", "params": {"request": 2, "prompt": "Password for you@example.com: "}}
> {"jsonrpc":"2.0","id":"mailctl-1","result":{"password":"..."}}
```

* mailctl's request ids are the strings `mailctl-1`, `mailctl-2`, and so
  on, so they cannot be mistaken for the client's. `request` is the id of
  the client's request that needed the password.
* To decline, answer with an error. The request that needed the password
  is then refused with `password_declined`. An answer that does not parse
  is answered `-32700` and counts as declining, and so does input ending.
  64 messages sent while the prompt waits with no answer among them end
  the wait too, refused with `prompt_input_full`, since mailctl reads no
  further until some are served.
* When a server refuses a prompted password (`auth_failed`), mailctl
  forgets it and asks again on the next request.
* Cancelling the request that needed the password ends the wait (see
  *Cancelling a request*). An answer sent after that answers nothing, and
  the next request that needs the password asks again.
* The client is holding the user's password. Never log it.

## Cancelling a request

Send the `$/cancelRequest` notification, the Language Server Protocol's
name for it, with the id of the request to stop:

```text
> {"jsonrpc":"2.0","method":"$/cancelRequest","params":{"id":7}}
```

* **A request stops between exchanges with the server, never after its
  first write.** A write may have landed, and is never sent again, so a
  request that has sent one runs to its end and is answered as usual: a
  result means it ran. One stopped in time is refused with `-32800` (see
  *Errors*).
* **A cancel names only a request sent before it**: the one in hand, or
  one still waiting its turn ahead of it, which is then refused when its
  turn comes, without running. A cancel for any other id is ignored: one
  that is unknown, one already answered, and one met in its own turn,
  once every request before it has been answered. So a request that
  reuses an answered id is never stopped by a cancel sent before it.
* A cancel is answered with nothing. Sent as a request, with an id, it
  waits its turn like any other and is answered `null`.
* **mailctl reads at most 64 messages ahead of the one it is serving.**
  A cancel sent behind 64 others not yet served is not read, and so not
  seen, until some of them have been answered.
* `filter.apply.execute` is not stopped between batches of messages: it
  hands the whole plan to the server as one write, and its
  `max_messages` ceiling bounds how long that runs.

## Notifications

mailctl sends three notifications, which take no reply. `request` is the
id of the client's request each belongs to, and `channel` says which server
it came from: `filter` or `mail`.

| Method | Parameters | Sent |
|--------|------------|------|
| `event` | `{"request", "document": {"version", "event": {"kind", ...}}}` | for each step of a change as it lands; `kind` and the keys beside it are in the table below |
| `server.alert` | `{"request", "document": {"version", "alert": {"channel", "kind", "text"}}}` | always, for an alert or warning a server sent; the same one once per session |
| `log` | `{"request", "channel", "message"}` | for the protocol exchange `--verbose` shows, only after `initialize` with `log: true` |

| Event `kind` | Keys beside it | Sent by |
|--------------|----------------|---------|
| `filterset_backed_up` | `filterset`, `path` | every execute that uploads a filter set, before it uploads |
| `filterset_uploaded` | `filterset`, `activated` | the same, once it has uploaded |
| `folder_created` | `folder`, `subscribed`, `subscribe_error`, `by_subscription` | `filter.add`, and `filter.apply` before it touches a message |
| `folder_renamed` | `old`, `new`, `children` | `folder.rename` |
| `subscription_changed` | `folder`, `subscribed` | `folder.rename` |
| `file_moved` | `source`, `destination` | `config.migrate` |
| `reference_rewritten` | `key`, `new`, `rewritten` | `config.migrate` |
| `old_dir_removed` | `path` | `config.migrate` |

## Errors

| Code | Means |
|------|-------|
| `-32700` | a line that is not UTF-8 JSON; answered with id `null` |
| `-32600` | not a request: not an object, no `"jsonrpc": "2.0"`, no method name, an id that is not a string or a number, or a batch |
| `-32601` | no such method |
| `-32602` | a parameter missing, unknown, or of the wrong type, or a `password` |
| `-32603` | an unexpected failure; the message names its type only, and `--debug` shows the rest on stderr |
| `-32800` | the request was cancelled, and nothing on the server was changed |
| `1` | mailctl refused the request |

A cancelled request's message is `the request was cancelled; nothing on
the server was changed`, and its `data` is `{"written": false}`: no write
was sent, though a read, such as the server's check of a filter set, may
already have sent it text. Where the request wrote a backup before it
stopped, `data` also carries `backup`, the backup's path.

A refusal's `data` is the error document from [*Output for
scripts*][scripting], `{"version", "error": {"message", "code", "fields"}}`,
where `code` names the condition. Branch on it, never on the message. The
API's own are `not_initialized`, `already_initialized`, `plan_unknown`, and
`password_declined`. The rest come from the work both interfaces share, so the
command line's `--json` carries the same ones. [docs/ERROR-CODES.md][codes]
lists every code, what it means, and the `fields` that come with it. A
connection failure can carry the provider's advice in `fields.advice`:
`check_settings` or `try_setting`.

## An exchange

Adding a filter, with the password left out of the settings so that
mailctl asks for it. Lines the client sends start `>`, lines mailctl sends
start `<`, and `…` marks what is left out.

```text
> {"jsonrpc":"2.0","id":1,"method":"initialize"}
< {"jsonrpc": "2.0", "id": 1, "result": {"protocol": 1, "json_version": 2, "mailctl": "…", "provider": "mxroute", "capabilities": {…}, "old_name": {"settings": [], "config_dir": null}}}
> {"jsonrpc":"2.0","id":2,"method":"filter.add.plan","params":{"filter":{"version":1,"criteria":{"match":"all","compare":"contains","terms":[{"header":"From","value":"noreply@github.com"}]}},"actions":{"move_to":"Lists/GitHub"},"name":"github","create_folder":true}}
< {"jsonrpc": "2.0", "id": "mailctl-1", "method": "credentials.prompt", "params": {"request": 2, "prompt": "Password for you@example.com: "}}
> {"jsonrpc":"2.0","id":"mailctl-1","result":{"password":"…"}}
< {"jsonrpc": "2.0", "id": 2, "result": {"plan_id": "1cf4f027f29155b8", "document": {"version": 2, "plan": {"command": "filter add", …}}}}
> {"jsonrpc":"2.0","id":3,"method":"filter.add.execute","params":{"plan_id":"1cf4f027f29155b8"}}
< {"jsonrpc": "2.0", "method": "event", "params": {"request": 3, "document": {"version": 2, "event": {"kind": "filterset_backed_up", …}}}}
< {"jsonrpc": "2.0", "method": "event", "params": {"request": 3, "document": {"version": 2, "event": {"kind": "folder_created", …}}}}
< {"jsonrpc": "2.0", "method": "event", "params": {"request": 3, "document": {"version": 2, "event": {"kind": "filterset_uploaded", …}}}}
< {"jsonrpc": "2.0", "id": 3, "result": {"version": 2, "result": {"command": "filter add", "filter": "github", …}}}
> {"jsonrpc":"2.0","id":4,"method":"filter.add.execute","params":{"plan_id":"1cf4f027f29155b8"}}
< {"jsonrpc": "2.0", "id": 4, "error": {"code": 1, "message": "no plan with that id is waiting: …", "data": {"version": 2, "error": {"message": "no plan with that id is waiting: …", "code": "plan_unknown", "fields": {"plan_id": "1cf4f027f29155b8"}}}}}
> {"jsonrpc":"2.0","id":5,"method":"shutdown"}
< {"jsonrpc": "2.0", "id": 5, "result": null}
```

[codes]: ERROR-CODES.md
[adr8]: ../adr/0008-json-rpc-api-interface-for-out-of-process-clients.md
[configure]: CONFIGURATION.md
[docker]: DOCKER.md
[filterdoc]: USAGE.md#filter-documents
[mxfilter]: CONFIGURATION.md#coming-from-mxfilter
[password]: CONFIGURATION.md#the-password
[scripting]: SCRIPTING.md
