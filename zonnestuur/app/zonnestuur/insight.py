"""Inzicht in teruglevering: hoeveel, wanneer, wat het kost, en wat helpt om het terug te dringen.

Werkt op de uurboekhouding van het hele huis (house_hour). Alles is uit eigen meetdata berekend; waar we
iets aannemen (batterijprijzen, rendement) staat dat erbij.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

# Richtprijzen (incl. btw, 2026) en eigenschappen; alleen voor een eerste indruk van de terugverdientijd.
BATTERIES = [
    {"name": "Stekkerbatterij ± 5 kWh (800 W)", "kwh": 5.0, "kw": 0.8, "price": 1100},
    {"name": "Thuisbatterij ± 10 kWh (5 kW, installateur)", "kwh": 10.0, "kw": 5.0, "price": 5000},
]
ROUND_TRIP = 0.90


def _nl(v: float, d: int = 1) -> str:
    """Getal op z'n Nederlands: 1.234,5"""
    return f"{v:,.{d}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _hourly_prices(rows: list[dict]) -> tuple[float, float]:
    imp = sum(r["import_kwh"] for r in rows)
    exp = sum(r["export_kwh"] for r in rows)
    avg_imp = sum(r["cost"] for r in rows) / imp if imp > 0.1 else 0.30
    avg_exp = sum(r["revenue"] for r in rows) / exp if exp > 0.1 else 0.0
    return avg_imp, avg_exp


def simulate_battery(rows: list[dict], kwh: float, kw: float, avg_imp: float, avg_exp: float) -> dict:
    """Uur voor uur: laden van wat je anders teruglevert, ontladen als je anders afneemt."""
    soc = 0.0
    eff = ROUND_TRIP ** 0.5
    charged = discharged = saving = 0.0
    for r in rows:
        imp_p = r["cost"] / r["import_kwh"] if r["import_kwh"] > 0.01 else avg_imp
        exp_p = r["revenue"] / r["export_kwh"] if r["export_kwh"] > 0.01 else avg_exp
        if r["export_kwh"] > 0:
            c = min(r["export_kwh"], kw, (kwh - soc) / eff)
            soc += c * eff
            charged += c
            saving -= c * exp_p
        if r["import_kwh"] > 0:
            d = min(r["import_kwh"], kw, soc * eff)
            soc -= d / eff
            discharged += d
            saving += d * imp_p
    return {"charged_kwh": round(charged, 1), "discharged_kwh": round(discharged, 1), "saving": round(saving, 2)}


def compute(rows: list[dict], tz, has_panels: bool = True, devices: Optional[list] = None,
            inverter_limit: bool = False) -> dict:
    rows = sorted(rows, key=lambda r: r["ts"])
    if not rows:
        return {"ok": False, "reason": "nog geen meetgegevens"}
    days = sorted({datetime.fromtimestamp(r["ts"], tz).date() for r in rows})
    n_days = max(1, len(days))
    exp = sum(r["export_kwh"] for r in rows)
    imp = sum(r["import_kwh"] for r in rows)
    avg_imp, avg_exp = _hourly_prices(rows)
    profile_exp = [0.0] * 24
    profile_imp = [0.0] * 24
    for r in rows:
        h = datetime.fromtimestamp(r["ts"], tz).hour
        profile_exp[h] += r["export_kwh"]
        profile_imp[h] += r["import_kwh"]
    profile_exp = [round(v / n_days, 3) for v in profile_exp]
    profile_imp = [round(v / n_days, 3) for v in profile_imp]
    # Hoe groot is het gat tussen wat je terugleverde en wat je had kunnen besparen?
    missed = exp * (avg_imp - avg_exp)
    year = 365 / n_days
    peak_hours = sorted(range(24), key=lambda h: -profile_exp[h])[:4]
    peak_hours = sorted(h for h in peak_hours if profile_exp[h] > 0.05)
    evening_imp = sum(profile_imp[h] for h in list(range(17, 24)) + list(range(0, 7)))
    batteries = []
    for b in BATTERIES:
        sim = simulate_battery(rows, b["kwh"], b["kw"], avg_imp, avg_exp)
        per_year = sim["saving"] * year
        batteries.append({**b, **sim, "saving_year": round(per_year), "payback_years":
                          round(b["price"] / per_year, 1) if per_year > 1 else None})
    advice = []
    kinds = {getattr(d, "kind", "") for d in (devices or [])}
    drivers = {getattr(d, "driver", "") for d in (devices or [])}
    if has_panels and exp / n_days > 1.0:
        span = f"{peak_hours[0]:02d}:00–{peak_hours[-1] + 1:02d}:00" if peak_hours else "midden op de dag"
        advice.append({"title": f"Je levert vooral terug tussen {span}",
                       "text": f"Gemiddeld {_nl(exp / n_days)} kWh per dag. Alles wat je in die uren laat draaien, "
                               "is stroom die je niet hoeft te kopen."})
        if "ha_start_button" not in drivers:
            advice.append({"title": "Laat de vaatwasser, wasmachine of droger op de zon starten",
                           "text": "Heeft je machine 'start op afstand' (Miele, Bosch/Siemens Home Connect, AEG…)? Koppel hem via "
                                   "Home Assistant; Zonnestuur start hem dan in de zonnigste uren. Zo'n programma kost 1–2 kWh."})
        if "ev" not in kinds:
            advice.append({"title": "Een elektrische auto is de grootste zonnespons",
                           "text": "Een auto neemt 1,4 tot 11 kW op en regelt Zonnestuur traploos mee met de zon."})
        if "boiler" not in kinds and "heatpump" not in kinds:
            advice.append({"title": "Warm water op zonnestroom",
                           "text": "Een boiler of warmtepompboiler slaat zonnestroom op als warm water: vaak 3–5 kWh per dag."})
        paying = [b for b in batteries if b["payback_years"]]
        best = min(paying, key=lambda b: b["payback_years"]) if paying else batteries[0]
        if best["saving_year"] > 50 and evening_imp > 1:
            advice.append({"title": f"Een batterij zou ongeveer € {best['saving_year']} per jaar schelen",
                           "text": f"{best['name']}: je laadt hem met wat je nu teruglevert en gebruikt het 's avonds. "
                                   f"Terugverdientijd ongeveer {_nl(best['payback_years'] or 0)} jaar bij € {_nl(best['price'], 0)} (richtprijs)."})
        if not inverter_limit:
            advice.append({"title": "Omvormer begrenzen bij negatieve prijzen",
                           "text": "Kost terugleveren je geld (negatieve prijs of hoge terugleverkosten)? Koppel het "
                                   "vermogensbegrenzing van je omvormer in Home Assistant; Zonnestuur zet hem dan op 'nul terug'."})
    return {"ok": True, "days": n_days, "first": days[0].isoformat(), "last": days[-1].isoformat(),
            "export_kwh": round(exp, 1), "import_kwh": round(imp, 1),
            "export_per_day": round(exp / n_days, 2), "import_per_day": round(imp / n_days, 2),
            "avg_import_price": round(avg_imp, 4), "avg_export_value": round(avg_exp, 4),
            "missed_eur": round(missed, 2), "missed_eur_year": round(missed * year), "profile_export": profile_exp,
            "profile_import": profile_imp, "peak_hours": peak_hours, "batteries": batteries, "advice": advice,
            "seasonal_warning": n_days < 330}
