# mailctl-tui

A terminal UI for mailctl. It starts the `mailctl-api` command and drives
mailctl through it, so it shows only what mailctl's API returns. Why it is
built this way is [ADR 0009](../../adr/0009-python-textual-tui-as-a-separate-package.md).

It shows the filters in the active filter set, in the order they run, and
the filters that cannot fire where they are. An alert from the mail server
is shown as it arrives. Where mailctl has no password configured, the TUI
asks for it in a masked field and hands it to mailctl; it keeps no copy.

## Install

mailctl first, then the TUI, each from a checkout of this repository:

```sh
uv tool install .
uv tool install ./clients/tui
```

Or, for development, into the repository's `.venv` beside mailctl:

```sh
make install
uv pip install -e './clients/tui[dev]'
```

## Run

```sh
mailctl-tui
```

It takes the same connection settings as `mailctl`, from the same places:
the config file, the environment, an env file, or options. Any option it
does not know itself is passed on to `mailctl-api`:

```sh
mailctl-tui --env-file ~/.config/mailctl/work.env
```

`--api COMMAND` starts another command in place of `mailctl-api`.

| Key | Does |
|-----|------|
| `r` | Reads the filters again |
| `q` | Quits |

## Test

```sh
make testtui
```

That type-checks the package and runs its tests, which need mailctl
installed in the same `.venv`: one test drives the real API.
