# MXRoute specifics

MXRoute documents very little of its Sieve surface, so what mailctl knows
about it is kept in three tiers — documented, observed on one server, and
unknown — in the provider's record,
[mailctl/providers/mxroute/RECORD.md][record], which has the sources and
dates. Nothing is promoted a tier to make the documentation read better,
and nothing observed is built in: mailctl reads the capabilities, the
active script, and the folder delimiter from the server on every run.

## Documented

* The `redirect` action is **disabled server-side** — MXRoute announced this
  on 2024-03-22, saying their own forwarders "are designed to properly handle
  SRS" where a Sieve redirect does not. `--redirect` fails with a pointer to
  the panel's Forwarders (or the `mxroute_forwarder` Terraform resource).
* The username is the **full email address**, on both IMAP and ManageSieve.
* The hostname is **per-account** — the same as your primary MX record, shown
  on the panel's Email Clients page. There is deliberately no default.
* IMAP is **993** (implicit TLS) or **143** (STARTTLS). mailctl picks the
  mode from the port: anything other than 143 is treated as implicit TLS.
* MXRoute's REST API exposes nothing for filters or Sieve, which is why this
  tool speaks ManageSieve rather than an API.

## Observed on one server, not promised

A probe of one account's server saw these. They describe that server on
that day, not MXRoute.

* **ManageSieve on port 4190 with STARTTLS worked.** That is the IANA/RFC
  5804 registered port and the Dovecot default, which is why it is
  mailctl's default; MXRoute documents neither. `--sieve-port` and
  `--sieve-tls` exist because of that, and a connection failure says so
  rather than implying you mistyped.
* **The folder delimiter was `.`** (Maildir++, so `INBOX.Lists.News`), and an
  `INBOX.spam` folder existed — lowercase, not `INBOX.Junk`. Neither is
  assumed. The delimiter is **detected at runtime** from the server's folder
  list, and folder names are matched against that list — type `Lists/News`
  or `INBOX.Lists.News` and the server's spelling is used for both the Sieve
  rule and the move. `mailctl folder list` is the authority for your account.
  The one exception is `--no-mail`, which has no folder list to consult and
  falls back to `.` (or `--delimiter`), and warns that it did.
* **`vacation` was advertised and `enotify` was not.** mailctl refuses both
  `vacation` and `notify` as **our own choice**, not as an MXRoute
  limitation: the panel does autoresponders, and mailctl does not write
  either action. Its error says so and points at the control panel.
  `mailctl server test` prints what your server actually advertises.

Folder names are **case-sensitive**, except `INBOX` itself (RFC 3501), so
`INBOX.Lists` and `INBOX.lists` are two folders. When the folder you name
does not exist but one differing only in case does, mailctl warns and
names both — and with `--create-folder`, says a second folder will be
created beside it.

## Unknown

* **Whether any of the observations above hold for another server**, or for
  the same one after MXRoute's migrations.
* **ManageSieve script-size, script-count, and rate limits.** There is no
  documented ceiling to design against.
* **Whether your account has a filter before Sieve** (below).

The record lists the rest.

## Which script mailctl edits

The active script's name is read from `LISTSCRIPTS` and written back to, and
is never guessed — the webmail's script name is server-side config, and
MXRoute is mid-migration on both its panel and Dovecot. `--filterset` overrides
it. With nothing active, a script called `mxfilter` — what the tool created
under its old name — is still recognised as its own and reused rather
than a second one made beside it; the name `mailctl` is used only when there
is neither.

The server runs one script, and editing a different one does not change
which. `--filterset NAME` on a script that is not the active one stores the
change and leaves NAME inactive, and mailctl says so before it uploads;
`--activate` makes NAME the active script as well. When the account has no
active script at all, the script mailctl writes is activated, since
otherwise nothing would run it.

## A filtering stage mailctl cannot see

mailctl sees **Sieve only**. Mail may pass through an earlier filter first,
and nothing mailctl reports covers it.

DirectAdmin's **Email Filters** panel writes an Exim filter that runs after
the mail server accepts a message and before it is delivered to the mailbox,
which is where Sieve runs. A message that filter matches is dropped (or sent
to a spam folder) there, and Sieve never sees it. DirectAdmin's filter rules
block by domain, address, word, or size; they do not file mail into folders.
MXRoute has provisioned such a filter with `where=delete`, which discards
high-scoring mail before Sieve.

**What is not known** is whether your account has one. MXRoute documents
neither this filter nor Sieve, and is phasing DirectAdmin out — MXRoute has
said that new customers already have no interaction with DirectAdmin — so
whether accounts set up since then still carry the filter is unconfirmed.

mailctl cannot read or change that filter, and will not: DirectAdmin's
filters need domain-owner credentials, and mailctl logs in as a mailbox. So:

* `mailctl server test` reports the Sieve and IMAP side, not the whole path
  mail takes.
* `mailctl filter list` works out which rules can never fire from their order
  in the Sieve script. A message dropped before Sieve is outside that
  analysis.
* A filter set in the panel is invisible here. If a rule never seems to fire,
  the mail may never have reached Sieve: check the panel's filters, if your
  account has them.

[record]: ../mailctl/providers/mxroute/RECORD.md
