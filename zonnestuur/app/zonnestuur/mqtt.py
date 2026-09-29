"""MQTT: één koppeling naar honderden apparaten (Zigbee2MQTT, Tasmota, ESPHome, P1-lezers, Victron, batterijen…).

Minimale MQTT 3.1.1-client, alleen standaardbibliotheek. QoS 0, abonnementen met + en #, automatisch opnieuw
verbinden. Laatste waarde per topic staat in `values` (bewaarde berichten komen direct binnen).
"""
from __future__ import annotations

import json
import logging
import socket
import struct
import threading
import time
from typing import Callable, Optional

log = logging.getLogger("zonnestuur.mqtt")


def _enc_str(s: str) -> bytes:
    b = s.encode()
    return struct.pack("!H", len(b)) + b


def _enc_len(n: int) -> bytes:
    out = bytearray()
    while True:
        d, n = n % 128, n // 128
        out.append(d | (0x80 if n else 0))
        if not n:
            return bytes(out)


def topic_matches(pattern: str, topic: str) -> bool:
    p, t = pattern.split("/"), topic.split("/")
    for i, part in enumerate(p):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(p) == len(t)


class MQTTClient:
    def __init__(self, host: str, port: int = 1883, username: str = "", password: str = "", client_id: str = "zonnestuur",
                 keepalive: int = 30):
        self.host, self.port, self.username, self.password = host, int(port or 1883), username, password
        self.client_id, self.keepalive = client_id, keepalive
        self.values: dict[str, bytes] = {}
        self.stamps: dict[str, float] = {}
        self.subs: set[str] = set()
        self.sock: Optional[socket.socket] = None
        self.connected = threading.Event()
        self.lock = threading.Lock()
        self._stop = False
        self._pid = 1
        self.error = ""
        self._thread = threading.Thread(target=self._run, daemon=True, name="mqtt")
        self._thread.start()

    # ---- openbaar
    def subscribe(self, pattern: str) -> None:
        if pattern in self.subs:
            return
        self.subs.add(pattern)
        if self.connected.is_set():
            self._send_subscribe([pattern])

    def publish(self, topic: str, payload, retain: bool = False) -> None:
        if not self.connected.wait(3):
            raise ConnectionError(self.error or "MQTT-broker niet bereikbaar")
        body = payload if isinstance(payload, bytes) else (json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)).encode()
        var = _enc_str(topic)
        pkt = bytes([0x30 | (1 if retain else 0)]) + _enc_len(len(var) + len(body)) + var + body
        self._write(pkt)

    def get(self, topic: str) -> Optional[bytes]:
        return self.values.get(topic)

    def wait_for(self, topic: str, timeout: float = 3.0) -> Optional[bytes]:
        end = time.time() + timeout
        while time.time() < end:
            if topic in self.values:
                return self.values[topic]
            time.sleep(0.05)
        return None

    def topics(self, pattern: str) -> dict[str, bytes]:
        return {t: v for t, v in list(self.values.items()) if topic_matches(pattern, t)}

    def close(self) -> None:
        self._stop = True
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass

    # ---- intern
    def _write(self, pkt: bytes) -> None:
        with self.lock:
            if not self.sock:
                raise ConnectionError("niet verbonden")
            self.sock.sendall(pkt)

    def _send_subscribe(self, patterns: list[str]) -> None:
        self._pid = self._pid % 65000 + 1
        var = struct.pack("!H", self._pid) + b"".join(_enc_str(p) + b"\x00" for p in patterns)
        self._write(bytes([0x82]) + _enc_len(len(var)) + var)

    def _connect(self) -> None:
        s = socket.create_connection((self.host, self.port), timeout=5)
        flags = 0x02
        payload = _enc_str(self.client_id)
        if self.username:
            flags |= 0x80
            payload += _enc_str(self.username)
            if self.password:
                flags |= 0x40
                payload += _enc_str(self.password)
        var = _enc_str("MQTT") + bytes([4, flags]) + struct.pack("!H", self.keepalive)
        s.sendall(bytes([0x10]) + _enc_len(len(var) + len(payload)) + var + payload)
        s.settimeout(5)
        hdr = s.recv(4)
        if len(hdr) < 4 or hdr[0] != 0x20:
            raise ConnectionError("geen CONNACK")
        if hdr[3] != 0:
            raise ConnectionError({4: "gebruikersnaam of wachtwoord klopt niet", 5: "geen toegang"}.get(hdr[3], f"geweigerd ({hdr[3]})"))
        s.settimeout(1.0)
        self.sock = s

    def _read_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
            except socket.timeout:
                if self._stop:
                    raise ConnectionError("gestopt")
                continue
            if not chunk:
                raise ConnectionError("verbinding gesloten")
            buf += chunk
        return buf

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop:
            try:
                self._connect()
                self.error, backoff = "", 1.0
                self.connected.set()
                if self.subs:
                    self._send_subscribe(sorted(self.subs))
                last_ping = time.time()
                while not self._stop:
                    if time.time() - last_ping > self.keepalive / 2:
                        self._write(b"\xc0\x00")
                        last_ping = time.time()
                    try:
                        b0 = self.sock.recv(1)
                    except socket.timeout:
                        continue
                    if not b0:
                        raise ConnectionError("verbinding gesloten")
                    mult, n = 1, 0
                    while True:
                        d = self._read_exact(1)[0]
                        n += (d & 0x7F) * mult
                        mult *= 128
                        if not d & 0x80:
                            break
                    body = self._read_exact(n) if n else b""
                    if b0[0] >> 4 == 3:                               # PUBLISH
                        tl = struct.unpack("!H", body[:2])[0]
                        topic = body[2:2 + tl].decode(errors="replace")
                        qos = (b0[0] >> 1) & 3
                        payload = body[2 + tl + (2 if qos else 0):]
                        self.values[topic] = payload
                        self.stamps[topic] = time.time()
            except (OSError, ConnectionError, ValueError) as exc:
                self.connected.clear()
                self.error = str(exc) or exc.__class__.__name__
                if self._stop:
                    return
                log.debug("MQTT: %s, opnieuw over %.0f s", self.error, backoff)
                time.sleep(backoff)
                backoff = min(60.0, backoff * 2)
            finally:
                try:
                    if self.sock:
                        self.sock.close()
                except OSError:
                    pass
                self.sock = None


_CLIENTS: dict[tuple, MQTTClient] = {}


def client_for(conf: dict) -> MQTTClient:
    """Eén gedeelde verbinding per broker."""
    key = (conf.get("host"), int(conf.get("port") or 1883), conf.get("username", ""), conf.get("password", ""))
    c = _CLIENTS.get(key)
    if c is None:
        c = _CLIENTS[key] = MQTTClient(*key, client_id=f"zonnestuur-{int(time.time()) % 100000}")
    return c


def extract(payload: Optional[bytes], key: str = "") -> Optional[float]:
    """Getal uit een bericht: kaal getal, of een veld uit JSON (punten voor diepere velden, bijv. ENERGY.Power)."""
    if payload is None:
        return None
    txt = payload.decode(errors="replace").strip()
    if not key:
        try:
            return float(txt)
        except ValueError:
            try:
                v = json.loads(txt)
                return float(v) if isinstance(v, (int, float)) else None
            except ValueError:
                return None
    try:
        v = json.loads(txt)
        for part in key.split("."):
            v = v[part]
        return float(v)
    except (ValueError, KeyError, TypeError):
        return None


def extract_state(payload: Optional[bytes], key: str = "") -> Optional[bool]:
    if payload is None:
        return None
    txt = payload.decode(errors="replace").strip()
    if key:
        try:
            v = json.loads(txt)
            for part in key.split("."):
                v = v[part]
            txt = str(v)
        except (ValueError, KeyError, TypeError):
            return None
    return txt.upper() in ("ON", "1", "TRUE")
