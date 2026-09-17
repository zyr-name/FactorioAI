#!/usr/bin/env python3
"""Local Factorio operations. Python standard library + Docker Compose only."""
import argparse
import contextlib
import datetime
import fcntl
import io
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
import zipfile

SERVER = Path(__file__).resolve().parent
DATA = SERVER / "data"
BACKUPS = SERVER / "backups"
ENV_FILE = SERVER / ".env"


def write_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2) + "\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def setup():
    if not ENV_FILE.exists():
        text = (SERVER / ".env.example").read_text()
        text = text.replace("PUID=1000", "PUID=" + str(os.getuid()))
        text = text.replace("PGID=1000", "PGID=" + str(os.getgid()))
        ENV_FILE.write_text(text)
        ENV_FILE.chmod(0o600)
    for directory in (DATA / "config", DATA / "saves", DATA / "mods", BACKUPS):
        directory.mkdir(parents=True, exist_ok=True)
    for source in (SERVER / "defaults").glob("*.json"):
        target = DATA / "config" / source.name
        if not target.exists():
            value = json.loads(source.read_text())
            if source.name == "server-settings.json":
                value["game_password"] = secrets.token_urlsafe(18)
            write_json(target, value)
            target.chmod(0o600)
    rcon = DATA / "config" / "rconpw"
    if not rcon.exists():
        rcon.write_text(secrets.token_urlsafe(24) + "\n")
        rcon.chmod(0o600)
    print("Setup ready: " + str(SERVER), flush=True)


def compose(*args, capture=False):
    if not ENV_FILE.exists():
        raise ValueError("Run bin/setup first.")
    # .env is the single source of instance configuration. Do not let shell
    # COMPOSE_FILE/PROJECT_NAME or image/port overrides target another instance.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("COMPOSE_") and key not in {
               "FACTORIO_VERSION", "FACTORIO_BIND", "FACTORIO_PORT",
               "DLC_SPACE_AGE", "PUID", "PGID"}}
    return subprocess.run(
        ["docker", "compose", "--project-directory", str(SERVER),
         "--env-file", str(ENV_FILE), "-f", str(SERVER / "compose.yaml"), *args],
        env=env, check=True, text=True,
        stdout=subprocess.PIPE if capture else None,
    ).stdout


def config():
    return json.loads(compose("config", "--format", "json", capture=True))["services"]["factorio"]


def validate():
    for path in (DATA / "config").glob("*.json"):
        json.loads(path.read_text())
    return config()


def active():
    # Include restarting/paused containers, which must also be stopped before
    # touching data. A failed Docker query must never be mistaken for 'stopped'.
    output = compose("ps", "--all", "--format", "json", "factorio", capture=True).strip()
    rows = json.loads(output) if output.startswith("[") else [json.loads(line) for line in output.splitlines()]
    return any(row.get("State") in {"running", "restarting", "paused"} for row in rows)


def start():
    validate()
    compose("up", "-d", "--wait", "--wait-timeout", "240", "factorio")


@contextlib.contextmanager
def stopped():
    was_active = active()
    if was_active:
        compose("stop", "factorio")
    try:
        yield
    finally:
        if was_active:
            start()


def snapshot(label):
    image, dlc = runtime_version()
    metadata = {"format": 1, "image": image,
                "dlc_space_age": dlc,
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    BACKUPS.mkdir(exist_ok=True)
    target = BACKUPS / (stamp + "-" + label + ".tar.gz")
    partial = target.with_suffix(".partial")
    try:
        with tarfile.open(partial, "w:gz") as archive:
            archive.add(DATA, arcname="data", filter=backup_member)
            payload = json.dumps(metadata, indent=2).encode()
            info = tarfile.TarInfo("metadata.json")
            info.size = len(payload)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(payload))
        partial.chmod(0o600)
        partial.rename(target)
    finally:
        partial.unlink(missing_ok=True)
    print("Backup: " + str(target), flush=True)
    return target


def runtime_version():
    # .env may already have been edited for an upgrade. Record the image that
    # last used this data, when its container is still available.
    container = compose("ps", "--all", "--quiet", "factorio", capture=True).strip()
    if container:
        details = json.loads(subprocess.run(
            ["docker", "inspect", "--format", "{{json .Config}}", container],
            check=True, capture_output=True, text=True).stdout)
        environment = dict(item.split("=", 1) for item in details["Env"])
        return details["Image"], environment.get("DLC_SPACE_AGE", "false")
    service = config()
    return service["image"], service["environment"]["DLC_SPACE_AGE"]


def backup_member(member):
    if member.name in {"data/temp", "data/.lock"}:
        return None
    if not (member.isfile() or member.isdir()):
        raise ValueError("Runtime data contains a link or special file: " + member.name)
    return member


def unpack_backup(path, destination):
    """Validate everything before extracting; reject links and special files."""
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        seen = set()
        for member in members:
            name = PurePosixPath(member.name)
            if (name.is_absolute() or ".." in name.parts or not name.parts
                    or name.parts[0] not in {"data", "metadata.json"}
                    or (name.parts[0] == "metadata.json" and member.name != "metadata.json")
                    or (member.name == "metadata.json" and not member.isfile())
                    or not (member.isfile() or member.isdir())
                    or str(name) in seen):
                raise ValueError("Unsafe or unsupported backup member: " + member.name)
            seen.add(str(name))
        if "metadata.json" not in seen or "data/config/server-settings.json" not in seen:
            raise ValueError("Not a backup created by bin/backup (missing metadata or settings).")
        metadata = json.load(archive.extractfile("metadata.json"))
        service = config()
        if not isinstance(metadata, dict) or metadata.get("format") != 1:
            raise ValueError("Unsupported backup format.")
        if (metadata.get("image") != service["image"]
                or metadata.get("dlc_space_age") != service["environment"]["DLC_SPACE_AGE"]):
            raise ValueError("Backup requires image " + str(metadata.get("image"))
                             + " and DLC_SPACE_AGE=" + str(metadata.get("dlc_space_age"))
                             + ". Set server/.env to match, then retry.")
        # Manual extraction avoids archive ownership, setuid bits and symlinks.
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o600)
    for path in (destination / "data" / "config").glob("*.json"):
        json.loads(path.read_text())


def replace_directory(target, replacement, staging):
    previous = staging / ("previous-" + uuid.uuid4().hex)
    target.rename(previous)
    try:
        replacement.rename(target)
    except BaseException:
        previous.rename(target)
        raise


def reset(seed):
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=SERVER) as temp:
        staging = Path(temp)
        replacement = staging / "saves"
        replacement.mkdir()
        with stopped():
            snapshot("before-reset")
            if seed is not None:
                path = DATA / "config" / "map-gen-settings.json"
                settings = json.loads(path.read_text())
                settings["seed"] = None if seed == "random" else seed
                write_json(path, settings)
            replace_directory(DATA / "saves", replacement, staging)
    print("World reset. A new world is generated on start; settings and mods are kept.")


def restore(path):
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=SERVER) as temp:
        staging = Path(temp)
        unpack_backup(path, staging)  # Reject invalid input before downtime.
        with stopped():
            snapshot("before-restore")
            replace_directory(DATA, staging / "data", staging)
    print("Restored: " + str(path))


def load_save(path):
    with zipfile.ZipFile(path) as archive:
        if not any(name.endswith(("/level.dat", "/level.dat0")) for name in archive.namelist()):
            raise ValueError("ZIP does not look like a Factorio world save.")
        bad = archive.testzip()
        if bad:
            raise ValueError("Corrupt save member: " + bad)
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=SERVER) as temp:
        staging = Path(temp)
        replacement = staging / "saves"
        replacement.mkdir()
        shutil.copyfile(path, replacement / "world.zip")
        with stopped():
            snapshot("before-load-save")
            replace_directory(DATA / "saves", replacement, staging)
    print("Loaded world: " + str(path))


def parse_seed(value):
    if value == "random":
        return value
    try:
        seed = int(value)
        if 0 <= seed <= 4294967295:
            return seed
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("Seed must be 0..4294967295 or 'random'.")


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    for name, help_text in {
        "setup": "Create local settings and random passwords; keep existing settings",
        "start": "Start and wait for RCON health",
        "stop": "Gracefully stop, keeping the world",
        "restart": "Stop/start and apply configuration changes",
        "status": "Show container and health status",
        "backup": "Back up all data, briefly stopping a running server",
        "backups": "List backup archives",
        "password": "Print the game password",
        "pull": "Download the configured image without restarting",
        "down": "Remove this instance's container/network, keeping data",
        "validate": "Validate Compose and JSON configuration",
    }.items():
        commands.add_parser(name, help=help_text)
    log = commands.add_parser("logs", help="Show recent logs")
    log.add_argument("-f", "--follow", action="store_true")
    log.add_argument("--tail", type=int, default=100)
    new = commands.add_parser("reset", help="Back up and clear saves, keeping config/mods")
    new.add_argument("--seed", type=parse_seed)
    for name in ("restore", "load-save"):
        commands.add_parser(name, help=("Restore a complete runtime backup" if name == "restore"
                                        else "Switch to a Factorio world ZIP")).add_argument("file", type=Path)
    commands.add_parser("rcon", help="Send a console command").add_argument("text", nargs="+")
    return root


def main():
    args = parser().parse_args()
    os.umask(0o077)
    # Long-lived log followers must not prevent operational commands.
    if args.command in {"logs", "status", "backups", "password"}:
        dispatch(args)
        return
    with (SERVER / ".operations.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another operation is in progress. Retry when it finishes.")
        dispatch(args)


def dispatch(args):
    command = args.command
    if command == "setup":
        setup()
    elif command == "start":
        start()
    elif command == "stop":
        compose("stop", "factorio")
    elif command == "restart":
        validate()
        compose("stop", "factorio")
        start()
    elif command == "status":
        compose("ps", "--all")
    elif command == "logs":
        compose("logs", "--tail", str(args.tail), *(["--follow"] if args.follow else []), "factorio")
    elif command == "validate":
        validate()
        print("Compose and runtime JSON are valid.")
    elif command == "backup":
        with stopped():
            snapshot("manual")
    elif command == "backups":
        for path in sorted(BACKUPS.glob("*.tar.gz")):
            print(str(path) + " (" + str(path.stat().st_size) + " bytes)")
    elif command == "password":
        print(json.loads((DATA / "config" / "server-settings.json").read_text())["game_password"])
    elif command == "restore":
        restore(args.file.resolve())
    elif command == "reset":
        reset(args.seed)
    elif command == "load-save":
        load_save(args.file.resolve())
    elif command == "rcon":
        compose("exec", "-T", "factorio", "rcon", " ".join(args.text))
    elif command == "pull":
        compose("pull", "factorio")
    elif command == "down":
        compose("down")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, tarfile.TarError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        print("Error: " + str(error), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
