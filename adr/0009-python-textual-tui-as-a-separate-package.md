# 9. The TUI is a Python and Textual package in this repository

- Status: accepted
- Date: 2026-10-30
- Accepted: 2026-10-30, by the operator on [#259][i259], for the language
  and the home. The framework is this record's choice, made under that
  decision (*Framework*, below).
- Builds on [ADR 0008][adr8]. Recorded for [#259][i259].

## Context

**The first client of the API is a TUI** ([#259][i259]). [ADR 0008][adr8]
made mailctl's operations an API, JSON-RPC 2.0 over the stdio of a process
the client starts, so that a UI in any language can drive the core. It
left the first client's language, framework, and home to that client's
issue, and held the client to a bar: it never logs, prints, or stores the
password it collects for `credentials.prompt`; it offers only what
`initialize`'s capabilities allow; and it renders the API's documents
rather than re-deriving what the core decides.

**Three questions, then.** What it is written in; where it lives; and how
it reaches a user, given that mailctl itself is published nowhere
([RELEASING.md](../RELEASING.md)).

**The operator answered the first two**, on [#259][i259], 2026-10-30:

> python, this repo, but icebox using go/bubbletea in a separate repo as a
> poc.

## Decision

### Language

**Python.** The client is the operator's own tool, kept beside a Python
core by the same person, so one toolchain covers both: `uv`, `ruff`,
`pyright`, and `pytest` as CONVENTIONS *Toolchain & reproducibility*
already sets them out. A second language would buy nothing the TUI needs
and would cost a second toolchain in CI.

**Python does not let it reach into the core.** It could import
`mailctl`, and it must not: [ADR 0008][adr8] puts every UI outside the
package, as a client of the API. Being in the same language makes that
line easy to cross by accident, so a test holds it (*The bar*, below).

### Framework

**Textual**, pinned `textual>=8.2,<9`. 8.2.8 is the newest release, of
2026-06-30, and declares Python 3.9 to 3.14, which covers mailctl's 3.11
floor. It gives this client four things it needs, each checked against
its documentation on 2026-10-30 (*Sources*):

- **A masked field for the password.** `Input(password=True)`, whose
  `password` is *"True if the input should be masked."*
- **Async work beside the screen.** The API is a child process whose
  answers arrive when they arrive, and it may ask for the password in the
  middle of a request. Textual runs on asyncio, so the client reads the
  child's stdout in a task of its own, and a worker (`@work`) awaits a
  request without blocking the screen.
- **Commands the app can withhold.** An app's `check_action` decides,
  before an action runs or the footer is drawn, whether a key is shown:
  *"False to hide the key and prevent the action running."* That is
  where the capabilities gate sits.
- **A test harness.** `App.run_test()` *"is an async context manager
  which returns a Pilot object"*, which presses keys and waits on the
  app, headless; the documentation pairs it with `pytest-asyncio`.

`rich` is declared too, without an upper bound of its own: the client
hands every piece of server text to the screen as a `rich.text.Text`, so
it is read as characters and never as markup. Textual depends on it and
sets the bound.

### Home

**`clients/tui/`, a separate distribution, `mailctl-tui`, with its own
`pyproject.toml`** and its package under `src/mailctl_tui/`.

- **Separate, so Textual is never a dependency of mailctl.** mailctl's
  `pyproject.toml` is untouched; a user who installs the CLI or the API
  pulls in nothing of the TUI's.
- **`clients/`, not `mailctl/` and not `tui/`.** Everything under
  `mailctl/` is the package, and the layer guards walk it. A client is
  outside it by [ADR 0008][adr8]'s own words, and `clients/` is where the
  later ones go: the web UI's backend, and anything else that drives the
  API.
- **`src/` layout**, so that running its tests from the repository root
  cannot import the package from the working tree by accident, and an
  installed copy is what is tested.
- **Its tests live with it** (`clients/tui/tests/`) and run with its own
  pytest configuration. The root suite's `testpaths` does not reach them,
  so `make test` still needs nothing of Textual's.

### How it ships

**As mailctl does: from a checkout, published nowhere.**

- **It does not depend on the `mailctl` package.** It starts the
  `mailctl-api` command it finds on `PATH`, so it needs that command
  installed, not mailctl in its own environment. `uv tool install .` and
  `uv tool install ./clients/tui` give each its own environment and put
  both commands on `PATH`; installing both into one virtual environment
  works the same way.
- **`--api COMMAND` names another command** to start, for a checkout
  that is not installed, or for a test double.
- **Every other argument is the API's.** `mailctl-tui` passes what it
  does not know to `mailctl-api`, which takes the CLI's connection
  settings, so the TUI never learns a setting's name.
- **It is not in the Docker image,** which builds mailctl alone. Whether
  a TUI belongs in it is a later question, asked when someone wants it
  there.

### The bar

[ADR 0008][adr8]'s *Consequences*, held by tests in `clients/tui/tests/`,
each seen to fail on a planted violation:

- **It imports nothing from `mailctl`.** Every module of the package is
  walked, and an `import` of `mailctl` or anything under it, or a load of
  one by name at run time, fails the suite.
- **It offers only what the capabilities allow.** Every key binding is
  built from one list of commands, each naming the capability it needs,
  and `check_action` hides a command whose capability `initialize` did
  not declare as true; a name the provider does not declare counts as
  false. Textual's command palette is off, since its own commands would
  be offered from outside that list. A test against the real API checks
  every capability the list names is one `initialize` returns.
- **The password goes from the masked field into the reply and nowhere
  else.** The field is cleared as the prompt closes, the reply is built
  and written in one place, and nothing is logged. A test types it and
  then finds no container the app holds with it.
- **It renders documents.** It shows `filter.list`'s document as it is,
  and words an error with the message the API sent, which names no
  front-end's control (CONVENTIONS *A core message names no front-end's
  flag or command*). Server text is shown with its control characters
  escaped, as the CLI shows it.

## Alternatives rejected

- **A repository of its own.** It would keep the TUI's history apart and
  make the boundary physical. The cost is the boundary's other side: the
  API's documents change in this repository, and the TUI's tests need the
  real API to check the stub they use against it. In one repository a
  change to a document and the client that renders it land in one pull
  request, and CI runs both. The import guard keeps the line a separate
  repository would have kept. The operator chose this repository.
- **Go, with Bubble Tea.** A single static binary is easier to hand to
  someone than a Python environment, and a client in another language
  would prove [ADR 0008][adr8]'s *any language* claim. It costs a second
  toolchain for the one person maintaining both halves, now. The operator
  chose Python for this client and asked for the Go one as a proof of
  concept in a repository of its own, recorded in the icebox.
- **A module beside `cli.py`.** A second in-process front-end, which the
  ICEBOX *TUI front-end* entry first described. [ADR 0008][adr8] settled
  that a UI is a client of the API instead, and this client is the first
  test of that.

## Consequences

- **CI gains a job,** *TUI*, that type-checks the client and runs its
  tests on the oldest and newest Python mailctl supports, with mailctl
  installed beside it so the test against the real API runs. The `Test`
  check the branch ruleset requires waits on it as well as on the unit
  tier, so a red TUI blocks a merge. `ruff` already covers it from the
  `Lint` job, under the root's configuration.
- **Where the API's documents lack something a screen needs, the gap is
  the API's to close.** The first is a filter's actions: `filter.list`
  gives them in the host's own words (`fileinto`), which the screen may
  not show (CONVENTIONS *What the user sees is the same for every
  provider*), and wording them is the provider's job, which the CLI
  reaches through a utility the TUI cannot call. The first screen shows a
  filter's conditions and marks, and leaves its actions out until the
  document carries them in neutral terms.
- **The stub and the real API are both in the suite.** The stub lets a
  test decide what the API says; one test against mailctl's
  `tests/api_child.py` keeps the stub honest about the real one.

## Sources

Fetched 2026-10-30.

- [Textual on PyPI][pypi] — 8.2.8, released 2026-06-30; Python 3.9 to
  3.14; MIT.
- [Textual, *Input*][input] — the `password` attribute, *"True if the
  input should be masked."*
- [Textual, *Workers*][workers] — `run_worker` and the `@work` decorator;
  *"The run_worker method schedules a new worker"*, and `exclusive`.
- [Textual, *Actions*][actions] — `check_action`: *"True to show the key
  and run the action as normal. False to hide the key and prevent the
  action running."*
- [Textual, *Screens*][screens] — modal screens, and *"To return data
  from a screen, call dismiss()"*.
- [Textual, *Testing*][testing] — *"The run_test() method is an async
  context manager which returns a Pilot object"*, and the
  `pytest-asyncio` plugin with `asyncio_mode = auto`.
- [pytest-asyncio on PyPI][pya] — 1.4.1, the newest release.

## Based on

- [ADR 0008][adr8] — the API this client drives, and the bar it sets the
  first client.
- [#259][i259] — the issue, and the operator's answer of 2026-10-30.

[adr8]: 0008-json-rpc-api-interface-for-out-of-process-clients.md
[i259]: https://github.com/harleypig/mailctl/issues/259
[pypi]: https://pypi.org/project/textual/
[input]: https://textual.textualize.io/widgets/input/
[workers]: https://textual.textualize.io/guide/workers/
[actions]: https://textual.textualize.io/guide/actions/
[screens]: https://textual.textualize.io/guide/screens/
[testing]: https://textual.textualize.io/guide/testing/
[pya]: https://pypi.org/project/pytest-asyncio/
