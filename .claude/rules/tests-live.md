---
paths:
  - "tests/live/**"
  - "tests/live_write.py"
  - "tests/container/test_live_*.py"
  - "scripts/live-readonly.sh"
---

# mailctl Tests — the live tier

Path-scoped detail moved out of `.claude/TESTS.md`, which stays always
loaded as the short core; read the core's *Where the detail lives* for
the whole map. Claude Code loads this file only when a file matching
the `paths:` above is read or edited.

## The read-only live check

`scripts/live-readonly.sh`, run by `make livecheck`, is a bash script that
runs read-only CLI commands and `--dry-run` plans against the real account,
then verifies that nothing changed. It reports in TAP. Name tests as
arguments to run only those; `--list` shows the names.

It is **not** the pytest live tier above, and the difference is the point:

- **It never writes.** `tests/live/` writes only behind the write guards and
  a second opt-in ([#9](https://github.com/harleypig/mailctl/issues/9));
  this one never does, so it needs neither and is safe to run any time.
- **It drives the CLI end to end**, as a user or an automation script would —
  the installed `mailctl` command, not the package from inside Python. The
  CLI is the automation surface (CONVENTIONS.md › *The core returns data;
  only the interfaces print*), so this is the check that its commands work
  against a real server.
- **It uses the normal mailctl config** — the same settings, env file, and
  password source the user runs with, not a test-only set. An env file is
  named with `--env-file PATH`, which the script hands to every mailctl
  call as `--env-file PATH`, so mailctl reads it as it always does (the
  mode check, `Secret`, never exported); the script never reads or exports
  it. Through make it rides in `TESTARGS`:
  `make livecheck TESTARGS='--env-file PATH reread-bytes'`. A path that
  does not exist is refused, exit 2, before any test runs ([#325][i325]).
- **It paces itself.** The account is real, so there are no loops over the
  server and there is a pause between tests.

**Every future read-only live check goes here** (operator, 2026-09-28). A
check that only reads, or only plans with `--dry-run`, is added to this
script, not to `tests/live/`. `search-unread` is one: a single `search
--unread --since <30 days ago> --limit 5`, checking the date and state
filters reach MXroute and every row it lists is unread ([#152][i152]). It
does not check each row's date, since a message near midnight can show on
either side of the server's. `filter optimize` is another: one
`filter optimize --dry-run --json`, checking the plan is well formed, has a
diff exactly when it proposes a change, and uploaded nothing
([#21][i21]). `uidvalidity` is a third: the newest UID and its
UIDVALIDITY from one `mail search --json`, a `mail view` pinned to that value,
and a `mail view` pinned to another, which must be refused ([#204][i204]).
`reread-bytes` is a fourth: the active filter set read twice by `filterset
backup --output`, a pause apart, into the run's own temp directory, and the
two files compared byte for byte; any difference fails it ([#299][i299]). It
is skipped where `MAILCTL_BACKUP_ROOT` keeps a backup out of that directory.
Its first run against the real account is [#298][i298].

## Live-test credentials & safety

Live tests touch a real mailbox, so the guards are not optional:

- **Settings are read as mailctl reads them** — the gate and
  `live_config` both resolve the account through `load_config`, so the
  `MAILCTL_*` variables, `config.toml`, and an env file named by
  `MAILCTL_LIVE_ENV_FILE` all count, as `--env-file` would for any
  command ([#380][i380]). That file is read by mailctl's own loader: one
  setting `MAILCTL_PASSWORD` is refused, coded `env_file_mode`, where
  group or other can read it; the password is wrapped in `Secret`; and
  nothing is exported, so no `set -a` is needed. A named file that
  cannot be read skips the run rather than falling back to the
  environment. Only a refusal from the loader is a skip, its code in the
  reason; any other failure working out the gate errors the test in
  setup, never a quiet skip (`test_live_gate.py`, [#403][i403]). The
  password is preferred via `MAILCTL_PASSWORD_FILE` or
  `MAILCTL_PASSWORD_CMD` over `MAILCTL_PASSWORD`; the gate accepts any
  of the three, testing only that one is set, never opening or running
  it (`test_live_gate.py`, [#369][i369]). `MAILCTL_LIVE=1` is required,
  and it and `MAILCTL_LIVE_WRITE` are read from the environment only, so
  a plain `pytest` can never touch the account.
- **The password stays out of every artifact.** A live test's output, a
  captured log, and a failure traceback are all places a credential could
  surface — `Secret` is what prevents it, so a live test must never unwrap a
  password to build a fixture or a diagnostic. See CONVENTIONS.md ›
  *Credentials*.
- **Writing is a second opt-in.** A test that writes uses the fixtures
  below, which reach the account through `write_mailbox`; it skips unless
  `MAILCTL_LIVE_WRITE` is exactly `1` as well (pinned offline by
  `test_live_write_gate.py`). `make testlive` alone reads. Every test in
  a `tests/live/test_live_write_*.py` file asks for one of the two
  fixtures, and each fixture asks for `write_mailbox`
  (`test_live_gate.py`, [#388][i388]).
- **mailctl runs through `run_mailctl`** (`tests/live_write.py`), which
  hands it the env file `MAILCTL_LIVE_ENV_FILE` names as `--env-file`. A
  bare `cli.main` would not see that file, since it is never exported,
  and would run against whatever the environment alone describes.
- **Back up and restore every script — `guarded_scripts`.** It captures
  each script's exact bytes and which is active, saves them to a `0600`
  file, and afterwards puts back only what changed, deletes what the test
  created, and confirms the result through two readers. A restore it
  cannot confirm fails the test's teardown, naming the saved copy. It runs
  after a failure and after Ctrl-C.
- **Never touch INBOX — `scratch_folder`.** A new `mailctl-test-…` folder
  in the personal namespace, deleted with its contents afterwards; INBOX,
  any folder that already exists, and any unmarked name are refused before
  anything is written. Append the test's own messages there
  (`scratch_folder.append`).
- **Few writes, never looped.** The guards write nothing when nothing
  changed. `tests/live_write.py` holds both guards and says what each
  sends.
- **Assume nothing about the server's configuration.** The live tier is
  precisely where *Discover, don't hardcode* (CONVENTIONS.md) gets exercised —
  a test that hardcodes the delimiter, the script name, or `INBOX.spam` is
  testing our assumption rather than the server.

[i325]: https://github.com/harleypig/mailctl/issues/325
[i152]: https://github.com/harleypig/mailctl/issues/152
[i21]: https://github.com/harleypig/mailctl/issues/21
[i204]: https://github.com/harleypig/mailctl/issues/204
[i299]: https://github.com/harleypig/mailctl/issues/299
[i298]: https://github.com/harleypig/mailctl/issues/298
[i380]: https://github.com/harleypig/mailctl/issues/380
[i403]: https://github.com/harleypig/mailctl/issues/403
[i369]: https://github.com/harleypig/mailctl/issues/369
[i388]: https://github.com/harleypig/mailctl/issues/388
