#!/bin/bash
# Exercise a real server without publishing ports or touching deployment data.
set -euo pipefail
cd "$(dirname "$0")/.."
image="${TEST_IMAGE:-local/factorio-server:test}"
name="factorio-smoke-$$"
volume="$name-data"
cleanup() {
    result=$?
    trap - EXIT
    if [[ "$result" != 0 ]]; then docker logs --tail 80 "$name" >&2 || true; fi
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker volume rm "$volume" >/dev/null 2>&1 || true
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
docker build --platform linux/amd64 -t "$image" .
docker volume create "$volume" >/dev/null
docker run -d --platform linux/amd64 --name "$name" \
    --mount "type=volume,source=$volume,target=/factorio" \
    --stop-timeout 120 --health-interval 5s "$image" >/dev/null
wait_healthy() {
    for ((attempt=0; attempt<120; attempt++)); do
        status=$(docker inspect -f '{{.State.Health.Status}}' "$name")
        if [[ "$status" == healthy ]]; then return 0; fi
        if [[ $(docker inspect -f '{{.State.Running}}' "$name") != true ]]; then break; fi
        sleep 2
    done
    echo 'Server did not become healthy.' >&2
    return 1
}
wait_healthy
docker exec "$name" jq -e \
    '.visibility.public == false and (.game_password | length >= 24)' \
    /factorio/config/server-settings.json >/dev/null
before=$(docker exec "$name" sha256sum /factorio/config/server-settings.json)
docker exec "$name" rcon /server-save smoke-persistence >/dev/null
# Saving is asynchronous: wait for the final ZIP, not just an RCON acknowledgement.
for ((attempt=0; attempt<60; attempt++)); do
    if docker exec "$name" test -s /factorio/saves/smoke-persistence.zip; then break; fi
    sleep 1
done
docker exec "$name" test -s /factorio/saves/smoke-persistence.zip
docker restart "$name" >/dev/null
wait_healthy
after=$(docker exec "$name" sha256sum /factorio/config/server-settings.json)
[[ "$before" == "$after" ]]
docker exec "$name" test -s /factorio/saves/smoke-persistence.zip
echo 'PASS: server is healthy, saves persist, and settings survive restart.'
