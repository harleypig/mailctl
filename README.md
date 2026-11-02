# mailctl

[![Tests][badge-ci]][ci]
[![License: MIT][badge-license]](#license)
[![Version][badge-version]][tags]
[![Python][badge-python]][pyproject]

mailctl manages the filters and the mail on one mailbox:

- **Filters that reach old mail too.** Merge a filter into the account's
  active filter set without disturbing the filters already there, and apply
  the same criteria to the mail already delivered.
- **Mail and folders.** Find, count, read, and mark messages, and manage the
  folders they are filed into.
- **Built for IMAP.** It works with mail providers that offer IMAP, and
  ManageSieve for saved filters. MXroute is the one provider it supports
  today (see [MXroute specifics][mxroute]); others would be added as new
  providers, and what you see stays the same.
- **Every operation, three ways.** The `mailctl` command line, a JSON-RPC
  [API][api], and a [terminal UI][tui]. The API is there so other front
  ends, such as a GUI or a web UI, can be built on it, as the terminal UI
  is.

## Why this exists

A server-side filter only ever sees **new** mail. Write a filter in webmail
and every message already in the mailbox stays exactly where it was. mailctl
does both halves, as two commands that take the same criteria: `filter add`
saves the filter, and `filter apply` acts on the mail already there.

It exists for a second reason too: webmail's filter and search screens are
too restricted, and mailctl is meant to replace them, not work within them.
Its criteria go further — any header, text in the body, whole-value and
wildcard comparisons, and, for mail already delivered, dates and read or
flagged state — and it can list, count, sort, and read the mail, so the
message a filter should catch can be found without opening webmail at all.
Filters you made in webmail are kept when mailctl changes the filter set.

Its scope is managing the filters and settings on one mailbox, by hand or
from a script: filters and their order, folders and their subscriptions,
message flags. It is not a mail client. It does not compose or send, and it
names an attachment but never saves or opens it. Forwarding is MXroute's
panel's job, since MXroute disables Sieve `redirect` (see [MXroute
specifics][mxroute]).

## Installation

For now, the only way to get mailctl is to clone this repository and
install from the checkout, so it takes a little more effort than a
published package would. You need Python 3.11 or newer, and a mail account
on MXroute.

```bash
git clone https://github.com/harleypig/mailctl.git
cd mailctl
pipx install .
```

With [uv][uv] instead of [pipx][pipx]: `uv tool install .`

Either puts `mailctl` on your `PATH`. The terminal UI is installed the same
way, from its own directory, beside mailctl:

```bash
pipx install ./clients/tui      # or: uv tool install ./clients/tui
```

Or build a container image that carries all of it with `make image`; how
to run it is in [docs/DOCKER.md][docker].

## Configuration

Create `~/.config/mailctl/config.toml` (or `$XDG_CONFIG_HOME/mailctl/` if
you set it) with your server, your full email address, and where mailctl
gets the password:

```toml
host = "mail.example-server.mxrouteXX.com"   # your MXroute server
user = "you@yourdomain.com"                  # the full address
password_cmd = "pass show email/you@yourdomain.com"
```

The password is never written in this file. Give a command that prints it,
as above, or `password_file` naming a file only you can read; with neither,
mailctl asks for it. Every other setting is in
[docs/CONFIGURATION.md][configuration].

## Quick start

These only read your mail and filters; nothing changes:

```bash
mailctl folder list               # your folders
mailctl mail search --limit 10    # the ten newest messages in INBOX
mailctl mail senders --top 10     # who sends you the most mail
mailctl filter list               # the filters you already have
```

Then a filter. Look first, save it, and apply it to the mail already
there:

```bash
# Show what would change, without changing it. --create-folder makes
# Lists/News if you do not have it yet.
mailctl filter add --from newsletter@example.com --move-to Lists/News \
    --create-folder --dry-run

# Save the filter. It acts on new mail only.
mailctl filter add --from newsletter@example.com --move-to Lists/News \
    --create-folder

# Move the matching mail already delivered. It shows the messages and asks
# before moving any.
mailctl filter apply --from newsletter@example.com --move-to Lists/News
```

Before changing a mailbox you care about, [docs/VERIFYING.md][verify] walks
from commands that touch nothing to ones that move mail. Every command, by
task, is in [docs/USAGE.md][usage].

## Documentation

[docs/README.md][docs] is the index: configuration, usage, safety, output
for scripts, the API, the terminal UI, Docker, MXroute specifics, known
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
[pipx]: https://pipx.pypa.io/
[pyproject]: pyproject.toml
[releasing]: RELEASING.md
[sievelib]: https://github.com/tonioo/sievelib
[tags]: https://github.com/harleypig/mailctl/tags
[textual]: https://github.com/Textualize/textual
[tui]: docs/TUI.md
[usage]: docs/USAGE.md
[uv]: https://docs.astral.sh/uv/
[verify]: docs/VERIFYING.md
