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

    def move(self, x, y, relative=False, timeout=65):
        current = self.request("status")
        if relative:
            x += current["position"]["x"]
            y += current["position"]["y"]
        state = self.request("move", x=x, y=y)
        command_id = state["motion"]["command_id"]
        deadline = time.monotonic() + timeout
        last_tick = state["tick"]
        tick_changed = time.monotonic()
        while state["motion"]["state"] == "moving":
            time.sleep(0.3)
            state = self.request("heartbeat")
            if state.get("motion", {}).get("command_id") != command_id:
                raise BridgeError("superseded", "The move was replaced by another command.")
            now = time.monotonic()
            if state["tick"] != last_tick:
                last_tick, tick_changed = state["tick"], now
            if now - tick_changed > 5:
                raise BridgeError("paused", "Simulation is paused. Join the game or disable auto_pause.")
            if now > deadline:
                raise BridgeError("timeout", "Move exceeded the controller's wall-clock deadline.")
        if state["motion"]["state"] != "arrived":
            raise BridgeError(state["motion"]["state"], "Move stopped before reaching its target.")
        return state
