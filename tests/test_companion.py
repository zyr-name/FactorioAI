import json
from pathlib import Path
import socket
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from companion.client import BridgeError, Companion
from companion.package import build
from companion.rcon import RconClient, RconError


def packet(response_id, kind, body):
    payload = struct.pack("<ii", response_id, kind) + body.encode() + b"\0\0"
    return struct.pack("<i", len(payload)) + payload


class FakeSocket:
    def __init__(self, incoming):
        self.incoming = bytearray(incoming)
        self.sent = bytearray()
        self.closed = False

    def settimeout(self, _timeout):
        pass

    def recv(self, size):
        if not self.incoming:
            return b""
        # Exercise framed reads that arrive in small chunks.
        size = min(size, 3)
        result = bytes(self.incoming[:size])
        del self.incoming[:size]
        return result

    def sendall(self, data):
        self.sent.extend(data)

    def close(self):
        self.closed = True


class RconTests(unittest.TestCase):
    def test_auth_command_and_marker_with_partial_reads(self):
        fake = FakeSocket(packet(1, 2, "") + packet(2, 0, "result\n") + packet(3, 0, "2.0.77\n"))
        with patch("socket.create_connection", return_value=fake):
            client = RconClient("localhost", 27015, "secret")
            self.assertEqual(client.command("/companion {}"), "result\n")
        # Three outgoing packets: auth, command and read-only marker.
        payload = bytes(fake.sent)
        texts = []
        while payload:
            size, = struct.unpack("<i", payload[:4])
            body, payload = payload[4:4 + size], payload[4 + size:]
            texts.append(body[8:-2].decode())
        self.assertEqual(texts, ["secret", "/companion {}", "/version"])

    def test_auth_failure_closes_socket(self):
        fake = FakeSocket(packet(-1, 2, ""))
        with patch("socket.create_connection", return_value=fake):
            with self.assertRaisesRegex(RconError, "authentication failed"):
                RconClient("localhost", 27015, "wrong")
        self.assertTrue(fake.closed)


class FakeTransport:
    def __init__(self):
        self.actions = []

    def command(self, text):
        request = json.loads(text.removeprefix("/companion "))
        self.actions.append(request)
        if request["action"] == "acquire":
            result = {"state": "ready"}
        elif request["action"] == "heartbeat":
            result = {"state": "ready"}
        else:
            result = {"state": "ready"}
        return json.dumps({"id": request["id"], "ok": True, "result": result})


class ClientTests(unittest.TestCase):
    def test_acquire_is_idempotent_and_release_clears_session(self):
        transport = FakeTransport()
        client = Companion(transport)
        client.acquire()
        token = client.session
        client.acquire()
        self.assertEqual([request["action"] for request in transport.actions], ["acquire", "heartbeat"])
        self.assertEqual(client.session, token)
        client.release()
        self.assertIsNone(client.session)
        self.assertEqual(transport.actions[-1]["session"], token)

    def test_rejects_mismatched_response(self):
        class WrongTransport:
            def command(self, _text):
                return '{"id":"wrong","ok":true,"result":{}}'
        with self.assertRaisesRegex(BridgeError, "did not match"):
            Companion(WrongTransport()).request("status")


class PackageTests(unittest.TestCase):
    def test_package_is_deterministic_and_has_versioned_root(self):
        with tempfile.TemporaryDirectory() as directory:
            first = build(Path(directory) / "first.zip")
            second = build(Path(directory) / "second.zip")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(archive.namelist(), [
                    "factorio-ai-companion_0.2.0/control.lua",
                    "factorio-ai-companion_0.2.0/info.json",
                ])


if __name__ == "__main__":
    unittest.main()
