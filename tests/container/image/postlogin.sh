#!/bin/sh
# Dovecot's IMAP post-login script. It writes to the client, so a user whose
# name starts with "alert-" is sent an IMAP ALERT as the login completes --
# the one way found to make Dovecot send one (mailctl #205). Every other
# user sees nothing, so no other test meets an alert it did not ask for.
set -eu

case "$USER" in
  alert-*)
    printf '* OK [ALERT] Maintenance tonight at 22:00 UTC\r\n'
    ;;
esac

exec "$@"
