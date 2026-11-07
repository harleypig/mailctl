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

The tier itself is described in the core's *Four tiers*; *The API* and
*The API's framing* are [tests-api.md](tests-api.md).

This is a map: what each file is for and the property it must hold. What
each test pins, and the issue it came from, is in the test's own name and
docstring. Read the file before adding to it.

- `criteria` — a criteria set translated to Sieve **and** to IMAP `SEARCH`,
  including the cases where the two differ and the client-side re-check
  closes the gap; criteria from a message merged with flags; and the
  filter document round-tripping and refusing each malformed shape by
  name.
- `sieve` (`test_sieve.py`, through the MXroute dialect) — parse, merge,
  render, and rename in place, and confirm rules the tool did not write
  survive. The overwrite-would-have-destroyed-it case is the one that
  matters ([ADR 0002](../../adr/0002-non-destructive-script-merge.md)),
  with a hand-written Roundcube-shaped script as its fixture. A refused
  action (`redirect`, `notify`, `vacation`) raises with the pointer
  message rather than emitting the action.
- `imap` (`test_imap.py`) — folder-name normalization across both
  separators (`Lists/GitHub` ≡ `INBOX.Lists.GitHub`), and the session over
  the `fake_imap` double: how a connection is opened, shut down, and how a
  socket error is raised coded. What MXroute and `Config` add is
  `test_mxroute_imap.py`; server-module selection by `ID` is
  `test_imap_servers.py`.
- **Non-ASCII search** (`test_imap_search.py`) — run through IMAPClient's
  **real** serializer over a recording transport, because the double
  records a key without ever encoding it.
- **Folder counts** (`test_imap_status.py`) — `LIST-STATUS` lines read into
  counts, run again through imaplib's **real** reader, since only it turns a
  literal name into the shape the parser must handle.
- **Server alerts** (`test_imap_alerts.py`) — an `ALERT` read out of every
  status response and handed to `progress`, run again over IMAPClient and
  imaplib reading a scripted server's bytes, since the session wraps a
  private of imaplib. The CLI's half is the `folders-alert*` snapshots.
- `config` — the settings ladder, `Secret` rendering `<redacted>`, and
  `MAILCTL_BACKUP_ROOT` taken from the environment only. The utilities'
  half of the backup root is `test_utilities_backup.py`.
- **The utilities** (`test_utilities_<module>.py`, one per module under
  `mailctl/utilities/`; a backup's bytes on disk are `test_backup.py`,
  the migration utility `test_migration.py`) — every plan and execute
  step driven with plain inputs over a session, as any front-end would
  call it. The safety policy is pinned here, since it lives here: backup
  before upload, merge never overwrite, the `--max-messages` ceiling, the
  UIDVALIDITY check, a stale plan refused, a backup never replacing a
  file, the checkpoint stopping work between exchanges, and every refusal
  carrying its code. `utilities/server_test.py` has no file of its own;
  *The API* covers it. The fakes they share are
  `tests/utilities_support.py`.
- **The session** (`test_engine.py`) — only what is about the connection:
  lazy open per half, a read sent once more and a write never, the lock,
  sessions sharing nothing, the idle probe, the checkpoint, and every
  transport operation's kind pinned so a change of kind is a deliberate
  diff.
- **Sieve extensions** (`test_extensions.py`) — the emit table checked
  against sievelib's `require` line over every rule shape, and
  `disabled_extensions`. Its ladder is in `test_config.py`.
- **The old name** (`test_migration.py`) — `config migrate` and the
  old-name warnings, checked against a sentinel value that must never
  appear in any output. The `mxfilter` script's reuse is in
  `test_utilities_rules.py`.
- **CLI snapshots** (`test_cli_snapshots.py`) — each subcommand run end to
  end through `cli.main`, recording exit code, stdout, stderr, and every
  server call into `tests/snapshots/cli/<name>.txt`. A behaviour change
  shows up as a snapshot diff; regenerate with
  `MAILCTL_UPDATE_SNAPSHOTS=1` and read the diff before committing. Both
  sides are compared after `argparse_neutral()`, since argparse's wrapping
  and quoting vary with the Python version. `tests/conftest.py` sets
  `NO_COLOR`, clears `PYTHON_COLORS` and `FORCE_COLOR`, and pins `COLUMNS`
  to 80 for every test.
- **The command tree** (`test_cli_help.py`, over `tests/cli_support.py`,
  which reads the groups and commands off the parser) — `mailctl help`
  matching `--help` everywhere, the groups' order, and every old flat name
  refused naming its new one.
- **`--json`** (`test_json_output.py`) — the documents the CLI prints and
  the API shares, one mapping per neutral record; a `Secret` refused
  rather than serialised; failures as one coded JSON line; and no refused
  command line quoting a word argparse could not use.
- **Packaging** (`test_packaging.py`) — a wheel built from a copy of the
  source and run with nothing from the checkout on the path, so a
  subpackage left out fails here rather than in a user's install.
- **The Makefile targets** (`test_make_typecheck.py`,
  `test_make_install.py`, `test_make_pytest.py`) — each target run from a
  copy of the Makefile alone with a stand-in tool on `PATH`: refused
  without the venv's own tool, running the venv's rather than `PATH`'s, and
  failing when the tool fails.
- The **presentation guard** (`test_core_no_presentation.py`) — no core
  module prints, prompts, or exits, and neither the session nor any
  utility imports a front-end or reads the terminal or the environment.
  No string a core module builds names a CLI flag or command; every error
  code has a rendering in `cli.ERROR_TEXT` and every rendering a raiser;
  and every `MailctlError` built anywhere passes a `code`. It reads names
  in the source, so a code passed through `**` or a variable is taken on
  trust.
- **The error-code catalogue** (`test_error_codes_catalogue.py`) —
  `docs/ERROR-CODES.md` lists every code the package raises, and no other,
  and none twice. A code the walk cannot find fails rather than passes.
- The **layer-purity guard** (`test_layer_purity.py`) — the four layers
  of [ADR 0007][adr7] over the components of [ADR 0006][adr6], held line
  by line:
  - a module under `components/` imports only the stdlib, the library
    its own component wraps, other components, and `MailctlError`;
  - `engine.py`, every module under `utilities/`, and `cli.py` import no
    component, and the session and the utilities reach a provider only
    through `providers.base` and `providers.registry`, never by name;
  - `providers/base.py` and `providers/model.py` import no component, and
    every model type is the neutral model's own class;
  - each provider's two halves are derived by following their imports: no
    module is on both sides, a dialect opens no connection, and a
    transport imports no building helper and no utility;
  - the **interface guard**: a front-end imports only the utilities, the
    session's opening calls, and the neutral model;
  - a utility reaches the transport only through the session it was
    handed;
  - the **read/write guard**: a read-only utility never reaches a
    transport operation classified write. Read-only is derived, not
    listed, and reach is followed through other utilities to a fixed
    point.

  The interfaces are derived as every module outside the core, so the API
  is under the interface guard unlisted.
- **Neutral wording** (`test_neutral_wording.py`) — under `mxroute`, no
  provider word, and no *rule* outside a quoted name, in any help page or
  snapshot output outside the places
  [providers.md](providers.md) *What the user sees* allows. A filter's
  step `kind` is held to it too.
- **Providers** (`test_providers.py`) — each half of every registered
  provider implements or explicitly declines every operation, and the two
  decline exactly what the capabilities say. A fake second provider is
  driven through the utilities and must receive the same calls as
  `mxroute`'s; fakes without each capability pin what that removes from
  the help and what is refused.

**Each guard — presentation, error codes, layer purity, neutral wording —
carries a planted case it is seen to catch**, so none passes for reading
nothing. A new guard needs one too.

[adr7]: ../../adr/0007-interfaces-utilities-session-provider-layering.md
[adr6]: ../../adr/0006-two-layer-component-and-provider-architecture.md
