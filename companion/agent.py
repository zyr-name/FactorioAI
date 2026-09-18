"""Bounded observe-decide-act-verify runtime for one companion."""
from dataclasses import dataclass
import re
import threading
import time

from companion.client import ActionInterrupted, BridgeError
from companion.models import ModelError


@dataclass(frozen=True)
class Goal:
    item: str
    count: int
    text: str

    @property
    def inventory_key(self):
        return self.item + ":normal"


def parse_goal(text):
    """Parse a deliberately small first-job grammar from portal instructions."""
    match = re.search(
        r"(?:produce|make|craft)\s+(\d+)\s+([a-z][a-z0-9 _-]*?)"
        r"(?:\s+(?:from|using|with|within)\b|[.,;]|$)", text, re.I,
    )
    if not match:
        raise ValueError("Instructions must include a goal like 'Produce 20 iron plates'.")
    count = int(match.group(1))
    if not 1 <= count <= 10000:
        raise ValueError("Goal count must be between 1 and 10000.")
    item = match.group(2).strip().lower().replace(" ", "-")
    if item.endswith("s"):
        item = item[:-1]
    return Goal(item=item, count=count, text=text.strip())


class AgentRuntime:
    def __init__(self, client, adapter, goal, emit=lambda **_event: None,
                 max_decisions=30, max_seconds=300, max_consecutive_failures=5):
        self.client = client
        self.adapter = adapter
        self.goal = goal
        self.emit = emit
        self.max_decisions = max_decisions
        self.max_seconds = max_seconds
        self.max_consecutive_failures = max_consecutive_failures
        self.started = time.monotonic()
        self.decisions = 0
        self.failures = 0
        self.recoveries = 0
        self.consecutive_failures = 0
        self.recent = []
        self.last_action = None
        self.phase = "observing"

    def snapshot(self, **extra):
        value = {
            "phase": self.phase,
            "objective": {"text": self.goal.text, "item": self.goal.item, "count": self.goal.count},
            "decisions": self.decisions,
            "failures": self.failures,
            "recoveries": self.recoveries,
            "elapsed_seconds": round(time.monotonic() - self.started, 1),
            "last_action": self.last_action,
        }
        value.update(extra)
        return value

    def _send(self, **extra):
        self.emit(**self.snapshot(**extra))

    def _check_control(self, control):
        command = control() if control else None
        if command in {"pause", "stop"}:
            raise ActionInterrupted(command)

    def _complete(self, observation):
        return observation.get("self", {}).get("inventory", {}).get(self.goal.inventory_key, 0) >= self.goal.count

    def _remember(self, result):
        self.recent.append(result)
        self.recent = self.recent[-6:]

    def _execute(self, decision, control):
        action, fields = decision.action, dict(decision.arguments)
        if action == "move":
            return self.client.move(control=control, **fields)
        if action == "mine":
            return self.client.mine(control=control, **fields)
        if action == "craft":
            return self.client.craft(control=control, **fields)
        if action in {"place", "rotate", "transfer"}:
            return self.client.request(action, **fields)
        if action == "wait":
            deadline = time.monotonic() + fields["seconds"]
            state = None
            while time.monotonic() < deadline:
                self._check_control(control)
                time.sleep(min(0.25, max(0, deadline - time.monotonic())))
                state = self.client.request("heartbeat")
            return state
        if action == "finish":
            raise BridgeError("objective_unverified", "The objective is not yet visible in the character inventory.")
        raise ModelError("Unsupported action: " + action)

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

        thread = threading.Thread(target=keepalive, name="companion-lease-keeper", daemon=True)
        thread.start()
        try:
            return self._run(control, keepalive_error)
        finally:
            stopped.set()
            thread.join(timeout=2)

    def _run(self, control, keepalive_error):
        while True:
            self._check_control(control)
            if keepalive_error:
                raise keepalive_error[0]
            elapsed = time.monotonic() - self.started
            if self.decisions >= self.max_decisions:
                raise ModelError("Decision limit reached before the objective was verified.")
            if elapsed >= self.max_seconds:
                raise ModelError("Wall-clock limit reached before the objective was verified.")

            self.phase = "observing"
            observation = self.client.request("observe", radius=16)
            if self._complete(observation):
                self.phase = "completed"
                self._send(verified={"item": self.goal.item, "count": observation["self"]["inventory"][self.goal.inventory_key]})
                return observation

            self.phase = "deciding"
            self._send()
            try:
                response = self.adapter.decide({
                    "objective": {"text": self.goal.text, "required_inventory": {self.goal.item: self.goal.count}},
                    "limits": {"decisions_remaining": self.max_decisions - self.decisions,
                               "seconds_remaining": max(0, round(self.max_seconds - elapsed, 1))},
                    "observation": observation,
                    "recent_results": self.recent,
                })
            except ModelError as error:
                self.failures += 1
                self.consecutive_failures += 1
                self.recoveries += 1
                self.phase = "recovering"
                self._remember({"action": "decide", "ok": False, "error": str(error)})
                self._send(error=str(error))
                if self.consecutive_failures >= self.max_consecutive_failures:
                    raise
                continue
            decision = response.decision
            if keepalive_error:
                raise keepalive_error[0]
            self.decisions += 1
            self.last_action = {"action": decision.action, "arguments": decision.arguments,
                                "reason": decision.reason, "metrics": response.metrics}
            self.phase = "acting"
            self._send()
            self._check_control(control)
            try:
                self._execute(decision, control)
            except (BridgeError, ModelError) as error:
                self.failures += 1
                self.consecutive_failures += 1
                self.phase = "recovering"
                self._remember({"action": decision.action, "arguments": decision.arguments,
                                "ok": False, "error": str(error)})
                self._send(error=str(error))
                if self.consecutive_failures >= self.max_consecutive_failures:
                    raise ModelError("Too many consecutive action failures; last failure: " + str(error)) from error
                self.recoveries += 1
                continue
            self.consecutive_failures = 0
            self.phase = "verifying"
            self._remember({"action": decision.action, "arguments": decision.arguments, "ok": True})
            self._send()
