#!/usr/bin/env python3
"""Have a real local model design and build a sustained smelting setup."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.client import BridgeError, Companion
from companion.factory import FactoryRuntime
from companion.models import OllamaAdapter, SequencePlanAdapter
from companion.package import build
from companion.rcon import RconClient, RconError


def docker(*args):
    return subprocess.run(["docker", *args], check=True, text=True, stdout=subprocess.PIPE).stdout


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen3:8b"
    name = "factorio-factory-challenge-" + uuid.uuid4().hex[:10]
    password = secrets.token_urlsafe(24)
    transport = None
    with tempfile.TemporaryDirectory(prefix="factorio-factory-challenge-") as directory:
        data = Path(directory) / "data"
        config, mods = data / "config", data / "mods"
        config.mkdir(parents=True)
        mods.mkdir()
        build(mods / "factorio-ai-companion_0.6.0.zip")
        (mods / "mod-list.json").write_text(json.dumps({"mods": [
            {"name": "base", "enabled": True}, {"name": "factorio-ai-companion", "enabled": True},
        ]}))
        settings = json.loads((ROOT / "server/defaults/server-settings.json").read_text())
        settings.update(auto_pause=False, require_user_verification=False)
        (config / "server-settings.json").write_text(json.dumps(settings))
        (config / "rconpw").write_text(password)
        (config / "map-gen-settings.json").write_text(json.dumps({
            "seed": 12345, "peaceful_mode": True,
            "autoplace_controls": {"enemy-base": {"frequency": 0, "size": 0, "richness": 0}},
        }))
        try:
            docker("run", "-d", "--platform", "linux/amd64", "--name", name,
                   "--mount", "type=bind,source=" + str(data) + ",target=/factorio",
                   "--tmpfs", "/factorio/temp:mode=1777", "-p", "127.0.0.1::27015",
                   "--stop-timeout", "120", "-e", "PUID=" + str(os.getuid()),
                   "-e", "PGID=" + str(os.getgid()), "-e", "DLC_SPACE_AGE=false",
                   os.environ.get("FACTORIO_TEST_IMAGE", "factoriotools/factorio:2.0.77"))
            port = int(docker("port", name, "27015/tcp").strip().rsplit(":", 1)[1])
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                try:
                    transport = RconClient("127.0.0.1", port, password)
                    client = Companion(transport)
                    client.request("status")
                    break
                except (OSError, RconError, BridgeError):
                    if transport:
                        transport.close()
                    transport = None
                    time.sleep(0.2)
            if not transport:
                raise RuntimeError("Disposable Factorio server did not start.")

            client.request("spawn", name="Ada")
            transport.command("/sc rcon.print('factory-challenge-scripting-enabled')")
            setup_output = transport.command(
                "/sc local s=game.surfaces.nauvis; local c=s.find_entities_filtered{type='character'}[1]; "
                "for _,e in pairs(s.find_entities_filtered{area={{-12,-12},{12,12}}}) do "
                "if e~=c and e.type~='character' then e.destroy() end end; "
                "local tiles={}; for x=-12,12 do for y=-12,12 do "
                "tiles[#tiles+1]={name='grass-1',position={x,y}} end end; s.set_tiles(tiles); "
                "c.teleport({5,0}); c.insert{name='burner-mining-drill',count=1}; "
                "c.insert{name='stone-furnace',count=1}; c.insert{name='coal',count=10}; "
                "for x=-1,0 do for y=-1,0 do s.create_entity{name='iron-ore',"
                "position={x+0.5,y+0.5},amount=10000} end end; "
                "for x=-1,2 do "
                "s.create_entity{name='stone-wall',position={x,-3},force='player'} end"
            )
            client.acquire()
            prepared = client.request("observe", radius=16)
            inventory = prepared["self"]["inventory"]
            resources = prepared["local_area"]["resources"]
            if (inventory.get("burner-mining-drill:normal") != 1
                    or inventory.get("stone-furnace:normal") != 1
                    or not any(value["name"] == "iron-ore" for value in resources)):
                raise RuntimeError("Factory scene setup failed: " + setup_output + " " + json.dumps(prepared))

            def record(**event):
                print(json.dumps(event, separators=(",", ":")), flush=True)

            if model == "sequence":
                adapter = SequencePlanAdapter([{
                    "name": "deterministic direct smelter", "reason": "Exercise real mechanics.",
                    "placements": [
                        {"id": "drill", "item": "burner-mining-drill", "x": 0, "y": 0,
                         "direction": 4},
                        {"id": "furnace", "item": "stone-furnace", "x": 2, "y": 0,
                         "direction": 0},
                    ],
                    "connections": [{"from": "drill", "to": "furnace", "kind": "direct-output"}],
                    "supplies": [
                        {"entity": "drill", "inventory": "fuel", "item": "coal", "count": 1},
                        {"entity": "furnace", "inventory": "fuel", "item": "coal", "count": 1},
                    ],
                }])
            else:
                adapter = OllamaAdapter(model)
            runtime = FactoryRuntime(
                client, adapter,
                "Build a sustainable iron smelting setup from the available materials.",
                emit=record, max_designs=4, max_seconds=300, sample_seconds=12,
            )
            started = time.monotonic()
            verified = runtime.run()
            summary = {
                "model": model, "designs": runtime.designs, "failures": runtime.failures,
                "recoveries": runtime.recoveries, "wall_seconds": round(time.monotonic() - started, 2),
                **verified,
            }
            print("PASS: " + json.dumps(summary, sort_keys=True), flush=True)
        finally:
            if transport:
                transport.close()
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
