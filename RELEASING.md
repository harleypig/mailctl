# Releasing

How a version of `mailctl` is cut — and, first, what "released" does and does
not mean here.

## Nothing is published

`mailctl` is **not on PyPI, not in any registry, and has no release
pipeline.** There is no publish workflow, no signing setup, and no packaging
step beyond the `setuptools` metadata in `pyproject.toml`. It is installed
from a checkout.

Whether to publish it is an open question nobody has needed answered. That
question is **deliberately left unanswered here** rather than pre-documented:
writing down a release path that does not exist produces instructions nobody
has run, which age into something worse than an honest gap. If publication is
ever adopted it is a decision in its own right — record it as an ADR and add
the concrete steps to this file at that point.

## Installing

From a checkout:

```sh
uv pip install -e .          # editable, into the repo's .venv (development)
uv tool install .            # isolated, on PATH (the uv equivalent of pipx)
pipx install .               # same, via pipx
```

The console entry point is `mailctl` (`mailctl.cli:main`), and
`python -m mailctl` works from a checkout without installing anything.

## What v1 promises

From `v1.0.0` on, everything under *Frozen* below stays as it is for every
1.x release. A change that breaks any of it ships only in a new major
version, 2.0.0, and is listed under `BREAKING CHANGES:` in the
[changelog](CHANGELOG.md). Everything under *Free to change* can change in
any release. The operator settled the two open questions on [#282]: the
API is frozen as well as the command line, and a break after v1 is clean,
with no deprecation period.

### Frozen

**The command line (`mailctl`):**

1. **Command paths and their options.** Every `mailctl <group> <action>`
   path, and every option each takes, keeps its name and meaning. An
   option may gain new accepted values. It never loses one. An option is
   matched by its whole name, never by a prefix, so a new option cannot
   change what an old command line means.
2. **Exit status.** `0` done, including "nothing to change". `1` a
   failure or refusal. `2` the command line was rejected. `3` and `4`
   `server baseline check` drift, informational and serious. `130`
   interrupted.
3. **`--json` documents.** Every document listed in
   [docs/SCRIPTING.md][scripting], at `"version": 2`. stdout holds
   the document alone. A failure or refusal, exit status 1, is one JSON
   line, the last one on stderr. A command line rejected with exit status
   2 ends the same way: its usage and reason come first, as text, and one
   coded JSON line follows them.
4. **Documents with their own version.** The filter document
   (`--build-filter --json`, read by `--filter`), version 1; the `server
   probe --json` document, version 1; a saved baseline, version 1; and
   the drift report, version 1. Each keeps its own version number.
   mailctl 1.x reads every filter document and baseline that any 1.x
   release wrote.
5. **`mail search --uids-only`**: one UID per line, nothing else.
6. **Error codes.** A refusal that carries a `code` keeps that code. A
   code is never renamed or removed, and never reused for a different
   condition. New codes may be added, and a refusal with no code may gain
   one. A script should treat an unknown code as an ordinary failure.
7. **Settings.** Every `MAILCTL_*` variable, every `config.toml` key, the
   config directory (`$XDG_CONFIG_HOME/mailctl/`), the order settings are
   resolved in (flag, env file, environment, config file, default), and
   the password ladder.
8. **Backups.** Where they go by default
   (`$XDG_CONFIG_HOME/mailctl/backups`), that each holds the server's
   exact bytes, that one never replaces another file, and the file name
   `<filterset>-<YYYYmmddTHHMMSS.mmmZ>.sieve`.
9. **Safety behaviour.** Merge, never overwrite. A backup before every
   upload. *Show, then change*. The `--max-messages` ceiling. The refusal
   when the filter set changed, or another was made active, since the
   plan was shown. No 1.x release removes or weakens any of these.

**The API (`mailctl-api`):**

1. **The carrier and framing.** JSON-RPC 2.0 over stdin and stdout, one
   message per line. stderr is diagnostics only.
2. **The protocol, at version 1** (`initialize`'s `protocol`). Method
   names, their parameters, and their results. The plan, decide and
   execute pairing, with single-use plan ids, and at least the newest
   32 plans kept waiting. `credentials.prompt`. `$/cancelRequest` and
   `-32800`. The notifications `event`, `server.alert` and `log`, and
   every event `kind`. The JSON-RPC error codes in the table in
   [docs/API.md › *Errors*][api-errors], and a refusal's `data.code`,
   held to the same rule as the command line's error codes.
3. **Results are the `--json` documents**, at the same version.

**The Docker image:**

1. **How it is run.** The `/config/mailctl` mount it requires, the
   refusal to run as root, `MAILCTL_BACKUP_ROOT` fixed to the mount, and
   the first word that picks what runs: `api` for the API, `tui` for the
   terminal UI, and anything else passed to `mailctl`. The image is not
   published, so this covers building and running it from a checkout.

### Additive, so allowed in any 1.x release

- A new command, group, or API method.
- A new optional option or parameter, whose absence means what it meant
  before.
- A new key in a JSON document, or a new event `kind`. Consumers must
  ignore keys they do not know.
- A new error code, or a code on a refusal that had none.
- A new provider, or a new capability a provider declares.
- A refusal of something that was always unsafe or wrong, where accepting
  it was a bug.

### Free to change

- Every message meant for a person: stderr text, prompts, progress and
  event lines, the wording of an error's `message`. Scripts branch on
  exit status and `code`, never on text.
- The layout of `server test`, the `server probe` text report, and
  `server baseline show`'s text output. Their exit status is frozen and
  their text is not.
- Help text and its layout.
- What `--verbose` and the API's `log` notification carry.
- The order of entries where a document does not say it is ordered.
- Everything inside the Python package. mailctl offers no Python library:
  `import mailctl` is not supported, whatever it exposes.
- The TUI (`mailctl-tui`). It is a separate package with its own version
  number, and its screens, keys and options are not covered by mailctl's
  v1. It reaches mailctl only through the API, which is frozen.
- The provider record's observations about MXroute, which describe the
  host rather than mailctl.

### How a break is made after v1

A break waits for the next major version. When it lands, it is a **clean
break, with no aliases**. The old command, option, or setting name is
refused with a message naming its replacement, as `mxfilter` was in the
rename and the flat command names were in 0.11.0. A retired command or
option fails with exit status 2. A retired setting name is reported by
name and never read. A document key that changes raises that document's
`version`. An API change that breaks raises `protocol`.

## Cutting a tag

Versioning is `repo`-scope semver — the full policy is in
[.claude/CONVENTIONS.md](.claude/CONVENTIONS.md) › *Versioning & tagging*. In
short:

- **`v1.y.z` from 1.0.0 on.** `y` for an addition, `z` for a fix, and a
  break only in a new major version, as *What v1 promises* says.
- **No API-major alignment.** The sibling provider ties its MAJOR to the
  MXroute REST API's major; this tool speaks ManageSieve and IMAP, which have
  no vendor version to track. Do not import that policy.
- **`v1.0.0` is the tag that makes the promise in *What v1 promises*,
  above.** From it on, a break waits for a new major version. Two gates
  on it are recorded. The rename (`mxfilter` → `mailctl`, and
  `MXROUTE_*` → `MAILCTL_*`) had to land first, because a name is part of
  the promise and renaming afterwards costs a major version; it has
  landed (#45). And every refusal, the command line's own checks on its
  arguments included, had to carry a `code` before the promise freezes
  the codes; that has landed too (#352). [docs/ERROR-CODES.md][codes] lists
  them.

Steps:

1. Merge the work to `master`.
2. Cut an **annotated** tag at the merge commit and push it — use the
   `release-tag` skill, which automates this:

   ```sh
   git tag -a v1.0.0 -m "v1.0.0"
   git push origin v1.0.0
   ```

3. Move the accumulated [CHANGELOG.md](CHANGELOG.md) entries from
   `## Unreleased` under a `## 1.0.0` heading in the same change, and bump
   `version` in `pyproject.toml`. That is the only file the version is
   written in: `mailctl.__version__`, and so `mailctl --version`, is read
   from the installed package's metadata (#106). An editable install
   records the version when it is installed, so after a bump run
   `uv pip install -e .` again before `mailctl --version` shows it.

**A tag publishes nothing**, which is the one place this differs usefully from
the sibling provider: there, pushing a tag triggers a Registry release that
**cannot be unpublished**, so tagging early is dangerous. Here a tag is a
marker on history — cheap, and carrying no outward-facing consequence. It is
still immutable once pushed (`git.md`): a mistake gets a **new** tag, never a
moved one.

[#282]: https://github.com/harleypig/mailctl/issues/282
[codes]: docs/ERROR-CODES.md
[api-errors]: docs/API.md#errors
[scripting]: docs/SCRIPTING.md
