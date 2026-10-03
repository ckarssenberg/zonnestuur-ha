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
COOLDOWN = {"surplus": timedelta(hours=20), "negative_tomorrow": timedelta(hours=20), "sunny_tomorrow": timedelta(hours=20),
            "expensive_evening": timedelta(hours=20), "offline": timedelta(hours=12), "meter": timedelta(hours=6),
            "inverter": timedelta(hours=6), "morning": timedelta(hours=20), "fail": timedelta(hours=6),
            "guarantee": timedelta(hours=6), "resolved": timedelta(0), "week": timedelta(days=6),
            "appliance": timedelta(0), "weather_today": timedelta(hours=20)}
TIP_KINDS = ("surplus", "negative_tomorrow", "sunny_tomorrow", "expensive_evening", "morning", "weather_today")
ALERT_KINDS = ("offline", "meter", "fail", "guarantee", "resolved")


@dataclass
class Notifier:
    """Meldregels (spaarzaam, altijd met iets wat je kunt doen):

    STORING  altijd en direct (ook 's nachts, tenzij uitgezet): meter > 5 min stil, apparaat > 30 min onbereikbaar,
             schakelen mislukt na twee pogingen, warm water / klaar-tijd in gevaar. Eén melding per storing, plus
             'opgelost' als het weer werkt.
    KANS     hoogstens één per dag, alleen 08:00–21:00: morgen negatieve prijzen, morgen veel zon, vanavond dure stroom,
             nu veel over terwijl alles al draait. Altijd met een bedrag erbij.
    RAPPORT  het weekrapport op zondag 19:00. Geen tips en rapporten tussen 22:00 en 07:00.
    WITGOED  zet je de was klaar met 'start op afstand', dan meteen wanneer hij start (en wat dat kost), en een
             melding als hij gestart is. Vergeten een programma te kiezen? Dan na 10 minuten een seintje.
             Niet tussen 22:00 en 07:00: dan komt de melding om 07:00.
    """
    service: str = ""                 # Home Assistant: bijv. notify.mobile_app_telefoon
    tips: bool = True
    alerts: bool = True
    reports: bool = True
    morning: bool = False             # dagelijkse ochtendtip (standaard uit: zo min mogelijk meldingen)
    appliances: bool = True           # witgoed: staat klaar (en wanneer hij start), en gestart
    night_alerts: bool = True         # storingen ook 's nachts
    channels: dict = field(default_factory=dict)      # volledige meld-instellingen (ntfy, telegram, e-mail)
    sent: dict = field(default_factory=dict)          # sleutel -> datetime laatst verstuurd
    _since: dict = field(default_factory=dict)        # sleutel -> monotone tijd waarop een toestand begon
    history: list = field(default_factory=list)       # laatste meldingen, voor het dashboard
    active: dict = field(default_factory=dict)        # openstaande storingen: sleutel -> titel
    queued: list = field(default_factory=list)        # storingen die elders zijn vastgesteld (bijv. schakelen mislukt)
    tip_day: str = ""

    @classmethod
    def from_config(cls, c: dict) -> Optional["Notifier"]:
        from .notify import channels_of
        if not c or not channels_of(c):
            return None
        return cls(service=c.get("service", ""), tips=c.get("tips", True), alerts=c.get("alerts", True),
                   reports=c.get("reports", True), morning=bool(c.get("morning", False)),
                   appliances=c.get("appliances", True),
                   night_alerts=c.get("night_alerts", True), channels=dict(c))

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

    @staticmethod
    def quiet(now: datetime) -> bool:
        return now.hour >= 22 or now.hour < 7

    def tip_allowed(self, now: datetime) -> bool:
        return self.tips and 8 <= now.hour < 21 and self.tip_day != now.date().isoformat()

    def queue_alert(self, key: str, title: str, msg: str) -> None:
        self.queued.append((key, "fail", title, msg))

    def _alert(self, out: list, key: str, kind: str, cond_held: bool, now: datetime, title: str, msg: str,
               resolved_msg: str) -> None:
        if cond_held:
            if key not in self.active and self._may(key, kind, now):
                self.active[key] = title
                out.append((key, kind, title, msg))
        elif key in self.active and not self._since.get(key):
            t = self.active.pop(key)
            out.append((f"ok:{key}:{now:%Y%m%d%H%M}", "resolved", f"Opgelost · {t}", resolved_msg))

    def evaluate(self, now: datetime, mono: float, *, grid_w: Optional[float], meter_online: bool,
                 has_panels: bool, devices: list, states: dict, tomorrow_negative: Optional[tuple],
                 tomorrow_sunny: Optional[dict] = None, evening_expensive: Optional[tuple] = None,
                 guarantee_risk: Optional[list] = None, value_kwh: float = 0.22,
                 today_tip: Optional[dict] = None) -> list[tuple]:
        """Geeft [(sleutel, soort, titel, tekst)] terug van meldingen die nu verstuurd moeten worden."""
        out: list[tuple] = []
        # ---- storingen
        if self.alerts:
            meter_down = not meter_online and grid_w is None
            self._alert(out, "meter", "meter", self._held("meter", meter_down, mono, 300), now,
                        "Zonnestuur kan de meter niet lezen",
                        "Al 5 minuten geen meterstand. Zonnestuur stuurt nu alleen nog op tijd en prijs. "
                        "Zit de P1-kabel of meter nog goed?", "De meter geeft weer waarden door.")
            for d in devices:
                st = states.get(d.id)
                down = bool(st) and not st.online and not st.offline_reason   # 'wacht tot je hem klaarzet' is geen storing
                k = f"offline:{d.id}"
                self._alert(out, k, "offline", self._held(k, down, mono, 1800), now, f"{d.name} reageert niet",
                            f"Zonnestuur kan {d.name} al een half uur niet bereiken. Staat hij aan en in het netwerk?",
                            f"{d.name} is weer bereikbaar.")
            for d, ready in guarantee_risk or []:
                k = f"guarantee:{d.id}:{ready:%Y%m%d%H%M}"
                if k not in self.active and self._may(k, "guarantee", now):
                    self.active[k] = f"{d.name} mogelijk niet op tijd klaar"
                    out.append((k, "guarantee", f"{d.name} mogelijk niet op tijd klaar",
                                f"{d.name} moet om {ready:%H:%M} klaar zijn, maar Zonnestuur kan hem nu niet bereiken. "
                                "Kijk of hij aan staat; zet hem anders zelf aan."))
            out.extend(i for i in self.queued if self._may(i[0], "fail", now))
            self.queued.clear()
        if self.quiet(now) and not self.night_alerts:
            out = [i for i in out if i[1] == "resolved"]
        # ---- kansen (hoogstens één per dag)
        if self.tip_allowed(now):
            tip = None
            if tomorrow_negative and now.hour >= 19 and (now.hour, now.minute) >= (19, 30):
                s, e, low = tomorrow_negative
                key = f"neg:{s:%Y-%m-%d}"
                if self._may(key, "negative_tomorrow", now):
                    gain = max(0.0, value_kwh - low)
                    tip = (key, "negative_tomorrow", "Morgen is stroom gratis of negatief",
                           f"Tussen {s:%H:%M} en {e:%H:%M} krijg je geld toe voor stroom (laagste {low * 100:.0f} ct/kWh). "
                           f"Zonnestuur plant je apparaten daar al op. Plan je was of vaat in dat venster: ≈ € {gain:.2f} voordeel per wasbeurt.")
            if tip is None and today_tip and 8 <= now.hour < 12:
                key = f"weer:{now:%Y-%m-%d}"
                if self._may(key, "weather_today", now):
                    tip = (key, "weather_today", today_tip["title"], today_tip["text"])
            if tip is None and evening_expensive and now.hour >= 15:
                s, e, p = evening_expensive
                key = f"duur:{s:%Y-%m-%d}"
                if self._may(key, "expensive_evening", now):
                    tip = (key, "expensive_evening", "Vanavond is stroom erg duur",
                           f"Tussen {s:%H:%M} en {e:%H:%M} kost stroom tot € {p:.2f} per kWh. Draai de was, droger of vaatwasser liever "
                           "eerder of later: dat scheelt al snel € 0,30 per beurt.")
            if tip is None and tomorrow_sunny and has_panels and (now.hour, now.minute) >= (19, 30):
                key = f"zonmorgen:{tomorrow_sunny['day']}"
                if self._may(key, "sunny_tomorrow", now):
                    tip = (key, "sunny_tomorrow", "Morgen veel zon over",
                           f"Tussen {tomorrow_sunny['from']} en {tomorrow_sunny['to']} verwacht Zonnestuur ± {tomorrow_sunny['kwh']:.0f} kWh over. "
                           f"Zet de was of vaatwasser klaar voor dat venster: ≈ € {value_kwh * 1.2:.2f} per wasbeurt.")
            if tip is None and has_panels:
                idle = [d for d in devices if states.get(d.id) and states[d.id].online and not states[d.id].on
                        and not d.one_shot]
                waiting = [d for d in devices if d.one_shot and states.get(d.id) and not states[d.id].online]
                cond = grid_w is not None and grid_w < -1500 and not idle and 9 <= now.hour < 17
                if self._held("surplus", cond, mono, 600) and self._may("surplus", "surplus", now):
                    extra = (" Zet de " + " of ".join(d.name.lower() for d in waiting[:2]) + " klaar met 'start op afstand'"
                             if waiting else " Tijd voor de wasmachine, vaatwasser of een extra lading")
                    tip = ("surplus", "surplus", "Gratis zonnestroom over",
                           f"Je levert nu {abs(grid_w) / 1000:.1f} kW terug en alles wat Zonnestuur kan aanzetten draait al."
                           + extra + f"; dan gebruik je je eigen stroom (≈ € {value_kwh:.2f} per kWh) in plaats van hem weg te geven.")
            if tip:
                out.append(tip)
        return [(k, kind, t, _nlnum(m)) for k, kind, t, m in out]

    def send(self, ha_factory: Callable, now: datetime, items: list[tuple], click: str = "") -> None:
        for key, kind, title, msg in items:
            self.sent[key] = now
            if kind in TIP_KINDS:
                self.tip_day = now.date().isoformat()
            self.history = ([{"at": now.isoformat(timespec="minutes"), "title": title, "message": msg, "kind": kind}] + self.history)[:30]
        if not items:
            return

        def _go():
            from .notify import send_all
            for _, _, title, msg in items:
                send_all(self.channels or {"service": self.service}, ha_factory, title, msg, click)
        threading.Thread(target=_go, daemon=True).start()


def _nlnum(msg: str) -> str:
    """Bedragen als '€ 0.40' naar Nederlandse notatie '€ 0,40'."""
    import re
    return re.sub(r"€ (-?\d+)\.(\d\d)", r"€ \1,\2", msg)


def negative_window(slots_upcoming: list, tomorrow) -> Optional[tuple]:
    """Uit [(start, eind, prijs)] het aaneengesloten blok met negatieve afnameprijs op 'tomorrow'."""
    neg = [(s, e, p) for s, e, p in slots_upcoming if p < 0 and s.date() == tomorrow]
    if not neg:
        return None
    return neg[0][0], neg[-1][1], min(p for _, _, p in neg)
