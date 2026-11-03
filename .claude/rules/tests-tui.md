---
paths:
  - "clients/tui/**"
---

# mailctl Tests — the TUI

Path-scoped detail moved out of `.claude/TESTS.md`, which stays always
loaded as the short core; read the core's *Where the detail lives* for
the whole map. Claude Code loads this file only when a file matching
the `paths:` above is read or edited.

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
  execute. A search sends what was typed, leaves an empty folder to the API,
  and names the folder as the API spelt it; a message is read in the listing's
  folder under its UIDVALIDITY, its text keeping its lines with markup shown
  as characters and control characters escaped, and a key pressed while
  `mail.view` is held unanswered sends nothing and leaves the search screen, a
  refused one giving its keys back ([#401][i401]); an alert reaches the panel
  over another screen. The form is filled in by `mail.search`'s `like`, never
  from the message's headers, with criteria it has no field for carried as
  sent; an API refusing `like` or `build_filter` as unknown opens an empty
  form that says so, and any other -32602 is shown in the API's own words. A
  key pressed while `like` is held unanswered, by the stub's `hold`, sends
  nothing and leaves the message screen ([#381][i381]). The plan is shown as
  the document has it, saving sends `filter.add.execute` the plan's id alone
  and shows each `event` and where the backup went, Escape sends
  `plan.discard`, a plan that changes nothing offers no save, and a refused
  plan or a failed save says so. The form offers only what the provider
  declares, one capability withheld at a time removing exactly its options;
  the filter list's keys are not offered over another screen; `subscribe` is
  sent only beside `create_folder`, and what the user left alone is not sent;
  and the form opens focused on the first field there is something to give.
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

[adr9]: ../../adr/0009-python-textual-tui-as-a-separate-package.md
[i351]: https://github.com/harleypig/mailctl/issues/351
[i348]: https://github.com/harleypig/mailctl/issues/348
[i259]: https://github.com/harleypig/mailctl/issues/259
[i401]: https://github.com/harleypig/mailctl/issues/401
[i381]: https://github.com/harleypig/mailctl/issues/381
