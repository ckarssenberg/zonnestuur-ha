"""Vangnet voor warm water: een klein script op de Shelly zelf.

Zonnestuur geeft elke paar minuten een hartslag aan de Shelly (KVS-sleutel zs_hb). Hoort de Shelly drie uur
niets van de Box (stroomstoring van de Box, kapotte SD-kaart, stekker eruit), dan neemt het script het over en
zet het de boiler elke dag aan in een vast middagvenster (standaard 12–15 uur). Komt de Box terug, dan stopt
het script vanzelf met sturen. Zo is er altijd warm water, ook als de Box het niet doet.

De Box installeert en werkt het script zelf bij via de lokale RPC-API van de Shelly (Gen2/Gen3/Pro).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from typing import Optional

log = logging.getLogger("zonnestuur.failsafe")

NAME = "zonnestuur-vangnet"
VERSION = 1
SILENT_S = 3 * 3600
HEARTBEAT_S = 300
DEFAULT_WINDOW = (12, 15)

SCRIPT = """// Zonnestuur-vangnet v%(version)d - door de Zonnestuur Box geplaatst, niet aanpassen.
// Hoort deze Shelly %(hours)d uur niets van de Box, dan verwarmt hij de boiler tussen %(start)d en %(end)d uur.
var ID = %(id)d, VAN = %(start)d, TOT = %(end)d, STIL_S = %(silent)d;
var actief = false;
function tik() {
  Shelly.call("KVS.Get", {key: "zs_hb"}, function (r, err) {
    var hb = (err === 0 && r && r.value) ? Number(r.value) : 0;
    var sys = Shelly.getComponentStatus("sys");
    if (!hb || !sys || !sys.unixtime || !sys.time) return;
    if (sys.unixtime - hb < STIL_S) {
      if (actief) { actief = false; print("Zonnestuur Box weer bereikbaar, vangnet uit"); }
      return;
    }
    if (!actief) { actief = true; print("Geen Zonnestuur Box, vangnet aan"); }
    var uur = Number(sys.time.split(":")[0]);
    var aan = uur >= VAN && uur < TOT;
    var sw = Shelly.getComponentStatus("switch:" + ID);
    if (sw && sw.output !== aan) Shelly.call("Switch.Set", {id: ID, on: aan});
  });
}
Timer.set(60000, true, tik);
tik();
"""


def script_code(switch_id: int = 0, window: tuple = DEFAULT_WINDOW) -> str:
    return SCRIPT % {"version": VERSION, "id": int(switch_id), "start": int(window[0]), "end": int(window[1]),
                     "silent": SILENT_S, "hours": SILENT_S // 3600}


def parse_window(text) -> tuple:
    """'12-15' -> (12, 15). Minstens 1 en hoogstens 12 uur, binnen de dag."""
    try:
        a, b = (int(x) for x in str(text).replace("–", "-").split("-", 1))
    except (ValueError, TypeError):
        return DEFAULT_WINDOW
    if not (0 <= a < b <= 24 and 1 <= b - a <= 12):
        return DEFAULT_WINDOW
    return a, b


def wanted(device) -> Optional[tuple]:
    """Welk venster voor dit apparaat, of None als er geen vangnet hoort."""
    if device.driver != "shelly":
        return None
    p = device.params or {}
    on = p.get("failsafe")
    if on is None:
        on = device.kind == "boiler"                        # standaard alleen de boiler: die mag altijd bijverwarmen
    return parse_window(p.get("failsafe_window", "12-15")) if on else None


class ShellyRPC:
    def __init__(self, host: str, timeout: float = 6.0):
        self.base = host if host.startswith("http") else f"http://{host}"
        self.timeout = timeout

    def call(self, method: str, params: Optional[dict] = None):
        body = json.dumps({"id": 1, "method": method, "params": params or {}}).encode()
        req = urllib.request.Request(f"{self.base}/rpc", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode() or "{}")
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"{method}: {data['error'].get('message', data['error'])}")
        return data.get("result", data) if isinstance(data, dict) else data


def install(rpc: ShellyRPC, switch_id: int = 0, window: tuple = DEFAULT_WINDOW) -> str:
    """Script plaatsen of bijwerken. Geeft 'geplaatst', 'bijgewerkt' of 'al goed' terug."""
    marker = f"{VERSION}:{int(switch_id)}:{window[0]}-{window[1]}"
    try:
        cur = (rpc.call("KVS.Get", {"key": "zs_fs_ver"}) or {}).get("value")
    except Exception:
        cur = None
    scripts = (rpc.call("Script.List") or {}).get("scripts", [])
    existing = next((s for s in scripts if s.get("name") == NAME), None)
    if existing and cur == marker and existing.get("running"):
        return "al goed"
    if existing:
        sid = existing["id"]
        try:
            rpc.call("Script.Stop", {"id": sid})
        except Exception:
            pass
    else:
        sid = rpc.call("Script.Create", {"name": NAME})["id"]
    code = script_code(switch_id, window)
    for i in range(0, len(code), 1000):
        rpc.call("Script.PutCode", {"id": sid, "code": code[i:i + 1000], "append": i > 0})
    rpc.call("Script.SetConfig", {"id": sid, "config": {"enable": True}})
    rpc.call("Script.Start", {"id": sid})
    rpc.call("KVS.Set", {"key": "zs_fs_ver", "value": marker})
    heartbeat(rpc)
    return "bijgewerkt" if existing else "geplaatst"


def remove(rpc: ShellyRPC) -> bool:
    scripts = (rpc.call("Script.List") or {}).get("scripts", [])
    existing = next((s for s in scripts if s.get("name") == NAME), None)
    if not existing:
        return False
    try:
        rpc.call("Script.Stop", {"id": existing["id"]})
    except Exception:
        pass
    rpc.call("Script.Delete", {"id": existing["id"]})
    try:
        rpc.call("KVS.Delete", {"key": "zs_fs_ver"})
    except Exception:
        pass
    return True


def heartbeat(rpc: ShellyRPC) -> None:
    rpc.call("KVS.Set", {"key": "zs_hb", "value": int(time.time())})


class Guard:
    """Houdt per Shelly het vangnet bij: plaatsen bij het starten, daarna hartslagen. Fouten stoppen niets."""

    def __init__(self):
        self.status: dict[str, str] = {}                   # device id -> leesbare stand
        self._installed: dict[str, tuple] = {}
        self._last_hb: dict[str, float] = {}
        self._lock = threading.Lock()

    def sync(self, devices) -> None:
        todo, drop = [], []
        with self._lock:
            keep = {d.id for d in devices if wanted(d) is not None}
            for dev_id, key in list(self._installed.items()):
                if dev_id not in keep:                      # apparaat weg of vangnet uit: script van de Shelly halen,
                    drop.append(key[0])                     # anders neemt het straks ongevraagd de boiler over
                    del self._installed[dev_id]
                    self.status.pop(dev_id, None)
            for d in devices:
                win = wanted(d)
                key = (d.host, int(d.switch_id or 0), win)
                if win is None:
                    self.status.pop(d.id, None)
                    continue
                if self._installed.get(d.id) != key:
                    todo.append((d, win, key))
        if todo or drop:
            threading.Thread(target=self._install_all, args=(todo, drop), daemon=True, name="vangnet").start()

    def _install_all(self, todo, drop=()) -> None:
        for host in drop:
            if any(k[0] == host for k in self._installed.values()):
                continue                                    # zelfde Shelly nog in gebruik door een ander apparaat
            try:
                remove(ShellyRPC(host))
                log.info("Vangnet van %s gehaald", host)
            except Exception as exc:
                log.warning("Vangnet weghalen van %s lukte niet: %s", host, exc)
        for d, win, key in todo:
            try:
                res = install(ShellyRPC(d.host), int(d.switch_id or 0), win)
                with self._lock:
                    self._installed[d.id] = key
                    self.status[d.id] = f"vangnet {res}: bij storing van de Box verwarmt de Shelly van {win[0]} tot {win[1]} uur"
                log.info("%s: vangnet %s", d.name, res)
            except Exception as exc:
                with self._lock:
                    self.status[d.id] = f"vangnet niet geplaatst: {exc}"
                log.warning("%s: vangnet plaatsen lukte niet: %s", d.name, exc)

    def beat(self, devices) -> None:
        now = time.time()
        for d in devices:
            if d.id not in self._installed or now - self._last_hb.get(d.id, 0) < HEARTBEAT_S:
                continue
            self._last_hb[d.id] = now
            try:
                heartbeat(ShellyRPC(d.host, timeout=3))
            except Exception as exc:
                log.debug("%s: hartslag mislukt: %s", d.name, exc)
