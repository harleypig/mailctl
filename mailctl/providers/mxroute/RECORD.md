# MXroute: what its documentation says and what a probe saw

This is the `mxroute` provider's record, as [ADR 0006][adr6] requires. It
says what was true of MXroute when it was taken, in the three tiers
[CONVENTIONS.md][conv] › *Confidence* defines:

- **Documented**: MXroute, or the software it runs, says so. Each entry
  gives the URL and the date it was fetched.
- **Observed**: a probe saw it. Each entry gives the probe, the date, and
  the server.
- **Unknown**: nobody has settled it.

Nothing moves up a tier without new evidence. When to refresh the record,
and what a probe is, is CONVENTIONS.md › *Providers are probed and read*.

**This record never decides anything at run time.** The provider still
discovers capabilities, the active script, and the delimiter from the server
(CONVENTIONS.md › *Discover, don't hardcode*). The record is for the people
and agents building and maintaining the provider: it says what the provider
can rely on, what it must still probe, and what it is leaving unused.

| Tier | Last refreshed |
|---|---|
| Documented | 2026-09-27 (every public URL re-fetched; the panel page is behind a login) |
| Observed | 2026-09-29 (one account, `heracles.mxrouting.net`) |

## Documented

### `redirect` is disabled

- **Sieve `redirect` is disabled.** MXroute's own blog says so — *"Why we
  disabled redirect sieve filters on MXroute"* (2024-03-22) — and gives the
  reason: real forwarders "are designed to properly handle SRS".
  - Source: [the blog post][blog-redirect], fetched 2026-09-27. It reads
    *"We've decided to disable the ability for users to create redirect
    sieve filters"* and *"Email forwarders on MXroute are designed to
    properly handle SRS"*.
  - **Date resolved:** on 2026-09-27 the post showed **Mar 22, 2024**, and
    its own metadata gives `2024-03-22T00:00:00.000Z`. This record,
    `mailctl test`'s note, and the refusal message all say 2024-03-22
    ([#102][i102]).

### Connecting to IMAP

- **IMAP 993 / 143, full-email-address username, per-account hostname** (the
  panel's *Email Clients* page).
  - Source: the panel's *Email Clients* page, which is behind a login. Its
    fetch date was not recorded, and it could not be re-fetched on
    2026-09-27. The panel gives the hostname as "the same as your primary MX
    record".
  - Corroborated in part by the public [iOS Mail guide][docs-ios], fetched
    2026-09-27: the incoming server is *"`[your-server-name].mxrouting.net`"*,
    port *"993"*, *"Use SSL"* on, and the username is *"Your full email
    address"*. That page does not mention port 143 or STARTTLS, so 143 rests
    on the panel page alone.

### The REST API has no filter surface

- **The REST API exposes nothing for filters or Sieve.** Verified twice, two
  ways: against the published OpenAPI document (26 paths, none filter-related)
  and against MXroute's own 4.0.1 changelog, which enumerates the API's
  categories in full — Domains, Email Accounts, Forwarders, Spam Settings,
  Catch-All, DNS Information, Quota, Reseller. This is the load-bearing fact
  behind [ADR 0001][adr1].
  - **Wording trap:** that changelog describes *"Forwarders — set up and
    manage email forwarding **rules**"*. "Rules" there means **forwarders**,
    not Sieve rules. Do not read it as filter support and re-open the
    question.
  - Sources: [the OpenAPI document][api-openapi] (`info.version` 1.0.0) and
    [the 4.0.1 changelog][cl-401], both re-fetched 2026-09-27. There were
    still 26 paths, and none mentions Sieve or a mail filter. The only
    "filter" text is the spam-settings description, which is about spam
    scoring.

### Announced changes

MXroute is changing the software under the account. These are what it has
said, most recent first. They are the standing trigger for a refresh
(CONVENTIONS.md › *Providers are probed and read*).

- **MXroute 4.1, in progress** ([changelog][cl-41], fetched 2026-09-27):
  - an in-house webmail *"is now live at webmail.mxroute.com"*;
  - *"Replace Roundcube with In-House Webmail"* and *"Replace Crossbox with
    In-House Webmail"* are **in development**;
  - *"Replace DirectAdmin with the New Panel"* is **in development**, for
    *"customers still using DirectAdmin"*;
  - *"Self-Service Spam Filter Settings"* and a *"User-Configurable Spam
    Folder"*, with a score threshold that triggers the move, are **in
    development**.
- **MXroute 4.0.1** ([changelog][cl-401], fetched 2026-09-27):
  *"chocobo.mxrouting.net"* is the first server without DirectAdmin access,
  and Dovecot 2.4 servers *"have seen improved IMAP performance"*.
- **Dovecot 2.3 → 2.4** ([blog, 2026-03-04][blog-dovecot24], fetched
  2026-09-27): six servers run 2.4, namely *"blizzard.mxrouting.net,
  chocobo.mxrouting.net, fusion.mxrouting.net, sunfire.mxrouting.net,
  taylor.mxrouting.net, and wednesday.mxrouting.net"*, and *"The rest of
  the fleet will be there eventually."* The post describes a quota-reporting
  change and nothing about Sieve, ManageSieve, or IMAP behaviour.
- **The migration plan** ([blog, *Ripping off bandages*,
  2026-07-19][blog-bandages], fetched 2026-09-27): MXroute plans to
  *"migrate away from DirectAdmin, Crossbox, and Roundcube"* this year. The
  post says nothing about filters, Sieve, or Dovecot. It links
  [*The Next Chapter*][blog-next] (2025-12-05, the new control panel) and
  the Dovecot 2.4 post above. The statement was first recorded here without
  a URL, and CONVENTIONS.md › *Discover, don't hardcode* cites it. This
  post and the 4.1 changelog are now its sources.
- **Custom webmail branding** ([custom hostnames][docs-hostnames], fetched
  2026-09-27): the in-house webmail has its own *Custom Webmail Branding*
  guide, which uses the CNAME target `webmail-connect.mxroute.com`. It
  warns that mixing that guide with the custom-hostnames one *"will break
  the in-house webmail"*. The branding guide's own page was not found.
- **Not this webmail:** [*Introducing the New MXroute Webmail
  Upgrade*][blog-2023] (2023-05-11) predates the in-house client and is
  most likely an earlier webmail change. Its body did not load when
  fetched, so that reading is **assumed**.

**Known condition, not news: the in-house webmail exists and Roundcube is
still in place.** As of 2026-09-27, no MXroute source documents the
in-house webmail beyond the lines above. There is no user guide, nothing on
filters, Sieve, or folders, and no date for retiring Roundcube. The
replacement is *in development*, not done. A small team announcing a
multi-phase migration and delivering it over time is the expected state of
this host, and MXroute has been reliable for years. So the webmail being
live is **not** a reason to re-raise the migration as if it had moved.
Re-check only when a new changelog entry or post says a phase finished, or
when mailctl starts relying on webmail behaviour. Operator, 2026-09-27:
*"this is a known condition--storing these details will help prevent this
from coming up again."*

**Two of mailctl's MXroute assumptions are exposed to the webmail change.**
The Roundcube `# rule:[NAME]` dialect the provider writes back is
Roundcube's. And *webmail shows only subscribed folders* is how Roundcube
behaves ([#38][i38]). Whether the in-house webmail keeps either is under
*Unknown*.

### The filtering stage before Sieve

This is documented by DirectAdmin, the software, and not by MXroute. It was
researched on [#30][i30], and the README's *A filtering stage mailctl cannot
see* is the user-facing account of it.

- DirectAdmin's **Email Filters** panel writes an **Exim** filter. It runs
  after Exim has accepted the message and before delivery, which is where
  Sieve runs. A match ends address processing, so Sieve never sees the
  message. DirectAdmin's documentation calls it *"a very basic Exim filter
  that lets you drop messages which Exim has already accepted"*.
- Its rules are **block-only**. `type` is `domain`, `email`, `word`, or
  `size`, and `where` is `inbox`, `delete`, `spamfolder`, or
  `userspamfolder`.
- MXroute's own `enable_spamd.sh` writes `where=delete`, so high-scoring
  mail is discarded before Sieve.
- The filters need **User-level (domain-owner) credentials**. DirectAdmin's
  Email Level gives a mailbox user password change and a vacation message,
  and nothing else. DirectAdmin's JSON API has no filter endpoints: #30
  checked the live demo `swagger.json`.
- **MXroute documents none of this, and never documents Sieve.**
  [Expert Spam Filtering][docs-expert] exists, but it describes a
  different mechanism, SMTP-level rejection.
- #30 recorded neither the DirectAdmin URLs nor their fetch dates. That
  gap is recorded rather than filled.

### Roundcube's disabled rule

This is documented by Roundcube, the software, and not by MXroute:
**documented (upstream Roundcube source, 2026-09-28)**, not observed on
MXroute's instance. Read from the managesieve plugin's
[`rcube_sieve_script.php`][rc-script] at `master`
(`cbf2500dd8db`), fetched 2026-09-28 ([#158][i158]).

- **Writing.** A disabled rule is written as `if false # <its test>`:
  `'if ' . ($rule['disabled'] ? 'false # ' : '')`, then the test, then a
  CRLF and the body's `{`. The body is kept. The name marker stays
  `# rule:[NAME]`.
- **The test is on one line.** Several tests are joined by a comma and a
  space inside `allof (...)` or `anyof (...)`; a single test is written
  bare, or inside `allof (...)` when the rule says *all of*.
- **Reading.** Right after `if`, the parser matches
  `/^\s*false\s+#\s*/i` and then reads the rest of that line as the test.
  So the comment must stay on the `if false` line, directly after `false`.

mailctl's `disable-rule` and `enable-rule` write and read exactly this
form, so a rule switched off in either shows as off in the other. Whether
MXroute's Roundcube is a version with this code is under *Unknown*.

## Observed

### `mailctl test`, 2026-08-14, one account

The server's name was not recorded. The 2026-09-28 probe recorded it for
the same account (*Identity and the IMAP capability list, 2026-09-28*),
but nothing records that the account was on that server in August.

A live read against a single MXroute server settled several of these. It is a
**separate tier on purpose**: an observation is stronger than a guess and
weaker than documentation, and it describes *that server* rather than MXroute.

| Observed | Value |
|---|---|
| ManageSieve port + TLS | **4190 + STARTTLS works** — the protocol default was right |
| Folder delimiter | **`.`** — Maildir++, as the blog-post path suggested |
| Spam folder | **`INBOX.spam` exists** |
| Active script name | **`managesieve`** |
| `vacation` | **advertised** |
| `enotify` | **not advertised** |
| `FILTER=SIEVE` | **not advertised** — no server-side retroactive filtering |
| `spamtest`, `extlists` | **not advertised** |
| `regex`, `mailbox`, `imap4flags`, `copy`, `envelope` | **advertised** |

**This does not license hardcoding any of it**, and the reason matters: the
tool discovers these at runtime not because we were unsure what *this* account
reports, but because MXroute is mid-migration on both its panel and Dovecot
(CONVENTIONS.md › *Discover, don't hardcode*). An observation dated today says
nothing about the same server next quarter, and nothing about anyone else's
server. The script name in particular is a per-server configuration value
(`managesieve_script_name`), so it is the **least** generalizable item here.

**`vacation` being advertised changes the status of our refusal.** mailctl
still refuses both `vacation` and `notify`, but they are no longer refusals of
the same kind: `enotify` is **not advertised** on this server, while
`vacation` **is** — so declining to emit it is a **deliberate choice of ours**
(the control panel does autoresponders, and a Sieve autoresponder has real
footguns), where declining an action the server never advertised is barely a
choice at all.

The shared refusal message is still correct for both and needs no tailoring:
it says the refusal is ours rather than a documented MXroute restriction, and
points at `mailctl test` to find out what this server actually advertises.
Distinguishing the two in the message would bake a per-server observation into
a string, which is precisely what CONVENTIONS.md › *Discover, don't hardcode*
exists to prevent.

Half the trigger on the `vacation` icebox entry has therefore fired; see
[ICEBOX.md](../../../ICEBOX.md).

### Identity and the full capability sets, 2026-08-14

Recorded on [#18][i18] and [#16][i16], the same day and the same account.
The probe was a direct read of each protocol's responses, but the command
that did it was not recorded.

| Observed | Value |
|---|---|
| ManageSieve `IMPLEMENTATION` | **`Dovecot Pigeonhole`**, no version |
| IMAP `ID` | **`name: Dovecot`**, no version |
| ManageSieve `SASL` | **`PLAIN`** only |
| IMAP `CAPABILITY` | **39 entries**; the list itself, and whether it was read before or after login, were not recorded |
| Sieve extensions | **23**, listed below |

The Sieve extensions, as the server listed them:

```text
body, comparator-i;ascii-numeric, copy, date, duplicate, encoded-character,
envelope, environment, extracttext, fileinto, foreverypart, ihave,
imap4flags, include, index, mailbox, mime, regex, reject, relational,
subaddress, vacation, variables
```

**Both identity strings are version-free**, and that is deliberate
hardening. So nothing a client can see says which Dovecot this is, and the
capability set is the only durable description of the server. That is why
quirks are keyed on behaviour and capabilities, never on a version
([ADR 0006][adr6]). The two strings are also what selects the
`components/imap/servers/dovecot.py` and
`components/managesieve/servers/pigeonhole.py` profiles.

### Identity and the IMAP capability list, 2026-09-28

Recorded on [#120][i120]. The server was **`heracles.mxrouting.net`**, and
every value below was read **after login**. The probe was one read-only IMAP
session that ran `CAPABILITY`, `ID`, and `NAMESPACE` through `ImapSession`
from a throwaway script. No mailctl command prints all three yet; that is
the gap [#101][i101] closes.

| Observed | Value |
|---|---|
| IMAP `ID` | **`name: Dovecot`**, no version, as on 2026-08-14 |
| IMAP `NAMESPACE`, personal | **`(("", "."),)`**: an empty prefix, delimiter `.` |
| IMAP `CAPABILITY` | **43 entries**, listed below |

The IMAP capabilities, as the server listed them:

```text
BINARY CATENATE CHILDREN COMPRESS=DEFLATE CONDSTORE CONTEXT=SEARCH ENABLE
ESEARCH ESORT I18NLEVEL=1 ID IDLE IMAP4REV1 INPROGRESS LIST-EXTENDED
LIST-STATUS LITERAL+ LOGIN-REFERRALS METADATA MOVE MULTIAPPEND NAMESPACE
NOTIFY PREVIEW PREVIEW=FUZZY QRESYNC QUOTA REPLACE SASL-IR SAVEDATE
SEARCHRES SNIPPET=FUZZY SORT SORT=DISPLAY SPECIAL-USE STATUS=SIZE
THREAD=ORDEREDSUBJECT THREAD=REFERENCES THREAD=REFS UIDPLUS UNSELECT
URL-PARTIAL WITHIN
```

**The 39 of 2026-08-14 and the 43 here cannot be compared.** A server may
advertise a different list before and after login, and the earlier count
does not say which it was. So the difference is **not** evidence of drift,
and it is not recorded as drift.

**The empty `NAMESPACE` prefix bears on [#116][i116].** On this server a
new top-level folder sits at the root, not under `INBOX`.

`heracles.mxrouting.net` is not one of the six servers the 2026-03-04
Dovecot post names as running 2.4 (*Announced changes*). That post is six
months older than this probe, and the server reports no version, so which
Dovecot it runs is still unknown.

### Sieve extensions and ManageSieve capabilities, 2026-09-29

Read with `mailctl probe --json` ([#101][i101]) on
**`heracles.mxrouting.net`**, one account. The ManageSieve capability list
is the one the server sends **before login**; sievelib does not read it
again after AUTHENTICATE, so `OWNER` and `MAXREDIRECTS`, which a server
sends only after login, are not in it.

| Observed | Value |
|---|---|
| ManageSieve capabilities, before login | `IMPLEMENTATION`, `SASL`, `SIEVE`, `VERSION` |
| ManageSieve `IMPLEMENTATION` | **`Dovecot Pigeonhole`**, no version |
| Sieve extensions | **24**, listed below |
| Active script | `managesieve` |
| IMAP | `name: Dovecot`, 43 capabilities after login, delimiter `.`, personal `NAMESPACE` prefix empty, as on 2026-09-28 |

```text
body, comparator-i;ascii-numeric, copy, date, duplicate, editheader,
encoded-character, envelope, environment, extracttext, fileinto,
foreverypart, ihave, imap4flags, include, index, mailbox, mime, regex,
reject, relational, subaddress, vacation, variables
```

**`editheader` is new against the 23 of 2026-08-14**; the other 23 are
unchanged. The earlier list is from a server whose name was not recorded,
so this is a difference between two readings, not established drift on
one server.

### Folder subscription, 2026-08-14

Recorded on [#38][i38]. A read-only comparison on the same account found
`LIST` returning 8 folders and `LSUB` 6. `INBOX` and `Junk` existed and
were not subscribed. So `CREATE` did not subscribe on this server. That is
the protocol's behaviour and not a Dovecot quirk ([ADR 0006][adr6]
*Amendment*), and the `imap` component subscribes explicitly for every
server.

### The 2026-09-27 library evaluation

The evaluation behind [ADR 0006][adr6] was about `sievelib` and
`IMAPClient`, and **it found nothing new about MXroute's servers**. Its one
MXroute input was the identity pair above, which it used to choose the
server modules. Neither module carries a quirk yet. Recorded so that the
evaluation is not later mistaken for a probe.

## Unknown

Say so plainly rather than filling the gap:

- **Whether any of the observations above generalize.** MXroute documents
  neither the ManageSieve port, the TLS mode, the delimiter, nor the extension
  set. Every one of those rows is one server on one day.
- **ManageSieve script-size, script-count, and rate limits.** Nothing in the
  `CAPABILITY` response speaks to these, so a live read cannot settle them.
- **Which server the 2026-08-14 observations came from.** The 2026-09-28
  ones came from `heracles.mxrouting.net`, but which Dovecot that server
  runs is not known.
- **What the in-house webmail does with the script and the folder list.**
  It might keep reading and writing the Roundcube `# rule:[NAME]` dialect,
  or it might ignore rule names. It might show only subscribed folders, or
  everything `LIST` returns. It might edit the same active script or keep
  filters somewhere else.
- **Whether Sieve subscribes a folder it creates with `fileinto :create`.**
  RFC 5490 says nothing about subscription, and this account has never been
  seen doing it either way. `mailctl`'s `--no-imap` wording depends on this
  staying unknown.
- **Whether a given account has the DirectAdmin Exim filter,** including
  accounts set up since MXroute began phasing DirectAdmin out.
- **Whether the panel's *Spam Filters* menu is DirectAdmin's
  `CMD_EMAIL_FILTER`,** and whether the customer panel is DirectAdmin's.
  These are marked UNCONFIRMED on [#30][i30], and both sit badly against
  the phase-out.
- **Port 143 with STARTTLS**, beyond the panel page (see *Connecting to
  IMAP*).
- **Whether MXroute's Roundcube writes a disabled rule the way upstream
  does** (*Roundcube's disabled rule*). Settle it by switching one rule
  off in the webmail and reading the script with `mailctl show`.
- **Whether this server's IMAP `CAPABILITY` list changes at login.** Only
  the after-login list is recorded, so the 2026-08-14 count cannot be
  placed against it.

## Not yet used

This is what the service offers and mailctl does not yet take advantage of.
It is the other half of the operator's requirement (CONVENTIONS.md ›
*Providers are probed and read*). An entry here is not a commitment to build
it. It records that the capability exists, so that a feature request can
start from it.

### Sieve

mailctl emits three of the 23 advertised extensions: `fileinto`,
`imap4flags`, and `mailbox` (the emit table in
`components/managesieve/emit.py`). The rest are unused. [#16][i16] is the
open reading task for all of them. These look the most useful:

- **`regex`**: real pattern matching. mailctl offers only `:contains`,
  `:is`, and `:matches`.
- **`envelope`**: the SMTP recipient rather than the `To:` header. It is how
  to catch mail sent to an alias.
- **`subaddress`**: `user+tag@` matching.
- **`duplicate`**: de-duplicate repeated notifications. It needs
  server-side state.
- **`date`** and **`relational`**: time-based and numeric rules.
- **`body`**, **`mime`**, **`foreverypart`**, and **`extracttext`**:
  matching on the content rather than the headers.
- **`variables`**, **`include`**, and **`ihave`**. `ihave` is the Sieve-side
  runtime capability test, relevant to [#19][i19].
- **`copy`**: file a message and keep it in the inbox as well.
- **`reject`**: refuse delivery with a message.
- **`vacation`**: refused by choice (*Observed*, above), and iceboxed.

`sievelib`'s parser rejects `subaddress`, `include`, `duplicate`, `ihave`,
and the `i;ascii-numeric` comparator ([ADR 0006][adr6], gap S6). Each needs
the parser extended before mailctl can round-trip a script that uses it.

### IMAP

mailctl uses `MOVE` when it is advertised, `UIDPLUS` for `UID EXPUNGE` in
the move fallback, and `ID` to choose a server profile. It reads
`FILTER=SIEVE` only to report on it. Of the 43 capabilities seen on
2026-09-28 (*Observed*), these could serve mailctl. Each line says what it
might do, not what will be built:

- **`SPECIAL-USE`**: find Trash, Junk, Sent, and Archive by their role
  rather than their name. It bears on rules that file to Trash, and on
  `INBOX.spam` versus `Junk`.
- **`NAMESPACE`**: the parent for a new folder, read from the server rather
  than guessed ([#116][i116]).
- **`PREVIEW`** / **`SNIPPET=FUZZY`**: a server-side preview line for
  `mailctl search`, without fetching bodies.
- **`ESEARCH`**, **`SEARCHRES`**, and **`WITHIN`**: cheaper searches, and
  age criteria such as *older than N days* for the retroactive pass.
- **`SORT`**, **`SORT=DISPLAY`**, and **`THREAD=*`**: server-side ordering
  and threading for `mailctl search`.
- **`METADATA`**: per-mailbox annotations. Whether mailctl has a use for it
  is unclear; it is recorded as available.
- **`QUOTA`**: the account's quota is a setting on the account, so under
  the scoping rule `mailctl test` could report it.
- **`NOTIFY`** / **`IDLE`**: push notification of new mail. Probably out of
  scope: mailctl is a one-shot CLI and holds no session open to be told
  anything.

### The host

- **Self-service spam settings and a user-set spam-folder threshold**,
  once MXroute 4.1 ships them. They are settings on the account, so under
  the scoping rule they are in scope if they are reachable with a mailbox
  credential. Where they will be reachable is unknown.
- **The REST API's spam settings, spam lists, and forwarders** exist, but
  the sibling [terraform-provider-mxroute][provider] owns them
  (CONVENTIONS.md › *The sibling repository*). They are listed here so that
  nobody reimplements them in mailctl.

### Offered, and out of reach

- **The DirectAdmin/Exim filtering stage.** mailctl can neither read it nor
  change it. It needs a domain-owner credential, which mailctl does not hold
  and should not ([#30][i30], closed). This is recorded so that nobody
  attempts it again. The mitigation is to say that the stage exists, which
  `mailctl test` and the README do.

## Refresh log

| Date | What | How |
|---|---|---|
| 2026-08-14 | *Observed*: the `mailctl test` table, identity, capability sets, subscription | `mailctl test` and direct protocol reads on one account (#16, #18, #38); server name not recorded |
| 2026-09-27 | *Documented*: every public source re-fetched; announced changes added | WebFetch of the URLs above; OpenAPI paths counted; no probe run |
| 2026-09-27 | *Documented*: in-house webmail sources (migration post, branding guide, 2023 post); recorded as a known condition | Web search of docs, blog and community; no webmail documentation exists beyond these |
| 2026-09-28 | *Observed*: IMAP `CAPABILITY` (43, after login), `ID`, `NAMESPACE`; also a `mailctl test` read and the read-only CLI commands | One read-only IMAP session through `ImapSession` in a throwaway script (#101, #120); server `heracles.mxrouting.net` |
| 2026-09-28 | *Documented*: Roundcube's disabled-rule form | Upstream `rcube_sieve_script.php` read at `cbf2500dd8db` (#158); not probed on MXroute |
| 2026-09-29 | *Observed*: Sieve extensions (24, `editheader` new), ManageSieve capabilities before login, active script; IMAP unchanged | `mailctl probe --json`, read-only, server `heracles.mxrouting.net` (#101) |

**Due next:** a probe, by 2026-12-29 on the quarterly cadence. The in-house
webmail does not bring it forward; see *Known condition* above. The
2026-09-28 probe recorded the server name and the full IMAP `CAPABILITY`
list that the 2026-08-14 entry left out; its command was a throwaway
script, which [#101][i101] replaces; `mailctl probe` also says whether each
list was read before or after login. Separately, what the in-house webmail
does with filters and folders is an *Unknown*. The only way to
settle it is to use it: create one filter there and read the script back
with `mailctl show`.

[adr1]: ../../../adr/0001-standalone-cli-over-provider-resource.md
[adr6]: ../../../adr/0006-two-layer-component-and-provider-architecture.md
[conv]: ../../../.claude/CONVENTIONS.md
[provider]: https://github.com/harleypig/terraform-provider-mxroute
[i16]: https://github.com/harleypig/mailctl/issues/16
[i18]: https://github.com/harleypig/mailctl/issues/18
[i19]: https://github.com/harleypig/mailctl/issues/19
[i30]: https://github.com/harleypig/mailctl/issues/30
[i38]: https://github.com/harleypig/mailctl/issues/38
[i101]: https://github.com/harleypig/mailctl/issues/101
[i102]: https://github.com/harleypig/mailctl/issues/102
[i116]: https://github.com/harleypig/mailctl/issues/116
[i120]: https://github.com/harleypig/mailctl/issues/120
[i158]: https://github.com/harleypig/mailctl/issues/158
[rc-script]: https://github.com/roundcube/roundcubemail/blob/cbf2500dd8db31ada3fccf71e247c8d8c852c3d7/plugins/managesieve/lib/Roundcube/rcube_sieve_script.php
[blog-redirect]: https://blog.mxroute.com/why-we-disabled-redirect-sieve-filters-on-mxroute
[blog-dovecot24]: https://blog.mxroute.com/we-fixed-quota-reporting-then-dovecot-2-4-happened
[blog-bandages]: https://blog.mxroute.com/ripping-off-bandages
[blog-next]: https://blog.mxroute.com/the-next-chapter
[blog-2023]: https://blog.mxroute.com/introducing-the-new-mxroute-webmail-upgrade-all-your-email-needs-in-one-place
[docs-hostnames]: https://docs.mxroute.com/docs/branding/customhostnames.html
[docs-ios]: https://docs.mxroute.com/docs/general/ios-mail.html
[docs-expert]: https://docs.mxroute.com/docs/expert-spam-filtering.html
[api-openapi]: https://api.mxroute.com/openapi.yaml
[cl-401]: https://docs.mxroute.com/docs/changelog/mxroute-4.0.1.html
[cl-41]: https://docs.mxroute.com/docs/changelog/mxroute-4.1.html
