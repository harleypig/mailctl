default: fmt lint typecheck test

# There is no `build` target on purpose: mailctl is pure Python with only
# setuptools metadata, so there is nothing to compile or bundle (the Build QA
# dimension is N/A — see .claude/CONVENTIONS.md).

# uv refuses `uv venv` over an existing .venv (exit 2), and a .venv without
# the dev extra is the checkout `make typecheck` sends people here from, so
# an existing one is kept and installed into (harleypig/mailctl#262).
venv:
	@test -d .venv || uv venv

install: venv
	uv pip install -e '.[dev]'

# Format and lint go through pre-commit rather than calling ruff directly, so
# the configs stay the single source of truth for tool version and flags
# (the global rules/pre-commit.md). `fmt` is the modifying prep step; run it
# once, then `lint`.
fmt:
	pre-commit run --all-files --config .pre-commit-config-fix.yaml

# NOTE: this runs the full check config, which includes no-commit-to-branch —
# so it fails on `master` by design. Branch first; that is the convention, not
# a broken target.
lint:
	pre-commit run --all-files

# pyright over the package and the tests, in the mode and scope set by
# [tool.pyright] in pyproject.toml; CI's Lint job runs the same.
#
# [tool.pyright] resolves imports from .venv. Without one, pyright says
# "venv .venv subdirectory not found", falls back to whatever Python is on
# PATH, and can report 0 errors against packages that are not mailctl's
# (#244 - typecheck passes without .venv). So the target refuses unless
# .venv holds the pinned pyright, and runs that one. Once .venv is found, a
# dependency missing from it is pyright's own reportMissingImports error.
typecheck:
	@test -x .venv/bin/pyright || { \
	  echo "make typecheck: .venv/bin/pyright not found. [tool.pyright] resolves imports from .venv at the repo root; without it pyright checks against whatever Python is on PATH. Run 'make install', or 'uv pip install -e .[dev]' where .venv already exists." >&2; \
	  exit 1; \
	}
	.venv/bin/pyright

# The test targets run .venv's pytest, never PATH's: another environment's
# pytest runs against packages that are not mailctl's (harleypig/mailctl#262,
# the shape #244 fixed for typecheck above). Each refuses, naming the fix,
# where .venv lacks it.
PYTEST := .venv/bin/pytest

pytest-venv:
	@test -x $(PYTEST) || { \
	  echo "make: $(PYTEST) not found. The test targets run the pytest in .venv at the repo root, not whichever is first on PATH. Run 'make install', or 'uv pip install -e .[dev]' where .venv already exists." >&2; \
	  exit 1; \
	}

test: pytest-venv
	$(PYTEST)

# Live tests hit a REAL MXroute account and mutate real state. They need the
# MAILCTL_* credentials in the environment. TESTARGS passes extra flags
# through to pytest, e.g. a run filter for a scoped pass:
#   make testlive TESTARGS='-k sieve'
testlive: pytest-venv
	MAILCTL_LIVE=1 $(PYTEST) -v $(TESTARGS)

# The container tier: every write path against a throwaway local Dovecot +
# Pigeonhole in Docker (tests/container/). Never a real account, so it is
# safe to run any time Docker is up. TESTARGS passes flags through, e.g.
#   make testcontainer TESTARGS='-k restore'
testcontainer: pytest-venv
	MAILCTL_CONTAINER=1 $(PYTEST) -v tests/container $(TESTARGS)

# Read-only checks against the configured account, as TAP: only read-only
# subcommands and --dry-run are sent. TESTARGS names the checks to run
# (default all; `scripts/live-readonly.sh --list` names them), e.g.
#   make livecheck TESTARGS='list folders'
livecheck:
	scripts/live-readonly.sh $(TESTARGS)

# The runnable image (Dockerfile; README.md *In Docker*). Built locally and
# never pushed. IMAGE names the tag, e.g. `make image IMAGE=mailctl:local`.
IMAGE ?= mailctl:dev

image:
	docker build -t $(IMAGE) .

.PHONY: default venv install fmt lint typecheck pytest-venv test testlive testcontainer livecheck image
