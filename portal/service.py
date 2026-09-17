"""Server operations exposed safely to the local portal."""
import json
from pathlib import Path
import subprocess
import threading

from server import manage


ROOT = Path(__file__).resolve().parents[1]


class ServerService:
    def __init__(self):
        self.lock = threading.Lock()

    def command(self, *arguments, timeout=300):
        with self.lock:
            result = subprocess.run(
                [str(ROOT / "bin/factorio"), *arguments], cwd=str(ROOT), text=True,
                capture_output=True, timeout=timeout,
            )
        output = (result.stdout + result.stderr).strip()
        if result.returncode:
            raise ValueError(output or "Operation failed.")
        return output

    def status(self):
        try:
            output = manage.compose("ps", "--all", "--format", "json", "factorio", capture=True).strip()
            rows = json.loads(output) if output.startswith("[") else [json.loads(line) for line in output.splitlines()]
            row = rows[0] if rows else {}
            return {
                "state": row.get("State", "stopped"),
                "health": row.get("Health", ""),
                "status": row.get("Status", "Not created"),
                "name": row.get("Name", "factorio"),
            }
        except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
            return {"state": "unavailable", "health": "", "status": str(error), "name": "factorio"}

    def backups(self):
        manage.BACKUPS.mkdir(exist_ok=True)
        return [
            {"name": path.name, "size": path.stat().st_size,
             "created_at": path.stat().st_mtime}
            for path in sorted(manage.BACKUPS.glob("*.tar.gz"), reverse=True)
        ]

    def logs(self, tail=120):
        tail = max(10, min(int(tail), 500))
        try:
            return self.command("logs", "--tail", str(tail), timeout=30)
        except ValueError as error:
            return str(error)

    def configuration(self):
        environment = {}
        if manage.ENV_FILE.exists():
            for line in manage.ENV_FILE.read_text().splitlines():
                if line and not line.lstrip().startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    environment[key] = value
        for key, value in {
            "FACTORIO_VERSION": "2.0.77",
            "FACTORIO_BIND": "127.0.0.1",
            "FACTORIO_PORT": "34198",
            "FACTORIO_RCON_PORT": "27016",
            "DLC_SPACE_AGE": "false",
        }.items():
            environment.setdefault(key, value)
        settings_path = manage.DATA / "config/server-settings.json"
        settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
        settings.pop("game_password", None)
        return {"environment": environment, "server_settings": settings}

    def update_configuration(self, payload):
        allowed_environment = {
            "FACTORIO_VERSION", "FACTORIO_BIND", "FACTORIO_PORT", "FACTORIO_RCON_PORT", "DLC_SPACE_AGE"
        }
        environment = payload.get("environment", {})
        unknown = set(environment) - allowed_environment
        if unknown:
            raise ValueError("Unsupported environment setting: " + sorted(unknown)[0])
        if "FACTORIO_BIND" in environment and environment["FACTORIO_BIND"] != "127.0.0.1":
            raise ValueError("The local portal currently requires FACTORIO_BIND=127.0.0.1.")
        for key in ("FACTORIO_PORT", "FACTORIO_RCON_PORT"):
            if key in environment and not 1 <= int(environment[key]) <= 65535:
                raise ValueError(key + " must be a valid port.")
        original_environment = manage.ENV_FILE.read_bytes()
        settings_path = manage.DATA / "config/server-settings.json"
        original_settings = settings_path.read_bytes()
        with self.lock:
            lines = manage.ENV_FILE.read_text().splitlines()
            seen = set()
            updated = []
            for line in lines:
                if "=" in line and not line.lstrip().startswith("#"):
                    key = line.split("=", 1)[0]
                    if key in environment:
                        line = key + "=" + str(environment[key])
                        seen.add(key)
                updated.append(line)
            for key in environment:
                if key not in seen:
                    updated.append(key + "=" + str(environment[key]))
            manage.ENV_FILE.write_text("\n".join(updated) + "\n")
            settings = payload.get("server_settings")
            if settings is not None:
                if not isinstance(settings, dict):
                    raise ValueError("Server settings must be a JSON object.")
                current = json.loads(settings_path.read_text())
                password = current.get("game_password", "")
                settings["game_password"] = password
                manage.write_json(settings_path, settings)
        try:
            self.command("validate", timeout=30)
        except BaseException:
            with self.lock:
                manage.ENV_FILE.write_bytes(original_environment)
                settings_path.write_bytes(original_settings)
            raise
        return self.configuration()

    def action(self, action, payload):
        if action in {"start", "stop", "restart", "backup", "install-companion"}:
            return self.command(action)
        if action == "reset":
            seed = payload.get("seed")
            arguments = ["reset"] + (["--seed", str(seed)] if seed not in (None, "") else [])
            return self.command(*arguments)
        if action == "restore":
            supplied = str(payload.get("backup", ""))
            name = Path(supplied).name
            path = manage.BACKUPS / name
            if supplied != name or not name or not path.is_file() or path.suffixes[-2:] != [".tar", ".gz"]:
                raise ValueError("Choose an existing backup.")
            return self.command("restore", str(path))
        raise ValueError("Unknown server action.")
