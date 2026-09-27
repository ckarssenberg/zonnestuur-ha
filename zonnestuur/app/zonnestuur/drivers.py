"""Koppelingen met apparaten van veel merken.

Rechtstreeks (lokaal, zonder cloud):
  meters:      HomeWizard P1-meter, Shelly EM / Pro 3EM
  schakelaars: Shelly Gen1/Gen2/Gen3, HomeWizard Energy Socket, Tasmota
Via Home Assistant (alles wat daar gekoppeld is):
  meters:      elke vermogenssensor (W of kW), of aparte sensoren voor afname en teruglevering
  apparaten:   switch / input_boolean / light     -> aan/uit
               water_heater (boiler, warmtepompboiler) -> doeltemperatuur verhogen
               climate (warmtepomp, airco)          -> doeltemperatuur verhogen
               number (laadstroom van laadpaal/auto) -> traploos regelen
               + eventueel een switch om het laden te starten/stoppen

Elke schakel-driver kent: status() -> SwitchStatus, set(on).
Traploze drivers kennen ook: set_power(watt) en de eigenschappen min_w, max_w, step_w.
"""
from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from typing import Optional

from .adapters import DeviceError, NotReady, HomeWizardP1, MeterReading, ShellySwitch, SwitchStatus, http_get_json


def _request(url: str, method: str = "GET", body: Optional[dict] = None, headers: Optional[dict] = None,
             timeout: float = 4.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except Exception as exc:
        raise DeviceError(f"{url}: {exc}") from exc


def _base(host: str) -> str:
    return host.rstrip("/") if host.startswith("http") else f"http://{host}"


# ---------------------------------------------------------------- HomeWizard
class HomeWizardSocket:
    """HomeWizard Energy Socket (HWE-SKT), lokale API v1."""

    def __init__(self, host: str):
        self.base = _base(host)

    def status(self) -> SwitchStatus:
        st = http_get_json(f"{self.base}/api/v1/state")
        data = http_get_json(f"{self.base}/api/v1/data")
        return SwitchStatus(bool(st.get("power_on")), float(data.get("active_power_w") or 0.0),
                            _kwh_to_wh(data.get("total_power_import_kwh")))

    def set(self, on: bool) -> None:
        _request(f"{self.base}/api/v1/state", "PUT", {"power_on": bool(on)})


def _kwh_to_wh(v) -> Optional[float]:
    try:
        return float(v) * 1000
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- Shelly
class ShellyGen1Switch:
    """Shelly van de eerste generatie (Shelly 1PM, Plug S, 2.5 ...)."""

    def __init__(self, host: str, switch_id: int = 0):
        self.base, self.switch_id = _base(host), switch_id

    def status(self) -> SwitchStatus:
        st = http_get_json(f"{self.base}/status")
        relays, meters = st.get("relays") or [{}], st.get("meters") or [{}]
        relay = relays[min(self.switch_id, len(relays) - 1)]
        meter = meters[min(self.switch_id, len(meters) - 1)] if meters else {}
        total = meter.get("total")     # watt-minuten
        return SwitchStatus(bool(relay.get("ison")), float(meter.get("power") or 0.0),
                            None if total is None else float(total) / 60)

    def set(self, on: bool) -> None:
        http_get_json(f"{self.base}/relay/{self.switch_id}?turn={'on' if on else 'off'}")


class ShellyEMMeter:
    """Shelly Pro 3EM / Pro EM / EM Gen3 als netmeter (stroomtangen in de meterkast)."""

    def __init__(self, host: str, invert: bool = False):
        self.base, self.invert = _base(host), invert

    def read(self) -> MeterReading:
        w = None
        try:
            st = http_get_json(f"{self.base}/rpc/EM.GetStatus?id=0")
            w = st.get("total_act_power")
        except DeviceError:
            pass
        if w is None:
            st = http_get_json(f"{self.base}/rpc/EM1.GetStatus?id=0")
            w = st.get("act_power")
        if w is None:
            raise DeviceError("Shelly EM gaf geen vermogen terug")
        w = float(w)
        return MeterReading(-w if self.invert else w, None, None)


# ---------------------------------------------------------------- Tasmota
class TasmotaSwitch:
    """Stekkers en relais met Tasmota-firmware (veel merken: Nous, Blitzwolf, Sonoff ...)."""

    def __init__(self, host: str, switch_id: int = 0):
        self.base = _base(host)
        self.index = "" if switch_id == 0 else str(switch_id + 1)

    def _cmd(self, cmd: str) -> dict:
        return http_get_json(f"{self.base}/cm?cmnd={urllib.parse.quote(cmd)}")

    def status(self) -> SwitchStatus:
        p = self._cmd(f"Power{self.index}")
        on = str(p.get(f"POWER{self.index}", p.get("POWER", "OFF"))).upper() == "ON"
        power, energy = 0.0, None
        try:
            e = self._cmd("Status 8").get("StatusSNS", {}).get("ENERGY", {})
            power = float(e.get("Power") or 0.0)
            energy = _kwh_to_wh(e.get("Total"))
        except DeviceError:
            pass
        return SwitchStatus(on, power, energy)

    def set(self, on: bool) -> None:
        self._cmd(f"Power{self.index} {'On' if on else 'Off'}")


# ---------------------------------------------------------------- Home Assistant
class HomeAssistant:
    """Verbinding met Home Assistant via de REST-API en een langlevend toegangstoken."""

    def __init__(self, url: str, token: str, timeout: float = 5.0):
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def ping(self) -> bool:
        r = _request(f"{self.url}/api/", headers=self._h(), timeout=self.timeout)
        return "message" in r

    def states(self) -> list[dict]:
        r = _request(f"{self.url}/api/states", headers=self._h(), timeout=self.timeout)
        return r if isinstance(r, list) else []

    def state(self, entity_id: str) -> dict:
        return _request(f"{self.url}/api/states/{entity_id}", headers=self._h(), timeout=self.timeout)

    def call(self, domain: str, service: str, data: dict) -> None:
        _request(f"{self.url}/api/services/{domain}/{service}", "POST", data, headers=self._h(), timeout=self.timeout)

    def number(self, entity_id: str) -> Optional[float]:
        st = self.state(entity_id)
        try:
            v = float(st.get("state"))
        except (TypeError, ValueError):
            return None
        unit = (st.get("attributes") or {}).get("unit_of_measurement", "")
        return v * 1000 if unit == "kW" else v


class HAMeter:
    """Netmeter uit Home Assistant: één sensor (positief = afname), of afname- en terugleversensor apart."""

    def __init__(self, ha: HomeAssistant, entity: str = "", import_entity: str = "", export_entity: str = "",
                 invert: bool = False):
        self.ha, self.entity, self.imp, self.exp, self.invert = ha, entity, import_entity, export_entity, invert

    def read(self) -> MeterReading:
        if self.entity:
            w = self.ha.number(self.entity)
        else:
            a, b = self.ha.number(self.imp), self.ha.number(self.exp)
            w = None if a is None or b is None else a - b
        if w is None:
            raise DeviceError("Home Assistant-sensor heeft geen geldige waarde")
        return MeterReading(-w if self.invert else w, None, None)


class HASwitch:
    """Aan/uit via Home Assistant (switch, input_boolean, light), met optionele vermogenssensor."""

    def __init__(self, ha: HomeAssistant, entity: str, power_entity: str = ""):
        self.ha, self.entity, self.power_entity = ha, entity, power_entity
        self.domain = entity.split(".", 1)[0]

    def status(self) -> SwitchStatus:
        st = self.ha.state(self.entity)
        power = self.ha.number(self.power_entity) if self.power_entity else None
        return SwitchStatus(st.get("state") == "on", float(power or 0.0), None)

    def set(self, on: bool) -> None:
        domain = self.domain if self.domain in ("switch", "input_boolean", "light", "fan") else "homeassistant"
        self.ha.call(domain, "turn_on" if on else "turn_off", {"entity_id": self.entity})


class HASetpointBoost:
    """Warmtepomp, warmtepompboiler of airco via Home Assistant: 'aan' = doeltemperatuur verhogen.

    Zo slaat het apparaat warmte op (in het water of in de vloer) als er zon is, zonder dat
    Zonnestuur de compressor zelf hoeft te schakelen. 'Uit' zet de normale temperatuur terug.
    """

    def __init__(self, ha: HomeAssistant, entity: str, normal_temp: float, boost_temp: float, power_entity: str = ""):
        self.ha, self.entity, self.power_entity = ha, entity, power_entity
        self.domain = entity.split(".", 1)[0]          # climate, water_heater of number (bijv. warmwater-doeltemperatuur)
        self.normal, self.boost = normal_temp, boost_temp

    def status(self) -> SwitchStatus:
        st = self.ha.state(self.entity)
        target = st.get("state") if self.domain in ("number", "input_number") else (st.get("attributes") or {}).get("temperature")
        try:
            float(target)
        except (TypeError, ValueError):
            target = None
        on = target is not None and float(target) >= self.boost - 0.1
        power = self.ha.number(self.power_entity) if self.power_entity else None
        return SwitchStatus(on, float(power or 0.0), None)

    def set(self, on: bool) -> None:
        t = self.boost if on else self.normal
        if self.domain in ("number", "input_number"):
            self.ha.call(self.domain, "set_value", {"entity_id": self.entity, "value": t})
        else:
            self.ha.call(self.domain, "set_temperature", {"entity_id": self.entity, "temperature": t})


class HAStartButton:
    """Witgoed via Home Assistant (bijv. Miele, Home Connect): het programma starten als de zon schijnt.

    Jij vult de machine en zet hem op 'start op afstand'. Zonnestuur drukt op de startknop zodra er genoeg
    overschot is. Uitzetten doet Zonnestuur nooit: een gestart programma loopt altijd af.
    """

    RUN_HOURS = 4.0

    def __init__(self, ha: HomeAssistant, button_entity: str, remote_entity: str = "", power_entity: str = ""):
        self.ha, self.button, self.remote, self.power_entity = ha, button_entity, remote_entity, power_entity
        self.started_at: Optional[float] = None

    def _armed(self) -> bool:
        if self.ha.state(self.button).get("state") in ("unavailable", "unknown", None):
            return False
        return not self.remote or self.ha.state(self.remote).get("state") == "on"

    def status(self) -> SwitchStatus:
        armed = self._armed()
        power = self.ha.number(self.power_entity) if self.power_entity else None
        running = self.started_at is not None and time.monotonic() - self.started_at < self.RUN_HOURS * 3600 and not armed
        if running:
            return SwitchStatus(True, float(power or 0.0), None)
        self.started_at = None
        if not armed:
            raise NotReady("wacht tot je hem klaarzet met start op afstand")
        return SwitchStatus(False, 0.0, None)

    def set(self, on: bool) -> None:
        if not on:
            return                                      # een lopend programma nooit afbreken
        if not self._armed():
            raise NotReady("niet klaargezet")
        self.ha.call("button", "press", {"entity_id": self.button})
        self.started_at = time.monotonic()


class HACurrentControl:
    """Laadpaal of auto via Home Assistant: laadstroom (A) traploos instellen, plus optioneel laden aan/uit.

    Vermogen = stroom × spanning × fasen. Onder de minimale stroom (meestal 6 A) stopt het laden.
    """

    modulating = True

    def __init__(self, ha: HomeAssistant, current_entity: str, switch_entity: str = "", power_entity: str = "",
                 phases: int = 1, volts: float = 230.0, min_a: float = 6.0, max_a: float = 16.0):
        self.ha, self.cur, self.sw, self.power_entity = ha, current_entity, switch_entity, power_entity
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

    def status(self) -> SwitchStatus:
        on = True
        if self.sw:
            on = self.ha.state(self.sw).get("state") == "on"
        power = self.ha.number(self.power_entity) if self.power_entity else None
        return SwitchStatus(on, float(power or 0.0), None)

    def set(self, on: bool) -> None:
        if self.sw:
            domain = self.sw.split(".", 1)[0]
            self.ha.call(domain if domain != "button" else "button", "turn_on" if on else "turn_off", {"entity_id": self.sw})
        elif not on:
            self.ha.call("number", "set_value", {"entity_id": self.cur, "value": 0})

    def set_power(self, watt: float) -> None:
        amps = max(self.min_a, min(self.max_a, math.floor(watt / self.w_per_a)))
        amps = int(amps) if float(amps).is_integer() else amps
        self.ha.call("number", "set_value", {"entity_id": self.cur, "value": amps})


# ---------------------------------------------------------------- fabrieken
def make_meter(cfg):
    m = getattr(cfg, "meter", None) or {}
    kind = m.get("driver") or "homewizard"
    if kind == "homewizard":
        return HomeWizardP1(m.get("host") or cfg.p1_host)
    if kind == "shelly_em":
        return ShellyEMMeter(m.get("host") or cfg.p1_host, bool(m.get("invert")))
    if kind == "ha":
        return HAMeter(ha_client(cfg), m.get("entity", ""), m.get("import_entity", ""), m.get("export_entity", ""),
                       bool(m.get("invert")))
    raise ValueError(f"Onbekende meter: {kind}")


def make_switch(cfg, d):
    drv, p = d.driver, d.params or {}
    if drv == "shelly":
        return ShellySwitch(d.host, d.switch_id)
    if drv == "shelly_gen1":
        return ShellyGen1Switch(d.host, d.switch_id)
    if drv == "homewizard_socket":
        return HomeWizardSocket(d.host)
    if drv == "tasmota":
        return TasmotaSwitch(d.host, d.switch_id)
    ha = ha_client(cfg)
    if drv == "ha_switch":
        return HASwitch(ha, p["entity"], p.get("power_entity", ""))
    if drv == "ha_setpoint":
        return HASetpointBoost(ha, p["entity"], float(p.get("normal_temp", 50)), float(p.get("boost_temp", 60)),
                               p.get("power_entity", ""))
    if drv == "ha_start_button":
        return HAStartButton(ha, p["button_entity"], p.get("remote_entity", ""), p.get("power_entity", ""))
    if drv == "ha_current":
        return HACurrentControl(ha, p["current_entity"], p.get("switch_entity", ""), p.get("power_entity", ""),
                                int(p.get("phases", 1)), float(p.get("volts", 230)), float(p.get("min_a", 6)),
                                float(p.get("max_a", 16)))
    raise ValueError(f"Onbekende koppeling: {drv}")


def ha_client(cfg) -> HomeAssistant:
    from .config import effective_ha
    ha = effective_ha(cfg)
    if not ha.get("url") or not ha.get("token"):
        raise ValueError("Home Assistant is nog niet gekoppeld (adres en token ontbreken)")
    return HomeAssistant(ha["url"], ha["token"])


# ---------------------------------------------------------------- Home Assistant: wat kan Zonnestuur gebruiken?
# Wat Zonnestuur nooit voorstelt: instellingen van camera's en sensoren, rolluiken, verlichting-extra's
_NOT_A_LOAD = ("camera", "privacy", "detectie", "detection", "watermerk", "watermark", "mute", "volume", "indicator",
               "permit join", "omdraaien", "flip", "siren", "sirene", "slaapstand", "audio", "status licht", "niet storen",
               "do not disturb", "kinderslot", "child lock", "wake sound", "rolluik", "screen", "zonwering", "cover",
               "opname", "recording", "firmware", "update", "led", "motion", "beweging")
_METER_WORDS = ("p1", "dsmr", "grid", "net_", "meter", "tibber", "pulse", "homewizard", "hoofd", "mains", "slimme meter",
                "smart meter", "afname", "import", "levering", "consumption")
_EXPORT_WORDS = ("productie", "production", "export", "teruglever", "returned", "injection", "feed")
_HOT_WATER_WORDS = ("hotwater", "hot_water", "warmwater", "warm_water", "tapwater", "dhw", "boiler", "sanitary")


def _text(eid: str, name: str) -> str:
    return f"{eid} {name}".lower().replace("_", " ") + " " + eid.lower()


def _is_power(s: dict) -> bool:
    a = s.get("attributes") or {}
    return a.get("device_class") == "power" or a.get("unit_of_measurement") in ("W", "kW")


def ha_candidates(states: list[dict]) -> dict:
    """Groepeer HA-entiteiten in wat Zonnestuur ermee kan: meters en stuurbare apparaten, met een voorgestelde soort.

    De meest waarschijnlijke komen bovenaan; ruis (camera-instellingen, rolluiken, onbeschikbare dingen) valt weg.
    """
    power_sensors, devices, price_sensors = [], [], []
    by_id = {s.get("entity_id"): s for s in states}
    charging_switches = [e for e in by_id if e.startswith("switch.") and e.endswith("_charging")]
    for s in states:
        eid = s.get("entity_id", "")
        dom = eid.split(".", 1)[0]
        a = s.get("attributes") or {}
        name = a.get("friendly_name") or eid
        unit = a.get("unit_of_measurement", "")
        t = _text(eid, name)
        if s.get("state") in ("unavailable",) and dom not in ("sensor", "button"):
            continue
        if dom == "sensor" and "/kwh" in unit.lower().replace(" ", "") and any(c in unit.lower() for c in ("€", "eur", "ct")):
            price_sensors.append({"entity": eid, "name": name, "unit": unit, "state": s.get("state")})
        elif dom == "sensor" and _is_power(s):
            score = 2 * any(w in t for w in _METER_WORDS) - any(w in t for w in ("dimmer", "lamp", "licht", "light", "max ", "min ", "gemiddeld", "average"))
            power_sensors.append({"entity": eid, "name": name, "unit": unit, "state": s.get("state"), "score": score,
                                  "export": any(w in t for w in _EXPORT_WORDS)})
        elif dom == "button" and (eid.endswith(("_starten", "_start", "_start_program")) or name.lower().endswith((" starten", " start"))):
            remote = _match_remote_start(eid, by_id)
            if not remote:
                continue                                  # zonder 'start op afstand' is het vaak geen witgoed
            dev_name = name[: -len(" starten")] if name.lower().endswith(" starten") else name
            devices.append({"driver": "ha_start_button", "button_entity": eid, "remote_entity": remote, "name": dev_name,
                            "kind": "generic", "power_w": 1200, "power_entity": _match_power(eid, by_id), "score": 3})
        elif dom == "switch":
            if any(w in t for w in _NOT_A_LOAD):
                continue
            kind = _guess_kind(eid, name)
            pw = _match_power(eid, by_id)
            devices.append({"driver": "ha_switch", "entity": eid, "name": name, "kind": kind, "power_entity": pw,
                            "score": (2 if kind != "generic" else 0) + (1 if pw else 0),
                            "warning": "Circulatiepomp: niet laten schakelen door Zonnestuur" if "pomp" in t and "warmtepomp" not in t else ""})
        elif dom == "water_heater":
            devices.append({"driver": "ha_setpoint", "entity": eid, "name": name, "kind": "boiler",
                            "normal_temp": a.get("temperature") or 50, "boost_temp": min(65, (a.get("temperature") or 50) + 10),
                            "power_entity": _match_power(eid, by_id), "score": 3})
        elif dom == "climate":
            if any(w in t for w in ("ventilat", "ventilation", "fan", "ventilator", "airflow")):
                continue                                 # ventilatie-unit: geen warmte om op te slaan
            temp = a.get("temperature") or 20
            devices.append({"driver": "ha_setpoint", "entity": eid, "name": name, "kind": "heatpump",
                            "normal_temp": temp, "boost_temp": round(float(temp) + 1.5, 1), "power_entity": _match_power(eid, by_id),
                            "score": 3})
        elif dom == "number" and (unit in ("°C", "°F") or a.get("device_class") == "temperature") and any(w in t for w in _HOT_WATER_WORDS) and "max" not in t and "booster" not in t:
            try:
                temp = float(s.get("state"))
            except (TypeError, ValueError):
                temp = 50.0
            devices.append({"driver": "ha_setpoint", "entity": eid, "name": name, "kind": "boiler", "normal_temp": temp,
                            "boost_temp": min(float(a.get("max", 65) or 65), temp + 10), "power_entity": _match_power(eid, by_id),
                            "score": 3})
        elif dom == "number" and (unit == "A" or any(w in eid for w in ("charging_current", "charge_current", "charging_amps",
                                                                          "laadstroom", "available_current"))):
            if any(w in t for w in ("3 to 1", "phase switch", "fallback", "offline")):
                continue                                  # instellingen, geen laadstroom
            zaptec = "available_current" in eid or "zaptec" in t
            sw = _match_switch(eid, by_id) or (charging_switches[0] if len(charging_switches) == 1 else "")
            devices.append({"driver": "ha_current", "current_entity": eid, "name": name, "kind": "ev",
                            "min_a": a.get("min", 6) if (a.get("min") or 0) >= 6 else 6, "max_a": min(16, a.get("max", 16) or 16),
                            "switch_entity": sw, "power_entity": _match_power(eid, by_id), "score": 4,
                            # Zaptec: laadstroom niet vaker dan eens per 15 minuten aanpassen (advies van Zaptec)
                            "min_interval_s": 900 if zaptec else 30})
    used = {d.get("switch_entity") for d in devices if d["driver"] == "ha_current"}
    devices = [d for d in devices if not (d["driver"] == "ha_switch" and d["entity"] in used)]   # laadschakelaar hoort bij de laadpaal
    # Aan/uit-knop van witgoed met een startknop: aanzetten start geen programma, dus weglaten
    starters = {_stem(d["button_entity"]) for d in devices if d["driver"] == "ha_start_button"}
    devices = [d for d in devices if not (d["driver"] == "ha_switch" and _stem(d["entity"]) in starters)]
    # Meter met aparte sensor voor teruglevering (bijv. Tibber Pulse: 'power' en 'stroomproductie')
    for p in power_sensors:
        if not p["export"]:
            prefix = p["entity"].rsplit("_", 1)[0]
            twin = next((q for q in power_sensors if q["export"] and q["entity"].startswith(prefix.split(".", 1)[0] + ".")
                         and _common(q["entity"], p["entity"]) >= 12), None)
            p["export_entity"] = twin["entity"] if twin else ""
    power_sensors.sort(key=lambda p: (-p["score"], p["export"], p["name"]))
    devices.sort(key=lambda d: (-d["score"], d["name"]))
    # Laadpalen die Home Assistant wel ziet maar niet kan sturen (bijv. Zaptec via de Tibber-koppeling)
    readonly = []
    has_control = any(d["driver"] == "ha_current" for d in devices)
    if not has_control:
        for eid, st in by_id.items():
            a = st.get("attributes") or {}
            if not eid.startswith("binary_sensor.") or a.get("device_class") != "battery_charging":
                continue
            prefix = eid.split(".", 1)[1].rsplit("_", 1)[0]
            siblings = [e for e in by_id if e.startswith("sensor." + prefix + "_")]
            if not any(("charge_current" in e or "grid_phases" in e or "laadstroom" in e) for e in siblings):
                continue                                  # waarschijnlijk een auto, geen laadpaal
            name = (a.get("friendly_name") or prefix).rsplit(" ", 1)[0]
            readonly.append({"name": name, "brand": "zaptec" if any("fallback_current" in e for e in siblings) else "",
                             "via": "een andere koppeling, zoals Tibber"})
    return {"power_sensors": power_sensors, "devices": devices, "price_sensors": price_sensors, "readonly_chargers": readonly}


def _common(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _stem(eid: str) -> str:
    base = eid.split(".", 1)[1]
    for suffix in ("_start_program", "_starten", "_start", "_charging_current", "_max_charging_current", "_current", "_switch", "_charging", "_power",
                   "_hot_water", "_water_heater"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def _match_power(eid: str, by_id: dict) -> str:
    stem = _stem(eid)
    for cand, s in by_id.items():
        if cand.startswith("sensor.") and stem and stem in cand:
            a = s.get("attributes") or {}
            if a.get("device_class") == "power" or a.get("unit_of_measurement") in ("W", "kW"):
                return cand
    return ""


def _match_remote_start(eid: str, by_id: dict) -> str:
    stem = _stem(eid)
    for suffix in ("_mobiel_starten", "_start_op_afstand", "_mobile_start", "_remote_start", "_remote_start_allowed"):
        cand = f"binary_sensor.{stem.split('.', 1)[-1]}{suffix}"
        if cand in by_id:
            return cand
    return ""


def _match_switch(eid: str, by_id: dict) -> str:
    stem = _stem(eid)
    for cand in by_id:
        if cand.startswith("switch.") and stem and stem in cand:
            return cand
    return ""


def _guess_kind(eid: str, name: str) -> str:
    t = f"{eid} {name}".lower()
    if any(w in t for w in ("boiler", "water", "quooker")):
        return "boiler"
    if any(w in t for w in ("laad", "charg", "wallbox", "easee", "zaptec", "alfen", "auto", "car")):
        return "ev"
    if any(w in t for w in ("warmtepomp", "heat", "sg_ready", "sgready")):
        return "heatpump"
    return "generic"
