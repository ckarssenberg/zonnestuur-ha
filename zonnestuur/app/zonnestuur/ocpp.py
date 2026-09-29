"""OCPP 1.6J: laadpalen rechtstreeks sturen, zonder Home Assistant en zonder cloud.

Zonnestuur is dan de 'centrale' (Central System). Je stelt in de laadpaal (app of webpagina van de installateur)
de OCPP-server in op:  ws://<adres van Zonnestuur>:8887/<naam-van-de-laadpaal>

Werkt met laadpalen die OCPP 1.6J spreken: Alfen, Peblar, Wallbox, ABB, Etrel, Ecotap, EVBox, Smappee,
Easee en Zaptec (via hun OCPP-optie), Heidelberg met OCPP-module, en vele andere.

Zonnestuur regelt de laadstroom met een laadprofiel (SetChargingProfile, TxDefaultProfile, in ampère) en start
het laden zelf als de auto is aangesloten (RemoteStartTransaction). Alleen standaardbibliotheek.
"""
from __future__ import annotations

import base64
import hashlib
import itertools
import json
import logging
import math
import socket
import socketserver
import struct
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from .adapters import DeviceError, NotReady, SwitchStatus

log = logging.getLogger("zonnestuur.ocpp")
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ChargePoint:
    def __init__(self, cp_id: str, conn: socket.socket):
        self.id, self.conn = cp_id, conn
        self.lock = threading.Lock()
        self.connected = True
        self.status = "Unknown"
        self.power_w: Optional[float] = None
        self.current_a: Optional[float] = None
        self.energy_wh: Optional[float] = None
        self.transaction: Optional[int] = None
        self.limit_a: Optional[float] = None
        self.info: dict = {}
        self.last_seen = time.time()
        self._pending: dict[str, list] = {}
        self._ids = itertools.count(1)

    # ---- versturen
    def _send_frame(self, text: str) -> None:
        data = text.encode()
        n = len(data)
        hdr = struct.pack("!BB", 0x81, n) if n < 126 else struct.pack("!BBH", 0x81, 126, n) if n < 65536 else struct.pack("!BBQ", 0x81, 127, n)
        with self.lock:
            self.conn.sendall(hdr + data)

    def send(self, msg: list) -> None:
        self._send_frame(json.dumps(msg))

    def call(self, action: str, payload: dict, timeout: float = 10.0) -> dict:
        if not self.connected:
            raise DeviceError(f"laadpaal {self.id} is niet verbonden")
        mid = f"zs{next(self._ids)}"
        ev = threading.Event()
        self._pending[mid] = [ev, None]
        self.send([2, mid, action, payload])
        if not ev.wait(timeout):
            self._pending.pop(mid, None)
            raise DeviceError(f"laadpaal {self.id} antwoordt niet op {action}")
        res = self._pending.pop(mid)[1]
        if isinstance(res, Exception):
            raise res
        return res

    # ---- commando's
    def set_limit(self, amps: float) -> None:
        amps = max(0.0, round(amps, 1))
        if self.limit_a is not None and abs(self.limit_a - amps) < 0.05:
            return
        start = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        res = self.call("SetChargingProfile", {"connectorId": 0, "csChargingProfiles": {
            "chargingProfileId": 1, "stackLevel": 0, "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute", "chargingSchedule": {
                "startSchedule": start, "chargingRateUnit": "A",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": amps}]}}})
        if res.get("status") != "Accepted":
            raise DeviceError(f"laadpaal {self.id} weigert laadprofiel ({res.get('status')})")
        self.limit_a = amps

    def remote_start(self) -> None:
        res = self.call("RemoteStartTransaction", {"connectorId": 1, "idTag": "zonnestuur"})
        if res.get("status") != "Accepted":
            raise DeviceError(f"laadpaal {self.id} wil niet starten ({res.get('status')})")

    # ---- ontvangen
    def handle(self, msg: list) -> None:
        self.last_seen = time.time()
        kind = msg[0]
        if kind == 2:
            _, mid, action, payload = msg
            try:
                self.send([3, mid, self._on_call(action, payload or {})])
            except NotImplementedError:
                self.send([4, mid, "NotImplemented", f"{action} niet ondersteund", {}])
        elif kind in (3, 4):
            p = self._pending.get(msg[1])
            if p:
                p[1] = msg[2] if kind == 3 else DeviceError(f"laadpaal {self.id}: {msg[2]} {msg[3] if len(msg) > 3 else ''}")
                p[0].set()

    def _on_call(self, action: str, p: dict) -> dict:
        if action == "BootNotification":
            self.info = {k: p.get(k) for k in ("chargePointVendor", "chargePointModel", "firmwareVersion")}
            log.info("Laadpaal %s aangemeld: %s %s", self.id, p.get("chargePointVendor"), p.get("chargePointModel"))
            return {"status": "Accepted", "currentTime": _now(), "interval": 60}
        if action == "Heartbeat":
            return {"currentTime": _now()}
        if action == "StatusNotification":
            if int(p.get("connectorId", 1)) > 0:
                self.status = p.get("status", self.status)
            return {}
        if action == "MeterValues":
            self._meter(p)
            return {}
        if action == "Authorize":
            return {"idTagInfo": {"status": "Accepted"}}
        if action == "StartTransaction":
            self.transaction = int(time.time()) % 2_000_000_000
            return {"transactionId": self.transaction, "idTagInfo": {"status": "Accepted"}}
        if action == "StopTransaction":
            self.transaction = None
            self.power_w = 0.0
            return {"idTagInfo": {"status": "Accepted"}}
        if action == "DataTransfer":
            return {"status": "Accepted"}
        if action in ("FirmwareStatusNotification", "DiagnosticsStatusNotification", "SecurityEventNotification"):
            return {}
        raise NotImplementedError(action)

    def _meter(self, p: dict) -> None:
        power = cur = None
        for mv in p.get("meterValue") or []:
            for sv in mv.get("sampledValue") or []:
                meas = sv.get("measurand", "Energy.Active.Import.Register")
                try:
                    v = float(sv.get("value"))
                except (TypeError, ValueError):
                    continue
                unit = sv.get("unit", "")
                if meas == "Power.Active.Import" and not sv.get("phase"):
                    power = v * (1000 if unit == "kW" else 1)
                elif meas == "Current.Import":
                    cur = (cur or 0.0) + v if sv.get("phase") else v
                elif meas == "Energy.Active.Import.Register" and not sv.get("phase"):
                    self.energy_wh = v * (1000 if unit == "kWh" else 1)
        if power is not None:
            self.power_w = power
        elif cur is not None:
            self.power_w = cur * 230
        if cur is not None:
            self.current_a = cur


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        server: "CentralSystem" = self.server.cs          # type: ignore[attr-defined]
        conn: socket.socket = self.request
        conn.settimeout(120)
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = conn.recv(4096)
            if not chunk:
                return
            head += chunk
        lines = head.split(b"\r\n\r\n", 1)[0].decode(errors="replace").split("\r\n")
        path = lines[0].split(" ")[1] if " " in lines[0] else "/"
        hdrs = {k.strip().lower(): v.strip() for k, v in (ln.split(":", 1) for ln in lines[1:] if ":" in ln)}
        key = hdrs.get("sec-websocket-key")
        cp_id = path.rstrip("/").split("/")[-1] or "laadpaal"
        if not key:
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            return
        accept = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
        proto = "Sec-WebSocket-Protocol: ocpp1.6\r\n" if "ocpp1.6" in hdrs.get("sec-websocket-protocol", "") else ""
        conn.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Accept: {accept}\r\n{proto}\r\n".encode())
        cp = ChargePoint(cp_id, conn)
        server.points[cp_id] = cp
        log.info("Laadpaal %s verbonden via OCPP", cp_id)
        buf = head.split(b"\r\n\r\n", 1)[1]

        def read(n):
            nonlocal buf
            while len(buf) < n:
                chunk = conn.recv(65536)
                if not chunk:
                    raise ConnectionError("gesloten")
                buf += chunk
            out, buf = buf[:n], buf[n:]
            return out
        try:
            parts = b""
            while True:
                b1, b2 = read(2)
                op, n = b1 & 0x0F, b2 & 0x7F
                if n == 126:
                    n = struct.unpack("!H", read(2))[0]
                elif n == 127:
                    n = struct.unpack("!Q", read(8))[0]
                mask = read(4) if b2 & 0x80 else b"\0\0\0\0"
                data = bytes(c ^ mask[i % 4] for i, c in enumerate(read(n)))
                if op == 0x8:
                    break
                if op == 0x9:
                    with cp.lock:
                        conn.sendall(struct.pack("!BB", 0x8A, len(data)) + data)
                    continue
                if op == 0xA:
                    continue
                parts += data
                if b1 & 0x80:
                    try:
                        cp.handle(json.loads(parts.decode()))
                    except (ValueError, IndexError, TypeError) as exc:
                        log.warning("OCPP %s: onleesbaar bericht (%s)", cp_id, exc)
                    parts = b""
        except (OSError, ConnectionError):
            pass
        finally:
            cp.connected = False
            log.info("Laadpaal %s is niet meer verbonden", cp_id)


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class CentralSystem:
    def __init__(self, host: str = "0.0.0.0", port: int = 8887):
        self.points: dict[str, ChargePoint] = {}
        self.port = port
        self.srv = _Server((host, port), _Handler)
        self.srv.cs = self                               # type: ignore[attr-defined]
        threading.Thread(target=self.srv.serve_forever, daemon=True, name="ocpp").start()
        log.info("OCPP-server luistert op poort %d", port)

    def get(self, cp_id: str) -> Optional[ChargePoint]:
        return self.points.get(cp_id)

    def overview(self) -> list[dict]:
        return [{"id": cp.id, "connected": cp.connected, "status": cp.status, "power_w": cp.power_w, **cp.info}
                for cp in self.points.values()]

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


_SYSTEMS: dict[int, CentralSystem] = {}


def central_system(port: int = 8887) -> CentralSystem:
    cs = _SYSTEMS.get(port)
    if cs is None:
        cs = _SYSTEMS[port] = CentralSystem(port=port)
    return cs


class OCPPCharger:
    """Traploos laden via OCPP (zelfde gedrag als een laadpaal via Home Assistant)."""

    modulating = True

    def __init__(self, cs: CentralSystem, cp_id: str, phases: int = 3, volts: float = 230.0, min_a: float = 6.0, max_a: float = 16.0):
        self.cs, self.cp_id = cs, cp_id
        self.phases, self.volts, self.min_a, self.max_a = phases, volts, min_a, max_a

    @property
    def w_per_a(self) -> float:
        return self.volts * self.phases

    @property
    def min_w(self) -> float:
        return self.min_a * self.w_per_a

    @property
    def max_w(self) -> float:
        return self.max_a * self.w_per_a

    @property
    def step_w(self) -> float:
        return self.w_per_a

    def _cp(self) -> ChargePoint:
        cp = self.cs.get(self.cp_id)
        if not cp or not cp.connected:
            raise DeviceError(f"laadpaal '{self.cp_id}' is nog niet verbonden met Zonnestuur (OCPP)")
        return cp

    def status(self) -> SwitchStatus:
        cp = self._cp()
        if cp.status in ("Available", "Unavailable", "Faulted"):
            raise NotReady("geen auto aangesloten" if cp.status == "Available" else f"laadpaal: {cp.status}")
        on = (cp.limit_a or 0) > 0 and cp.status in ("Charging", "SuspendedEV", "Preparing")
        return SwitchStatus(on, float(cp.power_w or 0.0), cp.energy_wh)

    def set(self, on: bool) -> None:
        cp = self._cp()
        if on:
            cp.set_limit(max(self.min_a, cp.limit_a or 0.0) if (cp.limit_a or 0) > 0 else self.min_a)
            if cp.transaction is None and cp.status in ("Preparing", "SuspendedEVSE", "Finishing"):
                cp.remote_start()
        else:
            cp.set_limit(0)

    def set_power(self, watt: float) -> None:
        amps = max(self.min_a, min(self.max_a, math.floor(watt / self.w_per_a)))
        self._cp().set_limit(amps)
