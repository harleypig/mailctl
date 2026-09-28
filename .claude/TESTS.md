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
   - `engine` — every plan and execute step driven with plain inputs and
     session fakes, as any front-end would call it (`test_engine.py`).
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
     in `test_engine.py`.
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
     module prints, prompts, or exits, and the engine imports no front-end.
     `components/` and `providers/` are walked, not listed.
   - The **layer-purity guard** (`test_layer_purity.py`) — a module under
     `components/` imports only the stdlib, the library its own component
     wraps (`imapclient` for `imap`, `sievelib` for `managesieve`), other
     components, and `MailctlError` ([ADR 0006][adr6]). The other way
     round, `engine.py` and `cli.py` import no component, and the engine
     reaches a provider only through `providers.base` and
     `providers.registry`, with no provider's name in its code.
   - **Providers** (`test_providers.py`) — every registered provider
     implements or explicitly declines every operation of `Provider`, and
     declines exactly what its capabilities say. The `provider` setting
     climbs the ladder with provenance; an unknown name is refused before
     anything connects. A fake second provider, registered for the test,
     is driven through the engine's representative operations and receives
     the same calls, in the same shape, as `mxroute`. A capability it
     declines is refused before it is opened.
2. **Live tests** (`MAILCTL_LIVE=1`) — stand up **real** Sieve scripts and
   move **real** mail against a **live MXroute account**. They mutate real
   state; run them manually (`make testlive`), **never** in a default gate.

The live tier is also this repo's **end-to-end** pass (`qa.md` dimension 8) —
there is no third tier and no separate e2e suite. A CLI that talks to two
servers has no meaningful integration layer between "offline logic" and "does
it actually work against MXroute".

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
```

`TESTARGS` passes extra flags through, e.g. a run filter for a scoped live
pass: `make testlive TESTARGS='-k sieve'`.

[i89]: https://github.com/harleypig/mailctl/issues/89
[i90]: https://github.com/harleypig/mailctl/issues/90
[adr6]: ../adr/0006-two-layer-component-and-provider-architecture.md
