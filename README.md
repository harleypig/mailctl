# mailctl

[![Tests][badge-ci]][ci]
[![License: MIT][badge-license]](#license)
[![Version][badge-version]][tags]
[![Python][badge-python]][pyproject]

Read, sort, and filter the mail on your account from the command line: build
a filter from criteria flags, merge it into the account's **active** filter
set without disturbing the filters already there, and apply the same criteria
to the mail that has already been delivered. It also finds, counts, reads,
and marks that mail, and manages the folders it is filed into. MXRoute is
the provider it speaks to today (see [MXRoute specifics][mxroute]); what it
shows you is the same whichever provider it is.

## Why this exists

A server-side filter only ever sees **new** mail. Write a filter in webmail and
every message already in the mailbox stays exactly where it was. mailctl
does both halves, as two commands that take the same criteria: `add` saves
the filter, and `apply` acts on the mail already there.

It exists for a second reason too: the filter and search screens in
Roundcube, the webmail MXRoute ships, are too restricted, and mailctl is
replacing them rather than working within them. mailctl's
criteria go further — any header, text in the body, whole-value and
wildcard comparisons, and, for mail already delivered, dates and read or
flagged state — and it can list, count, sort, and read the mail, so the
message a filter should catch can be found without leaving the terminal. The
filters it writes are in the form webmail uses, so they show up there, and
the filters you made in webmail are kept ([ADR 0002][adr2]).

Its scope is managing the filters and settings on one mailbox, by hand or
from a script: filters and their order, folders and their subscriptions,
message flags. It is not a mail client. It does not compose or send, and it
names an attachment but never saves or opens it. Forwarding is MXRoute's
panel's job, since MXRoute disables Sieve `redirect` (see [MXRoute
specifics][mxroute]).

Besides the command line there is a JSON-RPC [API][api] for programs in any
language, and a [terminal UI][tui] built on it.

## Installation

You need Python 3.11 or newer and a mail account on a host mailctl speaks
to (MXRoute today). Nothing is published, so install from a checkout:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/mailctl --version
```

To run it from a container image instead, see [docs/DOCKER.md][docker]; for
the terminal UI, see [docs/TUI.md][tui].

## Quick start

**Before the first run against a real mailbox, work through
[docs/VERIFYING.md][verify].** It is an ordered ladder from commands that
touch nothing to ones that move mail, and it is where the unconfirmed
assumptions in [MXRoute specifics][mxroute] get settled for your account.

```bash
# Check both services and what they support. Changes nothing.
mailctl server test

# See exactly what would change, without changing it.
mailctl filter add --from newsletter@example.com --move-to Lists/News --dry-run

# Save the filter: merge it, back up, upload, activate. It filters new mail
# only; the mail already delivered is left alone.
mailctl filter add --from newsletter@example.com --move-to Lists/News

# Then act on the mail already there, with the same criteria and actions.
# It previews the messages and asks before it moves any.
mailctl filter apply --from newsletter@example.com --move-to Lists/News
```

Every command, by task, is in [docs/USAGE.md][usage].

## Configuration

Copy `config.toml.example` to `$XDG_CONFIG_HOME/mailctl/config.toml` and fill
it in, or copy `.env.example` to `.env` and pass `--env-file`:

```toml
host = "mail.example-server.mxrouteXX.com"
user = "you@yourdomain.com"
password_cmd = "pass show email/you@yourdomain.com"
default_folder = "Lists"
```

Every setting, the password's sources, and how they rank are in
[docs/CONFIGURATION.md][configuration].

## Documentation

[docs/README.md][docs] is the index: configuration, usage, safety, output
for scripts, the API, the terminal UI, Docker, MXRoute specifics, known
limits, and the error codes.

## Contributing

Work happens in a checkout, inside a `.venv` at its root that `make
install` creates with `uv`:

```bash
make install    # the package and its dev tools, into .venv
make test       # the offline test suite
make testtui    # the terminal UI's type-check and tests
```

`make` alone formats, lints, type-checks, and tests. Changes land through a
pull request against `master`; [RELEASING.md][releasing] covers tags and
what v1 promises.

## License

MIT.

## Acknowledgements

mailctl is built on [sievelib][sievelib] for ManageSieve and Sieve, and
[IMAPClient][imapclient] for IMAP; the terminal UI on [Textual][textual].

[adr2]: adr/0002-non-destructive-script-merge.md
[api]: docs/API.md
[badge-ci]: https://github.com/harleypig/mailctl/actions/workflows/test.yml/badge.svg
[badge-license]: https://img.shields.io/badge/license-MIT-blue.svg
[badge-python]: https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fharleypig%2Fmailctl%2Fmaster%2Fpyproject.toml
[badge-version]: https://img.shields.io/github/v/tag/harleypig/mailctl?label=version
[ci]: https://github.com/harleypig/mailctl/actions/workflows/test.yml
[configuration]: docs/CONFIGURATION.md
[docker]: docs/DOCKER.md
[docs]: docs/README.md
[imapclient]: https://github.com/mjs/imapclient
[mxroute]: docs/MXROUTE.md
[pyproject]: pyproject.toml
[releasing]: RELEASING.md
[sievelib]: https://github.com/tonioo/sievelib
[tags]: https://github.com/harleypig/mailctl/tags
[textual]: https://github.com/Textualize/textual
[tui]: docs/TUI.md
[usage]: docs/USAGE.md
[verify]: docs/VERIFYING.md
