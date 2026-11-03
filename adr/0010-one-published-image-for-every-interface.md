# 10. One published image for the CLI, the TUI, and the API

- Status: accepted
- Date: 2026-11-03
- Accepted: 2026-11-03, by the operator, in advance of this record, for
  the recommendation it sets out.
- Builds on [ADR 0008][adr8] and [ADR 0009][adr9]. Recorded for
  [#435][i435].

## Context

**A user should be able to run mailctl from an image without building
anything.** That is the goal of milestone M9, *Published images*. Today
nothing is published: the image is built locally with `make image`
([RELEASING.md](../RELEASING.md), [docs/DOCKER.md](../docs/DOCKER.md)).

**The image that exists already carries all three interfaces.** The
top-level `Dockerfile` installs mailctl and the TUI into one venv
(`/opt/mailctl/venv`), so the TUI finds `mailctl-api` on `PATH`
([ADR 0009][adr9]). Its entrypoint, `docker/entrypoint.sh`, chooses the
command from the first argument: `api` runs `mailctl-api`, `tui` runs
`mailctl-tui`, and anything else is handed to `mailctl` as its arguments
([#347][i347]). Its guards hold whichever runs: the config directory must
be a mount, the user is not root, `MAILCTL_BACKUP_ROOT` is set again
whatever the run passed, and the TUI's `--api` is refused, since the
command it names need not keep them.

**So the question is the shape of what is published**: that one image, or
one image per interface. And with it, which tags, which architectures, and
what triggers a publish.

## Decision

**We publish one image, `ghcr.io/harleypig/mailctl`, built from the
top-level `Dockerfile` as it is today.** It carries the CLI, the API, and
the TUI, chosen at run time by the entrypoint as above.

- **Tags:** `X.Y.Z`, `X.Y`, and `latest`.
- **Trigger:** a `vX.Y.Z` version tag, and nothing else. A push to
  `master` or a pull request publishes nothing.
- **Architectures:** `linux/amd64` and `linux/arm64`.

## Alternatives rejected

### One image per interface

Smaller CLI and API images, since neither would carry Textual or Rich. It
lost on two counts. It is three images to tag, scan, and keep in step,
where one image is one of each. And it does not separate cleanly: the TUI
is a client of the API and starts `mailctl-api` itself
([ADR 0009][adr9]), so a TUI image carries the API, and with it mailctl,
anyway.

### Publishing nothing

What RELEASING.md describes today. It is the opposite of this milestone's
goal: a user who wants mailctl from an image would still have to build it.

## Consequences

- **A CLI-only user pulls Textual and Rich as well.** Accepted: the cost
  is image size, not behaviour, and splitting the image to avoid it is the
  alternative rejected above.
- **One image is scanned.** The *Image scan* workflow's `trivy` run
  already covers it ([#238][i238]); nothing about the scan multiplies.
- **The base image has to serve both architectures.** The `Dockerfile`
  pins `PYTHON_IMAGE` by tag and digest; the publish build needs that
  digest to name an image index that holds both `linux/amd64` and
  `linux/arm64`.
- **A tag publishes something.** RELEASING.md's *a tag publishes nothing*
  stops holding once the publish workflow lands: a `vX.Y.Z` tag then
  pushes an image a user can pull, and a pushed image cannot be taken back
  from everyone who pulled it, so a tag is no longer free to cut early.
  RELEASING.md and docs/DOCKER.md change with that workflow, not with this
  record.

## Follow-up work

Filed in M9, separately from this record:

- the publish workflow: build for both architectures and push the three
  tags on a `vX.Y.Z` tag;
- RELEASING.md: what a tag publishes, and what that does to cutting one;
- docs/DOCKER.md: pulling the published image, beside building one.

## Based on

- [#435][i435] — the issue, and what it asked to weigh.
- [ADR 0009][adr9] — the TUI as a client that starts the API, which is
  why a TUI image carries the API.
- [#347][i347] — the entrypoint's `api` and `tui` dispatch.

[adr8]: 0008-json-rpc-api-interface-for-out-of-process-clients.md
[adr9]: 0009-python-textual-tui-as-a-separate-package.md
[i238]: https://github.com/harleypig/mailctl/issues/238
[i347]: https://github.com/harleypig/mailctl/issues/347
[i435]: https://github.com/harleypig/mailctl/issues/435
