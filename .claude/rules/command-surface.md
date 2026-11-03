---
paths:
  - "mailctl/cli.py"
  - "mailctl/json_output.py"
  - "mailctl/criteria.py"
  - "mailctl/api/**"
  - "tests/test_cli*.py"
  - "tests/snapshots/**"
  - "tests/cli_support.py"
  - "docs/**"
---

# mailctl Conventions — the command surface

Path-scoped detail moved out of `.claude/CONVENTIONS.md`, which stays always
loaded as the short core; read the core's *Where the detail lives* for
the whole map. Claude Code loads this file only when a file matching
the `paths:` above is read or edited.

## The command surface

The decision, *Read and write are separate*, the group tree, and the
user-facing noun are in the core's own *The command surface*.

**No option is taken by a prefix of its name** (`allow_abbrev=False`, in
the CLI's parsers and the API's): a script's `--jso` working today would
stop the day an option sharing the prefix is added ([#352][i352]).

**A command line is refused through `cli.Parser`.** mailctl's own
refusals go through `Parser.refuse`, in words that quote nothing typed
but a command or an option's name; argparse's go through `Parser.error`,
which words them by `json_output.rejection` (*Credentials*). Either way
the exit is 2 after argparse's usage block, and the refusal carries a
code: `invalid_arguments` unless it has one of its own, such as
`gone_command`, `gone_option`, or `setting_invalid` for a bad value given
to a connection flag ([#352][i352], [#364][i364]).

**`--provider` and `--env-file` may come before the command, between a
group and its action, or after it** ([#243][i243], [#265][i265]); given
more than once, the later wins. Before the command or its action,
`--env-file` needs its PATH: a bare one would take the next word of the
command as its file, so that is refused, naming it. `config migrate` reads
no settings, and refuses either given anywhere before `migrate`.

**`filter add` saves a filter; `filter apply` acts on mail already
delivered.** `filter add` writes the rule into the active script and touches
no message; `filter apply` is the only command that acts on existing mail
([#149][i149]). So `--max-messages` and `--move-threshold` are `filter
apply`'s and not `filter add`'s, `filter add` asks nothing and takes no
`--yes`, and `filter add` ends by saying the mail already there was left alone
and pointing at `filter apply`. Both take their criteria as flags **or** as
`--filter FILE|-` (a filter file, or `-` for stdin), never both — `--match`
and `--compare` count as flags, since the document carries its own — and both
take `--like UID`, which pre-fills the criteria from a message in `--folder`.
`--like` merges with criteria flags exactly as `mail search --like` does, and
is refused with `--filter`, since each supplies the whole set. Every one of
these refusals is made before connecting.

**Dates and message state select delivered mail only** ([#152][i152]).
`--since`, `--before`, `--older-than`, `--unread`, and `--flagged` are
`mail search`, `mail senders`, and `filter apply` criteria, always ANDed with
the rest. `filter add` refuses them before connecting, from flags or a
`--filter` document, because a message being delivered is new, unread, and
unflagged; and `filter add` has no date `--before`, since its `--before NAME`
places the rule. `--body` is in all four, and is always a substring test.

**The split was decided over a recorded objection** (operator, 2026-09-28):
it puts the tool's two halves, the rule and the existing mail, in two
commands. `filter apply` taking the same `--like` and `--filter` as `filter
add` is what keeps the two in step.

**`mail search` finds messages and never writes.**:

- `--like UID` pre-fills the criteria from a message, and `--build-filter
  [--json]` prints the filter those criteria make and saves nothing
  ([#148][i148]). A criteria flag given with `--like` replaces what was
  derived for its own header and adds any other; `--match` and
  `--compare` govern the whole set (`criteria.merge_criteria`). The
  `--json` form is the filter document `filter add --filter` and `filter
  apply --filter` read: versioned, criteria only, written and read by
  `criteria.dump_filter` / `load_filter`, which refuse a version or a key
  they do not know.
- `--raw QUERY` is the escape hatch, a query in the host's own search
  language. Each provider declares it, and it is offered only where
  declared, like every other capability-gated option (*Providers*).

**`filter rename OLD NEW` changes a rule's name and no other byte**
([#216][i216]). The dialect rewrites the name marker where it sits and
reads the result back, so the rule's body, position, and disabled state,
and every other rule and comment, are kept exactly, and the diff is of the
script's own bytes. An unknown OLD, an empty or taken NEW (coded
`rule_name_taken`), and a NEW the dialect cannot read back as itself are
refused before any upload.

**`mail view` stays read-only; `mail mark` is its write twin.** `mail mark
UID...` takes `--read`, `--unread`, `--flag`, `--unflag`, `--keyword K`, and
`--no-keyword K`, several at once, over separate add-flag and remove-flag
transport operations ([#150][i150]).

**A UID from an earlier run can carry its folder's UIDVALIDITY**
([#204][i204]). `mail search` shows the value, and `mail view`, `mail mark`,
and `--like` take it as `--uidvalidity N`, one per command since every UID a
command takes is in its one folder. A mismatch refuses the command, coded
`uidvalidity_changed`, before any UID is used; a bare UID is still taken
unchecked.

**`config test` checks that a configuration works, and changes nothing**
([#440][i440]): `server test`'s settings block and mail section, printed
by the same code, then one line saying whether the filter server
connected. It exits as `server test` does.

**`mail senders` counts a folder's mail by address, domain, or List-Id and
never writes** ([#160][i160]): one search, then headers a page per FETCH,
refused above `--max-messages` (default 5000) before any header is read.

**`--json` is offered where the output is data a script consumes**
([#151][i151]). A report for a person, such as `server test` or `config
test`, offers none, and its layout is not a contract; the contract is its
exit code. The API's `server.test` returns a document of its own instead,
built from the same probes by `utilities.server_test`, never from the CLI's
text. `config.test` returns that document's settings and mail parts, and
`rules` saying only that the filter server connected; one that cannot
connect is an error, coded as `server.test`'s is. On a write
command `--json` prints the `--dry-run` plan and is refused without
`--dry-run`, since a document on stdout leaves no room for a prompt; stdout
holds the document alone, and a failure is one JSON line, the last on
stderr, carrying the refusal's code where it is one, and its `fields`
where it has any: the error document the API answers with, built by
`json_output.error` (`cli.failure_line`). A field that cannot be
serialised drops the fields, never the line or its code ([#374][i374]).
A command line
refused before any command runs is the same: under `--json`, wherever it
stands, exit 2's usage block and error line end with a coded JSON line
([#352][i352]).

[i352]: https://github.com/harleypig/mailctl/issues/352
[i364]: https://github.com/harleypig/mailctl/issues/364
[i243]: https://github.com/harleypig/mailctl/issues/243
[i265]: https://github.com/harleypig/mailctl/issues/265
[i149]: https://github.com/harleypig/mailctl/issues/149
[i152]: https://github.com/harleypig/mailctl/issues/152
[i148]: https://github.com/harleypig/mailctl/issues/148
[i216]: https://github.com/harleypig/mailctl/issues/216
[i150]: https://github.com/harleypig/mailctl/issues/150
[i204]: https://github.com/harleypig/mailctl/issues/204
[i440]: https://github.com/harleypig/mailctl/issues/440
[i160]: https://github.com/harleypig/mailctl/issues/160
[i151]: https://github.com/harleypig/mailctl/issues/151
[i374]: https://github.com/harleypig/mailctl/issues/374
