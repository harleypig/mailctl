# mailctl Test Layout

The global `testing.md` carries the bar (success **and** failure paths, a
regression test per bug, a manual verification note per feature); `python.md`
carries the layout convention (`tests/` at the repo root, mirroring the
package). This file records what belongs here.

**The offline tier is written and green** (`make test`); the only skips are
the live- and container-gated ones. `pytest -q` reports the current count;
none is kept here, because a count nobody re-derives is only ever stale.
The live tier (`tests/live/`) is skipped by default and run by hand; the
container tier (`tests/container/`) is where every write path is proved
first. The TUI's tests are a separate suite with their own configuration,
run by `make testtui` and never by the root `pytest`.

## Where the detail lives

**This file is the always-loaded core; the detail is path-scoped.** What
each test file covers is in `.claude/rules/`, loaded only when a matching
file is read or edited:

| Section | Home | Loads for |
|---|---|---|
| *Three tiers* › the unit tier, file by file | [rules/tests-unit.md](rules/tests-unit.md) | `tests/**` |
| *Three tiers* › *The API* and *The API's framing* | [rules/tests-api.md](rules/tests-api.md) | `tests/test_api*.py`, `tests/api_*.py`, `mailctl/api/**` |
| *The container tier* | [rules/tests-container.md](rules/tests-container.md) | `tests/container/**` |
| *The read-only live check*, *Live-test credentials & safety* | [rules/tests-live.md](rules/tests-live.md) | `tests/live/**`, `tests/live_write.py`, `tests/container/test_live_*.py`, `scripts/live-readonly.sh` |
| *The TUI* | [rules/tests-tui.md](rules/tests-tui.md) | `clients/tui/**` |

## Three tiers

1. **Unit tests** (`tests/test_*.py`) — **offline**, no network, no
   credentials. The default gate, and the tier that should hold almost
   everything. The package was split specifically so this tier can reach the
   interesting logic, and what each module's tests pin is
   [rules/tests-unit.md](rules/tests-unit.md).
2. **Live tests** (`MAILCTL_LIVE=1`) — stand up **real** Sieve scripts and
   move **real** mail against a **live MXroute account**. They mutate real
   state; run them manually (`make testlive`), **never** in a default gate.
   A test that writes also needs `MAILCTL_LIVE_WRITE=1` and the write
   guards (*Live-test credentials & safety*). The writing tests are
   `test_live_write_smoke.py`, which saves one filter, and
   `test_live_write_paths.py` ([#388][i388]), one test each for `filter
   apply` (one appended message moved into a folder made under the
   scratch folder), `folder create` (a folder made and subscribed under
   it), `filter remove` (a filter added, then removed, leaving the
   others as they were), and `filterset restore` (the active filter set
   backed up, changed, and restored to the backup's bytes; skipped on an
   account with none). Each runs its command once and lists in its
   docstring what it sends. All five passed against the real account on
   2026-11-01, run one at a time, and left no `mailctl-test-` folder and
   the same filter set active ([#388][i388]).
3. **Container tests** (`MAILCTL_CONTAINER=1`, `tests/container/`) — every
   **write** path against a **throwaway local Dovecot + Pigeonhole** in
   Docker, never a real account. See *The container tier*.
   `test_image.py` sits under the same gate and builds mailctl's **own**
   image instead (*The container tier*).

The live tier and the read-only live check are together this repo's
**end-to-end** pass (`qa.md` dimension 8) against MXroute itself. The
container tier sits between the offline tier and them: a real server, so
it shows what a double cannot (what the server *keeps*, and what a stored
rule *does*), but not MXroute, so it says nothing about MXroute's own
configuration.

## Coverage

**Coverage is not measured by default and gates nothing.** No coverage
tool is configured in `pyproject.toml`, the `Makefile`, or CI, and no
threshold applies, per the global `testing.md`: what must be covered is
named per behaviour in the tier rules. Read it when wanted, with
`pytest-cov` installed by hand: `pytest --cov=mailctl --cov-report=term`.

## Manual verification

Every shipped feature also carries a plain-language note — where to go, what
to do, what success looks like **concretely**, and what failure looks like —
written in the PR as the first draft of the user-facing docs
(`testing.md` › *The manual verification bar*). This matters more than usual
here: a filter that was written but silently does nothing looks identical, at
the terminal, to one that works. "The rule was added" is not a success
criterion; "`mailctl filter list` shows the rule, and a new message matching
it lands in `Lists/GitHub`" is.

## Running

```sh
pytest                 # unit (offline, credential-free)
make test              # the same, via the Makefile
make testlive          # live (MAILCTL_LIVE=1; the account from mailctl's config)
make testlive MAILCTL_LIVE_ENV_FILE=PATH  # the account set by an env file
MAILCTL_LIVE_WRITE=1 make testlive TESTARGS=tests/live/test_live_write_smoke.py
                       # a live test that writes, behind the guards
MAILCTL_LIVE_WRITE=1 make testlive MAILCTL_LIVE_ENV_FILE=PATH \
  TESTARGS=tests/live/test_live_write_paths.py::test_apply_moves_existing_mail
                       # one write path, the account set by an env file
make testcontainer     # write paths against a local Dovecot (needs Docker)
make testcontainer TESTARGS='-k test_image'  # one file, by -k, not a path
make testtui           # the TUI: its pyright, then its own pytest suite
make livecheck         # read-only live check (normal mailctl config)
scripts/live-readonly.sh --list        # the read-only check's test names
scripts/live-readonly.sh list rules    # run only the named checks
scripts/live-readonly.sh --env-file PATH  # an account set by an env file
```

`TESTARGS` passes extra flags through, e.g. a run filter for a scoped live
pass: `make testlive TESTARGS='-k sieve'`.

[i388]: https://github.com/harleypig/mailctl/issues/388
