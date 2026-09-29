"""Meer koppelingen, zonder Home Assistant.

Meters:      YouLess (LS110/LS120), DSMR-reader, ESPHome (web_server), MQTT (elk topic, DSMR-reader via MQTT),
             Homey Pro (elk apparaat met vermogen)
Apparaten:   MQTT (Zigbee2MQTT, Tasmota, Shelly, of vrije topics), ESPHome-schakelaar, Homey Pro (aan/uit en
             thermostaat), SG-ready warmtepomp (twee relais, van welk merk ook)
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Optional

from .adapters import DeviceError, MeterReading, SwitchStatus, http_get_json
from .mqtt import client_for, extract, extract_state


def _base(host: str) -> str:
    return host.rstrip("/") if host.startswith("http") else f"http://{host}"


def _f(v) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- YouLess
class YouLessMeter:
    """YouLess LS120 met P1: http://<ip>/e?f=j geeft o.a. 'pwr' (W, netto) en de meterstanden."""

    def __init__(self, host: str, invert: bool = False):
        self.base, self.sign = _base(host), -1.0 if invert else 1.0

    def read(self) -> MeterReading:
        data = http_get_json(f"{self.base}/e?f=j")
        row = data[0] if isinstance(data, list) and data else data
        if not isinstance(row, dict) or "pwr" not in row:
            raise DeviceError("YouLess gaf geen 'pwr' terug (P1-kabel aangesloten?)")
        imp = (_f(row.get("p1")) or 0) + (_f(row.get("p2")) or 0) if "p1" in row else None
        exp = (_f(row.get("n1")) or 0) + (_f(row.get("n2")) or 0) if "n1" in row else None
        return MeterReading(self.sign * float(row["pwr"]), imp, exp)


# ---------------------------------------------------------------- DSMR-reader
class DSMRReaderMeter:
    """DSMR-reader (Raspberry Pi met P1-kabel): REST-API v2 met API-sleutel."""

    def __init__(self, host: str, api_key: str):
        self.base, self.key = _base(host), api_key

    def read(self) -> MeterReading:
        import urllib.request
        req = urllib.request.Request(f"{self.base}/api/v2/datalogger/dsmrreading?limit=1&ordering=-timestamp",
                                     headers={"X-AUTHKEY": self.key})
        try:
            with urllib.request.urlopen(req, timeout=4) as r:
                data = json.loads(r.read().decode())
        except Exception as exc:
            raise DeviceError(f"DSMR-reader: {exc}") from exc
        rows = data.get("results") or []
        if not rows:
            raise DeviceError("DSMR-reader heeft nog geen metingen")
        r = rows[0]
        grid = ((_f(r.get("electricity_currently_delivered")) or 0) - (_f(r.get("electricity_currently_returned")) or 0)) * 1000
        imp = (_f(r.get("electricity_delivered_1")) or 0) + (_f(r.get("electricity_delivered_2")) or 0)
        exp = (_f(r.get("electricity_returned_1")) or 0) + (_f(r.get("electricity_returned_2")) or 0)
        return MeterReading(grid, imp, exp)


# ---------------------------------------------------------------- ESPHome (web_server)
def _esp_value(base: str, domain: str, obj: str) -> dict:
    return http_get_json(f"{base}/{domain}/{urllib.parse.quote(obj)}")


def _esp_watts(d: dict) -> float:
    v = _f(d.get("value"))
    if v is None:
        raise DeviceError(f"ESPHome {d.get('id')}: geen waarde")
    return v * 1000 if str(d.get("state", "")).strip().lower().endswith("kw") else v


class ESPHomeMeter:
    """ESPHome-apparaat met web_server (bijv. SlimmeLezer, P1-lezer op ESP): vermogen uit een sensor."""

    def __init__(self, host: str, sensor: str, export_sensor: str = "", invert: bool = False):
        self.base, self.sensor, self.export, self.sign = _base(host), sensor, export_sensor, -1.0 if invert else 1.0

    def read(self) -> MeterReading:
        w = _esp_watts(_esp_value(self.base, "sensor", self.sensor))
        if self.export:
            w -= _esp_watts(_esp_value(self.base, "sensor", self.export))
        return MeterReading(self.sign * w, None, None)


class ESPHomeSwitch:
    def __init__(self, host: str, switch: str, power_sensor: str = ""):
        self.base, self.switch, self.power = _base(host), switch, power_sensor

    def status(self) -> SwitchStatus:
        d = _esp_value(self.base, "switch", self.switch)
        on = d.get("value") is True or str(d.get("state", "")).upper() == "ON"
        p = _esp_watts(_esp_value(self.base, "sensor", self.power)) if self.power else 0.0
        return SwitchStatus(on, p, None)

    def set(self, on: bool) -> None:
        import urllib.request
        req = urllib.request.Request(f"{self.base}/switch/{urllib.parse.quote(self.switch)}/{'turn_on' if on else 'turn_off'}",
                                     data=b"", method="POST")
        try:
            urllib.request.urlopen(req, timeout=4).read()
        except Exception as exc:
            raise DeviceError(f"ESPHome: {exc}") from exc


# ---------------------------------------------------------------- MQTT
MQTT_PROFILES = {
    # profiel: hoe een apparaat zijn toestand meldt en hoe je hem schakelt; {n} = naam/topic van het apparaat
    "zigbee2mqtt": {"state": "zigbee2mqtt/{n}", "state_key": "state", "power": "zigbee2mqtt/{n}", "power_key": "power",
                    "command": "zigbee2mqtt/{n}/set", "on": '{"state": "ON"}', "off": '{"state": "OFF"}'},
    "tasmota": {"state": "stat/{n}/POWER", "state_key": "", "power": "tele/{n}/SENSOR", "power_key": "ENERGY.Power",
                "command": "cmnd/{n}/POWER", "on": "ON", "off": "OFF", "poll": "cmnd/{n}/POWER"},
    "shelly": {"state": "{n}/status/switch:0", "state_key": "output", "power": "{n}/status/switch:0", "power_key": "apower",
               "command": "{n}/command/switch:0", "on": "on", "off": "off"},
}


class MQTTSwitch:
    def __init__(self, conf: dict, p: dict):
        prof = dict(MQTT_PROFILES.get(p.get("profile", ""), {}))
        n = p.get("name", "")

        def g(k: str, d: str = "") -> str:
            v = p.get(k)
            if v in (None, ""):
                v = prof.get(k, d)
            return v.replace("{n}", n) if isinstance(v, str) else v
        self.state_t, self.state_k = g("state"), g("state_key")
        self.power_t, self.power_k = g("power"), g("power_key")
        self.cmd_t, self.on_p, self.off_p = g("command"), g("on", "ON"), g("off", "OFF")
        self.poll = g("poll")
        self.c = client_for(conf)
        for t in {self.state_t, self.power_t} - {""}:
            self.c.subscribe(t)
        if self.poll:
            try:
                self.c.publish(self.poll, "")
            except ConnectionError:
                pass

    def status(self) -> SwitchStatus:
        if not self.c.connected.is_set():
            raise DeviceError(f"MQTT-broker niet bereikbaar ({self.c.error or 'verbinden…'})")
        on = extract_state(self.c.get(self.state_t), self.state_k)
        if on is None:
            if self.poll:
                self.c.publish(self.poll, "")
            raise DeviceError(f"nog geen bericht op {self.state_t}")
        p = extract(self.c.get(self.power_t), self.power_k) if self.power_t else None
        return SwitchStatus(on, float(p or 0.0), None)

    def set(self, on: bool) -> None:
        try:
            self.c.publish(self.cmd_t, self.on_p if on else self.off_p)
        except ConnectionError as exc:
            raise DeviceError(f"MQTT: {exc}") from exc


class MQTTMeter:
    """Vermogen uit MQTT. Eén topic (+ afname, − teruglevering) of aparte topics voor afname en teruglevering."""

    def __init__(self, conf: dict, m: dict):
        prof = m.get("profile", "")
        if prof == "dsmr_reader":
            m = {"topic": "dsmr/reading/electricity_currently_delivered", "export_topic": "dsmr/reading/electricity_currently_returned",
                 "unit": "kW", **{k: v for k, v in m.items() if v}}
        self.topic, self.key = m.get("topic", ""), m.get("key", "")
        self.export_topic, self.export_key = m.get("export_topic", ""), m.get("export_key", "")
        self.factor = 1000.0 if str(m.get("unit", "W")).lower() == "kw" else 1.0
        self.sign = -1.0 if m.get("invert") else 1.0
        self.c = client_for(conf)
        for t in {self.topic, self.export_topic} - {""}:
            self.c.subscribe(t)

    def read(self) -> MeterReading:
        if not self.c.connected.is_set():
            raise DeviceError(f"MQTT-broker niet bereikbaar ({self.c.error or 'verbinden…'})")
        v = extract(self.c.get(self.topic), self.key)
        if v is None:
            raise DeviceError(f"nog geen getal op {self.topic}")
        w = v * self.factor
        if self.export_topic:
            e = extract(self.c.get(self.export_topic), self.export_key)
            w -= (e or 0.0) * self.factor
        return MeterReading(self.sign * w, None, None)


# ---------------------------------------------------------------- Homey Pro
class Homey:
    """Homey Pro (2023) lokale Web API met een API-sleutel (Homey-app → Instellingen → API-sleutels)."""

    def __init__(self, url: str, token: str):
        self.base, self.token = _base(url), token

    def _req(self, path: str, method: str = "GET", body: Optional[dict] = None):
        import urllib.request
        req = urllib.request.Request(f"{self.base}{path}", data=json.dumps(body).encode() if body is not None else None, method=method,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                raw = r.read()
                return json.loads(raw.decode()) if raw else {}
        except Exception as exc:
            raise DeviceError(f"Homey: {exc}") from exc

    def devices(self) -> dict:
        return self._req("/api/manager/devices/device/")

    def device(self, dev_id: str) -> dict:
        return self._req(f"/api/manager/devices/device/{dev_id}")

    def cap(self, dev: dict, name: str):
        return ((dev.get("capabilitiesObj") or {}).get(name) or {}).get("value")

    def set_cap(self, dev_id: str, name: str, value) -> None:
        self._req(f"/api/manager/devices/device/{dev_id}/capability/{name}", "PUT", {"value": value})


class HomeyMeter:
    def __init__(self, homey: Homey, device: str, invert: bool = False):
        self.h, self.dev, self.sign = homey, device, -1.0 if invert else 1.0

    def read(self) -> MeterReading:
        d = self.h.device(self.dev)
        v = _f(self.h.cap(d, "measure_power"))
        if v is None:
            raise DeviceError("Homey-apparaat heeft geen vermogen (measure_power)")
        return MeterReading(self.sign * v, _f(self.h.cap(d, "meter_power.imported")), _f(self.h.cap(d, "meter_power.exported")))


class HomeySwitch:
    def __init__(self, homey: Homey, device: str):
        self.h, self.dev = homey, device

    def status(self) -> SwitchStatus:
        d = self.h.device(self.dev)
        return SwitchStatus(bool(self.h.cap(d, "onoff")), float(_f(self.h.cap(d, "measure_power")) or 0.0), None)

    def set(self, on: bool) -> None:
        self.h.set_cap(self.dev, "onoff", bool(on))


class HomeySetpoint:
    """Thermostaat of boiler in Homey: bij zon de doeltemperatuur hoger, daarna terug."""

    def __init__(self, homey: Homey, device: str, normal: float, boost: float):
        self.h, self.dev, self.normal, self.boost = homey, device, normal, boost

    def status(self) -> SwitchStatus:
        d = self.h.device(self.dev)
        t = _f(self.h.cap(d, "target_temperature"))
        return SwitchStatus(t is not None and t >= self.boost - 0.1, float(_f(self.h.cap(d, "measure_power")) or 0.0), None)

    def set(self, on: bool) -> None:
        self.h.set_cap(self.dev, "target_temperature", self.boost if on else self.normal)


def homey_candidates(devices: dict) -> dict:
    meters, devs = [], []
    for dev_id, d in (devices or {}).items():
        caps = set(d.get("capabilities") or (d.get("capabilitiesObj") or {}).keys())
        name = d.get("name", dev_id)
        low = name.lower()
        power = ((d.get("capabilitiesObj") or {}).get("measure_power") or {}).get("value")
        if "measure_power" in caps and any(w in low for w in ("p1", "meter", "dongle", "energie", "energy", "netto", "grid")):
            meters.append({"device": dev_id, "name": name, "power": power})
        if "target_temperature" in caps:
            devs.append({"device": dev_id, "name": name, "driver": "homey_setpoint", "kind": "heatpump" if "warmtepomp" in low or "heat" in low else "boiler",
                         "power": power})
        elif "onoff" in caps and d.get("class") in ("socket", "heater", "other", "fan", "boiler", "evcharger", "airconditioning", "kettle", None):
            devs.append({"device": dev_id, "name": name, "driver": "homey_switch", "kind": "ev" if d.get("class") == "evcharger" else "generic",
                         "power": power})
    return {"meters": meters, "devices": devs}


# ---------------------------------------------------------------- SG-ready
class SGReady:
    """SG-ready warmtepomp via twee relais (van welk merk ook: Shelly, Home Assistant, MQTT…).

    Stand  relais A  relais B
    1      aan       uit      geblokkeerd (gebruikt Zonnestuur niet)
    2      uit       uit      normaal
    3      uit       aan      aanbeveling: draai extra (zon of goedkope stroom)
    4      aan       aan      opdracht: draai zoveel mogelijk (alleen als 'forceren' aan staat)
    """

    def __init__(self, relay_a, relay_b, force: bool = False, power=None):
        self.a, self.b, self.force, self.power = relay_a, relay_b, force, power

    def status(self) -> SwitchStatus:
        a, b = self.a.status(), self.b.status()
        p = self.power.status().power_w if self.power else max(a.power_w, b.power_w)
        return SwitchStatus(b.on, p, None)

    def set(self, on: bool) -> None:
        if on:
            self.a.set(self.force)
            self.b.set(True)
        else:
            self.b.set(False)
            self.a.set(False)
