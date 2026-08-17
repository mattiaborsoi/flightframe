#!/bin/sh
# Nightly on-droplet backup of everything stateful: the registry (tenants,
# users, hashed tokens), per-tenant state, and position history.
#
#   /root/backups/flightframe/data-YYYYMMDD-HHMM.tar.gz
#
# Retention is hard-capped: anything older than KEEP_DAYS goes, and only the
# newest KEEP_MAX archives survive even inside that window, so the drive can
# never fill however small the box. Installed by deploy as /etc/cron.d/
# flightframe-backup (see DEPLOY.md "Backups").
#
# This protects against bad deploys and fat fingers, not against losing the
# droplet itself — it lives on the same disk by design (the operator chose
# local-only). DigitalOcean Snapshots are the option if that changes.
set -eu

SRC="${1:-/root/flightframe/data}"
DEST="${2:-/root/backups/flightframe}"
KEEP_DAYS=14
KEEP_MAX=20

mkdir -p "$DEST"
tar -czf "$DEST/data-$(date +%Y%m%d-%H%M).tar.gz" -C "$(dirname "$SRC")" \
    "$(basename "$SRC")"

find "$DEST" -name 'data-*.tar.gz' -mtime "+$KEEP_DAYS" -delete
ls -1t "$DEST"/data-*.tar.gz 2>/dev/null | tail -n "+$((KEEP_MAX + 1))" \
    | xargs -r rm -f
