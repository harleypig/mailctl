---
paths:
  - "mailctl/api/**"
  - "mailctl/json_output.py"
  - "clients/**"
---

# mailctl Conventions — the API

Path-scoped detail moved out of `.claude/CONVENTIONS.md`, which stays always
loaded as the short core; read the core's *Where the detail lives* for
the whole map. Claude Code loads this file only when a file matching
the `paths:` above is read or edited.

## The API as an interface

These paragraphs were part of *The core returns data; only the interfaces
print*, which the core keeps.

**The API is the second interface, and a UI is its client.** A TUI, GUI,
or web UI is presentation only, may be written in any language, and lives
outside the package, calling the API rather than the utilities
([ADR 0008][adr8]). Over stdio its one peer is the process that started
it, so the client is not authenticated: it already runs as the same user.
**The first client is the TUI** in `clients/tui/`, in Python on Textual
([ADR 0009][adr9]): it starts `mailctl-api`, imports nothing from
`mailctl`, offers only what `initialize`'s capabilities allow, and
renders the API's documents rather than re-deriving them.

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

[adr8]: ../../adr/0008-json-rpc-api-interface-for-out-of-process-clients.md
[adr9]: ../../adr/0009-python-textual-tui-as-a-separate-package.md
[i346]: https://github.com/harleypig/mailctl/issues/346
[i331]: https://github.com/harleypig/mailctl/issues/331
[i258]: https://github.com/harleypig/mailctl/issues/258
[i279]: https://github.com/harleypig/mailctl/issues/279
[i259]: https://github.com/harleypig/mailctl/issues/259
[i341]: https://github.com/harleypig/mailctl/issues/341
