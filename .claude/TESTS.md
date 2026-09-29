# mailctl Test Layout

The global `testing.md` carries the bar (success **and** failure paths, a
regression test per bug, a manual verification note per feature); `python.md`
carries the layout convention (`tests/` at the repo root, mirroring the
package). This file records what belongs here.

**The offline tier is written and green** (`make test`); the only skips are
the live-gated ones. `pytest -q` reports the current count; none is kept
here, because a count nobody re-derives is only ever stale.
The live tier is scaffolded (`tests/live/`) and skipped by default; it stays
open until it has run against a real account, and the backup-and-restore
fixture required before anything writes to one is still outstanding
([#9](https://github.com/harleypig/mailctl/issues/9)).

## Two tiers

1. **Unit tests** (`tests/test_*.py`) — **offline**, no network, no
   credentials. The default gate, and the tier that should hold almost
   everything. The package was split specifically so this tier can reach the
   interesting logic:
   - `criteria` — a criteria set translated to Sieve **and** to IMAP `SEARCH`,
     including the cases where the two differ and the client-side re-check
     closes the gap.
   - `sieve` (`test_sieve.py`, through the MXroute dialect) — parse an
     existing script, merge a rule into it, render it back, and confirm
     rules the tool did not write survive verbatim. The
     overwrite-would-have-destroyed-it case is the one that matters
     ([ADR 0002](../adr/0002-non-destructive-script-merge.md)); it deserves a
     test with a hand-written Roundcube-shaped script as its fixture.
   - `sieve` — a refused action (`redirect`, `notify`, `vacation`) raises with
     the pointer message rather than emitting the action.
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
   - `config` — the flag → env → file → default resolution order, and that a
     `Secret` renders `<redacted>` from `str()`, `repr()`, and an f-string.
   - **The utilities** (`test_utilities_<module>.py` for `rules`,
     `scripts`, `backup`, `folders`, `mail`, `messages`, and `reports`
     under `mailctl/utilities/`; a backup's bytes on disk are
     `test_backup.py`, and the migration utility `test_migration.py`) —
     every plan and execute step driven with plain inputs over a session,
     as any front-end would call it. The safety
     policy is pinned here, since it lives here: an upload backs up first
     and still leaves the backup when the server rejects the script, a
     rule is merged rather than written over, and a mail plan over the
     `--max-messages` ceiling is refused whole. `test_utilities_mail.py`
     also holds the re-check narrowing the host's search to what the rule
     matches. The fakes they share are `tests/utilities_support.py`.
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
   - **The old name** (`test_migration.py`) — `migrate-config` moving the
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
   - **The ManageSieve wrapper** (`test_managesieve_client.py`) — the real
     `SieveClient` against a scripted socket that replays a server's bytes:
     GETSCRIPT byte-exact whatever the script ends with ([#90][i90]), the
     whole CAPABILITY response kept, the read timeout honoured, and nothing
     printed even with sievelib's debug flag forced on. Its server-module
     selection and capability parsing are `test_managesieve_servers.py`.
   - The **presentation guard** (`test_core_no_presentation.py`) — no core
     module prints, prompts, or exits, and neither the session nor any
     utility imports a front-end or reads the terminal or the environment.
     `components/`, `providers/`, and `utilities/` are walked, not listed.
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
       component, or the session's transport, connection, or dialect.

     The component, session-and-utilities, neutral-model, and half guards
     each carry a case built to break them, so each is seen to fail as well
     as pass.
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
     Fakes without a capability pin what that removes ([#99][i99]):
     without `stop` a default rule still plans; without `ordering` the
     placement flags and `move-rule` are not offered in help and are
     refused by name if given; without `extensions` `disabled_extensions`
     is refused; a connection flag the provider does not read is hidden and
     refused; and `add` and `test` under a fake carry its own wording, with
     nothing about Sieve or MXroute. `mxroute`'s help hides nothing but the
     always-hidden flags.
2. **Live tests** (`MAILCTL_LIVE=1`) — stand up **real** Sieve scripts and
   move **real** mail against a **live MXroute account**. They mutate real
   state; run them manually (`make testlive`), **never** in a default gate.

The live tier and the read-only live check below are together this repo's
**end-to-end** pass (`qa.md` dimension 8) — there is no separate e2e suite. A
CLI that talks to two servers has no meaningful integration layer between
"offline logic" and "does it actually work against MXroute".

## The read-only live check

`scripts/live-readonly.sh`, run by `make livecheck`, is a bash script that
runs read-only CLI commands and `--dry-run` plans against the real account,
then verifies that nothing changed. It reports in TAP. Name tests as
arguments to run only those; `--list` shows the names.

It is **not** the pytest live tier above, and the difference is the point:

- **It never writes.** `tests/live/` will write, under the backup-and-restore
  fixture [#9](https://github.com/harleypig/mailctl/issues/9) requires; this
  one never does, so it needs no such fixture and is safe to run any time.
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
script, not to `tests/live/`.

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
- **Back up and restore the active script.** A live run must capture the
  account's existing script before it writes, and put it back afterwards —
  including on failure. The account's real filters are not the test's to lose.
- **Scope the mail-moving tests to a dedicated folder.** They must not run
  against `INBOX` and must not disturb live mail; use a purpose-made test
  folder and tear it down. Prefer messages the test appended itself over
  whatever happens to be in the mailbox.
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
criterion; "`mailctl list` shows the rule, and a new message matching it
lands in `Lists/GitHub`" is.

## Running

```sh
pytest                 # unit (offline, credential-free)
make test              # the same, via the Makefile
make testlive          # live (MAILCTL_LIVE=1; needs MAILCTL_* in the env)
make livecheck         # read-only live check (normal mailctl config)
scripts/live-readonly.sh --list        # the read-only check's test names
scripts/live-readonly.sh list rules    # run only the named checks
```

`TESTARGS` passes extra flags through, e.g. a run filter for a scoped live
pass: `make testlive TESTARGS='-k sieve'`.

[i89]: https://github.com/harleypig/mailctl/issues/89
[i90]: https://github.com/harleypig/mailctl/issues/90
[adr6]: ../adr/0006-two-layer-component-and-provider-architecture.md
[adr7]: ../adr/0007-interfaces-utilities-session-provider-layering.md
[i99]: https://github.com/harleypig/mailctl/issues/99
