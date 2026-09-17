"""Long-lived managed companion process used by the local portal."""
import argparse
import json
import select
import signal
import sys
import time

from companion.cli import endpoint
from companion.client import BridgeError, Companion
from companion.rcon import RconClient, RconError


def emit(kind, **payload):
    print(json.dumps({"kind": kind, **payload}, separators=(",", ":")), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--player-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--password-file", type=argparse.FileType("r"))
    args = parser.parse_args(argv)
    stop = [False]

    def interrupted(*_):
        stop[0] = True

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    transport = None
    client = None
    try:
        if args.host:
            host, port = args.host, args.port or 27016
        else:
            class EndpointArgs:
                pass
            values = EndpointArgs()
            values.host, values.port = None, args.port
            host, port = endpoint(values)
        if args.password_file:
            password = args.password_file.read().strip()
        else:
            from companion.cli import ROOT
            password = (ROOT / "server/data/config/rconpw").read_text().strip()
        transport = RconClient(host, port, password)
        client = Companion(transport)
        state = client.request("spawn", name=args.name)
        client.acquire()
        emit("started", player_id=args.player_id, status=state)
        next_status = 0
        while not stop[0]:
            readable, _, _ = select.select([sys.stdin], [], [], 0.25)
            if readable:
                line = sys.stdin.readline()
                if not line or line.strip() == "stop":
                    break
            if time.monotonic() >= next_status:
                state = client.request("heartbeat")
                emit("status", status=state)
                next_status = time.monotonic() + 1
        emit("stopping", reason="requested")
        return 0
    except (OSError, ValueError, RconError, BridgeError) as error:
        emit("error", error=str(error))
        return 1
    finally:
        if client and client.session:
            try:
                client.release()
            except (OSError, ValueError, RconError, BridgeError):
                pass
        if transport:
            transport.close()


if __name__ == "__main__":
    sys.exit(main())
