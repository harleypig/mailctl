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
- **MXroute today, other providers later.** It works with an MXroute
  mailbox now. Support for other mail providers can be added, and the
  commands you use stay the same.
- **The command line, and more.** Everything is a `mailctl` command, so it
  can be scripted. There is also a terminal UI, and an API other programs,
  such as a desktop or web app, can be built on.

## Why this exists

A server-side filter only ever sees **new** mail. Write a filter in webmail
and every message already in the mailbox stays exactly where it was. mailctl
does both halves, as two commands that take the same criteria: `filter add`
saves the filter, and `filter apply` acts on the mail already there.

It exists for a second reason too: webmail's filter and search screens are
too restricted, and mailctl is meant to replace them, not work within them.
Filters you made in webmail are kept when mailctl changes the filter set.

## Installation

You need Python 3.11 or newer, and a mail account on MXroute. mailctl is
installed from a copy of this repository:

```bash
git clone https://github.com/harleypig/mailctl.git
cd mailctl
pipx install .        # or, with uv: uv tool install .
mailctl --version     # prints the version if the install worked
```

The terminal UI and a Docker image are optional; see
[docs/TUI.md][tui] and [docs/DOCKER.md][docker].

## Configuration

Create `~/.config/mailctl/config.toml` (or `$XDG_CONFIG_HOME/mailctl/` if
you set it) with your server, your full email address, and where mailctl
gets the password:

```toml
host = "mail.example-server.mxrouteXX.com"   # your MXroute server
user = "you@yourdomain.com"                  # the full address
password_cmd = "pass show email/you@yourdomain.com"
```

Your server's name is the same as your domain's primary MX record, and
MXroute's panel shows it on the Email Clients page.

Leave the password settings out and mailctl asks for the password each
time. To skip the question, give a command that prints it, as above, or
`password_file` naming a file only you can read. The password itself is
never written in this file. Every other setting is in
[docs/CONFIGURATION.md][configuration].

## Quick start

First check that mailctl can reach your account. This shows each setting
and where it came from, then connects; it changes nothing:

```bash
mailctl config test
```

These only read your mail and filters; nothing changes:

```bash
mailctl folder list                      # your folders
mailctl mail search --limit 10           # the ten newest messages in INBOX
mailctl mail senders --since 2026-10-01  # who sent you the most since then
mailctl filter list                      # the filters you already have
```

`mail senders` counts at most 5000 messages, so `--since` keeps it to
recent mail; pick your own date.

### When you want to change something

Before your first change to a mailbox you care about,
[docs/VERIFYING.md][verify] takes you from commands that touch nothing to
ones that move mail.

A filter is three steps: look first, save it, then apply it to the mail
already there:

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

Every command, by task, is in [docs/USAGE.md][usage].

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
make teste2e ROUTE=pipx  # installed by pipx, uv, or docker, then run
```

`make` alone formats, lints, type-checks, and tests. Changes land through a
pull request against `master`; [RELEASING.md][releasing] covers tags and
what v1 promises.

## License

MIT. The full text is in [LICENSE](LICENSE).

## Acknowledgements

mailctl is built on [sievelib][sievelib] for ManageSieve and Sieve, and
[IMAPClient][imapclient] for IMAP; the terminal UI on [Textual][textual].

[badge-ci]: https://github.com/harleypig/mailctl/actions/workflows/test.yml/badge.svg
[badge-license]: https://img.shields.io/badge/license-MIT-blue.svg
[badge-python]: https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fharleypig%2Fmailctl%2Fmaster%2Fpyproject.toml
[badge-version]: https://img.shields.io/github/v/tag/harleypig/mailctl?label=version
[ci]: https://github.com/harleypig/mailctl/actions/workflows/test.yml
[configuration]: docs/CONFIGURATION.md
[docker]: docs/DOCKER.md
[docs]: docs/README.md
[imapclient]: https://github.com/mjs/imapclient
[pyproject]: pyproject.toml
[releasing]: RELEASING.md
[sievelib]: https://github.com/tonioo/sievelib
[tags]: https://github.com/harleypig/mailctl/tags
[textual]: https://github.com/Textualize/textual
[tui]: docs/TUI.md
[usage]: docs/USAGE.md
[verify]: docs/VERIFYING.md
