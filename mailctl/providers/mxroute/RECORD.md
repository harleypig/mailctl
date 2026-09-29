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

### Folder rename, 2026-08-14

Recorded on [#5][i5]. A rename done by hand on the same account moved the
folder and its 360 messages, and left the new name out of `LSUB`, so
webmail stopped showing it. That is the protocol's behaviour too: `RENAME`
does not carry a subscription (RFC 3501 section 6.3.5). Whether the old
name stayed in `LSUB` was not recorded. `rename-folder` does not depend on
either answer: it subscribes each new name that was subscribed before,
drops any old name still listed, and reads `LSUB` back afterwards. The
container tier's Dovecot 2.4 did the same on 2026-09-29, keeping the old
names subscribed; that is a local server, not an MXroute one.

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
- **Whether `LIST-STATUS` answers as advertised here.** Both it and
  `STATUS=SIZE` are in the 2026-09-28 list, and `folders --counts` has run
  against the container tier's Dovecot 2.4 but not yet against this
  server. The read-only live check's `folder-counts` test settles it.
- **Whether this server reports `UIDVALIDITY`, and keeps it.** The
  protocol requires it, and the container tier's Dovecot 2.4 gave a folder
  deleted and made again a new one; this server has not been read. The
  read-only live check's `uidvalidity` test settles the first half.
- **Whether this server's IMAP `CAPABILITY` list changes at login.** Only
  the after-login list is recorded, so the 2026-08-14 count cannot be
  placed against it.
- **How MXroute's Pigeonhole behaves for any advertised Sieve extension
  beyond listing it** (*Not yet used › Sieve*): the `subaddress`
  separator, how long `duplicate` remembers an ID, which headers
  `editheader` may change, and the `vacation` minimum are all the
  implementation's to choose, and none has been read or probed.
- **Whether a stored message carries its envelope in a header**, such as
  `Delivered-To` or `X-Original-To`. It decides whether the IMAP pass
  could reproduce an `envelope` rule. Settle it by reading the headers of
  one delivered message with `mailctl view`.

## Not yet used

This is what the service offers and mailctl does not yet take advantage of.
It is the other half of the operator's requirement (CONVENTIONS.md ›
*Providers are probed and read*). An entry here is not a commitment to build
it. It records that the capability exists, so that a feature request can
start from it.

### Sieve

mailctl emits four of the 24 extensions advertised on 2026-09-29
(*Observed*): `fileinto`, `imap4flags`, `mailbox`, and, since
[#152][i152], `body` for `add --body` (the emit table in
`components/managesieve/emit.py`). A rule using `body` has run against the
container tier's Dovecot 2.4 Pigeonhole, not yet against an MXroute
server. The two tables below cover all 24, from the reading [#16][i16]
asked for.

**Keep the tiers apart when reading them.** What an extension does is
**documented** by its defining document, an RFC for all but `regex`. That
the server lists it is **observed**, on one server on one day. How MXroute's Pigeonhole behaves for any of them beyond
listing it is **unknown**: nothing here was run against a mail server.

- **Sources.** The [IANA Sieve extensions registry][iana-sieve] (last
  updated 2024-10-15) names each extension's defining document. Each RFC
  was read from the RFC Editor, and the `regex` draft from the IETF
  archive. All were fetched 2026-09-29.
- **The parser column is measured.** Each extension's commands, tests, and
  tags were parsed in a minimal script with the repo's `sievelib` 1.5.0,
  offline, on 2026-09-29.

**None of the 24 is base language.** Each is named in `require` before
use. RFC 5228's base is `keep`, `discard`, `redirect`, `stop`, `if`, the
`header`, `address`, `exists`, and `size` tests, `allof` / `anyof` /
`not`, and the `i;octet` and `i;ascii-casemap` comparators. `fileinto`,
`envelope`, and `encoded-character` are defined inside RFC 5228, but as
optional extensions that must be `require`d. That corrects [#82][i82]'s table,
which labelled `fileinto` and `envelope` as core. The same table named the
`regex` document `draft-ietf-sieve-regex`; the registry's reference is
`draft-murchison-sieve-regex-07`, which expired in 2004 and never became
an RFC.

#### What each one is

| Extension | Defined in | What a rule could say that mailctl cannot | mailctl | sievelib 1.5.0 parses it |
|---|---|---|---|---|
| `body` | RFC 5173 | Match the whole undecoded body (`:raw`), or only parts of named content types (`:content`). `:text :contains` is already emitted | **emits** `body :text` | yes: `:raw`, `:content`, `:text` |
| `comparator-i;ascii-numeric` | RFC 4790 §9.1, under RFC 5228's `comparator-` prefix | Compare a header as a number (a spam score), with `relational` | no | **no**: the comparator is refused; a `require` line naming it is accepted |
| `copy` | RFC 3894 | `fileinto :copy`: file a copy without cancelling the implicit keep, so a later `discard` can still drop the inbox copy. mailctl's `--keep` writes an explicit `keep`, which nothing later cancels | no | yes |
| `date` | RFC 5260 | `date`: one part (year, hour, weekday, …) of a date header, in a chosen zone. `currentdate`: the same of the delivery time | no | yes: both tests, `:zone`, `:originalzone` |
| `duplicate` | RFC 7352 | True when a message with the same `Message-ID`, another header, or a given value was seen before, optionally within `:seconds` | no | **no** |
| `editheader` | RFC 5293 | `addheader` / `deleteheader`: change the header of the message as stored | no | **no** |
| `encoded-character` | RFC 5228 §2.4.2.4 | `${hex:…}` and `${unicode:…}` inside strings. mailctl writes UTF-8 directly and has no need of it | no | yes |
| `envelope` | RFC 5228 §5.4 | Match the SMTP `MAIL FROM` and this user's `RCPT TO` rather than the headers: mail sent to an alias, or Bcc'd | no | yes; not with `subaddress`'s `:detail` |
| `environment` | RFC 5183 | Facts about the interpreter: `domain`, `host`, `name`, `phase`, `remote-host`, `remote-ip`, `version`, … | no | yes |
| `extracttext` | RFC 5703 §7 | Put a MIME part's text in a variable. Useful only inside `foreverypart` and with `variables` | no | **no**: fails at `foreverypart` |
| `fileinto` | RFC 5228 §4.1 | — | **emits** | yes, with `:copy`, `:create`, `:flags` |
| `foreverypart` | RFC 5703 §3 | Loop over every MIME part, with `break` | no | **no** |
| `ihave` | RFC 5463 | Test at delivery whether an extension exists, and use it only if so. Adds `error` | no | **no**: neither `ihave` nor `error` |
| `imap4flags` | RFC 5232 | `setflag` / `removeflag`, the `hasflag` test, and `:flags` on `fileinto` / `keep` | **emits** `addflag` | yes: `addflag`, `hasflag`, `:flags` |
| `include` | RFC 6609 | Run another stored script (`:personal` or `:global`); `return` from it | no | **no** |
| `index` | RFC 5260 | `:index N` / `:last` on `header`, `address`, and `date`: test one occurrence of a repeated field, such as the first `Received` | no | **no** |
| `mailbox` | RFC 5490 | The `mailboxexists` test | **emits** `:create` | `:create` yes; `mailboxexists` **no** |
| `mime` | RFC 5703 §4 | `:mime` / `:anychild` on `header`, `address`, and `exists`, with `:type`, `:subtype`, `:contenttype`, `:param`: "any part is a PDF" | no | **no** |
| `regex` | draft-murchison-sieve-regex-07 | A `:regex` match type, POSIX extended regular expressions, on `header`, `address`, and `envelope` | no | yes |
| `reject` | RFC 5429 | Refuse delivery, with a reason sent back to the sender | no | yes |
| `relational` | RFC 5231 | `:count` (how many values: more than N recipients) and `:value` (`gt`, `ge`, `lt`, `le`, `eq`, `ne`) | no | yes with the default comparator; **no** with `i;ascii-numeric` |
| `subaddress` | RFC 5233 | The `:user` and `:detail` parts of `user+tag@` | no | **no**, on `address` or `envelope` |
| `vacation` | RFC 5230 | An autoresponder | no: refused by choice (*Observed*) and iceboxed | yes, every tag |
| `variables` | RFC 5229 | `set`, the `string` test, and `${name}` / `${1}` in strings, as in `fileinto "Lists/${1}"` from a `:matches` capture | no | partly: `set` and `${…}` yes; the `string` test and `set`'s modifiers (`:lower`, …) **no** |

#### Footguns, and each against the adoption tests

Each verdict is against the three tests in CONVENTIONS.md › *Rule
conventions* ([#17][i17]): (1) the IMAP pass can reproduce it on mail
already delivered; (2) it has an `EMIT_TABLE` entry and a decided absence
path; (3) the provider's own panel does not do it better. Test 1 decides
most of them. IMAP `SEARCH` is a case-insensitive substring match, with no
pattern, number, or MIME-part keys (RFC 3501 §6.4.4), so anything beyond
that means fetching and re-checking client-side, as mailctl already does
for `:matches`.

| Extension | Footgun | Verdict |
|---|---|---|
| `body` | A part that cannot be decoded MAY be read as US-ASCII, left out, or handled by local convention (RFC 5173 §5.2). IMAP `BODY` is substring-only, so `:matches` over a body needs the body fetched | **Adopted** ([#152][i152]) for `:text :contains`. `:raw` and `:content` would each need test 1 again |
| `comparator-i;ascii-numeric` | Input is cut at the first non-digit, so `"5.9"` equals `"5"`. A value not starting with a digit is positive infinity, so a missing or `n/a` score is larger than every number. No substring matching | Fails test 1 in practice: no numeric search, so every header in the folder is fetched and compared. Not now; only useful with `relational` |
| `copy` | Two copies count against quota. It differs from `fileinto` + `keep` only when a later rule might `discard` (RFC 3894 §1) | Passes test 1 (IMAP `COPY` without deleting). Adds little over `--keep`. Not a candidate |
| `date` | `date` reads a header the sender wrote, and is false when it is missing or unparsable (RFC 5260 §4). `currentdate` is the moment of delivery | Fails test 1. `SENTBEFORE` / `SENTSINCE` compare `Date:` to the day, ignoring time and zone; `currentdate` has no retroactive meaning |
| `duplicate` | Needs server-side state. The ID is recorded only when the script finishes; parallel deliveries can both miss (RFC 7352 §3) | Fails test 1: the server's tracking list cannot be read, so a pass cannot know what delivery counted as seen |
| `editheader` | `Received` and `Auto-Submitted` cannot be deleted, and a refused change is **silently** ignored (RFC 5293 §6). Later tests see the edited header | Fails test 1: an IMAP message is immutable; editing means appending a new message and deleting the old |
| `encoded-character` | Once required, any literal `${hex:` or `${unicode:` in a string is decoded | Nothing to adopt: mailctl writes UTF-8 |
| `envelope` | `to` is only the `RCPT TO` that delivered to this user (RFC 5228 §5.4). The envelope is not part of the stored message | Fails test 1 unless the server records the envelope in a header of the stored message; whether MXroute does is *Unknown* |
| `environment` | Every value is the implementation's, and most describe the delivery moment | Fails test 1. Not a candidate |
| `extracttext` | Outside `foreverypart` it sets the variable to the empty string (RFC 5703 §7) | Fails test 1 without fetching every part. Not a candidate |
| `fileinto` | A missing folder MAY be an error, created, or delivered elsewhere (RFC 5228 §4.1). That is why mailctl uses `:create` | **Adopted** |
| `foreverypart` | Nesting depth MAY be limited by the implementation (RFC 5703 §3) | Plumbing for `mime` and `extracttext`, not a feature. Not a candidate alone |
| `ihave` | Requiring it turns off parse-time checking of extension use (RFC 5463 §4), so `CHECKSCRIPT` stops catching a misused extension; it fails at delivery | Not a feature to adopt; see *`ihave` against drift checks* below |
| `imap4flags` | `setflag` replaces any flags set before it; the flags held when the message is filed, the implicit keep included, are the ones stored (RFC 5232 §3) | **Adopted** (`addflag`). `removeflag` would pass test 1 (IMAP `STORE -FLAGS`). `hasflag` reads the script's own flag variable, empty when the script starts, not the stored message's flags, so it has no IMAP counterpart |
| `include` | A recursive include is an error at execution, never at upload; a missing script is an error unless `:optional` (RFC 6609 §3.1) | Structure, not an effect; mailctl merges into one active script. Not a candidate |
| `index` | Counts separate header fields, not addresses within one field (RFC 5260 §6) | Test 1 only by fetching the headers and picking the occurrence. Not now |
| `mailbox` | Whether `:create` subscribes the folder is *Unknown* | **Adopted** (`:create`). `mailboxexists` has no use in a filter mailctl writes |
| `mime` | Matches on part headers, not file contents; a message's own `Content-Type` is one part among the rest | Test 1 possible by fetching `BODYSTRUCTURE`, which the pass does not do yet. Blocked on the parser. Later, perhaps |
| `regex` | Not a standard. POSIX EREs, not Python's `re`: `\b`, `\w`, and back-references are unsupported, and Python reads `[[:lower:]]` as a plain character set without an error. A backslash is doubled by Sieve string escaping | Nearest to passing. Test 1 only if the re-check evaluates the same dialect; the risk is the halves disagreeing, which #17 exists to prevent |
| `reject` | Where the server cannot refuse during the SMTP transaction, it notifies the envelope sender, which spam forges: the "Joe-job" the RFC is written against (RFC 5429 §1) | Fails test 1: mail already delivered cannot be refused |
| `relational` | With the default `i;ascii-casemap` comparator `:value` compares as text, so `"10"` sorts before `"9"`; numbers need `i;ascii-numeric` | Test 1 only client-side; no count or order in IMAP `SEARCH`. Not now |
| `subaddress` | The separator is the implementation's; `+` is usual but not given (RFC 5233 §4) | Header form passes test 1 through the re-check, but `--to 'user+tag@'` already matches the same mail. The envelope form fails as `envelope` does. Not a candidate |
| `vacation` | Reply loops and replies to lists are held back only by `:days` (default 7 or the site minimum) and the implementation's checks (RFC 5230 §4.1, §4.6) | Fails tests 1 and 3: replying to old mail is wrong, and the panel does autoresponders. Iceboxed |
| `variables` | Once required, every `${…}` in every string is expanded, and an unknown variable becomes the empty string, so `fileinto "Lists/${1}"` with no capture files into `Lists/` | Test 1 possible for `:matches` captures, client-side. Not a candidate alone |

**No extension passes all three tests today.** `regex` is the nearest: it
parses, its absence path is a refusal, and the re-check is already where a
pattern would be evaluated. It passes test 1 only if mailctl evaluates
POSIX EREs as Pigeonhole does, and that is not established. `mime` is the
next, once the parser and a `BODYSTRUCTURE` re-check exist.

#### The parser gaps, as measured

`sievelib` 1.5.0 refuses `duplicate`, `editheader`, `foreverypart` (and so
`extracttext`), `ihave` and `error`, `include`, `index`, `mailboxexists`,
`mime`, `subaddress`'s `:user` / `:detail`, the `i;ascii-numeric`
comparator, and `variables`' `string` test and `set` modifiers ([ADR
0006][adr6], gap S6). It accepts a `require` line naming any of the 24, so
the failure comes at the first use. **This matters before mailctl ever
emits one.** A rule written in the webmail with any of these makes the
active script unparseable, and a parse failure is a hard stop for every
command that merges into it ([ADR 0002][adr2]).

#### `ihave` against drift checks

`ihave` and `check-baseline` ([#19][i19]) answer the same question, *is
this extension still there?*, at different times and for different
readers:

- **`check-baseline` asks at command time and tells the user.** It compares
  a fresh probe with a saved one and reports what changed.
- **`ihave` asks at delivery time and tells nobody.** A rule wrapped in it
  keeps working, degraded, if the server drops the extension after
  upload. The script chooses its own fallback, or stops with `error`.

So `ihave` covers the gap between two probes, when no one runs mailctl. It
is not a replacement for drift checks. It has three costs. The script can
no longer be checked for extension misuse at upload (the footgun above).
sievelib cannot parse it. And the IMAP pass would have to choose the same
branch the server will choose at delivery, which it can only guess. Not
adopted.

### IMAP

mailctl uses `MOVE` when it is advertised, `UIDPLUS` for `UID EXPUNGE` in
the move fallback, `ID` to choose a server profile, and, since
[#159][i159], `SORT` for `search --sort`: one `UID SORT` when it is
advertised, a client-side sort when it is not. `SORT` was seen advertised
on 2026-09-28 (*Observed*); the sort has run against the container tier's
Dovecot, not yet against an MXroute server. `folders --counts`
([#157][i157]) uses `LIST-STATUS` to count every folder in one `LIST`, and
asks for each folder's size too when `STATUS=SIZE` is advertised; without
`LIST-STATUS` it is refused. Since [#204][i204] it reads the
`UIDVALIDITY` every `SELECT` and `EXAMINE` reports (base protocol, RFC 9051
section 2.3.1.1, so *Documented*), to refuse a UID from before a folder was
renumbered. It reads `FILTER=SIEVE` only to report on it. Of the 43 capabilities seen on 2026-09-28 (*Observed*), these could
serve mailctl. Each line says what it might do, not what will be built:

- **`SPECIAL-USE`**: find Trash, Junk, Sent, and Archive by their role
  rather than their name. It bears on rules that file to Trash, and on
  `INBOX.spam` versus `Junk`.
- **`NAMESPACE`**: the parent for a new folder, read from the server rather
  than guessed ([#116][i116]).
- **`PREVIEW`** / **`SNIPPET=FUZZY`**: a server-side preview line for
  `mailctl search`, without fetching bodies.
- **`ESEARCH`**, **`SEARCHRES`**, and **`WITHIN`**: cheaper searches, and
  finer age criteria. `WITHIN` and `SEARCHRES` were both advertised on
  2026-09-28 (*Observed*) and neither is used: `--older-than` ([#152][i152])
  is sent as IMAP4rev1's own `BEFORE`, with a date counted back from today,
  so it needs no extension and works to the day. `WITHIN`'s `OLDER` /
  `YOUNGER` would give it to the second.
- **`SORT=DISPLAY`**, **`ESORT`**, and **`THREAD=*`**: sorting by the
  displayed name rather than the address (`DISPLAYFROM`), sort results
  returned as ranges, and threading, for `mailctl search`. `SORT` itself
  is used (above); its `FROM`, `CC`, and `SUBJECT` keys are not.
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
| 2026-09-29 | *Not yet used › Sieve*: each of the 24 advertised extensions against its defining document, sievelib's parser, and the adoption tests | IANA registry, the RFCs, and the `regex` draft fetched; minimal scripts parsed with `sievelib` 1.5.0 offline (#16); no probe |

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
[adr2]: ../../../adr/0002-non-destructive-script-merge.md
[adr6]: ../../../adr/0006-two-layer-component-and-provider-architecture.md
[conv]: ../../../.claude/CONVENTIONS.md
[provider]: https://github.com/harleypig/terraform-provider-mxroute
[i16]: https://github.com/harleypig/mailctl/issues/16
[i17]: https://github.com/harleypig/mailctl/issues/17
[i18]: https://github.com/harleypig/mailctl/issues/18
[i19]: https://github.com/harleypig/mailctl/issues/19
[i30]: https://github.com/harleypig/mailctl/issues/30
[i5]: https://github.com/harleypig/mailctl/issues/5
[i38]: https://github.com/harleypig/mailctl/issues/38
[i101]: https://github.com/harleypig/mailctl/issues/101
[i102]: https://github.com/harleypig/mailctl/issues/102
[i116]: https://github.com/harleypig/mailctl/issues/116
[i120]: https://github.com/harleypig/mailctl/issues/120
[i152]: https://github.com/harleypig/mailctl/issues/152
[i157]: https://github.com/harleypig/mailctl/issues/157
[i158]: https://github.com/harleypig/mailctl/issues/158
[i159]: https://github.com/harleypig/mailctl/issues/159
[i204]: https://github.com/harleypig/mailctl/issues/204
[i82]: https://github.com/harleypig/mailctl/issues/82
[iana-sieve]: https://www.iana.org/assignments/sieve-extensions
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
