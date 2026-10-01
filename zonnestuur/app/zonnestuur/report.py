"""Doel, weekrapport, 'goed moment nu', contractvergelijking en 'Zo rekenen we'.

Pure rekenfuncties: de Engine geeft de gegevens, hier komt het verhaal uit.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable, Optional

CO2_KG_PER_KWH = 0.30        # gemiddelde Nederlandse stroommix (schatting, ronde waarde)
TAX_CREDIT = {2027: 628.69}  # vermindering energiebelasting per jaar incl. btw (Belastingplan 2027: € 519,58 excl.)


def _nl(v: float, d: int = 0) -> str:
    return f"{v:,.{d}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def eur(v: float) -> str:
    return ("−" if v < 0 else "") + "€ " + _nl(abs(v), 2)


def tax_credit_for(year: int) -> float:
    if year in TAX_CREDIT:
        return TAX_CREDIT[year]
    return TAX_CREDIT[min(TAX_CREDIT, key=lambda y: abs(y - year))]


# ------------------------------------------------------------------------------------------------ doel
def self_use(rows: list[dict]) -> dict:
    pv = sum(r.get("pv_kwh", 0.0) for r in rows)
    exp = sum(r.get("export_kwh", 0.0) for r in rows)
    measured = any(r.get("pv_src") == 2 for r in rows)
    return {"pv": pv, "export": exp, "measured": measured,
            "pct": None if pv < 0.5 else max(0.0, min(100.0, 100 * (pv - exp) / pv))}


def goal(cfg_goal: dict, rows_month: list[dict], dev_solar_kwh: float, eur_month: float, has_panels: bool,
         today: date) -> dict:
    """Doel als 'procentpunten boven wat je zonder Zonnestuur zou halen': zo telt het seizoen niet mee.

    Zonder Zonnestuur was de zonnestroom die naar de gestuurde apparaten ging grotendeels teruggeleverd.
    Verwacht zonder sturing = (opwek − teruglevering − zon naar apparaten) / opwek."""
    g = dict(cfg_goal or {})
    typ = g.get("type") or ("pct" if has_panels else "eur")
    out: dict = {"type": typ, "set": bool(cfg_goal)}
    import calendar
    days_in = calendar.monthrange(today.year, today.month)[1]
    frac = today.day / days_in
    if typ == "eur":
        target = float(g.get("eur_month") or 25)
        expected = target * frac
        out.update(target=round(target, 2), now=round(eur_month, 2), expected_now=round(expected, 2),
                   status="gehaald" if eur_month >= target else ("op koers" if eur_month >= expected * 0.95 else
                                                                  "net niet" if eur_month >= expected * 0.8 else "achter"),
                   text=f"Doel deze maand: {eur(target)} · nu {eur(eur_month)} (verwacht op dit moment {eur(expected)})")
        return out
    su = self_use(rows_month)
    if su["pct"] is None:
        out.update(status="geen data", text="Na een paar dagen met zon zie je hier je doel voor deze maand.")
        return out
    without = max(0.0, 100 * (su["pv"] - su["export"] - dev_solar_kwh) / su["pv"])
    extra = float(g.get("extra_pp") or 10)
    target = min(90.0, without + extra)
    pct = su["pct"]
    status = "op koers" if pct >= target else ("net niet" if pct >= target - 3 else "achter")
    if pct >= target and today.day >= days_in - 1:
        status = "gehaald"
    out.update(target=round(target), now=round(pct), without=round(without), extra_pp=extra, status=status,
               measured=su["measured"],
               text=f"Doel deze maand: {round(target)}% zelf gebruikt (zonder Zonnestuur ± {round(without)}%) · nu {round(pct)}%")
    return out


# ------------------------------------------------------------------------------------------------ goed moment nu
def moment(now: datetime, grid_w: Optional[float], surplus_next_w: Optional[float], price_now: Optional[float],
           price_rank: Optional[float], next_green: Optional[str], has_panels: bool) -> dict:
    """Stoplicht voor wat je zelf aanzet (wasmachine, droger, vaatwasser)."""
    surplus = -grid_w if grid_w is not None else None
    if (surplus is not None and surplus > 500 and (surplus_next_w is None or surplus_next_w > 500)) or \
            (price_now is not None and price_now < 0.05):
        why = "je hebt nu zonnestroom over" if surplus is not None and surplus > 500 else "stroom is nu heel goedkoop"
        return {"state": "groen", "word": "Nu goed moment", "text": f"Zet nu de was, droger of vaatwasser aan: {why}.",
                "icon": "sun"}
    if not has_panels and price_rank is not None and price_rank <= 0.25:
        return {"state": "groen", "word": "Nu goedkoop", "text": "Stroom hoort nu bij de goedkoopste van vandaag. Goed moment voor de was, droger of vaatwasser.",
                "icon": "sun"}
    if (surplus is None or surplus <= 100) and price_rank is not None and price_rank >= 0.75:
        return {"state": "rood", "word": f"Wacht tot {next_green}" if next_green else "Liever wachten",
                "text": "Stroom is nu bij de duurste van vandaag" + (f"; om {next_green} is het beter." if next_green else "."),
                "icon": "wait"}
    if next_green:
        why = f"Om {next_green} verwacht Zonnestuur zon over." if has_panels else f"Om {next_green} is stroom goedkoper."
        return {"state": "oranje", "word": f"Kan, beter om {next_green}", "text": f"Nu komt je stroom van het net. {why}",
                "icon": "later"}
    return {"state": "oranje", "word": "Gewoon moment", "text": "Geen zon over en geen bijzondere prijs: aanzetten kan, wachten levert weinig op.",
            "icon": "ok"}


# ------------------------------------------------------------------------------------------------ weekrapport
def week_report(*, week_start: date, eur_week: float, eur_prev: float, su_week: dict, goal_v: dict,
                per_device: dict[str, dict], names: dict[str, str], solar_to_devices: float, outlook: str,
                action: Optional[dict], overrides: int, failed: int, meetdagen: int, motivation: str) -> dict:
    lines = []
    diff = eur_week - eur_prev
    lines.append(f"Zonnestuur leverde deze week ≈ {eur(eur_week)} op" +
                 (f" ({'+' if diff >= 0 else '−'} {eur(abs(diff))} t.o.v. vorige week)." if abs(eur_prev) > 0.009 else "."))
    if su_week.get("pct") is not None:
        g = goal_v.get("target") if goal_v.get("type") == "pct" else None
        lines.append(f"Zelf gebruikt: {round(su_week['pct'])}% van je zonnestroom" + (f" (doel {g}%)." if g else "."))
    best = max(per_device.items(), key=lambda kv: kv[1].get("eur", 0), default=None)
    if best and best[1].get("eur", 0) > 0.01:
        lines.append(f"Beste apparaat: {names.get(best[0], best[0])}, {eur(best[1]['eur'])} en {_nl(best[1].get('kwh_solar', 0), 1)} kWh zon.")
    if solar_to_devices > 0.05:
        co2 = solar_to_devices * CO2_KG_PER_KWH
        lines.append(f"{_nl(solar_to_devices, 1)} kWh eigen zonnestroom ging naar je apparaten: ± {_nl(co2)} kg CO₂ die niet uit het net kwam.")
    if outlook:
        lines.append(outlook)
    if action:
        gain = f" (± {eur(action['eur_year'])[2:]} per jaar)" if action.get("eur_year") else ""
        lines.append(f"Eén ding om te doen: {action['title']}{gain}.")
    extra = []
    if overrides:
        extra.append(f"{overrides}× zelf ingegrepen")
    if failed:
        extra.append(f"{failed}× schakelen mislukt")
    if meetdagen:
        extra.append(f"{meetdagen} meetdag{'en' if meetdagen != 1 else ''} zonder sturing")
    title = f"Je week met Zonnestuur: {eur(eur_week)}"
    if motivation == "milieu" and solar_to_devices > 0.05:
        title = f"Je week met Zonnestuur: {_nl(solar_to_devices, 1)} kWh eigen zon gebruikt"
    return {"title": title, "lines": lines, "extra": extra, "message": "\n".join(lines[:6]),
            "week": f"{week_start:%G-W%V}", "from": week_start.isoformat(), "to": (week_start + timedelta(days=6)).isoformat(),
            "eur": round(eur_week, 2), "eur_prev": round(eur_prev, 2), "self_use_pct": None if su_week.get("pct") is None else round(su_week["pct"]),
            "solar_to_devices": round(solar_to_devices, 2), "co2_kg": round(solar_to_devices * CO2_KG_PER_KWH, 1)}


# ------------------------------------------------------------------------------------------------ contractcheck 2027
def compare(rows: list[dict], market: dict[int, float], tax_of: Callable[[int], float], dynamic: dict, fixed: dict,
            own_import_price: float, own: dict) -> dict:
    """Wat had elk contract gekost met jouw afname en teruglevering per uur (opgeschaald naar een jaar)?

    rows: house_hour met ts, import_kwh, export_kwh. market: ts -> kale marktprijs incl. btw (dynamisch).
    Vaste contracten: afnameprijs = jouw eigen huidige tarief (de verschillen zitten in vergoeding en terugleverkosten)."""
    if not rows:
        return {"ok": False, "error": "Nog geen verbruiksgegevens"}
    span_days = max(1.0, (rows[-1]["ts"] - rows[0]["ts"]) / 86400 + 1 / 24)
    scale = 365.0 / span_days
    imp = sum(r["import_kwh"] for r in rows)
    exp = sum(r["export_kwh"] for r in rows)
    covered = [r for r in rows if r["ts"] in market]
    out = []
    if covered and len(covered) >= 0.8 * len(rows):
        k = len(rows) / len(covered)
        for key, s in dynamic.items():
            cost = sum(r["import_kwh"] * (market[r["ts"]] + tax_of(r["ts"]) + s["markup"]) for r in covered)
            rev = sum(r["export_kwh"] * (market[r["ts"]] / 1.21 + s["feed_in"]) for r in covered)
            year = (cost - rev) * k * scale + 12 * (s.get("monthly") or 6.5)
            out.append({"key": key, "name": s["name"], "type": "dynamisch", "year_eur": round(year),
                        "verified": s.get("verified", False), "monthly_known": s.get("monthly") is not None})
    for key, s in fixed.items():
        net_feed = s["feed_in_2027"] - s["return_cost_2027"]
        year = (imp * own_import_price - exp * net_feed) * scale + 12 * float(own.get("fixed_monthly") or 0)
        out.append({"key": key, "name": s["name"], "type": "vast", "year_eur": round(year), "net_feed": round(net_feed, 4),
                    "verified": s.get("verified", False), "status": s.get("status", "")})
    out.sort(key=lambda x: x["year_eur"])
    return {"ok": True, "days": round(span_days), "import_kwh_year": round(imp * scale), "export_kwh_year": round(exp * scale),
            "rows": out, "dynamic_included": bool(covered and len(covered) >= 0.8 * len(rows))}
