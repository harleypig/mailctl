---
paths:
  - "tests/container/**"
---

# mailctl Tests — the container tier

Path-scoped detail moved out of `.claude/TESTS.md`, which stays always
loaded as the short core; read the core's *Where the detail lives* for
the whole map. Claude Code loads this file only when a file matching
the `paths:` above is read or edited.

## The container tier

`make testcontainer` builds a small image from `tests/container/image/` and
starts one container for the run; each test then gets its own empty mailbox
on it, so no test sees another's scripts, folders, or mail. It needs a
running Docker daemon and skips cleanly without one, or without
`MAILCTL_CONTAINER=1` exactly — its own gate, never `MAILCTL_LIVE`, so a
container run cannot be mistaken for an MXroute one ([#49][i49]).

**A file in `TESTARGS` does not narrow the run.** The target runs `pytest
-v tests/container $(TESTARGS)`, so a file is added beside the tier and
the whole tier runs; pytest collects the file once. One file runs alone
under `-k`, `make testcontainer TESTARGS='-k test_image'`, or with pytest
called directly, `MAILCTL_CONTAINER=1 .venv/bin/pytest -v
tests/container/test_image.py` ([#387][i387]).

- **What it proves.** `filter add` merging beside a Roundcube-written rule
  (ADR 0002), `filter remove`, `filter move`, `filter disable` then `filter
  enable` (a replaced disabled rule staying disabled), a fresh account's new
  active script, `filterset backup` then `filterset restore` byte for byte,
  `--create-folder` with and without `--no-subscribe` and the `folder
  subscribe` / `folder unsubscribe` toggles, `filter apply` moving, flagging
  and discarding existing mail, `filter apply --keep` copying it as the saved
  rule does, both copies flagged ([#188][i188]), and run again copying nothing
  twice ([#192][i192]), `--max-messages` refusing the whole pass, one filter
  document from `mail search --build-filter --json` driving both `filter add
  --filter` and `filter apply --filter`, `mail view` and the message listing
  leaving mail unread, `mail mark` setting then clearing read, flagged, and a
  keyword and refusing a UID the folder does not hold, and `mail search` and
  `filter apply --dry-run` selecting appended mail by body (a non-ASCII one
  included), arrival date, `--older-than`, and read or flagged state, alone
  and together ([#152][i152]). Its dates are counted from the day it runs, so
  it means the same whenever it does. Reads are here too: `probe --json`
  checked against what the server says about itself, and against printing the
  password; `server probe --report` finding nothing to report on this
  recognised server, and neither `server probe` nor `server test` calling it
  unrecognised ([#39][i39]); `folder list --counts --json` checked against
  each folder's own `STATUS`, leaving every message unread ([#157][i157]); and
  `mail senders` in `test_senders.py` ([#160][i160]), counting each address
  and its unread exactly, grouping by domain and List-Id, refusing above its
  ceiling, and leaving every message as it was. `mail search --sort` runs
  against Dovecot's own `SORT` in `test_search_sort.py` ([#159][i159]).
  UIDVALIDITY in `test_uidvalidity.py` ([#204][i204]): a folder deleted and
  made again gets a new value from Dovecot (asserted, not assumed), `mail
  search --json` reports the server's, and a UID pinned to the old value is
  refused by `mail mark` and `mail view`, leaving the message that now has it
  unread, while a pin to the current value marks it. An IMAP `ALERT` in
  `test_alerts.py` ([#205][i205]): the image's post-login script
  (`image/postlogin.sh`) sends one to a user whose name starts with `alert-`,
  and it reaches stderr, under `--json` too, with stdout untouched; any other
  user is sent nothing, so no other test meets one. A Sieve `WARNINGS` in
  `test_sieve_warnings.py` ([#208][i208]): Pigeonhole warns on `addflag
  "\\Bogus"`, and PUTSCRIPT's warning reaches stderr once. And baselines: one
  saved from the server checks clean against it, saving writes nothing there,
  and drift made by editing the saved file exits 3 or 4 as documented. `filter
  rename` in `test_rename_rule.py` ([#216][i216]): the script's bytes changed
  only in the two name markers, a disabled rule's included, the renamed rule
  still filing new mail and the disabled one still not, and a taken name
  refused with the script untouched. `folder rename` in
  `test_rename_folder.py` ([#5][i5]): the folder and its subfolder moved, both
  subscribed under the new names and gone from `LSUB` under the old, the
  message count kept, and the script's bytes changed only in the two folder
  names. `filter optimize` in `test_optimize_rules.py` ([#21][i21]): the same
  messages handed to `dovecot-lda` before and after a merge and a removal land
  in the same folders, a reorder moves only the starved rule's mail, and a dry
  run stores nothing. New mail is also handed to `dovecot-lda`, which runs the
  uploaded script, so the going-forward half is seen filing it too — by
  header, and by body through a `filter add --body` rule. A filter set
  edited between plan and execute in `test_filterset_changed.py`
  ([#257][i257]): another client stores an edit while `filter add` runs,
  and the upload is refused naming `filterset show`, the edit kept as that
  client stored it, no backup written, and the target folder not made. In
  the same file ([#308][i308]), another client makes a second filter set
  active while `filter add` runs, and the upload is refused naming
  `filterset list`, the switch kept, the planned set's bytes unchanged,
  no backup written, and the target folder not made. The API in
  `test_api_filter_add.py` ([#258][i258]): started as `python -m
  mailctl.api` with the password left out of its settings, so it asks for
  it through `credentials.prompt`, once; a filter added through plan and
  execute sends its events in order (backup, then folder, then upload),
  the backup holds the script's exact bytes, the stored script keeps the
  Roundcube rule beside the new one, new mail is filed by it on delivery,
  and the password is on no line of stdout and not on stderr.
- **The oracle is not mailctl.** Each test reads the server back with
  sievelib's and IMAPClient's own clients, and a byte-exact claim with the
  script file on the container's disk, so a write that mailctl both gets
  wrong and reads back wrong still fails. Scripts are seeded through
  `live_write.SievelibClient`, sievelib's `Client` with only its literal
  framing replaced, because under sievelib 1.6 the plain `Client` turns
  every LF into CRLF on PUTSCRIPT and the tier would stop testing an LF
  script at all ([#232][i232]); the live tier's script guard restores
  through it for the same reason. Every test's docstring names the
  break that turns it red, and each was seen red under that break.
- **mailctl's own image** (`test_image.py`, [#230][i230]). It builds the
  top-level `Dockerfile` and runs the image as a user would, with no
  network and no Dovecot: every refusal comes before a connection. A
  backup directory or `filterset backup --output` in `/dev/shm` is refused
  from every source, a path through the mount cannot lead out, the run
  cannot lift `MAILCTL_BACKUP_ROOT` with `-e` or `--entrypoint`, a run as
  root or without the config mount is refused, and `--help` and
  `--version` need no mount. The image's `api` and `tui` ([#347][i347])
  are held to the same two refusals: `api` and `api help`, `tui` and
  `tui --version`, a help word after `--`, and one beside other
  arguments for either, since each starts what it is given. `tui`
  without a terminal is refused, and its `--api`, in every spelling its
  parser takes, with the control that after `--` the word is handed on
  rather than refused. `api` answers `initialize` over `docker run -i`
  and is held to the root under `-e MAILCTL_BACKUP_ROOT=/`; `tui` under
  a pty reaches the container's own API, as far as its password prompt;
  and `api --version`, `api -h`, and `tui --help` need no mount.
- **Where the live tier's write guards are proved.** The mail store is
  disposable, so this tier needs no guard of its own; instead
  `test_live_write_guards.py` runs the live tier's guards against it
  through the same `Mailbox` type, as context managers and as fixtures in
  a child pytest run that fails, is interrupted, or passes
  ([#9](https://github.com/harleypig/mailctl/issues/9)). The live write
  tests themselves run here too: `test_live_writes_on_dovecot.py` loads
  every `tests/live/test_live_write_*.py` by path and runs its tests
  unchanged ([#388][i388]), on an account seeded with a Roundcube-shaped
  filter set and named by a `0600` env file through
  `MAILCTL_LIVE_ENV_FILE`, as an operator's run names it. After each one
  it checks with its own oracle that the filter set's bytes on disk, the
  active set, and the folder list are as they were, and that the password
  reached no output.
- **The server.** Debian trixie's own `dovecot-*` packages — **Dovecot 2.4.1
  with Pigeonhole** — rather than the `dovecot/docker` image, whose packaging
  is CC BY-NC-SA 4.0. Debian was the first choice on [#49][i49] and it ships
  2.4, the side of MXroute's 2.3 → 2.4 migration worth testing; Ubuntu 24.04
  and 25.04 ship 2.3.21, and Alpine ships 2.4.5.
- **Shaped after the MXroute record, and where it differs.** Maildir++, a
  `.` separator, an empty personal-namespace prefix (as read on
  2026-09-28), `PLAIN` only, STARTTLS on 4190 and 143, implicit TLS on 993,
  and `enotify` turned off, which leaves 23 Sieve extensions. It differs
  in these ways: Sieve lacks `editheader`, the one extension of the 24 the
  MXroute probe listed on 2026-09-29 that it does not advertise; IMAP lacks
  `METADATA` and `QUOTA` (41 capabilities, not 43); the script is named
  `mailctl` on a fresh account rather than Roundcube's `managesieve` (tests
  seed `managesieve` where it matters); no `INBOX.spam`, `Junk`, or other
  default folders exist; any user name logs in with the run's password;
  a user named `alert-*` is sent an `ALERT` at login, where whether
  MXroute ever sends one is unknown; and `redirect` is **not** refused by
  the server, which MXroute's is (mailctl refuses it itself). IMAP runs on
  implicit TLS only: mailctl treats only port 143 as STARTTLS, and
  Docker's port is not 143.
- **The certificate and the password.** The container makes a fresh
  self-signed certificate at start (`localhost` / `127.0.0.1`), and only
  the certificate is copied out, for `SSL_CERT_FILE`. The password is
  random per run, in a `0600` file mailctl reads through
  `MAILCTL_PASSWORD_FILE`, handed to the container on `docker exec` stdin,
  and never printed or put on a command line.
- **In CI, and not a required check** ([#452][i452]).
  `.github/workflows/container-tests.yml` (*Container tests*) runs `make
  testcontainer` on a pull request touching `tests/container/**`,
  `tests/live/**`, `tests/live_write.py`, `mailctl/components/**`,
  `mailctl/providers/**`, or the workflow itself; weekly; and on manual
  dispatch. `test_image.py` runs with the rest: it opens its own pty, so
  the runner needs no terminal. A step fails the job where Docker does
  not answer, so a runner without a daemon cannot pass by skipping the
  tier. It is not required because building the test image fetches
  Debian packages, and a mirror outage must not block a merge. Its first
  run, on 2026-11-03, took 1 min 38 s, 137 passed and none skipped.
  Making it required, or caching the image so a run does not depend on
  the mirror, waits on how long it takes and how often it fails with
  nothing wrong in the code.

[i49]: https://github.com/harleypig/mailctl/issues/49
[i452]: https://github.com/harleypig/mailctl/issues/452
[i387]: https://github.com/harleypig/mailctl/issues/387
[i188]: https://github.com/harleypig/mailctl/issues/188
[i192]: https://github.com/harleypig/mailctl/issues/192
[i152]: https://github.com/harleypig/mailctl/issues/152
[i39]: https://github.com/harleypig/mailctl/issues/39
[i157]: https://github.com/harleypig/mailctl/issues/157
[i160]: https://github.com/harleypig/mailctl/issues/160
[i159]: https://github.com/harleypig/mailctl/issues/159
[i204]: https://github.com/harleypig/mailctl/issues/204
[i205]: https://github.com/harleypig/mailctl/issues/205
[i208]: https://github.com/harleypig/mailctl/issues/208
[i216]: https://github.com/harleypig/mailctl/issues/216
[i5]: https://github.com/harleypig/mailctl/issues/5
[i21]: https://github.com/harleypig/mailctl/issues/21
[i257]: https://github.com/harleypig/mailctl/issues/257
[i308]: https://github.com/harleypig/mailctl/issues/308
[i258]: https://github.com/harleypig/mailctl/issues/258
[i232]: https://github.com/harleypig/mailctl/issues/232
[i230]: https://github.com/harleypig/mailctl/issues/230
[i347]: https://github.com/harleypig/mailctl/issues/347
[i388]: https://github.com/harleypig/mailctl/issues/388
