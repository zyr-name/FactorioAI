"""Command-line runner and report writer for repeatable model comparisons."""
import argparse
import datetime
import json
from pathlib import Path
import statistics
import uuid

from benchmarks.factory_smelting import run as run_factory_smelting
from benchmarks.factory_smelting import scenario_spec


NUMERIC_METRICS = (
    "wall_seconds", "game_seconds", "decisions", "failures", "recoveries",
    "rollbacks", "prompt_tokens", "output_tokens", "inference_seconds",
    "estimated_cost_usd", "waste_items", "human_interventions",
)


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def failed_record(model, spec, error, started_at, ended_at):
    value = {
        "scenario": spec["id"], "model": model.removeprefix("ollama:"), "success": False,
        "started_at": started_at, "ended_at": ended_at, "error": str(error),
        "sustained_production": False,
    }
    value.update({metric: None for metric in NUMERIC_METRICS})
    return value


def summarize(runs):
    grouped = {}
    for run in runs:
        grouped.setdefault(run["model"], []).append(run)
    summaries = []
    for model in sorted(grouped):
        values = grouped[model]
        row = {
            "model": model, "runs": len(values),
            "successes": sum(value["success"] for value in values),
            "success_rate": sum(value["success"] for value in values) / len(values),
            "sustained_runs": sum(value.get("sustained_production") is True for value in values),
        }
        for metric in NUMERIC_METRICS:
            present = [value[metric] for value in values if value.get(metric) is not None]
            row["total_" + metric] = round(sum(present), 3) if present else None
            row["mean_" + metric] = round(statistics.fmean(present), 3) if present else None
        summaries.append(row)
    return summaries


def markdown_report(payload):
    spec = payload["scenario"]
    lines = [
        "# Model comparison: " + spec["name"], "",
        f"Experiment `{payload['id']}` started `{payload['started_at']}`.", "",
        "## Timing policy", "", spec["timing_policy"], "",
        "## Aggregate comparison", "",
        "| Model | Success | Sustained | Wall s | Game s | Decisions | Failures | Recoveries | Rollbacks | Prompt tok | Output tok | Cost USD | Waste | Human |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["summary"]:
        mean = lambda key: "—" if row.get("mean_" + key) is None else str(row["mean_" + key])
        lines.append(
            f"| {row['model']} | {row['successes']}/{row['runs']} "
            f"({row['success_rate']:.0%}) | {row['sustained_runs']}/{row['runs']} | "
            f"{mean('wall_seconds')} | {mean('game_seconds')} | {mean('decisions')} | "
            f"{mean('failures')} | {mean('recoveries')} | {mean('rollbacks')} | {mean('prompt_tokens')} | "
            f"{mean('output_tokens')} | {mean('estimated_cost_usd')} | "
            f"{mean('waste_items')} | {mean('human_interventions')} |"
        )
    lines.extend(["", "## Individual runs", ""])
    for index, run in enumerate(payload["runs"], 1):
        outcome = "PASS" if run["success"] else "FAIL"
        display = lambda key, suffix="": "—" if run.get(key) is None else str(run[key]) + suffix
        details = (f"wall={display('wall_seconds', 's')}, game={display('game_seconds', 's')}, "
                   f"decisions={display('decisions')}, failures={display('failures')}, "
                   f"recoveries={display('recoveries')}")
        if not run["success"]:
            details += ", error=" + run.get("error", "unknown")
        lines.append(f"{index}. **{outcome}** `{run['model']}` — {details}")
    lines.extend(["", "## Metric notes", "", spec["waste_policy"], ""])
    return "\n".join(lines)


def write_reports(output_dir, payload):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base = output_dir / payload["id"]
    json_path, markdown_path = base.with_suffix(".json"), base.with_suffix(".md")
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    markdown_path.write_text(markdown_report(payload))
    return json_path, markdown_path


def compare(models, repeats=1, sample_seconds=None, event_sink=None, run_scenario=run_factory_smelting):
    spec = scenario_spec()
    started = utc_now()
    runs = []
    for repeat in range(1, repeats + 1):
        for model in models:
            run_started = utc_now().isoformat()
            try:
                value = run_scenario(model, sample_seconds=sample_seconds, event_sink=event_sink)
            except Exception as error:  # Each model must leave a comparable record and allow the matrix to continue.
                value = failed_record(model, spec, error, run_started, utc_now().isoformat())
            value["repeat"] = repeat
            runs.append(value)
    identifier = started.strftime("%Y%m%dT%H%M%SZ") + "-" + spec["id"] + "-" + uuid.uuid4().hex[:8]
    return {
        "format": 1, "id": identifier, "started_at": started.isoformat(),
        "ended_at": utc_now().isoformat(), "scenario": spec,
        "models": [value.removeprefix("ollama:") for value in models],
        "repeats": repeats, "runs": runs, "summary": summarize(runs),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay a fixed Factorio challenge across models.")
    parser.add_argument("--scenario", default="factory-smelting", choices=["factory-smelting"])
    parser.add_argument("--model", action="append", dest="models",
                        help="Model name, ollama:model, or sequence; repeat for comparisons.")
    parser.add_argument("--repeat", type=int, default=1, help="Runs per model (default: 1).")
    parser.add_argument("--sample-seconds", type=float,
                        help="Override equal production sample intervals (default: scenario value).")
    parser.add_argument("--output-dir", default="benchmark-results")
    parser.add_argument("--events", action="store_true", help="Print detailed runtime events.")
    args = parser.parse_args(argv)
    if not 1 <= args.repeat <= 100:
        parser.error("--repeat must be between 1 and 100")
    if args.sample_seconds is not None and not 1 <= args.sample_seconds <= 60:
        parser.error("--sample-seconds must be between 1 and 60")
    models = args.models or ["qwen3:8b"]

    def event_sink(event):
        if args.events:
            print(json.dumps(event, separators=(",", ":")), flush=True)

    print(f"Running {args.scenario}: {len(models)} model(s) x {args.repeat} repeat(s)", flush=True)
    payload = compare(models, repeats=args.repeat, sample_seconds=args.sample_seconds,
                      event_sink=event_sink)
    json_path, markdown_path = write_reports(args.output_dir, payload)
    for row in payload["summary"]:
        print(f"{row['model']}: {row['successes']}/{row['runs']} passed", flush=True)
    print("JSON: " + str(json_path.resolve()), flush=True)
    print("Report: " + str(markdown_path.resolve()), flush=True)
    return 0 if all(value["success"] for value in payload["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
