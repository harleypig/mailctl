---
paths:
  - "pyproject.toml"
  - "clients/tui/pyproject.toml"
  - "Makefile"
  - "Dockerfile"
  - ".dockerignore"
  - "docker/**"
  - ".hadolint.yaml"
  - ".github/**"
  - ".pre-commit-config*.yaml"
  - ".markdownlint.json"
  - ".yamllint"
---

# mailctl Conventions — toolchain and QA

Path-scoped detail moved out of `.claude/CONVENTIONS.md`, which stays always
loaded as the short core; read the core's *Where the detail lives* for
the whole map. Claude Code loads this file only when a file matching
the `paths:` above is read or edited.

## Toolchain & reproducibility

- **Python ≥ 3.11** (`requires-python` in `pyproject.toml`), tested on 3.11
  through 3.14 ([#231][i231]), and developed inside a **`.venv`** at the
  repo root — never against the system Python.
- **`uv` provisions it** (the isolated-app rung of the global
  `toolchain-provisioning.md` ladder):

  ```sh
  make install    # create .venv if it is missing, then install into it
  ```

  That runs `uv venv` only where no `.venv` exists, since `uv` refuses to
  create one over an existing one, and then `uv pip install -e '.[dev]'`,
  the package plus the dev tools ([#262][i262]). Typed by hand, the bare
  `uv venv` is right on a fresh checkout only.

- **Runtime dependencies are `sievelib` and `IMAPClient`, and that is
  deliberate.** Both are pinned by lower bound in `pyproject.toml`, and
  both now carry an upper bound on the next major, so raising a bound is a
  deliberate, tested change rather than something a fresh install does
  unreviewed. Adding a third runtime dependency to a tool whose whole job is
  two protocol conversations deserves an argument first.
- Dev tooling (`ruff`, `pytest`, `pyright`, and `setuptools`, so
  `tests/test_packaging.py` can build a wheel offline) is an **optional
  dependency group**, so a user installing the CLI never pulls the linter
  in.
- **Every subpackage is found, not listed**
  (`[tool.setuptools.packages.find]`, `include = ["mailctl*"]`), so a
  non-editable install — a wheel, `uv tool install .`, `pipx install .` —
  carries the whole package ([#229][i229]).
- **The TUI is installed for development into the same `.venv`**, beside
  mailctl, and `make install` does not do it:

  ```sh
  make install                            # mailctl and its dev tools
  uv pip install -e './clients/tui[dev]'  # the TUI and its test tools
  ```

  Its dependencies — `textual` and `rich`, and in its dev extra `pytest`
  and `pytest-asyncio` — are in `clients/tui/pyproject.toml`, so mailctl's
  own runtime dependencies stay the two above ([ADR 0009][adr9]). For use
  rather than development, `uv tool install ./clients/tui` gives it an
  environment of its own; it needs `mailctl-api` on `PATH`, not mailctl
  in that environment.
- **A build-it-yourself Docker image** is the top-level `Dockerfile`, built
  with `make image` and published nowhere; [docs/DOCKER.md][docker]'s *In
  Docker* says how to run it. It sets `MAILCTL_BACKUP_ROOT` (*Rule
  conventions*).
  It carries the CLI, the API, and the TUI in one venv, so the TUI finds
  `mailctl-api` on `PATH`: `api` runs the API, `tui` the TUI, and
  anything else is the CLI's arguments ([#347][i347]). Its guards — the
  config directory a mount, the user not root, and a terminal for the
  TUI — hold whichever runs, and the TUI's `--api` is refused, since the
  command it names need not keep them.

## QA

The global `qa.md` owns the pipeline and its ordering; this section is the
concrete toolchain and the **status of every dimension** for this repo.

- **Format + lint:** `ruff` — `ruff format` and `ruff check`, configured under
  `[tool.ruff]` in `pyproject.toml`. Pre-commit gates both, in the two-file
  fix-then-check split (`.pre-commit-config-fix.yaml` runs the auto-fixers
  once as a prep step; `.pre-commit-config.yaml` is the non-modifying gate).
  Do **not** wire black/isort/flake8 alongside ruff (`python.md`).
  `clients/tui/` has no `[tool.ruff]` of its own, deliberately, so the
  root's configuration covers it and the two packages share one bar.
- **Code smell / complexity:** ruff's `B` (bugbear), `C4`, `SIM`, `UP`, and
  `RUF` rule sets, inside the same `ruff check`.
- **Type-check:** `pyright` in `standard` mode over `mailctl` and `tests`,
  configured under `[tool.pyright]` in `pyproject.toml`; `make typecheck`
  runs it. It resolves imports from `.venv`, so it runs where the package
  is installed, and the target runs `.venv/bin/pyright`, never `PATH`'s,
  refusing with the reason where it is missing: without the venv, pyright
  checks against whatever Python is on `PATH` and can pass there
  ([#244][i244]). That is also why it is in CI's Lint job and **not** in
  pre-commit: a pre-commit hook's own environment lacks `sievelib`,
  `IMAPClient`, and `pytest`, and supplying them would mean a second copy
  of the dependency list. Its version is pinned exactly in the dev extra,
  because each pyright release adds checks. **The TUI is type-checked
  separately**: the root run covers `mailctl` and `tests` only, and
  `clients/tui/pyproject.toml` has its own `[tool.pyright]` over the TUI's
  `src` and `tests`, standard mode, resolving from the same root `.venv`.
  `make testtui` and CI's *TUI* job run it from `clients/tui/`.
- **Security:** `gitleaks` + `detect-private-key` in pre-commit (secrets).
  The built image is scanned by `trivy` for HIGH and CRITICAL findings in
  the *Image scan* workflow, **report only**: it never fails and is not a
  required check, because the base image carries HIGH findings with no fix
  released, so a failing scan would be red for good ([#238][i238]).
  Note that the *most* important security property of this repo — the password
  never being emitted — is a code-structure guarantee (`Secret`), not
  something a scanner checks; review it by reading `Secret.reveal()` call
  sites.
- **Prose:** `markdownlint` and `yamllint` in pre-commit.
- **Dockerfile:** `hadolint` in pre-commit, a local hook pinned by tag and
  digest, in the check config only since it has no fixer. `.hadolint.yaml`
  fails on warnings and errors and trusts base images from `docker.io` and
  `ghcr.io` only ([#238][i238]).

Full dimension status:

| Dimension | Status |
|-----------|--------|
| 1. Format | **Active** — `ruff format` |
| 2. Lint | **Active** — `ruff check` |
| 3. Type-check | **Active** — `pyright` (standard mode) over the package and the tests, in CI's Lint job and `make typecheck` ([#10][i10]); over the TUI by its own configuration, in CI's *TUI* job and `make testtui` ([ADR 0009][adr9]) |
| 4. Code smell / complexity | **Active** — ruff `B`/`C4`/`SIM`/`UP`/`RUF` |
| 5. Security | **Active (secrets, Dockerfile, image report-only)** — `gitleaks`, `detect-private-key`; `hadolint` on the Dockerfile; `trivy` on the built image, report only and not a required check, since the base image carries HIGH findings with no fix ([#238][i238]). SAST is **Off**: the attack surface is two outbound TLS client sessions and no untrusted input parsing beyond the user's own Sieve script |
| 6. Tests | **Active** — the offline tier is green (`make test` / `pytest`), and the TUI's suite (`make testtui`) beside it; see [TESTS.md](../TESTS.md) |
| 7. UI/UX & accessibility | **Active (TUI behaviour); accessibility Missing** — the TUI's screen is driven headless with Textual's Pilot (`clients/tui/tests/`): the masked password field, server text shown as plain characters, an alert shown as it arrives, and a command hidden without its capability. Nothing checks accessibility (contrast, colour-only meaning, screen-reader output), and no decision to build that check has been taken. The CLI's terminal output is covered by the *show, then change* convention |
| 8. End-to-end | **Active (manual, gated)** — `tests/live/` skips unless `MAILCTL_LIVE=1`, and writes only with `MAILCTL_LIVE_WRITE=1` as well; run by hand, never in CI. Its write guards were proved on the container tier ([#9][i9]), and every live test that writes has passed against the real account, the guards restoring everything: the smoke test on 2026-10-31 ([#283][i283]), and on 2026-11-01 it again and the tests for `filter apply`, `folder create`, `filter remove`, and `filterset restore` ([#388][i388]) |
| 9. Compatibility | **Active** — CPython 3.11 through 3.14, every one tested in CI, and sievelib at both ends of its bound (1.5.0 and the newest below 2); the TUI on 3.11 and 3.14 only, the oldest and newest; no external contract we publish ([#231][i231], [#232][i232]) |
| 10. Performance & load | **N/A** — interactive, single-mailbox, human-scale. Revisit only if a retroactive pass over a very large folder proves slow |
| 11. Reliability & observability | **N/A** — a one-shot CLI, not a service. Its reliability property is the backup-before-upload convention |
| 12. Build | **Active** — pure Python, no compile step. `tests/test_packaging.py` builds a wheel with the declared backend and runs it with nothing from the checkout on the path ([#229][i229]); the Docker image (`make image`), which installs the TUI beside mailctl ([#347][i347]), is built by `tests/container/test_image.py`, and in CI by the *Image scan* workflow on a PR or push touching its inputs (`Dockerfile`, `.dockerignore`, `docker/**`, `pyproject.toml`, `clients/tui/pyproject.toml`, `clients/tui/src/**`, and the workflow file itself), weekly on a schedule until 60 quiet days turn it off ([ICEBOX.md][icebox-scan]), and on manual dispatch; `test_image.py` itself is not in CI |
| 13. Documentation | **Active** — this file, `README.md`, `docs/`, and `adr/`; markdownlint gates the prose |
| 14. Code review | **Informal** — solo repo; `master` is PR-only, 0 required reviewers |
| 15. CI | **Active** — `.github/workflows/test.yml`, on every PR and on pushes to `master`. `Lint` runs `ruff check`, `ruff format --check`, and `pyright` once, on 3.12. `pytest` runs on 3.11 through 3.14 as `Test (3.x)`, each a second time on `sievelib==1.5.0`. `TUI (3.x)` runs on 3.11 and 3.14, with mailctl installed beside the TUI so its real-API test runs: the TUI's `pyright`, then its `pytest`, both from `clients/tui/` ([ADR 0009][adr9]). One job named `Test` waits on both matrices and passes only if every version of each did, since that is the check the ruleset requires, so a red TUI blocks a merge ([#231][i231], [#232][i232]). The live and container tiers are deliberately excluded — the one needs real credentials, the other a Docker daemon and a Debian mirror. `.github/workflows/image-scan.yml` (*Image scan*, job `Trivy (report only)`) runs when the image's inputs change, weekly on a schedule (Monday 06:17 UTC) so a CVE published against the unchanged base image is still reported, and on manual dispatch. GitHub turns that schedule off after 60 days with no activity in the repository, and nothing here warns when it does ([ICEBOX.md][icebox-scan]). The scan never fails on a finding and is not a required check ([#238][i238], [#264][i264]) |

[i231]: https://github.com/harleypig/mailctl/issues/231
[i262]: https://github.com/harleypig/mailctl/issues/262
[i229]: https://github.com/harleypig/mailctl/issues/229
[adr9]: ../../adr/0009-python-textual-tui-as-a-separate-package.md
[docker]: ../../docs/DOCKER.md
[i347]: https://github.com/harleypig/mailctl/issues/347
[i244]: https://github.com/harleypig/mailctl/issues/244
[i238]: https://github.com/harleypig/mailctl/issues/238
[i10]: https://github.com/harleypig/mailctl/issues/10
[i9]: https://github.com/harleypig/mailctl/issues/9
[i283]: https://github.com/harleypig/mailctl/issues/283
[i388]: https://github.com/harleypig/mailctl/issues/388
[i232]: https://github.com/harleypig/mailctl/issues/232
[icebox-scan]: ../../ICEBOX.md#the-weekly-image-scan-stopping-after-60-quiet-days
[i264]: https://github.com/harleypig/mailctl/issues/264
