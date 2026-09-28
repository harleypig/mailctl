# 6. Two layers: protocol component libraries and providers

- Status: accepted
- Date: 2026-09-27
- Amended: 2026-09-27, at step 4 and again for #99 (see *Amendment*)
- Supersedes the open questions of [#26][i26]; recorded for epic [#92][i92].

## Context

**Gmail is coming, and the code has no seam to put it behind.** Today
`sieve.py` and `imap.py` each mix three kinds of knowledge that change for
different reasons:

- **Protocol** — what RFC 5804 and IMAP4rev1 say. It is the same for every
  host.
- **Server software** — what Dovecot and Pigeonhole do beyond the RFCs. It
  is the same for every host running that software.
- **Host** — what MXroute has set up and decided.
  `MXROUTE_FORBIDDEN_ACTIONS` sits in `sieve.py`. The Roundcube name
  dialect sits there too, because Roundcube is the webmail MXroute ships.
  `imap.py` carries MXroute's spam folder and login advice.

A Gmail provider shares the protocol knowledge (it speaks IMAP), shares none
of the server knowledge, and has entirely different host knowledge: no
ManageSieve at all, and a REST filter API instead. With the three mixed,
adding it means either copying `imap.py` or threading `if gmail` through it.

**The MXroute REST API has no filter surface** ([ADR 0001][adr1]), so
MXroute filters are reachable only over ManageSieve. For Gmail it is the
other way round: filters are reachable only over the Gmail API
(`users.settings.filters`), and there is no ManageSieve (see *Gmail*,
below). No single protocol spans both hosts, so the abstraction cannot be a
protocol.

**[#26][i26] settled the provider interface and left the structure below it
open.** Its settled position is that every backend presents one formalized
interface. What varies is declared as data. A rule a backend cannot express
is refused before any network work. Selection is one of the interface's
operations. The plugin framework is deferred. [ADR 0003][adr3] explicitly
left this seam to #26. This record settles the structure and carries #26's
position into it.

**Both wrapped libraries were evaluated again on 2026-09-27.** Every source
was fetched that day, and each claim below carries a label: **V** verified
in a primary source, **R** relayed from a secondary source, **A** assumed.

## Decision

**Two layers.** Layer 1 is one library per protocol, knowing nothing about
any host. Layer 2 is one provider per host, composing layer-1 libraries and
holding that host's setup and policies. The engine talks only to a provider.

```text
mailctl/components/          layer 1: provider-agnostic, one library per protocol
  imap/                      client (wraps IMAPClient) + servers/dovecot.py, ...
  managesieve/               client + Sieve script handling (wraps sievelib)
                             + servers/pigeonhole.py
  gmail_api/                 (later) filters and labels over the Gmail API
mailctl/providers/           layer 2: how one host uses those components
  mxroute/                   imap + managesieve; MXroute's policies
  gmail/                     (later) imap + gmail_api
```

### Layer 1: protocol component libraries

**Each library is named by its protocol and knows only that protocol.** It is
`imap`, `managesieve`, and later `gmail_api`, never `dovecot`. That is what
lets the Gmail provider reuse `imap` unchanged.

**Each one wraps the existing Python library instead of replacing it.** Both
verdicts are *adopt with named wrappers*. Neither library is being worked
around in general. Each has specific gaps, and the wrapper is where each one
is closed or deliberately left open.

**`sievelib` 1.5.0**, under `managesieve`. MIT, one active maintainer, one or
two releases a year, six open issues (V). It implements every RFC 5804
command except `NOOP` and `UNAUTHENTICATE`, with SASL PLAIN, LOGIN,
DIGEST-MD5, OAUTHBEARER, and XOAUTH2 (V). Its gaps, all V:

| # | Gap | Wrapper's answer |
|---|-----|------------------|
| S1 | `GETSCRIPT` rewrites CRLF to LF and drops the trailing newline (upstream tonioo/sievelib#95) | Byte-exact read — [#90][i90] |
| S2 | No `NOOP` or `UNAUTHENTICATE` | Not needed today; noted |
| S3 | `WARNINGS`, `TAG`, and `REFERRAL` response codes not exposed; `NO` codes only as a raw `errcode` | Parse in the wrapper when a caller needs one |
| S4 | The `MAXREDIRECTS`, `OWNER`, and `UNAUTHENTICATE` capabilities are dropped | Full capability parsing |
| S5 | No injectable `SSLContext`; read timeout hard-coded at 5 s | Configurable timeout; the context when a caller needs one |
| S6 | The parser rejects `subaddress`, `spamtest`/`virustest`, `include`, `mailboxexists`, `editheader`, `duplicate`, `special-use`, `ihave`, and the `i;ascii-numeric` comparator | Extend through `commands.add_commands()` as each is wanted |
| S7 | Free-standing comments are dropped on render | [#7][i7] |
| S8 | Debug mode prints the base64 `AUTHENTICATE` payload | `debug=False` stays pinned, and a test holds it there |
| S9 | The response reader loops forever when the connection closes mid-literal, for every command but `GETSCRIPT` (amendment) | sievelib's line and literal readers are overridden to read through the client's own, which raises on EOF — fixed in [#95][i95] |

S8 is a credential leak rather than a rough edge. The pin is already in
`sieve.py`, and it moves with the session as a guarantee, not a default.

**`IMAPClient` 4.1.0**, under `imap`. BSD-3, active again in 2026 (4.0 and
4.1 both in September), 54 open issues (V). It covers IMAP4rev1 plus `ID`,
`NAMESPACE`, `ENABLE`, `IDLE`, `MOVE`, quota, ACL, XOAUTH2/OAUTHBEARER/SASL,
and the Gmail `X-GM-*` helpers (V). Its gaps, all V:

| # | Gap | Wrapper's answer |
|---|-----|------------------|
| I1 | Non-ASCII `SEARCH`: the charset is not carried into nested criteria (upstream mjs/imapclient#645); reproduced | Fix — [#89][i89] |
| I2 | No `LIST-EXTENDED` or `RETURN (SPECIAL-USE)`; plain `LIST` flags only | Not needed today; noted |
| I3 | No built-in `MOVE` fallback | mailctl already has one (COPY + `\Deleted` + EXPUNGE); it moves with the session |
| I4 | `CONDSTORE`/`QRESYNC` select parameters and `VANISHED` unsupported | Not needed today; noted |
| I5 | No `COMPRESS` | Not needed today; noted |
| I6 | Our dependency pin has no upper bound | [#91][i91] |
| I7 | A nested group's closing `)` is glued onto its last item; an 8-bit last item is then sent as a literal, so the group never closes (amendment) | A trailing `ALL` closes the group — fixed in [#98][pr98] |

**Server modules hold one server software's quirks**, beside the protocol
client that needs them: `imap/servers/dovecot.py`,
`managesieve/servers/pigeonhole.py`, and a future `cyrus.py`. A quirk is
behaviour a server has beyond or against its RFC. [#39][i39]'s *does
`CREATE` subscribe* is not one: it is protocol behaviour, and the session
checks it for every server (see *Amendment*).

- **A server module is chosen by probing, never by configuration.** It reads
  IMAP `ID` and the ManageSieve `IMPLEMENTATION` capability. Both libraries
  already expose these (`IMAPClient.id_()`, `sievelib`'s
  `get_implementation()`). The observed values on MXroute are
  `name: Dovecot` and `Dovecot Pigeonhole` ([#18][i18]). A setting would
  be one more thing to go stale when the host migrates. See CONVENTIONS
  *Discover, don't hardcode*.
- **A server with no matching module gets plain protocol behaviour.** An
  unknown server is the normal case for a new provider, not an error.

**Quirks are keyed on observed behaviour or advertised capabilities, never on
version numbers.** MXroute's server reports no version on either protocol
([#18][i18]): both identity strings are deliberately version-free. A quirk
keyed on a version could therefore never be selected here, and it would
break silently on a server that does report one. Where a server gives a
version, it is a hint. It is never the only signal a quirk turns on.

### Layer 2: providers

**A provider composes layer-1 libraries and holds one host's setup and
policies.** For `mxroute` that is: `imap` plus `managesieve`, `redirect`
refused with a pointer to forwarders, the Roundcube `# rule:[NAME]` name
dialect written back, and the MXroute login advice. For `gmail`, later, it is
`imap` plus `gmail_api`.

**Every provider presents the same formalized interface.** It is #26's
settled position, and its constraints come with it:

- **Capabilities are data.** Each provider has a frozen, enumerable set that
  the core can read, print, and test against, with no `hasattr()` probing.
- **Specifics are namespaced and schema-described.** Ordering and `stop` for
  Sieve, labels for Gmail. An unknown key is an error, never ignored.
- **Validation happens before any network work.** A rule the provider cannot
  express is refused at parse time, through one error path that names the
  provider, the construct, and why.
- **The core offers only what the selected provider declares.** An option the
  provider cannot honour is not offered.
- **Selection is an interface operation.** The core cannot translate
  provider-specific rule content into an IMAP `SEARCH`. So *does this rule
  match this message* is answered by the provider, and the retroactive pass
  stands on it for every provider.

**The engine talks to a provider, never to a protocol session.** The engine
imports no layer-1 library. The CLI gains one setting, `provider`, which
defaults to `mxroute`. Nothing else in the CLI changes.

**Providers are in-tree and registered in-tree; the plugin framework is
deferred.** An in-tree registry with an ABC, plus a test that every
registered provider implements or explicitly declines each operation, gives
the exhaustiveness `himalaya` v2 gets from its compiler (see *Alternatives*).
Entry points can be added later as a backward-compatible change. A published
plugin API cannot be withdrawn the same way. So interface-first is
reversible and framework-first is not (#26).

### Each provider carries a probe-plus-documentation record

**Every provider keeps a dated record of what the host's documentation says
and what a probe observed**, in the tiers CONVENTIONS *Confidence* already
uses: **documented**, **observed**, and **unknown**. A tier is never
promoted without new evidence. An observation describes one server on one
day.

The record is refreshed:

- **when the provider is built;**
- **when the host announces a change** (MXroute's Dovecot 2.3 → 2.4 and
  panel migrations are the standing case);
- **on a cadence**, so a change nobody announced is still caught.

It builds on the capability work in [#16][i16], [#18][i18], and [#19][i19].
The MXroute facts in CONVENTIONS *Confidence* move into the `mxroute`
provider's record. The record says what was true when it was taken. At run
time the provider still discovers.

### Gmail

This ADR names Gmail as the second provider, but it does not build it.
Below is what the shape above is checked against:

- **`users.settings.filters` has create, delete, get, and list, and no
  update** (V). Its criteria are from, to, subject, query, negatedQuery,
  hasAttachment, excludeChats, size, and sizeComparison. Its actions are
  addLabelIds, removeLabelIds, and forward.
- **`users.labels` has full CRUD** (V).
- **There is no ManageSieve** (V by absence; R).
- **Gmail IMAP takes XOAUTH2** (V). Less-secure-app password access ended on
  2025-03-14; app passwords remain (V).
- **The Python client is `google-api-python-client`, Apache-2.0** (V).
- **So a Gmail provider is `imap` plus a new `gmail_api` library, with no
  Sieve** (A). This is the assumption the two-layer split is designed
  around, and it gets tested when that provider is built.

## Alternatives rejected

- **Naming a layer-1 library by server app (`dovecot`).** It would tie the
  protocol code to one server. Gmail speaks IMAP and is not Dovecot, so the
  Gmail provider could not reuse it. Worse, it would put protocol behaviour
  and one server's deviations in one module, which is the mix this ADR
  exists to undo. The server app gets a module of its own, under the
  protocol.
- **Quirks in the provider.** A quirk belongs to the server software, not
  the host. Two hosts on Dovecot would each carry a copy, and the copies
  would drift. One host migrating from one server to another would have to
  rewrite its provider instead of probing a new module. And the provider
  would be keying on server identity, which is layer 1's job.
- **Version-keyed quirks.** The server reports no version ([#18][i18]), so on
  the one host this tool serves a version-keyed quirk could never fire. Where
  a version is reported, it says less than the behaviour does. A backport or
  a distribution patch changes behaviour without moving the number, and
  behaviour is what the quirk is about.
- **An entry-point plugin framework now.** `himalaya` built the general
  version and abandoned it. Its v1 optional-feature registry produced
  roughly 25 runtime *not available* error variants. v2 replaced it with a
  closed enum and exhaustive matching, and the maintainer called the
  abstraction *"overkill… a maintenance tax"* (#26). There is no third-party
  provider to serve, and a published plugin API cannot be withdrawn
  compatibly. In-tree registration keeps that option open.
- **Switching libraries.** Nothing surveyed is better (V):
  - `managesieve` is PSF/GPL-3.0 and client-only;
  - `aioimaplib` is GPL-3.0 and async;
  - `imap-tools` is a high-level wrapper with no low-level gain;
  - stdlib `imaplib` is lower-level than what we have.

  Every gap above is closable in a wrapper. [ADR 0003][adr3] reached the
  same conclusion for the stack as a whole on 2026-08-14.

## Consequences

- **The migration is five steps, each its own PR, in order** (#92):
  1. this ADR;
  2. layer 1 `managesieve`: move `SieveSession` and the script helpers;
     close S1 ([#90][i90]), S4, and S5's timeout; keep S8's `debug=False`
     pin; add `servers/pigeonhole.py`;
  3. layer 1 `imap`: move `ImapSession` and folder normalization; fix I1
     ([#89][i89]); add `servers/dovecot.py`, the home for Dovecot's
     quirks when one is found;
  4. layer 2: the provider interface, the in-tree registry, and the
     `mxroute` provider; move the engine onto the provider; add the
     `provider` setting;
  5. provider maintenance: the per-provider record and the CONVENTIONS rule
     for refreshing it; move the MXroute record out of CONVENTIONS.
- **Behaviour against MXroute does not change.** Every CLI snapshot stays as
  it is unless a step says otherwise. The engine ends up importing no
  protocol library, and a test registers a fake provider to prove that
  adding one needs no engine change.
- **#26 closes when this ADR is accepted.** Its settled position is carried
  above, and its open question (is ordering in the interface?) is answered:
  ordering is a Sieve provider's declared specific, not a shared operation.
- **The three defects land with or before the layer-1 moves.** [#90][i90]
  (byte-exact backups) lands in or before the `managesieve` step, and
  [#89][i89] (non-ASCII search) in or before the `imap` step. [#91][i91]
  (upper bounds on both runtime dependencies) lands with or before the first
  of them, so the wrappers are written against a library major that cannot
  move under them.
- **Selection for `mxroute` is today's two-engine translation until the
  interpreter lands.** [ADR 0004][adr4] adopted `migadu/go-sieve`, and #26
  noted that an interpreter makes selection authoritative for Sieve. It is
  not built yet. `criteria.py` still says there is no Sieve interpreter
  anywhere in the tool. So the `mxroute` provider first answers selection
  with the current IMAP `SEARCH` translation plus the client-side
  re-check, and swaps in the interpreter behind the same operation later.
  The interface does not wait for it.
- **Unimplemented-by-us actions stay out of the provider.** `notify` and
  `vacation` are refused because mailctl does not generate them, not
  because a host disables them (CONVENTIONS *Rule conventions*). So they
  travel with the Sieve script handling in `managesieve`. Only `redirect`,
  a confirmed MXroute policy, moves to the `mxroute` provider.
- **More modules, and one more indirection on every call.** It is the price
  of a second provider, and it is paid now for a Gmail provider that does
  not yet exist. The shape is designed against the two hosts that
  concretely exist, not an imagined third, which is #26's caution against
  the generality `himalaya` paid for.
- **The provider-agnostic rule schema stays iceboxed.** [`ICEBOX.md`][icebox]
  *Declarative rules in YAML* depends on this seam and is not decided here.
  Namespaced provider specifics are the same mechanism that entry asks for,
  so nothing here forecloses it.

## Amendment

2026-09-27, made with step 4 of [#92][i92]. The decision above stands. This
records three things learned since it was accepted.

### The provider is a two-way translator

The operator, on [#92][i92]:

> the provider library should present--as much as possible--the same
> interface to the main program, while converting that input into the
> appropriate output to the provider (and vice versa).

*One formalized interface* therefore means translation in both
directions:

- **The engine speaks one provider-neutral model.** A rule is criteria
  plus an action spec. The model also covers folders, messages,
  capabilities, results, and `MailctlError`.
- **Outbound, the provider converts that model into its host's terms.**
  For `mxroute` that is Sieve text over ManageSieve, in Roundcube's
  rule-name dialect.
- **Inbound, it converts the host's answers back into the same model.** A
  parsed script becomes neutral rules. A server refusal becomes a
  `MailctlError`.
- **Sameness is the default.** Where hosts differ, the difference is
  declared as data. It is never a reason for the engine to branch on
  which provider it has. A test holds this: a fake second provider
  receives exactly the calls `mxroute` receives, in the same shape.

Step 4 settled one reading. **`ordering` and `stop` are capability flags**,
checked against fields the shared model already carries: a rule's
placement, and whether it stops evaluation. `rule_sets` covers a named or
activated script the same way. **Namespaced specifics** (`<namespace>.<key>`,
schema-described) are for parameters no shared field carries, such as
Gmail's labels. `mxroute` declares none. Both are checked before any
network work, through one refusal naming the provider, the construct, and
why.

### Two more library gaps

These were found while building steps 2 and 3, and are now in the tables
above:

- **S9 (`sievelib`).** The response reader loops forever when the
  connection closes mid-literal. This affects every command except
  `GETSCRIPT`, whose reader mailctl already replaced ([#95][i95]).
- **I7 (`IMAPClient`).** A nested group's closing `)` is glued onto its
  last item. When that item is 8-bit, it is then sent as a literal, so the
  group never closes. [#98][pr98] fixed it by appending `ALL` to such a
  group, which puts `SEARCH CHARSET UTF-8 (SUBJECT {5+}café ALL)` on the
  wire.

### #39 is protocol behaviour, not a Dovecot quirk

The decision placed *`CREATE` doesn't subscribe* ([#39][i39]) in
`dovecot.py`. That was wrong. RFC 3501's `CREATE` (6.3.3) says nothing
about subscription. The subscribed set, which `LSUB` returns, changes only
through `SUBSCRIBE` (6.3.6). So a server that does not subscribe on
`CREATE` is following the protocol.

That is why the `imap` session subscribes explicitly and re-reads `LSUB` to
confirm it, for every server. It is also why `dovecot.py` carries no quirk
yet.

### The neutral model is layer 2's, and the offer is enforced (#99)

Three readings, settled after step 4 ([#99][i99]):

- **The neutral model is defined in the provider layer.** Step 4
  re-exported the placement, diff, folder, and message records from the
  components. They are now defined in `providers/model.py`, which, like
  the interface, imports nothing from layer 1. A component keeps its own
  records, and each provider translates.
- **"The core offers only what the selected provider declares" hides,
  and does not remove.** The CLI reads the provider first, then builds
  its options from the capabilities. An option the provider does not
  declare is left out of help and usage, but still parses, so it meets
  the engine's refusal. The refusal is the backstop for every front-end.
- **Connection settings are declared as capability data.** Each provider
  names the connection settings it reads, and only those are offered.

## Based on

- [#26][i26] — the provider interface's settled position, and the
  `himalaya` evidence.
- [#92][i92] — the epic. *The shape* is the operator's direction of
  2026-09-27.
- The layer-1 library evaluation of 2026-09-27. Its sources:
  [sievelib][src-sievelib], [IMAPClient][src-imapclient],
  [RFC 5804][src-rfc5804], [Gmail filters][src-gfilters],
  [Gmail labels][src-glabels], [Gmail XOAUTH2][src-xoauth2], and the
  [less-secure-apps transition][src-lsa].
- [ADR 0001][adr1] — why the MXroute side has no REST filter surface.
- [ADR 0003][adr3] — component language. It left this seam to #26, and any
  provider is Python under it.
- [ADR 0004][adr4] — the Sieve interpreter that makes Sieve selection
  authoritative once it is built.

[adr1]: 0001-standalone-cli-over-provider-resource.md
[adr3]: 0003-python-core-with-per-component-language-choice.md
[adr4]: 0004-adopt-go-sieve-as-the-evaluation-engine.md
[icebox]: ../ICEBOX.md
[i7]: https://github.com/harleypig/mailctl/issues/7
[i16]: https://github.com/harleypig/mailctl/issues/16
[i18]: https://github.com/harleypig/mailctl/issues/18
[i19]: https://github.com/harleypig/mailctl/issues/19
[i26]: https://github.com/harleypig/mailctl/issues/26
[i39]: https://github.com/harleypig/mailctl/issues/39
[i89]: https://github.com/harleypig/mailctl/issues/89
[i90]: https://github.com/harleypig/mailctl/issues/90
[i91]: https://github.com/harleypig/mailctl/issues/91
[i92]: https://github.com/harleypig/mailctl/issues/92
[i95]: https://github.com/harleypig/mailctl/issues/95
[i99]: https://github.com/harleypig/mailctl/issues/99
[pr98]: https://github.com/harleypig/mailctl/pull/98
[src-sievelib]: https://github.com/tonioo/sievelib
[src-imapclient]: https://github.com/mjs/imapclient
[src-rfc5804]: https://www.rfc-editor.org/rfc/rfc5804.txt
[src-gfilters]: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings.filters
[src-glabels]: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.labels
[src-xoauth2]: https://developers.google.com/workspace/gmail/imap/xoauth2-protocol
[src-lsa]: https://knowledge.workspace.google.com/admin/sync/transition-from-less-secure-apps-to-oauth
