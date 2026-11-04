---
paths:
  - "tests/e2e/**"
  - "tests/dovecot_support.py"
  - ".github/workflows/e2e.yml"
---

# mailctl Tests — the e2e tier

Path-scoped detail moved out of `.claude/TESTS.md`, which stays always
loaded as the short core; read the core's *Where the detail lives* for
the whole map. Claude Code loads this file only when a file matching
the `paths:` above is read or edited.

## The e2e tier

The use-case end-to-end tests of [#418][i418], built with [#421][i421]
(install from a clone) as the first use case ([#483][i483]). The
container tier runs `cli.main` in the test process, so it never installs
mailctl; this tier installs it from the checkout by each route — **pipx**,
**uv**, and **Docker** — and runs the **installed** commands as
subprocesses. Each test runs once per route and walks a use case from
start to finish, stopping only at a failed step; the other routes and use
cases still run. Stepping through one step at a time stays a manual job.

- **The gate.** `MAILCTL_E2E` must equal exactly `1` and Docker must
  answer, as the container tier's gate works; anything else is a skip, so
  a plain `pytest` and `make test` run none of it. `make teste2e
  ROUTE=pipx|uv|docker` runs one route, an empty `ROUTE` runs all three,
  and a route name it does not know is a usage error rather than an empty
  run. `TESTARGS` passes through as for the other tiers. The target sets
  `tmp_path_retention_policy=failed`, since a route's sandbox holds whole
  virtualenvs: a passing run leaves only pytest's own empty
  `pytest-of-<user>` directory under `$TMPDIR`. On a host whose `/tmp` is
  small, point `TMPDIR` somewhere larger.
- **Where it lives.** `tests/e2e/conftest.py` holds the framework;
  `tests/dovecot_support.py` holds the test server both tiers start, so
  neither copies it.
- **The install, once per route** (`installed`, session-scoped). The
  checkout's tracked files are copied into a sandbox and installed from
  there, so a build writes nothing into the checkout. pipx installs under
  `PIPX_HOME`, `PIPX_BIN_DIR`, and `PIPX_MAN_DIR`, uv under `UV_TOOL_DIR`,
  `UV_TOOL_BIN_DIR`, and `UV_PYTHON_INSTALL_DIR`, all in the sandbox, so
  nothing under `~/.local` is touched; the TUI is installed beside mailctl
  from `clients/tui`. Docker runs `make image` under a tag of the run's
  own, `mailctl-e2e:<random>`, never `mailctl:dev`, which may be the
  operator's. Teardown uninstalls with the route's own command
  ([#482][i482]): `pipx uninstall` and `uv tool uninstall` for the TUI's
  package (its name read from `clients/tui/pyproject.toml`) and for
  `mailctl`, and `docker image rm` for the tag. It fails if a mailctl
  command is still on the run's `PATH` or the image still exists.
- **Nothing else answers for the install.** `installed.run(command,
  *args, config=...)` runs one of `mailctl`, `mailctl-api`, and
  `mailctl-tui` with an environment built from nothing and a `PATH` of the
  sandbox's bin directory plus every entry holding no mailctl command, so
  a mailctl in `~/.local/bin` cannot pass for a route whose install
  failed. On Docker it is `docker run` with `--security-opt
  no-new-privileges` and the host's uid, the first word picking the
  command, and with a config the mounts `docs/DOCKER.md` gives, the test
  server's certificate, and the host's network, which is where the server
  is published. A command with no config runs on no network. It returns
  the exit status, stdout, and stderr, and runs in a new session with
  empty stdin, so a password prompt ends instead of waiting on a terminal.
- **The test server and a config.** `dovecot` is the container tier's
  Dovecot and Pigeonhole, started only when a test asks for it.
  `mail_config(variant)` writes a config in a temporary `XDG_CONFIG_HOME`
  for a new, empty mailbox on it: `good` (a `config.toml` and a `0600`
  password file), `no_config`, `no_password`, and `password_readable` (the
  password file at `0644`). `test_config_writer.py` proves each on every
  route through `folder list --json`: `good` lists `INBOX`, and the
  others are refused as `missing_settings`, `no_password`, and
  `password_file_mode`, with the password on neither stream.
- **The use cases.** `test_install.py` is [#421][i421]: the commands are
  on the sandbox's `PATH` and are the sandbox's own (on Docker, the image
  exists), `mailctl --version` names the version in `pyproject.toml`, and
  `mailctl-tui --help` prints a usage message. Each route was seen red
  under a dependency that cannot be resolved, which fails its install;
  pipx was seen red without the `mailctl-api` script and with its
  `mailctl` uninstall left out, which teardown names.

### Adding a use case

A use case is a new `tests/e2e/test_<name>.py` and nothing else. Mark it
`pytestmark = pytest.mark.e2e`, take `installed`, and it runs once per
route. Run commands only through `installed.run`, never by path or
through `cli.main`, so each route is tested as installed. Take
`mail_config` where it needs the server, and a broken variant where it
tests a refusal. Read the server back with an oracle that is not mailctl,
as the container tier does. Name in the docstring the break that turns it
red, and see it red once on each route. The password is held to the
container tier's bar: never printed, formatted into an assertion, or put
on a command line. A new kind of variant, or a new route, is framework
code and goes in the conftest. The TUI's use cases need a driver of
their own, built with [#423][i423], and no use case here uses a real
account.

### In CI, and not a required check

`.github/workflows/e2e.yml` (*E2E tests*) runs `make teste2e` over a
matrix of the three routes, each its own job, weekly and on manual
dispatch, and on a pull request only when it touches `tests/e2e/**`,
`tests/dovecot_support.py`, or the workflow. uv comes from
`astral-sh/setup-uv`; the runner has pipx and Docker. A scheduled run that
fails opens an issue titled *E2E weekly run failed*, or comments on the
one already open. It is not required, for the container tier's reason:
it downloads Debian packages, a base image, and Python packages.

[i418]: https://github.com/harleypig/mailctl/issues/418
[i421]: https://github.com/harleypig/mailctl/issues/421
[i423]: https://github.com/harleypig/mailctl/issues/423
[i482]: https://github.com/harleypig/mailctl/issues/482
[i483]: https://github.com/harleypig/mailctl/issues/483
