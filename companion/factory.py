"""Model-proposed, preflighted construction of a tiny sustainable factory."""
import math
import re
import threading
import time

from companion.client import ActionInterrupted, BridgeError
from companion.models import FactoryPlan, ModelError


OUTPUT_OFFSETS = {
    0: (-0.5, -1.3),
    4: (1.3, -0.5),
    8: (0.5, 1.3),
    12: (-1.3, 0.5),
}


def is_factory_goal(text):
    return bool(re.search(r"\b(?:build|design|construct)\b.*\b(?:smelt|smelting|factory|setup)\b", text, re.I))


def plan_dict(plan):
    return {
        "name": plan.name, "reason": plan.reason,
        "placements": [dict(value) for value in plan.placements],
        "connections": [dict(value) for value in plan.connections],
        "supplies": [dict(value) for value in plan.supplies],
    }


def _inventory_count(inventory, item):
    return inventory.get(item + ":normal", inventory.get(item, 0))


class FactoryRuntime:
    def __init__(self, client, adapter, objective, emit=lambda **_event: None,
                 max_designs=4, max_seconds=300, sample_seconds=12):
        self.client = client
        self.adapter = adapter
        self.objective = objective.strip()
        self.emit = emit
        self.max_designs = max_designs
        self.max_seconds = max_seconds
        self.sample_seconds = sample_seconds
        self.started = time.monotonic()
        self.designs = 0
        self.failures = 0
        self.recoveries = 0
        self.phase = "observing"
        self.last_action = None
        self.feedback = []

    def snapshot(self, **extra):
        value = {
            "phase": self.phase,
            "objective": {"text": self.objective, "kind": "sustainable-smelting"},
            "decisions": self.designs,
            "failures": self.failures,
            "recoveries": self.recoveries,
            "elapsed_seconds": round(time.monotonic() - self.started, 1),
            "last_action": self.last_action,
        }
        value.update(extra)
        return value

    def _send(self, **extra):
        self.emit(**self.snapshot(**extra))

    @staticmethod
    def _check_control(control):
        command = control() if control else None
        if command in {"pause", "stop"}:
            raise ActionInterrupted(command)

    def _wait(self, seconds, control):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._check_control(control)
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))

    @staticmethod
    def _constraints(observation):
        resources = observation.get("local_area", {}).get("resources", [])
        iron = next((item for item in resources if item.get("name") == "iron-ore"), None)
        if not iron or not iron.get("nearest"):
            raise ModelError("No local iron-ore patch is available for a burner mining drill.")
        nearest = iron["nearest"]
        xs = sorted({math.floor(nearest["x"]), math.ceil(nearest["x"])})
        ys = sorted({math.floor(nearest["y"]), math.ceil(nearest["y"])})
        centers = [{"x": x, "y": y} for x in xs for y in ys]
        return {
            "drill_centers": centers,
            "preferred_direction": 0,
            "directions": [0, 4, 8, 12],
            "allowed_placements": ["burner-mining-drill", "stone-furnace"],
            "required_connection": "drill direct-output to furnace",
        }

    def _live_constraints(self, observation):
        constraints = self._constraints(observation)
        valid_centers = []
        for center in constraints["drill_centers"]:
            try:
                self.client.request("validate_plan", placements=[{
                    "id": "drill_probe", "item": "burner-mining-drill",
                    "x": center["x"], "y": center["y"], "direction": 0,
                }])
                valid_centers.append(center)
            except BridgeError as error:
                if error.code != "cannot_place":
                    raise
        if not valid_centers:
            raise ModelError("Factorio found no placeable burner-drill center on the local iron patch.")
        constraints["drill_centers"] = valid_centers
        return constraints

    @staticmethod
    def _validate(plan, observation, constraints):
        if not isinstance(plan, FactoryPlan):
            raise ModelError("Adapter returned no factory plan.")
        placements = {value["id"]: value for value in plan.placements}
        if set(placements) != {"drill", "furnace"}:
            raise ModelError("Plan must contain exactly the drill and furnace placements.")
        if placements["drill"]["item"] != "burner-mining-drill":
            raise ModelError("The drill placement must use burner-mining-drill.")
        if placements["furnace"]["item"] != "stone-furnace":
            raise ModelError("The furnace placement must use stone-furnace.")
        for placement in placements.values():
            if placement["x"] != int(placement["x"]) or placement["y"] != int(placement["y"]):
                raise ModelError("Drill and furnace centers must use integer coordinates.")
        allowed_centers = {(value["x"], value["y"]) for value in constraints["drill_centers"]}
        drill = placements["drill"]
        if (drill["x"], drill["y"]) not in allowed_centers:
            raise ModelError("The drill must use one of build_constraints.drill_centers.")
        expected_connection = {"from": "drill", "to": "furnace", "kind": "direct-output"}
        if len(plan.connections) != 1 or plan.connections[0] != expected_connection:
            raise ModelError("Plan must declare drill direct-output to furnace.")
        offset = OUTPUT_OFFSETS[drill["direction"]]
        output = (drill["x"] + offset[0], drill["y"] + offset[1])
        furnace = placements["furnace"]
        if abs(output[0] - furnace["x"]) > 0.71 or abs(output[1] - furnace["y"]) > 0.71:
            canonical = {
                0: (drill["x"], drill["y"] - 2),
                4: (drill["x"] + 2, drill["y"]),
                8: (drill["x"], drill["y"] + 2),
                12: (drill["x"] - 2, drill["y"]),
            }[drill["direction"]]
            raise ModelError(
                "The furnace does not cover the drill output. For this drill and direction, "
                f"use furnace center x={canonical[0]}, y={canonical[1]}."
            )
        inventory = observation.get("self", {}).get("inventory", {})
        placement_budget = {}
        for placement in plan.placements:
            placement_budget[placement["item"]] = placement_budget.get(placement["item"], 0) + 1
        for item, count in placement_budget.items():
            if _inventory_count(inventory, item) < count:
                raise ModelError("The character lacks planned placement item " + item + ".")
        fuel_steps = {value["entity"]: value for value in plan.supplies
                      if value["inventory"] == "fuel" and value["item"] == "coal"}
        if set(fuel_steps) != {"drill", "furnace"} or any(value["count"] < 1 for value in fuel_steps.values()):
            raise ModelError("Plan must fuel both drill and furnace with coal.")
        if sum(value["count"] for value in fuel_steps.values()) > _inventory_count(inventory, "coal"):
            raise ModelError("Plan supplies more coal than the character has.")
        if len(plan.supplies) != 2:
            raise ModelError("This direct-feed plan needs only the two fuel supply steps.")
        return placements

    def _rollback(self, placed, control):
        for placement in reversed(placed):
            self._check_control(control)
            self.client.mine(placement["x"], placement["y"], name=placement["item"],
                             timeout=30, control=control)

    def _building(self, observation, placement):
        for building in observation.get("local_area", {}).get("buildings", []):
            position = building.get("position", {})
            if (building.get("name") == placement["item"]
                    and position.get("x") == placement["x"] and position.get("y") == placement["y"]):
                return building
        return None

    def _execute(self, plan, placements, control):
        placed = []
        try:
            for placement in plan.placements:
                self._check_control(control)
                self.client.request("place", item=placement["item"], x=placement["x"],
                                    y=placement["y"], direction=placement["direction"])
                placed.append(placement)
            for supply in plan.supplies:
                target = placements[supply["entity"]]
                self.client.request(
                    "transfer", direction="to", inventory=supply["inventory"], item=supply["item"],
                    count=supply["count"], x=target["x"], y=target["y"], name=target["item"],
                )
            # Mining targets and machine state are assigned by the simulation,
            # not synchronously by create_entity, and burner drills need fuel.
            self._wait(0.5, control)
            observation = self.client.request("observe", radius=16)
            drill = self._building(observation, placements["drill"])
            furnace = self._building(observation, placements["furnace"])
            if not drill or not furnace:
                raise ModelError("Placed factory entities are not visible for connection verification.")
            drop_position = drill.get("drop_position") or {}
            if (abs(drop_position.get("x", math.inf) - furnace["position"]["x"]) > 0.71
                    or abs(drop_position.get("y", math.inf) - furnace["position"]["y"]) > 0.71):
                raise ModelError(
                    "The drill's live output point is not covered by the planned furnace. "
                    f"drop_position={drop_position}, furnace_position={furnace.get('position')}."
                )
            if not drill.get("mining_target") or drill["mining_target"].get("name") != "iron-ore":
                raise ModelError("The placed drill has no live iron-ore mining target.")
        except ActionInterrupted as interruption:
            if interruption.command == "pause":
                self._rollback(placed, None)
            raise
        except (BridgeError, ModelError):
            self._rollback(placed, control)
            raise
        return placements["furnace"], placed

    @staticmethod
    def _recovery_feedback(error, plan):
        message = str(error)
        if ("cannot_place" not in message and "output point" not in message
                and "plan_collision" not in message):
            return message
        drill = next((value for value in plan.placements if value["id"] == "drill"), None)
        if not drill:
            return message
        x, y, rejected = drill["x"], drill["y"], drill["direction"]
        canonical = {
            0: (x, y - 2), 4: (x + 2, y), 8: (x, y + 2), 12: (x - 2, y),
        }[rejected]
        if "plan_collision" in message:
            return (message + f" For drill x={x}, y={y}, direction {rejected}, the furnace MUST "
                    f"use x={canonical[0]}, y={canonical[1]}. Correct the furnace coordinates.")
        alternatives = [
            f"direction 0 with furnace x={x}, y={y - 2}",
            f"direction 4 with furnace x={x + 2}, y={y}",
            f"direction 8 with furnace x={x}, y={y + 2}",
            f"direction 12 with furnace x={x - 2}, y={y}",
        ]
        alternatives = [value for value in alternatives if not value.startswith(f"direction {rejected} ")]
        return (message + f" Do not repeat direction {rejected}. Choose a different direction. "
                + "Exact alternatives for the same drill center: " + "; ".join(alternatives) + ".")

    def _result_count(self, placement):
        observation = self.client.request("observe", radius=16)
        furnace = self._building(observation, placement)
        if not furnace:
            raise ModelError("The furnace disappeared during sustained-production verification.")
        return _inventory_count(furnace.get("inventories", {}).get("result", {}), "iron-plate")

    def run(self, control=None):
        stopped = threading.Event()
        keepalive_error = []

        def keepalive():
            while not stopped.wait(1):
                try:
                    self.client.request("heartbeat")
                except (BridgeError, OSError) as error:
                    keepalive_error.append(error)
                    return

        thread = threading.Thread(target=keepalive, name="factory-lease-keeper", daemon=True)
        thread.start()
        try:
            return self._run(control, keepalive_error)
        finally:
            stopped.set()
            thread.join(timeout=2)

    def _run(self, control, keepalive_error):
        while self.designs < self.max_designs:
            self._check_control(control)
            if keepalive_error:
                raise keepalive_error[0]
            if time.monotonic() - self.started >= self.max_seconds:
                raise ModelError("Factory design exceeded its wall-clock limit.")
            self.phase = "observing"
            observation = self.client.request("observe", radius=16)
            constraints = self._live_constraints(observation)
            self.phase = "designing"
            self._send()
            self.designs += 1
            try:
                response = self.adapter.design({
                    "objective": self.objective,
                    "observation": observation,
                    "build_constraints": constraints,
                    "feedback": self.feedback[-3:],
                    "designs_remaining": self.max_designs - self.designs + 1,
                })
            except ModelError as error:
                self.failures += 1
                self.recoveries += 1
                self.feedback.append(str(error))
                self.phase = "recovering"
                self._send(error=str(error))
                continue
            plan = response.plan
            self.last_action = {"action": "design", "reason": plan.reason,
                                "plan": plan_dict(plan), "metrics": response.metrics}
            self.phase = "validating"
            self._send()
            try:
                placements = self._validate(plan, observation, constraints)
                self.client.request("validate_plan", placements=list(plan.placements))
                self.phase = "building"
                self._send()
                furnace, placed = self._execute(plan, placements, control)
            except (BridgeError, ModelError) as error:
                self.failures += 1
                self.recoveries += 1
                feedback = self._recovery_feedback(error, plan)
                self.feedback.append(feedback)
                self.phase = "recovering"
                self._send(error=feedback)
                continue

            try:
                self.phase = "verifying"
                self._send()
                self._wait(self.sample_seconds, control)
                first = self._result_count(furnace)
                self._wait(self.sample_seconds, control)
                second = self._result_count(furnace)
                if first < 1 or second <= first:
                    raise ModelError(
                        f"Factory did not sustain production: samples were {first} then {second} iron plates."
                    )
            except ActionInterrupted as interruption:
                if interruption.command == "pause":
                    self._rollback(placed, None)
                raise
            except (BridgeError, ModelError) as error:
                self._rollback(placed, control)
                self.failures += 1
                self.recoveries += 1
                feedback = str(error) + " Choose a different direction or drill center."
                self.feedback.append(feedback)
                self.phase = "recovering"
                self._send(error=feedback)
                continue
            self.phase = "completed"
            verified = {"product": "iron-plate", "first_count": first, "second_count": second,
                        "sample_seconds": self.sample_seconds, "increase": second - first}
            self._send(verified=verified)
            return verified
        raise ModelError("Factory design limit reached; last feedback: " + (self.feedback[-1] if self.feedback else "none"))
