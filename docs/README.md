# mailctl documentation

The [top-level README][readme] says what mailctl is, how to install it, and
gives a first example. Everything else is here.

**Before the first run against a real mailbox, work through
[VERIFYING.md][verify].** It is an ordered ladder from commands that touch
nothing to ones that move mail, and it is where the unconfirmed assumptions
in [MXRoute specifics][mxroute] get settled for your account.

## Using mailctl

* [CONFIGURATION.md][configuration] — where settings come from and how they
  rank: the env file, folder and provider, the password, turning off a Sieve
  extension, and coming from `mxfilter`.
* [USAGE.md][usage] — every command, by task: checking the servers, finding
  mail, saving and applying a filter, marking messages, folders, the filter
  set, backups, and watching the servers for change.
* [SAFETY.md][safety] — what mailctl does before and instead of changing
  anything, and what it refuses.
* [LIMITS.md][limits] — what mailctl cannot do, and why.
* [DOCKER.md][docker] — the image: building it, running the command line,
  the API, and the terminal UI in it.

## Programs and scripts

* [SCRIPTING.md][scripting] — `--json`, its documents, errors, and exit
  status.
* [API.md][api] — the JSON-RPC API for a program in any language.
* [ERROR-CODES.md][codes] — every error code, what it means, and the fields
  it carries.
* [TUI.md][tui] — the terminal UI, the API's first client. Installing it
  for development and its keys are in [clients/tui/README.md][tui-readme].

## The provider and the scope

* [MXROUTE.md][mxroute] — what is documented, observed, and unknown about
  MXRoute, which script mailctl edits, and a filtering stage it cannot see.
* [MAIL-READER-REQUIREMENTS.md][reader] — what a mail reader must, should,
  and may do, and where mailctl stands on each.

[api]: API.md
[codes]: ERROR-CODES.md
[configuration]: CONFIGURATION.md
[docker]: DOCKER.md
[limits]: LIMITS.md
[mxroute]: MXROUTE.md
[readme]: ../README.md
[reader]: MAIL-READER-REQUIREMENTS.md
[safety]: SAFETY.md
[scripting]: SCRIPTING.md
[tui]: TUI.md
[tui-readme]: ../clients/tui/README.md
[usage]: USAGE.md
[verify]: VERIFYING.md
