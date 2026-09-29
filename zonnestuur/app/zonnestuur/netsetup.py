"""Wifi instellen zonder kabel, voor de Zonnestuur Box.

Heeft de Box geen netwerk (geen kabel, nog geen wifi, of de wifi is weg), dan maakt hij na anderhalve minuut een
eigen wifi-netwerk "Zonnestuur-instellen". Verbind je telefoon daarmee: de instelpagina gaat vanzelf open
(captive portal), je kiest je eigen wifi en tikt het wachtwoord in. Daarna verbindt de Box met je wifi en is hij
bereikbaar op http://zonnestuur.local.

Twee delen:
  - de netwerkdienst (root, `python3 -m zonnestuur.netsetup`): bewaakt het netwerk en bedient NetworkManager (nmcli);
  - de app (gewone gebruiker): toont de instelpagina en geeft de keuze door via bestanden in STATE_DIR.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("zonnestuur.netsetup")

STATE_DIR = Path(os.environ.get("ZONNESTUUR_NET_DIR", "/var/lib/zonnestuur/net"))
HOTSPOT_SSID = "Zonnestuur-instellen"
HOTSPOT_CON = "Zonnestuur-instellen"
HOTSPOT_IP = "10.42.0.1"
OFFLINE_S = 90          # zo lang zonder netwerk voordat de instel-wifi aangaat
RETRY_SAVED_S = 300     # met een bewaarde wifi: zo vaak opnieuw proberen terwijl de instel-wifi aan staat
BOOT_GRACE_S = 60       # na het opstarten eerst NetworkManager de tijd geven


# ---------------------------------------------------------------- app-kant

def _read(name: str) -> Optional[dict]:
    try:
        return json.loads((STATE_DIR / name).read_text())
    except (OSError, ValueError):
        return None


def _write(name: str, data: dict, mode: int = 0o644) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_DIR / (name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.chmod(tmp, mode)
    tmp.replace(STATE_DIR / name)


def _remove(name: str) -> None:
    try:
        (STATE_DIR / name).unlink()
    except OSError:
        pass


def portal_active() -> bool:
    return (STATE_DIR / "hotspot.json").exists()


def portal_view() -> dict:
    st = _read("hotspot.json")
    res = _read("result.json") or {}
    return {"portal": st is not None, "ssid": HOTSPOT_SSID, "networks": (st or {}).get("networks", []),
            "error": (st or {}).get("error", ""), "result": res}


def submit(ssid: str, password: str) -> None:
    ssid = (ssid or "").strip()
    if not ssid or len(ssid) > 32:
        raise ValueError("Kies een wifi-netwerk")
    if password and not 8 <= len(password) <= 63:
        raise ValueError("Een wifi-wachtwoord is 8 tot 63 tekens")
    _write("request.json", {"ssid": ssid, "password": password or "", "at": time.time()}, mode=0o600)
    _write("result.json", {"state": "connecting", "ssid": ssid, "at": time.time()})


# ---------------------------------------------------------------- NetworkManager

_SPLIT = re.compile(r"(?<!\\):")


def _fields(line: str) -> list[str]:
    return [p.replace("\\:", ":").replace("\\\\", "\\") for p in _SPLIT.split(line)]


class NMCLI:
    """Dunne laag over nmcli; in tests vervangen door een nagebootst netwerk."""

    def run(self, *args: str, timeout: float = 60) -> tuple[int, str]:
        try:
            p = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 1, str(exc)

    def devices(self) -> list[dict]:
        rc, out = self.run("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device")
        rows = []
        for line in out.splitlines() if rc == 0 else []:
            f = _fields(line)
            if len(f) >= 4:
                rows.append({"device": f[0], "type": f[1], "state": f[2], "connection": f[3]})
        return rows

    def wifi_device(self) -> Optional[str]:
        return next((d["device"] for d in self.devices() if d["type"] == "wifi"), None)

    def online(self) -> bool:
        return any(d["type"] in ("ethernet", "wifi") and d["state"].startswith("connected")
                   and d["connection"] != HOTSPOT_CON for d in self.devices())

    def saved_wifi(self) -> list[str]:
        rc, out = self.run("-t", "-f", "NAME,TYPE", "connection", "show")
        names = []
        for line in out.splitlines() if rc == 0 else []:
            f = _fields(line)
            if len(f) >= 2 and f[1] in ("802-11-wireless", "wifi") and f[0] != HOTSPOT_CON:
                names.append(f[0])
        return names

    def scan(self, dev: str) -> list[dict]:
        rc, out = self.run("-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes", "ifname", dev,
                           timeout=30)
        best: dict[str, dict] = {}
        for line in out.splitlines() if rc == 0 else []:
            f = _fields(line)
            if len(f) < 3 or not f[0] or f[0] == HOTSPOT_SSID:
                continue
            try:
                sig = int(f[1])
            except ValueError:
                sig = 0
            if f[0] not in best or sig > best[f[0]]["signal"]:
                best[f[0]] = {"ssid": f[0], "signal": sig, "secure": bool(f[2] and f[2] != "--")}
        return sorted(best.values(), key=lambda n: -n["signal"])[:25]

    def hotspot_up(self, dev: str) -> bool:
        self.run("connection", "delete", HOTSPOT_CON)
        rc, out = self.run("connection", "add", "type", "wifi", "ifname", dev, "con-name", HOTSPOT_CON,
                           "autoconnect", "no", "ssid", HOTSPOT_SSID, "802-11-wireless.mode", "ap",
                           "802-11-wireless.band", "bg", "ipv4.method", "shared",
                           "ipv4.addresses", f"{HOTSPOT_IP}/24", "ipv6.method", "disabled")
        if rc != 0:
            log.warning("instel-wifi aanmaken mislukt: %s", out.strip())
            return False
        rc, out = self.run("connection", "up", HOTSPOT_CON)
        if rc != 0:
            log.warning("instel-wifi starten mislukt: %s", out.strip())
        return rc == 0

    def hotspot_down(self) -> None:
        self.run("connection", "down", HOTSPOT_CON)

    def connect(self, dev: str, ssid: str, password: str) -> tuple[bool, str]:
        args = ["--wait", "45", "device", "wifi", "connect", ssid]
        if password:
            args += ["password", password]
        args += ["ifname", dev]
        rc, out = self.run(*args, timeout=60)
        if rc == 0:
            return True, ""
        low = out.lower()
        if "secrets were required" in low or "802-1x" in low or "password" in low:
            return False, "Het wachtwoord klopt niet"
        if "no network with ssid" in low:
            return False, f"Netwerk \"{ssid}\" niet gevonden; staat de Box dicht genoeg bij de router?"
        return False, "Verbinden lukte niet: " + (out.strip().splitlines() or ["onbekende fout"])[-1][:160]

    def reconnect_saved(self, dev: str, wait_s: float = 35) -> bool:
        self.run("device", "connect", dev, timeout=wait_s + 5)
        end = time.time() + wait_s
        while time.time() < end:
            if self.online():
                return True
            time.sleep(2)
        return False


# ---------------------------------------------------------------- netwerkdienst

class NetSetup:
    def __init__(self, net=None, clock=time.time, sleep=time.sleep):
        self.net = net or NMCLI()
        self.clock, self.sleep = clock, sleep
        self.started = clock()
        self.offline_since: Optional[float] = None
        self.hotspot_since: Optional[float] = None
        self.last_retry = 0.0

    def _start_hotspot(self, dev: str, error: str = "") -> None:
        networks = self.net.scan(dev)                      # eerst zoeken: in instel-modus kan de wifi-chip niet scannen
        if self.net.hotspot_up(dev):
            self.hotspot_since = self.last_retry = self.clock()
            _write("hotspot.json", {"since": self.hotspot_since, "networks": networks, "error": error})
            log.info("Geen netwerk: instel-wifi \"%s\" staat aan", HOTSPOT_SSID)

    def _stop_hotspot(self) -> None:
        self.net.hotspot_down()
        self.hotspot_since = None
        _remove("hotspot.json")

    def _handle_request(self, dev: str) -> None:
        req = _read("request.json")
        _remove("request.json")
        if not req or not req.get("ssid"):
            return
        ssid = req["ssid"]
        log.info("Verbinden met wifi \"%s\"", ssid)
        self._stop_hotspot()
        ok, msg = self.net.connect(dev, ssid, req.get("password", ""))
        if ok:
            _write("result.json", {"state": "ok", "ssid": ssid, "at": self.clock()})
            self.offline_since = None
            log.info("Verbonden met \"%s\"", ssid)
        else:
            _write("result.json", {"state": "error", "ssid": ssid, "message": msg, "at": self.clock()})
            self._start_hotspot(dev, msg)

    def step(self) -> None:
        dev = self.net.wifi_device()
        now = self.clock()
        if dev and (STATE_DIR / "request.json").exists():
            return self._handle_request(dev)
        online = self.net.online()
        if online:
            self.offline_since = None
            if self.hotspot_since is not None:
                self._stop_hotspot()                        # bijv. toch een kabel ingestoken
            return
        if not dev:
            return
        if self.hotspot_since is not None:
            # bewaarde wifi weer terug (router opnieuw opgestart)? af en toe proberen
            if now - self.last_retry >= RETRY_SAVED_S and self.net.saved_wifi():
                self.last_retry = now
                self.net.hotspot_down()
                if self.net.reconnect_saved(dev):
                    log.info("Bewaarde wifi is terug; instel-wifi uit")
                    self.hotspot_since = None
                    _remove("hotspot.json")
                else:
                    st = _read("hotspot.json") or {}
                    self._start_hotspot(dev, st.get("error", ""))
            return
        if self.offline_since is None:
            self.offline_since = now
        if now - self.offline_since >= OFFLINE_S and now - self.started >= BOOT_GRACE_S:
            self._start_hotspot(dev)

    def run(self) -> None:
        _remove("hotspot.json")                             # oude toestand van vóór een herstart
        while True:
            try:
                self.step()
            except Exception:                               # de dienst mag nooit stoppen
                log.exception("netwerkcontrole mislukt")
            self.sleep(5)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:                                                    # de app (andere gebruiker) moet er zijn keuze in kunnen zetten
        st = STATE_DIR.parent.stat()
        os.chown(STATE_DIR, st.st_uid, st.st_gid)
        os.chmod(STATE_DIR, 0o750)
    except OSError:
        pass
    NetSetup().run()


if __name__ == "__main__":
    main()
