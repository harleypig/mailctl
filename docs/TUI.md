# TUI

`mailctl-tui` is a terminal UI for mailctl, and the API's first client. It
starts `mailctl-api` and shows only what the API returns. It is a package
of its own, in `clients/tui/`, so installing mailctl never pulls in its
terminal library; why it is built this way is [ADR 0009][adr9].

It shows **the filters** in the active filter set, in the order they run,
what each one does, and the ones that cannot fire where they are. From
there it can find a message and make a filter for mail like it:

* **Search** (`s`) a folder, or your default one when the field is left
  empty, optionally for mail whose From or Subject holds some text.
* **Read** a message by choosing it with Enter. It stays unread, and an
  attachment is named, never saved.
* **Add a filter like it** (`a`). mailctl fills the criteria in from the
  message, its List-Id, else its From, as `mail search --like` does, and
  you choose what the filter does. The form offers only what your
  provider declares: where the filter goes, letting later filters see the
  mail, showing a new folder in webmail, and each action.
* **See what would change** (`Ctrl+S`): what the filter matches and does,
  any filter that would keep it from firing, the folder it would create,
  and the diff. `y` saves it, backing the filter set up first and saying
  where; Escape cancels, and nothing is saved. The mail already
  delivered is left alone; `mailctl filter apply` acts on that.

`r` reads the filters again, Escape goes back a screen, and `q` quits; the
keys on each screen are in [clients/tui/README.md][tui]. An alert from the
mail server is shown as it arrives, whichever screen is up. Where mailctl
has no password configured, the TUI asks for it in a masked field, hands
it to mailctl, and keeps no copy.

```bash
uv tool install .                   # mailctl, for the mailctl-api command
uv tool install ./clients/tui       # the TUI
mailctl-tui
mailctl-tui --env-file ~/.config/mailctl/work.env
```

The TUI runs the `mailctl-api` it finds on `PATH`, so mailctl has to be
installed as well. It takes mailctl's settings from the same places: any
argument it does not know itself is passed on to `mailctl-api`, as
`--env-file` is above. `--api COMMAND` starts another command in place of
`mailctl-api`.

Installing it for development, and `make testtui`, which type-checks it
and runs its tests, are in [clients/tui/README.md][tui].

[adr9]: ../adr/0009-python-textual-tui-as-a-separate-package.md
[tui]: ../clients/tui/README.md
