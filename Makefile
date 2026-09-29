default: fmt lint typecheck test

# There is no `build` target on purpose: mailctl is pure Python with only
# setuptools metadata, so there is nothing to compile or bundle (the Build QA
# dimension is N/A — see .claude/CONVENTIONS.md).

venv:
	uv venv

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
typecheck:
	pyright

test:
	pytest

# Live tests hit a REAL MXroute account and mutate real state. They need the
# MAILCTL_* credentials in the environment. TESTARGS passes extra flags
# through to pytest, e.g. a run filter for a scoped pass:
#   make testlive TESTARGS='-k sieve'
testlive:
	MAILCTL_LIVE=1 pytest -v $(TESTARGS)

# The container tier: every write path against a throwaway local Dovecot +
# Pigeonhole in Docker (tests/container/). Never a real account, so it is
# safe to run any time Docker is up. TESTARGS passes flags through, e.g.
#   make testcontainer TESTARGS='-k restore'
testcontainer:
	MAILCTL_CONTAINER=1 pytest -v tests/container $(TESTARGS)

# Read-only checks against the configured account, as TAP: only read-only
# subcommands and --dry-run are sent. TESTARGS names the checks to run
# (default all; `scripts/live-readonly.sh --list` names them), e.g.
#   make livecheck TESTARGS='list folders'
livecheck:
	scripts/live-readonly.sh $(TESTARGS)

.PHONY: default venv install fmt lint typecheck test testlive testcontainer livecheck
