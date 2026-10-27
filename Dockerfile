# A build-it-yourself image of mailctl: `make image`, or
#   docker build -t mailctl .
# Nothing is published; the README's *Docker* section says how to run it.
#
# 3.12 because it is the version CI tests. The digest pins the exact base;
# bump tag and digest together, deliberately.
ARG PYTHON_IMAGE=python:3.12.16-slim-trixie@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f

# ############################################################################
# Build: a venv at /opt/mailctl/venv, the only thing copied to runtime.
FROM ${PYTHON_IMAGE} AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /src

COPY pyproject.toml README.md ./
COPY mailctl ./mailctl

# pip compiles what it installs, which matters because the runtime user
# cannot write __pycache__.
RUN python -m venv /opt/mailctl/venv \
    && /opt/mailctl/venv/bin/pip install . \
    && /opt/mailctl/venv/bin/pip uninstall --yes pip

# ############################################################################
# Runtime.
FROM ${PYTHON_IMAGE}

# XDG_CONFIG_HOME puts the config directory, and with it the backups and
# baselines, at /config/mailctl, which the entrypoint requires to be a
# mount. MAILCTL_BACKUP_ROOT makes mailctl refuse a backup anywhere else;
# the entrypoint sets it again, so it holds under `docker run -e` too, and
# this copy holds under `--entrypoint mailctl`.
ENV PATH=/opt/mailctl/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    XDG_CONFIG_HOME=/config \
    MAILCTL_BACKUP_ROOT=/config/mailctl

# /tmp and /var/tmp lose their world-writable bit, so a non-root user can
# write almost nowhere but a mount: the backstop behind MAILCTL_BACKUP_ROOT.
# /dev/shm is a run-time mount and stays writable, which is why the root is
# the guard and this is not. mailctl itself writes no temporary files.
RUN useradd --system --uid 10001 --user-group --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin mailctl \
    && install -d -m 0755 -o root -g root /config /work \
    && chmod 0755 /tmp /var/tmp

COPY --from=build /opt/mailctl/venv /opt/mailctl/venv
COPY docker/entrypoint.sh /usr/local/bin/mailctl-entrypoint

WORKDIR /work

USER mailctl

ENTRYPOINT ["/usr/local/bin/mailctl-entrypoint"]
CMD ["--help"]
