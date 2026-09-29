"""Zonnestuur leert je huis kennen.

Uit de meter (per uur) haalt Zonnestuur wat je huis *zelf* verbruikt, dus zonder de apparaten die Zonnestuur stuurt
en zonder de thuisbatterij:

    eigen verbruik = afname − teruglevering − batterij-laden + zonne-opwek − gestuurde apparaten

Daaruit leert hij:
  * je verbruiksprofiel per uur, apart voor werkdagen en het weekend (mediaan: één feestje verstoort niets);
  * je sluipverbruik (wat er 's nachts altijd loopt);
  * hoe lang elk apparaat per dag écht nodig heeft (boiler, warmtepomp, auto);
  * met een opwek-sensor: hoeveel je panelen echt leveren tegenover de voorspelling, per uur van de dag
    (schaduw van een boom 's ochtends, een oost-westdak, vuil op de panelen).

De planning (zon, prijzen, batterij) rekent daarna met dit geleerde huis in plaats van met vaste aannames.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from statistics import median
from typing import Optional

MIN_SAMPLES = 3          # zo vaak moet een uur gezien zijn voor een eigen waarde
LEARN_DAYS = 28          # zo ver kijkt hij terug voor het profiel
RUN_DAYS = 21            # en voor de looptijd per apparaat


def _daytype(d: datetime) -> str:
    return "weekend" if d.weekday() >= 5 else "werkdag"


def _q(vals: list[float], q: float) -> float:
    v = sorted(vals)
    return v[min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))]


@dataclass
class HouseModel:
    fallback_w: float = 350.0
    profile: dict = field(default_factory=dict)        # {"werkdag": [24 × W | None], "weekend": [...], "alle": [...]}
    night_w: Optional[float] = None                    # sluipverbruik
    days: int = 0                                      # met zoveel dagen is geleerd
    pv_factor: Optional[list] = None                   # per uur van de dag: echte opwek / voorspelling
    pv_factor_all: Optional[float] = None
    pv_days: int = 0
    device_run_min: dict = field(default_factory=dict)  # {id: {"min": minuten per dag, "kwh": kWh per dag, "days": n}}

    def base_w(self, when: datetime) -> float:
        """Verwacht eigen verbruik (W) van het huis op dit moment."""
        h = when.hour
        for key in (_daytype(when), "alle"):
            v = (self.profile.get(key) or [None] * 24)[h]
            if v is not None:
                return v
        return self.fallback_w

    def pv_calibration(self, when: datetime) -> float:
        if self.pv_factor and self.pv_factor[when.hour] is not None:
            return self.pv_factor[when.hour]
        return self.pv_factor_all or 1.0

    def run_min(self, device_id: str) -> Optional[float]:
        r = self.device_run_min.get(device_id)
        return r["min"] if r else None

    @property
    def confident(self) -> bool:
        return self.days >= 7

    def to_dict(self) -> dict:
        def rnd(a):
            return [None if v is None else round(v) for v in a] if a else None
        peak = None
        wk = self.profile.get("alle") or []
        if any(v is not None for v in wk):
            h = max(range(24), key=lambda i: wk[i] or 0)
            peak = {"hour": h, "w": round(wk[h] or 0)}
        return {"days": self.days, "confident": self.confident, "night_w": None if self.night_w is None else round(self.night_w),
                "profile": {k: rnd(v) for k, v in self.profile.items()}, "peak": peak,
                "day_kwh": {k: round(sum(x or 0 for x in v) / 1000, 1) for k, v in self.profile.items() if v},
                "pv_factor": [None if v is None else round(v, 2) for v in self.pv_factor] if self.pv_factor else None,
                "pv_factor_all": None if self.pv_factor_all is None else round(self.pv_factor_all, 2), "pv_days": self.pv_days,
                "device_run_min": {k: {"min": round(v["min"]), "kwh": round(v["kwh"], 1), "days": v["days"]}
                                   for k, v in self.device_run_min.items()}}


def learn(rows: list[dict], tz, has_panels: bool, fallback_w: float, device_days: Optional[dict] = None,
          device_power: Optional[dict] = None) -> HouseModel:
    """rows: uren uit house_hour (import_kwh, export_kwh, dev_kwh, batt_kwh, pv_kwh, pv_src, fc_kwh).

    pv_src: 2 = gemeten met opwek-sensor, 1 = geschat uit de zonvoorspelling, 0/None = onbekend (bijv. historie)."""
    m = HouseModel(fallback_w=fallback_w)
    if rows:
        last = max(r["ts"] for r in rows)
        rows = [r for r in rows if r["ts"] > last - LEARN_DAYS * 86400]
    buckets: dict[tuple, list] = {}
    all_vals, days = [], set()
    for r in rows:
        t = datetime.fromtimestamp(r["ts"], tz)
        src = r.get("pv_src") or 0
        pv = r.get("pv_kwh") or 0.0
        if has_panels and src == 0:
            # opwek onbekend: alleen uren zonder zon zijn betrouwbaar
            if r["export_kwh"] > 0.01 or 8 <= t.hour < 19:
                continue
        base = r["import_kwh"] - r["export_kwh"] - (r.get("batt_kwh") or 0.0) + pv - (r.get("dev_kwh") or 0.0)
        if base < -0.05:
            continue                       # meetfout of verkeerde schatting: niet van leren
        w = max(0.0, base) * 1000
        buckets.setdefault((_daytype(t), t.hour), []).append(w)
        buckets.setdefault(("alle", t.hour), []).append(w)
        all_vals.append(w)
        days.add(t.date())
    m.days = len(days)
    for key in ("werkdag", "weekend", "alle"):
        prof = []
        for h in range(24):
            v = buckets.get((key, h), [])
            prof.append(median(v) if len(v) >= MIN_SAMPLES else None)
        if any(x is not None for x in prof):
            m.profile[key] = prof
    if len(all_vals) >= 24:
        m.night_w = _q(all_vals, 0.1)
    # zonvoorspelling bijstellen met gemeten opwek
    pv_h, fc_h, pv_days = [0.0] * 24, [0.0] * 24, set()
    for r in rows:
        if (r.get("pv_src") or 0) == 2 and (r.get("fc_kwh") or 0) > 0.05:
            h = datetime.fromtimestamp(r["ts"], tz).hour
            pv_h[h] += r.get("pv_kwh") or 0.0
            fc_h[h] += r["fc_kwh"]
            pv_days.add(datetime.fromtimestamp(r["ts"], tz).date())
    m.pv_days = len(pv_days)
    if m.pv_days >= 5 and sum(fc_h) > 2:
        clamp = lambda x: max(0.2, min(1.6, x))  # noqa: E731
        m.pv_factor_all = clamp(sum(pv_h) / sum(fc_h))
        m.pv_factor = [clamp(pv_h[h] / fc_h[h]) if fc_h[h] > 0.5 else None for h in range(24)]
    # looptijd per apparaat
    for dev, per_day in (device_days or {}).items():
        vals = [k for k in per_day[-RUN_DAYS:] if k > 0.05]
        p = (device_power or {}).get(dev) or 0
        if len(vals) >= 4 and p > 0:
            kwh = median(vals)
            m.device_run_min[dev] = {"min": kwh * 1000 / p * 60, "kwh": kwh, "days": len(vals)}
    return m
