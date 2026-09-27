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
        if self.contract.type == "fixed":
            return self.contract.import_price
        slot = self._slot_at(when)
        if slot is None:
            return self.contract.import_price
        return slot.market + self.energy_tax(when) + self.contract.supplier_markup

    def feed_in_price(self, when: datetime) -> float:
        if self.contract.type == "fixed":
            return self.contract.feed_in_price - self.contract.return_cost
        slot = self._slot_at(when)
        if slot is None:
            return self.contract.feed_in_price
        return slot.market / 1.21 + self._feed_in_adjust()

    def value_of_own_kwh(self, when: datetime) -> float:
        """Wat een kWh eigen zonnestroom oplevert: afnameprijs min terugleververgoeding."""
        return self.import_price(when) - self.feed_in_price(when)

    def upcoming(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime, float]]:
        """Uurblokken tussen start en end met hun afnameprijs (alleen bij dynamisch contract)."""
        out = []
        for s in self.slots:
            if s.end > start and s.start < end:
                out.append((max(s.start, start), min(s.end, end), self.import_price(s.start, live=False)))
        return out

    def refresh(self, now: datetime, force: bool = False) -> None:
        if not self.enabled:
            return
        if not force and time.time() - self._last_fetch < 3600 and self._covers(now + timedelta(hours=12)):
            return
        day_start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=2)
        day_end = day_start + timedelta(days=2, hours=2)
        url = (f"{ENERGYZERO_URL}?fromDate={_iso(day_start)}&tillDate={_iso(day_end)}"
               f"&interval=4&usageType=1&inclBtw=true")
        try:
            data = self.fetcher(url)
            slots = []
            for p in data.get("Prices", []):
                start = datetime.fromisoformat(p["readingDate"].replace("Z", "+00:00"))
                slots.append(PriceSlot(start, start + timedelta(hours=1), float(p["price"])))
            if slots:
                self.slots = sorted(slots, key=lambda s: s.start)
            self._last_fetch = time.time()
            log.info("Prijzen opgehaald: %d uurblokken", len(slots))
        except Exception as exc:
            log.warning("Prijzen ophalen mislukt, gebruik vaste waarden: %s", exc)
            self._last_fetch = time.time() - 3000  # over 10 minuten opnieuw proberen

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
