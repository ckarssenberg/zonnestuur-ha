"""Thuisbatterij: slim laden en ontladen, met als doel zo min mogelijk terugleveren én zo goedkoop mogelijk.

Hoe het werkt
-------------
1. Plannen (elk uur opnieuw). Voor de komende uren kent Zonnestuur:
     - de stroomprijs per uur (dynamisch contract) of de vaste prijs,
     - wat een teruggeleverde kWh nog oplevert (soms negatief),
     - je eigen verbruiksprofiel per uur (uit de meter, laatste 14 dagen),
     - de zonvoorspelling.
   Met dynamisch programmeren (alle laadtoestanden × alle uren × alle keuzes) kiest hij per uur de goedkoopste keuze:
     auto    batterij houdt de meter op nul: laadt van zon-overschot, levert als het huis stroom vraagt
     sparen  alleen laden van zon-overschot, niet ontladen (bewaren voor de dure uren)
     laden   laden van het net (alleen als het straks duidelijk duurder is, of bij negatieve prijzen)
   Slijtage telt mee als kosten per kWh, dus hij gaat niet voor een paar cent heen en weer.
2. Uitvoeren (elke regelronde). Heeft de batterij zelf een 'nul op de meter'-stand (HomeWizard, Marstek,
   Sessy, Zendure), dan zet Zonnestuur alleen de stand; de batterij regelt zelf in fracties van een seconde.
   Anders stuurt Zonnestuur het laad- of ontlaadvermogen zelf bij op de meter.
3. Samenwerken met apparaten. Zon-overschot gaat eerst naar de boiler, auto of warmtepomp; de batterij krijgt
   de rest. En de batterij ontlaadt nooit in een apparaat dat Zonnestuur in een goedkoop uur aanzet.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger("zonnestuur.battery")

ACTIONS = ("auto", "save", "charge")
LABEL = {"auto": "zelf gebruiken", "save": "sparen voor later", "charge": "laden van het net", "idle": "stand-by"}


@dataclass
class BatteryConfig:
    id: str = "batterij"
    name: str = "Thuisbatterij"
    capacity_kwh: float = 5.0
    max_charge_w: float = 800.0
    max_discharge_w: float = 800.0
    min_soc: float = 10.0                 # % dat altijd in de batterij blijft
    efficiency: float = 0.90              # rondrit (laden én ontladen samen)
    cycle_cost: float = 0.02              # € slijtage per kWh door de batterij
    grid_charge: bool = True              # mag hij van het net laden als dat loont?
    driver: str = "mode"                  # mode | setpoint | split
    soc_entity: str = ""                  # sensor, %
    power_entity: str = ""                # sensor, W (optioneel)
    power_charge_positive: bool = True    # + = laden?
    mode_entity: str = ""                 # select
    mode_map: dict = field(default_factory=dict)   # {"auto": "zero", "save": "zero_charge_only", "charge": "to_full", "idle": "standby", "manual": ...}
    setpoint_entity: str = ""             # number, W (+ laden bij setpoint_charge_positive)
    setpoint_charge_positive: bool = True
    charge_entity: str = ""               # number, W (split)
    discharge_entity: str = ""            # number, W (split)

    @classmethod
    def from_dict(cls, d: dict) -> "BatteryConfig":
        names = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in names})

    def validate(self) -> None:
        if not self.soc_entity:
            raise ValueError(f"{self.name}: kies de sensor met het laadniveau (%)")
        if not (0.5 <= self.capacity_kwh <= 200):
            raise ValueError(f"{self.name}: capaciteit moet tussen 0,5 en 200 kWh liggen")
        if not (100 <= self.max_charge_w <= 50000 and 100 <= self.max_discharge_w <= 50000):
            raise ValueError(f"{self.name}: laad- en ontlaadvermogen tussen 100 en 50.000 W")
        if self.driver == "mode" and not (self.mode_entity and self.mode_map.get("auto")):
            raise ValueError(f"{self.name}: kies de stand-instelling en welke stand 'nul op de meter' is")
        if self.driver == "setpoint" and not self.setpoint_entity:
            raise ValueError(f"{self.name}: kies de instelling voor het vermogen")
        if self.driver == "split" and not (self.charge_entity and self.discharge_entity):
            raise ValueError(f"{self.name}: kies de instellingen voor laad- en ontlaadvermogen")
        if self.driver not in ("mode", "setpoint", "split"):
            raise ValueError(f"{self.name}: onbekende sturing {self.driver}")


# ------------------------------------------------------------------------------------------ plannen
@dataclass
class HourIn:
    start: datetime
    price: float          # afnameprijs €/kWh
    feed: float           # wat teruggeleverde kWh oplevert €/kWh (kan negatief)
    net_kwh: float        # verwacht verbruik min opwek in dit uur (+ afname, − overschot), zonder batterij


@dataclass
class HourPlan:
    start: datetime
    action: str
    soc: float            # verwacht laadniveau (%) aan het eind van het uur
    grid_kwh: float       # verwachte netafname (+) / teruglevering (−) met batterij
    price: float


def _step(cfg: BatteryConfig, e: float, h: HourIn, action: str) -> tuple[float, float, float, float]:
    """Eén uur doorrekenen. Geeft (nieuwe lading kWh, kosten €, afname kWh, teruglevering kWh)."""
    cap = cfg.capacity_kwh
    e_min = cap * cfg.min_soc / 100
    eta = math.sqrt(cfg.efficiency)
    c_max, d_max = cfg.max_charge_w / 1000, cfg.max_discharge_w / 1000
    surplus, need = max(0.0, -h.net_kwh), max(0.0, h.net_kwh)
    room = max(0.0, (cap - e) / eta)
    ch_sun = min(surplus, c_max, room)
    ch_grid = 0.0
    dis = 0.0
    if action == "charge":
        ch_grid = max(0.0, min(c_max - ch_sun, room - ch_sun))
    elif action == "auto":
        dis = min(need, d_max, max(0.0, (e - e_min) * eta))
    e2 = e + (ch_sun + ch_grid) * eta - dis / eta
    imp = need - dis + ch_grid
    exp = surplus - ch_sun
    cost = imp * h.price - exp * h.feed + cfg.cycle_cost * (ch_sun + ch_grid)
    return e2, cost, imp, exp


def plan(cfg: BatteryConfig, soc_pct: float, hours: list[HourIn], levels: int = 40) -> list[HourPlan]:
    """Goedkoopste keuze per uur (dynamisch programmeren over laadtoestand)."""
    if not hours:
        return []
    cap = cfg.capacity_kwh
    step = cap / levels
    actions = ACTIONS if cfg.grid_charge else ("auto", "save")
    # Wat is energie aan het eind nog waard? Die gebruik je later tegen een gemiddelde prijs.
    avg = sum(h.price for h in hours) / len(hours)
    end_value = max(0.0, avg * math.sqrt(cfg.efficiency) - cfg.cycle_cost) * 0.9
    INF = float("inf")
    n = len(hours)
    # best[t][s] = minimale kosten vanaf uur t in toestand s
    best = [[0.0] * (levels + 1) for _ in range(n + 1)]
    choice = [[("auto", 0)] * (levels + 1) for _ in range(n)]
    e_min = cap * cfg.min_soc / 100
    for s in range(levels + 1):
        best[n][s] = -max(0.0, s * step - e_min) * end_value
    for t in range(n - 1, -1, -1):
        for s in range(levels + 1):
            e = s * step
            b, bc = INF, ("auto", s)
            for a in actions:
                e2, cost, _, _ = _step(cfg, e, hours[t], a)
                s2 = min(levels, max(0, int(round(e2 / step))))
                v = cost + best[t + 1][s2]
                if v < b - 1e-9:
                    b, bc = v, (a, s2)
            best[t][s], choice[t][s] = b, bc
    out = []
    s = min(levels, max(0, int(round(cap * soc_pct / 100 / step))))
    e = s * step
    for t in range(n):
        a, _ = choice[t][s]
        e2, _, imp, exp = _step(cfg, e, hours[t], a)
        out.append(HourPlan(hours[t].start, a, round(100 * e2 / cap, 1), round(imp - exp, 3), hours[t].price))
        s = min(levels, max(0, int(round(e2 / step))))
        e = e2
    return out


def plan_value(cfg: BatteryConfig, soc_pct: float, hours: list[HourIn], plan_: list[HourPlan]) -> dict:
    """Wat het plan oplevert tegenover 'gewoon nul op de meter' en tegenover geen batterij."""
    def run(acts):
        e, cost, exp_tot = cfg.capacity_kwh * soc_pct / 100, 0.0, 0.0
        for h, a in zip(hours, acts):
            e, c, _, x = _step(cfg, e, h, a)
            cost += c
            exp_tot += x
        return cost, exp_tot
    smart, smart_exp = run([p.action for p in plan_])
    plain, _ = run(["auto"] * len(hours))
    none = sum(max(0.0, h.net_kwh) * h.price - max(0.0, -h.net_kwh) * h.feed for h in hours)
    none_exp = sum(max(0.0, -h.net_kwh) for h in hours)
    return {"vs_plain_eur": round(plain - smart, 2), "vs_none_eur": round(none - smart, 2),
            "export_kwh": round(smart_exp, 2), "export_without_kwh": round(none_exp, 2)}


# ------------------------------------------------------------------------------------------ uitvoeren
@dataclass
class BatteryRuntime:
    cfg: BatteryConfig
    soc: Optional[float] = None
    power_w: Optional[float] = None       # gemeten, + = laden
    action: str = "auto"
    reason: str = ""
    plan: list = field(default_factory=list)
    plan_key: str = ""
    value: dict = field(default_factory=dict)
    online: bool = False
    error: str = ""
    _mode_set: str = ""
    _target_w: float = 0.0
    _last_write: float = -1e9

    # ---- lezen
    def read(self, ha) -> None:
        c = self.cfg
        try:
            vals = [float(ha.state(e.strip()).get("state")) for e in c.soc_entity.split(",") if e.strip()]
            self.soc = sum(vals) / len(vals)           # meerdere batterijen in één groep: gemiddelde
            self.online = True
            self.error = ""
        except Exception as exc:
            self.online, self.soc = False, None
            self.error = f"laadniveau niet te lezen ({exc.__class__.__name__})"
            return
        if c.power_entity:
            try:
                st = ha.state(c.power_entity)
                v = float(st.get("state"))
                if (st.get("attributes") or {}).get("unit_of_measurement") == "kW":
                    v *= 1000
                self.power_w = v if c.power_charge_positive else -v
            except Exception:
                self.power_w = None
        elif c.driver != "mode":
            self.power_w = self._target_w          # geen meting: neem aan dat hij doet wat we vragen

    # ---- beslissen
    def decide_action(self, now: datetime, devices_on_cheap: bool) -> str:
        a = "auto"
        for p in self.plan:
            if p.start <= now < p.start + timedelta(hours=1):
                a = p.action
                break
        self.reason = LABEL[a]
        if a == "auto" and devices_on_cheap:
            a, self.reason = "save", "apparaten draaien op goedkope stroom: batterij spaart"
        self.action = a
        return a

    def target_w(self, grid_w: Optional[float]) -> float:
        """Voor batterijen zonder eigen 'nul op de meter': welk vermogen (+ laden, − ontladen)."""
        c = self.cfg
        cur = self.power_w if self.power_w is not None else self._target_w
        if self.action == "charge":
            t = c.max_charge_w
        elif grid_w is None:
            t = 0.0
        else:
            t = cur - grid_w                      # zo komt de meter op nul
            if self.action == "save":
                t = max(0.0, t)
        t = max(-c.max_discharge_w, min(c.max_charge_w, t))
        if self.soc is not None and self.soc <= c.min_soc:
            t = max(0.0, t)
        if self.soc is not None and self.soc >= 99.5:
            t = min(0.0, t)
        return round(t)

    def apply(self, ha, mono: float, grid_w: Optional[float]) -> Optional[str]:
        """Stuurt de batterij aan. Geeft een korte logregel terug als er iets veranderd is."""
        c = self.cfg
        if c.driver == "mode":
            want = c.mode_map.get(self.action) or (c.mode_map.get("auto") if self.action == "save" else None)
            if not want or want == self._mode_set:
                return None
            ha.call(c.mode_entity.split(".", 1)[0], "select_option", {"entity_id": c.mode_entity, "option": want})
            self._mode_set = want
            return f"stand {want}"
        # zelf regelen: eerst de stand op handmatig (als die bestaat), dan het vermogen
        if c.mode_entity and c.mode_map.get("manual") and self._mode_set != c.mode_map["manual"]:
            ha.call(c.mode_entity.split(".", 1)[0], "select_option", {"entity_id": c.mode_entity, "option": c.mode_map["manual"]})
            self._mode_set = c.mode_map["manual"]
        t = self.target_w(grid_w)
        if abs(t - self._target_w) < 40 and mono - self._last_write < 300:
            return None
        if mono - self._last_write < 8:
            return None
        if c.driver == "setpoint":
            v = t if c.setpoint_charge_positive else -t
            ha.call(c.setpoint_entity.split(".", 1)[0], "set_value", {"entity_id": c.setpoint_entity, "value": v})
        else:
            ha.call(c.charge_entity.split(".", 1)[0], "set_value", {"entity_id": c.charge_entity, "value": max(0, t)})
            ha.call(c.discharge_entity.split(".", 1)[0], "set_value", {"entity_id": c.discharge_entity, "value": max(0, -t)})
        self._target_w, self._last_write = t, mono
        return f"{t:+.0f} W"

    def to_dict(self, tz) -> dict:
        c = self.cfg
        return {"id": c.id, "name": c.name, "capacity_kwh": c.capacity_kwh, "soc": self.soc,
                "kwh": None if self.soc is None else round(c.capacity_kwh * self.soc / 100, 2),
                "power_w": None if self.power_w is None else round(self.power_w),
                "action": self.action, "reason": self.reason, "online": self.online, "error": self.error,
                "min_soc": c.min_soc, "value": self.value,
                "plan": [{"start": p.start.astimezone(tz).isoformat(timespec="minutes"), "action": p.action,
                          "soc": p.soc, "price": round(p.price, 4)} for p in self.plan[:36]]}


def hourly_profile(rows: list[dict], tz, days: int = 14) -> Optional[list[float]]:
    """Gemiddelde netto afname per uur van de dag (+ afname, − teruglevering) uit de meter."""
    if not rows:
        return None
    last = max(r["ts"] for r in rows)
    rows = [r for r in rows if r["ts"] > last - days * 86400]
    tot, cnt = [0.0] * 24, [0] * 24
    for r in rows:
        h = datetime.fromtimestamp(r["ts"], tz).hour
        tot[h] += r["import_kwh"] - r["export_kwh"] - r.get("batt_kwh", 0.0)   # zoals het zonder batterij was
        cnt[h] += 1
    if sum(cnt) < 24:
        return None
    return [tot[h] / cnt[h] if cnt[h] else 0.3 for h in range(24)]


def import_profile(rows: list[dict], tz, days: int = 14) -> Optional[list[float]]:
    if not rows:
        return None
    last = max(r["ts"] for r in rows)
    rows = [r for r in rows if r["ts"] > last - days * 86400]
    tot, cnt = [0.0] * 24, [0] * 24
    for r in rows:
        h = datetime.fromtimestamp(r["ts"], tz).hour
        tot[h] += max(0.0, r["import_kwh"] - r.get("batt_kwh", 0.0))
        cnt[h] += 1
    return [tot[h] / cnt[h] if cnt[h] else 0.3 for h in range(24)] if sum(cnt) >= 24 else None
