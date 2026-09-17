"""Bring up the complete local FactorioAI stack with one command."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from portal.app import ROOT, serve


def run(*arguments, timeout=300):
    result = subprocess.run(
        [str(ROOT / "bin/factorio"), *arguments], cwd=str(ROOT), text=True,
        capture_output=True, timeout=timeout,
    )
    if result.returncode:
        raise ValueError((result.stdout + result.stderr).strip() or "Operation failed.")


def companion_installed():
    info = json.loads((ROOT / "mods/factorio-ai-companion/info.json").read_text())
    archive = ROOT / "server/data/mods" / (info["name"] + "_" + info["version"] + ".zip")
    listing = ROOT / "server/data/mods/mod-list.json"
    if not archive.exists() or not listing.exists():
        return False
    mods = json.loads(listing.read_text()).get("mods", [])
    return any(mod.get("name") == info["name"] and mod.get("enabled") for mod in mods)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-server", action="store_true", help="Start only the portal")
    args = parser.parse_args(argv)
    if args.host != "127.0.0.1":
        parser.error("Only local portal binding is supported until authentication is configured.")
    startup_error = None
    if not args.no_server:
        try:
            run("setup", timeout=30)
            if not companion_installed():
                run("install-companion")
            run("start")
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            startup_error = str(error)
    try:
        serve(args.host, args.port, startup_error=startup_error)
    except KeyboardInterrupt:
        print("\nPortal stopped. The Factorio server keeps its current state.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
