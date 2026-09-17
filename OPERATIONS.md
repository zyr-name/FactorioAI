# Factorio experiment operations

Run commands from the repository root, or use their absolute paths from
any directory. They target this checkout's `server/compose.yaml` and `.env`.
See [server setup](server/README.md) for configuration.

| Command | Behavior |
| --- | --- |
| `bin/setup` | Initialize settings and random passwords; keep existing files |
| `bin/start` | Start the server and local portal at `http://127.0.0.1:8765` |
| `bin/portal` | Start only the portal, preserving current server state |
| `bin/factorio start` | Start/apply server configuration and wait for RCON health |
| `bin/stop` | Gracefully stop, preserving the current world |
| `bin/restart` | Stop and start, applying config changes without resetting |
| `bin/status` | Show container state and health |
| `bin/logs` | Show the last 100 log lines |
| `bin/logs -f --tail 200` | Follow logs |
| `bin/password` | Print the game password |
| `bin/rcon '/players'` | Send a command through the container's RCON client |
| `bin/install-companion` | Back up, install/enable the companion mod, disable auto-pause |
| `bin/companion status` | Inspect the persistent companion and controller lease |
| `bin/companion stop` | Emergency-stop the companion and revoke its lease |
| `bin/backup` | Back up saves, configuration, mods and script output |
| `bin/backups` | List backup paths and sizes |
| `bin/restore PATH.tar.gz` | Restore an archive, with a backup of current data first |
| `bin/reset` | Back up and remove active saves; keep settings and mods |
| `bin/reset --seed 12345` | Reset with a particular map seed |
| `bin/load-save PATH.zip` | Back up and replace all active saves with this world |
| `bin/validate` | Check Compose and runtime JSON configuration |
| `bin/pull` | Download the configured image without restarting |
| `bin/down` | Stop and remove containers/network; preserve data and backups |
| `bin/factorio --help` | List commands; each command supports `--help` |

## Typical experiment

```bash
bin/start
```

Keep this command running and use the portal for the usual workflow. It starts
the server and portal, then supervises AI-player processes launched from the UI.
Ctrl-C stops the portal and its AI-player processes; the Dockerized Factorio
server keeps its current state. This makes an accidental terminal close safe
and lets `bin/portal` reconnect later. Use `bin/stop` when the game server should
also stop.

The individual commands below remain available for scripts:

```bash
bin/backup
bin/reset --seed 12345
bin/backups
bin/restore server/backups/NAME.tar.gz
```

Backups briefly stop a running server to obtain a consistent snapshot and
restart it afterward. Restore, reset and load-save do the same, first creating
a `before-*` backup. A stopped instance stays stopped. No interactive
confirmation is needed, so these commands can be used by experiment scripts.
Completed backup paths are printed as operations run. Archives are never
automatically pruned.

If a backup fails, the destructive operation does not proceed. Restore
validates and extracts its archive into a temporary directory before touching
the active data. Concurrent mutating commands are rejected with an explicit
message. Use these commands rather than direct Compose operations so the
operation lock and consistent-backup behavior remain effective.

Backups contain passwords and should be treated as private. They include game
image/DLC metadata, but not `.env`, Docker images or this repository's source.
Temporary unpacked world files and the runtime lock are excluded.
Restore checks the configured image/DLC against that metadata. It preserves
local ports and the Compose project name; update `.env` separately for a
version rollback. Different game versions may not load each other's saves.

## Recovery and troubleshooting

```bash
bin/status
bin/logs --tail 200
bin/backups
bin/stop
bin/restore server/backups/NAME.tar.gz
bin/factorio start
```

If a new world, imported save or restored world fails its health check, the
command exits unsuccessfully and keeps the data for inspection. Read the logs,
correct the version/mod/config problem or restore the printed `before-*`
backup. No successful start is reported until RCON responds.

If Docker Desktop cannot find `docker-credential-desktop`, add its tools to
your shell's PATH (on macOS, usually
`/Applications/Docker.app/Contents/Resources/bin`) and retry.

For the separately deployed production server, see the retained
[production operations notes](ansible/OPERATIONS.md).
