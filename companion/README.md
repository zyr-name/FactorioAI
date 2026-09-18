# Scripted companion

The companion is a real, persistent `character` entity owned by the player
force. A Factorio mod stores its identity and inventory in the save; a Python
controller sends versioned JSON commands over RCON.

## Install and run

The complete local stack installs the mod automatically:

```bash
bin/start
```

Start Ada in the portal's **AI Players** section. The portal holds the controller
lease, records each run, and shows game-side lifetime movement, mining, crafting,
placement, and transfer counters.
For direct CLI testing, stop Ada in the portal first, then use
`bin/companion status` or `bin/companion move 5 0 --relative`.

For autonomous work, install an Ollama model (`ollama pull qwen3:8b`), enter
`ollama:qwen3:8b` as the player's model, and give it one measurable instruction
such as `Produce 20 iron plates from available supplies.` The worker uses fresh
observations and one schema-validated action per turn. The portal shows its
current phase, objective, latest decision, failures, and recovery attempts, and
provides pause, resume, and stop controls. `scripted` remains the passive default.

An instruction such as `Build a sustainable iron smelting setup` switches to the
factory planner. It proposes a burner-drill/stone-furnace layout, preflights the full
plan without changing the world, builds and fuels it transactionally, and reports
success only after the furnace's iron-plate output grows across two samples. Failed
placement or production checks roll the new entities back before redesigning.

`install-companion` creates a complete backup, installs/enables the mod, and
sets `auto_pause=false` so movement and the disconnect watchdog continue when
no human is connected. It preserves an existing character when updating the
same mod. The RCON port is published only on `127.0.0.1:27016` by default.

Every joining game client needs the same mod ZIP. Build it with:

```bash
bin/companion package
```

Copy the generated `dist/factorio-ai-companion_0.6.0.zip` into the Factorio client's `mods`
directory. On macOS that is normally
`~/Library/Application Support/factorio/mods/`. Restart Factorio after copying.

## Controller commands

| Command | Effect |
| --- | --- |
| `bin/companion spawn --name Ada` | Create Ada once, or return the existing companion |
| `bin/companion status` | Show identity, position, inventory, lease and motion state |
| `bin/companion move X Y` | Walk to an absolute point and wait for arrival |
| `bin/companion move DX DY --relative` | Walk by an offset and wait for arrival |
| `bin/companion move X Y --radius 3` | Walk to any reachable point within interaction range |
| `bin/companion inspect X Y --radius 8` | List nearby entities and inventories that are within reach |
| `bin/companion observe --radius 16` | Summarize local resources/buildings, explored map, craftable recipes, and machine tasks |
| `bin/companion mine X Y --name iron-ore` | Mine a reachable entity using its normal mining time |
| `bin/companion craft stone-furnace --count 1` | Hand-craft an enabled recipe with inventory ingredients |
| `bin/companion place stone-furnace X Y` | Consume and place an inventory item within build reach |
| `bin/companion rotate X Y --name transport-belt` | Rotate a reachable entity |
| `bin/companion transfer to source iron-ore 1 X Y` | Move items to/from a chest, fuel, source, result, input, or output inventory |
| `bin/companion stop` | Stop immediately and revoke any controller lease |
| `bin/companion connect` | Interactive status/movement session |

Interactive commands are `status`, `move X Y`, `move-by DX DY`, `stop`, and
`quit`. Ctrl+C and SIGTERM release the lease and stop movement. If the process
is killed or loses RCON, the game-side lease expires after 180 simulation ticks
(normally three seconds) and stops walking, mining, and shooting. Loading a save
also revokes any saved lease on its first tick.

Only one controller can hold the lease. Movement, mining, and crafting are
long-running actions; starting another action cancels the active one. A movement command is limited to 256
tiles and uses Factorio's native pathfinder with the character's real collision
rules. It detours around static obstacles and replans up to three times if the
world changes or progress stalls. Mining uses the prototype's mining time and
the character/force mining speed before Factorio performs the yield and capacity
check. Crafting and furnace processing use Factorio's native queues and recipes.
Every command ID is idempotent and retains a
bounded queued/running/completed/failed/cancelled action record.

## Observation boundaries

`observe` is deliberately narrower than the Lua API's omniscient view. Local
resources and player-force buildings are reported within 1–32 tiles around the
character. Machine inventories, selected recipes, and diagnoses are included
only while the character can interact with the entity. The wider map section
aggregates resources and buildings only from chunks charted by the player force;
it never reveals generated-but-unexplored chunks. Local building details are
capped at 200 entries and diagnoses at 100; map scans are capped at 256 charted
chunks and 20,000 entities, and recipe lists at 50 entries. Every bounded section
reports whether it was truncated.

Available recipes are enabled, non-hidden recipes the current character can
craft from its inventory. Machine tasks are sorted by urgency and currently
diagnose a blocked output, missing burner fuel, and missing recipe ingredients.

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

The factory acceptance test has deterministic and real-model modes:

```bash
python3 tests/factory_challenge.py sequence
python3 tests/factory_challenge.py qwen3:8b
```

For repeatable comparisons, use the versioned benchmark scenario instead:

```bash
bin/benchmark --model sequence --model qwen3:8b --repeat 2
```

Every run starts from a clean fixed-seed world. JSON data and a readable Markdown
comparison are written to `benchmark-results/`; the report records both simulation
ticks and wall time because the game continues running while Ollama is thinking.

It verifies idempotent spawning, inventory preservation, normal movement,
collision blocking, exclusive control, duplicate handling, emergency stop,
craft cancellation/refunds, inspect/observe/mine/craft/place/rotate/transfer, plan
preflight, a timed
furnace-to-iron-plate sequence, forced-process-death cleanup, and save/restart/reconnect.
