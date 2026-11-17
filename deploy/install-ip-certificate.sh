#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ $EUID -eq 0 ]] || exit 1
[[ "$RENEWED_LINEAGE" == /etc/letsencrypt-nwpu/live/nwpu-forwarder-ip ]] || exit 0
target=/etc/caddy/nwpu-forwarder-ip
install -d -m 750 -o root -g caddy "$target"
openssl x509 -in "$RENEWED_LINEAGE/fullchain.pem" -checkend 3600 -noout >/dev/null
install -m 644 -o root -g caddy "$RENEWED_LINEAGE/fullchain.pem" "$target/fullchain.pem.new"
install -m 640 -o root -g caddy "$RENEWED_LINEAGE/privkey.pem" "$target/privkey.pem.new"
mv -f "$target/fullchain.pem.new" "$target/fullchain.pem"
mv -f "$target/privkey.pem.new" "$target/privkey.pem"
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1
systemctl reload caddy
