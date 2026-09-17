import argparse
import json
import math
from pathlib import Path
import select
import shlex
import signal
import subprocess
import sys
import time

from companion.client import BridgeError, Companion
from companion.package import build, SOURCE
from companion.rcon import RconClient, RconError

ROOT = Path(__file__).resolve().parents[1]


def coordinate(text):
    try:
        value = float(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Coordinates must be numbers.") from error
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError("Coordinates must be finite.")
    return value


def radius(text):
    value = coordinate(text)
    if not 0.2 <= value <= 10:
        raise argparse.ArgumentTypeError("Radius must be between 0.2 and 10 tiles.")
    return value


def parser():
    root = argparse.ArgumentParser(description="Control one persistent Factorio companion.")
    root.add_argument("--host", help="RCON host; default is the local experiment server")
    root.add_argument("--port", type=int, help="RCON port (default: server configuration, or 27016 with --host)")
    root.add_argument("--password-file", type=Path, default=ROOT / "server/data/config/rconpw")
    commands = root.add_subparsers(dest="command", required=True)
    for command in ("spawn", "connect"):
        sub = commands.add_parser(command, help="Create/reuse the character" if command == "spawn" else "Interactive controller with heartbeats")
        sub.add_argument("--name", default="Ada")
    commands.add_parser("status", help="Inspect identity, position, inventory and movement")
    commands.add_parser("stop", help="Emergency stop and revoke the controller lease")
    move = commands.add_parser("move", help="Walk to a nearby point; Ctrl+C stops")
    move.add_argument("x", type=coordinate)
    move.add_argument("y", type=coordinate)
    move.add_argument("--relative", action="store_true", help="Treat coordinates as offsets")
    move.add_argument("--radius", type=radius, default=0.28, help="Accept any reachable point within this range")
    inspect = commands.add_parser("inspect", help="List nearby entities and reachable inventories")
    inspect.add_argument("x", type=coordinate)
    inspect.add_argument("y", type=coordinate)
    inspect.add_argument("--radius", type=coordinate, default=8)
    mine = commands.add_parser("mine", help="Mine a reachable entity using normal game timing")
    mine.add_argument("x", type=coordinate)
    mine.add_argument("y", type=coordinate)
    mine.add_argument("--name")
    mine.add_argument("--count", type=int, default=1)
    craft = commands.add_parser("craft", help="Hand-craft an enabled recipe")
    craft.add_argument("recipe")
    craft.add_argument("--count", type=int, default=1)
    place = commands.add_parser("place", help="Place an inventory item within build reach")
    place.add_argument("item")
    place.add_argument("x", type=coordinate)
    place.add_argument("y", type=coordinate)
    place.add_argument("--direction", type=int, default=0)
    rotate = commands.add_parser("rotate", help="Rotate a reachable entity")
    rotate.add_argument("x", type=coordinate)
    rotate.add_argument("y", type=coordinate)
    rotate.add_argument("--name")
    rotate.add_argument("--reverse", action="store_true")
    transfer = commands.add_parser("transfer", help="Transfer items to or from an entity inventory")
    transfer.add_argument("direction", choices=("to", "from"))
    transfer.add_argument("inventory", choices=("chest", "fuel", "source", "result", "input", "output"))
    transfer.add_argument("item")
    transfer.add_argument("count", type=int)
    transfer.add_argument("x", type=coordinate)
    transfer.add_argument("y", type=coordinate)
    transfer.add_argument("--name")
    package = commands.add_parser("package", help="Build the mod ZIP for server/client installation")
    package_info = json.loads((SOURCE / "info.json").read_text())
    package.add_argument(
        "--output", type=Path,
        default=ROOT / "dist" / (package_info["name"] + "_" + package_info["version"] + ".zip"),
    )
    return root


def endpoint(args):
    if args.host is not None:
        return args.host, args.port or 27016
    from server.manage import config
    ports = config()["ports"]
    rcon = next((port for port in ports if int(port["target"]) == 27015), None)
    if rcon is None:
        raise ValueError("No RCON port configured. Update the experiment setup and run bin/restart.")
    return "127.0.0.1", args.port or int(rcon["published"])


def display(state):
    print(json.dumps(state, indent=2, sort_keys=True), flush=True)


def interactive(client):
    print("Commands: status | move X Y | move-by DX DY | stop | quit. Ctrl+C disconnects.", flush=True)
    deadline = time.monotonic()
    while True:
        if time.monotonic() >= deadline:
            client.request("heartbeat")
            deadline = time.monotonic() + 0.3
        readable, _, _ = select.select([sys.stdin], [], [], max(0, deadline - time.monotonic()))
        if not readable:
            continue
        line = sys.stdin.readline()
        if not line:
            return
        try:
            words = shlex.split(line)
            if not words:
                continue
            if words == ["quit"]:
                return
            if words == ["status"]:
                display(client.request("status"))
            elif words == ["stop"]:
                client.release()
                display(client.acquire())
            elif len(words) == 3 and words[0] in {"move", "move-by"}:
                # Keep the input loop available so 'stop' can cancel mid-move.
                x, y = coordinate(words[1]), coordinate(words[2])
                if words[0] == "move-by":
                    position = client.request("status")["position"]
                    x, y = x + position["x"], y + position["y"]
                display(client.request("move", x=x, y=y))
            else:
                print("Use status, move X Y, move-by DX DY, stop, or quit.", flush=True)
        except (ValueError, argparse.ArgumentTypeError) as error:
            print(str(error), file=sys.stderr)


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "package":
        print(build(args.output))
        return 0
    transport = None
    client = None
    # SIGTERM follows the same release path as Ctrl+C. SIGKILL is handled by
    # the mod's lease timeout; the game never relies solely on Python cleanup.
    def interrupted(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    try:
        host, port = endpoint(args)
        password = args.password_file.read_text().strip()
        transport = RconClient(host, port, password)
        client = Companion(transport)
        if args.command in {"status", "stop"}:
            display(client.request(args.command))
        elif args.command == "spawn":
            display(client.request("spawn", name=args.name))
        elif args.command == "connect":
            display(client.request("spawn", name=args.name))
            client.acquire()
            interactive(client)
        elif args.command == "move":
            client.acquire()
            display(client.move(args.x, args.y, relative=args.relative, radius=args.radius))
        elif args.command == "inspect":
            client.acquire()
            display(client.request("inspect", x=args.x, y=args.y, radius=args.radius))
        elif args.command == "mine":
            client.acquire()
            display(client.mine(args.x, args.y, count=args.count, name=args.name))
        elif args.command == "craft":
            client.acquire()
            display(client.craft(args.recipe, count=args.count))
        elif args.command == "place":
            client.acquire()
            display(client.request("place", item=args.item, x=args.x, y=args.y,
                                   direction=args.direction))
        elif args.command == "rotate":
            client.acquire()
            fields = {"x": args.x, "y": args.y, "reverse": args.reverse}
            if args.name:
                fields["name"] = args.name
            display(client.request("rotate", **fields))
        elif args.command == "transfer":
            client.acquire()
            fields = {"direction": args.direction, "inventory": args.inventory,
                      "item": args.item, "count": args.count, "x": args.x, "y": args.y}
            if args.name:
                fields["name"] = args.name
            display(client.request("transfer", **fields))
        return 0
    except KeyboardInterrupt:
        print("Disconnecting; stopping companion.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RconError, BridgeError, subprocess.CalledProcessError) as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1
    finally:
        if client and client.session:
            try:
                client.release()
            except (OSError, ValueError, RconError, BridgeError):
                print("Release unavailable; the server lease will expire within 180 simulation ticks.", file=sys.stderr)
        if transport:
            transport.close()


if __name__ == "__main__":
    sys.exit(main())
