"""Small Source RCON client with framed reads, authentication and response IDs."""
import socket
import struct


class RconError(RuntimeError):
    pass


class RconClient:
    def __init__(self, host, port, password, timeout=2.0):
        self.socket = socket.create_connection((host, port), timeout=timeout)
        self.socket.settimeout(timeout)
        self.sequence = 0
        try:
            request = self._send(3, password)
            for _ in range(10):
                response_id, kind, _ = self._receive()
                if response_id == -1:
                    raise RconError("RCON authentication failed.")
                if kind == 2 and response_id == request:
                    break
            else:
                raise RconError("Missing RCON authentication response.")
        except BaseException:
            self.close()
            raise

    def close(self):
        self.socket.close()

    def _read(self, size):
        result = bytearray()
        while len(result) < size:
            chunk = self.socket.recv(size - len(result))
            if not chunk:
                raise RconError("RCON connection closed.")
            result.extend(chunk)
        return bytes(result)

    def _receive(self):
        size, = struct.unpack("<i", self._read(4))
        if size < 10 or size > 4 * 1024 * 1024:
            raise RconError("Invalid RCON packet length.")
        packet = self._read(size)
        if packet[-2:] != b"\0\0":
            raise RconError("Invalid RCON packet terminator.")
        response_id, kind = struct.unpack("<ii", packet[:8])
        return response_id, kind, packet[8:-2]

    def _send(self, kind, text):
        encoded = text.encode("utf-8")
        if b"\0" in encoded or len(encoded) > 4000:
            raise RconError("RCON command must be at most 4000 bytes, without NUL characters.")
        self.sequence += 1
        packet = struct.pack("<ii", self.sequence, kind) + encoded + b"\0\0"
        self.socket.sendall(struct.pack("<i", len(packet)) + packet)
        return self.sequence

    def command(self, text):
        try:
            request = self._send(2, text)
            # Factorio ignores empty commands. A read-only /version response
            # marks the end of possibly multiple packets for our actual command.
            marker = self._send(2, "/version")
            parts = []
            total = 0
            for _ in range(1024):
                response_id, kind, body = self._receive()
                if kind != 0:
                    raise RconError("Unexpected RCON response type.")
                if response_id == marker:
                    return b"".join(parts).decode("utf-8")
                if response_id != request:
                    raise RconError("Unexpected RCON response identifier.")
                parts.append(body)
                total += len(body)
                if total > 4 * 1024 * 1024:
                    raise RconError("RCON response is too large.")
            raise RconError("Too many RCON response packets.")
        except BaseException:
            # Never reuse a stream after a timeout or partial read.
            self.close()
            raise
