#!/usr/bin/env python3
"""Run the first real-model job in a disposable Factorio world."""
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

from companion.agent import AgentRuntime, parse_goal
from companion.client import BridgeError, Companion
from companion.models import OllamaAdapter
from companion.package import build
from companion.rcon import RconClient, RconError


def docker(*args):
    return subprocess.run(["docker", *args], check=True, text=True, stdout=subprocess.PIPE).stdout


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen3:8b"
    name = "factorio-model-challenge-" + uuid.uuid4().hex[:10]
    password = secrets.token_urlsafe(24)
    transport = None
    with tempfile.TemporaryDirectory(prefix="factorio-model-challenge-") as directory:
        data = Path(directory) / "data"
        config, mods = data / "config", data / "mods"
        config.mkdir(parents=True)
        mods.mkdir()
        build(mods / "factorio-ai-companion_0.5.0.zip")
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
            position = client.request("status")["position"]
            furnace = {"x": position["x"] + 2, "y": position["y"]}
            # A fresh server consumes the first /sc command to enable scripting.
            # Warm it up harmlessly before preparing this disposable scene.
            transport.command("/sc rcon.print('model-challenge-scripting-enabled')")
            setup_output = transport.command(
                "/sc local s=game.surfaces.nauvis; local c=s.find_entities_filtered{type='character'}[1]; "
                "for _,e in pairs(s.find_entities_filtered{area={{c.position.x-8,c.position.y-8},"
                "{c.position.x+8,c.position.y+8}},type={'tree','simple-entity','cliff'}}) do e.destroy() end; "
                "c.insert{name='iron-ore',count=20}; c.insert{name='coal',count=2}; "
                "s.create_entity{name='stone-furnace',position={"
                + str(furnace["x"]) + "," + str(furnace["y"]) + "},force='player'}"
            )
            client.acquire()
            prepared = client.request("observe", radius=16)
            if prepared["self"]["inventory"].get("iron-ore:normal") != 20 or not any(
                entity["name"] == "stone-furnace" for entity in prepared["local_area"]["buildings"]
            ):
                raise RuntimeError("Challenge scene setup failed: " + setup_output + " " + json.dumps(prepared))
            events = []

            def record(**event):
                events.append(event)
                print(json.dumps(event, separators=(",", ":")), flush=True)

            runtime = AgentRuntime(
                client, OllamaAdapter(model), parse_goal("Produce 20 iron plates from available supplies."),
                emit=record, max_decisions=30, max_seconds=300, max_consecutive_failures=5,
            )
            started = time.monotonic()
            result = runtime.run()
            summary = {
                "model": model, "verified_iron_plates": result["self"]["inventory"]["iron-plate:normal"],
                "decisions": runtime.decisions, "failures": runtime.failures,
                "recoveries": runtime.recoveries, "wall_seconds": round(time.monotonic() - started, 2),
            }
            print("PASS: " + json.dumps(summary, sort_keys=True), flush=True)
        finally:
            if transport:
                transport.close()
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
