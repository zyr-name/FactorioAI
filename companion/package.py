"""Build a deterministic mod ZIP usable by both server and game clients."""
import json
from pathlib import Path
import zipfile

SOURCE = Path(__file__).resolve().parents[1] / "mods" / "factorio-ai-companion"


def build(destination):
    info = json.loads((SOURCE / "info.json").read_text())
    folder = info["name"] + "_" + info["version"]
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(SOURCE.rglob("*")):
            if source.is_file():
                entry = zipfile.ZipInfo(folder + "/" + source.relative_to(SOURCE).as_posix())
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, source.read_bytes())
    return destination
