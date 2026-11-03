---
paths:
  - "tests/test_api*.py"
  - "tests/api_support.py"
  - "tests/api_child.py"
  - "mailctl/api/**"
---

# mailctl Tests — the API

Path-scoped detail moved out of `.claude/TESTS.md`, which stays always
loaded as the short core; read the core's *Where the detail lives* for
the whole map. Claude Code loads this file only when a file matching
the `paths:` above is read or edited.

## The API's tests

Two entries of the unit tier's list ([tests-unit.md](tests-unit.md)).

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
  `config.test` returning `server.test`'s settings and mail parts
  unchanged and the filter half only as connected, and refused, coded
  as `server.test` is, where a half cannot be reached ([#440][i440]);
  `server.baseline.save` writing once and refusing a file changed since
  the plan, `show` and `check` reading it, `no_baseline` coded; and
  `config.migrate` moving the old directory once, refusing a clash, a
  plan a file or a baseline since clashes with, and a variable expanded
  into a `config.toml` path. Neither the server group nor `config.test`
  sends the password.
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
  on. An error whose `fields` cannot be sent, a `Secret` among them,
  is answered with its code and message and `fields` empty, and
  serving goes on, the next request answered in its turn ([#407][i407]).
  The input thread and the cancel ([#280][i280]): `take` picking
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
  ended rather than hung, and refused coded `prompt_input_full` with
  the limit in its fields, never as the user declining, the held
  messages keeping their turn ([#378][i378]); a declined bad line, and
  a cancel ending a
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
  or not the LOGOUT is answered ([#362][i362]). Asked for STARTTLS, a
  server whose CAPABILITY lacks it is refused, coded
  `starttls_not_offered`, with nothing sent, and its socket closed
  without a LOGOUT; MXroute's advice to try the other TLS mode, and the
  CLI's words naming `--sieve-tls ssl`, are `test_mxroute_managesieve.py`
  ([#377][i377]). Its server-module selection and capability parsing
  are `test_managesieve_servers.py`.

[i258]: https://github.com/harleypig/mailctl/issues/258
[i349]: https://github.com/harleypig/mailctl/issues/349
[i352]: https://github.com/harleypig/mailctl/issues/352
[i280]: https://github.com/harleypig/mailctl/issues/280
[i364]: https://github.com/harleypig/mailctl/issues/364
[i307]: https://github.com/harleypig/mailctl/issues/307
[i259]: https://github.com/harleypig/mailctl/issues/259
[i341]: https://github.com/harleypig/mailctl/issues/341
[i346]: https://github.com/harleypig/mailctl/issues/346
[i279]: https://github.com/harleypig/mailctl/issues/279
[i440]: https://github.com/harleypig/mailctl/issues/440
[i332]: https://github.com/harleypig/mailctl/issues/332
[i331]: https://github.com/harleypig/mailctl/issues/331
[i407]: https://github.com/harleypig/mailctl/issues/407
[i378]: https://github.com/harleypig/mailctl/issues/378
[i90]: https://github.com/harleypig/mailctl/issues/90
[i208]: https://github.com/harleypig/mailctl/issues/208
[i232]: https://github.com/harleypig/mailctl/issues/232
[i362]: https://github.com/harleypig/mailctl/issues/362
[i377]: https://github.com/harleypig/mailctl/issues/377
