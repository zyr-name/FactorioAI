#!/usr/bin/env python3
"""Compatibility entry point for the factory-smelting benchmark scenario."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.factory_smelting import run


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen3:8b"
    result = run(model, event_sink=lambda event: print(
        json.dumps(event, separators=(",", ":")), flush=True
    ))
    if not result["success"]:
        raise RuntimeError(result.get("error", "Factory benchmark failed."))
    summary = {
        "model": result["model"], "designs": result["decisions"],
        "failures": result["failures"], "recoveries": result["recoveries"],
        "wall_seconds": result["wall_seconds"], "product": result["product"],
        "first_count": result["first_count"], "second_count": result["second_count"],
        "increase": result["increase"], "sample_seconds": result["sample_seconds"],
    }
    print("PASS: " + json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
