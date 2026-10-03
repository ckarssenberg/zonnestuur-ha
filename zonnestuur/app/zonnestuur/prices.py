"""Stroomprijzen: vast contract of dynamische uurprijzen (EnergyZero, geen API-sleutel nodig)."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .adapters import http_get_json
from .config import ContractConfig
from .suppliers import energy_tax_for

log = logging.getLogger(__name__)

ENERGYZERO_URL = "https://api.energyzero.nl/v1/energyprices"
# Sinds 1 oktober 2025 is de day-ahead-markt per kwartier. Deze publieke EnergyZero-bron geeft kwartierprijzen
# (kale marktprijs, excl. btw): één aanroep met de datum van vandaag geeft gisteren, vandaag en vanaf ±13:00 morgen.
ENERGYZERO_QUARTER_URL = "https://public.api.energyzero.nl/public/v1/prices"


@dataclass
class PriceSlot:
    start: datetime      # tijdzone-bewust (UTC)
    end: datetime
    market: float        # kale marktprijs €/kWh incl. btw, zoals EnergyZero hem geeft


class PriceProvider:
    """Geeft per moment de prijs van afname en teruglevering."""

    def __init__(self, contract: ContractConfig, enabled: bool = True,
                 fetcher: Optional[Callable[[str], dict]] = None):
        self.contract = contract
        self.enabled = enabled and contract.type == "dynamic"
        self.fetcher = fetcher or http_get_json
        self.slots: list[PriceSlot] = []
        self._last_fetch = 0.0
        self.live_price: Optional[float] = None     # actuele prijs uit Home Assistant (bijv. Tibber)
        self.live_at: Optional[datetime] = None

    def set_live_price(self, price: Optional[float], at: datetime) -> None:
        """Actuele all-in afnameprijs van je eigen leverancier (via een Home Assistant-sensor)."""
        self.live_price, self.live_at = price, at

    def _live(self, when: datetime) -> Optional[float]:
        if self.live_price is None or self.live_at is None:
            return None
        return self.live_price if abs((when - self.live_at).total_seconds()) < 900 else None

    def energy_tax(self, when: datetime) -> float:
        return self.contract.energy_tax if self.contract.energy_tax is not None else energy_tax_for(when.year)

    def _feed_in_adjust(self) -> float:
        c = self.contract
        return c.feed_in_adjust if c.feed_in_adjust is not None else -c.feed_in_cost

    # ---- publieke API -------------------------------------------------
    def import_price(self, when: datetime, live: bool = True) -> float:
        lp = self._live(when) if live else None
        if lp is not None:
            return lp
        tou = self.grid_tou(when)
        if self.contract.type == "fixed":
            return self.contract.import_price + tou
        slot = self._slot_at(when)
        if slot is None:
            return self.contract.import_price + tou
        return slot.market + self.energy_tax(when) + self.contract.supplier_markup + tou

    def marginal_return_cost(self) -> float:
        """Wat één extra teruggeleverde kWh aan terugleverkosten kost.

        per_kwh: het bedrag per kWh. vast: niets (je betaalt hoe dan ook). staffel: de sprong naar de volgende staffel,
        omgeslagen over de kWh tot die grens (een schatting: meer terugleveren duwt je een staffel omhoog)."""
        c = self.contract
        mode = getattr(c, "return_cost_mode", "per_kwh")
        if mode == "vast":
            return 0.0
        if mode == "staffel" and c.return_cost_tiers:
            tiers = sorted((float(a), float(b)) for a, b in c.return_cost_tiers)
            e = float(c.export_kwh_year or getattr(self, "export_estimate", 0.0) or 0.0)
            cur = max((t for t in tiers if t[0] <= e), default=tiers[0])
            nxt = next((t for t in tiers if t[0] > e), None)
            if not nxt:
                return 0.0
            return max(0.0, (nxt[1] - cur[1]) * 12 / max(1.0, nxt[0] - cur[0]))
        return c.return_cost

    def grid_tou(self, when: datetime) -> float:
        """Tijdsafhankelijk nettarief per kWh (vanaf de ingestelde datum, verwacht 2028)."""
        c = self.contract
        if not c.grid_tou:
            return 0.0
        try:
            if when.date().isoformat() < (c.grid_tou_from or "2028-01-01"):
                return 0.0
        except AttributeError:
            return 0.0
        hm = when.astimezone(when.tzinfo).strftime("%H:%M")
        for b in c.grid_tou:
            a, z = str(b.get("from", "00:00")), str(b.get("to", "24:00"))
            if (a <= hm < z) if a < z else (hm >= a or hm < z):
                try:
                    return float(b.get("eur_kwh", 0.0))
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def feed_in_price(self, when: datetime) -> float:
        if self.contract.type == "fixed":
            return self.contract.feed_in_price - self.marginal_return_cost()
        slot = self._slot_at(when)
        extra = self.marginal_return_cost() if getattr(self.contract, "return_cost_mode", "per_kwh") == "staffel" else 0.0
        if slot is None:
            return self.contract.feed_in_price - extra
        return slot.market / 1.21 + self._feed_in_adjust() - extra

    def value_of_own_kwh(self, when: datetime) -> float:
        """Wat een kWh eigen zonnestroom oplevert: afnameprijs min terugleververgoeding."""
        return self.import_price(when) - self.feed_in_price(when)

    @property
    def quarter(self) -> bool:
        """Zijn de prijzen per kwartier (in plaats van per uur)?"""
        return bool(self.slots) and (self.slots[0].end - self.slots[0].start) < timedelta(minutes=59)

    def upcoming(self, start: datetime, end: datetime, hourly: bool = True) -> list[tuple[datetime, datetime, float]]:
        """Prijsblokken tussen start en end met hun afnameprijs (alleen bij dynamisch contract).

        Standaard per uur (kwartierprijzen gemiddeld), zodat de planners met hele uren blijven werken.
        hourly=False geeft de blokken zoals de markt ze heeft (per kwartier)."""
        out = []
        for s in self.slots:
            if s.end > start and s.start < end:
                out.append((max(s.start, start), min(s.end, end), self.import_price(s.start, live=False)))
        if not hourly or not self.quarter:
            return out
        buckets: dict = {}
        for a, b, p in out:
            h = a.replace(minute=0, second=0, microsecond=0)
            bk = buckets.setdefault(h, [a, b, 0.0, 0.0])
            bk[0], bk[1] = min(bk[0], a), max(bk[1], b)
            w = (b - a).total_seconds()
            bk[2] += p * w
            bk[3] += w
        return [(v[0], v[1], v[2] / v[3]) for h, v in sorted(buckets.items()) if v[3] > 0]

    def best_quarter(self, start: datetime, end: datetime, minutes: int = 15) -> Optional[datetime]:
        """Goedkoopste begin (op een kwartier) voor iets dat 'minutes' duurt, binnen [start, end)."""
        q = [x for x in self.upcoming(start, end, hourly=False)]
        if not q:
            return None
        n = max(1, round(minutes / max(1, (q[0][1] - q[0][0]).total_seconds() / 60)))
        best, cost = None, float("inf")
        for i in range(len(q) - n + 1):
            c = sum(p for _, _, p in q[i:i + n])
            if c < cost:
                best, cost = q[i][0], c
        return best

    def due(self, now: datetime) -> bool:
        """Moeten de prijzen opnieuw worden opgehaald?

        Elk uur, of eerder als de komende 12 uur nog niet bekend zijn. De prijzen van morgen verschijnen pas
        in de loop van de middag: dan hoogstens eens per kwartier opnieuw proberen (niet elke regelronde)."""
        if not self.enabled:
            return False
        age = time.time() - self._last_fetch
        if age >= 3600:
            return True
        if not self.slots:
            return age >= 600
        return age >= 900 and not self._covers(now + timedelta(hours=12))

    def refresh(self, now: datetime, force: bool = False) -> None:
        if not self.enabled:
            return
        if not force and not self.due(now):
            return
        day_start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=2)
        day_end = day_start + timedelta(days=2, hours=2)
        url = (f"{ENERGYZERO_URL}?fromDate={_iso(day_start)}&tillDate={_iso(day_end)}"
               f"&interval=4&usageType=1&inclBtw=true")
        try:
            slots = self._quarters(now)
            if not slots:                                   # terugval: uurprijzen
                data = self.fetcher(url)
                for p in data.get("Prices", []):
                    start = datetime.fromisoformat(p["readingDate"].replace("Z", "+00:00"))
                    slots.append(PriceSlot(start, start + timedelta(hours=1), float(p["price"])))
            if slots:
                self.slots = sorted(slots, key=lambda s: s.start)
            self._last_fetch = time.time()
            log.info("Prijzen opgehaald: %d %s", len(slots), "kwartieren" if self.quarter else "uurblokken")
        except Exception as exc:
            log.warning("Prijzen ophalen mislukt, gebruik vaste waarden: %s", exc)
            self._last_fetch = time.time() - 3000  # over 10 minuten opnieuw proberen
            if not self.slots:
                self._last_fetch = time.time()     # nog niets: over 10 minuten (due: age >= 600)

    def _quarters(self, now: datetime) -> list[PriceSlot]:
        """Kwartierprijzen ophalen; lege lijst als de bron niet werkt (dan uurprijzen)."""
        if getattr(self, "_no_quarters_until", 0) > time.time():
            return []
        local = now.astimezone(timezone(timedelta(hours=1)))   # dag zoals de API hem wil (Nederlandse datum)
        try:
            data = self.fetcher(f"{ENERGYZERO_QUARTER_URL}?energyType=ENERGY_TYPE_ELECTRICITY&date={local:%d-%m-%Y}"
                                f"&interval=INTERVAL_QUARTER")
        except Exception as exc:
            log.info("Kwartierprijzen niet beschikbaar (%s); uurprijzen gebruikt", exc)
            self._no_quarters_until = time.time() + 6 * 3600
            return []
        by_start: dict = {}
        for row in (data or {}).get("base") or []:
            try:
                a = datetime.fromisoformat(str(row["start"]).replace("Z", "+00:00"))
                b = datetime.fromisoformat(str(row["end"]).replace("Z", "+00:00"))
                v = float((row.get("price") or {}).get("value"))
            except (KeyError, TypeError, ValueError):
                continue
            if b <= a or b - a > timedelta(hours=1):
                continue                                    # alleen kwartier- of uurblokken
            by_start[a] = PriceSlot(a, b, v * 1.21)         # kaal excl. btw -> zoals de uurbron (incl. btw)
        return [by_start[k] for k in sorted(by_start)]

    # ---- intern ---------------------------------------------------------
    def _slot_at(self, when: datetime) -> Optional[PriceSlot]:
        w = when.astimezone(timezone.utc)
        for s in self.slots:
            if s.start <= w < s.end:
                return s
        return None

    def _covers(self, when: datetime) -> bool:
        return self._slot_at(when) is not None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
