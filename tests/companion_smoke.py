#!/usr/bin/env python3
"""Exercise the real mod/controller in a disposable world; no production data."""
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from companion.client import BridgeError, Companion
from companion.package import build
from companion.rcon import RconClient, RconError


def docker(*args, capture=True):
    return subprocess.run(["docker", *args], check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def wait_for(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.15)
    raise AssertionError("Timed out waiting for " + getattr(check, "__name__", str(check)))


def assert_still(client):
    first = client.request("status")
    time.sleep(0.6)
    second = client.request("status")
    assert not second["walking"], second
    assert second["position"] == first["position"], (first, second)
    return second


def main():
    name = "factorio-companion-smoke-" + uuid.uuid4().hex[:10]
    password = secrets.token_urlsafe(24)
    transport = None
    child = None
    with tempfile.TemporaryDirectory(prefix="factorio-companion-smoke-") as directory:
        data = Path(directory) / "data"
        config = data / "config"
        config.mkdir(parents=True)
        mods = data / "mods"
        mods.mkdir()
        build(mods / "factorio-ai-companion_0.4.0.zip")
        (mods / "mod-list.json").write_text(json.dumps({"mods": [
            {"name": "base", "enabled": True}, {"name": "factorio-ai-companion", "enabled": True}]}))
        settings = json.loads((ROOT / "server/defaults/server-settings.json").read_text())
        settings.update(auto_pause=False, require_user_verification=False)
        (config / "server-settings.json").write_text(json.dumps(settings))
        (config / "rconpw").write_text(password)
        (config / "map-gen-settings.json").write_text(json.dumps({
            "seed": 12345, "peaceful_mode": True,
            "autoplace_controls": {"enemy-base": {"frequency": 0, "size": 0, "richness": 0}}}))
        try:
            docker("run", "-d", "--platform", "linux/amd64", "--name", name,
                   "--mount", "type=bind,source=" + str(data) + ",target=/factorio",
                   "--tmpfs", "/factorio/temp:mode=1777", "-p", "127.0.0.1::27015",
                   "--stop-timeout", "120", "-e", "PUID=" + str(os.getuid()),
                   "-e", "PGID=" + str(os.getgid()), "-e", "DLC_SPACE_AGE=false",
                   os.environ.get("FACTORIO_TEST_IMAGE", "factoriotools/factorio:2.0.77"))
            port = [int(docker("port", name, "27015/tcp").strip().rsplit(":", 1)[1])]

            def connect():
                try:
                    connection = RconClient("127.0.0.1", port[0], password)
                    Companion(connection).request("status")
                    return connection
                except (OSError, RconError, BridgeError):
                    if "connection" in locals():
                        connection.close()
                    return None

            transport = wait_for(connect, timeout=180)
            client = Companion(transport)
            assert client.request("status")["state"] == "absent"
            map_query = ("/sc rcon.print(helpers.table_to_json{"
                         "seed=game.surfaces.nauvis.map_gen_settings.seed,"
                         "enemy=game.surfaces.nauvis.map_gen_settings.autoplace_controls['enemy-base'].frequency})")
            transport.command(map_query)  # Confirm script commands for this disposable save.
            map_settings = json.loads(transport.command(map_query))
            assert map_settings == {"seed": 12345, "enemy": 0}, map_settings
            print("PASS: test world has fixed seed 12345 and enemy bases disabled", flush=True)
            initial = client.request("spawn", name="Ada")
            assert client.request("spawn", name="Ignored")["id"] == initial["id"]
            assert client.request("status")["name"] == "Ada"
            print("PASS: spawn is idempotent and character identity is stable", flush=True)

            # Test-only console setup: flat walking area and nonempty inventory.
            # These cheats are deliberately not part of the production bridge.
            transport.command("/sc local s=game.surfaces.nauvis; "
                              "for _,e in pairs(s.find_entities_filtered{area={{-70,-70},{70,70}},type={'tree','simple-entity','cliff'}}) do e.destroy() end; "
                              "local tiles={}; for x=-70,70 do for y=-70,70 do tiles[#tiles+1]={name='grass-1',position={x,y}} end end; s.set_tiles(tiles); "
                              "for _,e in pairs(s.find_entities_filtered{type='character'}) do "
                              "e.insert{name='stone',count=10}; e.insert{name='coal',count=2}; "
                              "e.insert{name='transport-belt',count=1}; e.insert{name='wood',count=7} end")
            inventory = client.request("status")["inventory"]
            assert inventory == {"coal:normal": 2, "stone:normal": 10,
                                 "transport-belt:normal": 1, "wood:normal": 7}, inventory
            client.acquire()
            second_transport = RconClient("127.0.0.1", port[0], password)
            second = Companion(second_transport)
            try:
                try:
                    second.acquire()
                    raise AssertionError("A second controller acquired an active lease")
                except BridgeError as error:
                    assert error.code == "busy", error

                cancelling = client.request("craft", recipe="stone-furnace", count=2)
                cancelling_id = cancelling["operation"]["command_id"]
                time.sleep(0.1)
                second.request("stop")
                cancelled = next(action for action in second.request("status")["actions"]
                                 if action["id"] == cancelling_id)
                assert cancelled["state"] == "cancelled", cancelled
                assert second.request("status")["inventory"]["stone:normal"] == 10
                client.session = None
                client.acquire()
                print("PASS: emergency stop cancels hand-crafting and refunds ingredients", flush=True)

                position = client.request("status")["position"]
                ore_x, ore_y = position["x"] + 1.5, position["y"]
                furnace_x, furnace_y = position["x"] - 2, position["y"]
                belt_x, belt_y = position["x"], position["y"] + 2
                transport.command("/sc game.surfaces.nauvis.create_entity{name='iron-ore',position={"
                                  + str(ore_x) + "," + str(ore_y) + "},amount=10}")
                nearby = client.request("inspect", x=position["x"], y=position["y"], radius=4)
                ore = next(entity for entity in nearby["entities"] if entity["name"] == "iron-ore")
                assert ore["reachable"] and ore["amount"] == 10, ore
                mined = client.mine(ore_x, ore_y, name="iron-ore", timeout=20)
                mine_action = mined["actions"][0]
                assert mine_action["finished_tick"] - mine_action["started_tick"] >= 60, mine_action
                assert mined["inventory"]["iron-ore:normal"] == 1, mined
                assert next(entity for entity in client.request("inspect", x=ore_x, y=ore_y, radius=1)["entities"]
                            if entity["name"] == "iron-ore")["amount"] == 9
                crafted = client.craft("stone-furnace")
                craft_action = crafted["actions"][0]
                assert craft_action["finished_tick"] - craft_action["started_tick"] >= 20, craft_action
                assert "stone-furnace:normal" in crafted["inventory"], crafted
                assert crafted["inventory"]["stone:normal"] == 5, crafted

                place_id = uuid.uuid4().hex
                placed = client.request("place", id=place_id, item="stone-furnace",
                                        x=furnace_x, y=furnace_y, direction=0)
                placed_action = next(action for action in placed["actions"] if action["id"] == place_id)
                assert placed_action["state"] == "completed", placed_action
                duplicate_place = client.request("place", id=place_id, item="stone-furnace",
                                                  x=furnace_x, y=furnace_y, direction=0)
                assert next(action for action in duplicate_place["actions"]
                            if action["id"] == place_id) == placed_action

                client.request("place", item="transport-belt", x=belt_x, y=belt_y, direction=0)
                rotate_id = uuid.uuid4().hex
                rotated = client.request("rotate", id=rotate_id, x=belt_x, y=belt_y,
                                         name="transport-belt")
                rotated_action = next(action for action in rotated["actions"] if action["id"] == rotate_id)
                duplicate_rotate = client.request("rotate", id=rotate_id, x=belt_x, y=belt_y,
                                                   name="transport-belt")
                assert next(action for action in duplicate_rotate["actions"]
                            if action["id"] == rotate_id) == rotated_action

                ore_transfer_id = uuid.uuid4().hex
                client.request("transfer", id=ore_transfer_id, direction="to", inventory="source",
                               item="iron-ore", count=1, x=furnace_x, y=furnace_y, name="stone-furnace")
                client.request("transfer", id=ore_transfer_id, direction="to", inventory="source",
                               item="iron-ore", count=1, x=furnace_x, y=furnace_y, name="stone-furnace")
                client.request("transfer", direction="to", inventory="fuel", item="coal", count=1,
                               x=furnace_x, y=furnace_y, name="stone-furnace")

                def plate_ready():
                    inspected = client.request("inspect", x=furnace_x, y=furnace_y, radius=1)
                    furnace = next(entity for entity in inspected["entities"]
                                   if entity["name"] == "stone-furnace")
                    return furnace if furnace["inventories"]["result"].get("iron-plate:normal") == 1 else None

                furnace = wait_for(plate_ready, timeout=15)
                assert furnace["inventories"]["source"] == {}, furnace
                client.request("transfer", direction="from", inventory="result", item="iron-plate", count=1,
                               x=furnace_x, y=furnace_y, name="stone-furnace")
                inventory = client.request("status")["inventory"]
                assert inventory == {"coal:normal": 1, "iron-plate:normal": 1,
                                     "stone:normal": 5, "wood:normal": 7}, inventory
                transport.command("/sc local e=game.surfaces.nauvis.find_entity('stone-furnace',{"
                                  + str(furnace_x) + "," + str(furnace_y)
                                  + "}); e.get_inventory(defines.inventory.furnace_result).insert{name='iron-plate',count=100}")
                try:
                    client.request("transfer", direction="to", inventory="result", item="coal", count=1,
                                   x=furnace_x, y=furnace_y, name="stone-furnace")
                    raise AssertionError("A full furnace output accepted coal")
                except BridgeError as error:
                    assert error.code == "destination_full", error
                assert client.request("status")["inventory"] == inventory
                print("PASS: inspect, timed mining/crafting, place, rotate and transfer produce one iron plate", flush=True)
                print("PASS: repeated and rejected mutations do not lose or duplicate items or entities", flush=True)

                origin = client.request("status")["position"]
                arrived = client.move(5, 0, relative=True)
                assert abs(arrived["position"]["x"] - origin["x"] - 5) < 0.31
                assert arrived["inventory"] == inventory
                original_motion = arrived["motion"]
                original_action = next(action for action in arrived["actions"]
                                       if action["id"] == original_motion["command_id"])
                assert original_action["state"] == "completed", original_action
                duplicate = client.request("move", id=original_motion["command_id"], x=origin["x"] + 5, y=origin["y"])
                assert duplicate["motion"] == original_motion
                duplicate_action = next(action for action in duplicate["actions"]
                                        if action["id"] == original_motion["command_id"])
                assert duplicate_action == original_action, (duplicate_action, original_action)
                print("PASS: movement completes once and command IDs are idempotent", flush=True)

                position = client.request("status")["position"]
                wall_x = math.floor(position["x"]) + 3
                wall_y = math.floor(position["y"])
                transport.command("/sc for y=" + str(wall_y - 4) + "," + str(wall_y + 4)
                                  + " do game.surfaces.nauvis.create_entity{name='stone-wall',position={"
                                  + str(wall_x) + ",y},force='player'} end")
                detour = client.move(8, 0, relative=True)
                assert abs(detour["position"]["x"] - position["x"] - 8) < 0.4, detour
                assert detour["statistics"]["moves_completed"] >= 2, detour
                transport.command("/sc for _,e in pairs(game.surfaces.nauvis.find_entities_filtered{name='stone-wall'}) do e.destroy() end")
                print("PASS: native pathfinding detours around a wall", flush=True)

                position = client.request("status")["position"]
                target_x, target_y = math.floor(position["x"]) + 6, math.floor(position["y"])
                transport.command(
                    "/sc local s=game.surfaces.nauvis; local x=" + str(target_x) + "; local y=" + str(target_y)
                    + "; for d=-2,2 do s.create_entity{name='stone-wall',position={x-2,y+d},force='player'}; "
                    + "s.create_entity{name='stone-wall',position={x+2,y+d},force='player'}; "
                    + "if d>-2 and d<2 then s.create_entity{name='stone-wall',position={x+d,y-2},force='player'}; "
                    + "s.create_entity{name='stone-wall',position={x+d,y+2},force='player'} end end")
                try:
                    client.move(target_x, target_y)
                    raise AssertionError("Reached a sealed target")
                except BridgeError as error:
                    assert error.code == "unreachable", error
                assert_still(client)
                transport.command("/sc for _,e in pairs(game.surfaces.nauvis.find_entities_filtered{name='stone-wall'}) do e.destroy() end")
                print("PASS: an unreachable target fails with a retained action result", flush=True)

                position = client.request("status")["position"]
                moving = client.request("move", x=position["x"] + 20, y=position["y"])
                command_id = moving["motion"]["command_id"]
                wait_for(lambda: client.request("heartbeat")["motion"]["state"] == "moving")
                current = client.request("status")["position"]
                wall_x, wall_y = math.floor(current["x"]) + 3, math.floor(current["y"])
                transport.command("/sc for y=" + str(wall_y - 4) + "," + str(wall_y + 4)
                                  + " do game.surfaces.nauvis.create_entity{name='stone-wall',position={"
                                  + str(wall_x) + ",y},force='player'} end")

                def completed_replan():
                    state = client.request("heartbeat")
                    action = next(item for item in state["actions"] if item["id"] == command_id)
                    return state if action["state"] not in {"queued", "running"} else None

                replanned = wait_for(completed_replan, timeout=40)
                replanned_action = next(item for item in replanned["actions"] if item["id"] == command_id)
                assert replanned_action["state"] == "completed", replanned
                assert replanned["motion"]["replan_count"] >= 1, replanned
                transport.command("/sc for _,e in pairs(game.surfaces.nauvis.find_entities_filtered{name='stone-wall'}) do e.destroy() end")
                print("PASS: a newly blocked route replans and completes", flush=True)

                position = client.request("status")["position"]
                client.request("move", x=position["x"] + 30, y=position["y"])
                time.sleep(0.3)
                second.request("stop")
                stopped = assert_still(second)
                cancelled = next(action for action in stopped["actions"]
                                 if action["id"] == stopped["motion"]["command_id"])
                assert cancelled["state"] == "cancelled", cancelled
                client.session = None
                print("PASS: emergency stop cancels the action and revokes the lease", flush=True)
            finally:
                second_transport.close()

            command = [str(ROOT / "bin/companion"), "--host", "127.0.0.1", "--port", str(port[0]),
                       "--password-file", str(config / "rconpw"), "move", "60", "0", "--relative"]
            for abrupt in (False, True):
                child = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                wait_for(lambda: client.request("status").get("motion", {}).get("state") == "moving")
                if abrupt:
                    child.kill()
                else:
                    child.send_signal(signal.SIGINT)
                _, stderr = child.communicate(timeout=10)
                assert child.returncode == (-signal.SIGKILL if abrupt else 130), stderr
                child = None
                wait_for(lambda: not client.request("status")["connected"], timeout=10)
                state = assert_still(client)
                assert state["motion"]["state"] == ("lease_expired" if abrupt else "disconnected"), state
                assert state["inventory"] == inventory
                print("PASS: " + ("SIGKILL watchdog" if abrupt else "Ctrl+C cleanup") + " stops movement", flush=True)

            client.acquire()
            client.release()
            before_restart = client.request("status")
            for key, expected in {"items_mined": 1, "items_crafted": 1, "entities_placed": 2,
                                  "entities_rotated": 1, "items_transferred": 3}.items():
                assert before_restart["statistics"][key] == expected, (key, before_restart)
            transport.command("/server-save companion-persistence")
            wait_for(lambda: (data / "saves/companion-persistence.zip").exists())
            transport.close()
            transport = None
            docker("restart", name)
            # Docker Desktop can reassign an ephemeral published port on restart.
            port[0] = int(docker("port", name, "27015/tcp").strip().rsplit(":", 1)[1])
            transport = wait_for(connect, timeout=180)
            client = Companion(transport)
            after_restart = client.request("status")
            for key in ("id", "name", "position", "inventory"):
                assert before_restart[key] == after_restart[key], (key, before_restart, after_restart)
            for key in ("items_mined", "items_crafted", "entities_placed",
                        "entities_rotated", "items_transferred"):
                assert before_restart["statistics"][key] == after_restart["statistics"][key]
            client.acquire()
            position = client.request("status")["position"]
            client.request("move", x=position["x"] + 30, y=position["y"])
            wait_for(lambda: client.request("status").get("motion", {}).get("state") == "moving")
            transport.close()
            transport = None
            docker("restart", name)
            port[0] = int(docker("port", name, "27015/tcp").strip().rsplit(":", 1)[1])
            transport = wait_for(connect, timeout=180)
            client = Companion(transport)
            restarted = assert_still(client)
            assert restarted["motion"]["state"] == "server_restarted", restarted
            for key in ("id", "name", "inventory"):
                assert before_restart[key] == restarted[key], (key, before_restart, restarted)
            print("PASS: save/restart/reconnect preserves identity, position and inventory", flush=True)
            print("PASS: restart revokes an active controller and stops movement", flush=True)
        except BaseException:
            logs = subprocess.run(["docker", "logs", "--tail", "90", name], capture_output=True, text=True)
            print((logs.stdout + logs.stderr).replace(password, "[REDACTED]"), file=sys.stderr)
            raise
        finally:
            if child is not None:
                child.kill()
                child.wait(timeout=10)
            if transport is not None:
                transport.close()
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
