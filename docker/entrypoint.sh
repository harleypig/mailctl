#!/bin/sh
# The image's entrypoint: refuse a run whose backups would land in the
# container's ephemeral layer, then hand over to the command asked for --
# `api` for mailctl-api, `tui` for mailctl-tui, anything else to mailctl.
#
# Every upload writes a backup first, so a config directory that is not a
# mount discards the only copy of the previous filter set on every run.
# Refusing here, exit 2, is the image's guard. Where a backup may go once
# the mount is there is mailctl's own check: MAILCTL_BACKUP_ROOT, set below
# whatever the run passed, refuses a backup directory from any source --
# flag, env file, environment or config.toml -- that resolves outside the
# mount. The filesystem is the backstop: the image leaves nothing writable
# to a non-root user except mounts and /dev/shm (see the Dockerfile).
#
# The guards run the same whichever command is chosen: the TUI reaches the
# server only through the mailctl-api it starts, which inherits this
# environment. Naming another command for it (the TUI's --api) is refused,
# since that command need not keep the guards.

set -eu

config_dir="${XDG_CONFIG_HOME:-/config}/mailctl"

# ----------------------------------------------------------------------------
refuse() {
  printf 'mailctl image: %s\n' "$*" >&2
  exit 2
}

# ----------------------------------------------------------------------------
# Help and the version write nothing, so they run without any mount. Only
# what makes $command exit before it reads a setting counts. After `--` a
# help word is an argument, not an option. No arguments and `help` are the
# CLI's help but start the API or the TUI; and the TUI hands every argument
# it does not know to the API it starts, so for those two only a help word
# given alone counts -- and --version not for the TUI, which hands it on
# and keeps running.
needs_guard() {
  if [ "$command" != mailctl ]; then
    [ "$#" -eq 1 ] || return 0

    case "$1" in
      --help | -h) return 1 ;;
      --version) [ "$command" = mailctl-tui ] || return 1 ;;
    esac

    return 0
  fi

  [ "$#" -eq 0 ] && return 1
  [ "$1" = help ] && return 1

  for arg in "$@"; do
    case "$arg" in
      --) return 0 ;;
      --help | -h | --version) return 1 ;;
    esac
  done

  return 0
}

# ----------------------------------------------------------------------------
# The TUI's --api, in every spelling its parser takes: a prefix of the name
# is accepted, and so is the value after `=`.
refuse_tui_api() {
  for arg in "$@"; do
    case "$arg" in
      --) return 0 ;;
      --a | --ap | --api | --a=* | --ap=* | --api=*)
        refuse "the image runs its own API for the TUI; --api is not taken."
        ;;
    esac
  done
}

# ############################################################################

# Neither word is a mailctl command, so nothing the CLI takes is shadowed.
case "${1-}" in
  api)
    shift
    command=mailctl-api
    ;;

  tui)
    shift
    command=mailctl-tui
    ;;

  *) command=mailctl ;;
esac

[ "$command" != mailctl-tui ] || refuse_tui_api "$@"

if needs_guard "$@"; then
  [ "$(id -u)" -ne 0 ] \
    || refuse "running as root defeats the guard on where backups go;" \
      "run with --user \"\$(id -u):\$(id -g)\"."

  mountpoint -q "$config_dir" \
    || refuse "$config_dir is not a mount, so every backup would be lost" \
      "with the container; mount your config directory there, e.g." \
      "-v \"\$HOME/.config/mailctl:$config_dir\"."

  # Without a terminal the TUI cannot draw, and would sit on a pipe.
  if [ "$command" = mailctl-tui ] && ! { [ -t 0 ] && [ -t 1 ]; }; then
    refuse "the TUI needs a terminal; run it with docker run -it."
  fi
fi

# Exported here rather than only in the Dockerfile, so `docker run -e` cannot
# lift it.
export MAILCTL_BACKUP_ROOT="$config_dir"

exec "$command" "$@"
