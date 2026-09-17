# Experiment server setup

This setup uses the pinned
[factoriotools/factorio image](https://github.com/factoriotools/factorio-docker)
and Docker Compose. No custom image build is needed. Its separate Compose
project, repository-local data and default port allow it to coexist with the
production deployment.

## First run

Install Docker with Compose v2+ and Python 3.9+. Start Docker, then run from
the repository root:

```bash
bin/start
```

This starts the complete local stack and prints the management-portal address.
Use the portal for configuration, logs, recovery operations, and AI players.
For server-only automation, the equivalent low-level sequence is
`bin/setup`, `bin/validate`, then `bin/factorio start`.

The scripts also work from other directories when invoked by their full path.
Use a local Docker daemon: filesystem operations act on this machine's files.
Do not point these commands at a remote Docker context or `DOCKER_HOST`.
The image runs as linux/amd64, including through Docker emulation on Apple
Silicon; initial world generation can take longer under emulation.

Setup is repeatable and leaves existing configuration intact. It creates
`.env` from `.env.example`, records your UID/GID for file ownership, and copies
the JSON defaults into `data/config/`. Game and RCON passwords are randomly
generated once. Run all commands as the same local user with Docker access.
On first start, the image supplies the complete `map-settings.json` defaults.
Temporary save unpacking uses a Linux tmpfs to avoid macOS bind-mount permission
issues. This consumes container memory; actual saves stay on disk.

## Files and configuration

| Path | Purpose |
| --- | --- |
| `compose.yaml` | Image, ports, persistence, health check |
| `.env` | Local instance name, version, bind address, port, DLC, UID/GID |
| `defaults/` | Templates used only when runtime files are missing |
| `data/config/server-settings.json` | Password, visibility, players, autosaves, pause |
| `data/config/map-gen-settings.json` | Seed, peaceful mode and map generation |
| `data/config/map-settings.json` | Pollution, enemy behavior and other world settings |
| `data/config/server-adminlist.json` | Optional JSON array of admin usernames |
| `data/config/rconpw` | RCON password |
| `data/saves/` | Current world and autosaves |
| `data/mods/` | Mods and mod settings |
| `data/script-output/` | Output from game scripts |
| `backups/` | Compressed runtime-data snapshots and compatibility metadata |

Runtime files, passwords and backups are ignored by Git. Edit `data/config/`,
not `defaults/`, to change an initialized instance. Stop the server before
editing runtime files, then run `bin/validate` and `bin/start`.

The server pauses when no players are connected by default. For experiments
that must continue without players, set `auto_pause` to `false` in
`data/config/server-settings.json`. RCON is published only to localhost for the
Python companion controller. Use `bin/rcon '/help'` for container-local
administration. Lua console commands can disable achievements on the
experimental world.

Install the scripted companion with `bin/install-companion`, then follow the
[companion guide](../companion/README.md). The installer backs up first and
disables auto-pause so the lease watchdog keeps running without human players.

## Change worlds

```bash
bin/reset --seed 12345                 # new map with a reproducible seed
bin/reset                            # use current map-generation settings
bin/load-save /absolute/path/world.zip
bin/restore server/backups/NAME.tar.gz
```

`reset` keeps settings and mods, clearing all active saves and autosaves after
a backup. `--seed random` requests a random seed. Map generation changes apply to a
new world; use reset after changing them. `load-save` replaces the active save
set, so a newer autosave cannot override your selected world. Imported saves
must match the configured game version, DLC and installed mods.

These commands preserve whether the server was running. When stopped, the
new world is created/loaded on the next `bin/factorio start`. `restore` replaces all
runtime data, including settings, mods and passwords; it keeps the local
instance's `.env` and networking. Only archives produced by `bin/backup` are
accepted. Use `bin/load-save` for a save ZIP from another server.

## Network and multiple instances

By default only this machine can connect, at `127.0.0.1:34198`. For a dedicated
development host or LAN access, set `FACTORIO_BIND=0.0.0.0` in `.env`, allow the
chosen UDP port in the host firewall, and connect directly to that host's IP.
Public server listing is disabled. Retrieve the game password with
`bin/password`.

For another experiment instance, copy/clone the repository, run `bin/setup`,
and give it a unique `COMPOSE_PROJECT_NAME` and `FACTORIO_PORT` before starting.
Keep the project name stable after creating containers. Each checkout owns its
own `server/data/` and `server/backups/`; do not share or symlink these to the
production data. Shell variables cannot override the instance settings used by
the scripts; edit `.env` instead.

## Upgrade

1. Run `bin/backup` before changing versions or DLC.
2. Run `bin/stop`.
3. Edit `FACTORIO_VERSION` or `DLC_SPACE_AGE` in `.env`.
4. Run `bin/pull`, then `bin/factorio start` (or use Start server in the portal).
5. Use a matching game client and check `bin/logs`.

For rollback, stop the server, set the version and DLC back to those recorded
in the backup, and restore the archive. `bin/restore` checks compatibility
before stopping the server or changing any data. Backups stay on this disk;
copy important snapshots elsewhere for protection against disk loss.

## Production deployment

The existing `ansible/` example is copied manually into the separate deployment
repository. This setup neither runs Ansible nor adopts that deployment's
containers, systemd service or `/home/factorio` data. The previous production
operations notes are preserved in [ansible/OPERATIONS.md](../ansible/OPERATIONS.md).
