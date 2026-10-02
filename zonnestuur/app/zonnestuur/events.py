"""Logboek: bij elke schakeling één regel waarom, en achteraf wat het opleverde.

Mensen vertrouwen een automatisch systeem als ze zien wat het deed, waarom, en wat het opleverde. Elke actie van
Zonnestuur krijgt daarom een vaste reden (code) met een zin in gewone taal, plus de cijfers waarop de beslissing
gebaseerd was. Staat een apparaat weer uit, dan komt erbij hoeveel kWh het gebruikte, hoeveel uit eigen zon,
en wat dat (geschat) opleverde of kostte.

Daarnaast: controle of een schakelopdracht echt is uitgevoerd (betrouwbaarheid).
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Optional

READY_WORD = {"boiler": "warm", "ev": "geladen", "heatpump": "op temperatuur"}

# Apparaten waarvan de aan/uit-stand na een opdracht betrouwbaar terug te lezen is
VERIFY_DRIVERS = ("shelly", "shelly_gen1", "homewizard_socket", "tasmota", "esphome", "ha_switch", "mqtt_switch",
                  "homey_switch")
CONFIRM_S = 60.0


def _nl(v: float, d: int = 0) -> str:
    return f"{v:,.{d}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def eur(v: float) -> str:
    return ("−" if v < 0 else "") + "€ " + _nl(abs(v), 2)


def classify(reason: str, on: bool) -> str:
    r = (reason or "").lower()
    if r.startswith("handmatig"):
        return "HANDMATIG"
    if r.startswith("meetdag"):
        return "MEETDAG"
    if r.startswith("garantie"):
        return "GARANTIE"
    if r.startswith("nu vol laden"):
        return "HANDMATIG"
    if r.startswith("onder ") and "%" in r:
        return "AUTO_MIN"
    if "doel bereikt" in r:
        return "DOEL_BEREIKT"
    if r.startswith("boven je maximumprijs"):
        return "MAXPRIJS"
    if "negatieve" in r:
        return "PRIJS_NEGATIEF"
    if r.startswith("goedkoop"):
        return "PRIJS_LAAG"
    if r.startswith("wacht tot") and "instelling" in r:
        return "NIET_VOOR"
    if on and ("zon" in r or "overschot" in r):
        return "ZON_OVERSCHOT"
    if not on and r in ("geen overschot", "te weinig zon", "wacht op stabiel overschot"):
        return "ZON_WEG"
    if not on and (r.startswith("vandaag klaar") or r.startswith("wacht op goedkoop") or r == "niets gepland"):
        return "PLAN_KLAAR"
    if r.startswith("programma"):
        return "PROGRAMMA"
    if r.startswith("geen meterdata"):
        return "GEEN_METER"
    return "OVERIG"


def explain(code: str, on: bool, now: datetime, device, reason: str, inp: dict) -> str:
    """Eén regel in gewone taal."""
    t = now.strftime("%H:%M")
    name = device.name
    surplus = inp.get("surplus_w")
    price = inp.get("price_now")
    rank = inp.get("price_rank")
    if code == "HANDMATIG":
        until = inp.get("until")
        return f"Door jou {'aangezet' if on else 'uitgezet'}" + (f" tot {until}." if until else ".")
    if code == "MEETDAG":
        return (f"Meetdag: vandaag stuurt Zonnestuur {name} niet, zodat we eerlijk kunnen meten wat sturing oplevert. "
                f"{name} doet vandaag wat hij zonder Zonnestuur ook zou doen.")
    if code == "GARANTIE":
        m = re.search(r"(\d\d:\d\d)", reason)
        word = READY_WORD.get(device.kind, "klaar")
        return f"Aan om {t}: anders is {name.lower()} om {m.group(1) if m else 'de klaar-tijd'} niet {word}. Dit stuk komt van het net."
    if code == "AUTO_MIN":
        m = re.search(r"onder (\d+)%", reason)
        return f"Aan om {t}: de accu zat onder je minimum van {m.group(1) if m else '?'}%. Tot dat minimum laadt hij meteen, de rest in goedkope uren."
    if code == "DOEL_BEREIKT":
        return f"Uit om {t}: {reason.replace(': doel bereikt', '')}, je doel is bereikt."
    if code == "MAXPRIJS":
        return f"Uit om {t}: stroom is nu duurder dan jouw maximumprijs. Hij laadt verder zodra het goedkoper is."
    if code == "PRIJS_NEGATIEF":
        return f"Aan om {t}: de stroomprijs is negatief ({eur(price) if price is not None else 'onder nul'}); je krijgt geld om stroom te gebruiken."
    if code == "PRIJS_LAAG":
        extra = f", bij de goedkoopste {max(1, round(rank * 100))}% van vandaag" if rank is not None else ""
        m = re.search(r"klaar om (\d\d:\d\d)", reason)
        why = f" De zon redt het niet vóór {m.group(1)}." if m else (" Er is te weinig zon verwacht." if "zon" in reason else "")
        return f"Aan om {t}: stroom kost nu {eur(price) if price is not None else 'weinig'} per kWh{extra}.{why}"
    if code == "ZON_OVERSCHOT":
        s = f"Aan om {t}: {_nl(max(0, surplus or 0) / 1000, 1)} kW zonnestroom over" if surplus is not None else f"Aan om {t}: zonnestroom over"
        h = inp.get("sun_hours")
        if h:
            s += f", nog ± {_nl(h, 1)} uur zon verwacht"
        if "beste" in (reason or ""):
            s += " (zonnigste uren van vandaag)"
        return s + "."
    if code == "ZON_WEG":
        imp = -(surplus or 0)
        return (f"Uit om {t}: te weinig zon, het huis haalde {_nl(max(0, imp) / 1000, 1)} kW van het net."
                if surplus is not None and imp > 50 else f"Uit om {t}: te weinig zonnestroom over.")
    if code == "PLAN_KLAAR":
        m = re.search(r"\((\d\d:\d\d)\)", reason)
        if m:
            return f"Uit om {t}: wacht op een goedkoper uur ({m.group(1)})."
        return f"Uit om {t}: {name.lower()} heeft vandaag genoeg gedraaid."
    if code == "NIET_VOOR":
        return f"Uit: {name.lower()} mag van jou pas na {inp.get('not_before', 'de ingestelde tijd')}."
    if code == "PROGRAMMA":
        return f"Gestart om {t}: het programma wordt altijd afgemaakt."
    if code == "GEEN_METER":
        return f"Uit om {t}: de meter gaf even geen waarde, dus Zonnestuur speelt op zeker."
    return f"{'Aan' if on else 'Uit'} om {t}: {reason}."


def outcome(kwh: float, kwh_solar: float, value: float) -> str:
    if kwh < 0.01:
        return "Nam geen stroom op (al vol of klaar)."
    s = f"Gebruikte {_nl(kwh, 2)} kWh"
    if kwh_solar >= 0.01:
        s += f", waarvan {_nl(kwh_solar, 2)} uit eigen zon"
    if value >= 0.005:
        s += f" · ≈ {eur(value)} bespaard"
    elif value <= -0.005:
        s += f" · kostte ≈ {eur(-value)} extra (voor je comfort of de klaar-tijd)"
    return s + "."


class EventTracker:
    """Houdt per apparaat het lopende 'aan'-blok bij en schrijft het logboek."""

    def __init__(self, ledger):
        self.ledger = ledger
        self.open: dict[str, dict] = {}           # device -> {"id", "ts", "kwh", "kwh_solar", "eur"}
        self.last_code: dict[str, str] = {}

    def switched(self, now: datetime, device, on: bool, reason: str, inputs: dict) -> None:
        code = classify(reason, on)
        text = explain(code, on, now, device, reason, inputs)
        if not on:
            self.close(now, device.id)
        eid = self.ledger.add_event(int(now.timestamp()), device.id, device.name, "on" if on else "off", code, text,
                                    {k: (round(v, 4) if isinstance(v, float) else v) for k, v in inputs.items() if v is not None})
        self.last_code[device.id] = code
        if on:
            self.open[device.id] = {"id": eid, "ts": int(now.timestamp()), "kwh": 0.0, "kwh_solar": 0.0, "eur": 0.0}

    def note(self, now: datetime, device_id: str, name: str, code: str, text: str, inputs: Optional[dict] = None) -> None:
        """Gebeurtenis zonder aan/uit (omvormer, batterij, mislukte schakeling)."""
        self.ledger.add_event(int(now.timestamp()), device_id, name, "note", code, text, inputs or {})

    def energy(self, per_device: dict[str, tuple]) -> None:
        for dev, (kwh, kwh_solar, value) in per_device.items():
            o = self.open.get(dev)
            if o:
                o["kwh"] += kwh
                o["kwh_solar"] += kwh_solar
                o["eur"] += value

    def close(self, now: datetime, device_id: str) -> None:
        o = self.open.pop(device_id, None)
        if o:
            self.ledger.close_event(o["id"], int(now.timestamp()), o["kwh"], o["kwh_solar"], o["eur"])

    def view(self, since_ts: int, limit: int = 100) -> list[dict]:
        rows = self.ledger.events(since_ts, limit)
        for r in rows:
            o = next((v for v in self.open.values() if v["id"] == r["id"]), None)
            if o:                                  # nog bezig: tussenstand
                r["kwh"], r["kwh_solar"], r["eur"], r["running"] = round(o["kwh"], 3), round(o["kwh_solar"], 3), round(o["eur"], 2), True
            if r["action"] == "on" and r["kwh"] is not None and (not r.get("running") or r["kwh"] >= 0.01):
                r["outcome"] = outcome(r["kwh"], r["kwh_solar"] or 0.0, r["eur"] or 0.0)
        return rows


def _view_device(self, device_id: str, since_ts: int) -> list[dict]:
    rows = self.ledger.events(since_ts, 1, device=device_id)
    for r in rows:
        o = self.open.get(device_id)
        if o and o["id"] == r["id"]:
            r["kwh"], r["kwh_solar"], r["eur"], r["running"] = round(o["kwh"], 3), round(o["kwh_solar"], 3), round(o["eur"], 2), True
        if r["action"] == "on" and r["kwh"] is not None and (not r.get("running") or r["kwh"] >= 0.01):
            r["outcome"] = outcome(r["kwh"], r["kwh_solar"] or 0.0, r["eur"] or 0.0)
    return rows


EventTracker.view_device = _view_device


class Health:
    """Werkt het schakelen echt? Een opdracht telt als gelukt als de stand binnen een minuut klopt."""

    def __init__(self):
        self.pending: dict[str, dict] = {}       # device -> {"want", "at", "retried"}
        self.day = ""
        self.ok = 0
        self.failed = 0
        self.failures: list[dict] = []           # recente mislukte schakelingen
        self.meter_ok_mono: Optional[float] = None

    def _roll(self, now: datetime) -> None:
        d = now.date().isoformat()
        if d != self.day:
            self.day, self.ok, self.failed = d, 0, 0

    def sent(self, now: datetime, mono: float, device, on: bool) -> None:
        self._roll(now)
        if device.driver in VERIFY_DRIVERS:
            self.pending[device.id] = {"want": on, "at": mono, "retried": False}
        else:
            self.ok += 1

    def send_failed(self, now: datetime, device, err: str) -> None:
        self._roll(now)
        self.failed += 1
        self.failures = ([{"at": now.isoformat(timespec="minutes"), "device": device.id, "name": device.name,
                           "error": err[:160]}] + self.failures)[:10]

    def check(self, now: datetime, mono: float, device, actual_on: Optional[bool]) -> Optional[str]:
        """'retry' = opnieuw sturen, 'failed' = definitief mislukt, None = niets doen."""
        p = self.pending.get(device.id)
        if not p:
            return None
        self._roll(now)
        if actual_on is not None and actual_on == p["want"]:
            self.pending.pop(device.id, None)
            self.ok += 1
            return None
        if mono - p["at"] < CONFIRM_S:
            return None
        if not p["retried"]:
            p["retried"], p["at"] = True, mono
            return "retry"
        self.pending.pop(device.id, None)
        self.send_failed(now, device, "stand veranderde niet na twee pogingen")
        return "failed"

    def view(self, now: datetime, mono: float) -> dict:
        same = self.day == now.date().isoformat()
        ok, failed = (self.ok, self.failed) if same else (0, 0)
        age = None if self.meter_ok_mono is None else max(0.0, mono - self.meter_ok_mono)
        state = "ok" if age is not None and age < 30 else ("warn" if age is not None and age < 300 else "bad")
        return {"meter_age_s": None if age is None else round(age), "meter_state": state, "local": True,
                "switches_today": ok + failed, "failed_today": failed,
                "failures": self.failures[:3], "pending": len(self.pending)}


def price_rank(price: Optional[float], day_prices: list[float]) -> Optional[float]:
    """0 = goedkoopste moment van de dag, 1 = duurste."""
    if price is None or len(day_prices) < 4:
        return None
    below = sum(1 for p in day_prices if p < price - 1e-9)
    return below / max(1, len(day_prices) - 1)


def sun_hours_ahead(now: datetime, production_w, base_w, threshold_w: float, max_h: int = 10) -> Optional[float]:
    """Hoeveel uur er vanaf nu naar verwachting nog genoeg overschot is (aaneengesloten)."""
    from datetime import timedelta
    h = 0.0
    t = now
    for _ in range(max_h * 4):
        t = t + timedelta(minutes=15)
        p = production_w(t)
        if p is None:
            return None if h == 0 else h
        if p - base_w(t) < threshold_w * 0.8:
            break
        h += 0.25
    return h if h > 0 else None


def _isnan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)
