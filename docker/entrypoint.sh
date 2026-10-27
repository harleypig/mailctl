#!/bin/sh
# The image's entrypoint: refuse a run whose backups would land in the
# container's ephemeral layer, then hand over to mailctl.
#
# Every upload writes a backup first, so a config directory that is not a
# mount discards the only copy of the previous filter set on every run.
# Refusing here, exit 2, is the image's guard. The filesystem is the
# backstop for what this cannot see: the image leaves nothing writable to a
# non-root user except mounts (see the Dockerfile).

set -eu

config_dir="${XDG_CONFIG_HOME:-/config}/mailctl"

# ----------------------------------------------------------------------------
refuse() {
  printf 'mailctl image: %s\n' "$*" >&2
  exit 2
}

# ----------------------------------------------------------------------------
# A backup directory must be the mounted config directory or inside it. The
# path is canonicalized first, so `..` cannot climb out of the mount.
check_backup_dir() {
  where=$(realpath -m -- "$1")

  case "$where/" in
    "$config_dir"/*) ;;

    *)
      refuse "backups would be written to $where, outside the mounted" \
        "config directory $config_dir; leave the backup directory unset," \
        "or point it inside that mount."
      ;;
  esac
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

  [ -n "${MAILCTL_BACKUP_DIR:-}" ] && check_backup_dir "$MAILCTL_BACKUP_DIR"

  # argparse takes any unambiguous prefix of a long option, so --ba,
  # --backup and --backup-d all mean --backup-dir.
  flag=--backup-dir
  expect_value=
  for arg in "$@"; do
    if [ -n "$expect_value" ]; then
      check_backup_dir "$arg"
      expect_value=
      continue
    fi

    case "$arg" in
      --ba*=*)
        name=${arg%%=*}
        case $flag in "$name"*) check_backup_dir "${arg#*=}" ;; esac
        ;;

      --ba*)
        case $flag in "$arg"*) expect_value=1 ;; esac
        ;;
    esac
  done
fi

exec mailctl "$@"
