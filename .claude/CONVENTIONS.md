# mailctl Conventions

Repo-specific conventions. The global `~/.claude/` config carries everything
generic (git/gh, code style, the Python toolchain via `python.md` +
`ruff.md`, the QA dimensions). This file records only what is specific to
**this** repo.

## What this is

`mailctl` — an MIT-licensed Python CLI that manages MXroute email filters end
to end. It does two things, and the second is the reason it exists:

1. **Creates server-side Sieve filters over ManageSieve**, merging the new
   rule **non-destructively** into the account's existing active script (see
   *Rule conventions* below and [ADR 0002][adr2]).
2. **Applies the same rule retroactively over IMAP** to mail already sitting
   in the mailbox — because Sieve only ever runs on **new incoming** mail.
   Writing the Sieve rule alone leaves every message already delivered exactly
   where it was.

Distribution name and package are both `mailctl`; the console entry point is
`mailctl = "mailctl.cli:main"`. It is **not published anywhere** — see
[RELEASING.md](../RELEASING.md).

### The old name

**The tool was `mxfilter` until #45, and the rename is a clean break.** The
old config directory (`$XDG_CONFIG_HOME/mxfilter/`) and the old `MXROUTE_*`
setting names are **not read** — no dual-reading period, no fallback. What
stops the break stranding anybody silently is detection plus a switch, not
compatibility:

- **The old directory is a finding.** While it exists and the new one does
  not, `engine.check_config_dir` returns `ConfigDirPending` and the CLI
  warns loudly on stderr before every command, naming `mailctl
  migrate-config`. That command is the switch the scoping rule asks for: a
  plan/execute pair in the engine (`plan_config_migration` /
  `execute_config_migration`) that lists what moves, honours `--dry-run` and
  `--yes`, moves by rename so every file keeps its mode, merges into a new
  directory that already exists, refuses outright if anything at the
  destination would be overwritten, points a `config.toml` `password_file` /
  `backup_dir` that named the old directory at the new one, and removes the
  old directory once empty.
- **An old setting name is a finding, by name only.**
  `config.LEGACY_ENV_NAMES` is the table of every old name and its new one.
  An old name present — in the environment or the env file — without its new
  one comes back as a `LegacySetting` (old, new, where), and the CLI warns
  with exactly that. **No value is read, compared, printed, or kept:**
  presence is tested with `in`, and the env-file parser records the old key
  and drops its value, because one of these is the password. An old
  `MXROUTE_PASSWORD` is therefore also never held to the env-file mode
  check — it is never used.
- **The script created under the old name is still ours.** A new account
  gets a script called `mailctl` (`engine.DEFAULT_SCRIPT_NAME`); with nothing
  active, an existing `mxfilter` script (`engine.LEGACY_SCRIPT_NAME`) is
  reused rather than a second one created beside it.
- **`MXROUTE_FORBIDDEN_ACTIONS` keeps its vendor name.** It is a fact about
  MXroute, not a setting of this tool (*Rule conventions*).

### The scoping rule

Stated by the operator, 2026-08-14, and recorded because it settles scope
questions rather than merely describing the tool:

> The general purpose of this app is to create ways of managing filters and
> settings, either by switch or automation.

Read it as a **test to apply**, not a mission statement. "Is this in scope?"
becomes *is it a filter or a setting, and is there no way to manage it here?*
— and if so, the answer is yes, without needing a decision.

Two words carry the weight:

- **Settings, not just filters.** A folder's subscription state, a rule's
  position, a capability the account has — anything the account holds that a
  user might want to change is in scope. The two halves in *What this is*
  above are what the tool was **built** for; they are not its boundary.
- **By switch or automation.** Both, and neither is the poor relation. A
  thing you can only do by hand and a thing you can only get as a side effect
  of something else are each half-built.

**What this rules out** matters as much: reading a setting, warning about it,
and telling the user to go fix it somewhere else. That is the shape a
management tool should not end on — it is a report wearing a command's
clothes.

**Finding and viewing messages is in scope** — listing the messages a set of
criteria matches, with their UIDs, and showing one message's headers, text,
and attachment names. Apply the test to it: a filter is built from what the
mail says, so seeing the mail is part of managing filters, and a tool that
cannot show it sends the user to another client to learn what to match on.
The operator, 2026-09-27, wanting it in every interface: *"I want the same
functionality for all interfaces"* — see *Every command is in every
interface* below.

It cuts the other way too. Scope is not *everything about email*: composing
and sending, an address book, account provisioning, and saving or opening
attachments are not filters or settings on this account, and the sibling
[terraform-provider-mxroute][provider] owns account state as code (see *The
sibling repository* below). Viewing a message stops at what building a filter
needs: an attachment is named, never saved or opened.

**A worked example**, so this reads as a rule rather than a slogan. Four
observations came out of the subscription work
([#38](https://github.com/harleypig/mailctl/issues/38)) and
were surfaced as open questions: no CLI way to subscribe an existing folder;
`folders` and `test` silent about subscription; an unsubscribed folder left
alone; a flag with no effect. Applying the rule, three are not questions at
all — subscription is a setting, so exposing it is in scope by definition
([#42](https://github.com/harleypig/mailctl/issues/42)) — and
the fourth is a plain bug
([#43](https://github.com/harleypig/mailctl/issues/43)).
Asking was the error.

Built on two libraries, both of which the code wraps rather than exposes:

- **`sievelib` (≥ 1.5.0, < 2)** — the ManageSieve client (`sievelib.managesieve`),
  the Sieve parser, and the `factory.FiltersSet` script builder.
- **`IMAPClient` (≥ 4.1, < 5)** — the IMAP half.

## Layout

- `mailctl/__init__.py` — the package docstring, `__version__`, and
  `MailctlError` (the one exception type every actionable failure raises).
- `mailctl/config.py` — endpoint and credential resolution, and the `Secret`
  wrapper (see *Credentials* below).
- `mailctl/criteria.py` — the shared criteria model, translated **both** to
  Sieve tests and to IMAP `SEARCH`. One model, two backends — this is what
  keeps the two halves in agreement.
- `mailctl/components/` — **layer 1** ([ADR 0006][adr6]): one library per
  protocol, knowing nothing about any host. It imports only the stdlib, the
  library it wraps, other components, and `MailctlError` — never `config`,
  the engine, the CLI, or a provider (`tests/test_layer_purity.py`).
  - `managesieve/client.py` — `SieveClient`, sievelib's client with ADR
    0006's gaps S1/S4/S5/S8 closed (byte-exact GETSCRIPT, the whole
    CAPABILITY response, a configurable read timeout, debug output
    impossible), and `SieveSession` on it, taking plain connection
    parameters.
  - `managesieve/capabilities.py` — the CAPABILITY response as data.
  - `managesieve/script.py` — the offline script handling (parse / merge /
    move / remove / render / diff), rule names through a `NameDialect`, and
    `UNIMPLEMENTED_ACTIONS`.
  - `managesieve/backup.py` — the backup path and the byte-exact writer.
  - `managesieve/servers/` — one module per server software, chosen by the
    `IMPLEMENTATION` capability, with a plain-protocol fallback;
    `pigeonhole.py` carries no quirks yet.
- `mailctl/providers/mxroute/sieve.py` — **transitional** home of what is
  MXroute's rather than the protocol's: `MXROUTE_FORBIDDEN_ACTIONS`, the
  Roundcube `# rule:[NAME]` dialect and the script functions bound to it,
  the connection and login advice, and `sieve_session()`, which maps
  `Config` onto a `SieveSession`. Epic #92's step 4 turns it into the
  `mxroute` provider.
- `mailctl/imap.py` — the IMAP session wrapper (folders, search, move, flag)
  and folder-name normalization.
- `mailctl/rules.py` — reads a parsed script into a flat rule model and
  reports which rules cannot fire where they are (shadowing, in both
  directions); offline.
- `mailctl/engine.py` — the engine: every piece of work the tool does
  (open sessions, plan the target folder, merge a rule, back up and upload,
  plan and run the existing-mail pass, derive criteria from a message), for
  any front-end. It takes plain values (`ActionSpec`, `RuleRequest`,
  `Criteria`, `Placement`, `Config`) and returns plans and results.
- `mailctl/cli.py` — the CLI front-end: argument parsing, turning flags into
  engine inputs, and rendering and confirming what the engine returns.
- `mailctl/__main__.py` — `python -m mailctl`.
- `tests/` — pytest, mirroring the package layout ([TESTS.md](TESTS.md)).

The split is deliberate: the **offline** logic (criteria translation, Sieve
generation, folder-name normalization, script merging) is exercisable with no
server at all. Keep new logic on the offline side of that line wherever it can
live there.

## The core returns data; only the CLI prints

**`config`, `criteria`, `imap`, `rules`, `components/`, `providers/`, and
the `engine` that drives them return structured values and raise
`MailctlError`. Every piece of rendering, prompting, confirmation, and
progress output lives in `cli.py`.** Two reasons, both cashing out now: the
core stays testable without capturing stdout, and a future front-end can
sit on the same core instead of requiring it to be torn apart first.

**The engine does not know how it was called.** The operator's instruction,
2026-09-27: *"the engine, the code that does the actual work, should not know
nor care how the app was called (cli, tui, gui, web)"*. So it never takes an
`argparse` namespace, never imports the CLI, and never reads the terminal or
the environment; `tests/test_core_no_presentation.py` enforces all three.
Every change is **plan → decide → execute**: a `plan_*` function is
read-only and returns what would change (a diff, placement findings, a
message preview, counts); the front-end renders it and makes the decision
(`--dry-run`, `--yes`, a confirmation prompt); an execute-style call carries
the plan out. New work goes into the engine first; `cli.py` should only gain
parsing and rendering.

**Nothing below the front-end writes to the terminal, progress included.**
`--verbose` protocol chatter leaves `SieveSession` and `ImapSession` through
a `progress` callback, and the steps of a change (backup written, script
uploaded, folder created) leave the engine through an `on_event` callback;
the CLI decides whether and how to show either. Do not add a second output
path beside them.

Safety policy lives in the engine, not the front-end: the backup before every
upload, merge-never-overwrite, and the `--max-messages` ceiling (re-checked
when a mail plan is executed) hold whichever front-end calls it.

**Every command is in every interface.** The operator, 2026-09-27:

> I want the same functionality for all interfaces. I honestly don't remember
> if I just didn't explain myself well, or something got lost along the way.
> There may be some parameters differences between cli, tui, gui, and web but
> the commands should all be available in all environments.

So every operation the engine offers is a command in every front-end — CLI,
TUI, GUI, and web. **Parameters may differ per front-end; availability may
not.** A capability wanted for one front-end is built into the engine and
exposed in all of them; a feature only one front-end has is a gap in the
others, not a design choice. The CLI is the only front-end today, so today
this means the CLI exposes every engine operation.

## The protocols

There is no vendor API here — the tool speaks two standard protocols.

- **ManageSieve** — RFC 5804. IANA reserved **TCP 4190** for it, and the RFC
  requires both ends to implement **STARTTLS** (*"Client and server
  implementations MUST implement the STARTTLS extension"*). That is the
  tool's default, and it is a **default, not a verified fact about MXroute**
  — see *Confidence* below.
- **IMAP** — port **993** (implicit TLS) or **143** (STARTTLS). The username
  is the **full email address**, and the hostname is **per-account**
  (MXroute's panel gives it as "the same as your primary MX record"), so it is
  always configuration and never a built-in default.

Every setting resolves highest-priority-first: a CLI flag → a `MAILCTL_*`
line in the `--env-file` file → a `MAILCTL_*` environment variable → the
TOML config file (`$XDG_CONFIG_HOME/mailctl/config.toml`) → a built-in
default. The env file is read, never exported: `load_config` takes the
environment as a mapping and layers the file over it, so `os.environ` is
never written. Where each setting came from is recorded on the resolved
`Config` (`sources`, `consulted`) as data, so a front-end can report it and
an error message can say "default" only about a default. The password is the
exception and is handled separately (*Credentials*).

### Confidence — documented, observed, and unknown

MXroute documents very little of its Sieve surface, and the gaps are
themselves a design driver (they are why *Discover, don't hardcode* below is a
rule rather than a preference). Mark these honestly; do **not** quietly
promote one tier to another.

**Confirmed:**

- **Sieve `redirect` is disabled.** MXroute's own blog says so — *"Why we
  disabled redirect sieve filters on MXroute"* (2024-03-21) — and gives the
  reason: real forwarders "are designed to properly handle SRS".
- **IMAP 993 / 143, full-email-address username, per-account hostname** (the
  panel's *Email Clients* page).
- **The REST API exposes nothing for filters or Sieve.** Verified twice, two
  ways: against the published OpenAPI document (26 paths, none filter-related)
  and against MXroute's own 4.0.1 changelog, which enumerates the API's
  categories in full — Domains, Email Accounts, Forwarders, Spam Settings,
  Catch-All, DNS Information, Quota, Reseller. This is the load-bearing fact
  behind [ADR 0001][adr1].
  - **Wording trap:** that changelog describes *"Forwarders — set up and
    manage email forwarding **rules**"*. "Rules" there means **forwarders**,
    not Sieve rules. Do not read it as filter support and re-open the
    question.

**Observed on one account — `mailctl test`, 2026-08-14:**

A live read against a single MXroute server settled several of these. It is a
**separate tier on purpose**: an observation is stronger than a guess and
weaker than documentation, and it describes *that server* rather than MXroute.

| Observed | Value |
|---|---|
| ManageSieve port + TLS | **4190 + STARTTLS works** — the protocol default was right |
| Folder delimiter | **`.`** — Maildir++, as the blog-post path suggested |
| Spam folder | **`INBOX.spam` exists** |
| Active script name | **`managesieve`** |
| `vacation` | **advertised** |
| `enotify` | **not advertised** |
| `FILTER=SIEVE` | **not advertised** — no server-side retroactive filtering |
| `spamtest`, `extlists` | **not advertised** |
| `regex`, `mailbox`, `imap4flags`, `copy`, `envelope` | **advertised** |

**This does not license hardcoding any of it**, and the reason matters: the
tool discovers these at runtime not because we were unsure what *this* account
reports, but because MXroute is mid-migration on both its panel and Dovecot
(*Discover, don't hardcode*, below). An observation dated today says nothing
about the same server next quarter, and nothing about anyone else's server.
The script name in particular is a per-server configuration value
(`managesieve_script_name`), so it is the **least** generalizable item here.

**`vacation` being advertised changes the status of our refusal.** mailctl
still refuses both `vacation` and `notify`, but they are no longer refusals of
the same kind: `enotify` is **not advertised** on this server, while
`vacation` **is** — so declining to emit it is a **deliberate choice of ours**
(the control panel does autoresponders, and a Sieve autoresponder has real
footguns), where declining an action the server never advertised is barely a
choice at all.

The shared refusal message is still correct for both and needs no tailoring:
it says the refusal is ours rather than a documented MXroute restriction, and
points at `mailctl test` to find out what this server actually advertises.
Distinguishing the two in the message would bake a per-server observation into
a string, which is precisely what *Discover, don't hardcode* exists to
prevent.

Half the trigger on the `vacation` icebox entry has therefore fired; see
[ICEBOX.md](../ICEBOX.md).

**Unconfirmed — say so plainly rather than filling the gap:**

- **Whether any of the observations above generalize.** MXroute documents
  neither the ManageSieve port, the TLS mode, the delimiter, nor the extension
  set. Every one of those rows is one server on one day.
- **ManageSieve script-size, script-count, and rate limits.** Nothing in the
  `CAPABILITY` response speaks to these, so a live read cannot settle them.

### Discover, don't hardcode

**Server capabilities, the active script name, and the folder delimiter are
discovered from the server at runtime — never hardcoded.** This is a named
convention, not a style preference, and the reason is concrete rather than
abstract: MXroute has publicly stated it intends to migrate away from
DirectAdmin, Crossbox, and **Roundcube** this year, and is mid-migration from
**Dovecot 2.3 to 2.4**. Runtime discovery survives both migrations; a baked-in
constant does not.

In practice:

- Take the active script from the server's own listing, not a constant.
- Take the folder delimiter from the server's folder list, and normalize
  user-supplied names against it — so `Lists/GitHub` and `INBOX.Lists.GitHub`
  name the same folder (`imap.normalize_folder`).
- Read the advertised Sieve extensions rather than assuming a capability is
  present.

A default is fine where the protocol supplies one (port 4190); an **assumption
about MXroute's configuration** is not.

## Rule conventions

- **Merge, never overwrite.** A new rule is merged into the parsed existing
  script; rules the tool did not write survive untouched. Roundcube's filter
  UI writes the same script, so overwriting silently destroys a user's
  hand-made filters. A parse failure is a **hard stop**, never a
  fall-back-to-overwrite. See [ADR 0002][adr2].
- **Back up before every upload.** The previous script is written to the
  backup directory before the new one is sent, and `mailctl backup` takes the
  same copy on demand. **One location, and it is the config directory** —
  `$XDG_CONFIG_HOME/mailctl/backups`, beside `config.toml`
  (`config.default_backup_dir`), overridable by `--backup-dir` /
  `MAILCTL_BACKUP_DIR`. XDG would call a backup *state*; co-locating it with
  the config is a deliberate departure from XDG, not an XDG-endorsed reading,
  and the reason is that a backup the user cannot find is not a backup. Two
  defaults for one kind of file is how somebody ends up looking in the
  directory that does not have their backup in it.
- **A backup is the server's exact bytes.** `SieveSession` reads GETSCRIPT
  by the literal's declared length, CRLF and final newline included
  ([#90][i90]), and `write_backup` writes what it was handed, with newline
  translation off, mode `0600` in a directory
  created `0700`. Nothing decorates it — `mailctl show` adds banner lines for
  a reader and is therefore *not* a backup, which is exactly the trap
  redirecting `show` to a file used to set. `mailctl restore` puts one back
  over the active script only: it shows a raw diff, asks for confirmation,
  backs up the current script, and runs CHECKSCRIPT before sending. It is
  the one write path that replaces instead of merging, and it may replace a
  script mailctl cannot parse ([ADR 0005][adr5], [#13][i13]).
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
  `engine.EMIT_TABLE` maps each command, test, and tag a rule can contain to
  the Sieve extension it needs (None for the base language). The required
  set `mailctl test` reports and the check a rule is held to are both read
  off it, and `tests/test_extensions.py` checks it against sievelib's own
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

## Credentials

**The mailbox password never reaches stdout, stderr, a log, or a transcript.**
This is the hard boundary of this repo, the analogue of a write-only secret,
and it is enforced by construction rather than by care:

- `config.Secret` wraps the password and overrides **both** `__str__` and
  `__repr__` to `<redacted>`, which covers every accidental disclosure path —
  `print`, an f-string, `%s` in a log line, and a `repr` in a traceback frame.
- The real value is reachable **only** through `Secret.reveal()`, which is
  greppable and therefore reviewable. Call it only when handing the password
  to a connection method — never to display, log, or format it.
- The password resolves through its own ladder, highest first: **an explicit
  flag** (`--password-file`, `--password-cmd`, `--password` — argparse makes
  them mutually exclusive) → the `--env-file` file's `MAILCTL_PASSWORD_FILE`
  → `MAILCTL_PASSWORD_CMD` → `MAILCTL_PASSWORD` → the same three from the
  environment → `password_file` → `password_cmd` (config file) → an
  interactive `getpass` prompt. Two rules produce that order, and both are
  load-bearing:
  - **A flag beats an ambient variable.** It was typed for *this* run; the
    variable merely happens to be exported. The inverse — which is what the
    code did until the ladder was fixed — silently authenticates as the
    wrong account when `MAILCTL_PASSWORD` is exported for one mailbox and
    `--password-cmd` names another. An env file was named for this run too,
    so **all three** of its rungs sit above all three ambient ones rather
    than interleaving by variable — otherwise an exported
    `MAILCTL_PASSWORD_FILE` for one mailbox would beat the file's
    `MAILCTL_PASSWORD` for another.
  - **A literal value never beats an instruction about where to fetch one.**
- The **literal password is never read from the TOML config file** — only
  `password_file` and `password_cmd` are. That is unchanged.
- **A password file is refused, not warned about, when its mode lets anyone
  else read it.** Any bit in `0o077` is a refusal naming the path, the mode,
  and the `chmod` that fixes it; the file is not opened at all. `libpq`
  applies the same rule to `~/.pgpass`, except that it ignores the file
  silently — here the file was named explicitly, so falling through the rest
  of the ladder without saying so would be worse than stopping. Note for
  WSL: a file on a Windows mount reports
  `0777` regardless of intent, so the file has to live on the Linux
  filesystem — do **not** add a filesystem exception to the check.
- **An env file that sets `MAILCTL_PASSWORD` is held to the password-file
  bar** — any bit in `0o077` is a refusal naming the path, the mode, and the
  `chmod`. The mode is taken from the open handle, so it is the mode of the
  file actually read. The password is wrapped in `Secret` as it is parsed
  and never sits in the file's plain value mapping. A line the parser
  cannot read is reported by **line number only**, never quoted, because it
  may be the password.
- **`--password` is deliberately the least safe rung and says so.** It exists
  because it was asked for; `cli.py` warns on stderr that an argument is
  visible in the process list and saved to shell history. The warning is
  presentation and stays in the CLI; the mode refusal is behaviour and stays
  in the core.
- The same bar binds test doubles and throwaway debug shims. To tell two
  credentials apart, emit a non-reversible discriminator (a literal
  `set`/`unset`, a length, a short hash prefix) — never the value. See the
  global `CLAUDE.md` *Secret Handling*.

## The sibling repository

[`terraform-provider-mxroute`][provider] and this tool are **complementary,
not overlapping**, and the boundary is clean because the API draws it for us:

| Repo | Owns |
|------|------|
| `terraform-provider-mxroute` | account/domain state as code — domains, mailboxes, **forwarders**, catch-all, spam lists, pointers |
| `mailctl` (here) | filter **rules** (Sieve) and retroactive mail sorting (IMAP) |

Two practical consequences:

- **Forwarding belongs there, not here.** It is the substitute for the
  disabled Sieve `redirect`, and it is already implemented as the
  `mxroute_forwarder` resource. Point users at it; do not reimplement
  forwarding in this tool.
- **Filters cannot belong there.** The REST API the provider is built on has
  no Sieve surface at all ([ADR 0001][adr1]).

## Where work is tracked

Work is tracked as **GitHub issues**, not in a planning file. The sentinel
the global `todo.md` reads:

tracker: github

`TODO.md` is gone, and every open item it held was migrated to an issue. A
repo carrying both grows two answers to "what is left to do", and the file is
always the stale one — it has no assignee, no labels, no cross-references, and
nothing closes it when a PR lands.

Two things follow, which is why this is a declared sentinel rather than a
habit:

- **A captured follow-up becomes an issue**, reconciled against the existing
  ones rather than appended blindly.
- **Every issue carries a `role:*` label.** An open issue without one has not
  been triaged (`labels.md`), and that absence is the signal a sweep keys on.

**Deferred work is still not an issue.** A considered "not now" belongs in
[ICEBOX.md](../ICEBOX.md) with its trigger, or as an `ICEBOX:` marker at the
relevant code. Issues are for work someone intends to do; the icebox is for
decisions taken and parked. Filing a deferral as an issue is how a tracker
stops being readable.

## Toolchain & reproducibility

- **Python ≥ 3.11** (`requires-python` in `pyproject.toml`), developed inside
  a **`.venv`** at the repo root — never against the system Python.
- **`uv` provisions it** (the isolated-app rung of the global
  `toolchain-provisioning.md` ladder):

  ```sh
  uv venv                       # create .venv
  uv pip install -e '.[dev]'    # the package plus the dev tools
  ```

- **Runtime dependencies are `sievelib` and `IMAPClient`, and that is
  deliberate.** Both are pinned by lower bound in `pyproject.toml`, and
  both now carry an upper bound on the next major, so raising a bound is a
  deliberate, tested change rather than something a fresh install does
  unreviewed. Adding a third runtime dependency to a tool whose whole job is
  two protocol conversations deserves an argument first.
- Dev tooling (`ruff`, `pytest`) is an **optional dependency group**, so a
  user installing the CLI never pulls the linter in.

## QA

The global `qa.md` owns the pipeline and its ordering; this section is the
concrete toolchain and the **status of every dimension** for this repo.

- **Format + lint:** `ruff` — `ruff format` and `ruff check`, configured under
  `[tool.ruff]` in `pyproject.toml`. Pre-commit gates both, in the two-file
  fix-then-check split (`.pre-commit-config-fix.yaml` runs the auto-fixers
  once as a prep step; `.pre-commit-config.yaml` is the non-modifying gate).
  Do **not** wire black/isort/flake8 alongside ruff (`python.md`).
- **Code smell / complexity:** ruff's `B` (bugbear), `C4`, `SIM`, `UP`, and
  `RUF` rule sets, inside the same `ruff check`.
- **Security:** `gitleaks` + `detect-private-key` in pre-commit (secrets).
  Note that the *most* important security property of this repo — the password
  never being emitted — is a code-structure guarantee (`Secret`), not
  something a scanner checks; review it by reading `Secret.reveal()` call
  sites.
- **Prose:** `markdownlint` and `yamllint` in pre-commit.

Full dimension status:

| Dimension | Status |
|-----------|--------|
| 1. Format | **Active** — `ruff format` |
| 2. Lint | **Active** — `ruff check` |
| 3. Type-check | **Planned** — the package is fully annotated but nothing gates it; wire `pyright` (`pyright.md`) ([#10][i10]) |
| 4. Code smell / complexity | **Active** — ruff `B`/`C4`/`SIM`/`UP`/`RUF` |
| 5. Security | **Active (secrets only)** — `gitleaks`, `detect-private-key`. SAST is **Off**: the attack surface is two outbound TLS client sessions and no untrusted input parsing beyond the user's own Sieve script |
| 6. Tests | **Active** — the offline tier is green (`make test` / `pytest`); see [TESTS.md](TESTS.md) |
| 7. UI/UX & accessibility | **N/A** — a CLI with no UI. Terminal output legibility is covered by the *show, then change* convention |
| 8. End-to-end | **Scaffolded, gated** — `tests/live/` exists and skips unless `MAILCTL_LIVE=1`; it has never written to a real account ([#9][i9]) |
| 9. Compatibility | **N/A** — single target (CPython ≥ 3.11); no external contract we publish |
| 10. Performance & load | **N/A** — interactive, single-mailbox, human-scale. Revisit only if a retroactive pass over a very large folder proves slow |
| 11. Reliability & observability | **N/A** — a one-shot CLI, not a service. Its reliability property is the backup-before-upload convention |
| 12. Build | **N/A** — pure Python, no build step (`setuptools` metadata only) |
| 13. Documentation | **Active** — this file, `README.md`, and `adr/`; markdownlint gates the prose |
| 14. Code review | **Informal** — solo repo; `master` is PR-only, 0 required reviewers |
| 15. CI | **Active** — `.github/workflows/test.yml` runs `ruff check`, `ruff format --check`, and `pytest` on every PR and on pushes to `master`. The live tier is deliberately excluded — it needs real credentials |

## How work is dispatched

This repo is **team-managed**. The sentinel the global
`team-managed-delegation.md` reads:

team-managed: enabled

So the main-thread agent is the **Project-Manager seat**: it plans, routes,
and integrates, and **substantive edits are dispatched to the role that owns
them** — the Developer for package source and its tests, QA for the test
tier, the Writer for prose, the Config Engineer for anything under `.claude/`.
Orchestration glue stays with the main thread: planning notes, CI wiring,
`.claude/settings*.json`, and scratch files.

**Enabled deliberately, on 2026-08-14, not at scaffold time.** The rule is
default-off and says an agent must not self-enable it; the operator asked
for it after watching a module and its whole test suite get authored on the
main thread. Recording the date matters because the norm is what makes
*"since the role last touched these files"* a meaningful boundary, and before
this date there is no such boundary to compute.

The nudge is a `PreToolUse` hook, and it is **advisory by construction** — it
injects a reminder and allows the edit. Two consequences worth having
written down rather than rediscovered:

- **An explicit instruction outranks it.** If the operator says to edit
  directly, or subagents are unavailable, edit directly — hold the change to
  the **owning role's standard** and say which role's standard you applied.
  Acknowledge the reminder once per session, not per edit.
- **Standing in for a role is not the same as the role having seen it.** Work
  the main thread authored directly should be handed back for that role's
  review once delegation is available again. The mechanism for that is not
  built yet ([dotagents#495][da495]), so until it is this is a discipline
  rather than a gate.

## How much we ask

The section above is how the team routes work internally; this is how it
engages the person the work is for. Recorded because the operator set it
deliberately on 2026-08-16, in these words:

> Record in this repos convention that I am testing the minimal involvement
> aspect of dotagents. I want to test the condition of a client coming to me
> with an idea, but no knowledge of how to accomplish it, little desire to
> answer questions beyond the minimal needed. This is going to be a little
> weird because the end product will be a docker image someone has to setup,
> but that's the nature of testing. :shrug:

So **this repo is a live test bed for the involvement dial**, held near its
minimum. The operator is standing in as the client they describe, and
"customer" throughout means them.

**Involvement is how many questions we ask versus how many defaults we take,
and it is the Project Manager's dial** (`customer-communication.md` *Length is
a separate dial from depth*; `agents/project-manager.md` *Discovery before
dispatch*). It is **not** the register dials — depth and length — which are
the Product Owner's. Keeping the two apart is the entire point of the
distinction, and this is precisely the setting that invites collapsing them:
this one says **ask less**, not **say less**. Answer a complaint about how
much we ask by writing shorter answers and we have tuned the wrong dial, left
the real one untouched, and guaranteed the complaint comes back.

**The client being simulated has an idea and no route to it.** They do not
know how it would be accomplished and have little appetite for finding out.
Working out the *how* is therefore the job rather than the thing to ask about
— a question is a cost charged to them, so the default posture is to resolve
the unknown and bring back a result.

In practice:

- **Apply the scoping rule above instead of asking whether something is in
  scope.** *Is it a filter or a setting, and is there no way to manage it
  here?* — if so, the answer is yes and no decision is needed. That rule
  exists because a batch of questions should have been an answer; the #42/#43
  worked example under *The scoping rule* is the case that produced it.
- **Run the decide-now gate hard** (`issue-evaluation.md`). If a rule, the
  code, or a documented default settles it, it is not a question — answer it,
  and record the answer where it belongs.
- **Report decisions; do not request permission** (`departure-reporting.md`).
  Proceed on the better judgment and say what was done and why. Asking first
  suppresses the departures that were right, which are the majority, in order
  to catch the few that were not.
- **Batch what genuinely must be asked** — few at a time, each answerable in a
  sentence. `AskUserQuestion` is the shape that has worked here.

**The escalate bar does not move with the dial** (`issue-evaluation.md`
*escalate*). Ask when they know something we cannot work out — a fact about
their accounts, their mail, or what they actually want — or when the answer
gets expensive to change. And a **Tier 3** change (`change-cost-tiers.md`)
stops and asks regardless of the dial: real risk is the one tier where being
wrong is not recoverable by more work, and a preference for fewer questions is
never authority to take that risk on their behalf.

**The test has a known flaw, and it is accepted rather than compensated for.**
The end product is a Docker image somebody has to set up, which is not what a
genuinely minimal-involvement client wants handed to them; the operator named
this themselves in the same breath as setting the dial. It is not a reason to
abandon the test, and it is specifically not a reason to quietly start asking
more — raising involvement because the deliverable is awkward destroys the
thing being measured.

**Being a test, it is supposed to produce findings.** Where the setting causes
a wrong turn — a default taken that should have been a question, an assumption
that sent work down the wrong path — that *is* the result the test exists to
produce, and it gets recorded rather than silently corrected, because a
quietly-fixed wrong turn is a data point destroyed. When the operator says
anything about how much we ask, update this section with the date and what
they said: the same recording discipline `customer-communication.md` applies
to the register setting (*The setting is recorded, with its date and its
evidence*),
applied here to the dial actually under test. It is a live fact about this
engagement rather than a style preference, so it belongs where the next agent
will read it.

## Merge policy & versioning

- **`master` is PR-only, enforced server-side.** The *Protect Master Branch*
  ruleset is active on `master` with no bypass actors: it blocks deletion and
  non-fast-forward pushes, requires a pull request (squash-only, 0 approvals,
  review threads resolved), and requires the `Lint` and `Test` status checks.
  The local `no-commit-to-branch` pre-commit hook and the global
  `branch-protection.py` edit-time hook sit in front of it as earlier layers
  (`git.md` *Protecting the Default Branch*).
- **Auto-merge is declared** (operator, 2026-09-27,
  [#11](https://github.com/harleypig/mailctl/issues/11)). The
  ruleset is the server-side guardrail the opt-in (`gh.md`) rests on, so
  invoking push-pr is consent through merge once the required checks are
  green. This is the agent workflow's opt-in, **not** GitHub's own auto-merge
  feature; the merge still obeys the ruleset, and the repo deletes head
  branches on merge. The sentinel push-pr reads:

  auto-merge: enabled

- **`merge-finalization` is not declared.** Its hook guards `TODO.md` /
  `ROADMAP.md` finalization, and this repo has neither — work is tracked as
  issues (*Where work is tracked*).
- **Versioning:** semver `vX.Y.Z`, `repo` scope (one version for the whole
  tool — the `git.md` *Versioning & tags* method). See below.

## Versioning & tagging

`repo`-scope semver, tagged `vX.Y.Z`, annotated, cut at the merge commit on
`master` with the `release-tag` skill.

- **Currently `v0.y.z` — alpha.** Per `git.md`, `X = 0` means **breakage is
  expected** and the `y.z` split is deliberately loose: bump `y` for a
  meaningful addition, `z` for a smaller change, and do not agonize over
  which. The `0 → 1` jump is a decision in its own right and is not near.
- **The rename gates `0 → 1`, and it has landed.** `v1.0.0` is where the
  version becomes a compatibility promise, and a name — the command, the
  config directory, the environment variables — is part of what it covers;
  renaming after v1 would cost a `v2.0.0` for nothing but a name already
  known to be wrong. So the rename had to come first, and with #45 it has:
  `mxfilter` → `mailctl`, `MXROUTE_*` → `MAILCTL_*`, and the config directory
  with them. The gate is recorded here and in
  [RELEASING.md](../RELEASING.md) so it is read at the moment it applies.
- **No API-major alignment.** The sibling provider aligns its MAJOR to the
  MXroute REST API's major, because it is a client of a versioned API. That
  does **not** carry over: this tool speaks ManageSieve (RFC 5804) and IMAP,
  standardized protocols with no vendor version to track. Do not import the
  provider's bump policy.
- **A tag publishes nothing.** There is no release pipeline and no registry —
  a tag is a marker on history, so it is cheap and carries no
  cannot-be-unpublished risk. See [RELEASING.md](../RELEASING.md).

[adr1]: ../adr/0001-standalone-cli-over-provider-resource.md
[adr2]: ../adr/0002-non-destructive-script-merge.md
[provider]: https://github.com/harleypig/terraform-provider-mxroute
[i9]: https://github.com/harleypig/mailctl/issues/9
[adr5]: ../adr/0005-restore-may-replace-an-unparseable-script.md
[adr6]: ../adr/0006-two-layer-component-and-provider-architecture.md
[i90]: https://github.com/harleypig/mailctl/issues/90
[i13]: https://github.com/harleypig/mailctl/issues/13
[i10]: https://github.com/harleypig/mailctl/issues/10
[da495]: https://github.com/harleypig/dotagents/issues/495
