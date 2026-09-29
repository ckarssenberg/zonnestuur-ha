"""Apparaten in het thuisnetwerk vinden en herkennen, zonder dat de klant IP-adressen hoeft te weten.

Twee manieren, allebei zonder extra pakketten:
1. mDNS: HomeWizard (API v1) meldt zich als _hwenergy._tcp, Shelly Gen2/Gen3 als _shelly._tcp.
2. Netwerkscan: alle adressen in het eigen /24-netwerk langs en vragen wat er antwoordt.
   Langzamer, maar werkt ook als mDNS door de router wordt tegengehouden.
Wat gevonden wordt, herkennen we via de eigen info-pagina van het apparaat.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

MDNS_ADDR, MDNS_PORT = "224.0.0.251", 5353
SERVICES = ("_hwenergy._tcp.local", "_shelly._tcp.local")


@dataclass
class Found:
    host: str                 # ip[:poort]
    kind: str                 # p1 | shelly | shelly_em | hw_socket | tasmota | homewizard-other
    driver: str = ""          # koppeling voor schakelaars (zie drivers.py)
    model: str = ""
    name: str = ""
    serial: str = ""
    ok: bool = True           # bruikbaar zoals het nu staat
    problem: str = ""         # uitleg voor de klant als ok False is
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---- herkennen ----------------------------------------------------------------
def _get(url: str, timeout: float) -> tuple[int, Optional[dict]]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read(65536)
            try:
                return r.status, json.loads(body.decode("utf-8"))
            except ValueError:
                return r.status, None
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None


SHELLY_MODELS = {
    "S3PL-00112EU": "Shelly Plug S Gen3", "S3SW-001P16EU": "Shelly 1PM Gen3", "S3SW-001X16EU": "Shelly 1 Gen3",
    "SPSW-201PE16EU": "Shelly Pro 1PM", "SPSW-201XE16EU": "Shelly Pro 1", "SNSW-001P16EU": "Shelly Plus 1PM",
    "SNSW-001X16EU": "Shelly Plus 1", "SNPL-00112EU": "Shelly Plus Plug S",
}


def identify(host: str, timeout: float = 1.5) -> Optional[Found]:
    """Vraag een adres wat voor apparaat het is. Geeft None als het geen HomeWizard of Shelly is."""
    base = host if host.startswith("http") else f"http://{host}"
    hostonly = host.replace("http://", "")

    code, info = _get(f"{base}/api", timeout)
    if code == 200 and info and "product_type" in info:
        ptype = str(info.get("product_type", ""))
        kind = {"HWE-P1": "p1", "HWE-SKT": "hw_socket"}.get(ptype, "homewizard-other")
        found = Found(host=hostonly, kind=kind, model=ptype, name=str(info.get("product_name", "HomeWizard")),
                      serial=str(info.get("serial", "")), driver="homewizard_socket" if kind == "hw_socket" else "")
        if kind == "hw_socket":
            dcode, st = _get(f"{base}/api/v1/state", timeout)
            if dcode != 200 or not st:
                found.ok, found.problem = False, "local_api_off"
            else:
                found.extra["metering"], found.extra["on"] = True, bool(st.get("power_on"))
            return found
        if found.kind == "p1":
            dcode, data = _get(f"{base}/api/v1/data", timeout)
            if dcode != 200 or not data or "active_power_w" not in data:
                found.ok = False
                found.problem = "local_api_off"
            else:
                found.extra["active_power_w"] = data["active_power_w"]
        return found

    code, info = _get(f"{base}/shelly", timeout)
    if code == 200 and info and ("gen" in info or "model" in info or "type" in info):
        gen = int(info.get("gen") or 1)
        model = str(info.get("model") or info.get("type") or "")
        if gen >= 2 and model in SHELLY_EM_MODELS:
            found = Found(host=hostonly, kind="shelly_em", model=model, serial=str(info.get("mac", "")),
                          name=SHELLY_EM_MODELS[model], extra={"gen": gen})
            if info.get("auth_en"):
                found.ok, found.problem = False, "shelly_password"
            return found
        found = Found(host=hostonly, kind="shelly", model=model, serial=str(info.get("mac", "")),
                      name=SHELLY_MODELS.get(model, f"Shelly ({model or 'onbekend'})"), extra={"gen": gen, "id": info.get("id", "")},
                      driver="shelly" if gen >= 2 else "shelly_gen1")
        if gen < 2:
            if info.get("auth"):
                found.ok, found.problem = False, "shelly_password"
            else:
                scode, st = _get(f"{base}/status", timeout)
                if scode != 200 or not st or not st.get("relays"):
                    found.ok, found.problem = False, "shelly_no_switch"
                else:
                    found.extra["metering"] = bool(st.get("meters"))
                    found.extra["on"] = bool(st["relays"][0].get("ison"))
        elif info.get("auth_en"):
            found.ok, found.problem = False, "shelly_password"
        else:
            scode, st = _get(f"{base}/rpc/Switch.GetStatus?id=0", timeout)
            if scode != 200 or not st:
                found.ok, found.problem = False, "shelly_no_switch"
            else:
                found.extra["metering"] = "apower" in st
                found.extra["on"] = bool(st.get("output"))
        return found

    code, info = _get(f"{base}/cm?cmnd=Status%200", timeout)
    if code == 200 and info and "Status" in info:
        st = info.get("Status") or {}
        names = st.get("FriendlyName") or [st.get("DeviceName") or "Tasmota"]
        found = Found(host=hostonly, kind="tasmota", model=str((info.get("StatusFWR") or {}).get("Hardware", "Tasmota")),
                      name=f"Tasmota: {names[0] if isinstance(names, list) else names}", driver="tasmota",
                      extra={"metering": "ENERGY" in json.dumps(info.get("StatusSNS") or {}),
                             "on": str(st.get("Power", 0)) not in ("0", "False")})
        return found
    return None


SHELLY_MODELS.update({"SHSW-PM": "Shelly 1PM", "SHPLG-S": "Shelly Plug S", "SHSW-25": "Shelly 2.5", "SHSW-1": "Shelly 1"})
SHELLY_EM_MODELS = {"SPEM-003CEBEU": "Shelly Pro 3EM", "SPEM-002CEBEU50": "Shelly Pro EM", "S3EM-002CXCEU": "Shelly EM Gen3",
                    "SPEM-003CEBEU120": "Shelly Pro 3EM"}

PROBLEMS = {
    "p1_serial": "Er zit een P1-kabel in, maar de slimme meter stuurt nog niets. Zit de stekker goed in de P1-poort van de meter? Oudere meters (voor 2014) moeten soms eerst door de netbeheerder worden aangezet.",
    "local_api_off": "Zet in de HomeWizard Energy-app bij deze P1-meter de schakelaar 'Lokale API' aan.",
    "shelly_gen1": "Deze oudere Shelly wordt niet ondersteund.",
    "shelly_password": "Op deze Shelly staat een wachtwoord. Zet het uit in de Shelly-app (Instellingen > Authenticatie).",
    "shelly_no_switch": "Deze Shelly heeft geen schakelaar die Zonnestuur kan bedienen.",
}


# ---- mDNS -----------------------------------------------------------------------
def _encode_name(name: str) -> bytes:
    out = b""
    for part in name.strip(".").split("."):
        out += bytes([len(part)]) + part.encode()
    return out + b"\x00"


def build_query(services=SERVICES) -> bytes:
    header = struct.pack("!HHHHHH", 0, 0, len(services), 0, 0, 0)
    body = b"".join(_encode_name(s) + struct.pack("!HH", 12, 1) for s in services)   # PTR, IN
    return header + body


def _read_name(data: bytes, off: int) -> tuple[str, int]:
    labels, jumped, end = [], False, off
    for _ in range(64):
        ln = data[off]
        if ln == 0:
            off += 1
            break
        if ln & 0xC0 == 0xC0:
            ptr = ((ln & 0x3F) << 8) | data[off + 1]
            if not jumped:
                end = off + 2
            off, jumped = ptr, True
            continue
        labels.append(data[off + 1: off + 1 + ln].decode("utf-8", "replace"))
        off += 1 + ln
    return ".".join(labels), (end if jumped else off)


def parse_response(data: bytes) -> list[dict]:
    """Haal uit een mDNS-antwoord per dienst het IP-adres, de poort en de TXT-gegevens."""
    try:
        _id, _flags, qd, an, ns, ar = struct.unpack("!HHHHHH", data[:12])
        off = 12
        for _ in range(qd):
            _, off = _read_name(data, off)
            off += 4
        records = []
        for _ in range(an + ns + ar):
            name, off = _read_name(data, off)
            rtype, _cls, _ttl, rdlen = struct.unpack("!HHIH", data[off:off + 10])
            off += 10
            rdata_off = off
            rec = {"name": name, "type": rtype}
            if rtype == 12:        # PTR
                rec["target"], _ = _read_name(data, rdata_off)
            elif rtype == 33:      # SRV
                _prio, _w, port = struct.unpack("!HHH", data[rdata_off:rdata_off + 6])
                rec["port"] = port
                rec["target"], _ = _read_name(data, rdata_off + 6)
            elif rtype == 1 and rdlen == 4:   # A
                rec["ip"] = socket.inet_ntoa(data[rdata_off:rdata_off + 4])
            elif rtype == 16:      # TXT
                txt, p = {}, rdata_off
                while p < rdata_off + rdlen:
                    ln = data[p]
                    kv = data[p + 1:p + 1 + ln].decode("utf-8", "replace")
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        txt[k] = v
                    p += 1 + ln
                rec["txt"] = txt
            records.append(rec)
            off = rdata_off + rdlen
    except (struct.error, IndexError):
        return []
    # koppel PTR -> SRV -> A
    srv = {r["name"]: r for r in records if r["type"] == 33}
    a = {r["name"]: r["ip"] for r in records if r["type"] == 1 and "ip" in r}
    txt = {r["name"]: r.get("txt", {}) for r in records if r["type"] == 16}
    out = []
    for r in records:
        if r["type"] != 12 or not any(r["name"].endswith(s) for s in SERVICES):
            continue
        inst = r["target"]
        s = srv.get(inst)
        ip = a.get(s["target"]) if s else None
        if ip:
            port = s["port"]
            out.append({"service": r["name"], "host": ip if port == 80 else f"{ip}:{port}", "txt": txt.get(inst, {})})
    return out


def mdns_browse(timeout: float = 3.0) -> list[str]:
    hosts: set[str] = set()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.settimeout(0.3)
        sock.sendto(build_query(), (MDNS_ADDR, MDNS_PORT))
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, _ = sock.recvfrom(9000)
            except socket.timeout:
                continue
            for item in parse_response(data):
                hosts.add(item["host"])
        sock.close()
    except OSError:
        pass
    return sorted(hosts)


# ---- netwerkscan -----------------------------------------------------------------
def local_subnet() -> Optional[ipaddress.IPv4Network]:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 80))          # stuurt niets, bepaalt alleen het eigen adres
        ip = s.getsockname()[0]
        s.close()
        if ip.startswith("127."):
            return None
        return ipaddress.ip_network(f"{ip}/24", strict=False)
    except OSError:
        return None


def _port_open(ip: str, port: int = 80, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


class Scanner:
    """Zoekt op de achtergrond; de app vraagt de voortgang op."""

    def __init__(self, extra_hosts: Optional[list[str]] = None, identify_fn: Callable = identify):
        self.extra_hosts = extra_hosts or []
        self.identify = identify_fn
        self.lock = threading.Lock()
        self.running = False
        self.progress = 0.0
        self.found: dict[str, Found] = {}
        self.started_at = 0.0

    def start(self) -> None:
        with self.lock:
            if self.running:
                return
            self.running, self.progress, self.found, self.started_at = True, 0.0, {}, time.time()
        threading.Thread(target=self._run, daemon=True).start()

    def _add(self, f: Optional[Found]) -> None:
        if f:
            with self.lock:
                self.found[f.host] = f

    def _run(self) -> None:
        try:
            candidates = list(dict.fromkeys(self.extra_hosts + mdns_browse(2.5)))
            self.progress = 0.15
            for h in candidates:
                self._add(self.identify(h))
            net = local_subnet()
            if net:
                ips = [str(ip) for ip in net.hosts()]
                done = 0
                with ThreadPoolExecutor(max_workers=48) as ex:
                    for ip, is_open in zip(ips, ex.map(_port_open, ips)):
                        done += 1
                        self.progress = 0.15 + 0.85 * done / len(ips)
                        if is_open and ip not in self.found:
                            self._add(self.identify(ip))
            try:                                            # P1-kabel in dit kastje zelf
                from .p1serial import probe
                for p in probe(timeout=12):
                    self._add(Found(host=p["port"], kind="p1_serial", name="Slimme meter via P1-kabel", model="P1-kabel",
                                    ok=p["ok"], problem="" if p["ok"] else "p1_serial",
                                    extra={"active_power_w": p["grid_w"] or 0}))
            except Exception:
                pass
        finally:
            self.progress = 1.0
            self.running = False

    def snapshot(self) -> dict:
        with self.lock:
            items = [f.to_dict() | {"problem_text": PROBLEMS.get(f.problem, "")} for f in self.found.values()]
        order = {"p1_serial": -1, "p1": 0, "shelly_em": 1, "shelly": 2, "hw_socket": 3, "tasmota": 4}
        items.sort(key=lambda x: (order.get(x["kind"], 9), x["host"]))
        return {"running": self.running, "progress": round(self.progress, 2), "found": items}
