#!/bin/sh
# The image's entrypoint: refuse a run whose backups would land in the
# container's ephemeral layer, then hand over to mailctl.
#
# Every upload writes a backup first, so a config directory that is not a
# mount discards the only copy of the previous filter set on every run.
# Refusing here, exit 2, is the image's guard. Where a backup may go once
# the mount is there is mailctl's own check: MAILCTL_BACKUP_ROOT, set below
# whatever the run passed, refuses a backup directory from any source --
# flag, env file, environment or config.toml -- that resolves outside the
# mount. The filesystem is the backstop: the image leaves nothing writable
# to a non-root user except mounts and /dev/shm (see the Dockerfile).

set -eu

config_dir="${XDG_CONFIG_HOME:-/config}/mailctl"

# ----------------------------------------------------------------------------
refuse() {
  printf 'mailctl image: %s\n' "$*" >&2
  exit 2
}

# ----------------------------------------------------------------------------
# Help and the version write nothing, so they run without any mount.
needs_guard() {
  [ "$#" -eq 0 ] && return 1
  [ "$1" = help ] && return 1

  for arg in "$@"; do
    case "$arg" in
      --help | -h | --version) return 1 ;;
    esac
  done

  return 0
}

# ############################################################################

if needs_guard "$@"; then
  [ "$(id -u)" -ne 0 ] \
    || refuse "running as root defeats the guard on where backups go;" \
      "run with --user \"\$(id -u):\$(id -g)\"."

  mountpoint -q "$config_dir" \
    || refuse "$config_dir is not a mount, so every backup would be lost" \
      "with the container; mount your config directory there, e.g." \
      "-v \"\$HOME/.config/mailctl:$config_dir\"."
fi

# Exported here rather than only in the Dockerfile, so `docker run -e` cannot
# lift it.
export MAILCTL_BACKUP_ROOT="$config_dir"

exec mailctl "$@"
