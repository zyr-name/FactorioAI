"""Versioned bridge requests and one controller lease."""
import json
import time
import uuid


class BridgeError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(code + ": " + message)


class Companion:
    def __init__(self, transport):
        self.transport = transport
        self.session = None

    def request(self, action, **fields):
        request = {"version": 1, "id": uuid.uuid4().hex, "action": action, **fields}
        if self.session is not None:
            request["session"] = self.session
        raw = self.transport.command("/companion " + json.dumps(request, separators=(",", ":"), allow_nan=False))
        try:
            response = json.loads(raw)
        except json.JSONDecodeError as error:
            raise BridgeError("invalid_response", "Install/enable the companion mod, then restart the server.") from error
        if not isinstance(response, dict) or response.get("id") != request["id"]:
            raise BridgeError("invalid_response", "The bridge response did not match this request.")
        if response.get("ok") is not True:
            details = response.get("error", {})
            raise BridgeError(details.get("code", "bridge_error"), details.get("message", "Request failed."))
        return response["result"]

    def acquire(self):
        if self.session is not None:
            return self.request("heartbeat")
        token = uuid.uuid4().hex
        result = self.request("acquire", session=token)
        self.session = token
        return result

    def release(self):
        if self.session is not None:
            try:
                return self.request("release")
            finally:
                self.session = None

    def move(self, x, y, relative=False, radius=0.28, timeout=65):
        current = self.request("status")
        if relative:
            x += current["position"]["x"]
            y += current["position"]["y"]
        state = self.request("move", x=x, y=y, radius=radius)
        command_id = state["motion"]["command_id"]
        return self.wait_for_action(state, command_id, "Move", timeout)

    def mine(self, x, y, count=1, name=None, timeout=300):
        fields = {"x": x, "y": y, "count": count}
        if name:
            fields["name"] = name
        state = self.request("mine", **fields)
        return self.wait_for_action(state, self._newest_action_id(state), "Mining", timeout)

    def craft(self, recipe, count=1, timeout=300):
        state = self.request("craft", recipe=recipe, count=count)
        return self.wait_for_action(state, self._newest_action_id(state), "Crafting", timeout)

    def wait_for_action(self, state, command_id, label="Action", timeout=300):
        deadline = time.monotonic() + timeout
        last_tick = state["tick"]
        tick_changed = time.monotonic()
        action = self._action(state, command_id)
        while action and action["state"] in {"queued", "running"}:
            time.sleep(0.3)
            state = self.request("heartbeat")
            action = self._action(state, command_id)
            if action is None:
                raise BridgeError("action_lost", label + " is no longer retained by the game bridge.")
            now = time.monotonic()
            if state["tick"] != last_tick:
                last_tick, tick_changed = state["tick"], now
            if now - tick_changed > 5:
                raise BridgeError("paused", "Simulation is paused. Join the game or disable auto_pause.")
            if now > deadline:
                raise BridgeError("timeout", label + " exceeded the controller's wall-clock deadline.")
        if not action or action["state"] != "completed":
            code = (action or {}).get("error") or (action or {}).get("state", "action_lost")
            raise BridgeError(code, label + " stopped before completion.")
        return state

    @staticmethod
    def _newest_action_id(state):
        actions = state.get("actions", [])
        if not actions:
            raise BridgeError("action_lost", "The game bridge did not retain the action.")
        return actions[0]["id"]

    @staticmethod
    def _action(state, command_id):
        for action in state.get("actions", []):
            if action.get("id") == command_id:
                return action
        # Protocol 1 compatibility while upgrading an existing server.
        motion = state.get("motion", {})
        if not state.get("actions") and motion.get("command_id") == command_id:
            legacy = motion.get("state")
            if legacy in {"moving", "pathfinding", "path_retry"}:
                return {"id": command_id, "state": "running"}
            if legacy == "arrived":
                return {"id": command_id, "state": "completed"}
            return {"id": command_id, "state": "failed", "error": legacy}
        return None
