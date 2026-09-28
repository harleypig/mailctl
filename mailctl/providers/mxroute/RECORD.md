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
| Observed | 2026-08-14 (one account, one server) |

## Documented

### `redirect` is disabled

- **Sieve `redirect` is disabled.** MXroute's own blog says so — *"Why we
  disabled redirect sieve filters on MXroute"* (2024-03-21) — and gives the
  reason: real forwarders "are designed to properly handle SRS".
  - Source: [the blog post][blog-redirect], fetched 2026-09-27. It reads
    *"We've decided to disable the ability for users to create redirect
    sieve filters"* and *"Email forwarders on MXroute are designed to
    properly handle SRS"*.
  - **Date mismatch:** on 2026-09-27 the post showed **Mar 22, 2024**.
    Everything in the tree says 2024-03-21: this record, `mailctl test`'s
    note, and the refusal message. The difference is a day, and it may be a
    time-zone effect. It is recorded here and not corrected, because the
    refusal text is pinned by a CLI snapshot.

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
- The earlier statement that MXroute *intends to migrate away from
  DirectAdmin, Crossbox, and Roundcube this year*, which CONVENTIONS.md ›
  *Discover, don't hardcode* cites, was recorded without a URL. The 4.1
  changelog above is now its source.

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

## Observed

### `mailctl test`, 2026-08-14, one account

The server's name was not recorded. The next probe records it (see
*Refresh log*).

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
| IMAP `CAPABILITY` | **39 entries**; the list itself was not recorded |
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
- **Which server the observations came from,** and so whether it is one of
  the six on Dovecot 2.4.
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
- **The 39 IMAP capabilities by name.** Their list was not recorded, so
  nothing below can say which of them go unused.

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
`FILTER=SIEVE` only to report on it. What else the 39 capabilities offer
cannot be listed until they are recorded (*Unknown*).

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

**Due next:** a probe. It is already due, because the in-house webmail is
an announced change that the 2026-08-14 observations predate. The quarterly
cadence would make it due by 2026-11-14 anyway. That probe should record
the server name, the full IMAP `CAPABILITY` list, and the probe command,
which are the three gaps the 2026-08-14 entry left.

[adr1]: ../../../adr/0001-standalone-cli-over-provider-resource.md
[adr6]: ../../../adr/0006-two-layer-component-and-provider-architecture.md
[conv]: ../../../.claude/CONVENTIONS.md
[provider]: https://github.com/harleypig/terraform-provider-mxroute
[i16]: https://github.com/harleypig/mailctl/issues/16
[i18]: https://github.com/harleypig/mailctl/issues/18
[i19]: https://github.com/harleypig/mailctl/issues/19
[i30]: https://github.com/harleypig/mailctl/issues/30
[i38]: https://github.com/harleypig/mailctl/issues/38
[blog-redirect]: https://blog.mxroute.com/why-we-disabled-redirect-sieve-filters-on-mxroute
[blog-dovecot24]: https://blog.mxroute.com/we-fixed-quota-reporting-then-dovecot-2-4-happened
[docs-ios]: https://docs.mxroute.com/docs/general/ios-mail.html
[docs-expert]: https://docs.mxroute.com/docs/expert-spam-filtering.html
[api-openapi]: https://api.mxroute.com/openapi.yaml
[cl-401]: https://docs.mxroute.com/docs/changelog/mxroute-4.0.1.html
[cl-41]: https://docs.mxroute.com/docs/changelog/mxroute-4.1.html
