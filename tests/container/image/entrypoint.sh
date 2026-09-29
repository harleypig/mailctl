#!/bin/sh
# Start Dovecot once the test fixture has handed over the password.
#
# A fresh self-signed certificate is generated on every start, so no
# private key is ever baked into the image or written on the host; the
# fixture copies the certificate (never the key) out and trusts it through
# SSL_CERT_FILE. The SAN covers the name and address it connects to.
#
# The password arrives as /etc/dovecot/secret.conf, written by the fixture
# through `docker exec` stdin, so it is never in the image, an environment
# variable, or an argument list. Dovecot is not started until it exists.
set -eu

install -d -m 0755 /etc/dovecot/tls

openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
  -subj '/CN=localhost' \
  -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' \
  -keyout /etc/dovecot/tls/tls.key -out /etc/dovecot/tls/tls.crt \
  2>/dev/null

chmod 0600 /etc/dovecot/tls/tls.key

# Thirty seconds, in tenths: the fixture writes the file as soon as the
# container is running, so this only waits out a slow `docker exec`.
tries=300

while [ ! -s /etc/dovecot/secret.conf ]; do
  tries=$((tries - 1))

  if [ "$tries" -le 0 ]; then
    echo "entrypoint: no /etc/dovecot/secret.conf after 30s" >&2
    exit 1
  fi

  sleep 0.1
done

exec dovecot -F
