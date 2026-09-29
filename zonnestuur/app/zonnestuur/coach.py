"""Zonnecoach: hoeveel van je zonnestroom gebruik je zelf, wanneer is er vandaag stroom over, en wat kun je doen.

Twee getallen die iedereen snapt:
  zelf gebruikt   = (opwek − teruglevering) / opwek        hoeveel van je zonnestroom je zelf opmaakt
  zelfvoorzienend = (opwek − teruglevering) / verbruik     hoeveel van je verbruik uit eigen zon komt

Zonder zonnepanelen kijkt de coach naar goedkope uren: welk deel van je stroom kocht je onder het daggemiddelde?

Tips zijn persoonlijk (op je eigen meterdata en apparaten), gerangschikt op wat ze per jaar opleveren, en hebben
altijd concrete stappen. Opbrengsten zijn schattingen en worden zo genoemd.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
from typing import Optional


def rating(pct: Optional[float]) -> tuple[str, str]:
    if pct is None:
        return "", ""
    if pct >= 70:
        return "top", "Top: je gebruikt het grootste deel van je zon zelf"
    if pct >= 50:
        return "goed", "Goed op weg"
    if pct >= 30:
        return "kan-beter", "Hier valt nog veel te winnen"
    return "laag", "Het meeste van je zon gaat nu naar het net"


def _period(rows: list[dict]) -> dict:
    known = [r for r in rows if (r.get("pv_src") or 0) >= 1]
    pv = sum(r.get("pv_kwh") or 0 for r in known)
    exp = sum(r["export_kwh"] for r in known)
    imp = sum(r["import_kwh"] for r in known)
    own = max(0.0, pv - exp)
    use = own + imp
    return {"pv_kwh": round(pv, 1), "export_kwh": round(exp, 1), "import_kwh": round(imp, 1), "own_kwh": round(own, 1),
            "self_use_pct": round(100 * own / pv) if pv > 0.3 else None,
            "self_supply_pct": round(100 * own / use) if use > 0.3 and pv > 0.3 else None,
            "measured": all((r.get("pv_src") or 0) == 2 for r in known) if known else False}


def _cheap_share(rows: list[dict], tz) -> Optional[int]:
    """Deel van de afname in uren onder het daggemiddelde (zonder panelen de belangrijkste score)."""
    by_day: dict = {}
    for r in rows:
        if r["import_kwh"] > 0.01:
            by_day.setdefault(datetime.fromtimestamp(r["ts"], tz).date(), []).append((r["cost"] / r["import_kwh"], r["import_kwh"]))
    cheap = tot = 0.0
    for hours in by_day.values():
        if len(hours) < 12:
            continue
        avg = sum(p for p, _ in hours) / len(hours)
        for p, k in hours:
            tot += k
            if p < avg:
                cheap += k
    return round(100 * cheap / tot) if tot > 1 else None


def scores(rows: list[dict], tz, now: datetime, has_panels: bool) -> dict:
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    ts = lambda d: int(d.timestamp())  # noqa: E731
    out = {"today": _period([r for r in rows if r["ts"] >= ts(day0)]),
           "week": _period([r for r in rows if r["ts"] >= ts(day0 - timedelta(days=6))]),
           "month": _period([r for r in rows if r["ts"] >= ts(day0 - timedelta(days=29))])}
    days = []
    for i in range(13, -1, -1):
        d = day0 - timedelta(days=i)
        p = _period([r for r in rows if ts(d) <= r["ts"] < ts(d + timedelta(days=1))])
        days.append({"day": d.date().isoformat(), **p})
    out["days"] = days
    main = out["week"]["self_use_pct"]
    out["rating"], out["rating_text"] = rating(main)
    if not has_panels:
        out["cheap_pct_week"] = _cheap_share([r for r in rows if r["ts"] >= ts(day0 - timedelta(days=6))], tz)
        out["cheap_pct_month"] = _cheap_share([r for r in rows if r["ts"] >= ts(day0 - timedelta(days=29))], tz)
    return out


def solar_windows(now: datetime, production, base_w, days: int = 2, min_w: float = 800) -> list[dict]:
    """Aaneengesloten uren waarin naar verwachting minstens min_w over is (opwek − eigen verbruik)."""
    out = []
    start = now.replace(minute=0, second=0, microsecond=0)
    for dd in range(days):
        day = (start + timedelta(days=dd)).date()
        cur = None
        for h in range(24):
            t = start.replace(hour=0) + timedelta(days=dd, hours=h)
            p = production(t)
            s = None if p is None else p - base_w(t)
            if s is not None and s >= min_w and t + timedelta(hours=1) > now:
                if cur is None:
                    cur = {"day": day.isoformat(), "from": t, "to": t + timedelta(hours=1), "kwh": 0.0, "peak_w": 0.0}
                cur["to"] = t + timedelta(hours=1)
                cur["kwh"] += s / 1000
                cur["peak_w"] = max(cur["peak_w"], s)
            elif cur is not None:
                out.append(cur)
                cur = None
        if cur is not None:
            out.append(cur)
    best = []
    for w in out:
        if w["kwh"] >= 1.0:
            best.append({"day": w["day"], "tomorrow": w["from"].date() != now.date(), "from": w["from"].strftime("%H:%M"),
                         "to": w["to"].strftime("%H:%M"), "kwh": round(w["kwh"], 1), "peak_kw": round(w["peak_w"] / 1000, 1)})
    return best


def _nl(v: float, d: int = 0) -> str:
    """Getal op z'n Nederlands: 1.234,5"""
    return f"{v:,.{d}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _hhmm(h: int) -> str:
    return f"{h:02d}:00"


def tips(ctx: dict) -> list[dict]:
    """ctx: has_panels, export_per_day, import_evening_per_day, peak_hours, value_kwh, devices (dicts), batteries (bool),
    inverter (bool), night_w, avg_price, plan, window (eerstvolgend zonnevenster of None), best_battery (dict|None)."""
    T = []
    exp_day = ctx.get("export_per_day") or 0.0
    val = max(0.05, ctx.get("value_kwh") or 0.2)
    kinds = {d["kind"] for d in ctx.get("devices", [])}
    drivers = {d["driver"] for d in ctx.get("devices", [])}
    ph = ctx.get("peak_hours") or []
    span = f"{_hhmm(ph[0])}–{_hhmm(ph[-1] + 1)}" if ph else "11:00–15:00"
    w = ctx.get("window")
    wtxt = (f"{'morgen ' if w['tomorrow'] else 'vandaag '}{w['from']}–{w['to']}") if w else span

    def add(id_, title, text, how, kwh_year=0.0, eur_year=None, kind="zon"):
        eur = round(kwh_year * val) if eur_year is None else round(eur_year)
        T.append({"id": id_, "title": title, "text": text, "how": how, "kwh_year": round(kwh_year), "eur_year": eur, "kind": kind})

    if ctx.get("has_panels"):
        room = exp_day * 330               # wat er grofweg per jaar nog te halen is
        if exp_day > 1.0 and "ha_start_button" not in drivers:
            k = min(room * 0.25, 3 * 52 * 1.3)
            add("witgoed", "Was, droog en vaatwas in je zonnevenster",
                f"Je levert vooral terug tussen {span}. Een wasprogramma kost 1–2 kWh; draai je die in de zon, dan betaal je er bijna niets voor.",
                [f"Start de machine {wtxt}, of zet 's ochtends de uitgestelde start zo dat hij in dat venster klaar is.",
                 "Heeft je machine 'start op afstand' (Miele, Bosch/Siemens Home Connect, AEG, Samsung)? Koppel hem via Home Assistant en voeg hem toe in Zonnestuur.",
                 "Zet hem klaar met 'start op afstand': Zonnestuur start hem op het zonnigste moment en maakt het programma altijd af."], k)
        if exp_day > 1.5 and not ({"boiler", "heatpump"} & kinds):
            add("warmwater", "Sla zonnestroom op als warm water",
                "Een boiler of warmtepompboiler is de goedkoopste 'batterij': 3–5 kWh per dag gaat erin, en het warme water gebruik je 's avonds.",
                ["Elektrische boiler: laat een installateur er een Shelly Pro 1PM (of vergelijkbare schakelaar met CE-keurmerk) voor plaatsen.",
                 "Warmtepomp met warm water: koppel hem via Home Assistant; Zonnestuur zet de boilertemperatuur hoger als de zon schijnt.",
                 "Voeg hem toe in Zonnestuur en stel 'klaar om' in (bijv. 18:00): dan heb je altijd warm water."], min(room * 0.5, 4 * 330))
        if exp_day > 2.0 and "ev" not in kinds:
            add("auto", "Laad je elektrische auto op de zon",
                "Een auto is de grootste zonnespons: hij neemt tot 11 kW op en Zonnestuur laat de laadstroom per ampère meelopen met je overschot.",
                ["Koppel je laadpaal (Zaptec, Easee, Alfen, Wallbox, go-e, Peblar…) of de auto zelf via Home Assistant.",
                 "Voeg hem toe in Zonnestuur als 'Auto' en stel je vertrektijd in, per weekdag.",
                 "Laad thuis in het weekend en op thuiswerkdagen in de middag; Zonnestuur vult aan in de goedkoopste uren als de zon niet genoeg is."],
                min(room * 0.6, 2000))
        for d in ctx.get("devices", []):
            if d["kind"] == "ev" and d.get("modulating") and d.get("min_w", 0) > 2500 and exp_day > 1:
                add(f"ev1fase-{d['id']}", f"Laat {d['name']} ook bij weinig zon laden",
                    f"Op 3 fasen begint laden pas bij {_nl(d['min_w'] / 1000, 1)} kW overschot. Op 1 fase kan dat al vanaf 1,4 kW: op bewolkte dagen scheelt dat veel.",
                    ["Zet in je laadpaal (app of Home Assistant) automatisch wisselen tussen 1 en 3 fasen aan, als hij dat kan (Zaptec, Easee, go-e).",
                     f"Of stel in Zonnestuur bij {d['name']} 1 fase en 6 A minimaal in voor dagen met weinig zon."], min(room * 0.2, 600))
            if d["driver"] == "ha_setpoint" and d.get("boost_delta") is not None and d["boost_delta"] < 1.5:
                add(f"buffer-{d['id']}", f"Laat {d['name']} meer zonnewarmte opslaan",
                    f"Bij zon zet Zonnestuur {d['name']} nu maar {_nl(d['boost_delta'], 1)} °C hoger. Met 2–3 °C slaat je huis of boiler veel meer zonnewarmte op.",
                    [f"Ga naar Instellingen → {d['name']} → 'Temperatuur bij zon' en zet die 2 tot 3 °C boven normaal.",
                     "Voor warm water: 55–60 °C bij zon is prima en goed tegen legionella."], min(room * 0.15, 400))
            if d.get("start_surplus_w") and d.get("power_w") and d["start_surplus_w"] > d["power_w"] * 1.05:
                add(f"drempel-{d['id']}", f"Laat {d['name']} eerder starten",
                    f"{d['name']} start nu pas bij {_nl(d['start_surplus_w'])} W overschot, meer dan hij zelf gebruikt. Daardoor mist hij zonnige momenten.",
                    [f"Instellingen → {d['name']} → Geavanceerd: zet 'Aan bij overschot' leeg (automatisch) of op ongeveer {_nl(0.9 * d['power_w'])} W."],
                    min(room * 0.1, 200))
        bb = ctx.get("best_battery")
        if not ctx.get("batteries") and bb and bb.get("saving_year", 0) > 60 and (ctx.get("import_evening_per_day") or 0) > 1:
            add("batterij", f"Een thuisbatterij zou ± € {_nl(bb['saving_year'])} per jaar schelen",
                f"Overdag lever je terug, 's avonds koop je in. {bb['name']}: laden met je overschot, 's avonds gebruiken. Terugverdientijd ± {_nl(bb['payback_years'], 1) if bb.get('payback_years') else '?'} jaar (richtprijs, op je eigen meterdata).",
                ["Kijk eerst of warm water en de auto al op de zon draaien: dat is goedkoper dan een batterij.",
                 "Een stekkerbatterij (± 800 W) kun je zelf plaatsen; een grotere thuisbatterij laat je door een installateur aansluiten.",
                 "Koppel hem via Home Assistant: Zonnestuur plant laden en ontladen op je verbruik en de uurprijzen."],
                eur_year=bb["saving_year"], kind="batterij")
        if not ctx.get("inverter") and ctx.get("negative_feed"):
            add("omvormer", "Omvormer afknijpen als terugleveren geld kost",
                "Bij negatieve prijzen betaal je om terug te leveren. Zonnestuur kan je omvormer dan precies op je eigen verbruik zetten.",
                ["Controleer of je omvormer-integratie in Home Assistant een vermogensbegrenzing heeft (SolarEdge, Huawei, Growatt, Hoymiles/OpenDTU…).",
                 "Instellingen → Minder terugleveren → kies die instelling en vul het vermogen in."], 0, eur_year=15)
        if exp_day > 0.5:
            add("gewoontes", "Kleine gewoontes, samen veel zon",
                f"Alles wat je naar {span} schuift, gebruik je zelf in plaats van het voor een paar cent weg te geven.",
                ["Laad e-bike, telefoon, laptop, stofzuiger en gereedschap overdag.",
                 "Oven, airfryer of inductie op zonnige weekenddagen: kook vaker 's middags, of maak iets dat je 's avonds opwarmt.",
                 "Droogkast of wasdroger liever niet 's avonds.",
                 "Zwembad- of vijverpomp, verwarmde handdoekrek of airco: op een schakelaar in Zonnestuur, alleen bij zon."], min(room * 0.05, 150))
    else:
        add("goedkoop", "Verschuif naar de goedkoopste uren",
            "Zonder zonnepanelen is de winst: stroom gebruiken als hij goedkoop is. Zonnestuur ziet de prijzen van morgen al rond 13:00.",
            ["Kijk op het dashboard bij 'Zo plant Zonnestuur je dag' welke uren blauw (goedkoop) zijn.",
             "Zet de vaatwasser en wasmachine met uitgestelde start in die uren, of koppel 'start op afstand'.",
             "Auto, boiler en warmtepomp laat je door Zonnestuur automatisch in die uren draaien."], 300, kind="prijs")
        if "ev" not in kinds:
            add("auto-goedkoop", "Laad je auto in de goedkoopste nachturen",
                "Het verschil tussen het duurste en goedkoopste uur is vaak 15–25 cent per kWh. Bij 2.500 kWh per jaar laden scheelt dat snel honderden euro's.",
                ["Koppel je laadpaal via Home Assistant en voeg hem toe als 'Auto'.", "Stel je vertrektijd per weekdag in; Zonnestuur kiest de goedkoopste uren ervoor."],
                0, eur_year=250, kind="prijs")
    nw = ctx.get("night_w")
    if nw and nw > 200:
        extra = (nw - 150) * 8.76 * 0.5      # de helft van wat boven een zuinig huis zit, is meestal te halen
        add("sluip", f"Je sluipverbruik is {_nl(nw)} W",
            f"Dat loopt dag en nacht door: ± {_nl(nw * 8.76)} kWh per jaar. Zuinige huizen zitten rond 100–150 W.",
            ["Zet 's avonds de meterkast-stekkers één voor één uit en kijk bij 'Bij de meter' wat er zakt.",
             "Bekende sluipers: oude koelkast of vriezer, netwerkschijf/server, waterbed, aquarium, zwembadpomp, apparaten op stand-by.",
             "Een slimme stekker met meting (Shelly, HomeWizard) laat precies zien wat een apparaat gebruikt."], extra, kind="besparen")
    T.sort(key=lambda t: -t["eur_year"])
    return T


def morning_message(window: Optional[dict]) -> Optional[tuple[str, str]]:
    if not window or window["tomorrow"] or window["kwh"] < 3:
        return None
    return ("Vandaag zon over", f"Tussen {window['from']} en {window['to']} verwacht Zonnestuur ± {_nl(window['kwh'])} kWh over. "
            "Tijd voor de was, de vaatwasser of de auto: dan gebruik je je eigen stroom.")
