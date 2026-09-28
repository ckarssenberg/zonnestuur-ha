"""Langetermijnstatistieken uit Home Assistant (recorder) ophalen via de websocket-API.

Home Assistant bewaart van elke sensor met een state_class per uur het gemiddelde, maanden tot jaren terug.
Daarmee kan Zonnestuur bij de eerste start meteen je verbruik en teruglevering van het afgelopen jaar tonen
en advies geven, in plaats van weken te moeten meten. Alleen standaardbibliotheek: een minimale websocket-client.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse


class WSError(Exception):
    pass


class _WS:
    def __init__(self, url: str, timeout: float = 30.0):
        u = urlparse(url)
        secure = u.scheme in ("https", "wss")
        port = u.port or (443 if secure else 80)
        path = (u.path.rstrip("/") or "") + "/api/websocket"
        if u.path.endswith("/core"):                      # Supervisor-proxy: http://supervisor/core
            path = "/core/websocket"
        raw = socket.create_connection((u.hostname, port), timeout=timeout)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=u.hostname) if secure else raw
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WSError("verbinding gesloten tijdens handshake")
            head += chunk
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise WSError(f"geen websocket: {status.decode(errors='replace')}")
        self.buf = head.split(b"\r\n\r\n", 1)[1]

    def _recv_exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WSError("verbinding gesloten")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, obj: dict) -> None:
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            hdr = struct.pack("!BB", 0x81, 0x80 | n)
        elif n < 65536:
            hdr = struct.pack("!BBH", 0x81, 0x80 | 126, n)
        else:
            hdr = struct.pack("!BBQ", 0x81, 0x80 | 127, n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(hdr + mask + masked)

    def recv(self) -> dict:
        parts = b""
        while True:
            b1, b2 = self._recv_exact(2)
            fin, op = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._recv_exact(8))[0]
            payload = self._recv_exact(n)
            if op == 0x8:
                raise WSError("verbinding gesloten door Home Assistant")
            if op == 0x9:                                  # ping -> pong
                continue
            parts += payload
            if fin:
                return json.loads(parts.decode())

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def hourly_means(url: str, token: str, statistic_ids: list[str], start: datetime,
                 end: Optional[datetime] = None) -> dict[str, list[tuple[int, float]]]:
    """Uurgemiddelden per sensor: {entity_id: [(unix-tijd begin uur, gemiddelde), ...]}."""
    ws = _WS(url)
    try:
        msg = ws.recv()
        if msg.get("type") != "auth_required":
            raise WSError(f"onverwacht: {msg}")
        ws.send({"type": "auth", "access_token": token})
        msg = ws.recv()
        if msg.get("type") != "auth_ok":
            raise WSError("Home Assistant weigert het token")
        req = {"id": 1, "type": "recorder/statistics_during_period", "start_time": start.isoformat(),
               "statistic_ids": statistic_ids, "period": "hour", "types": ["mean"]}
        if end:
            req["end_time"] = end.isoformat()
        ws.send(req)
        while True:
            msg = ws.recv()
            if msg.get("id") == 1:
                break
        if not msg.get("success"):
            raise WSError(str(msg.get("error")))
        out = {}
        for sid, rows in (msg.get("result") or {}).items():
            vals = []
            for r in rows:
                st = r.get("start")
                ts = int(st / 1000) if isinstance(st, (int, float)) else int(datetime.fromisoformat(st).timestamp())
                if r.get("mean") is not None:
                    vals.append((ts, float(r["mean"])))
            out[sid] = vals
        return out
    finally:
        ws.close()
