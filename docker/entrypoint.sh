#!/bin/bash
set -euo pipefail

# Seed only on first boot. Existing configuration and saves always win.
config_dir="${CONFIG:-/factorio/config}"
mkdir -p "$config_dir"
if [[ ! -f "$config_dir/server-settings.json" ]]; then
    umask 077
    settings_tmp=$(mktemp "$config_dir/.server-settings.XXXXXX")
    trap 'rm -f "$settings_tmp"' EXIT
    jq -s --arg password "$(pwgen -s 24 1)" \
        '.[0] * .[1] * {game_password: $password}' \
        /opt/factorio/data/server-settings.example.json \
        /defaults/server-settings.json > "$settings_tmp"
    mv "$settings_tmp" "$config_dir/server-settings.json"
    trap - EXIT
    echo 'Created private server settings. Read game_password from /factorio/config/server-settings.json.'
fi

exec /docker-entrypoint.sh "$@"
