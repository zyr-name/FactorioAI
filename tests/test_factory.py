import unittest

from companion.client import ActionInterrupted, BridgeError
from companion.factory import FactoryRuntime, is_factory_goal
from companion.models import ModelError, SequencePlanAdapter, validate_factory_plan


def plan(direction, furnace_x, furnace_y):
    return {
        "name": "Direct iron smelter",
        "reason": "A burner drill can feed a neighboring furnace continuously.",
        "placements": [
            {"id": "drill", "item": "burner-mining-drill", "x": 0, "y": 0,
             "direction": direction},
            {"id": "furnace", "item": "stone-furnace", "x": furnace_x, "y": furnace_y,
             "direction": 0},
        ],
        "connections": [{"from": "drill", "to": "furnace", "kind": "direct-output"}],
        "supplies": [
            {"entity": "drill", "inventory": "fuel", "item": "coal", "count": 1},
            {"entity": "furnace", "inventory": "fuel", "item": "coal", "count": 1},
        ],
    }


class FakeFactoryClient:
    def __init__(self):
        self.inventory = {
            "burner-mining-drill:normal": 1, "stone-furnace:normal": 1, "coal:normal": 4,
        }
        self.placed = {}
        self.fueled = set()
        self.plates = 0
        self.validation_calls = 0

    def request(self, action, **fields):
        if action in {"heartbeat", "status"}:
            return {}
        if action == "validate_plan":
            if len(fields["placements"]) == 1:
                return {"valid": True}
            self.validation_calls += 1
            if fields["placements"][0]["direction"] == 0:
                raise BridgeError("cannot_place", "Placement furnace is blocked.")
            return {"valid": True}
        if action == "place":
            self.placed[fields["item"]] = dict(fields)
            return {}
        if action == "transfer":
            self.fueled.add(fields["name"])
            return {}
        if action == "observe":
            if len(self.fueled) == 2:
                self.plates += 1
            buildings = []
            if "stone-furnace" in self.placed:
                furnace = self.placed["stone-furnace"]
                buildings.append({
                    "name": "stone-furnace", "position": {"x": furnace["x"], "y": furnace["y"]},
                    "inventories": {"result": {"iron-plate:normal": self.plates}},
                })
            if "burner-mining-drill" in self.placed:
                drill = self.placed["burner-mining-drill"]
                furnace = self.placed.get("stone-furnace", {"x": 2, "y": 0})
                buildings.append({
                    "name": "burner-mining-drill", "position": {"x": drill["x"], "y": drill["y"]},
                    "drop_position": {"x": drill["x"] + 1.3, "y": drill["y"] - 0.5},
                    "drop_target": {"name": "stone-furnace",
                                    "position": {"x": furnace["x"], "y": furnace["y"]}},
                    "mining_target": {"name": "iron-ore", "position": {"x": 0.5, "y": 0.5}},
                })
            return {
                "self": {"position": {"x": 5, "y": 0}, "inventory": dict(self.inventory)},
                "local_area": {
                    "resources": [{"name": "iron-ore", "count": 16, "amount": 16000,
                                   "nearest": {"x": 0.5, "y": 0.5}}],
                    "buildings": buildings,
                },
            }
        raise AssertionError("Unexpected action " + action)

    def mine(self, x, y, name=None, **_fields):
        self.placed.pop(name, None)
        return {}


class FactoryPlanTests(unittest.TestCase):
    def test_plan_schema_and_goal_detection(self):
        value = validate_factory_plan(plan(4, 2, 0))
        self.assertEqual(value.placements[0]["item"], "burner-mining-drill")
        self.assertTrue(is_factory_goal("Build a sustainable smelting setup"))
        self.assertFalse(is_factory_goal("Produce 20 iron plates"))
        broken = plan(4, 2, 0)
        broken["connections"][0]["to"] = "missing"
        with self.assertRaises(ModelError):
            validate_factory_plan(broken)

    def test_runtime_replans_after_preflight_failure_and_verifies_growth(self):
        client = FakeFactoryClient()
        adapter = SequencePlanAdapter([plan(0, 0, -2), plan(4, 2, 0)])
        events = []
        runtime = FactoryRuntime(
            client, adapter, "Build a sustainable smelting setup",
            emit=lambda **event: events.append(event), sample_seconds=0.01,
        )
        result = runtime.run()
        self.assertGreater(result["second_count"], result["first_count"])
        self.assertEqual((runtime.designs, runtime.failures, runtime.recoveries), (2, 1, 1))
        self.assertEqual(client.validation_calls, 2)
        self.assertEqual(events[-1]["phase"], "completed")

    def test_geometry_rejects_disconnected_furnace(self):
        client = FakeFactoryClient()
        observation = client.request("observe")
        constraints = FactoryRuntime._constraints(observation)
        with self.assertRaisesRegex(ModelError, "drill output"):
            FactoryRuntime._validate(validate_factory_plan(plan(4, 5, 0)), observation, constraints)

    def test_pause_rolls_back_incomplete_factory(self):
        client = FakeFactoryClient()
        runtime = FactoryRuntime(
            client, SequencePlanAdapter([plan(4, 2, 0)]),
            "Build a sustainable smelting setup", sample_seconds=0.01,
        )

        def control():
            return "pause" if len(client.placed) == 2 else None

        with self.assertRaises(ActionInterrupted) as raised:
            runtime.run(control=control)
        self.assertEqual(raised.exception.command, "pause")
        self.assertEqual(client.placed, {})


if __name__ == "__main__":
    unittest.main()
