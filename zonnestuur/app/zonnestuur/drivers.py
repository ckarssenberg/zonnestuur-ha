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
import re
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

    def set_state(self, entity_id: str, state, attributes: dict) -> None:
        """Eigen sensor in Home Assistant zetten (verdwijnt bij een herstart van HA; Zonnestuur zet hem opnieuw)."""
        _request(f"{self.url}/api/states/{entity_id}", "POST", {"state": state, "attributes": attributes},
                 headers=self._h(), timeout=self.timeout)

    def notify_services(self) -> list[str]:
        """Alle notify-diensten, bijvoorbeeld notify.mobile_app_telefoon (voor meldingen op je telefoon)."""
        data = _request(f"{self.url}/api/services", headers=self._h(), timeout=self.timeout)
        for d in data or []:
            if d.get("domain") == "notify":
                return sorted(f"notify.{k}" for k in (d.get("services") or {}) if k not in ("notify", "send_message"))
        return []

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


class HAChargeButtons:
    """Auto zonder laadschakelaar maar met knoppen 'start laden' en 'stop laden' (bijv. BMW, Kia/Hyundai, Renault, Mercedes).

    Of hij laadt, lezen we uit een 'laadt'-sensor of de vermogensmeting; anders geldt de laatste opdracht."""

    def __init__(self, ha: HomeAssistant, start_entity: str, stop_entity: str, charging_entity: str = "",
                 power_entity: str = "", plug_entity: str = ""):
        self.ha, self.start, self.stop = ha, start_entity, stop_entity
        self.charging, self.power_entity, self.plug = charging_entity, power_entity, plug_entity
        self._last: Optional[bool] = None

    def status(self) -> SwitchStatus:
        if self.plug and self.ha.state(self.plug).get("state") == "off":
            raise NotReady("wacht tot de auto is aangesloten")
        power = self.ha.number(self.power_entity) if self.power_entity else None
        on = self._last
        if self.charging:
            v = self.ha.state(self.charging).get("state")
            if v in ("on", "off"):
                on = v == "on"
            elif isinstance(v, str) and v:
                on = v.lower() in ("charging", "laden", "laadt", "in_progress", "active")
        elif power is not None:
            on = power > 300
        return SwitchStatus(bool(on), float(power or 0.0), None)

    def set(self, on: bool) -> None:
        ent = self.start if on else self.stop
        if not ent:
            return
        self.ha.call(ent.split(".", 1)[0], "press", {"entity_id": ent})
        self._last = on


class HASetpointBoost:
    """Warmtepomp, warmtepompboiler of airco via Home Assistant: 'aan' = doeltemperatuur verhogen.

    Zo slaat het apparaat warmte op (in het water of in de vloer) als er zon is, zonder dat
    Zonnestuur de compressor zelf hoeft te schakelen. 'Uit' zet de normale temperatuur terug.
    """

    def __init__(self, ha: HomeAssistant, entity: str, normal_temp: float, boost_temp: float, power_entity: str = "",
                 running_entity: str = "", nominal_w: float = 0.0):
        self.ha, self.entity, self.power_entity = ha, entity, power_entity
        self.running_entity, self.nominal_w = running_entity, nominal_w   # zonder vermogensmeting: 'draait' × vermogen
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
        if power is None and self.running_entity:
            power = self.nominal_w if self.ha.state(self.running_entity).get("state") == "on" else 0.0
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
            if self.remote and self.ha.state(self.remote).get("state") == "on":
                # start op afstand staat aan, maar de startknop is er (nog) niet: meestal geen programma gekozen
                raise NotReady("start op afstand staat aan, maar kies nog een programma op de machine")
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
                 phases: int = 1, volts: float = 230.0, min_a: float = 6.0, max_a: float = 16.0, plug_entity: str = ""):
        self.ha, self.cur, self.sw, self.power_entity = ha, current_entity, switch_entity, power_entity
        self.phases, self.volts, self.min_a, self.max_a = phases, volts, min_a, max_a
        self.plug = plug_entity

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
        if self.plug and self.ha.state(self.plug).get("state") == "off":
            raise NotReady("wacht tot de auto is aangesloten")
        on = True
        if self.sw:
            on = self.ha.state(self.sw).get("state") == "on"
        power = self.ha.number(self.power_entity) if self.power_entity else None
        return SwitchStatus(on, float(power or 0.0), None)

    def set(self, on: bool) -> None:
        if self.sw and self.ha.state(self.sw).get("state") in ("unavailable", "unknown"):
            if not on:
                self.ha.call("number", "set_value", {"entity_id": self.cur, "value": 0})
            return                                        # geen auto aangesloten: de schakelaar bestaat dan even niet
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
    from . import drivers_extra as X
    if kind == "p1_serial":
        from .p1serial import meter_for
        return meter_for(m.get("port") or "/dev/ttyUSB0", int(m.get("baud") or 115200), bool(m.get("invert")))
    if kind == "youless":
        return X.YouLessMeter(m.get("host") or cfg.p1_host, bool(m.get("invert")))
    if kind == "dsmr_reader":
        return X.DSMRReaderMeter(m.get("host", ""), m.get("api_key", ""))
    if kind == "esphome":
        return X.ESPHomeMeter(m.get("host", ""), m.get("sensor", ""), m.get("export_sensor", ""), bool(m.get("invert")))
    if kind == "mqtt":
        from .config import effective_mqtt
        return X.MQTTMeter(effective_mqtt(cfg), m)
    if kind == "homey":
        return X.HomeyMeter(homey_client(cfg), m.get("device", ""), bool(m.get("invert")))
    raise ValueError(f"Onbekende meter: {kind}")


def homey_client(cfg):
    from .drivers_extra import Homey
    h = cfg.homey or {}
    if not h.get("url") or not h.get("token"):
        raise ValueError("Homey Pro is nog niet gekoppeld (adres en API-sleutel)")
    return Homey(h["url"], h["token"])


def _sub_device(d, spec: dict):
    """Een relais binnen een samengesteld apparaat (SG-ready) als los apparaat bekijken."""
    from dataclasses import replace
    return replace(d, driver=spec.get("driver", "shelly"), host=spec.get("host", ""), switch_id=int(spec.get("switch_id", 0)),
                   params=dict(spec.get("params") or {}))


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
    from . import drivers_extra as X
    if drv == "esphome":
        return X.ESPHomeSwitch(d.host, p.get("switch", ""), p.get("power_sensor", ""))
    if drv == "mqtt_switch":
        from .config import effective_mqtt
        return X.MQTTSwitch(effective_mqtt(cfg), p)
    if drv == "homey_switch":
        return X.HomeySwitch(homey_client(cfg), p["device"])
    if drv == "homey_setpoint":
        return X.HomeySetpoint(homey_client(cfg), p["device"], float(p.get("normal_temp", 20)), float(p.get("boost_temp", 22)))
    if drv == "sg_ready":
        a, b = make_switch(cfg, _sub_device(d, p["a"])), make_switch(cfg, _sub_device(d, p["b"]))
        pw = make_switch(cfg, _sub_device(d, p["power"])) if p.get("power") else None
        return X.SGReady(a, b, bool(p.get("force")), pw)
    if drv == "ocpp":
        from .ocpp import OCPPCharger, central_system
        return OCPPCharger(central_system(int((cfg.ocpp or {}).get("port") or 8887)), p["cp_id"], int(p.get("phases", 3)),
                           float(p.get("volts", 230)), float(p.get("min_a", 6)), float(p.get("max_a", 16)))
    ha = ha_client(cfg)
    if drv == "ha_switch":
        return HASwitch(ha, p["entity"], p.get("power_entity", ""))
    if drv == "ha_setpoint":
        return HASetpointBoost(ha, p["entity"], float(p.get("normal_temp", 50)), float(p.get("boost_temp", 60)),
                               p.get("power_entity", ""), p.get("running_entity", ""), float(d.power_w))
    if drv == "ha_start_button":
        return HAStartButton(ha, p["button_entity"], p.get("remote_entity", ""), p.get("power_entity", ""))
    if drv == "ha_current":
        return HACurrentControl(ha, p["current_entity"], p.get("switch_entity", ""), p.get("power_entity", ""),
                                int(p.get("phases", 1)), float(p.get("volts", 230)), float(p.get("min_a", 6)),
                                float(p.get("max_a", 16)), p.get("plug_entity", ""))
    if drv == "ha_charge_buttons":
        return HAChargeButtons(ha, p["start_entity"], p.get("stop_entity", ""), p.get("charging_entity", ""),
                               p.get("power_entity", ""), p.get("plug_entity", ""))
    if drv == "ha_power":
        return HAPowerControl(ha, p["entity"], p.get("unit", "W"), float(d.power_w), float(p.get("min_w", 100)),
                              p.get("switch_entity", ""), p.get("power_entity", ""))
    raise ValueError(f"Onbekende koppeling: {drv}")


class HAPowerControl:
    """Traploze vermogensregelaar via Home Assistant (bijv. een boiler-regelaar met 0–10 V of een ESPHome-dimmer):
    het vermogen volgt precies het zonne-overschot, ook als dat kleiner is dan het element.

    entity: number.* of input_number.* in watt of procent. Optioneel een schakelaar en een vermogensmeting.
    Gebruik voor 230 V alleen een gecertificeerde regelaar, geplaatst door een installateur."""

    modulating = True

    def __init__(self, ha: HomeAssistant, entity: str, unit: str, max_w: float, min_w: float = 100.0,
                 switch_entity: str = "", power_entity: str = ""):
        self.ha, self.entity, self.unit = ha, entity, (unit or "W")
        self.max_w, self.min_w, self.sw, self.power_entity = max_w, min_w, switch_entity, power_entity
        self.step_w = 50.0
        self._set: float = 0.0

    def _value(self, w: float) -> float:
        w = max(0.0, min(self.max_w, w))
        return round(100.0 * w / self.max_w, 1) if self.unit == "%" else round(w)

    def _write(self, w: float) -> None:
        domain = self.entity.split(".", 1)[0]
        self.ha.call(domain, "set_value", {"entity_id": self.entity, "value": self._value(w)})
        self._set = w

    def status(self) -> SwitchStatus:
        on = True
        if self.sw:
            on = self.ha.state(self.sw).get("state") == "on"
        val = self.ha.number(self.entity) or 0.0
        setw = val * self.max_w / 100.0 if self.unit == "%" else val
        on = on and setw > 0
        power = self.ha.number(self.power_entity) if self.power_entity else setw
        return SwitchStatus(on, float(power or 0.0), None)

    def set(self, on: bool) -> None:
        if self.sw:
            self.ha.call(self.sw.split(".", 1)[0], "turn_on" if on else "turn_off", {"entity_id": self.sw})
        if not on:
            self._write(0.0)
        elif self._set <= 0:
            self._write(self.min_w)

    def set_power(self, w: float) -> None:
        self._write(max(self.min_w, w))


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
               "opname", "recording", "firmware", "update", "led", "motion", "beweging", "vergrendeling", "lock")
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
    power_sensors, devices, price_sensors, inverter_limits = [], [], [], []
    by_id = {s.get("entity_id"): s for s in states}
    charging_switches = [e for e in by_id if e.startswith("switch.") and e.endswith(("_charging", "_opladen", "_laden"))]
    has_available = any(e.startswith("number.") and ("available_current" in e or "beschikbare_stroom" in e) for e in by_id)
    for s in states:
        eid = s.get("entity_id", "")
        dom = eid.split(".", 1)[0]
        a = s.get("attributes") or {}
        name = a.get("friendly_name") or eid
        unit = a.get("unit_of_measurement", "")
        t = _text(eid, name)
        if s.get("state") in ("unavailable",) and dom not in ("sensor", "button"):
            continue
        if dom in ("number", "input_number") and _is_inverter_limit(t, unit):
            inverter_limits.append({"entity": eid, "name": name, "unit": unit,
                                    "state": s.get("state"), "max": a.get("max")})
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
                            "running_entity": _match_running(eid, by_id), "score": 3})
        elif dom == "number" and (unit == "A" or any(w in eid for w in ("charging_current", "charge_current", "charging_amps",
                                                                          "laadstroom", "available_current", "beschikbare_stroom"))):
            if any(w in t for w in ("3 to 1", "phase switch", "fallback", "offline", "terugschakel", "fase", "phase",
                                    "min stroom", "min current", "minimum", "helderheid", "brightness")):
                continue                                  # instellingen, geen laadstroom
            available = "available_current" in eid or "beschikbare_stroom" in eid
            if has_available and not available:
                continue                                  # Zaptec: alleen de installatiestroom gebruiken
            zaptec = available or "zaptec" in t
            sw = _match_switch(eid, by_id) or (charging_switches[0] if len(charging_switches) == 1 else "")
            pw = _match_power(eid, by_id) or (_match_power(sw, by_id) if sw else "")
            plug = _match_plug(sw or eid, by_id)
            devices.append({"driver": "ha_current", "current_entity": eid, "name": name, "kind": "ev",
                            "min_a": a.get("min", 6) if (a.get("min") or 0) >= 6 else 6, "max_a": min(32, a.get("max", 16) or 16),
                            "switch_entity": sw, "power_entity": pw, "plug_entity": plug, "score": 4,
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
    batteries = battery_candidates(states)
    taken = {b.get(k) for b in batteries for k in ("mode_entity", "setpoint_entity", "charge_entity", "discharge_entity")} - {None, ""}
    devices = [d for d in devices if not ({d.get("entity"), d.get("current_entity"), (d.get("params") or {}).get("entity"),
                                           (d.get("params") or {}).get("current_entity")} & taken)]
    return {"power_sensors": power_sensors, "devices": devices, "price_sensors": price_sensors, "readonly_chargers": readonly,
            "inverter_limits": inverter_limits, "batteries": batteries}


# ---------------------------------------------------------------- thuisbatterijen herkennen
_SOC_WORDS = ("state_of_charge", "soc", "battery_level", "electric_level", "laadniveau", "battery_percentage",
              "charge_level", "batterijniveau")
_MODE_KEYS = [  # volgorde telt: 'zero_charge_only' is vasthouden, geen laden
    ("save", ("zero_charge_only", "charge_only", "smart_charging", "alleen laden", "keep_batteries_charged", "backup",
              "no_discharge", "disable_discharge", "hold")),
    ("idle", ("standby", "idle", "stop", "stand-by", "stand_by", "pause", "none")),
    ("charge", ("to_full", "force_charge", "forcible_charge", "forced_charge", "grid_charge", "charge_from_grid",
                "battery_first", "eco_charge", "charge", "laden", "full")),
    ("manual", ("manual", "api", "remote", "remote_control", "handmatig", "custom", "forced", "command", "passive")),
    ("auto", ("zero", "anti_feed", "self_consumption", "self-consumption", "maximise_self_consumption",
              "maximize_self_consumption", "self_use", "selfuse", "load_first", "general", "nom", "smart", "auto",
              "nul op de meter", "eigen verbruik", "optimized", "autonomous", "default", "normal")),
]
_BATTERY_BRANDS = ("homewizard", "zendure", "solarflow", "hyper", "marstek", "venus", "sessy", "victron", "anker", "solix",
                   "ecoflow", "growatt", "huawei", "luna", "sungrow", "byd", "pylontech", "sonnen", "tesla powerwall",
                   "powerwall", "goodwe", "solaredge", "foxess", "fox_ess", "alpha", "deye", "sunsynk", "sigen", "enphase",
                   "solax", "solis", "sofar", "sma ", "fronius", "bluetti", "jackery", "hoymiles", "indevolt", "lg_ess",
                   "kostal", "battery", "batterij", "accu", "plug_in", "plug-in", "ess", "storage", "thuisaccu")
# Standaardwaarden per merk: capaciteit (kWh), laden/ontladen (W), rendement heen en terug. Aan te passen in de app.
BATTERY_DEFAULTS = {
    "homewizard": (2.7, 800, 800, 0.80), "marstek": (5.12, 2500, 2500, 0.75), "venus": (5.12, 2500, 2500, 0.75),
    "zendure": (1.92, 1200, 1200, 0.85), "solarflow": (1.92, 1200, 1200, 0.85), "hyper": (1.92, 1200, 1200, 0.85),
    "anker": (1.6, 1200, 800, 0.85), "solix": (1.6, 1200, 800, 0.85), "ecoflow": (1.92, 800, 800, 0.85),
    "sessy": (5.0, 2200, 1700, 0.85), "victron": (10.0, 5000, 5000, 0.90), "huawei": (5.0, 2500, 2500, 0.92),
    "luna": (5.0, 2500, 2500, 0.92), "growatt": (5.0, 2500, 2500, 0.90), "sungrow": (9.6, 5000, 5000, 0.92),
    "goodwe": (8.0, 5000, 5000, 0.92), "solaredge": (9.7, 5000, 5000, 0.92), "powerwall": (13.5, 5000, 5000, 0.90),
    "sonnen": (10.0, 3300, 3300, 0.90), "byd": (10.2, 5000, 5000, 0.92), "foxess": (10.4, 5000, 5000, 0.92),
    "deye": (10.0, 5000, 5000, 0.92), "sunsynk": (10.0, 5000, 5000, 0.92), "sigen": (8.0, 5000, 5000, 0.93),
    "enphase": (5.0, 1280, 1280, 0.90), "alpha": (10.1, 5000, 5000, 0.92), "solax": (6.3, 5000, 5000, 0.92),
    "bluetti": (2.0, 1200, 1200, 0.85), "jackery": (2.0, 1200, 800, 0.85), "hoymiles": (2.2, 800, 800, 0.85),
    "indevolt": (2.2, 800, 800, 0.85),
}
_SWITCH_WORDS = ("rs485", "remote_control", "modbus_control", "external_control", "control_mode", "remote control")


def _norm(o: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(o).lower()).strip("_")


def _map_modes(options: list) -> dict:
    out: dict[str, str] = {}
    for opt in options or []:
        o = _norm(opt)
        parts = o.split("_")
        if any(x in o for x in ("fed_to_grid", "feed_in", "export", "sell", "discharge", "off_grid", "peak_shav", "time_of_use", "trade")):
            continue                              # terugleveren, eiland of tijdschema: nooit door Zonnestuur gekozen
        for key, words in _MODE_KEYS:
            if key in out:
                continue
            if any(o == _norm(w) or o.startswith(_norm(w)) or _norm(w) in parts or ("_" in _norm(w) and f"_{_norm(w)}_" in f"_{o}_")
                   for w in words):
                out[key] = opt
                break
    return out


def _prefix_len(a: str, b: str) -> int:
    ta, tb = a.split(".", 1)[-1].split("_"), b.split(".", 1)[-1].split("_")
    n = 0
    for x, y in zip(ta, tb):
        if x != y:
            break
        n += 1
    return n


def battery_candidates(states: list[dict]) -> list[dict]:
    """Thuisbatterijen in Home Assistant: een laadniveau-sensor mét iets om hem te sturen (stand of vermogen).

    Alleen sensoren met een stuur-instelling ernaast tellen, zodat telefoons, auto's en Zigbee-sensoren wegvallen."""
    by_id = {s.get("entity_id", ""): s for s in states}
    socs = [e for e, s in by_id.items() if e.startswith("sensor.") and (s.get("attributes") or {}).get("unit_of_measurement") == "%"
            and any(w in e for w in _SOC_WORDS)]
    powers = [e for e, s in by_id.items() if e.startswith("sensor.") and _is_power(s) and ("battery" in e or "batterij" in e)]
    out, used = [], set()

    def near(ref: str, pool: list[str], minlen: int = 1) -> list[str]:
        scored = sorted(((_prefix_len(ref, e), e) for e in pool), reverse=True)
        return [e for n, e in scored if n >= minlen and n == scored[0][0]] if scored else []

    # 1. HomeWizard P1 met Plug-In Batteries: select met 'zero' / 'to_full' / 'standby'
    for e, s in by_id.items():
        opts = (s.get("attributes") or {}).get("options") or []
        if not e.startswith("select.") or "zero" not in opts or not ({"to_full", "standby"} & set(opts)):
            continue
        hw_socs = ([x for x in socs if "state_of_charge" in x and ("plug_in" in x or "homewizard" in x)]
                   or [x for x in socs if "state_of_charge" in x and not any(b in x for b in _BATTERY_BRANDS[1:9])])
        n = max(1, len(hw_socs))
        pw = [x for x in powers if "battery_group_power" in x] or near(e, powers)
        out.append({"name": "HomeWizard Plug-In Battery" + (f" ({n}×)" if n > 1 else ""), "brand": "homewizard", "driver": "mode",
                    "mode_entity": e, "mode_map": _map_modes(opts), "soc_entity": ",".join(hw_socs),
                    "power_entity": pw[0] if pw else "", "capacity_kwh": round(2.7 * n, 1),
                    "max_charge_w": 800 * n, "max_discharge_w": 800 * n, "options": opts})
        used.add(e)
    # 2. Andere merken: stand-select(s), schakelaar en vermogens-instellingen naast een laadniveau-sensor
    caps = [e for e, st in by_id.items() if e.startswith("sensor.") and (st.get("attributes") or {}).get("unit_of_measurement") in ("kWh", "Wh")
            and any(w in e for w in ("capacity", "capaciteit", "rated_energy", "battery_energy_total"))]
    for e, s in by_id.items():
        if e in used or not e.startswith(("select.", "number.", "input_select.", "switch.")):
            continue
        a = s.get("attributes") or {}
        t = _text(e, a.get("friendly_name") or "")
        if not any(b in t for b in _BATTERY_BRANDS):
            continue
        if e.startswith("switch.") and not any(w in t for w in _SWITCH_WORDS):
            continue
        soc = near(e, [x for x in socs if x not in used], 1)
        if not soc:
            continue
        soc = soc[0]
        key = soc
        cand = next((c for c in out if c.get("_key") == key), None)
        if cand is None:
            name = (by_id[soc].get("attributes") or {}).get("friendly_name") or soc
            for w in (" State of charge", " state of charge", " Laadniveau", " laadniveau", " SOC", " Soc", " Battery level",
                      " Electric level", " Battery Level", " Batterij SOC", " batterij soc"):
                name = name.replace(w, "")
            pw = near(e, powers)
            cand = {"_key": key, "name": name.strip() or "Thuisbatterij", "brand": next((b for b in _BATTERY_BRANDS if b in t), ""),
                    "driver": "", "soc_entity": soc, "power_entity": pw[0] if pw else "", "mode_entity": "", "mode_map": {},
                    "force_entity": "", "force_map": {}, "control_switch_entity": "", "charge_power_entity": "",
                    "setpoint_entity": "", "charge_entity": "", "discharge_entity": "", "capacity_kwh": 0.0,
                    "max_charge_w": 0, "max_discharge_w": 0}
            cap = near(soc, caps, 1)
            if cap:
                try:
                    v = float(by_id[cap[0]].get("state"))
                    v = v / 1000 if (by_id[cap[0]].get("attributes") or {}).get("unit_of_measurement") == "Wh" else v
                    if 0.5 <= v <= 200:
                        cand["capacity_kwh"] = round(v, 2)
                except (TypeError, ValueError):
                    pass
            out.append(cand)
        if e.startswith("switch."):
            cand["control_switch_entity"] = cand["control_switch_entity"] or e
        elif e.startswith(("select.", "input_select.")):
            m = _map_modes(a.get("options") or [])
            if (m.get("auto") or m.get("manual")) and not cand["mode_entity"]:
                cand["mode_entity"], cand["mode_map"], cand["options"] = e, m, a.get("options") or []
            elif m.get("charge") and m.get("idle") and not m.get("auto"):
                cand["force_entity"], cand["force_map"] = e, {k: v for k, v in m.items() if k in ("charge", "idle")}
        elif "discharge" in t or "output_limit" in e or "ontla" in t:
            cand["discharge_entity"] = cand["discharge_entity"] or e
            cand["max_discharge_w"] = _max_w(a) or cand["max_discharge_w"]
        elif "setpoint" in t:
            cand["setpoint_entity"] = e
            cand["max_charge_w"] = cand["max_discharge_w"] = _max_w(a) or cand["max_charge_w"]
        elif "charge" in t or "input_limit" in e or "laad" in t:
            cand["charge_entity"] = cand["charge_entity"] or e
            cand["max_charge_w"] = _max_w(a) or cand["max_charge_w"]
    for c in out:
        brand = next((b for b in BATTERY_DEFAULTS if b in (c.get("brand") or "") or b in _text(c.get("soc_entity", ""), c.get("name", ""))), "")
        cap, ch, dis, eff = BATTERY_DEFAULTS.get(brand, (5.0, 2500, 2500, 0.90))
        c["capacity_kwh"] = c.get("capacity_kwh") or cap
        c["max_charge_w"] = c.get("max_charge_w") or ch
        c["max_discharge_w"] = c.get("max_discharge_w") or dis
        c.setdefault("efficiency", eff)
        if brand:
            c["brand"] = brand
        if c.get("brand") == "sessy":           # Sessy: positief = ontladen (setpoint = batterij + net)
            c["setpoint_charge_positive"] = c["power_charge_positive"] = False
        if c.get("driver"):
            continue
        m = c.get("mode_map") or {}
        if m.get("auto") and (c.get("force_entity") or m.get("save") or m.get("charge") or m.get("idle")):
            c["driver"] = "mode"
        elif c.get("setpoint_entity"):
            c["driver"] = "setpoint"
        elif c.get("charge_entity") and c.get("discharge_entity"):
            c["driver"] = "split"
        elif m.get("auto"):
            c["driver"] = "mode"
        if c["driver"] == "mode" and c.get("charge_entity") and (c.get("force_entity") or m.get("charge")):
            c["charge_power_entity"] = c["charge_entity"]
    return [{k: v for k, v in c.items() if k != "_key"} for c in out if c.get("driver")]


def _max_w(attrs: dict) -> Optional[float]:
    try:
        v = float(attrs.get("max"))
    except (TypeError, ValueError):
        return None
    if attrs.get("unit_of_measurement") == "kW":
        v *= 1000
    return v if 100 <= v <= 50000 else None


_INVERTER_WORDS = ("inverter", "omvormer", "solaredge", "growatt", "huawei", "sungrow", "fronius", "goodwe", "solis",
                   "sma ", "hoymiles", "enphase", "apsystems", "deye", "solax", "foxess", "pv ", "zonnepan", "opendtu", "ahoy")
_LIMIT_WORDS = ("power limit", "active power", "export limit", "limit", "vermogenslimiet", "begrenzing", "max output",
                "maximaal vermogen", "power control", "active_power", "limit_nonpersistent")


def _is_inverter_limit(t: str, unit: str) -> bool:
    """Een instelbaar getal waarmee je het vermogen van de omvormer begrenst (in W, kW of %)."""
    return unit in ("W", "kW", "%") and any(w in t for w in _LIMIT_WORDS) and any(w in t for w in _INVERTER_WORDS)


def _common(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _stem(eid: str) -> str:
    base = eid.split(".", 1)[1]
    for suffix in ("_start_program", "_starten", "_start", "_opladen", "_laden", "_laadvermogen", "_charge_power", "_charging_current", "_max_charging_current", "_current", "_switch", "_charging", "_power",
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


def _match_running(eid: str, by_id: dict) -> str:
    """Aan/uit-sensor van de compressor of het element van hetzelfde apparaat (als er geen vermogensmeting is)."""
    obj = eid.split(".", 1)[-1]
    best, score = "", 0
    for cand, s in by_id.items():
        if not cand.startswith("binary_sensor."):
            continue
        c = cand.split(".", 1)[1]
        n = _common(c, obj)
        if n < 10:
            continue
        w = c[c.rfind("_", 0, n) + 1:]                  # vanaf de laatste woordgrens: '..._dk_heatpump'
        if any(k in w for k in ("heatpump", "warmtepomp", "compressor", "running", "draait", "heating")) and "element" not in w:
            if n > score:
                best, score = cand, n
    return best


def _match_remote_start(eid: str, by_id: dict) -> str:
    stem = _stem(eid)
    for suffix in ("_mobiel_starten", "_start_op_afstand", "_mobile_start", "_remote_start", "_remote_start_allowed"):
        cand = f"binary_sensor.{stem.split('.', 1)[-1]}{suffix}"
        if cand in by_id:
            return cand
    return ""


def _match_plug(eid: str, by_id: dict) -> str:
    """Stekker-sensor van dezelfde laadpaal (bijv. binary_sensor.<laadpaal>_stekker / _plug)."""
    stem = _stem(eid)
    for cand, s in by_id.items():
        if cand.startswith("binary_sensor.") and stem and cand.split(".", 1)[1].startswith(stem + "_") \
                and (s.get("attributes") or {}).get("device_class") == "plug":
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
