"""Replaceable, structured decision adapters for companion agents."""
from dataclasses import dataclass, field
import json
import math
import re
import urllib.request


ACTIONS = ("move", "mine", "craft", "place", "rotate", "transfer", "wait", "finish")
DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "action": {"type": "string", "enum": list(ACTIONS)},
        "arguments": {"type": "object"},
    },
    "required": ["reason", "action", "arguments"],
    "additionalProperties": False,
}

FACTORY_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "reason": {"type": "string"},
        "placements": {
            "type": "array", "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"}, "item": {"type": "string"},
                    "x": {"type": "number"}, "y": {"type": "number"},
                    "direction": {"type": "integer", "enum": [0, 4, 8, 12]},
                },
                "required": ["id", "item", "x", "y", "direction"],
                "additionalProperties": False,
            },
        },
        "connections": {
            "type": "array", "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"}, "to": {"type": "string"},
                    "kind": {"type": "string", "enum": ["direct-output"]},
                },
                "required": ["from", "to", "kind"], "additionalProperties": False,
            },
        },
        "supplies": {
            "type": "array", "items": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string"},
                    "inventory": {"type": "string", "enum": ["fuel"]},
                    "item": {"type": "string"}, "count": {"type": "integer"},
                },
                "required": ["entity", "inventory", "item", "count"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["name", "reason", "placements", "connections", "supplies"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You control one Factorio character. Choose exactly one bounded action.
Python validates your JSON and Factorio enforces all mechanics. Never invent items or remote details.
Every turn includes a fresh observation and recent results. Prefer fixing observation.tasks first.
Actions and arguments:
- move: x, y, optional radius (absolute coordinates)
- mine: x, y, optional name and count
- craft: recipe, optional count. This is character hand-crafting and only recipes listed in
  observation.recipes.available are valid. Never use craft for furnace or machine recipes.
- place: item, x, y, optional direction
- rotate: x, y, optional name and reverse
- transfer: direction ('to'/'from'), inventory ('chest', 'fuel', 'source', 'result', 'input', or
  'output'), item, count, x, y, optional name. For a furnace, put ore in 'source', put coal
  in 'fuel', and collect plates from 'result'. Use these exact inventory names. Inventory
  observations append quality (for example 'iron-ore:normal'); action item names omit it.
- wait: seconds (0.25..10; use while a machine is processing)
- finish: no arguments; only when the objective is visibly satisfied
Machines process automatically after their source/input and fuel inventories are loaded. If the
fresh observation shows those inputs, wait for production and then transfer from result/output.
Trust the fresh observation over older results; a failure applies only to that result's arguments.
Give a short reason. Do not emit prose outside the required JSON object."""

FACTORY_SYSTEM_PROMPT = """Design one tiny, sustainable base-Factorio smelting setup.
Return only the required JSON plan. Python and Factorio validate everything before construction.
Use exactly one burner-mining-drill feeding one stone-furnace directly, without belts or inserters.
Use ids 'drill' and 'furnace'. Both 2x2 entities use integer center coordinates and must not overlap.
Place the drill on one of build_constraints.drill_centers and use a cardinal direction:
0 north, 4 east, 8 south, 12 west. Its item-output offset from its center is respectively
(-0.5,-1.3), (1.3,-0.5), (0.5,1.3), or (-1.3,0.5). Use the canonical non-overlapping furnace
center offset for the chosen direction: north (0,-2), east (2,0), south (0,2), west (-2,0).
Declare one direct-output connection
from drill to furnace. Supply at least one coal to each entity's fuel inventory, staying within
the character's inventory budget. Prefer build_constraints.preferred_direction unless feedback
says that placement is blocked. Treat feedback from rejected plans as mandatory correction.
When feedback says a direction is blocked, you MUST choose a different direction and must not
repeat the rejected placement.
Never add ore manually: the drill mines the ore patch and feeds the furnace continuously."""


class ModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class Decision:
    reason: str
    action: str
    arguments: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterResponse:
    decision: Decision
    metrics: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FactoryPlan:
    name: str
    reason: str
    placements: tuple
    connections: tuple
    supplies: tuple


@dataclass(frozen=True)
class PlanResponse:
    plan: FactoryPlan
    metrics: dict = field(default_factory=dict)


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ModelError(name + " must be a finite number.")
    return value


def _integer(value, name, minimum=1, maximum=100000):
    _number(value, name)
    if int(value) != value or not minimum <= value <= maximum:
        raise ModelError(f"{name} must be an integer from {minimum} to {maximum}.")
    return int(value)


def validate_decision(value):
    if not isinstance(value, dict) or set(value) != {"reason", "action", "arguments"}:
        raise ModelError("Decision must contain only reason, action, and arguments.")
    reason, action, arguments = value["reason"], value["action"], value["arguments"]
    if not isinstance(reason, str) or not 1 <= len(reason) <= 500:
        raise ModelError("Decision reason must contain 1..500 characters.")
    if action not in ACTIONS or not isinstance(arguments, dict):
        raise ModelError("Decision action or arguments are invalid.")
    arguments = dict(arguments)

    required = {
        "move": {"x", "y"}, "mine": {"x", "y"}, "craft": {"recipe"},
        "place": {"item", "x", "y"}, "rotate": {"x", "y"},
        "transfer": {"direction", "inventory", "item", "count", "x", "y"},
        "wait": {"seconds"}, "finish": set(),
    }[action]
    optional = {
        "move": {"radius"}, "mine": {"name", "count"}, "craft": {"count"},
        "place": {"direction"}, "rotate": {"name", "reverse"},
        "transfer": {"name"}, "wait": set(), "finish": set(),
    }[action]
    keys = set(arguments)
    if not required <= keys or not keys <= required | optional:
        raise ModelError(f"Invalid arguments for {action}.")
    for key in keys & {"x", "y", "radius", "seconds"}:
        _number(arguments[key], key)
    if "radius" in arguments and not 0.2 <= arguments["radius"] <= 10:
        raise ModelError("radius must be between 0.2 and 10.")
    if "seconds" in arguments and not 0.25 <= arguments["seconds"] <= 10:
        raise ModelError("seconds must be between 0.25 and 10.")
    if "count" in arguments:
        arguments["count"] = _integer(arguments["count"], "count")
    if "direction" in arguments and action == "place":
        arguments["direction"] = _integer(arguments["direction"], "direction", 0, 15)
    for key in keys & {"recipe", "item", "name", "inventory"}:
        if not isinstance(arguments[key], str) or not 1 <= len(arguments[key]) <= 200:
            raise ModelError(key + " must be a short string.")
    if "item" in arguments and arguments["item"].endswith(":normal"):
        arguments["item"] = arguments["item"][:-7]
    if action == "transfer" and arguments["direction"] not in {"to", "from"}:
        raise ModelError("transfer direction must be 'to' or 'from'.")
    if action == "transfer" and arguments["inventory"] not in {
        "chest", "fuel", "source", "result", "input", "output"
    }:
        raise ModelError("Invalid transfer inventory.")
    if "reverse" in arguments and not isinstance(arguments["reverse"], bool):
        raise ModelError("reverse must be boolean.")
    return Decision(reason=reason, action=action, arguments=dict(arguments))


def _short_string(value, label, maximum=500):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ModelError(label + " must be a short string.")
    return value


def validate_factory_plan(value):
    required_keys = {"name", "reason", "placements", "connections", "supplies"}
    if not isinstance(value, dict) or set(value) != required_keys:
        raise ModelError("Factory plan contains invalid top-level fields.")
    name = _short_string(value["name"], "Plan name", 100)
    reason = _short_string(value["reason"], "Plan reason")
    if not isinstance(value["placements"], list) or not 1 <= len(value["placements"]) <= 20:
        raise ModelError("Factory plan needs 1..20 placements.")
    placements, ids = [], set()
    for raw in value["placements"]:
        if not isinstance(raw, dict) or set(raw) != {"id", "item", "x", "y", "direction"}:
            raise ModelError("A placement contains invalid fields.")
        identifier = _short_string(raw["id"], "Placement id", 80)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier) or identifier in ids:
            raise ModelError("Placement ids must be unique identifiers.")
        ids.add(identifier)
        item = _short_string(raw["item"], "Placement item", 200)
        x, y = _number(raw["x"], "x"), _number(raw["y"], "y")
        direction = _integer(raw["direction"], "direction", 0, 12)
        if direction not in {0, 4, 8, 12}:
            raise ModelError("Placement direction must be 0, 4, 8, or 12.")
        placements.append({"id": identifier, "item": item, "x": x, "y": y, "direction": direction})

    if not isinstance(value["connections"], list) or not 1 <= len(value["connections"]) <= 20:
        raise ModelError("Factory plan needs 1..20 connections.")
    connections = []
    for raw in value["connections"]:
        if not isinstance(raw, dict) or set(raw) != {"from", "to", "kind"}:
            raise ModelError("A connection contains invalid fields.")
        if raw["from"] not in ids or raw["to"] not in ids or raw["from"] == raw["to"]:
            raise ModelError("Connections must reference two different placements.")
        if raw["kind"] != "direct-output":
            raise ModelError("Only direct-output connections are supported.")
        connections.append(dict(raw))

    if not isinstance(value["supplies"], list) or not 1 <= len(value["supplies"]) <= 40:
        raise ModelError("Factory plan needs 1..40 supply steps.")
    supplies = []
    for raw in value["supplies"]:
        if not isinstance(raw, dict) or set(raw) != {"entity", "inventory", "item", "count"}:
            raise ModelError("A supply step contains invalid fields.")
        if raw["entity"] not in ids:
            raise ModelError("Supply steps must reference a placement.")
        inventory = _short_string(raw["inventory"], "Supply inventory", 40)
        if inventory not in {"chest", "fuel", "source", "result", "input", "output"}:
            raise ModelError("Supply inventory is invalid.")
        item = _short_string(raw["item"], "Supply item", 200)
        if item.endswith(":normal"):
            item = item[:-7]
        supplies.append({"entity": raw["entity"], "inventory": inventory,
                         "item": item, "count": _integer(raw["count"], "count")})
    return FactoryPlan(name, reason, tuple(placements), tuple(connections), tuple(supplies))


class SequenceAdapter:
    """Deterministic adapter for tests and scripted experiments."""
    def __init__(self, decisions):
        self.decisions = iter(decisions)

    def decide(self, _context):
        try:
            value = next(self.decisions)
        except StopIteration as error:
            raise ModelError("Scripted adapter ran out of decisions.") from error
        return AdapterResponse(validate_decision(value), {"adapter": "sequence"})


class SequencePlanAdapter:
    """Deterministic factory-plan adapter for tests."""
    def __init__(self, plans):
        self.plans = iter(plans)

    def design(self, _context):
        try:
            value = next(self.plans)
        except StopIteration as error:
            raise ModelError("Scripted adapter ran out of factory plans.") from error
        return PlanResponse(validate_factory_plan(value), {"adapter": "sequence"})


class OllamaAdapter:
    def __init__(self, model, endpoint="http://127.0.0.1:11434", timeout=90):
        if not model or any(character.isspace() for character in model):
            raise ValueError("Ollama model name is invalid.")
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    def decide(self, context):
        result = self._chat(SYSTEM_PROMPT, DECISION_SCHEMA, context)
        try:
            decision = validate_decision(json.loads(result["message"]["content"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ModelError("Ollama returned no valid decision: " + str(error)) from error
        return AdapterResponse(decision, self._metrics(result))

    def design(self, context):
        result = self._chat(FACTORY_SYSTEM_PROMPT, FACTORY_PLAN_SCHEMA, context)
        try:
            plan = validate_factory_plan(json.loads(result["message"]["content"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ModelError("Ollama returned no valid factory plan: " + str(error)) from error
        return PlanResponse(plan, self._metrics(result))

    def _chat(self, system_prompt, schema, context):
        prompt = "Objective and current state:\n" + json.dumps(context, separators=(",", ":"), sort_keys=True)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "format": schema,
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "num_ctx": 16384},
            "keep_alive": "5m",
        }
        request = urllib.request.Request(
            self.endpoint + "/api/chat",
            data=json.dumps(payload, separators=(",", ":")).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read())
        except (OSError, TypeError, ValueError) as error:
            raise ModelError("Ollama request failed: " + str(error)) from error
        return result

    def _metrics(self, result):
        return {
            "adapter": "ollama", "model": result.get("model", self.model),
            "total_duration_ns": result.get("total_duration"),
            "load_duration_ns": result.get("load_duration"),
            "prompt_tokens": result.get("prompt_eval_count"),
            "output_tokens": result.get("eval_count"),
        }


def adapter_for(name, endpoint="http://127.0.0.1:11434"):
    if name.startswith("ollama:"):
        return OllamaAdapter(name.split(":", 1)[1], endpoint=endpoint)
    raise ValueError("Choose a model as ollama:<model-name>; 'scripted' remains idle mode.")
