"""Replaceable, structured decision adapters for companion agents."""
from dataclasses import dataclass, field
import json
import math
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


class OllamaAdapter:
    def __init__(self, model, endpoint="http://127.0.0.1:11434", timeout=90):
        if not model or any(character.isspace() for character in model):
            raise ValueError("Ollama model name is invalid.")
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    def decide(self, context):
        prompt = "Objective and current state:\n" + json.dumps(context, separators=(",", ":"), sort_keys=True)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "format": DECISION_SCHEMA,
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
            content = result["message"]["content"]
            decision = validate_decision(json.loads(content))
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise ModelError("Ollama returned no valid decision: " + str(error)) from error
        metrics = {
            "adapter": "ollama", "model": result.get("model", self.model),
            "total_duration_ns": result.get("total_duration"),
            "load_duration_ns": result.get("load_duration"),
            "prompt_tokens": result.get("prompt_eval_count"),
            "output_tokens": result.get("eval_count"),
        }
        return AdapterResponse(decision, metrics)


def adapter_for(name, endpoint="http://127.0.0.1:11434"):
    if name.startswith("ollama:"):
        return OllamaAdapter(name.split(":", 1)[1], endpoint=endpoint)
    raise ValueError("Choose a model as ollama:<model-name>; 'scripted' remains idle mode.")
