# 7. Four layers: interfaces, utilities, session, provider

- Status: accepted
- Date: 2026-09-28
- Amends [ADR 0006][adr6]: the provider stays one package per host, now
  in two halves. Recorded for epic [#137][i137].

## Context

**`engine.py` does three jobs at once.** It opens the provider from the
configuration. It also holds every piece of work the tool does: planning a
folder, merging a rule, backing up and uploading, and planning and running
the existing-mail pass. And it holds the safety policy those steps run
under. That is one module for connection handling, for the logic built on
it, and for the rules the logic must obey. When something goes wrong, there
is no line between *the server said no* and *we built the wrong thing*.

**The provider mixes the same two things one layer down.** [ADR 0006][adr6]
made the provider a two-way translator between the neutral model and the
host. Today one `Provider` class answers both offline questions (translate
an action, parse a script, merge a rule into it, render a diff, word a
refusal) and questions that need the server (list the rule sets, store one,
select mail, move it). The IMAP client-side re-check, which makes the
retroactive pass match what the Sieve rule will do, sits inside the `imap`
component. It is composition logic, not protocol.

**Every front-end must get every command.** CONVENTIONS *Every command is
in every interface* (operator, 2026-09-27) asks for the CLI, a TUI, a GUI,
and the web to offer the same operations. The CLI opens a connection, runs
one command, and exits. The other three keep running, so the connection
has to outlive one command. Nothing in the tool keeps a session open today.

## Decision

**mailctl is layered in four, and each layer calls only the one below it**
(operator, 2026-09-28, [#137][i137]):

```text
interfaces   cli · tui · gui · web        parse input, render output, ask the user
utilities    rules · mail · folders · reading · reports   (their own modules)
session      engine.py: config → pick provider → open, keep, reconnect, close
provider     dialect   (offline: neutral model ⇄ host language, host refusals as data)
             transport (communication only: one server call, or the host's own composite)
```

### The provider has two halves

- **The dialect is offline and host-specific.** It translates the neutral
  model into the host's language and back: it builds, parses, and edits
  (for `mxroute`: Sieve text in Roundcube's `# rule:[NAME]` dialect). It
  holds the host's refusals as data, such as `redirect` on MXroute, and the
  host's wording. It never touches the network.
- **The transport handles communication only.** Each operation is one
  exchange with the server, or a composite the host performs natively. `MOVE`
  is one operation where the server has it, and the transport's
  `COPY` + `EXPUNGE` fallback stands in for it where it does not.

The operator's statement of the rule, in session on 2026-09-28:

> any activity on the provider side ... only handles communication with
> that provider's server, it doesn't manage loops (unless the protocol
> supports it), it doesn't manage the building or validation of a filter,
> it just tries to save it and reports success or failure

Building, validation, and presentation belong to the utilities, which use
the dialect for the host-specific parts.

### Three kinds of operation

- **Atomic operations** are one exchange with the server, or a composite
  the host does natively. They live in the provider's transport, and the
  utilities pass them straight through.
- **Syntax sugar** is another spelling of an operation that already exists:
  `--unread` becomes `UNSEEN`, and `--older-than 30d` becomes `BEFORE`. It
  lives in the input model, so every front-end gets it.
- **Utilities** are host-independent logic. They compose atomic operations
  and keep state on the client, such as reading one message after another.
  Each group of utilities (rules, mail, folders, reading, reports) is its
  own module.

**Connection routines stay separate from utilities**, in the operator's
words, *"for ease of debugging and tracking"*. A failure is then either a
transport call that failed or a utility that composed the calls wrongly,
and the layer it came from says which.

### The session persists

**The session lives as long as the app does.** The CLI keeps its shape: it
opens the session, runs one command, and closes it. The long-running
front-ends keep one open. Its requirements:

- **Reconnect transparently for reads only.** A failed write is reported
  and never retried. A retried write can land twice, and a retry hides the
  failure the user needs to see.
- **One session per user for the web front-end.**
- **Commands on one IMAP connection are serialised**, and every call
  re-selects its folder rather than trusting the one left selected by the
  previous call.
- **Each protocol's connection opens the first time it is needed.** A
  command that only reads rules never opens IMAP.

### Safety policy lives with the utilities

**The safety policy moves from `engine.py` to the utilities.** That policy
is: back up before every upload, merge and never overwrite ([ADR
0002][adr2]), and the `--max-messages` ceiling, re-checked when a mail plan
is carried out. **Interfaces call only utilities**, never the session or the
transport directly. So no front-end can reach a write without passing
through the policy.

## Alternatives rejected

- **Moving all building and validation into one generic builder in the
  engine.** This would keep the provider thin by putting every build step
  in shared code. It was rejected because a Sieve builder is not generic.
  Rule text, the Roundcube name dialect, and which extensions a rule needs
  are all Sieve's, and a Gmail filter is a JSON object with its own fields.
  A generic builder would have to branch on the host to know what to build,
  which is a dialect under another name. The host-specific part therefore
  stays in the dialect, and only the host-independent composition goes to
  the utilities.

## Consequences

- **The work is four steps, each its own PR, in order** ([#137][i137]).
  Each keeps the suite green and the CLI snapshots unchanged, unless it
  says otherwise:
  1. [#133][i133]: move the utilities out of `engine.py` into
     `mailctl/utilities/`, one module per group. `engine.py` keeps only the
     session: config → pick provider → open, keep, reconnect, close.
  2. [#134][i134]: split each provider's methods into dialect and
     transport. Move the IMAP client-side re-check out of the `imap`
     component and into the mail utility.
  3. [#135][i135]: the persistent session, with lazy open per protocol,
     reconnect for reads, and no retry for writes.
  4. [#136][i136]: enforce *interfaces call only utilities*, and update
     CONVENTIONS and TESTS to match.

  The mechanical move comes first, so the later diffs land in smaller
  files.
- **ADR 0006's layers stand, with the provider refined.** The components
  are unchanged as layer 1. The provider is still one package per host,
  selected and registered as before, and now has two halves. ADR 0006's
  *engine talks only to a provider* now reads *the session opens the
  provider, and the utilities use it*.
- **The `imap` component gets simpler.** Once the re-check leaves it, its
  search returns what the server matched, and the mail utility narrows that
  to what the rule will do. That is the same split this ADR draws
  everywhere, applied to the one piece of composition that sat in layer 1.
- **More modules, and one more hop on every call.** This is the price of
  being able to tell a server failure from a logic failure, and of a
  session the long-running front-ends can share. It is paid before those
  front-ends exist, because the CLI is the only one today.
- **A write failure is final for that command.** Some writes that would
  have succeeded on a retry will be reported as failures. That is accepted
  on purpose: a user can re-run a command, but a doubled upload or a
  doubled move cannot be undone without their noticing it first.

## Based on

- [#137][i137] — the epic, with the operator's decision of 2026-09-28.
- [#132][i132] — this record.
- [ADR 0006][adr6] — the component and provider layers this refines.
- [ADR 0002][adr2] — merge, never overwrite, one of the policies that moves
  with the utilities.

[adr2]: 0002-non-destructive-script-merge.md
[adr6]: 0006-two-layer-component-and-provider-architecture.md
[i132]: https://github.com/harleypig/mailctl/issues/132
[i133]: https://github.com/harleypig/mailctl/issues/133
[i134]: https://github.com/harleypig/mailctl/issues/134
[i135]: https://github.com/harleypig/mailctl/issues/135
[i136]: https://github.com/harleypig/mailctl/issues/136
[i137]: https://github.com/harleypig/mailctl/issues/137
