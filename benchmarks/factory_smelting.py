"""Disposable, fixed-seed factory-smelting benchmark scenario."""
import datetime
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import uuid

from companion.client import BridgeError, Companion
from companion.factory import FactoryRuntime
from companion.models import ModelError, OllamaAdapter, SequencePlanAdapter
from companion.package import build
from companion.rcon import RconClient, RconError


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = Path(__file__).resolve().parent / "scenarios/factory-smelting.json"


def scenario_spec():
    return json.loads(SPEC_PATH.read_text())


def docker(*args):
    return subprocess.run(["docker", *args], check=True, text=True,
                          stdout=subprocess.PIPE).stdout


def deterministic_adapter():
    return SequencePlanAdapter([{
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


def placement_waste(observation):
    inventory = observation["self"]["inventory"]
    buildings = observation["local_area"]["buildings"]
    recovered = sum(inventory.get(item + ":normal", 0)
                    for item in ("burner-mining-drill", "stone-furnace"))
    built = sum(value.get("name") in {"burner-mining-drill", "stone-furnace"}
                for value in buildings)
    return max(0, 2 - recovered - built)


def run(model, sample_seconds=None, event_sink=None):
    """Run one clean scenario and return a normalized comparison record."""
    spec = scenario_spec()
    sample_seconds = sample_seconds or spec["limits"]["default_sample_seconds"]
    normalized_model = model.removeprefix("ollama:")
    image = os.environ.get("FACTORIO_TEST_IMAGE", "factoriotools/factorio:2.0.77")
    name = "factorio-benchmark-" + uuid.uuid4().hex[:10]
    password = secrets.token_urlsafe(24)
    transport = None
    design_metrics = []
    seen_designs = set()
    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(prefix="factorio-benchmark-") as directory:
        data = Path(directory) / "data"
        config, mods = data / "config", data / "mods"
        config.mkdir(parents=True)
        mods.mkdir()
        build(mods / "factorio-ai-companion_0.6.0.zip")
        (mods / "mod-list.json").write_text(json.dumps({"mods": [
            {"name": "base", "enabled": True},
            {"name": "factorio-ai-companion", "enabled": True},
        ]}))
        settings = json.loads((ROOT / "server/defaults/server-settings.json").read_text())
        settings.update(auto_pause=False, require_user_verification=False)
        (config / "server-settings.json").write_text(json.dumps(settings))
        (config / "rconpw").write_text(password)
        (config / "map-gen-settings.json").write_text(json.dumps({
            "seed": spec["seed"], "peaceful_mode": True,
            "autoplace_controls": {"enemy-base": {"frequency": 0, "size": 0, "richness": 0}},
        }))
        try:
            docker("run", "-d", "--platform", "linux/amd64", "--name", name,
                   "--mount", "type=bind,source=" + str(data) + ",target=/factorio",
                   "--tmpfs", "/factorio/temp:mode=1777", "-p", "127.0.0.1::27015",
                   "--stop-timeout", "120", "-e", "PUID=" + str(os.getuid()),
                   "-e", "PGID=" + str(os.getgid()), "-e", "DLC_SPACE_AGE=false", image)
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
                raise RuntimeError("Disposable Factorio benchmark server did not start.")

            client.request("spawn", name="Ada")
            transport.command("/sc rcon.print('factory-benchmark-scripting-enabled')")
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
                raise RuntimeError(
                    "Factory benchmark scene setup failed: " + setup_output + " " + json.dumps(prepared)
                )

            def record(**event):
                if event_sink:
                    event_sink(event)
                design = event.get("decisions")
                metrics = (event.get("last_action") or {}).get("metrics") or {}
                if event.get("phase") == "validating" and design not in seen_designs:
                    seen_designs.add(design)
                    design_metrics.append(metrics)

            adapter = deterministic_adapter() if normalized_model == "sequence" else OllamaAdapter(normalized_model)
            runtime = FactoryRuntime(
                client, adapter, spec["objective"], emit=record,
                max_designs=spec["limits"]["max_designs"],
                max_seconds=spec["limits"]["max_wall_seconds"],
                sample_seconds=sample_seconds,
            )
            wall_started = time.monotonic()
            verified, runtime_error = None, None
            try:
                verified = runtime.run()
            except (BridgeError, ModelError) as error:
                runtime_error = error
            wall_seconds = round(time.monotonic() - wall_started, 3)
            final = client.request("observe", radius=16)
            game_ticks = final["tick"] - prepared["tick"]
            result = {
                "scenario": spec["id"], "model": normalized_model,
                "success": runtime_error is None,
                "started_at": started_at,
                "ended_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "factorio_image": image, "seed": spec["seed"],
                "wall_seconds": wall_seconds, "game_ticks": game_ticks,
                "game_seconds": round(game_ticks / 60, 3),
                "decisions": runtime.designs, "failures": runtime.failures,
                "recoveries": runtime.recoveries, "rollbacks": runtime.rollbacks,
                "rolled_back_entities": runtime.rolled_back_entities,
                "prompt_tokens": sum(value.get("prompt_tokens") or 0 for value in design_metrics),
                "output_tokens": sum(value.get("output_tokens") or 0 for value in design_metrics),
                "inference_seconds": round(sum(value.get("total_duration_ns") or 0
                                               for value in design_metrics) / 1_000_000_000, 3),
                "estimated_cost_usd": 0.0,
                "cost_basis": "Local model execution; electricity and hardware amortization excluded.",
                "waste_items": placement_waste(final),
                "human_interventions": 0,
                "sustained_production": runtime_error is None,
                "product": verified["product"] if verified else "iron-plate",
                "first_count": verified["first_count"] if verified else None,
                "second_count": verified["second_count"] if verified else None,
                "increase": verified["increase"] if verified else None,
                "sample_seconds": sample_seconds,
            }
            if runtime_error:
                result["error"] = str(runtime_error)
            return result
        finally:
            if transport:
                transport.close()
            subprocess.run(["docker", "rm", "-f", name], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
