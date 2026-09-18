"""Long-lived managed companion process used by the local portal."""
import argparse
import json
import select
import signal
import sys
import time

from companion.cli import endpoint
from companion.agent import AgentRuntime, parse_goal
from companion.client import ActionInterrupted, BridgeError, Companion
from companion.models import ModelError, adapter_for
from companion.rcon import RconClient, RconError


def emit(kind, **payload):
    print(json.dumps({"kind": kind, **payload}, separators=(",", ":")), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--player-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--model", default="scripted")
    parser.add_argument("--instructions", default="")
    parser.add_argument("--ollama-endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--password-file", type=argparse.FileType("r"))
    args = parser.parse_args(argv)
    stop = [False]
    commands = []

    def interrupted(*_):
        stop[0] = True

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)

    def control():
        if stop[0]:
            return "stop"
        readable, _, _ = select.select([sys.stdin], [], [], 0)
        if readable:
            line = sys.stdin.readline()
            command = line.strip() if line else "stop"
            if command in {"stop", "pause", "resume"}:
                commands.append(command)
        return commands.pop(0) if commands else None
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
        if args.model != "scripted":
            runtime = AgentRuntime(
                client, adapter_for(args.model, args.ollama_endpoint), parse_goal(args.instructions),
                emit=lambda **event: emit("agent", **event),
            )
            while not stop[0]:
                try:
                    runtime.run(control=control)
                    state = client.request("status")
                    emit("status", status=state)
                    break
                except ActionInterrupted as interruption:
                    if interruption.command == "stop":
                        break
                    client.release()
                    runtime.phase = "paused"
                    runtime._send()
                    paused_at = time.monotonic()
                    while not stop[0]:
                        command = control()
                        if command == "stop":
                            stop[0] = True
                            break
                        if command == "resume":
                            runtime.started += time.monotonic() - paused_at
                            client.acquire()
                            runtime.phase = "observing"
                            runtime._send()
                            break
                        time.sleep(0.1)
            emit("stopping", reason="completed" if runtime.phase == "completed" else "requested")
            return 0

        next_status = 0
        while not stop[0]:
            command = control()
            if command == "stop":
                break
            time.sleep(0.25)
            if time.monotonic() >= next_status:
                state = client.request("heartbeat")
                emit("status", status=state)
                next_status = time.monotonic() + 1
        emit("stopping", reason="requested")
        return 0
    except (OSError, ValueError, RconError, BridgeError, ModelError) as error:
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
