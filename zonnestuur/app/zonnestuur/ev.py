"""Auto laden: welke auto hangt aan de lader, hoe vol is hij, en hoeveel moet er nog bij.

Auto's komen uit Home Assistant (bijvoorbeeld via Tibber, SEAT/Volkswagen, Stellantis-apps, Tesla, Kia/Hyundai).
Met het accupercentage rekent Zonnestuur precies uit hoeveel kWh er tot je doel nodig is, in plaats van een
schatting per gereden kilometer. Tussen twee metingen in schat hij het percentage zelf bij met wat er geladen is.
"""
from __future__ import annotations

import math
import re
import time
from datetime import datetime
from typing import Optional

EFFICIENCY = 0.90          # van het stopcontact naar de accu
VOLTS = 230.0

# Bekende auto's. Bruikbare accu (kWh), lader in de auto (kW, fasen), verbruik (kWh/km). Afgerond, bron: EV Database.
MODELS = [
    {"key": "seat_mii", "name": "Seat Mii electric", "words": ["mii"], "battery_kwh": 32.3, "ac_kw": 7.2, "ac_phases": 2, "kwh_km": 0.15},
    {"key": "vw_eup2", "name": "Volkswagen e-up! (2020 en later)", "words": ["eup", "e_up"], "battery_kwh": 32.3, "ac_kw": 7.2, "ac_phases": 2, "kwh_km": 0.15},
    {"key": "vw_eup1", "name": "Volkswagen e-up! (2013–2019)", "words": [], "battery_kwh": 16.4, "ac_kw": 3.6, "ac_phases": 1, "kwh_km": 0.14},
    {"key": "skoda_citigo", "name": "Škoda Citigo e iV", "words": ["citigo"], "battery_kwh": 32.3, "ac_kw": 7.2, "ac_phases": 2, "kwh_km": 0.15},
    {"key": "citroen_ec4_54", "name": "Citroën ë-C4 (54 kWh, 2023 en later)", "words": ["ec4", "e_c4", "c4"], "battery_kwh": 50.8, "ac_kw": 7.4, "ac_phases": 1, "kwh_km": 0.16},
    {"key": "citroen_ec4_50", "name": "Citroën ë-C4 (50 kWh, 2021–2023)", "words": [], "battery_kwh": 46.3, "ac_kw": 7.4, "ac_phases": 1, "kwh_km": 0.16},
    {"key": "peugeot_e208", "name": "Peugeot e-208 / Opel Corsa Electric", "words": ["e208", "e_208", "corsa"], "battery_kwh": 46.3, "ac_kw": 7.4, "ac_phases": 1, "kwh_km": 0.15},
    {"key": "peugeot_e2008", "name": "Peugeot e-2008 / Opel Mokka Electric", "words": ["e2008", "e_2008", "mokka"], "battery_kwh": 50.8, "ac_kw": 7.4, "ac_phases": 1, "kwh_km": 0.17},
    {"key": "tesla_m3", "name": "Tesla Model 3", "words": ["model_3", "model3"], "battery_kwh": 57.5, "ac_kw": 11.0, "ac_phases": 3, "kwh_km": 0.14},
    {"key": "tesla_my", "name": "Tesla Model Y", "words": ["model_y", "modely"], "battery_kwh": 75.0, "ac_kw": 11.0, "ac_phases": 3, "kwh_km": 0.16},
    {"key": "vw_id3", "name": "Volkswagen ID.3", "words": ["id3", "id_3"], "battery_kwh": 58.0, "ac_kw": 11.0, "ac_phases": 3, "kwh_km": 0.16},
    {"key": "vw_id4", "name": "Volkswagen ID.4 / Škoda Enyaq", "words": ["id4", "id_4", "enyaq"], "battery_kwh": 77.0, "ac_kw": 11.0, "ac_phases": 3, "kwh_km": 0.18},
    {"key": "kia_niro", "name": "Kia Niro EV / e-Niro", "words": ["niro"], "battery_kwh": 64.8, "ac_kw": 11.0, "ac_phases": 3, "kwh_km": 0.16},
    {"key": "hyundai_kona", "name": "Hyundai Kona Electric", "words": ["kona"], "battery_kwh": 64.0, "ac_kw": 11.0, "ac_phases": 3, "kwh_km": 0.16},
    {"key": "renault_zoe", "name": "Renault Zoe (52 kWh)", "words": ["zoe"], "battery_kwh": 52.0, "ac_kw": 22.0, "ac_phases": 3, "kwh_km": 0.16},
    {"key": "mg4", "name": "MG4 Electric", "words": ["mg4"], "battery_kwh": 61.7, "ac_kw": 11.0, "ac_phases": 3, "kwh_km": 0.17},
    {"key": "nissan_leaf", "name": "Nissan Leaf (40 kWh)", "words": ["leaf"], "battery_kwh": 39.0, "ac_kw": 6.6, "ac_phases": 1, "kwh_km": 0.16},
    {"key": "polestar2", "name": "Polestar 2", "words": ["polestar"], "battery_kwh": 75.0, "ac_kw": 11.0, "ac_phases": 3, "kwh_km": 0.18},
    {"key": "other", "name": "Andere auto", "words": [], "battery_kwh": 50.0, "ac_kw": 11.0, "ac_phases": 3, "kwh_km": 0.17},
]
MODEL_BY_KEY = {m["key"]: m for m in MODELS}
# Laders in de auto om uit te kiezen (sommige modellen hebben een 11 kW-optie)
AC_OPTIONS = [(3.6, 1), (6.6, 1), (7.2, 2), (7.4, 1), (11.0, 3), (22.0, 3)]

_SOC_SUFFIX = ("_state_of_charge", "_battery_level", "_battery_state_of_charge", "_laadniveau", "_soc", "_accuniveau",
               "_charge_level", "_batterijniveau", "_battery")
_RANGE_WORDS = ("range", "actieradius", "bereik", "autonomy")
_PLUG_WORDS = ("stekker", "plug", "plugged", "charging_cable", "charge_cable", "laadkabel", "cable_connected")
_TARGET_SUFFIX = ("_target_state_of_charge", "_charge_limit", "_target_soc", "_charging_target", "_doel_laadniveau",
                  "_charge_limit_soc", "_target_charge_level")
_NAME_TAIL = re.compile(r"\s*(state of charge|battery level|battery|laadniveau|accuniveau|batterijniveau|soc|charge level)\s*$", re.I)


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower().replace("ë", "e").replace("é", "e"))


def guess_model(name: str, entity: str = "") -> str:
    """Modelsleutel bij een autonaam uit Home Assistant ('Mii' → Seat Mii electric, 'e-C4' → Citroën ë-C4)."""
    c = _compact(name) + " " + _compact(entity)
    raw = (name + " " + entity).lower()
    for m in MODELS:
        for w in m["words"]:
            if _compact(w) and (_compact(w) in c or w in raw):
                return m["key"]
    return "other"


def car_from_model(key: str) -> dict:
    m = MODEL_BY_KEY.get(key) or MODEL_BY_KEY["other"]
    return {"model": m["key"], "battery_kwh": m["battery_kwh"], "ac_kw": m["ac_kw"], "ac_phases": m["ac_phases"],
            "kwh_km": m["kwh_km"]}


def discover_cars(states: list[dict]) -> list[dict]:
    """Auto's in Home Assistant: een accupercentage met daarbij een actieradius of stekker-sensor.

    Die combinatie heeft een telefoon of laptop niet, dus die vallen af."""
    by_id = {s.get("entity_id", ""): s for s in states}
    out, seen = [], set()
    for eid, s in by_id.items():
        if not eid.startswith("sensor."):
            continue
        a = s.get("attributes") or {}
        if a.get("unit_of_measurement") != "%":
            continue
        obj = eid.split(".", 1)[1]
        suffix = next((x for x in _SOC_SUFFIX if obj.endswith(x)), None)
        if not suffix or (a.get("device_class") not in (None, "battery")):
            continue
        prefix = obj[: -len(suffix)]
        if not prefix or prefix in seen:
            continue
        sib = [e for e in by_id if e.split(".", 1)[1].startswith(prefix + "_") and e != eid]
        rng = next((e for e in sib if e.startswith("sensor.") and any(w in e for w in _RANGE_WORDS)), "")
        plug = next((e for e in sib if e.startswith("binary_sensor.") and (any(w in e for w in _PLUG_WORDS)
                                                                          or (by_id[e].get("attributes") or {}).get("device_class") == "plug")), "")
        if not rng and not plug:
            continue
        seen.add(prefix)
        target = next((e for e in sib if e.split(".", 1)[1].endswith(_TARGET_SUFFIX)), "")
        conn = next((e for e in sib if e.startswith("binary_sensor.") and (by_id[e].get("attributes") or {}).get("device_class") == "connectivity"), "")
        charging = next((e for e in sib if e.startswith("binary_sensor.") and e != plug and
                         ((by_id[e].get("attributes") or {}).get("device_class") == "battery_charging" or e.endswith(("_opladen", "_charging")))), "")
        fname = str(a.get("friendly_name") or prefix)
        name = _NAME_TAIL.sub("", fname).strip() or prefix.replace("_", " ").title()
        try:
            soc = float(s.get("state"))
        except (TypeError, ValueError):
            soc = None
        model = guess_model(name, prefix)
        out.append({"id": re.sub(r"[^a-z0-9]+", "-", prefix.lower()).strip("-")[:30] or "auto", "name": name[:40],
                    "soc_entity": eid, "plug_entity": plug, "range_entity": rng, "target_entity": target,
                    "conn_entity": conn, "charging_entity": charging, "soc": soc, "last_changed": s.get("last_changed"),
                    "model": model, "model_name": MODEL_BY_KEY[model]["name"]})
    out.sort(key=lambda c: c["name"].lower())
    return out


def new_car(found: dict, target_pct: int = 80, min_pct: int = 20) -> dict:
    """Autoprofiel om op te slaan bij de laadpaal."""
    car = {"id": found["id"], "name": found["name"], "soc_entity": found.get("soc_entity", ""),
           "plug_entity": found.get("plug_entity", ""), "conn_entity": found.get("conn_entity", ""),
           "charging_entity": found.get("charging_entity", ""), "target_entity": found.get("target_entity", ""),
           "target_pct": int(target_pct), "min_pct": int(min_pct)}
    car.update(car_from_model(found.get("model") or "other"))
    return car


def car_amps(car: dict) -> float:
    """Hoeveel ampère per fase de lader in de auto maximaal trekt."""
    ph = max(1, int(car.get("ac_phases") or 1))
    return max(6.0, round(float(car.get("ac_kw") or 11) * 1000 / (VOLTS * ph)))


def charge_kw(car: dict, charger_phases: int, charger_max_a: float) -> float:
    """Werkelijk laadvermogen: wat de laadpaal geeft en wat de auto aankan, het laagste van de twee."""
    ph = max(1, min(int(car.get("ac_phases") or 1), int(charger_phases or 1)))
    amps = min(float(charger_max_a or 16), car_amps(car))
    return round(min(float(car.get("ac_kw") or 11), amps * VOLTS * ph / 1000), 2)


def need(car: dict, soc: Optional[float], target_pct: Optional[float] = None) -> Optional[float]:
    """kWh uit het stopcontact tot het doel. None als het percentage onbekend is."""
    if soc is None:
        return None
    target = float(target_pct if target_pct is not None else car.get("target_pct", 80))
    return max(0.0, (target - soc) / 100.0 * float(car.get("battery_kwh") or 50) / EFFICIENCY)


def soc_after(car: dict, soc: float, kwh_grid: float) -> float:
    return min(100.0, soc + kwh_grid * EFFICIENCY / float(car.get("battery_kwh") or 50) * 100.0)


class CarTracker:
    """Per laadpaal: welke auto hangt eraan, wat is het (geschatte) percentage, wat wil je (slim of nu vol)."""

    def __init__(self):
        self.active: Optional[str] = None
        self.chosen_by = ""                    # "stekker" | "jij" | "laatst" | "enige"
        self.manual: Optional[str] = None      # door jou gekozen, tot de stekker eruit gaat
        self.mode = "slim"                     # "slim" | "vol"
        self.readings: dict[str, dict] = {}    # car id -> {soc, at (lokale tijd iso), mono, plug, conn, target}
        self.base: dict[str, tuple] = {}       # car id -> (soc, wh geladen sinds die meting)
        self.last_car: Optional[str] = None
        self.plugged: Optional[bool] = None
        self.read_mono = -math.inf

    # ---- uitlezen ------------------------------------------------------------
    def read(self, ha, cars: list[dict], mono: Optional[float] = None, every_s: float = 60.0) -> None:
        mono = time.monotonic() if mono is None else mono
        if mono - self.read_mono < every_s:
            return
        self.read_mono = mono
        for c in cars:
            r = self.readings.setdefault(c["id"], {})
            for key in ("soc_entity", "plug_entity", "conn_entity", "charging_entity", "target_entity"):
                ent = c.get(key)
                if not ent:
                    continue
                try:
                    st = ha.state(ent)
                except Exception:
                    continue
                v = st.get("state")
                if key == "soc_entity":
                    try:
                        soc = float(v)
                    except (TypeError, ValueError):
                        continue
                    changed = st.get("last_changed") or st.get("last_updated") or ""
                    if r.get("soc") != soc or r.get("at") != changed:
                        self.base[c["id"]] = (soc, 0.0)
                    r["soc"], r["at"] = soc, changed
                elif key == "target_entity":
                    try:
                        r["target"] = float(v) if 30 <= float(v) <= 100 else None
                    except (TypeError, ValueError):
                        r["target"] = None
                else:
                    r[key.split("_")[0]] = None if v in ("unknown", "unavailable", None) else v == "on"

    def charged(self, wh: float) -> None:
        """Tussen twee metingen: wat er geladen is telt mee voor de actieve auto."""
        if self.active and self.active in self.base and wh > 0:
            soc, done = self.base[self.active]
            self.base[self.active] = (soc, done + wh)

    def soc(self, car: dict) -> Optional[float]:
        b = self.base.get(car["id"])
        if not b:
            return None
        return round(soc_after(car, b[0], b[1] / 1000.0), 1)

    # ---- welke auto ----------------------------------------------------------
    def resolve(self, cars: list[dict], charger_plugged: Optional[bool]) -> Optional[dict]:
        if not cars:
            self.active = None
            return None
        if self.plugged and charger_plugged is False:
            self.manual, self.mode = None, "slim"           # stekker eruit: volgende keer opnieuw herkennen
            if self.active:
                self.last_car = self.active
        self.plugged = charger_plugged
        ids = {c["id"]: c for c in cars}
        if self.manual in ids:
            self.active, self.chosen_by = self.manual, "jij"
        elif len(cars) == 1:
            self.active, self.chosen_by = cars[0]["id"], "enige"
        else:
            plugged = [c for c in cars if self.readings.get(c["id"], {}).get("plug") is True]
            charging = [c for c in cars if self.readings.get(c["id"], {}).get("charging") is True]
            if len(plugged) == 1:
                self.active, self.chosen_by = plugged[0]["id"], "stekker"
            elif len(charging) == 1:
                self.active, self.chosen_by = charging[0]["id"], "stekker"
            else:
                self.active = self.active if self.active in ids else (self.last_car if self.last_car in ids else cars[0]["id"])
                self.chosen_by = "laatst"
        return ids[self.active]

    def choose(self, car_id: Optional[str]) -> None:
        self.manual = car_id or None
        if car_id:
            self.active, self.chosen_by = car_id, "jij"

    def stale_days(self, car: dict, now: datetime) -> Optional[float]:
        at = self.readings.get(car["id"], {}).get("at")
        if not at:
            return None
        try:
            t = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        except ValueError:
            return None
        return max(0.0, (now - t).total_seconds() / 86400)
