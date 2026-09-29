# A throwaway Dovecot + Pigeonhole server for mailctl's container test
# tier (tests/container/). Built from Debian's own packages rather than the
# dovecot/docker image, whose packaging is CC BY-NC-SA 4.0 (#49).
#
# Debian trixie is the release that ships Dovecot 2.4 with Pigeonhole
# (2.4.1 at the time of writing); MXroute is migrating its fleet from 2.3 to
# 2.4, so 2.4 is the side worth testing against.
FROM debian:trixie-slim

# hadolint ignore=DL3008
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        dovecot-core dovecot-imapd dovecot-sieve dovecot-managesieved openssl \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /etc/dovecot/conf.d /etc/dovecot/private \
    && groupadd --gid 5000 vmail \
    && useradd --uid 5000 --gid vmail --home-dir /srv/vmail --no-create-home \
        --shell /usr/sbin/nologin vmail \
    && install -d -o vmail -g vmail -m 0700 /srv/vmail

COPY dovecot.conf /etc/dovecot/dovecot.conf
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY postlogin.sh /usr/local/bin/postlogin.sh

EXPOSE 143 993 4190

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
