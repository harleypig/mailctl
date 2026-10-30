# mailctl-tui

A terminal UI for mailctl. It starts the `mailctl-api` command and drives
mailctl through it, so it shows only what mailctl's API returns. Why it is
built this way is [ADR 0009](../../adr/0009-python-textual-tui-as-a-separate-package.md).

It shows the filters in the active filter set, in the order they run,
what each one does, and the filters that cannot fire where they are. From
there you can search a folder, read a message, and add a filter for mail
like it:

1. **Search** a folder, or leave the folder empty for your default one,
   optionally for messages whose From or Subject holds some text.
2. **Read** a message by choosing it. Nothing is marked read.
3. **Add a filter like it.** mailctl fills in what identifies the message,
   its mailing list or else its sender, and you choose what the filter
   does: the folder to move it to, and whatever else your mail host
   offers. Only the options your host supports are on the form.
4. **See what would change** before anything does: the change to the
   filter set, any filter that would stop the new one from running, and
   the folder it would create.
5. **Save it, or cancel.** Saving backs the filter set up first and shows
   where the copy went. Mail already delivered is left alone; the filter
   acts on new mail.

An alert from the mail server is shown as it arrives, whichever screen
is up. Where mailctl has no password configured, the TUI asks for it in a
masked field and hands it to mailctl; it keeps no copy.

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

| Screen | Key | Does |
|--------|-----|------|
| Filters | `r` | Reads the filters again |
| Filters | `s` | Searches mail |
| Filters | `q` | Quits |
| Search | `Enter` | Searches, or reads the chosen message |
| Message | `a` | Adds a filter for mail like this one |
| Add a filter | `Ctrl+S` | Shows what would change |
| What would change | `y` | Saves it |
| What would change | `Enter` | Goes back to the filters once saved |
| Any but the filters | `Escape` | Goes back, or cancels |

## Test

```sh
make testtui
```

That type-checks the package and runs its tests, which need mailctl
installed in the same `.venv`: one test drives the real API.
