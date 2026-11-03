# mailctl Conventions

Repo-specific conventions. The global `~/.claude/` config carries everything
generic (git/gh, code style, the Python toolchain via `python.md` +
`ruff.md`, the QA dimensions). This file records only what is specific to
**this** repo.

**This file is the always-loaded core; the detail is path-scoped.** It holds
what any session needs whatever the work. The rest sits in `.claude/rules/`,
each file carrying `paths:` frontmatter so Claude Code loads it only when a
matching file is read or edited. `tests/test_claude_context_budget.py` holds
the always-loaded set to a byte budget, so growth here is a decision.

## Where the detail lives

A reference elsewhere to *CONVENTIONS.md › Section* finds its section here
when it is not in this file.

| Section | Home | Loads for |
|---|---|---|
| *Layout*, *The old name*, and the layering detail (*Three kinds of operation*, *Interfaces call only utilities*, *Nothing below the front-end writes*, *A core message names no front-end's flag*) | [rules/package.md](rules/package.md) | `mailctl/**`, `clients/**` |
| The API's paragraphs (*The API is the second interface*, *Across the API the plan stays*, *An API method is named by its command path*) | [rules/api.md](rules/api.md) | `mailctl/api/**`, `mailctl/json_output.py`, `clients/**` |
| *The command surface* in full | [rules/command-surface.md](rules/command-surface.md) | `mailctl/cli.py`, `mailctl/json_output.py`, `mailctl/criteria.py`, `mailctl/api/**`, CLI tests and snapshots, `docs/**` |
| *Providers*, *The protocols*, *Confidence*, *Providers are probed and read*, *Discover, don't hardcode* | [rules/providers.md](rules/providers.md) | `mailctl/providers/**`, `mailctl/components/**`, `mailctl/engine.py`, `mailctl/config.py` |
| *Rule conventions* | [rules/filter-rules.md](rules/filter-rules.md) | `mailctl/utilities/**`, `mailctl/providers/**`, `mailctl/components/managesieve/**`, `mailctl/rules.py`, `mailctl/criteria.py`, `mailctl/config.py` |
| *Toolchain & reproducibility*, *QA* | [rules/toolchain-qa.md](rules/toolchain-qa.md) | `pyproject.toml`, `Makefile`, `Dockerfile`, CI and pre-commit config |
| The test layout | [TESTS.md](TESTS.md) and its rules | always (core), `tests/**`, `clients/tui/**` |

## What this is

`mailctl` — an MIT-licensed Python CLI that manages MXroute email filters end
to end. It does two things, and the second is the reason it exists:

1. **Creates server-side Sieve filters over ManageSieve**, merging the new
   rule **non-destructively** into the account's existing active script (see
   *Rule conventions* and [ADR 0002][adr2]).
2. **Applies the same rule retroactively over IMAP** to mail already sitting
   in the mailbox — because Sieve only ever runs on **new incoming** mail.
   Writing the Sieve rule alone leaves every message already delivered exactly
   where it was.

They are two commands, `filter add` then `filter apply` (*The command
surface*).

Distribution name and package are both `mailctl`; the console entry point is
`mailctl = "mailctl.cli:main"`. It is **not published anywhere** — see
[RELEASING.md](../RELEASING.md).

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

**Roundcube's filter and search UI is not a limit on what mailctl writes or
offers.** The operator, 2026-09-29: *"one of the reasons I'm writing this
app is because the search and filter functions are so restricted."* And
the same day, on [#219][i219]: *"we aren't using roundcube, we're replacing
it and taking ideas for functionality from it."* So a rule mailctl writes
need not be one Roundcube's UI can edit, and nothing mailctl shows the user
is worded the way Roundcube words it (*What the user sees is the same for
every provider*). The merge still holds ([ADR 0002][adr2]): Roundcube
may still write the same script, so the rules it wrote are kept. Rules also
stay a flat list of `# rule:[NAME]` blocks, on grounds of their own: the
name is how every command finds a rule, and `mailctl/rules.py` reads the
rules as one ordered list, so it cannot judge a nested rule.

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
`folder list` and `server test` silent about subscription; an unsubscribed
folder left alone; a flag with no effect. Applying the rule, three are not
questions at all — subscription is a setting, so exposing it is in scope by
definition ([#42](https://github.com/harleypig/mailctl/issues/42)) — and the
fourth is a plain bug ([#43](https://github.com/harleypig/mailctl/issues/43)).
Asking was the error.

Built on two libraries, both of which the code wraps rather than exposes:

- **`sievelib` (≥ 1.5.0, < 2)** — the ManageSieve client
  (`sievelib.managesieve`), the Sieve parser, and the `factory.FiltersSet`
  script builder.
- **`IMAPClient` (≥ 4.1, < 5)** — the IMAP half.

## The core returns data; only the interfaces print

**`config`, `criteria`, `rules`, `components/`, `providers/`, the session
in `engine.py`, and the `utilities/` that do the work return structured
values and raise `MailctlError`. Every piece of rendering, prompting,
confirmation, and progress output lives in an interface: `cli.py`, or the
API in `mailctl/api/`.** Two reasons, both cashing out now: the core stays
testable without capturing stdout, and a second front-end sits on the same
core instead of requiring it to be torn apart first — the API did
([ADR 0008][adr8]).

**Four layers, and each calls only the one below it** (operator,
2026-09-28, [#137][i137]; [ADR 0007][adr7] holds the reasoning):

```text
interfaces  cli · api               parse input, render output, ask
utilities   mailctl/utilities/      the work, and the safety policy
session     engine.py               config → provider → open, keep, close
provider    dialect                 offline: neutral model ⇄ host language
            transport               communication only
```

**The utilities do not know how they were called.** The operator's
instruction, 2026-09-27: *"the engine, the code that does the actual work,
should not know nor care how the app was called (cli, tui, gui, web)"* —
the work named there is the utilities' now, and the session's under them.
So neither takes an `argparse` namespace, imports the CLI, or reads the
terminal or the environment; `tests/test_core_no_presentation.py` enforces
all three. Every change is **plan → decide → execute**: a `plan_*`
function is read-only and returns what would change (a diff, placement
findings, a message preview, counts); the front-end renders it and makes
the decision (`--dry-run`, `--yes`, a confirmation prompt); an
execute-style call carries the plan out. New work goes into the utilities
first; an interface should only gain parsing and rendering.

**Safety policy lives in the utilities, not the front-end or the
provider**: the backup before every upload (`scripts.upload_script`, the
one upload path) and the refusal of a filter set changed or switched
since the plan (*Rule conventions*), merge-never-overwrite, the
`--max-messages` ceiling (re-checked when a mail plan is executed), and
the UIDVALIDITY check on a UID before it is used (checked again when a
mark or mail plan is executed) hold whichever front-end calls them.

**Every command is in every interface.** The operator, 2026-09-27:

> I want the same functionality for all interfaces. I honestly don't remember
> if I just didn't explain myself well, or something got lost along the way.
> There may be some parameters differences between cli, tui, gui, and web but
> the commands should all be available in all environments.

So every operation lives in the utilities and is callable by every
front-end — CLI, TUI, GUI, and web. **Parameters may differ per front-end;
availability may not.** A capability wanted for one front-end is built into
the utilities and exposed in all of them; a feature only one front-end has
is a gap in the others, not a design choice.

**An interface may leave out an operation that does not fit its medium**
(operator, 2026-09-28, [#145][i145]). "Next message" is a stateless
utility: a TUI stepping through a folder calls it, and the one-shot CLI
does not expose it. What is left out is left out for the medium, never
because the work was built into one front-end. The CLI is the one
front-end that offers every operation today, so today this means the CLI
exposes every utility operation that fits a command line.

**The CLI comes first, and it is the baseline.** The operator, 2026-09-28:

> I don't think I've said this before, even though it's been my plan all
> along. I think I thought it was implied. The cli version of mailctl is
> mainly for automating tasks. Focusing on it first is the baseline. It's
> easier to build cli -> tui -> {gui,web} than to backfill.

So the order is **CLI, then TUI, then GUI and web**. The CLI is the
automation surface, and it is built first; a later front-end is built on
what the CLI already exposes, never backfilled into it. Read with the
paragraph above, this is why the CLI exposing every utility operation that
fits it is the standing state rather than a stopgap.

**Per-folder retention is the shape of automation this means**
([ICEBOX.md][icebox-retention] › *Per-folder retention — expire mail after N
days*). Retention only works if it runs repeatedly, and mailctl stays a
one-shot CLI: a scheduler (cron or a systemd timer) runs it, rather than the
tool growing a daemon.

One consequence, because automation is the CLI's job: **its output and exit
codes are a contract a script depends on** — every exit code, and every
output that is data (*The command surface* › `--json`). Change them
deliberately, never as a side effect. The CLI snapshots (TESTS.md) are what
make such a change visible.

## The command surface

**The decisions are the operator's**, 2026-09-28 ([#145][i145]). `filter
add` saves the rule only, `filter apply` acts on mail already delivered, and
`filter add --like` and `filter apply --like` build the criteria from a
message ([#149][i149], as decided on [#148][i148]).

**Read and write are separate at every layer.** A command, a utility, and a
transport operation each either changes the server or does not, even where
one server command could do both. A new write that has a read twin is a
separate operation, never a mode switch on the read. The transport's
classification (*Providers*) already names each operation's kind;
[#154][i154] guards that a read-only utility never calls a write.

**Commands are grouped, noun first: `mailctl <group> <action>`**
([#219][i219]). The operator, 2026-09-29, replacing the flat commands of
[#145][i145]: *"yes, change the decision, and the groups as above is much
better."* The groups, in the order the top-level help lists them:

```text
mailctl mail      search · view · mark · senders
mailctl folder    list · create · rename · subscribe · unsubscribe
mailctl filter    list · add · apply · remove · move · rename · enable · disable · optimize
mailctl filterset list · show · backup · restore
mailctl server    test · probe · baseline save|show|check
mailctl config    test · migrate
mailctl help [group [action]]
```

The user-facing noun is **filter**, for every provider; code may keep
`Rule` and `rules.py`. A command is named by its path (`filter add`)
everywhere a command is named: an error's `operation` field, a plan's
`--json` `command`, and the help. Capability gating stays per command, and
a group none of whose commands is offered is hidden whole: `filterset` goes
with `rule_sets`. A hidden command still parses and runs, as a hidden
option does. The old
flat names are a clean break, never aliases: each is refused, exit 2,
naming the command it is now (`cli.GONE_COMMANDS`), coded `gone_command`.
`mailctl help [group [action]]` prints the same as `--help` there
([#153][i153]).

The rest of the surface — each command's options, `--json`, `--like`,
and the refusals — is [rules/command-surface.md](rules/command-surface.md).

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
    variable merely happens to be exported. The inverse silently
    authenticates as the wrong account when `MAILCTL_PASSWORD` is exported for one mailbox and
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
- **The API takes the same ladder without `--password`, and its prompt is
  a request to its client.** Where no rung supplies the password, the API
  sends `credentials.prompt`, and the reply's `password` is wrapped in
  `Secret` as its line is parsed (`api/protocol.py`), before anything
  dispatches or logs it. No method takes a password. A login the server
  refuses, coded `auth_failed`, makes `Config.forget_prompted_password`
  drop a prompted password so the next request asks again; one from a
  file, a command, or a setting is kept. On either interface, a password
  IMAP cannot send, since imaplib sends `LOGIN` as ASCII, is refused as
  `credentials_not_ascii`, quoting nothing. A cancel of the request ends
  the prompt's wait; a late answer to that prompt answers nothing, and the
  next request asks again ([#280][i280]). The half connects before it
  asks, so the cancel also closes that connection ([#349][i349]).
- **A refused command line quotes no word argparse could not use.** That
  word may be a password that lost its option, so `json_output.rejection`
  words every argparse refusal before it is shown — on the CLI's error
  line and its `--json` line, and on the API's stderr and in its answer
  to `initialize`. A long option is named up to its `=`, a short one only
  alone; a word straight after an option, or after `--`, with no `=` may
  be that option's value, so it is counted, never named; and a message of
  no shape it knows reads *the command line was refused*
  (`json_output.REFUSED`), never argparse's text ([#364][i364]). Two
  values are refused below argparse, so the core words them itself, on
  either interface: an unknown provider, `unknown_provider`, and an env
  file that cannot be read or is not UTF-8, `env_file_unreadable` and
  `env_file_not_utf8`, name neither the value nor the path
  ([#373][i373]). The env file's mode and line refusals still name its
  path: the file exists, and the `chmod` needs it.
- **A password command reads nothing of mailctl's stdin.** It runs with
  stdin from `/dev/null` on either interface: under the API stdin is the
  client's pipe, and under the CLI it may be a `--filter -` document
  ([#280][i280]).
- **The API expands nothing in a path a client sends.** The client has no
  shell on the API's host, so `filterset.restore`'s `file` is read as
  given, and every message names it as given: expanded,
  `$MAILCTL_PASSWORD` would be the password. A restore of a password file
  or the env file is refused, coded `credential_file`, on either interface
  (*Rule conventions*).
- The same bar binds test doubles and throwaway debug shims. To tell two
  credentials apart, emit a non-reversible discriminator (a literal
  `set`/`unset`, a length, a short hash prefix) — never the value. See the
  global `CLAUDE.md` *Secret Handling*.

## The sibling repository

[`terraform-provider-mxroute`][provider] and this tool are **complementary,
not overlapping**, and the boundary is clean because the API draws it for us.
(That repository is a *Terraform* provider; it is unrelated to mailctl's own
`mxroute` provider, which is this tool's layer 2 — see *Providers*.)

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

There is no `TODO.md`. A
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

### The board

**Every open issue and pull request is on the [Mail Control][board]
board**, user project 4, private (decided on [#277][i277] and
[#278][i278]). It shows what is next and what state it is in; the issue
stays the record.

**Workflows add and move items, so an agent never adds one or sets Status
by hand.** A new issue, pull request, or sub-issue is added as Todo;
linking a pull request to an issue sets it In Progress; closing an issue
or merging a pull request sets it Done, and the merge closes the issue.
What an agent does set, on every issue it files:

- **Size** — `XS`, `S`, `M`, or `L`;
- **Layer** — `interface`, `utilities`, `session`, `provider`, or
  `component` (the layers of [ADR 0007][adr7] and the components under
  them), or `infra/CI` or `docs`;
- **the milestone.**

**Two kinds of issue carry fewer, by design** ([#287][i287]):

- **A milestone's introspection issue carries no Layer.** It is about how
  the work went, not about a layer of the code, and no Layer option
  describes it; it still carries a Size and its milestone.
- **An epic carries no Size or Layer.** Its sub-issues carry them, so the
  epic's own would only repeat theirs or contradict them. It carries a
  milestone only when every sub-issue sits in one, as [#418][i418] does
  in M8: the *Milestone* view draws sub-issues under their parent, so an
  epic with no milestone pulls them into the *No milestone* group. Where
  its sub-issues span milestones, it carries none.

So an issue missing a field is a gap unless it is one of these. A new
Layer option for the introspection issues was weighed and declined: it
would describe nothing else.

Role and type stay labels, and the board does not repeat them.

**Four views, each for one question.** *Milestone* is a table of open
items grouped by milestone, with Size and Layer: what each milestone still
holds. *Status* is a board by Status: what is in flight. *Needs You* is
the issues labelled `needs-answers`, `needs-shaping`, or `needs-action`:
what waits on the operator. *Bugs* is the open bugs. There is no Roadmap
layout ([dotagents#2322][da2322]).

**`needs-action` is waiting on the operator to do something** — a run, a
setting, an approval — where `needs-answers` waits on an answer and
`needs-shaping` on requirements not yet settled. The operator asked for it
([dotagents#2358][da2358]).

**Milestones are numbered `M0`, `M1`, and on.** Each description is a
one-line goal and a *Done when:* line, and each milestone carries an
introspection issue reporting to [dotagents#2312][da2312].

**Not on the board:** closed history from before v0.12.0, and ICEBOX
entries ever, since a deferral is not an issue (above).

**Workflows are changed in the web UI.** The API can read them and delete
one, but cannot turn one on or set what it does. **Views can be changed
over the API**: GraphQL's `updateProjectV2View` takes a view's name,
layout, filter, and configuration, and set the *Needs You* filter on
2026-10-30.

**Set Size and Layer over REST, not GraphQL.** Projects v2 has REST
endpoints for a user's project too: `/users/harleypig/projectsV2/4/items`
and `.../fields` read the board, and a `PATCH` on one item sets its
fields, a single-select value by its option's ID. REST spends the core
budget; GraphQL's has run out four times in M1 and M2, so where GraphQL is
needed, send several mutations as one aliased request
([dotagents#2359][da2359]).

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

- **`v1.y.z`, strict semver from `1.0.0` on.** The version is a
  compatibility promise: a break ships only in a new major version, `y`
  marks an addition, and `z` a fix. What the promise covers, and what stays
  free to change, is [RELEASING.md][promise] *What v1 promises*; read it
  there rather than here.
- **No API-major alignment.** The sibling provider aligns its MAJOR to the
  MXroute REST API's major, because it is a client of a versioned API. That
  does **not** carry over: this tool speaks ManageSieve (RFC 5804) and IMAP,
  standardized protocols with no vendor version to track. Do not import the
  provider's bump policy.
- **A tag publishes nothing.** There is no release pipeline and no registry —
  a tag is a marker on history, so it is cheap and carries no
  cannot-be-unpublished risk. See [RELEASING.md](../RELEASING.md).
- **The version is written in one place: `pyproject.toml`.** A release
  bumps that file and nothing else. `mailctl/__init__.py` reads
  `__version__` back from the installed metadata with
  `importlib.metadata.version("mailctl")`, falling back to `0+unknown`
  when nothing is installed, and `tests/test_version.py` fails if the two
  ever disagree ([#106][i106]). An editable install records the version at
  install time, so a bump shows in `mailctl --version` only after
  `uv pip install -e .` is run again.

[adr2]: ../adr/0002-non-destructive-script-merge.md
[i219]: https://github.com/harleypig/mailctl/issues/219
[provider]: https://github.com/harleypig/terraform-provider-mxroute
[adr8]: ../adr/0008-json-rpc-api-interface-for-out-of-process-clients.md
[i137]: https://github.com/harleypig/mailctl/issues/137
[adr7]: ../adr/0007-interfaces-utilities-session-provider-layering.md
[i145]: https://github.com/harleypig/mailctl/issues/145
[icebox-retention]: ../ICEBOX.md#per-folder-retention--expire-mail-after-n-days
[i149]: https://github.com/harleypig/mailctl/issues/149
[i148]: https://github.com/harleypig/mailctl/issues/148
[i154]: https://github.com/harleypig/mailctl/issues/154
[i153]: https://github.com/harleypig/mailctl/issues/153
[i280]: https://github.com/harleypig/mailctl/issues/280
[i349]: https://github.com/harleypig/mailctl/issues/349
[i364]: https://github.com/harleypig/mailctl/issues/364
[i373]: https://github.com/harleypig/mailctl/issues/373
[adr1]: ../adr/0001-standalone-cli-over-provider-resource.md
[board]: https://github.com/users/harleypig/projects/4
[i277]: https://github.com/harleypig/mailctl/issues/277
[i278]: https://github.com/harleypig/mailctl/issues/278
[i287]: https://github.com/harleypig/mailctl/issues/287
[i418]: https://github.com/harleypig/mailctl/issues/418
[da2322]: https://github.com/Harleypig-LLC/dotagents/issues/2322
[da2358]: https://github.com/Harleypig-LLC/dotagents/issues/2358
[da2312]: https://github.com/Harleypig-LLC/dotagents/issues/2312
[da2359]: https://github.com/Harleypig-LLC/dotagents/issues/2359
[da495]: https://github.com/harleypig/dotagents/issues/495
[promise]: ../RELEASING.md#what-v1-promises
[i106]: https://github.com/harleypig/mailctl/issues/106
