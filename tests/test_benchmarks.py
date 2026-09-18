import tempfile
import unittest

from benchmarks.factory_smelting import placement_waste, scenario_spec
from benchmarks.runner import compare, markdown_report, summarize, write_reports


def successful_run(model, wall=10):
    return {
        "scenario": "factory-smelting-v1", "model": model, "success": True,
        "sustained_production": True, "wall_seconds": wall, "game_seconds": wall + 1,
        "decisions": 2, "failures": 1, "recoveries": 1, "rollbacks": 0,
        "prompt_tokens": 100, "output_tokens": 20, "inference_seconds": 4,
        "estimated_cost_usd": 0, "waste_items": 0, "human_interventions": 0,
    }


class BenchmarkTests(unittest.TestCase):
    def test_summary_aggregates_repeated_runs_by_model(self):
        rows = summarize([
            successful_run("alpha", 10), successful_run("alpha", 14),
            successful_run("beta", 20),
        ])
        alpha = next(row for row in rows if row["model"] == "alpha")
        self.assertEqual(alpha["runs"], 2)
        self.assertEqual(alpha["success_rate"], 1)
        self.assertEqual(alpha["mean_wall_seconds"], 12)
        self.assertEqual(alpha["total_prompt_tokens"], 200)

    def test_comparison_records_failure_and_continues(self):
        calls = []

        def fake(model, **_fields):
            calls.append(model)
            if model == "broken":
                raise RuntimeError("model unavailable")
            return successful_run(model)

        payload = compare(["good", "broken"], repeats=2, run_scenario=fake)
        self.assertEqual(calls, ["good", "broken", "good", "broken"])
        self.assertEqual(len(payload["runs"]), 4)
        broken = next(row for row in payload["summary"] if row["model"] == "broken")
        self.assertEqual(broken["successes"], 0)
        self.assertIn("model unavailable", payload["runs"][1]["error"])

    def test_reports_include_timing_policy_and_machine_data(self):
        spec = scenario_spec()
        payload = {
            "format": 1, "id": "example", "started_at": "2026-09-18T00:00:00+00:00",
            "ended_at": "2026-09-18T00:01:00+00:00", "scenario": spec,
            "models": ["alpha"], "repeats": 1,
            "runs": [successful_run("alpha")],
            "summary": summarize([successful_run("alpha")]),
        }
        report = markdown_report(payload)
        self.assertIn("## Timing policy", report)
        self.assertIn("| alpha | 1/1 (100%)", report)
        with tempfile.TemporaryDirectory() as directory:
            json_path, markdown_path = write_reports(directory, payload)
            self.assertTrue(json_path.is_file())
            self.assertEqual(markdown_path.read_text(), report)

    def test_waste_counts_only_missing_placement_assets(self):
        observation = {
            "self": {"inventory": {"stone-furnace:normal": 1}},
            "local_area": {"buildings": [{"name": "burner-mining-drill"}]},
        }
        self.assertEqual(placement_waste(observation), 0)
        observation["self"]["inventory"].clear()
        self.assertEqual(placement_waste(observation), 1)


if __name__ == "__main__":
    unittest.main()
