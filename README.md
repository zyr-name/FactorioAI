# Factorio server

A Docker image and Compose deployment for a private Factorio server on Linux.
The default is vanilla Factorio **2.0.77**, with a generated game password,
five-minute autosaves, ten autosave slots, and automatic pause when empty.
Saves, mods, and configuration live in `./data` and survive container recreation.

## Start on your Linux server

Requirements: an **x86-64 Linux server**, Git, and
[Docker Engine with the Compose plugin](https://docs.docker.com/engine/install/).
Run commands from the repository directory with an account allowed to use Docker.
ARM servers are not the deployment target; the image explicitly uses `linux/amd64`.

1. Clone this repository after pushing it to your Git host, or copy this directory
   to your server (for example, `rsync -av --exclude=.git --exclude=data --exclude=backups
   --exclude=.env ./ user@server:~/factorio-server/` from your local machine).
2. On the server:

   ```bash
   cd ~/factorio-server
   cp .env.example .env
   # Optional: edit .env, including Space Age before the first start.
   docker compose up -d --build
   docker compose ps
   docker compose logs -f --tail=100
   ```

   First startup downloads the base image and creates a world. Wait for the
   server to become `healthy`. Ctrl-C stops following logs; the server keeps running.

3. Read your generated join password:

   ```bash
   docker compose exec factorio jq -r .game_password /factorio/config/server-settings.json
   ```

4. Allow **UDP 34197** in the Linux/provider firewall. If behind a router, forward
   UDP 34197 to this server. If you changed `FACTORIO_PORT`, use that port instead.
5. In Factorio, choose **Multiplayer → Connect to address**, enter
   `YOUR_SERVER_IP:34197`, and supply the password. Clients need the same game
   version and mods. Public listing and LAN discovery are disabled by default;
   direct-IP connections work on both LAN and internet.

Docker starts the container again after a server reboot when the Docker service
is enabled. The health check reports responsiveness; Docker's restart policy
restarts an exited process, not a process that is merely unhealthy.

## Configuration

| File | Purpose |
| --- | --- |
| `.env` | Image version, UDP port, data location, Space Age |
| `data/config/server-settings.json` | Name, password, player limit, autosaves, visibility |
| `data/config/server-adminlist.json` | Optional JSON array of Factorio usernames, e.g. `["your-name"]` |
| `data/config/server-whitelist.json` | Optional JSON array restricting who can join |
| `data/config/map-gen-settings.json`, `map-settings.json` | Settings used when creating a new world |
| `data/saves/` | World ZIP files; startup loads the most recently modified save |
| `data/mods/` | Mod ZIP files and `mod-list.json` |

Runtime data is owned by the container's UID/GID **845:845**. On Linux, use
`sudoedit data/config/server-settings.json` to edit settings. Stop the server
before editing configuration, then start it again:

```bash
docker compose stop
sudoedit data/config/server-settings.json
docker compose start
```

`config/server-settings.example.json` supplies first-boot defaults for the image.
Changing it and rebuilding affects new data directories only. Existing runtime
configuration is preserved. `.env`, runtime data, and backups are excluded from Git
and from the Docker build context. Custom absolute data paths must also be kept
outside version control.

To list the server publicly, set `visibility.public` to `true` and fill in
`username` and `token` in the runtime settings using your
[Factorio profile](https://factorio.com/profile). Keep the join password if you want
to restrict access. Private direct-IP hosting needs no listing token.

### Space Age and mods

Set `DLC_SPACE_AGE=true` in `.env` **before the first world is generated**, then
run `docker compose up -d --build`. Joining players need Space Age. Changing this
flag later changes the enabled built-in mods and can make existing saves incompatible.
For community mods, stop the server, copy matching mod ZIPs and your `mod-list.json`
into `data/mods/`, and start it again. Automatic mod updates are disabled.

### Import an existing save

Start the server once to initialize directories, then:

```bash
docker compose stop
sudo cp /path/to/your-world.zip data/saves/imported.zip
sudo touch data/saves/imported.zip
docker compose start
```

The imported file must be the newest ZIP in `data/saves`. Match the game version,
Space Age setting, and mods to the save. For a fresh world with custom map settings,
stop the server, back up and move the existing save ZIPs outside `data/saves`, edit
the generated map settings, then start it again.

## Operations

```bash
docker compose ps                         # Status and health
docker compose logs -f --tail=100          # Logs
docker compose exec factorio rcon /players
docker compose exec factorio rcon /server-save
docker compose restart                   # Restart after editing runtime JSON
docker compose down                      # Remove container; keep ./data
```

RCON is available through `docker compose exec`; its TCP port is not published.
The upstream startup script can include its generated RCON credential in logs,
so treat those logs as private.

### Back up and restore

```bash
./scripts/backup.sh
```

The script briefly stops a running server, archives the entire mounted data
directory into `backups/`, and restarts it. It also works with an absolute
`FACTORIO_DATA_DIR`. Backups include credentials; keep them private and copy them
off the server. To restore with the default `./data` location:

```bash
docker compose down
sudo mv data "data-before-restore-$(date +%s)"
sudo mkdir data
sudo tar -xzf backups/YOUR_BACKUP.tar.gz -C data
docker compose up -d
```

Use your configured data directory instead if you changed `FACTORIO_DATA_DIR`.
Restore using the Factorio version and mods that created the backup.

### Update deliberately

1. Run `./scripts/backup.sh`.
2. Change `FACTORIO_VERSION` in `.env` to the desired upstream image tag.
3. Run `docker compose build --pull && docker compose up -d`.
4. Check health/logs and update clients to match.

Loading a save in a newer version may migrate it. To roll back, restore the
pre-update backup and previous image version. After changing any `.env` values,
use `docker compose up -d --build` so Compose recreates the container.

## Development and verification

```bash
docker compose config --quiet
./tests/smoke.sh
```

The smoke test builds the image, starts a real server without published ports,
checks private defaults and RCON health, saves a world, and verifies persistence
across restart. It removes its own container and volume afterward. GitHub Actions
runs these checks on pushes and pull requests.

The Dockerfile extends [FactorioTools](https://github.com/factoriotools/factorio-docker)
and retains its map creation, privilege dropping, and save loading logic. The
default version was checked against the
[official release API](https://factorio.com/api/latest-releases). Factorio itself
is proprietary software; players need their own copy of the game.
