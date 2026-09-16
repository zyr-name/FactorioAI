#!/bin/bash
# Consistent full backup: stop, archive the mounted data, restart if previously running.
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
mkdir -p backups
backup="backups/factorio-$(date -u +%Y%m%dT%H%M%SZ)-$$.tar.gz"
running=$(docker compose ps --status running -q factorio)
restart_needed=false
complete=false
cleanup() {
    result=$?
    trap - EXIT
    if [[ "$complete" != true ]]; then rm -f "$backup"; fi
    if [[ "$restart_needed" == true ]]; then
        docker compose start factorio || result=1
    fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ -n "$running" ]]; then
    restart_needed=true
    docker compose stop factorio
fi
docker compose run --rm --no-deps -T --entrypoint tar factorio \
    -C /factorio -czf - . > "$backup"
complete=true
echo "Backup saved to $backup"
