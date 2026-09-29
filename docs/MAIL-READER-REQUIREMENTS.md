# Mail reader requirements

What a mail reader must, should, and may do, graded in the [RFC 2119][2119]
sense, with where mailctl stands on each today. No single published source is
a complete list: the nearest are the ungraded 1995 checklist
[RFC 1820][1820] and the client advice in [RFC 2683][2683] and
[RFC 4549][4549], so each grade below links the spec it comes from, or says
*common practice* where the practice is near-universal and no spec grades it.

Marks: `[built]` built, `[partial]` partly built, `[—]` not built;
`[out]` falls outside the scoping rule in `.claude/CONVENTIONS.md`.

## Connection and security

* MUST — **Both TLS styles**: connect with implicit TLS and with STARTTLS. [RFC 9051 §11.2][9051-11.2] `[built]`
* MUST — **Certificate check**: verify the server's certificate against its hostname. [RFC 9051 §11.1][9051-11.1] `[built]`
* MUST — **Modern TLS**: use TLS 1.2 or newer. [RFC 9051 §11.1][9051-11.1] `[built]`
* MUST — **No password in the clear**: send no credential until a verified TLS session is up. [RFC 8314 §5.1][8314-5.1] `[built]`
* MUST — **Server alerts**: show the server's ALERT text to the user, prominently. [RFC 2683 §3.4.11][2683-3.4.11] `[built]`
* SHOULD — **Security indicator**: show how well each account's connection is protected. [RFC 8314 §5][8314-5] `[partial]`
* SHOULD — **Capability discovery**: learn the server's features from what it advertises. [RFC 2683 §3.3.1][2683-3.3.1] `[built]`
* SHOULD — **Password sources**: take the password from a keyring, file, or command, not only a prompt. Common practice. `[built]`
* SHOULD — **Secret stays secret**: never show the password in output, logs, or errors. Common practice. `[built]`
* MAY — **OAuth sign-in**: log in with an OAuth token instead of a password. [RFC 7628][7628] `[—]`

## Accounts and setup

* SHOULD — **Find servers from the address**: look up IMAP and submission servers in DNS. [RFC 8314 §5.1][8314-5.1], [RFC 6186][6186] `[—]`
* SHOULD — **Several accounts**: work with more than one mailbox. Common practice. `[partial]`
* MAY — **Autoconfig**: fetch settings published by the provider. [draft-ietf-mailmaint-autoconfig][autoconfig] `[—]`
* MAY — **Settings file**: keep settings in a file, the environment, or flags. `[built]`
* MAY — **Quota**: show how much of the account's storage is used. [RFC 9208][9208] `[—]`
* MAY — **Provisioning**: create or delete mailbox accounts. `[—]` `[out]`

## Folders

* MUST — **Non-ASCII folder names**: display folder names in any script. [RFC 9051 §5.1][9051-5.1] `[built]`
* SHOULD — **Server's delimiter**: accept folder paths in the server's own hierarchy separator. [RFC 2683 §3.4.10][2683-3.4.10] `[built]`
* SHOULD — **Subscribed and all views**: show either the subscribed folders or every folder. [RFC 2683 §3.2.2][2683-3.2.2] `[built]`
* SHOULD — **Subscribe**: subscribe and unsubscribe folders. [RFC 2683 §3.2.2][2683-3.2.2] `[built]`
* SHOULD — **Folder management**: create, rename, and delete folders. Common practice. `[partial]`
* SHOULD — **Counts**: show each folder's total and unread messages. Common practice. `[built]`
* SHOULD — **Special folders**: recognise Sent, Drafts, Trash, Junk, and Archive by their role. Common practice; roles from [RFC 6154][6154]. `[—]`

## Message list

* SHOULD — **List a folder**: show its messages with date, sender, and subject. Common practice; [RFC 1820 §3.5][1820] `[built]`
* SHOULD — **State in the list**: show each message's read and flagged state. Common practice. `[built]`
* SHOULD — **First unread**: go straight to the unread mail. [RFC 2683 §3.2.1.2][2683-3.2.1.2] `[built]`
* SHOULD — **Long folders**: page through a large folder a screenful at a time. [RFC 2683 §3.2.1.2][2683-3.2.1.2] `[partial]`
* SHOULD — **Sort**: order by date, arrival, sender, subject, or size. [RFC 1820 §3.9][1820], [RFC 5256][5256] `[partial]`
* MAY — **Preview line**: show the opening words of each message. [RFC 8970][8970] `[—]`
* MAY — **Attachment indicator**: mark messages that carry attachments. [RFC 2683 §3.2.1.4][2683-3.2.1.4] `[—]`
* MAY — **Size**: show each message's size. `[built]`

## Reading and rendering

* MUST — **Transfer decoding**: decode base64 and quoted-printable. [RFC 2049 §2][2049-2] `[built]`
* MUST — **Encoded headers**: decode encoded words in headers. [RFC 2049 §2][2049-2], [RFC 2047 §6][2047-6] `[built]`
* MUST — **Character sets**: show text in its declared charset, with a safe fallback. [RFC 2049 §2][2049-2] `[built]`
* MUST — **Unknown multipart**: treat an unknown multipart subtype as mixed. [RFC 2046 §5.1.3][2046-5.1.3] `[built]`
* MUST — **Unknown types**: offer an unknown content type as an attachment, never as raw text. [RFC 2049 §2][2049-2] `[built]`
* MUST — **Attached messages**: show a forwarded message (message/rfc822) inside another. [RFC 2049 §2][2049-2] `[partial]`
* MUST — **Malformed headers**: still show a message whose encoded words are broken. [RFC 2047 §6.2][2047-6] `[built]`
* SHOULD — **Alternatives**: show one version of a multipart/alternative, not all of them. [RFC 2046 §5.1.4][2046-5.1.4] `[built]`
* SHOULD — **UTF-8 headers**: show internationalised addresses and headers. Common practice; [RFC 6532][6532] `[built]`
* SHOULD — **HTML mail**: show HTML mail readably and safely. Common practice. `[built]`
* SHOULD — **No remote content**: load no remote images or trackers unless asked. Common practice. `[built]`
* SHOULD — **Mark read on open**: mark a message read when it is opened. Common practice. `[partial]`
* SHOULD — **Full headers and source**: show every header, and the raw message. Common practice. `[built]`
* SHOULD — **Phishing warning**: warn when the server tags a message as phishing and junk. [RFC 9051 §2.3.2][9051-2.3.2] `[—]`
* MAY — **Read without marking**: open a message and leave it unread. [RFC 9051 §6.4.5][9051-6.4.5] `[built]`
* MAY — **Flowed text**: reflow format=flowed paragraphs. [RFC 3676 §4.1][3676-4.1] `[—]`
* MAY — **Authentication results**: show SPF, DKIM, and DMARC results from the account's own server only. [RFC 8601 §4.1][8601-4.1] `[—]`
* MAY — **Inline images**: show images inside the message body. `[—]` `[out]`
* MAY — **Print**: print a message. [RFC 1820 §4][1820] `[—]` `[out]`

## Search

* SHOULD — **Search**: find messages by header, body text, date, and state. [RFC 9051 §6.4.4][9051-6.4.4] `[built]`
* SHOULD — **Non-ASCII search**: search for text in any script. [RFC 9051 §6.4.4][9051-6.4.4] `[built]`
* MAY — **All folders**: search every folder at once. [RFC 7377][7377] `[—]`
* MAY — **Saved searches**: keep a search to run again, or as a virtual folder. `[partial]`
* MAY — **Native query**: pass a query in the server's own search language. `[built]`
* MAY — **Sender summary**: count a folder's mail by sender, domain, or list. `[built]`

## Filtering and rules

* SHOULD — **Filters**: sort incoming mail automatically by rules. Common practice; [RFC 1820 §3.8][1820] `[built]`
* SHOULD — **Junk marking**: mark a message as junk or not junk. Common practice; keywords in [RFC 9051 §2.3.2][9051-2.3.2] `[partial]`
* MAY — **Server-side rules**: store rules on the server, so they run with no client open. [RFC 5804][5804] `[built]`
* MAY — **Rules on existing mail**: apply a rule to mail already delivered. `[built]`
* MAY — **Rule from a message**: build a rule from a message's headers. `[built]`
* MAY — **Rule order and state**: reorder, disable, and enable rules. `[built]`
* MAY — **Rule backup**: back up and restore the rule set. `[built]`
* MAY — **Mute a thread**: file away future replies in a conversation. `[—]`
* MAY — **Autoresponder**: reply automatically while away. [RFC 5230][5230] `[—]`
* MAY — **Forwarding**: forward mail to another address. `[—]` `[out]`
* MAY — **Unsubscribe**: leave a mailing list from its List-Unsubscribe header, with consent. [RFC 2369 app. B][2369], [RFC 8058 §3.2][8058-3.2] `[—]` `[out]`

## Flags and state

* SHOULD — **Read and unread**: mark messages read or unread. [RFC 9051 §2.3.2][9051-2.3.2] `[built]`
* SHOULD — **Flag**: flag and unflag messages. [RFC 9051 §2.3.2][9051-2.3.2] `[built]`
* SHOULD — **Delete**: delete messages, to Trash or permanently. Common practice; [RFC 1820 §3.5][1820] `[partial]`
* SHOULD — **Move and copy**: move or copy messages to another folder. Common practice; [RFC 6851][6851] `[partial]`
* MAY — **Keywords**: set and clear tags on messages. [RFC 9051 §2.3.2][9051-2.3.2] `[built]`
* MAY — **Undo**: undo the last change. `[—]`

## Threading

* MAY — **Conversations**: group messages into threads by their references. [RFC 5256][5256], [RFC 5322 §3.6.4][5322-3.6.4] `[—]` `[out]`

## Attachments

* SHOULD — **List attachments**: name each attachment, with its type and size. [RFC 2183 §2][2183-2] `[built]`
* SHOULD — **Save safely**: save an attachment under a cleaned file name, never a path it supplies. [RFC 2183 §2.3][2183-2.3] `[—]` `[out]`
* MAY — **Open**: open an attachment in another program. [RFC 1820 §4][1820] `[—]` `[out]`

## Composing and sending

* SHOULD — **Compose**: write and send a new message. Common practice; [RFC 1820 §3.5][1820] `[—]` `[out]`
* SHOULD — **Reply**: reply to the Reply-To address, else the From. [RFC 5322 §3.6.2][5322-3.6.2] `[—]` `[out]`
* SHOULD — **Reply in thread**: keep a reply in its conversation. [RFC 5322 §3.6.4][5322-3.6.4] `[—]` `[out]`
* SHOULD — **Forward**: forward a message, inline or attached. Common practice; [RFC 1820 §3.5][1820] `[—]` `[out]`
* SHOULD — **Submission over TLS**: send through the submission port with TLS. [RFC 8314 §3.3][8314-3.3] `[—]` `[out]`
* SHOULD — **Read receipts**: send a read receipt only with the user's consent. [RFC 8098 §2.1][8098-2.1] `[—]` `[out]`
* MAY — **Drafts**: save an unfinished message and resume it. `[—]` `[out]`
* MAY — **Signatures**: add a signature block. `[—]` `[out]`
* MAY — **Attach files**: attach files to an outgoing message. `[—]` `[out]`

## Address book

* SHOULD — **Contacts**: keep addresses and complete them while composing. Common practice; [RFC 1820 §3.10][1820] `[—]` `[out]`
* MAY — **Contact sync**: sync contacts with a server. [RFC 6352][6352] `[—]` `[out]`

## Offline and sync

* MUST — **Reset folders**: stop acting on remembered message IDs once the server has renumbered the folder. [RFC 4549 §4.1][4549-4.1], [RFC 2683 §3.4.3][2683-3.4.3] `[partial]`
* MAY — **Offline reading**: read and change mail while disconnected, and sync later. [RFC 4549][4549] `[—]` `[out]`
* MAY — **Fast resync**: catch up on changes made elsewhere without rereading the folder. [RFC 7162][7162] `[—]` `[out]`

## Notifications

* MAY — **New mail push**: learn of new mail as it arrives. [RFC 2177][2177], [RFC 5465][5465] `[—]` `[out]`
* MAY — **Alerts**: raise a desktop or sound alert for new mail. `[—]` `[out]`

## Accessibility

* SHOULD — **Keyboard only**: make every function usable from the keyboard. [WCAG 2.2 §2.1.1][wcag-keyboard] `[built]`
* SHOULD — **Not colour alone**: never carry meaning by colour alone. [WCAG 2.2 §1.4.1][wcag-color] `[built]`

## Encryption and signing

* MAY — **Verify signatures**: check S/MIME and OpenPGP signatures. [RFC 8551][8551], [RFC 9580][9580] `[—]` `[out]`
* MAY — **Decrypt**: read encrypted S/MIME and OpenPGP mail. [RFC 8551][8551], [RFC 9580][9580] `[—]` `[out]`
* MAY — **Protected headers**: show the protected subject and headers of a signed or encrypted message. [RFC 9788 §4.6][9788-4.6] `[—]` `[out]`
* MAY — **Autocrypt**: exchange keys automatically in mail headers. [Autocrypt Level 1][autocrypt] `[—]` `[out]`

## Configuration and automation

* MAY — **Machine-readable output**: print results as data a script can read. `[built]`
* MAY — **Dry run**: show what a change would do, and change nothing. `[built]`
* MAY — **Stable exit codes**: report success and failure in codes a script can test. `[built]`
* MAY — **Unattended runs**: run from a scheduler with no prompt. `[built]`
* MAY — **Server drift**: report when the server's features change. `[built]`

[2119]: https://www.rfc-editor.org/rfc/rfc2119.html
[1820]: https://www.rfc-editor.org/rfc/rfc1820.html
[2046-5.1.3]: https://www.rfc-editor.org/rfc/rfc2046.html#section-5.1.3
[2046-5.1.4]: https://www.rfc-editor.org/rfc/rfc2046.html#section-5.1.4
[2047-6]: https://www.rfc-editor.org/rfc/rfc2047.html#section-6
[2049-2]: https://www.rfc-editor.org/rfc/rfc2049.html#section-2
[2177]: https://www.rfc-editor.org/rfc/rfc2177.html
[2183-2]: https://www.rfc-editor.org/rfc/rfc2183.html#section-2
[2183-2.3]: https://www.rfc-editor.org/rfc/rfc2183.html#section-2.3
[2369]: https://www.rfc-editor.org/rfc/rfc2369.html#appendix-B
[2683]: https://www.rfc-editor.org/rfc/rfc2683.html
[2683-3.2.1.2]: https://www.rfc-editor.org/rfc/rfc2683.html#section-3.2.1.2
[2683-3.2.1.4]: https://www.rfc-editor.org/rfc/rfc2683.html#section-3.2.1.4
[2683-3.2.2]: https://www.rfc-editor.org/rfc/rfc2683.html#section-3.2.2
[2683-3.3.1]: https://www.rfc-editor.org/rfc/rfc2683.html#section-3.3.1
[2683-3.4.3]: https://www.rfc-editor.org/rfc/rfc2683.html#section-3.4.3
[2683-3.4.10]: https://www.rfc-editor.org/rfc/rfc2683.html#section-3.4.10
[2683-3.4.11]: https://www.rfc-editor.org/rfc/rfc2683.html#section-3.4.11
[3676-4.1]: https://www.rfc-editor.org/rfc/rfc3676.html#section-4.1
[4549]: https://www.rfc-editor.org/rfc/rfc4549.html
[4549-4.1]: https://www.rfc-editor.org/rfc/rfc4549.html#section-4.1
[5230]: https://www.rfc-editor.org/rfc/rfc5230.html
[5256]: https://www.rfc-editor.org/rfc/rfc5256.html
[5322-3.6.2]: https://www.rfc-editor.org/rfc/rfc5322.html#section-3.6.2
[5322-3.6.4]: https://www.rfc-editor.org/rfc/rfc5322.html#section-3.6.4
[5465]: https://www.rfc-editor.org/rfc/rfc5465.html
[5804]: https://www.rfc-editor.org/rfc/rfc5804.html
[6154]: https://www.rfc-editor.org/rfc/rfc6154.html
[6186]: https://www.rfc-editor.org/rfc/rfc6186.html
[6352]: https://www.rfc-editor.org/rfc/rfc6352.html
[6532]: https://www.rfc-editor.org/rfc/rfc6532.html
[6851]: https://www.rfc-editor.org/rfc/rfc6851.html
[7162]: https://www.rfc-editor.org/rfc/rfc7162.html
[7377]: https://www.rfc-editor.org/rfc/rfc7377.html
[7628]: https://www.rfc-editor.org/rfc/rfc7628.html
[8058-3.2]: https://www.rfc-editor.org/rfc/rfc8058.html#section-3.2
[8098-2.1]: https://www.rfc-editor.org/rfc/rfc8098.html#section-2.1
[8314-3.3]: https://www.rfc-editor.org/rfc/rfc8314.html#section-3.3
[8314-5]: https://www.rfc-editor.org/rfc/rfc8314.html#section-5
[8314-5.1]: https://www.rfc-editor.org/rfc/rfc8314.html#section-5.1
[8551]: https://www.rfc-editor.org/rfc/rfc8551.html
[8601-4.1]: https://www.rfc-editor.org/rfc/rfc8601.html#section-4.1
[8970]: https://www.rfc-editor.org/rfc/rfc8970.html
[9051-2.3.2]: https://www.rfc-editor.org/rfc/rfc9051.html#section-2.3.2
[9051-5.1]: https://www.rfc-editor.org/rfc/rfc9051.html#section-5.1
[9051-6.4.4]: https://www.rfc-editor.org/rfc/rfc9051.html#section-6.4.4
[9051-6.4.5]: https://www.rfc-editor.org/rfc/rfc9051.html#section-6.4.5
[9051-11.1]: https://www.rfc-editor.org/rfc/rfc9051.html#section-11.1
[9051-11.2]: https://www.rfc-editor.org/rfc/rfc9051.html#section-11.2
[9208]: https://www.rfc-editor.org/rfc/rfc9208.html
[9580]: https://www.rfc-editor.org/rfc/rfc9580.html
[9788-4.6]: https://www.rfc-editor.org/rfc/rfc9788.html#section-4.6
[autoconfig]: https://datatracker.ietf.org/doc/draft-ietf-mailmaint-autoconfig/
[autocrypt]: https://docs.autocrypt.org/level1.html
[wcag-keyboard]: https://www.w3.org/TR/WCAG22/#keyboard
[wcag-color]: https://www.w3.org/TR/WCAG22/#use-of-color
