import json
import time
import unittest
from unittest.mock import patch

from companion.agent import AgentRuntime, parse_goal
from companion.client import ActionInterrupted
from companion.models import ModelError, OllamaAdapter, SequenceAdapter, validate_decision


class FakeCompanion:
    def __init__(self):
        self.inventory = {}
        self.calls = []

    def request(self, action, **fields):
        self.calls.append((action, fields))
        if action == "observe":
            return {"self": {"inventory": dict(self.inventory)}, "tasks": [], "local_area": {}}
        if action == "transfer":
            self.inventory[fields["item"] + ":normal"] = fields["count"]
        return {"inventory": dict(self.inventory)}


class AdapterTests(unittest.TestCase):
    def test_decisions_are_strictly_validated(self):
        decision = validate_decision({
            "reason": "Collect the output", "action": "transfer",
            "arguments": {"direction": "from", "inventory": "result", "item": "iron-plate",
                          "count": 20, "x": 1.5, "y": 2.5},
        })
        self.assertEqual(decision.action, "transfer")
        normalized = validate_decision({
            "reason": "Use an observed stack", "action": "transfer",
            "arguments": {"direction": "to", "inventory": "source", "item": "iron-ore:normal",
                          "count": 20, "x": 1.5, "y": 2.5},
        })
        self.assertEqual(normalized.arguments["item"], "iron-ore")
        with self.assertRaises(ModelError):
            validate_decision({"reason": "unsafe", "action": "console", "arguments": {}})
        with self.assertRaises(ModelError):
            validate_decision({"reason": "too long", "action": "wait", "arguments": {"seconds": 60}})

    @patch("companion.models.urllib.request.urlopen")
    def test_ollama_uses_schema_and_returns_metrics(self, urlopen):
        response = {
            "model": "qwen3:8b", "message": {"content": json.dumps({
                "reason": "Let the furnace work", "action": "wait", "arguments": {"seconds": 1},
            })}, "prompt_eval_count": 123, "eval_count": 18,
        }
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(response).encode()
        result = OllamaAdapter("qwen3:8b").decide({"objective": {"item": "iron-plate"}})
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["format"]["required"], ["reason", "action", "arguments"])
        self.assertFalse(payload["stream"])
        self.assertEqual(result.metrics["prompt_tokens"], 123)
        self.assertEqual(result.decision.action, "wait")


class RuntimeTests(unittest.TestCase):
    def test_goal_parser_normalizes_plural_item(self):
        goal = parse_goal("Produce 20 iron plates from available supplies.")
        self.assertEqual((goal.item, goal.count), ("iron-plate", 20))

    def test_runtime_recovers_and_verifies_inventory(self):
        client = FakeCompanion()
        adapter = SequenceAdapter([
            {"reason": "I think it is done", "action": "finish", "arguments": {}},
            {"reason": "Collect plates", "action": "transfer", "arguments": {
                "direction": "from", "inventory": "result", "item": "iron-plate",
                "count": 20, "x": 0, "y": 0,
            }},
        ])
        events = []
        runtime = AgentRuntime(client, adapter, parse_goal("Produce 20 iron plates"),
                               emit=lambda **value: events.append(value))
        result = runtime.run()
        self.assertEqual(result["self"]["inventory"]["iron-plate:normal"], 20)
        self.assertEqual((runtime.decisions, runtime.failures, runtime.recoveries), (2, 1, 1))
        self.assertEqual(events[-1]["phase"], "completed")

    def test_runtime_stops_at_decision_limit(self):
        runtime = AgentRuntime(
            FakeCompanion(),
            SequenceAdapter([{"reason": "try a machine", "action": "rotate", "arguments": {"x": 0, "y": 0}}]),
            parse_goal("Produce 1 iron plate"), max_decisions=1,
        )
        with self.assertRaisesRegex(ModelError, "Decision limit"):
            runtime.run()

    def test_lease_is_kept_alive_during_slow_inference(self):
        class SlowAdapter:
            def decide(self, context):
                time.sleep(1.1)
                return SequenceAdapter([{
                    "reason": "collect", "action": "transfer", "arguments": {
                        "direction": "from", "inventory": "result", "item": "iron-plate",
                        "count": 1, "x": 0, "y": 0,
                    },
                }]).decide(context)

        client = FakeCompanion()
        AgentRuntime(client, SlowAdapter(), parse_goal("Produce 1 iron plate")).run()
        self.assertIn(("heartbeat", {}), client.calls)

    def test_pause_after_inference_prevents_selected_action(self):
        client = FakeCompanion()
        runtime = AgentRuntime(client, SequenceAdapter([{
            "reason": "collect", "action": "transfer", "arguments": {
                "direction": "from", "inventory": "result", "item": "iron-plate",
                "count": 1, "x": 0, "y": 0,
            },
        }]), parse_goal("Produce 1 iron plate"))
        commands = iter([None, "pause"])
        with self.assertRaises(ActionInterrupted):
            runtime.run(control=lambda: next(commands, None))
        self.assertNotIn("iron-plate:normal", client.inventory)


if __name__ == "__main__":
    unittest.main()
