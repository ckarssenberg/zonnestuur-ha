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
     sparen  vasthouden: niet ontladen (bewaren voor de dure uren), alleen als er geen zon over is
     laden   laden van het net op 25, 50, 75 of 100% vermogen, tot hoogstens 95%
   Verlies per richting, slijtage per kWh, schakelkosten per uur en wat er op het eind nog in zit tellen mee.
   Levert slim plannen minder dan € 0,03 op, dan blijft hij gewoon zelf gebruiken. Het doelpercentage volgt
   uit het plan: zoveel als nodig om de dure uren te overbruggen. Optioneel: (winter)reserve en piekgrens.
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
LABEL = {"auto": "zelf gebruiken", "save": "vasthouden voor later", "charge": "laden van het net", "idle": "stand-by"}
WINTER = (10, 11, 12, 1, 2, 3)


@dataclass
class BatteryConfig:
    id: str = "batterij"
    name: str = "Thuisbatterij"
    brand: str = ""
    capacity_kwh: float = 5.0
    max_charge_w: float = 800.0
    max_discharge_w: float = 800.0
    max_grid_charge_w: float = 0.0        # hoogstens zo snel laden van het net (0 = max_charge_w)
    min_soc: float = 10.0                 # % dat altijd in de batterij blijft
    max_grid_soc: float = 95.0            # van het net nooit verder laden dan dit
    efficiency: float = 0.90              # rondrit (laden én ontladen samen)
    cycle_cost: float = 0.015             # € slijtage per kWh die in de batterij gaat
    switch_cost: float = 0.01             # € per uur laden of vasthouden: niet schakelen voor een paar cent
    min_gain: float = 0.03                # levert slim plannen minder op dan dit, dan gewoon zelf gebruiken
    grid_charge: bool = True              # mag hij van het net laden als dat loont?
    reserve_kwh: float = 0.0              # reserve die in de batterij blijft …
    reserve_from: int = 12                # … vanaf dit uur …
    reserve_until: int = 17               # … tot dit uur (daarna mag hij op, bv. voor de kookpiek)
    reserve_winter_only: bool = True      # alleen oktober t/m maart
    peak_limit_w: float = 0.0             # capaciteitstarief: netafname tijdens laden onder deze grens (0 = uit)
    driver: str = "mode"                  # mode | setpoint | split | scripts
    soc_entity: str = ""                  # sensor, %
    power_entity: str = ""                # sensor, W (optioneel)
    power_charge_positive: bool = True    # + = laden?
    mode_entity: str = ""                 # select
    mode_map: dict = field(default_factory=dict)   # {"auto": "zero", "save": "zero_charge_only", "charge": "to_full", "idle": "standby", "manual": ...}
    charge_power_entity: str = ""         # number, W: laadvermogen bij 'laden' (stand-sturing)
    force_entity: str = ""                # tweede select voor gedwongen laden/stilstaan (bv. Marstek 'force mode')
    force_map: dict = field(default_factory=dict)  # {"charge": "charge", "idle": "standby"}
    control_switch_entity: str = ""       # switch die aan moet voor sturen van buitenaf (bv. RS485 bij Marstek)
    setpoint_entity: str = ""             # number, W (+ laden bij setpoint_charge_positive)
    setpoint_charge_positive: bool = True
    charge_entity: str = ""               # number, W (split)
    discharge_entity: str = ""            # number, W (split)
    script_auto: str = ""                 # scripts: werkt met elk merk dat Home Assistant kan sturen
    script_save: str = ""
    script_charge: str = ""               # krijgt de variabele power_w mee

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
        if not (0.5 <= self.efficiency <= 1.0):
            raise ValueError(f"{self.name}: rendement tussen 50 en 100%")
        if self.driver == "mode" and not (self.mode_entity and self.mode_map.get("auto")):
            raise ValueError(f"{self.name}: kies de stand-instelling en welke stand 'nul op de meter' is")
        if self.driver == "setpoint" and not self.setpoint_entity:
            raise ValueError(f"{self.name}: kies de instelling voor het vermogen")
        if self.driver == "split" and not (self.charge_entity and self.discharge_entity):
            raise ValueError(f"{self.name}: kies de instellingen voor laad- en ontlaadvermogen")
        if self.driver == "scripts" and not self.script_auto:
            raise ValueError(f"{self.name}: kies minstens het script voor 'zelf gebruiken'")
        if self.driver not in ("mode", "setpoint", "split", "scripts"):
            raise ValueError(f"{self.name}: onbekende sturing {self.driver}")

    # wat deze sturing kan
    def can_save(self) -> bool:
        if self.driver == "mode" and self.force_entity:
            return bool(self.force_map.get("idle"))
        if self.driver == "mode" and self.mode_map:
            return bool(self.mode_map.get("save") or self.mode_map.get("idle"))
        if self.driver == "scripts":
            return bool(self.script_save)
        return True

    def can_charge(self) -> bool:
        if not self.grid_charge:
            return False
        if self.driver == "mode" and self.force_entity:
            return bool(self.force_map.get("charge"))
        if self.driver == "mode" and self.mode_map:
            return bool(self.mode_map.get("charge"))
        if self.driver == "scripts":
            return bool(self.script_charge)
        return True

    def charge_steps(self) -> list[float]:
        top = self.max_grid_charge_w or self.max_charge_w
        top = min(top, self.max_charge_w)
        if self.driver == "mode" and not self.charge_power_entity:
            return [top]                              # 'vol laden'-stand: vermogen kiest de batterij zelf
        return sorted({round(top * f) for f in (0.25, 0.5, 0.75, 1.0) if top * f >= 100})


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
    soc: float            # verwacht laadniveau (%) aan het eind van het uur = doel bij laden
    grid_kwh: float       # verwachte netafname (+) / teruglevering (−) met batterij
    price: float
    charge_w: float = 0.0
    why: str = ""


def _floor(cfg: BatteryConfig, start: datetime) -> float:
    """Ondergrens in kWh voor dit uur: minimum, of de (winter)reserve."""
    e_min = cfg.capacity_kwh * cfg.min_soc / 100
    if cfg.reserve_kwh > 0 and (not cfg.reserve_winter_only or start.month in WINTER) \
            and cfg.reserve_from <= start.hour < cfg.reserve_until:
        return max(e_min, min(cfg.capacity_kwh, e_min + cfg.reserve_kwh))
    return e_min


def _step(cfg: BatteryConfig, e: float, h: HourIn, action: str, charge_w: float = 0.0
          ) -> Optional[tuple[float, float, float, float]]:
    """Eén uur doorrekenen. Geeft (nieuwe lading kWh, kosten €, afname kWh, teruglevering kWh), of None als het niet kan."""
    cap = cfg.capacity_kwh
    eta = math.sqrt(cfg.efficiency)                       # verlies per richting
    c_max, d_max = cfg.max_charge_w / 1000, cfg.max_discharge_w / 1000
    surplus, need = max(0.0, -h.net_kwh), max(0.0, h.net_kwh)
    room = max(0.0, (cap - e) / eta)
    ch_sun = min(surplus, c_max, room)
    ch_grid = dis = 0.0
    if action == "charge":
        room_g = max(0.0, (cap * cfg.max_grid_soc / 100 - e) / eta - ch_sun)
        ch_grid = max(0.0, min(charge_w / 1000 - ch_sun, c_max - ch_sun, room_g))
        if cfg.peak_limit_w > 0:
            ch_grid = min(ch_grid, max(0.0, cfg.peak_limit_w / 1000 - need))
        if ch_grid < 0.05:
            return None                                   # vol of piekgrens: 'laden' is hier geen echte keuze
    elif action == "auto":
        dis = min(need, d_max, max(0.0, (e - _floor(cfg, h.start)) * eta))
    e2 = e + (ch_sun + ch_grid) * eta - dis / eta
    imp = need - dis + ch_grid
    exp = surplus - ch_sun
    cost = imp * h.price - exp * h.feed + cfg.cycle_cost * (ch_sun + ch_grid)
    if action != "auto":
        cost += cfg.switch_cost
    return e2, cost, imp, exp


def _options(cfg: BatteryConfig, h: HourIn) -> list[tuple[str, float]]:
    out = [("auto", 0.0)]
    if cfg.can_save() and h.net_kwh > 0:                   # vasthouden alleen als er geen zon over is
        out.append(("save", 0.0))
    if cfg.can_charge():
        out += [("charge", w) for w in cfg.charge_steps()]
    return out


def _simulate(cfg: BatteryConfig, soc_pct: float, hours: list[HourIn], acts: list[tuple[str, float]]) -> tuple[float, list]:
    e, total, rows = cfg.capacity_kwh * soc_pct / 100, 0.0, []
    for h, (a, w) in zip(hours, acts):
        r = _step(cfg, e, h, a, w) or _step(cfg, e, h, "save" if a == "charge" else "auto") or _step(cfg, e, h, "auto")
        e, c, imp, exp = r
        total += c
        rows.append((a, w, e, imp, exp))
    return total, rows


def _end_value(cfg: BatteryConfig, hours: list[HourIn]) -> float:
    """Wat een kWh die op het eind nog in de batterij zit waard is: een lage prijs van die periode."""
    ps = sorted(h.price for h in hours)
    low = ps[len(ps) // 4] if ps else 0.0
    return max(0.0, low * math.sqrt(cfg.efficiency) - cfg.cycle_cost)


def plan(cfg: BatteryConfig, soc_pct: float, hours: list[HourIn], levels: int = 0) -> list[HourPlan]:
    """Goedkoopste keuze per uur over de hele horizon (dynamisch programmeren over de lading).

    Per uur: zelf gebruiken, vasthouden, of laden van het net op een van de vermogensstappen. Verlies per richting,
    slijtage, schakelkosten en wat er op het eind nog in zit tellen mee. Het doelpercentage volgt uit het plan:
    laden tot wat nodig is om de dure uren te overbruggen, niet meer."""
    if not hours:
        return []
    cap = cfg.capacity_kwh
    levels = levels or int(min(300, max(60, round(cap / 0.02))))
    step = cap / levels
    INF = float("inf")
    n = len(hours)
    end_v = _end_value(cfg, hours)
    e_min = cap * cfg.min_soc / 100
    best = [[0.0] * (levels + 1) for _ in range(n + 1)]
    choice = [[("auto", 0.0)] * (levels + 1) for _ in range(n)]
    for s in range(levels + 1):
        best[n][s] = -max(0.0, s * step - e_min) * end_v
    opts = [_options(cfg, h) for h in hours]
    for t in range(n - 1, -1, -1):
        h, nxt = hours[t], best[t + 1]
        for s in range(levels + 1):
            e = s * step
            b, bc = INF, ("auto", 0.0)
            for a, w in opts[t]:
                r = _step(cfg, e, h, a, w)
                if r is None:
                    continue
                s2 = min(levels, max(0, int(round(r[0] / step))))
                v = r[1] + nxt[s2]
                if v < b - 1e-9:
                    b, bc = v, (a, w)
            best[t][s], choice[t][s] = b, bc
    acts = []
    e = cap * soc_pct / 100
    for t in range(n):
        s = min(levels, max(0, int(round(e / step))))
        a, w = choice[t][s]
        r = _step(cfg, e, hours[t], a, w) or _step(cfg, e, hours[t], "auto")
        acts.append((a, w))
        e = r[0]
    smart, rows = _simulate(cfg, soc_pct, hours, acts)
    plain, plain_rows = _simulate(cfg, soc_pct, hours, [("auto", 0.0)] * n)
    end_smart = rows[-1][2] if rows else 0
    end_plain = plain_rows[-1][2] if plain_rows else 0
    gain = (plain - end_plain * end_v) - (smart - end_smart * end_v)
    if gain < cfg.min_gain:                               # loont niet: gewoon zelf gebruiken
        rows = plain_rows
    out = []
    for t, (a, w, e2, imp, exp) in enumerate(rows):
        out.append(HourPlan(hours[t].start, a, round(100 * e2 / cap, 1), round(imp - exp, 3), hours[t].price,
                            charge_w=w if a == "charge" else 0.0, why=_why(cfg, hours, t, a)))
    return out


def _why(cfg: BatteryConfig, hours: list[HourIn], t: int, a: str) -> str:
    h = hours[t]
    later = hours[t + 1:]
    if a == "charge":
        if h.price < 0:
            return f"negatieve prijs ({_eur(h.price)}): je krijgt geld om te laden"
        top = max(later, key=lambda x: x.price, default=None)
        return f"nu {_eur(h.price)}, om {top.start:%H}:00 {_eur(top.price)}" if top else f"nu {_eur(h.price)}"
    if a == "save":
        top = max(later[:16], key=lambda x: x.price, default=None)
        return f"nu {_eur(h.price)}: bewaren voor {top.start:%H}:00 ({_eur(top.price)})" if top else "bewaren voor later"
    return ""


def _eur(v: float) -> str:
    return f"€ {v:.2f}".replace(".", ",")


def breakeven(cfg: BatteryConfig, hours: list[HourIn]) -> Optional[float]:
    """Vuistregel: laden van het net loont onder (duurste prijs straks × rendement − slijtage)."""
    if len(hours) < 2:
        return None
    top = max(h.price for h in hours[1:])
    return round(top * cfg.efficiency - cfg.cycle_cost - cfg.switch_cost, 3)


def plan_value(cfg: BatteryConfig, soc_pct: float, hours: list[HourIn], plan_: list[HourPlan]) -> dict:
    """Wat het plan oplevert tegenover 'gewoon nul op de meter' en tegenover geen batterij."""
    def run(acts):
        e, cost, exp_tot = cfg.capacity_kwh * soc_pct / 100, 0.0, 0.0
        for h, (a, w) in zip(hours, acts):
            r = _step(cfg, e, h, a, w) or _step(cfg, e, h, "auto")
            e, c, _, x = r
            cost += c
            exp_tot += x
        return cost, exp_tot, e
    end_v = _end_value(cfg, hours)
    smart, smart_exp, e1 = run([(p.action, p.charge_w) for p in plan_])
    plain, _, e2 = run([("auto", 0.0)] * len(hours))
    none = sum(max(0.0, h.net_kwh) * h.price - max(0.0, -h.net_kwh) * h.feed for h in hours)
    none_exp = sum(max(0.0, -h.net_kwh) for h in hours)
    e0 = cfg.capacity_kwh * soc_pct / 100
    return {"vs_plain_eur": round((plain - e2 * end_v) - (smart - e1 * end_v), 2),
            "vs_none_eur": round(none - (smart - (e1 - e0) * end_v), 2),
            "export_kwh": round(smart_exp, 2), "export_without_kwh": round(none_exp, 2),
            "grid_charge_kwh": round(sum(max(0.0, p.grid_kwh - max(0.0, h.net_kwh)) for p, h in zip(plan_, hours) if p.action == "charge"), 2),
            "breakeven": breakeven(cfg, hours)}


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
    last_full: Optional[datetime] = None  # laatst 100% (LFP-accu's willen dat af en toe voor een goed percentage)
    target_soc: Optional[float] = None    # doel van de huidige laadsessie
    charge_w: float = 0.0                 # gevraagd laadvermogen nu
    _mode_set: str = ""
    _force_set: str = ""
    _script_set: str = ""
    _switch_set: Optional[bool] = None
    _power_set: Optional[float] = None
    _target_w: float = 0.0
    _last_write: float = -1e9
    _peak_since: Optional[float] = None
    _peak_cap: Optional[float] = None
    _peak_block: float = -1e9
    _hour: Optional[datetime] = None

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
                v = v if c.power_charge_positive else -v
                if abs(v) <= 1.2 * max(c.max_charge_w, c.max_discharge_w) + 100:
                    self.power_w = v                    # losse onmogelijke pieken zijn uitleesfouten: negeren
            except Exception:
                self.power_w = None
        elif c.driver != "mode":
            self.power_w = self._target_w          # geen meting: neem aan dat hij doet wat we vragen

    def note_full(self, now: datetime) -> None:
        if (self.soc is not None and self.soc >= 99) or self.last_full is None:
            self.last_full = now

    def full_days(self, now: datetime) -> Optional[int]:
        return None if self.last_full is None else (now - self.last_full).days

    # ---- beslissen
    def current(self, now: datetime) -> Optional[HourPlan]:
        return next((p for p in self.plan if p.start <= now < p.start + timedelta(hours=1)), None)

    def decide_action(self, now: datetime, devices_on_cheap: bool, grid_w: Optional[float] = None,
                      mono: Optional[float] = None) -> str:
        c = self.cfg
        p = self.current(now)
        a = p.action if p else "auto"
        self.reason = (p.why if p and p.why else LABEL[a]) if p else "geen plan: zelf gebruiken"
        self.target_soc, self.charge_w = None, 0.0
        if a == "charge":
            self.target_soc = min(c.max_grid_soc, p.soc)
            self.charge_w = p.charge_w or c.max_charge_w
            if mono is not None and mono < self._peak_block:
                a, self.reason = ("save" if c.can_save() else "auto"), "piekgrens geraakt: even niet laden"
            elif self.soc is not None and self.soc >= self.target_soc - 1:
                a = "save" if c.can_save() else "auto"
                self.reason = f"doel {round(self.target_soc)}% bereikt: vasthouden tot {(p.start + timedelta(hours=1)):%H:%M}"
        elif a == "save" and grid_w is not None and grid_w < -200:
            a, self.reason = "auto", "zon over: weer zelf gebruiken"
        if a == "auto" and devices_on_cheap and c.can_save():
            a, self.reason = "save", "apparaten draaien op goedkope stroom: batterij spaart"
        if self.soc is not None and a == "auto" and self.soc <= _floor(c, now) / c.capacity_kwh * 100 and c.can_save() \
                and _floor(c, now) > c.capacity_kwh * c.min_soc / 100:
            a, self.reason = "save", "winterreserve vasthouden"
        self.action = a
        return a

    def peak_guard(self, grid_w: Optional[float], mono: float) -> Optional[str]:
        """Capaciteitstarief: loopt de netafname tijdens laden 20 s boven de grens, dan zachter laden of stoppen."""
        c = self.cfg
        if c.peak_limit_w <= 0 or self.action != "charge" or grid_w is None:
            self._peak_since = None
            return None
        if grid_w <= c.peak_limit_w:
            self._peak_since = None
            return None
        if self._peak_since is None:
            self._peak_since = mono
            return None
        if mono - self._peak_since < 20:
            return None
        self._peak_since = None
        cur = self._peak_cap or self.charge_w
        new = cur - (grid_w - c.peak_limit_w) - 100
        if new < 300:
            self._peak_block, self._peak_cap = mono + 1800, None
            self.action = "save" if c.can_save() else "auto"
            self.reason = "piekgrens geraakt: laden gestopt (30 min)"
            return "piekgrens: gestopt"
        self._peak_cap = new
        return f"piekgrens: laden naar {new:.0f} W"

    def target_w(self, grid_w: Optional[float]) -> float:
        """Voor batterijen zonder eigen 'nul op de meter': welk vermogen (+ laden, − ontladen)."""
        c = self.cfg
        cur = self.power_w if self.power_w is not None else self._target_w
        if self.action == "charge":
            t = min(self.charge_w or c.max_charge_w, self._peak_cap or 1e9)
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
        msgs = []
        if c.control_switch_entity:
            want_sw = self.action != "auto"
            if want_sw != self._switch_set:
                ha.call(c.control_switch_entity.split(".", 1)[0], "turn_on" if want_sw else "turn_off",
                        {"entity_id": c.control_switch_entity})
                self._switch_set = want_sw
        if c.driver == "scripts":
            script = {"auto": c.script_auto, "save": c.script_save or c.script_auto, "charge": c.script_charge or c.script_auto}[self.action]
            pw = round(min(self.charge_w or c.max_charge_w, self._peak_cap or 1e9)) if self.action == "charge" else 0
            key = f"{script}:{pw}"
            if key == self._script_set and mono - self._last_write < 3600:
                return None
            ha.call("script", "turn_on", {"entity_id": script, "variables": {"power_w": pw, "action": self.action}})
            self._script_set, self._last_write = key, mono
            return f"script {script.split('.')[-1]}" + (f" ({pw} W)" if pw else "")
        if c.driver == "mode":
            if self.action == "charge" and c.charge_power_entity:
                pw = round(min(self.charge_w or c.max_charge_w, self._peak_cap or 1e9))
                if self._power_set is None or abs(pw - self._power_set) >= 50:
                    ha.call(c.charge_power_entity.split(".", 1)[0], "set_value", {"entity_id": c.charge_power_entity, "value": pw})
                    self._power_set = pw
                    msgs.append(f"{pw} W")
            force = None
            if c.force_entity:                    # twee standen: regelmodus (zelf/handmatig) + gedwongen laden/stilstaan
                want = c.mode_map.get("auto") if self.action == "auto" else (c.mode_map.get("manual") or c.mode_map.get("auto"))
                force = c.force_map.get("charge" if self.action == "charge" else "idle")
            else:
                want = c.mode_map.get(self.action)
                if not want and self.action == "save":
                    want = c.mode_map.get("idle") or c.mode_map.get("auto")
            if force and force != getattr(self, "_force_set", ""):
                if want and want != self._mode_set:      # eerst de regelmodus, dan de opdracht
                    ha.call(c.mode_entity.split(".", 1)[0], "select_option", {"entity_id": c.mode_entity, "option": want})
                    self._mode_set = want
                    msgs.insert(0, f"stand {want}")
                ha.call(c.force_entity.split(".", 1)[0], "select_option", {"entity_id": c.force_entity, "option": force})
                self._force_set = force
                msgs.append(force)
            if want and want != self._mode_set:
                ha.call(c.mode_entity.split(".", 1)[0], "select_option", {"entity_id": c.mode_entity, "option": want})
                self._mode_set = want
                msgs.insert(0, f"stand {want}")
            return " ".join(msgs) or None
        # zelf regelen: eerst de stand op handmatig (als die bestaat), dan het vermogen
        if c.mode_entity and c.mode_map.get("manual") and self._mode_set != c.mode_map["manual"]:
            ha.call(c.mode_entity.split(".", 1)[0], "select_option", {"entity_id": c.mode_entity, "option": c.mode_map["manual"]})
            self._mode_set = c.mode_map["manual"]
        t = self.target_w(grid_w)
        if abs(t - self._target_w) < 40 and mono - self._last_write < 300:
            return None
        if mono - self._last_write < 8:
            return None
        self._write_power(ha, t)
        self._target_w, self._last_write = t, mono
        return f"{t:+.0f} W"

    def _write_power(self, ha, t: float) -> None:
        c = self.cfg
        if c.driver == "setpoint":
            v = t if c.setpoint_charge_positive else -t
            ha.call(c.setpoint_entity.split(".", 1)[0], "set_value", {"entity_id": c.setpoint_entity, "value": v})
        else:
            ha.call(c.charge_entity.split(".", 1)[0], "set_value", {"entity_id": c.charge_entity, "value": max(0, t)})
            ha.call(c.discharge_entity.split(".", 1)[0], "set_value", {"entity_id": c.discharge_entity, "value": max(0, -t)})

    def release(self, ha) -> None:
        """Veilig loslaten (stoppen, Basis, fout): batterij terug naar zijn eigen 'nul op de meter'."""
        c = self.cfg
        try:
            if c.driver == "scripts":
                ha.call("script", "turn_on", {"entity_id": c.script_auto, "variables": {"power_w": 0, "action": "auto"}})
            elif c.driver == "mode" or (c.mode_entity and c.mode_map.get("auto")):
                if c.force_entity and c.force_map.get("idle"):
                    ha.call(c.force_entity.split(".", 1)[0], "select_option", {"entity_id": c.force_entity, "option": c.force_map["idle"]})
                ha.call(c.mode_entity.split(".", 1)[0], "select_option", {"entity_id": c.mode_entity, "option": c.mode_map["auto"]})
            else:
                self._write_power(ha, 0)
            if c.control_switch_entity:
                ha.call(c.control_switch_entity.split(".", 1)[0], "turn_off", {"entity_id": c.control_switch_entity})
        finally:
            self._mode_set, self._script_set, self._switch_set, self._power_set = "", "", None, None
            self._force_set = ""
            self.action = "auto"

    def to_dict(self, tz, now: Optional[datetime] = None) -> dict:
        c = self.cfg
        nxt = None
        if now is not None:
            for p in self.plan:
                if p.start + timedelta(hours=1) > now and p.action != "auto":
                    nxt = {"start": p.start.astimezone(tz).strftime("%H:%M"), "action": p.action, "soc": p.soc, "why": p.why}
                    break
        return {"id": c.id, "name": c.name, "brand": c.brand, "capacity_kwh": c.capacity_kwh, "soc": self.soc,
                "kwh": None if self.soc is None else round(c.capacity_kwh * self.soc / 100, 2),
                "power_w": None if self.power_w is None else round(self.power_w),
                "action": self.action, "reason": self.reason, "online": self.online, "error": self.error,
                "min_soc": c.min_soc, "max_grid_soc": c.max_grid_soc, "value": self.value, "target_soc": self.target_soc,
                "charge_w": round(self.charge_w) if self.action == "charge" else 0, "next": nxt,
                "can": {"save": c.can_save(), "charge": c.can_charge()},
                "reserve": {"kwh": c.reserve_kwh, "from": c.reserve_from, "until": c.reserve_until, "winter_only": c.reserve_winter_only},
                "peak_limit_w": c.peak_limit_w,
                "full_days": None if now is None else self.full_days(now),
                "plan": [{"start": p.start.astimezone(tz).isoformat(timespec="minutes"), "action": p.action,
                          "soc": p.soc, "price": round(p.price, 4), "charge_w": round(p.charge_w), "why": p.why} for p in self.plan[:48]]}


def hourly_profile(rows: list[dict], tz, days: int = 14, weekend: Optional[bool] = None) -> Optional[list[float]]:
    """Gemiddelde netto afname per uur van de dag (+ afname, − teruglevering) uit de meter.

    Met weekend=True/False alleen weekend- of werkdagen (als daar genoeg van is)."""
    if not rows:
        return None
    last = max(r["ts"] for r in rows)
    rows = [r for r in rows if r["ts"] > last - days * 86400]
    if weekend is not None:
        sel = [r for r in rows if (datetime.fromtimestamp(r["ts"], tz).weekday() >= 5) == weekend]
        if len(sel) < 40:
            return hourly_profile(rows, tz, days)
        rows = sel
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
