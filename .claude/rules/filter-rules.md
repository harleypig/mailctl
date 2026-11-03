---
paths:
  - "mailctl/utilities/**"
  - "mailctl/providers/**"
  - "mailctl/components/managesieve/**"
  - "mailctl/rules.py"
  - "mailctl/criteria.py"
  - "mailctl/config.py"
---

# mailctl Conventions — filter rules

Path-scoped detail moved out of `.claude/CONVENTIONS.md`, which stays always
loaded as the short core; read the core's *Where the detail lives* for
the whole map. Claude Code loads this file only when a file matching
the `paths:` above is read or edited.

## Rule conventions

- **Merge, never overwrite.** A new rule is merged into the parsed existing
  script; rules the tool did not write survive untouched. Roundcube's filter
  UI writes the same script, so overwriting silently destroys a user's
  hand-made filters. A parse failure is a **hard stop**, never a
  fall-back-to-overwrite. See [ADR 0002][adr2].
- **Back up before every upload.** The previous script is written to the
  backup directory before the new one is sent, and `mailctl filterset backup`
  takes the same copy on demand. **One location, and it is the config
  directory** — `$XDG_CONFIG_HOME/mailctl/backups`, beside `config.toml`
  (`config.default_backup_dir`), overridable by `--backup-dir` /
  `MAILCTL_BACKUP_DIR`. XDG would call a backup *state*; co-locating it with
  the config is a deliberate departure from XDG, not an XDG-endorsed reading,
  and the reason is that a backup the user cannot find is not a backup. Two
  defaults for one kind of file is how somebody ends up looking in the
  directory that does not have their backup in it. **Where
  `MAILCTL_BACKUP_ROOT` is set, every backup must resolve inside it** —
  the Docker image sets `/config/mailctl` — whichever chose the location:
  `--backup-dir`, `MAILCTL_BACKUP_DIR`, `backup_dir` in `config.toml`, or
  `filterset backup --output`. Paths are resolved first, symlinks included,
  so neither `..` nor a link leads out ([#230][i230]).
- **An upload is refused if the filter set changed since the plan**
  ([#257][i257]). Before the backup, `upload_script` reads the server's
  copy again, and on any byte difference from what the plan was made from
  it sends nothing and refuses, coded `filterset_changed`; what it read is
  what it backs up. Otherwise an edit made in between — the webmail's
  filter UI, another run, a second client — would be overwritten and
  missing from the backup, which is ADR 0002's failure by another road.
  Every execute that uploads goes through it, `filterset restore` and
  `folder rename` included, and a byte compare needs no parse, so a
  restore over an unparseable script ([ADR 0005][adr5]) is held to it too.
  A `folder rename` with no filter to repoint uploads nothing, and still
  reads the filter set and which one is active again before the folder
  moves, refusing the same way before any write ([#332][i332]): a filter
  filing into the old name, added since, would be left pointing at a
  folder that no longer exists.
  **So is an upload once the active filter set is not the one the plan
  was made against** ([#308][i308]): the same code, nothing backed up or
  sent, and the refusal points at `filterset list` rather than at one
  set's text. The plan decided from the active set whether the upload
  activates its own, so a switch made since would be undone, or an edit
  planned as not live would go live, and the plan shown said neither.
- **A backup is the server's exact bytes.** `SieveSession` reads GETSCRIPT
  by the literal's declared length, CRLF and final newline included
  ([#90][i90]), and `write_backup` writes what it was handed, with newline
  translation off, mode `0600` in a directory
  created `0700`. **A backup never replaces a file** ([#330][i330]). It
  is named `<set>-<YYYYmmddTHHMMSS.mmmZ>.sieve`, UTC to the millisecond,
  and created exclusively. Where mailctl chose the name, a clash takes the
  next free one, `<stem>-2.sieve`, `-3`, and on; where the user named the
  file (`filterset backup --output FILE`), a clash is refused and the file
  kept, at the plan too, so `--dry-run` never promises a write the run
  refuses ([#331][i331]). A baseline is the exception: refreshing one is
  an explicit command, so it replaces its file. Nothing decorates a backup —
  `mailctl filterset show` adds banner lines for a reader and is therefore
  *not* a backup, which is exactly the
  trap redirecting `filterset show` to a file used to set. `mailctl filterset
  restore` puts one back over the active script only: it shows a raw diff,
  asks for confirmation, backs up the current script, and runs CHECKSCRIPT
  before sending. It is the one write path that replaces instead of merging,
  and it may replace a script mailctl cannot parse ([ADR 0005][adr5],
  [#13][i13]). It refuses, unopened, a file this run reads the password
  from — a password file from any rung, or the env file, links followed —
  coded `credential_file`, since its contents would come back as the diff
  (*Credentials*).
- **Show, then change.** Every mutating subcommand works out what would
  change, shows it (a diff for the script, a preview for the messages), and
  only then applies it. `--dry-run` stops after the "show it" step.
- **Refuse the actions we will not emit, with a pointer** — and keep the two
  reasons for refusing apart, because the distinction is exactly the
  confidence tiering above:
  - `MXROUTE_FORBIDDEN_ACTIONS` (`providers/mxroute/sieve.py`) holds
    **`redirect` alone**. It is the only action refused because MXroute is
    *confirmed* to disable it, and its message points at forwarders (the
    panel, or the `mxroute_forwarder` Terraform resource).
  - `UNIMPLEMENTED_ACTIONS` (`components/managesieve/script.py`) holds
    **`notify` and `vacation`**. These are refused because *we* do not
    generate them, and their message says so explicitly rather than implying
    an MXroute restriction — no source confirms or refutes their
    availability.

  Collapsing the two would restate an unverified assumption as a server fact,
  which is the failure this repo's *Confidence* discipline exists to prevent.
- **Supported and used:** `fileinto`, `discard`, `stop`, `keep`, and flag
  actions.
- **Every emitted feature declares its extension, in one table.**
  `EMIT_TABLE` (`components/managesieve/emit.py`) maps each command, test,
  and tag a rule can contain to
  the Sieve extension it needs (None for the base language). The required
  set `mailctl server test` reports and the check a rule is held to are both
  read off it, and `tests/test_extensions.py` checks it against sievelib's own
  `require` line over every rule shape — so a new action is added to the
  table, never listed by hand anywhere else.
- **`disabled_extensions` narrows what we emit; it never widens it.** A
  disabled extension counts as not advertised, in plan and in execute: a
  rule that needs it is refused naming the setting, and a fallback is used
  where one exists (`mailbox` off → plain `fileinto`, folder created over
  IMAP). It governs what mailctl writes, not rules already in the script.
- **One criteria model, two translations.** A new matching capability is added
  to `criteria.py` and translated to *both* Sieve and IMAP `SEARCH` — never to
  one side only. Where IMAP `SEARCH` is coarser than the Sieve comparator, the
  results are re-checked client-side against the real Sieve semantics so the
  retroactive pass matches what the filter will do going forward.
- **A further Sieve extension is adopted only when it passes three tests**
  ([#17][i17]), so each candidate is judged against these rather than
  argued from scratch:
  1. **The retroactive pass can reproduce it.** The IMAP half must be able
     to apply the same effect to mail already delivered. An extension with
     no IMAP equivalent makes the two halves disagree, which is the failure
     this tool exists to prevent — that alone is grounds to decline.
  2. **It is registered in `EMIT_TABLE` with a decided absence path.** The
     extension it needs is declared there like every other emitted feature,
     and the rule says what happens on a server that does not advertise it:
     a named fallback, or a refusal before anything is written. Never an
     unconditional `require` that fails on the server.
  3. **The provider's own panel does not already do it better.** Duplicating
     the webmail badly is worse than not doing it; this is why `vacation`
     (autoresponders) stays refused although the server may advertise it.

[adr2]: ../../adr/0002-non-destructive-script-merge.md
[i230]: https://github.com/harleypig/mailctl/issues/230
[i257]: https://github.com/harleypig/mailctl/issues/257
[adr5]: ../../adr/0005-restore-may-replace-an-unparseable-script.md
[i332]: https://github.com/harleypig/mailctl/issues/332
[i308]: https://github.com/harleypig/mailctl/issues/308
[i90]: https://github.com/harleypig/mailctl/issues/90
[i330]: https://github.com/harleypig/mailctl/issues/330
[i331]: https://github.com/harleypig/mailctl/issues/331
[i13]: https://github.com/harleypig/mailctl/issues/13
[i17]: https://github.com/harleypig/mailctl/issues/17
