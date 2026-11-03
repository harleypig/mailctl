---
paths:
  - "mailctl/providers/**"
  - "mailctl/components/**"
  - "mailctl/engine.py"
  - "mailctl/config.py"
  - "tests/test_providers.py"
  - "tests/test_engine.py"
---

# mailctl Conventions — providers and protocols

Path-scoped detail moved out of `.claude/CONVENTIONS.md`, which stays always
loaded as the short core; read the core's *Where the detail lives* for
the whole map. Claude Code loads this file only when a file matching
the `paths:` above is read or edited.

## Providers

**A provider is a two-way translator** ([ADR 0006][adr6] *Amendment*).
The utilities speak one provider-neutral model: a rule is criteria plus an
`ActionSpec`, and the model also covers folders, messages, capabilities,
results, and `MailctlError`. The provider converts that model into its
host's terms on the way out, and converts the host's answers back on the
way in. For `mxroute`, outbound is Sieve over ManageSieve, and inbound a
parsed script becomes `Rule` values.

**It does so in two halves** ([ADR 0007][adr7]):

- **The dialect is offline and host-specific.** It translates, parses, and
  edits (for `mxroute`, Sieve in Roundcube's `# rule:[NAME]` form), holds
  the host's refusals and its words for the server reports as data, and
  never touches the network.
  `tests/test_layer_purity.py` holds a dialect's modules — derived by
  following its imports, not listed — to opening no connection.
- **The transport is communication only.** Each operation is one exchange
  with the server, or a composite the host performs natively. It does not
  loop unless the protocol does, and it builds and validates nothing: it
  tries to store what it is handed and reports success, or raises
  `MailctlError` with the server's answer. The same guard holds a
  transport's modules to importing no building helper and no utility.

**Every transport operation is classified**, once, on the `Transport`
interface in `base.py`: which half it uses (`rules` or `mail`) and whether
it is a **read**, a **write**, or a **connection** operation (`open`,
`connect`, `disconnect`, `dropped`, the `has_*` answers). One read per half
is also marked that half's **probe** (`Operation.probe`): `list_rule_sets`
on rules and `mail_noop`, an IMAP `NOOP`, on mail. A probe must be a real
exchange with the server, never an answer cached at login, which is why
neither capability read serves and the mail half has `mail_noop`. The
session reads that classification and never asks which provider it has.

**The session is lazy, and what it does around each call it reads from the
classification** (`engine.Session`):

- **Each half connects the first time a utility uses it**, and only if the
  command asked for it; a command that only reads rules never opens IMAP.
  The session closes what it opened, last first, when it ends.
- **A read the server cut off is sent once more on a fresh connection.** A
  second failure is raised, and so is a refusal — `dropped` tells the two
  apart.
- **A write is never sent again.** It may have landed before the
  connection went; a retry could land it twice, and would hide the failure
  the user needs to see. A failed write is final for that command.
- **A write on a half idle for a minute is preceded by its probe**
  (`engine.IDLE_PROBE_AFTER`, 60 seconds). A server may log an idle client
  out, and a long-lived front-end's first call after a while may be a
  write, which would then fail for nothing. The probe is a read, so a
  connection dropped while idle is found by a call that may be sent again,
  and the write goes out on a connection known to be live. A half not yet
  connected, or used within the minute, sends none; a probe the server
  refuses stops the write.
- **One call at a time per half**, under a lock, since IMAP and
  ManageSieve are both one command after another; and each mail call names
  its folder rather than trusting the one a previous call left selected.
- **A front-end may stop a call before it is sent.** `engine.connect` and
  `Session` take a `checkpoint`, a third callback beside `progress` and
  `on_event`, called before every transport operation, True for a write,
  and before the half is connected; what it raises stops the operation
  unsent and reaches the caller as raised. The utilities never know it is
  there, so every loop in them stops between exchanges, never during one
  ([#280][i280]).
- **A session owns its connections, and nothing is shared between
  sessions.** There is no module-level connection state: a long-running
  front-end keeps one session per user.

**The CLI keeps its shape**: one command opens a session with the halves it
needs, runs, and closes it. It uses the session's lazy open, so bad input
fails before any login or password prompt (decided on [#137][i137]).
**The API holds one session for its process**: `initialize` resolves the
configuration from the process's own arguments, as the CLI does, and opens
the session, connecting nothing; `shutdown` or the end of input closes it.
A bad argument does not end the process, which a client would read as the
end of its input: it is said on stderr and answers `initialize`, coded,
in the CLI's words for it (*Credentials*; [#352][i352], [#364][i364]).
Requests are handled one at a time, in order. **A request can be cancelled
between exchanges, never after its first write** ([#280][i280]). The
client sends `$/cancelRequest`, the Language Server Protocol's
notification, naming the request's id, and the API's checkpoint stops it
before its next exchange, answered `-32800` with `written` false and the
path of any backup it wrote first. Once a write is sent the request runs
to its end and answers as it would have, since that write may have landed
and is never sent again. A cancel names only a request sent before it,
the one in hand or one waiting its turn ahead of it; any other id is
ignored, so a late cancel never stops a later request reusing the id.
A cancel sent behind 64 messages not yet served is not seen until some
are served, and a `credentials.prompt` waiting while that many are held
is refused, coded `prompt_input_full`, since its answer could no longer be
read ([#346][i346], [#378][i378]).

**How a provider is chosen, declared, and held to its interface:**

- **Selected by the `provider` setting**, default `mxroute`, resolved like
  every other setting: `--provider`, then `MAILCTL_PROVIDER` in the env
  file or the environment, then `provider` in `config.toml`. An unknown
  name is refused, naming the known ones, before anything connects.
- **In-tree and registered in-tree.** A provider is a `Provider` record —
  its name, its `ProviderCapabilities`, its dialect class, and its
  transport class — listed in `providers/registry.py`. There are no entry
  points; ADR 0006 defers them.
- **Differences are data, never a branch.** A provider declares
  `ProviderCapabilities`: `ordering`, `stop`, `rule_sets`, `disable`,
  `rename`, its `actions`, `extensions`, `raw_query`, `mark`,
  `folder_counts`, `uidvalidity`, `folder_visibility`, its namespaced
  `specifics` with their schema, the connection `settings` it reads, and
  the operations it `declined`. The utilities read those and never ask
  which provider they have.
- **Refused before any network work.** `utilities.rules.check_rule`
  refuses a rule the provider cannot express, through one error naming the
  provider, the construct, and why (`providers.base.refuse`). It takes a
  code: `unsupported` for what the provider declares it cannot do, which a
  front-end can learn before asking, or one of its own where the server
  or a setting is the cause ([#352][i352]).
- **Every provider answers every operation.** Each half implements or
  explicitly declines (`@declined`) every operation of its interface —
  `Dialect` or `Transport`, `mail_noop` among them — and what the two
  decline matches the
  provider's capabilities. `tests/test_providers.py` holds this. It also
  drives a fake second provider through the utilities to show the calls
  match `mxroute`'s.
- **The neutral model is the provider layer's own.** `providers/model.py`
  defines every record the utilities and a front-end build or read, and it
  and `base.py` import nothing from a component. A component keeps its
  own records, and the provider translates (`mxroute/records.py`,
  `mxroute/sieve.py`), so a provider with no Sieve never imports the Sieve
  component ([#99][i99]).
- **What the user sees is the same for every provider** (operator,
  2026-09-29, [#219][i219]). The user-facing nouns are *filter* and *filter
  set*, whatever the host calls them, and never *rule* or *script*; code
  names such as `Rule`, `rules.py`, `RulePlan`, the `rule_sets` capability,
  and `script` may stay. The options follow: `--move-to` (was
  `--fileinto`), `--filterset` (was `--script`), and `--no-mail` (was
  `--no-imap`), each old name refused naming the new one
  (`cli.GONE_OPTIONS`, beside `cli.GONE_COMMANDS`); and a `--json`
  document's `filter` / `filters` / `filterset` / `filtersets` keys and its
  folder `status` values (`create`, `created-on-delivery`,
  `create-and-on-delivery`). A provider's own words reach the user in three
  places only:
  1. documentation about the provider, such as its `RECORD.md`;
  2. the help of an option only it has — `--disable-extension`, and the
     connection settings it declares (`--host`, `--imap-*`, `--sieve-*`,
     which keep their names) — and a refusal of a provider-only function,
     such as an extension it lacks or an action it forbids;
  3. `server test`, `server probe`, and the baselines, whose job is to
     describe the servers, and `config test`, which prints `server test`'s
     own settings and mail sections ([#440][i440]).

  Everywhere else — every other help page, prompt, progress and event
  line, error, the diff's heading, a rule's actions, and the frame
  `filterset show` puts round the source — is fixed text in the front-end.
  The filter set's own text, in a diff or `filterset show`, is the host's
  language and is shown as it is; so is a server's alert or warning, and
  `--verbose`'s record of the exchange. `tests/test_neutral_wording.py`
  holds this under `mxroute`, over every help page read off the parser and
  every snapshot but the server reports'.
- **The host's words for its reports are the dialect's data.** A
  front-end lays out one report for every provider and fills it from
  `Provider.wording` — its service names, what its extensions are called,
  what it calls a rule set (for the unrecognised-server report), and its
  closing notes such as MXroute's redirect and Exim/DirectAdmin notes —
  and from `connection_facts` and `mail_facts` (labelled lines), each
  reached through a utility; `Wording` holds nothing else. What a rule's
  actions do comes back from `describe_actions` as neutral `ActionStep`
  records the front-end words. `DisplayDiff.label` names the diff's
  language for a `--json` plan, and is never a heading. Nothing about one
  host is written into `cli.py`.
- **A default comes from the capabilities.** `ActionSpec.stop` None is the
  provider's default, which `utilities.rules.resolve_stop` settles from the
  `stop` capability: a host that cannot stop is never asked to, so its
  default rules are not refused. `--no-stop` sends False; an explicit True
  is still refused where `stop` is not declared.
- **Offered only what it declares** ([#26][i26] constraint 4). The CLI parses
  twice: a first pass reads only `--provider` and `--env-file` and resolves
  the provider, then the parsers are built from its capabilities. Without
  `ordering`, the placement flags, `filter move`, and `filter optimize` are
  not offered; without `stop`, `--no-stop`; without `rule_sets`, `filter
  add`'s `--filterset` and `--activate`; without `disable`, `filter
  disable` and `filter enable`; without `rename`, `filter rename`; without
  `extensions`, `--disable-extension`; without `raw_query`, `mail search`'s
  `--raw`; without `mark`, `mail mark`; without `folder_counts`, `folder
  list`'s `--counts`;
  without `uidvalidity`, `--uidvalidity`; without `folder_visibility`,
  `folder subscribe`, `folder unsubscribe`, and `--no-subscribe`, and a
  folder is created and renamed with no subscription step and listed with
  no subscription wording ([#39][i39]). The connection flags (`--host`,
  `--imap-*`, `--sieve-*`) are offered only where
  `ProviderCapabilities.settings` names them, with the help it gives — they
  keep their names and their `MAILCTL_*` variables, being `mxroute`'s
  connection options. An unoffered option is **hidden, not removed**: given
  anyway it still parses and meets the refusal below the front-end naming the
  provider, which stays the backstop for every front-end. A first pass that
  cannot resolve a provider falls back to the default provider's offer, and
  the run reports the problem. `mxroute` declares everything, so its help is
  what it always was.
- **A setting belongs to the provider that can use it.**
  `disabled_extensions` keeps its name and its ladder, but only a provider
  declaring `extensions` takes it; any other refuses it, before
  connecting, naming the provider and where the setting came from. A
  connection flag a provider does not read is refused the same way. An
  ambient connection setting from the environment or the config file may
  serve another provider, so only a flag is refused.
- **Adding one is a record, a package, and a registry line**:
  `providers/<name>/RECORD.md` first (*Providers are probed and read*),
  then `providers/<name>/` — a dialect, a transport, and the `Provider`
  record naming them — composing the layer-1 components it needs, with no
  change to the session or the utilities.

## The protocols

There is no vendor API here — the tool speaks two standard protocols.

- **ManageSieve** — RFC 5804. IANA reserved **TCP 4190** for it, and the RFC
  requires both ends to implement **STARTTLS** (*"Client and server
  implementations MUST implement the STARTTLS extension"*). That is the
  tool's default, and it is a **default, not a documented fact about
  MXroute**. A probe observed it working on one server; see the MXroute
  [record][rec-mxroute].
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

**`MAILCTL_BACKUP_ROOT` is the one other exception** ([#230][i230]). It
limits `backup_dir` rather than supplying a value, so it is read from the
process environment only: a rung that could set it could lift it. Where it
is set, a backup outside it is refused, coded `backup_outside_root`, before
anything connects and again where each backup is written (*Rule
conventions*).

### Confidence — documented, observed, and unknown

Every fact mailctl relies on about a host sits in one of three tiers. The
tiers are **documented** (the host, or the software it runs, says so),
**observed** (a probe saw it on one server on one day), and **unknown**.
Mark each fact honestly, and do **not** quietly promote one tier to
another. An observation is stronger than a guess and weaker than
documentation. It describes *that server*, not the host. Unknowns are stated
plainly rather than filled.

MXroute documents very little of its Sieve surface. That gap is itself a
design driver: it is why *Discover, don't hardcode* below is a rule rather
than a preference. **The facts themselves live with the provider**, one
record per provider, and each record is kept in these three tiers:

| Provider | Record |
|---|---|
| `mxroute` | [`mailctl/providers/mxroute/RECORD.md`][rec-mxroute] |

Code and docs that need a host fact cite the provider's record, not this
section. This section owns the discipline.

### Providers are probed and read

The operator's requirement, 2026-09-27:

> during development and maintenance the provider, if possible, needs to be
> probed as well as documentation read to make sure we're able to take full
> advantage of the service.

So a provider's record has two jobs. It says **what the provider can rely
on**. It also says **what the host offers that mailctl does not yet use**,
in a *Not yet used* section, so that "take full advantage" has somewhere to
land. **Both halves are refreshed together**: read the documentation again,
and probe where a probe is possible.

**A record is keyed on observed capabilities and behaviour, with dates,
never on a version number.** Hosts run different server versions at
different times, and servers hide their version: MXroute's reports none on
either protocol ([#18][i18]). A version a server does report is a hint.

**Refresh a provider's record:**

- **when the provider is built.** A new provider, Gmail for example,
  **starts with its record, written before its code**. What the host
  documents and what a probe shows are the inputs to its capability
  declaration, so they come first.
- **when the host announces a change.** For MXroute, the standing cases are
  the Dovecot 2.3 → 2.4 migration and the replacement of Roundcube,
  Crossbox, and DirectAdmin. Each record lists the announcements it has
  seen.
- **before relying on an old observation for a new feature.** Re-probe
  before building on an observation older than the cadence below. It
  describes one server on one day.
- **quarterly, at the latest.** Refresh any record whose newest observation
  is more than 90 days old.

**Why quarterly, and not each release.** Tags here are cheap and publish
nothing ([RELEASING.md](../../RELEASING.md)), so they come in bursts and
droughts that have nothing to do with the host. A release-keyed refresh
would run three times in a busy week and then not for months. The host's
own rhythm is the one that matters, and MXroute's releases have come a few
months apart: 3.2 in June 2025, 3.9 in December 2025, 4.0 in January 2026,
and 4.0.1 in May 2026. So a quarter catches a change nobody announced
within about one host release. The trigger is the date on the record, which
anyone can check. Nothing automates it yet.

**What a probe is, today.** A probe is a read-only live session against one
account, recorded with the command, the date, and the server's hostname:

- **`mailctl server probe`** prints, dated and with the hosts configured, each
  server's identity (IMAP `ID`, ManageSieve `IMPLEMENTATION`) and its whole
  capability list — saying whether it was read before or after login — the
  Sieve extensions, the active script, the delimiter, and the namespaces
  ([#101][i101]). `--json` is the same as a versioned document with sorted
  lists, so two probes of an unchanged server differ only in the time.
- **`mailctl server test`** adds the folder and subscription counts and what
  `MOVE`, `UIDPLUS`, and `FILTER=SIEVE` mean for mailctl, and one line on
  drift where a baseline is saved.
- **`mailctl server baseline save`** stores the probe as the host's baseline,
  and **`mailctl server baseline check`** reports drift from it (below).
- **`make testlive`** is the live tier's read-only smoke tests. It
  confirms the port, the TLS mode, and the delimiter.
- **`make livecheck`** runs the read-only CLI checks against the account
  and confirms nothing changed (TESTS.md › *The read-only live check*).

A probe never prints the password. It is held to the same bar as a debug
shim (*Credentials*).

**The automated form is built** ([#18][i18], [#19][i19]).
`server baseline save` writes the probe to
`$XDG_CONFIG_HOME/mailctl/baselines/<host>.json`, the server's part once
and the active script per account; `server baseline check` compares a fresh
probe with it and says what each difference means for the account, exiting 0,
3 (informational drift), or 4 (serious drift). The file is on one machine and
is not versioned, so the record still transcribes what a probe saw; a refresh
is *save a baseline, then copy what changed into the record*. A save is
refused, coded `baseline_changed`, with nothing written, when the file is
no longer what its plan read ([#279][i279]): a plan carries every other
account's part of the file as it read them, so writing over a save made
since would lose that account's part.

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
  name the same folder (`components.imap.normalize_folder`).
- Read the advertised Sieve extensions rather than assuming a capability is
  present.

A default is fine where the protocol supplies one (port 4190); an **assumption
about MXroute's configuration** is not.

**Drift from a stored baseline is warned about, never refused**
([#19][i19]): the live server wins, the difference is reported, and
refreshing the baseline is an explicit command that shows the diff and asks.

[adr6]: ../../adr/0006-two-layer-component-and-provider-architecture.md
[adr7]: ../../adr/0007-interfaces-utilities-session-provider-layering.md
[i280]: https://github.com/harleypig/mailctl/issues/280
[i137]: https://github.com/harleypig/mailctl/issues/137
[i352]: https://github.com/harleypig/mailctl/issues/352
[i364]: https://github.com/harleypig/mailctl/issues/364
[i346]: https://github.com/harleypig/mailctl/issues/346
[i378]: https://github.com/harleypig/mailctl/issues/378
[i99]: https://github.com/harleypig/mailctl/issues/99
[i219]: https://github.com/harleypig/mailctl/issues/219
[i440]: https://github.com/harleypig/mailctl/issues/440
[i26]: https://github.com/harleypig/mailctl/issues/26
[i39]: https://github.com/harleypig/mailctl/issues/39
[rec-mxroute]: ../../mailctl/providers/mxroute/RECORD.md
[i230]: https://github.com/harleypig/mailctl/issues/230
[i18]: https://github.com/harleypig/mailctl/issues/18
[i101]: https://github.com/harleypig/mailctl/issues/101
[i19]: https://github.com/harleypig/mailctl/issues/19
[i279]: https://github.com/harleypig/mailctl/issues/279
