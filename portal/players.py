"""Supervise AI-player worker processes and persist their activity."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[1]


class PlayerManager:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.processes = {}
        self.states = {}

    def start(self, player_id):
        with self.lock:
            if any(process.poll() is None for process, _run in self.processes.values()):
                if player_id in self.processes and self.processes[player_id][0].poll() is None:
                    return self.status(player_id)
                raise ValueError("The current game bridge supports one active AI player at a time.")
            player = self.store.player(player_id)
            if not player:
                raise ValueError("Unknown AI player.")
            run_id = self.store.start_run(player_id)
            command = [
                sys.executable, "-m", "companion.worker", "--player-id", player_id, "--name", player["name"]
            ]
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT) + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
            try:
                process = subprocess.Popen(
                    command, cwd=str(ROOT), env=environment, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                )
            except BaseException:
                self.store.finish_run(run_id, "failed", "Could not start worker process.")
                raise
            self.processes[player_id] = (process, run_id)
            self.states[player_id] = {"state": "starting", "pid": process.pid, "game": None, "error": None}
            threading.Thread(target=self._read, args=(player_id, process, run_id), daemon=True).start()
            return self.status(player_id)

    def _read(self, player_id, process, run_id):
        error = None
        last_status_event = 0
        for line in process.stdout:
            try:
                event = json.loads(line)
                kind = event.pop("kind")
            except (ValueError, KeyError):
                kind, event = "output", {"text": line.rstrip()}
            now = time.monotonic()
            if kind != "status" or now - last_status_event >= 30:
                self.store.event(run_id, kind, event)
                if kind == "status":
                    last_status_event = now
            with self.lock:
                state = self.states.setdefault(player_id, {})
                if kind in {"started", "status"}:
                    state.update(state="running", game=event.get("status"), error=None)
                elif kind == "error":
                    error = event.get("error", "Worker failed.")
                    state.update(state="failed", error=error)
                elif kind == "stopping":
                    state["state"] = "stopping"
        return_code = process.wait()
        with self.lock:
            requested = self.states.get(player_id, {}).get("requested_stop", False)
            outcome = "stopped" if requested and return_code == 0 else "failed" if return_code else "stopped"
            self.store.finish_run(run_id, outcome, error)
            self.states[player_id] = {
                "state": "stopped" if outcome == "stopped" else "failed",
                "pid": None, "game": self.states.get(player_id, {}).get("game"), "error": error,
            }
            current = self.processes.get(player_id)
            if current and current[0] is process:
                del self.processes[player_id]

    def stop(self, player_id, timeout=8):
        with self.lock:
            current = self.processes.get(player_id)
            if not current or current[0].poll() is not None:
                return self.status(player_id)
            process = current[0]
            self.states[player_id]["requested_stop"] = True
            try:
                process.stdin.write("stop\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        return self.status(player_id)

    def status(self, player_id):
        with self.lock:
            return dict(self.states.get(player_id, {"state": "stopped", "pid": None, "game": None, "error": None}))

    def all(self):
        result = []
        for player in self.store.players():
            player["auto_start"] = bool(player["auto_start"])
            player["runtime"] = self.status(player["id"])
            player["statistics"] = self.store.player_summary(player["id"])
            result.append(player)
        return result

    def auto_start(self):
        for player in self.store.players():
            if player["auto_start"]:
                try:
                    self.start(player["id"])
                except (OSError, ValueError):
                    pass

    def close(self):
        with self.lock:
            player_ids = list(self.processes)
        for player_id in player_ids:
            self.stop(player_id)
