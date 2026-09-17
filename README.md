# FactorioAI

A playground for AI-controlled Factorio companions: build factories together,
learn how game automation and planning work, and compare different models.
The aim is a longer hobby project with small, visible weekly improvements and
individually testable parts.

## One-command local stack

Requires Python 3.9+, Docker Engine / Docker Desktop and Docker Compose v2+.
Run on Linux or macOS with a local Docker daemon, as your normal Docker-enabled
user (no `sudo`). On Apple Silicon, enable Docker's amd64 emulation.

```bash
bin/start
```

This initializes the local instance, installs the companion bridge, starts the
Factorio server, and serves the management portal at
**http://127.0.0.1:8765**. Keep the command running while using the portal;
Ctrl-C stops the portal and managed AI-player processes, while preserving the
current Factorio server state. Use `bin/portal` when the server is already in
the desired state.

The portal manages server configuration and logs, manual backups and restores,
safe world resets, AI-player lifecycle, and persistent run/lifetime statistics.
All state stays in this checkout. The portal is deliberately bound to localhost
and has no remote-server dependency.

To open the portal from another device on the same trusted LAN, run
`bin/start --lan` and use this computer's private IP with port 8765. LAN mode
has no authentication, so do not use it on an untrusted network or expose the
port through the router.

Connect from Factorio **2.0.77**, with Space Age disabled, to
**127.0.0.1:34198**. Read the generated join password with `bin/password`.
The first start downloads the image and generates a peaceful world.

```bash
bin/backup
bin/reset --seed 12345
bin/backups
bin/restore server/backups/NAME.tar.gz
bin/stop
```

See [operations](OPERATIONS.md) for all commands and recovery behavior, and
[server setup](server/README.md) for settings, mods, networking and upgrades.

Recovery tests (no Docker needed): `python3 -m unittest discover -s tests -v`.

## Scripted companion

The one-command stack installs the first controllable companion automatically.
It can be started and stopped in the portal. The lower-level CLI remains useful
for direct movement tests:

```bash
bin/companion spawn --name Ada
bin/companion move 5 0 --relative
bin/companion status
```

The character, its position, and its inventory persist in the save. The Python
controller holds a short game-side lease while moving; Ctrl+C stops immediately,
and an ungraceful disconnect stops within 180 simulation ticks. See the
[companion guide](companion/README.md) for client mod installation, interactive
control, remote access, and the full safety model.

## Project plan and progress

This roadmap now assumes one local machine: start with **one character**, use
**base Factorio**, and run the server, portal, controller, and future local model
together. Multiple cooperating characters come later.

Status reviewed on **2026-09-16**, using this repository and the project
conversation history. Checked items have implementation or reported verification
behind them; unchecked items are pending or have not yet been verified.
Experiment reset and restore milestones were verified locally on **2026-09-17**. Earlier
test results and deployment reports are historical, not a fresh production test.

### Intended setup

| Location | Responsibility |
| --- | --- |
| Current Mac | Dockerized Factorio, management portal, controllers, data, and experiment history |
| Factorio client | Play alongside the companion and observe its actions |
| Future Linux workstation | Run the same checkout and local stack when more compute is useful |

The companion is a named, mod-controlled character in the shared world; it does
not appear as a separately authenticated multiplayer client.

The intended workflow is to run `bin/start`, join the world, start the agent in
the portal, give it a small job, and stop it there. Stopping or losing the
controller connection stops character actions while preserving its position and
inventory. Remote provisioning is outside the active workflow.

### 0. Server playground — mostly complete

- [x] Create the Git repository and Docker/Compose server setup.
- [x] Pin the initial base-game version to Factorio 2.0.77.
- [x] Provide private-server defaults, a generated join password, persistent data,
  autosaves, and a health check.
- [x] Provide Ansible deployment files, a dedicated `factorio` account, and a
  systemd service under `/home/factorio`.
- [x] Deploy to the Ubuntu server and join from a matching game client
  (confirmed in the project conversations).
- [x] Verify image build, startup, saving, and persistence across a container
  restart in the initial smoke test; add the test to GitHub Actions.
- [x] Add a backup script and verify archive contents and restart behavior
  during the initial setup.
- [x] Document backup, restore, restart, world reset, and changing worlds in
  [OPERATIONS.md](OPERATIONS.md).
- [x] Create and verify a fixed-seed, enemies-disabled disposable test world.
- [ ] Verify a repeated Ansible deployment preserves the save and makes no
  unnecessary changes; verify persistence across a host reboot.
- [x] Test restoring a checkpoint end to end in the local experiment server.
- [x] Add `bin/reset` with automatic backups, preserving server configuration
  and credentials; verify reset, save import and restart on the local experiment server.

The later operations discussion prioritized quick reset-and-retry experiments.
Scheduled and off-server backups are deferred; manual backup support already exists.

### 1. One scripted companion — complete locally

- [x] Build a minimal Lua bridge and Python controller using RCON.
- [x] Create one named character with its own inventory and stable identity.
- [x] Connect, move, stop, and reconnect without using a model.
- [x] Preserve the same character, position, and inventory through save/reload.
- [x] Stop walking, mining, and shooting on cancellation or controller loss.

Verified with a disposable real server on **2026-09-17**, including collisions,
Ctrl+C, forced controller termination, and server restart. The remaining manual
check is watching the character from a matching modded game client.

### 2. Navigation and basic game actions

- [x] Walk to a destination or interaction range; handle obstacles, detours,
  unreachable targets, newly blocked routes, and getting stuck.
- [x] Inspect, mine, craft, place, rotate, transfer items, and cancel actions.
- [x] Enforce normal movement, action time, reach, recipes, research,
  placement rules, and personal inventory capacity in the game bridge.
- [x] Give requests character IDs and unique command IDs; expose running,
  completed, failed, and cancelled states and prevent duplicate effects on retries.
- [x] Script a complete furnace sequence using real supplies.

Verified in a disposable Factorio 2.0.77 world on **2026-09-17**: a script mines
ore, crafts and places a furnace, loads fuel and ore, waits for normal smelting,
and retrieves one plate. Cancellation refunds ingredients, while repeated command
IDs do not lose or duplicate items or entities.

### 3. World understanding

- [ ] Describe nearby resources, buildings, recipes, inventories, and tasks in
  compact structured observations.
- [ ] Detect missing fuel, missing ingredients, and blocked outputs in prepared scenes.
- [ ] Implement and document observation boundaries, initially local surroundings
  plus the team's explored map, with close inspection requiring proximity.

**Done when:** small repeatable scenes produce correct, useful diagnoses.

### 4. One model-controlled worker

- [ ] Add an observe → decide → act → verify loop with a replaceable model adapter.
- [ ] Run a first local model through Ollama on the workstation and measure its
  suitability; start by evaluating an approximately 8B model.
- [ ] Let the model choose bounded jobs while Python and Lua execute mechanics.
- [ ] Complete the first configured job: **produce 20 iron plates from available
  supplies**, within a decision and time limit.
- [ ] Show the current task, recent actions, failures, and recovery attempts;
  support safe pause and stop.

**Done when:** the local model finishes the small production task and recovers
from simple missing-supply or blocked-action situations.

### 5. Factory designer

- [ ] Propose a small smelting layout and construction sequence.
- [ ] Validate available materials, placement, reach, and connections before execution.
- [ ] Build the setup and recover from a failed placement or connection.

**Done when:** the finished setup sustains production, rather than merely placing
all the requested entities.

### 6. Repeatable model comparisons

- [ ] Maintain a persistent playground and resettable challenge scenarios.
- [ ] Reload identical starting saves and swap models with the same tools,
  instructions, supplies, and limits.
- [ ] Record model identity, task success, sustained production, game and wall-clock
  time, decisions, cost, failed actions, recovery, waste, and human interventions.
- [ ] Repeat runs and export readable comparisons; record the simulation timing policy.
- [ ] Optionally add inexpensive cloud model adapters with explicit spending limits.

**Done when:** one command replays a challenge across models and produces
comparable results. Comparisons can begin with one character.

### 7. Multiple cooperating characters

- [ ] Add a second character with independent inventory, memory, goals, and model choice.
- [ ] Add a shared task board, supply requests, item handoffs, and work reservations.
- [ ] Isolate agent failures and handle a human changing the world mid-task.
- [ ] Compare identical-model and mixed-model teams with repeated runs and role swaps.

**Done when:** two characters divide a small factory job and complete it without
repeatedly competing for supplies or rebuilding each other's work.

### 8. Open-source release

- [ ] Choose a license and prepare the repository for public release.
- [ ] Document the game API contracts, module boundaries, and contribution workflow.
- [ ] Provide an example scenario and simple setup and manual-start instructions
  for the complete agent system.

Planned modules are the Lua game bridge, Python world understanding and skills,
agent runtime and model adapters, cooperation, and experiment records. These are
code boundaries within one project; they do not require separate services.

### Weekly rhythm

Choose one observable behavior, build a tiny scenario, implement and test it,
finish with a short play session, and record what was learned. The refined initial
sequence is companion lifecycle → walking → mining/crafting → scripted furnace →
local model → status/recovery → tiny factory → model comparison. These are flexible
weekly targets, not completion dates.

## Archived remote deployment reference

The material below is retained only as historical reference. It is not part of
the active workflow and `bin/start` never contacts or deploys to a remote host.
For all current work, use the local portal and `server/` runtime described above.
Older production operations notes remain in [ansible/OPERATIONS.md](ansible/OPERATIONS.md).

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
