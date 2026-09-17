# Scripted companion

The companion is a real, persistent `character` entity owned by the player
force. A Factorio mod stores its identity and inventory in the save; a Python
controller sends versioned JSON commands over RCON.

## Install and run

Initialize the experiment server first, then install the mod:

```bash
bin/setup
bin/install-companion
bin/start
bin/companion spawn --name Ada
bin/companion status
bin/companion move 5 0 --relative
```

`install-companion` creates a complete backup, installs/enables the mod, and
sets `auto_pause=false` so movement and the disconnect watchdog continue when
no human is connected. It preserves an existing character when updating the
same mod. The RCON port is published only on `127.0.0.1:27016` by default.

Every joining game client needs the same mod ZIP. Build it with:

```bash
bin/companion package
```

Copy `dist/factorio-ai-companion_0.1.0.zip` into the Factorio client's `mods`
directory. On macOS that is normally
`~/Library/Application Support/factorio/mods/`. Restart Factorio after copying.

## Controller commands

| Command | Effect |
| --- | --- |
| `bin/companion spawn --name Ada` | Create Ada once, or return the existing companion |
| `bin/companion status` | Show identity, position, inventory, lease and motion state |
| `bin/companion move X Y` | Walk to an absolute point and wait for arrival |
| `bin/companion move DX DY --relative` | Walk by an offset and wait for arrival |
| `bin/companion stop` | Stop immediately and revoke any controller lease |
| `bin/companion connect` | Interactive status/movement session |

Interactive commands are `status`, `move X Y`, `move-by DX DY`, `stop`, and
`quit`. Ctrl+C and SIGTERM release the lease and stop movement. If the process
is killed or loses RCON, the game-side lease expires after 180 simulation ticks
(normally three seconds) and stops walking, mining, and shooting. Loading a save
also revokes any saved lease on its first tick.

Only one controller can hold the lease. A movement command is limited to 64
tiles, uses normal character speed and collisions, and stops if progress stalls
for two seconds. This milestone deliberately provides direct steering rather
than pathfinding; obstacle-aware navigation belongs to the next milestone.

For a remote development server, keep RCON private and forward it over SSH:

```bash
ssh -N -L 27016:127.0.0.1:27016 user@development-host
bin/companion --host 127.0.0.1 --port 27016 \
  --password-file /secure/path/to/rconpw status
```

Do not expose RCON directly to the internet. The game password and RCON
password are separate credentials.

## Verification

Unit tests need no server:

```bash
python3 -m unittest discover -s tests -v
```

The Docker smoke test creates and removes its own data and container:

```bash
python3 tests/companion_smoke.py
```

It verifies idempotent spawning, inventory preservation, normal movement,
collision blocking, exclusive control, duplicate move handling, emergency stop,
Ctrl+C cleanup, forced-process-death cleanup, and save/restart/reconnect.
