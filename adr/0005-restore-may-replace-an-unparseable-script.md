# 5. `restore` may replace a script mxfilter cannot parse

- Status: accepted
- Date: 2026-09-27
- Note: the tool is now `mailctl` (#45); this record keeps its old name.

## Context

[ADR 0002](0002-non-destructive-script-merge.md) makes an unparseable active
script a **hard stop**: merging into it is refused, because the tempting
recovery — *then just write ours* — turns a visible error into silent data
loss, and does so at the moment it is most likely.

`mxfilter restore` ([#13][i13]) is the one write path that does not merge. It
uploads a backup file, byte for byte, over the active script. So it meets the
same question from the other side: may it replace a script that mxfilter
cannot parse? Inheriting 0002's hard stop unexamined would say no.

## Decision

**Yes.** `restore` replaces the active script whether or not mxfilter can
parse what is there now.

0002's stop protects rules from being **lost**. Restore loses nothing: before
anything is sent, the current script is written to the backup directory as
the server's exact bytes, the same backup every upload takes. And recovering
from a broken script — one a hand edit or another client left unparseable —
is precisely what someone reaching for `restore` is most likely to be doing.
Refusing that case would make the command fail at its main job while
protecting nothing the backup does not already protect.

The rest of the write-path discipline is kept, not relaxed:

- the **raw** diff between the file and the server's script is shown first —
  not the normalized diff a merge shows, because restore uploads the file's
  exact bytes and every difference in it is real;
- the server validates the file with `CHECKSCRIPT` before it is stored;
- a confirmation is required (`--yes` to skip it, `--dry-run` to stop after
  the diff);
- only the **active** script is written; no other stored script is touched.

## Consequences

- `restore` removes any rule added since the backup was taken, including one
  made in the panel. The diff shows every such rule as removed, and the
  confirmation is where that is caught.
- 0002's hard stop is unchanged for every path that edits the parsed script
  (`add`, `from-message`, `remove-rule`, `move-rule`). This ADR is an
  exception for the one path that does not merge, not a relaxation of that
  rule.

[i13]: https://github.com/harleypig/mailctl/issues/13
