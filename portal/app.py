"""Dependency-free local web portal and JSON API."""
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import urllib.parse

from portal.players import PlayerManager
from portal.service import ServerService
from portal.store import Store


ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"


class Portal:
    def __init__(self, database=None):
        self.store = Store(database or ROOT / "server/portal.sqlite3")
        self.players = PlayerManager(self.store)
        self.server = ServerService()

    def state(self):
        return {
            "server": self.server.status(),
            "configuration": self.server.configuration(),
            "backups": self.server.backups(),
            "players": self.players.all(),
        }

    def close(self):
        self.players.close()


def handler(portal):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FactorioAI/0.1"

        def log_message(self, format_, *args):
            print("portal: " + format_ % args, flush=True)

        def json_response(self, value, status=HTTPStatus.OK):
            body = json.dumps(value, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def error_response(self, error, status=HTTPStatus.BAD_REQUEST):
            self.json_response({"error": str(error)}, status)

        def body(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1024 * 1024:
                raise ValueError("Request is too large.")
            if not length:
                return {}
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError("Expected a JSON object.")
            return value

        def same_origin(self):
            origin = self.headers.get("Origin")
            if not origin:
                return True
            parsed = urllib.parse.urlparse(origin)
            host = self.headers.get("Host", "")
            return parsed.scheme == "http" and parsed.netloc == host

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            try:
                if path == "/api/state":
                    return self.json_response(portal.state())
                if path == "/api/logs":
                    return self.json_response({"logs": portal.server.logs()})
                if path == "/health":
                    return self.json_response({"ok": True})
                file_path = STATIC / ("index.html" if path == "/" else path.lstrip("/"))
                try:
                    resolved = file_path.resolve()
                    resolved.relative_to(STATIC.resolve())
                except (ValueError, OSError):
                    return self.send_error(HTTPStatus.NOT_FOUND)
                if not resolved.is_file():
                    return self.send_error(HTTPStatus.NOT_FOUND)
                body = resolved.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mimetypes.guess_type(str(resolved))[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)
            except (OSError, ValueError) as error:
                self.error_response(error)

        def do_POST(self):
            if not self.same_origin():
                return self.error_response("Cross-origin request rejected.", HTTPStatus.FORBIDDEN)
            path = urllib.parse.urlparse(self.path).path
            try:
                payload = self.body()
                if path == "/api/server/action":
                    action = str(payload.get("action", ""))
                    if action in {"stop", "restart", "restore", "reset"}:
                        portal.players.close()
                    output = portal.server.action(action, payload)
                    return self.json_response({"ok": True, "output": output})
                match = re.fullmatch(r"/api/players/([a-z0-9_-]+)/(start|stop)", path)
                if match:
                    player_id, action = match.groups()
                    value = portal.players.start(player_id) if action == "start" else portal.players.stop(player_id)
                    return self.json_response({"ok": True, "runtime": value})
                self.send_error(HTTPStatus.NOT_FOUND)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                self.error_response(error)

        def do_PUT(self):
            if not self.same_origin():
                return self.error_response("Cross-origin request rejected.", HTTPStatus.FORBIDDEN)
            path = urllib.parse.urlparse(self.path).path
            try:
                payload = self.body()
                if path == "/api/server/configuration":
                    return self.json_response(portal.server.update_configuration(payload))
                match = re.fullmatch(r"/api/players/([a-z0-9_-]+)", path)
                if match:
                    name = str(payload.get("name", "")).strip()
                    if not 1 <= len(name) <= 48:
                        raise ValueError("Player name must contain 1–48 characters.")
                    for key in ("model", "instructions"):
                        if key in payload and len(str(payload[key])) > 4000:
                            raise ValueError(key.capitalize() + " is too long.")
                    return self.json_response(portal.store.update_player(match.group(1), payload))
                self.send_error(HTTPStatus.NOT_FOUND)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                self.error_response(error)

    return Handler


def serve(host="127.0.0.1", port=8765, database=None, startup_error=None):
    portal = Portal(database)
    server = ThreadingHTTPServer((host, port), handler(portal))
    print("FactorioAI portal: http://" + host + ":" + str(port), flush=True)
    if startup_error:
        print("Factorio startup warning: " + startup_error, flush=True)
    portal.players.auto_start()
    try:
        server.serve_forever()
    finally:
        portal.close()
        server.server_close()
