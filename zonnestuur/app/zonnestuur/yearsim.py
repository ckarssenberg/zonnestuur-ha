"""Jaarberekening met echte uurprijzen en echte zoninstraling.

Rekent voor één huishouden een jaar lang uur voor uur uit wat stroom kost, bij verschillende manieren van
aansturen. Alles volgens de regels vanaf 2027 (geen salderen): wat je terugleveft, levert de kale
marktprijs plus of min de vergoeding van je leverancier op.

Apparaten (standaard, allemaal aan te passen):
  boiler   2 kW aan/uit, 4 kWh per dag, warm om 18:00 (venster: 18:00 gisteren tot 18:00 vandaag)
  auto     laadt 1-fase 1,4–3,7 kW traploos, 7,5 kWh per werkdag / 4 kWh per weekenddag,
           thuis van 18:00 tot 07:30, in het weekend ook overdag; vol om 07:30

Manieren van aansturen:
  gewoon         boiler warmt na gebruik op (07:00 en 20:00), auto laadt direct bij thuiskomst
  tijdklok       boiler 's nachts vanaf 23:00, auto laadt vanaf 23:00
  goedkoopst     alleen de goedkoopste uren binnen het venster (geen rekening met de zon)
  alleen zon     eerst zonne-overschot; wat ontbreekt vlak voor de klaar-tijd (vast contract-gedrag)
  zon+goedkoop   eerst zonne-overschot; wat ontbreekt in de goedkoopste uren binnen het venster
                 (Zonnestuur met dynamisch contract)

Aannames die het resultaat gunstiger maken dan de praktijk:
  - de zonvoorspelling is perfect (in werkelijkheid zit hij er soms naast)
  - het huisverbruik volgt een vast dagpatroon
Aannames die het ongunstiger maken:
  - apparaten zonder zonnepanelen draaien in hele uren
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

STRATEGIES = ("gewoon", "tijdklok", "goedkoopst", "alleen zon", "zon+goedkoop")

# Huisverbruik zonder gestuurde apparaten, per uur (aandeel van een dag), grofweg het NEDU-profiel
BASE_SHAPE = [0.030, 0.026, 0.024, 0.023, 0.023, 0.026, 0.036, 0.046, 0.045, 0.040, 0.038, 0.038,
              0.039, 0.037, 0.036, 0.038, 0.044, 0.056, 0.066, 0.066, 0.061, 0.055, 0.047, 0.037]


@dataclass
class Household:
    kwp: float = 5.0                       # 0 = geen zonnepanelen
    base_kwh_year: float = 2800.0          # gewoon huisverbruik zonder boiler en auto
    boiler_kwh_day: float = 4.0
    boiler_kw: float = 2.0
    ev_kwh_weekday: float = 7.5
    ev_kwh_weekend: float = 4.0
    ev_min_kw: float = 1.4
    ev_max_kw: float = 3.7
    markup: float = 0.018                  # inkoopvergoeding incl. btw (Tibber)
    feed_in_adjust: float = -0.018         # verkoopvergoeding bij teruglevering
    energy_tax: float = 0.10648            # energiebelasting 2027 incl. btw (voorstel)
    fixed_import: Optional[float] = None   # vast contract: prijs per kWh (dan geen uurprijzen)
    fixed_feed_in: float = 0.04            # vast contract: netto vergoeding per kWh vanaf 2027
    tz: str = "Europe/Amsterdam"


@dataclass
class Day:
    start: datetime                        # lokale middernacht
    price: list                            # 24 marktprijzen incl. btw (€/kWh)
    pv: list                               # 24 × kWh zonnestroom
    base: list                             # 24 × kWh huisverbruik


def build_days(hh: Household, hours_utc: list[tuple[str, float]], solar: Optional[dict]) -> list[Day]:
    """Zet uurreeksen (UTC) om naar dagen in lokale tijd. Ontbrekende uren krijgen de waarde van het uur ervoor."""
    tz = ZoneInfo(hh.tz)
    price_by = {datetime.fromisoformat(k.replace("Z", "+00:00")): v for k, v in hours_utc}
    gti_by, temp_by = {}, {}
    if solar and solar.get("time"):
        for t, g, c in zip(solar["time"], solar.get("gti", []), solar.get("temp", [])):
            ts = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
            gti_by[ts], temp_by[ts] = (g or 0.0), (c if c is not None else 10.0)
    if not price_by:
        return []
    first = min(price_by).astimezone(tz)
    last = max(price_by).astimezone(tz)
    day = first.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    days, prev_p = [], None
    base_day = hh.base_kwh_year / 365
    while day + timedelta(days=1) <= last:
        price, pv, base = [], [], []
        for h in range(24):
            local = (day + timedelta(hours=h))
            ts = local.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
            p = price_by.get(ts, prev_p if prev_p is not None else 0.1)
            prev_p = p
            g = gti_by.get(ts, 0.0)
            tc = temp_by.get(ts, 10.0)
            # Paneeltemperatuur ruwweg lucht + 25 °C bij volle zon; -0,4 %/°C boven 25 °C; 14 % systeemverlies
            cell = tc + 25.0 * g / 1000.0
            eff = 0.86 * (1 - 0.004 * max(0.0, cell - 25.0))
            price.append(p)
            pv.append(hh.kwp * g / 1000.0 * eff)
            base.append(base_day * BASE_SHAPE[h])
        days.append(Day(day, price, pv, base))
        day += timedelta(days=1)
    return days


# ---------------------------------------------------------------- toewijzing per apparaat
def _hours_cheapest(slots: list[tuple[int, float]], need: float, kw: float) -> dict[int, float]:
    """Goedkoopste slots eerst, tot de energie er is. slots: (index, prijs). Geeft index -> kWh."""
    out, left = {}, need
    for i, _p in sorted(slots, key=lambda x: x[1]):
        if left <= 1e-9:
            break
        e = min(kw, left)
        out[i] = e
        left -= e
    return out


def _hours_last(slots: list[int], need: float, kw: float) -> dict[int, float]:
    out, left = {}, need
    for i in reversed(slots):
        if left <= 1e-9:
            break
        e = min(kw, left)
        out[i] = e
        left -= e
    return out


def _hours_first(slots: list[int], need: float, kw: float) -> dict[int, float]:
    out, left = {}, need
    for i in slots:
        if left <= 1e-9:
            break
        e = min(kw, left)
        out[i] = e
        left -= e
    return out


def simulate(hh: Household, days: list[Day], strategy: str) -> dict:
    """Rekent het hele jaar door. Werkt op een doorlopende uurreeks, zodat vensters over middernacht gaan."""
    n = len(days) * 24
    price = [p for d in days for p in d.price]
    pv = [x for d in days for x in d.pv]
    load = [x for d in days for x in d.base]
    dev = [0.0] * n
    fixed = hh.fixed_import is not None

    def surplus(i: int) -> float:
        return pv[i] - load[i] - dev[i]

    def imp_price(i: int) -> float:
        return hh.fixed_import if fixed else price[i] + hh.energy_tax + hh.markup

    def exp_price(i: int) -> float:
        return hh.fixed_feed_in if fixed else price[i] / 1.21 + hh.feed_in_adjust

    for di, d in enumerate(days):
        base_i = di * 24
        weekend = d.start.weekday() >= 5
        # ---- boiler: venster 18:00 gisteren -> 18:00 vandaag
        if di > 0:
            win = list(range(base_i - 6, base_i + 18))
            need = hh.boiler_kwh_day
            if strategy == "gewoon":
                plan = _hours_first([base_i + 7, base_i + 8], need * 0.6, hh.boiler_kw)
                plan.update(_hours_first([base_i - 4, base_i - 3], need * 0.4, hh.boiler_kw))
            elif strategy == "tijdklok":
                plan = _hours_first(list(range(base_i - 1, base_i + 7)), need, hh.boiler_kw)
            elif strategy == "goedkoopst":
                plan = _hours_cheapest([(i, imp_price(i)) for i in win], need, hh.boiler_kw)
            else:
                # Zon eerst: uren met het meeste overschot (minstens de helft van het vermogen)
                sunny = sorted((i for i in win if surplus(i) >= hh.boiler_kw * 0.5), key=lambda i: -surplus(i))
                plan, left = {}, need
                for i in sunny:
                    if left <= 1e-9:
                        break
                    e = min(hh.boiler_kw, left)
                    plan[i] = e
                    left -= e
                rest = [i for i in win if i not in plan]
                if left > 1e-9:
                    if strategy == "zon+goedkoop" and not fixed:
                        plan.update(_hours_cheapest([(i, imp_price(i)) for i in rest], left, hh.boiler_kw))
                    else:
                        plan.update(_hours_last(rest, left, hh.boiler_kw))
            for i, e in plan.items():
                dev[i] += e
        # ---- auto: vandaag 18:00 -> morgen 07:30 (plus overdag in het weekend)
        need = hh.ev_kwh_weekend if weekend else hh.ev_kwh_weekday
        if di + 1 < len(days):
            night = list(range(base_i + 18, base_i + 24 + 7))          # 18:00 .. 06:59 (07:30 afgerond)
            day_home = list(range(base_i + 9, base_i + 17)) if weekend else []
            win = day_home + night
            if strategy == "gewoon":
                plan = _hours_first(night, need, hh.ev_max_kw)
            elif strategy == "tijdklok":
                plan = _hours_first(list(range(base_i + 23, base_i + 31)), need, hh.ev_max_kw)
            elif strategy == "goedkoopst":
                plan = _hours_cheapest([(i, imp_price(i)) for i in win], need, hh.ev_max_kw)
            else:
                plan, left = {}, need
                for i in win:                                            # traploos op overschot
                    s = surplus(i)
                    if s >= hh.ev_min_kw and left > 1e-9:
                        e = min(hh.ev_max_kw, s, left)
                        plan[i] = e
                        left -= e
                rest = [i for i in win if i not in plan]
                if left > 1e-9:
                    if strategy == "zon+goedkoop" and not fixed:
                        plan.update(_hours_cheapest([(i, imp_price(i)) for i in rest], left, hh.ev_max_kw))
                    else:
                        plan.update(_hours_last(rest, left, hh.ev_max_kw))
            for i, e in plan.items():
                dev[i] += e

    cost = imp_kwh = exp_kwh = self_used = dev_kwh = 0.0
    for i in range(n):
        net = load[i] + dev[i] - pv[i]
        if net >= 0:
            cost += net * imp_price(i)
            imp_kwh += net
        else:
            cost -= (-net) * exp_price(i)
            exp_kwh += -net
        self_used += min(pv[i], load[i] + dev[i])
        dev_kwh += dev[i]
    return {"strategy": strategy, "cost": round(cost, 2), "import_kwh": round(imp_kwh), "export_kwh": round(exp_kwh),
            "self_used_kwh": round(self_used), "device_kwh": round(dev_kwh), "pv_kwh": round(sum(pv)),
            "days": len(days)}


def run_all(hh: Household, days: list[Day], strategies=STRATEGIES) -> dict:
    res = [simulate(hh, days, s) for s in strategies]
    ref = res[0]["cost"]
    for r in res:
        r["saving_vs_gewoon"] = round(ref - r["cost"], 2)
    scale = 365 / max(1, len(days))
    return {"days": len(days), "scale_to_year": round(scale, 3), "results": res}


def household_from_query(q: dict, cfg=None) -> Household:
    """Parameters uit de URL (?kwp=5&ev=7.5&boiler=4&vast=0.27)."""
    hh = Household()
    c = getattr(cfg, "contract", None)
    if c is not None and getattr(c, "type", "") == "dynamic":
        hh.markup = c.supplier_markup
        if c.feed_in_adjust is not None:
            hh.feed_in_adjust = c.feed_in_adjust

    def num(key, default):
        try:
            return float(q.get(key, [default])[0])
        except (TypeError, ValueError):
            return default
    hh.kwp = num("kwp", hh.kwp)
    hh.ev_kwh_weekday = num("ev", hh.ev_kwh_weekday)
    hh.ev_kwh_weekend = num("ev_weekend", hh.ev_kwh_weekend)
    hh.ev_max_kw = num("ev_kw", hh.ev_max_kw)
    hh.boiler_kwh_day = num("boiler", hh.boiler_kwh_day)
    hh.base_kwh_year = num("huis", hh.base_kwh_year)
    if "vast" in q:
        hh.fixed_import = num("vast", 0.27)
        hh.fixed_feed_in = num("terug", hh.fixed_feed_in)
    return hh
