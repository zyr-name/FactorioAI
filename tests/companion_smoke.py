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
        build(mods / "factorio-ai-companion_0.2.0.zip")
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
                              "for _,e in pairs(s.find_entities_filtered{type='character'}) do e.insert{name='iron-plate',count=11}; e.insert{name='wood',count=7} end")
            inventory = client.request("status")["inventory"]
            assert inventory == {"iron-plate:normal": 11, "wood:normal": 7}, inventory
            client.acquire()
            second_transport = RconClient("127.0.0.1", port[0], password)
            second = Companion(second_transport)
            try:
                try:
                    second.acquire()
                    raise AssertionError("A second controller acquired an active lease")
                except BridgeError as error:
                    assert error.code == "busy", error
                origin = client.request("status")["position"]
                arrived = client.move(5, 0, relative=True)
                assert abs(arrived["position"]["x"] - origin["x"] - 5) < 0.31
                assert arrived["inventory"] == inventory
                original_motion = arrived["motion"]
                duplicate = client.request("move", id=original_motion["command_id"], x=origin["x"] + 5, y=origin["y"])
                assert duplicate["motion"] == original_motion
                print("PASS: movement arrives, keeps inventory and ignores a repeated move", flush=True)

                position = client.request("status")["position"]
                wall_x = math.floor(position["x"]) + 3
                wall_y = math.floor(position["y"])
                transport.command("/sc for y=" + str(wall_y - 4) + "," + str(wall_y + 4)
                                  + " do game.surfaces.nauvis.create_entity{name='stone-wall',position={"
                                  + str(wall_x) + ",y},force='player'} end")
                try:
                    client.move(8, 0, relative=True)
                    raise AssertionError("Walked through an obstacle")
                except BridgeError as error:
                    assert error.code == "blocked", error
                assert_still(client)
                transport.command("/sc for _,e in pairs(game.surfaces.nauvis.find_entities_filtered{name='stone-wall'}) do e.destroy() end")
                print("PASS: blocked movement stops without bypassing collisions", flush=True)

                position = client.request("status")["position"]
                client.request("move", x=position["x"] + 30, y=position["y"])
                time.sleep(0.3)
                second.request("stop")
                assert_still(second)
                client.session = None
                print("PASS: emergency stop revokes the controller lease", flush=True)
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
