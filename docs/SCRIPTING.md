# Output for scripts

`--json` prints a command's result as one JSON document on stdout, and nothing
else there: whatever the command says on the way — progress, a server's alert,
the message `--like` read — goes to stderr. It is offered where the output is
data — `mail search`, `mail view`, `mail senders`, `folder list`, `filter
list`, `filterset list`, `server baseline check`, `server baseline show` — and
on every write command's plan, where it needs `--dry-run`, since a document
on stdout leaves no room for a prompt. `filterset backup`, `server baseline
save`, and `config migrate`, which write only local files, have none.
`server test` is a report for a person and has none; its contract is its
exit status. `server probe --json` prints its own versioned document (see
[*Check the servers*][check]), with the same stdout and error handling. `mail search
--uids-only` prints the matching UIDs, one per line, and nothing else.

| Command | Document |
|---------|----------|
| `filterset list` | `{"version", "filtersets": [{"name", "active"}]}` |
| `folder list` | `{"version", "delimiter", "prefix", "folders": [{"name", "subscribed"}]}`; with `--counts`, each folder also has `"messages", "unseen", "size"` (`null` where the server gave none) |
| `mail search` | `{"version", "folder", "uidvalidity", "more", "sort", "messages": [{"uid", "received", "size", "flags", "has_attachments", "from", "subject", "folder"}]}` |
| `mail senders` | `{"version", "folder", "by", "messages", "unread", "groups", "senders": [{"key", "name", "total", "unread", "unread_percent"}]}` — busiest first; `messages`, `unread`, and `groups` count everything matched, rows `--top` and `--min` left out included; `key` is `null` for mail with no address or no List-Id |
| `mail view` | `{"version", "message": {"uid", "folder", "uidvalidity", "size", "flags", "headers": [{"name", "value"}], "body", "body_from_html", "attachments": [{"name", "content_type", "size"}]}}` |
| `filter list` | `{"version", "filterset", "filters": [{"position", "name", "disabled", "stops", "combinator", "tests", "actions", "steps": [{"kind", "argument", "creates"}], "unmodelled"}], "findings": [{"certainty", "broad", "narrow", "reason"}]}` |
| a write, `--dry-run` | `{"version", "plan": {"command", "changes", ...}}` — `command` is the command's path, such as `"filter add"`; what else a plan holds depends on the command |
| `server baseline check` | `{"version", "host", "baseline", "baseline_taken", "taken", "account_recorded", "requires_known", "serious", "informational", "drift": [{"severity", "kind", "half", "name", "before", "after"}]}` |
| `server baseline show` | the saved file as stored (see [*Watch the servers for change*][watch]) |
| any, failing | `{"version", "error": {"message", "code", "fields"}}`, one line, the last on stderr; `code` names the refusal, and is absent only for an interrupted run or an unexpected failure; `fields` holds the values the refusal carries, such as the `setting` a `setting_invalid` names, and is absent where it carries none |

* `version` is `2`. A key may be added without changing it; one renamed,
  removed, or given a new meaning changes it.
* Values are whole: nothing is clipped, flags are IMAP's own (`\Seen`),
  and text outside printable ASCII is escaped as `\uXXXX`.
* `received` is ISO 8601 in local time, with no offset, because the
  server's date reaches mailctl without one.
* A plan's `changes` is `false` where the command would do nothing; its
  `diff`, on a filter set change, is then `null`. A `folder rename` plan
  always changes something; its `diff` is `null` when no filter files into
  the folder, and the filter set is then left alone.
* A plan that uploads a filter set names it as `filterset`, beside `active`
  and `activate`, and a plan for one filter names it as `filter`. A
  `filterset restore` plan counts `filters_before` and `filters_after`; a
  `folder rename` plan lists the filters it repoints as `filters`
  (`{"filter", "old", "new"}`). A plan's folder `status` is `none`, `exists`, `missing`,
  `create` (mailctl creates it), `created-on-delivery` (the filter creates
  it as mail arrives), `create-and-on-delivery` (both), or
  `uncreatable`. `diff.label` names the language the diff's text is in
  (`sieve` on MXRoute).
* A `filter optimize` plan lists `removals` (`{"filter", "covered_by"}`),
  `reorders` (`{"filter", "before"}`), `merges` (`{"into", "absorbed",
  "header", "match_type", "keys"}`), and `uncertain` (`{"kind", "filters",
  "reason"}`), what was left alone for want of certainty; `considered`
  names the kinds of change looked for.
* An `apply` plan's `mail` is `null` where the actions leave delivered mail
  as it is. Otherwise it holds `source`, `uidvalidity`, `destination`,
  `flags`, `discard`, `moves`, `copies`, `count`, `held`, `unidentified`,
  and `messages` (each as in `search`). `copies` is `true` for `--keep` with a
  folder; `held` is the UIDs the folder already has by Message-ID, which
  are not copied again, and `unidentified` the UIDs with no Message-ID,
  which are copied regardless.
* A `search` listing's `sort` is `null` for newest first, else
  `{"key", "reverse"}` as `--sort` and `--reverse` gave them, and
  `messages` are in that order. `more` is `true` when there may be
  matches past `--limit`.
* `uidvalidity` is what the UIDs are valid under in that folder, `null`
  where the server reports none; `search`, `view`, and the `mark` and
  `apply` plans carry it. Pass it back as `--uidvalidity` to have a UID
  checked before it is used.
* `search --build-filter --json` prints a filter document instead (see
  [*Filter documents*][filterdoc]).
* A `filter list` filter's `actions` are the host's own names for what it
  does (`fileinto`, `setflag` on MXRoute); its `steps` say the same in
  words that are the same for every provider, one step per action, in
  order. `kind` is `file`, `discard`, `flag`, `unflag`, `keep`, `stop`, or
  `other`, an action with no such word, whose `argument` is then its name
  as the filter set writes it. `creates` is whether a `file` step also
  creates its folder. In a listing `argument` is otherwise `null` and
  `creates` is `false`, since a filter is read back by its action names
  alone: the same limit as the text listing's *files the message into a
  folder*.

**An error's `code`** is a short name for the condition, so a script can
tell one refusal from another without reading the message. Every refusal
carries one, the command line's own included; an interrupted run and an
unexpected failure do not. [docs/ERROR-CODES.md][codes] lists every code,
what it means, and the `fields` that come with it.

**Exit status**, on every command:

| Status | Means |
|--------|-------|
| 0 | done, including "nothing to change" |
| 1 | a failure or a refusal, with the reason on stderr (one JSON line under `--json`) |
| 2 | the command line was rejected before anything ran — an unknown command or option, a value that cannot be read, `--no-subscribe` without `--create-folder`; the usage and the reason are text, and under `--json` one coded JSON line follows them |
| 3, 4 | `server baseline check` only: informational, or serious, drift |
| 130 | interrupted |

An option is matched by its whole name, never by a prefix: `--jso` is
refused rather than taken as `--json`. A rejected command line never
quotes a word mailctl could not use (see [*The password*][password]).

**From v1.0.0 these are a promise.** The documents, the error codes, and
the exit statuses above, with the API below, are kept for every 1.x
release; what is kept, and what may still change, is in
[RELEASING.md › *What v1 promises*][promise].

[codes]: ERROR-CODES.md
[promise]: ../RELEASING.md#what-v1-promises
[check]: USAGE.md#check-the-servers
[filterdoc]: USAGE.md#filter-documents
[password]: CONFIGURATION.md#the-password
[watch]: USAGE.md#watch-the-servers-for-change
