"""Twee hulpjes om teruglevering te beperken en de klant erbij te helpen.

InverterLimiter  Kost terugleveren op dit moment geld (negatieve prijs, of terugleverkosten hoger dan de
                 vergoeding)? Dan knijpt Zonnestuur de omvormer af tot je precies je eigen verbruik opwekt.
                 Zodra terugleveren weer iets oplevert, gaat de omvormer terug naar vol vermogen.

Notifier         Stuurt via Home Assistant een melding naar je telefoon als je zelf iets kunt doen:
                 'je levert nu veel terug, zet de wasmachine aan', 'morgen negatieve prijzen', 'apparaat offline'.
                 Nooit vaker dan nodig: elke soort melding heeft een eigen rustperiode.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

log = logging.getLogger("zonnestuur.guard")


# ------------------------------------------------------------------------------------------ omvormer begrenzen
@dataclass
class InverterLimiter:
    entity: str                       # number.* / input_number.* met het maximale vermogen of percentage
    max_w: float                      # vol vermogen van de omvormer
    unit: str = "W"                   # "W", "kW" of "%"
    min_w: float = 0.0                # laagste instelling die de omvormer accepteert
    interval_s: float = 20.0          # niet vaker bijsturen dan dit
    margin_w: float = 50.0            # liever een klein beetje afnemen dan terugleveren
    limit_w: Optional[float] = None   # wat wij het laatst hebben ingesteld (None = niet begrensd)
    _last: float = field(default=-1e9, repr=False)
    reason: str = ""

    @classmethod
    def from_config(cls, c: dict) -> Optional["InverterLimiter"]:
        if not c or not c.get("entity") or not c.get("max_w"):
            return None
        return cls(entity=c["entity"], max_w=float(c["max_w"]), unit=c.get("unit", "W"),
                   min_w=float(c.get("min_w", 0)))

    @property
    def active(self) -> bool:
        return self.limit_w is not None

    def _value(self, w: float) -> float:
        if self.unit == "%":
            return round(max(0.0, min(100.0, 100.0 * w / self.max_w)), 1)
        if self.unit == "kW":
            return round(w / 1000, 2)
        return round(w)

    def decide(self, mono: float, grid_w: Optional[float], feed_value: float) -> Optional[float]:
        """Geeft de nieuwe waarde voor de entiteit terug, of None als er niets hoeft te veranderen.

        grid_w: + afname, − teruglevering.  feed_value: wat een teruggeleverde kWh nu oplevert (€).
        Regelt stapsgewijs: nieuwe grens = oude grens + netvermogen − marge. Zo komt de teruglevering op nul
        uit zonder dat we het opgewekte vermogen hoeven te kennen.
        """
        if mono - self._last < self.interval_s:
            return None
        if feed_value >= 0 or grid_w is None:
            if self.limit_w is None:
                return None
            self.limit_w, self._last = None, mono
            self.reason = "terugleveren levert weer iets op: vol vermogen"
            return self._value(self.max_w)
        # terugleveren kost geld
        current = self.max_w if self.limit_w is None else self.limit_w
        if self.limit_w is None and grid_w > -self.margin_w:
            return None                                # we leveren niet terug: niets aan de hand
        target = max(self.min_w, min(self.max_w, current + grid_w - self.margin_w))
        if self.limit_w is not None and abs(target - self.limit_w) < 100:
            return None
        self.limit_w, self._last = target, mono
        self.reason = f"terugleveren kost nu {abs(feed_value) * 100:.1f} ct/kWh: omvormer op {target:.0f} W"
        return self._value(target)

    def apply(self, ha, value: float) -> None:
        domain = self.entity.split(".", 1)[0]
        ha.call(domain, "set_value", {"entity_id": self.entity, "value": value})


# ------------------------------------------------------------------------------------------ meldingen
COOLDOWN = {"surplus": timedelta(hours=20), "negative_tomorrow": timedelta(hours=20),
            "offline": timedelta(hours=12), "meter": timedelta(hours=6), "inverter": timedelta(hours=6),
            "morning": timedelta(hours=20)}


@dataclass
class Notifier:
    service: str                      # bijv. notify.mobile_app_telefoon
    tips: bool = True
    alerts: bool = True
    sent: dict = field(default_factory=dict)          # sleutel -> datetime laatst verstuurd
    _since: dict = field(default_factory=dict)        # sleutel -> monotone tijd waarop een toestand begon
    history: list = field(default_factory=list)       # laatste meldingen, voor het dashboard

    @classmethod
    def from_config(cls, c: dict) -> Optional["Notifier"]:
        if not c or not c.get("service"):
            return None
        return cls(service=c["service"], tips=c.get("tips", True), alerts=c.get("alerts", True))

    def _held(self, key: str, cond: bool, mono: float, seconds: float) -> bool:
        """True als cond al 'seconds' lang onafgebroken waar is."""
        if not cond:
            self._since.pop(key, None)
            return False
        start = self._since.setdefault(key, mono)
        return mono - start >= seconds

    def _may(self, key: str, kind: str, now: datetime) -> bool:
        last = self.sent.get(key)
        return last is None or now - last >= COOLDOWN[kind]

    def evaluate(self, now: datetime, mono: float, *, grid_w: Optional[float], meter_online: bool,
                 has_panels: bool, devices: list, states: dict, tomorrow_negative: Optional[tuple]) -> list[tuple]:
        """Geeft [(sleutel, soort, titel, tekst)] terug van meldingen die nu verstuurd moeten worden."""
        out = []
        # Tip: flink terugleveren terwijl Zonnestuur niets meer kan bijschakelen
        if self.tips and has_panels:
            idle = [d for d in devices if states.get(d.id) and states[d.id].online and not states[d.id].on
                    and not d.one_shot]
            waiting = [d for d in devices if d.one_shot and states.get(d.id) and not states[d.id].online]
            cond = grid_w is not None and grid_w < -1500 and not idle and 9 <= now.hour < 17
            if self._held("surplus", cond, mono, 600) and self._may("surplus", "surplus", now):
                extra = (" Zet de " + " of ".join(d.name.lower() for d in waiting[:2]) + " klaar met 'start op afstand'"
                         if waiting else " Tijd voor de wasmachine, vaatwasser of een extra lading")
                out.append(("surplus", "surplus", "Gratis zonnestroom over",
                            f"Je levert nu {abs(grid_w) / 1000:.1f} kW terug en alles wat Zonnestuur kan aanzetten draait al."
                            + extra + "; dan gebruik je je eigen stroom in plaats van hem weg te geven."))
        if self.tips and tomorrow_negative:
            key = f"neg:{tomorrow_negative[0]:%Y-%m-%d}"
            if self._may(key, "negative_tomorrow", now) and now.hour >= 14:
                s, e, low = tomorrow_negative
                out.append((key, "negative_tomorrow", "Morgen negatieve stroomprijzen",
                            f"Tussen {s:%H:%M} en {e:%H:%M} krijg je geld toe voor stroom (laagste {low * 100:.0f} ct/kWh). "
                            "Zonnestuur plant je apparaten daar al op. Heb je een machine met 'start op afstand'? "
                            "Zet hem vanavond klaar."))
        if self.alerts:
            if self._held("meter", not meter_online and grid_w is None, mono, 600) and self._may("meter", "meter", now):
                out.append(("meter", "meter", "Zonnestuur kan de meter niet lezen",
                            "Al 10 minuten geen meterstand. Zonnestuur stuurt nu alleen nog op tijd en prijs."))
            for d in devices:
                st = states.get(d.id)
                down = bool(st) and not st.online and not st.offline_reason   # 'wacht tot je hem klaarzet' is geen storing
                k = f"offline:{d.id}"
                if self._held(k, down, mono, 1800) and self._may(k, "offline", now):
                    out.append((k, "offline", f"{d.name} reageert niet",
                                f"Zonnestuur kan {d.name} al een half uur niet bereiken. Staat hij aan en in het netwerk?"))
        return out

    def send(self, ha_factory: Callable, now: datetime, items: list[tuple]) -> None:
        for key, kind, title, msg in items:
            self.sent[key] = now
            self.history = ([{"at": now.isoformat(timespec="minutes"), "title": title, "message": msg}] + self.history)[:20]
        if not items:
            return

        def _go():
            try:
                ha = ha_factory()
                svc = self.service.split(".", 1)[1] if self.service.startswith("notify.") else self.service
                for _, _, title, msg in items:
                    ha.call("notify", svc, {"title": title, "message": msg})
            except Exception as exc:                    # meldingen mogen de regeling nooit storen
                log.warning("melding versturen mislukt: %s", exc)
        threading.Thread(target=_go, daemon=True).start()


def negative_window(slots_upcoming: list, tomorrow) -> Optional[tuple]:
    """Uit [(start, eind, prijs)] het aaneengesloten blok met negatieve afnameprijs op 'tomorrow'."""
    neg = [(s, e, p) for s, e, p in slots_upcoming if p < 0 and s.date() == tomorrow]
    if not neg:
        return None
    return neg[0][0], neg[-1][1], min(p for _, _, p in neg)
