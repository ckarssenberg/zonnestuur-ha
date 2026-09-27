"""Stroomprijzen: vast contract of dynamische uurprijzen (EnergyZero, geen API-sleutel nodig)."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .adapters import http_get_json
from .config import ContractConfig

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

    # ---- publieke API -------------------------------------------------
    def import_price(self, when: datetime) -> float:
        if self.contract.type == "fixed":
            return self.contract.import_price
        slot = self._slot_at(when)
        if slot is None:
            return self.contract.import_price
        return slot.market + self.contract.energy_tax + self.contract.supplier_markup

    def feed_in_price(self, when: datetime) -> float:
        if self.contract.type == "fixed":
            return self.contract.feed_in_price
        slot = self._slot_at(when)
        if slot is None:
            return self.contract.feed_in_price
        return slot.market / 1.21 - self.contract.feed_in_cost

    def value_of_own_kwh(self, when: datetime) -> float:
        """Wat een kWh eigen zonnestroom oplevert: afnameprijs min terugleververgoeding."""
        return self.import_price(when) - self.feed_in_price(when)

    def upcoming(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime, float]]:
        """Uurblokken tussen start en end met hun afnameprijs (alleen bij dynamisch contract)."""
        out = []
        for s in self.slots:
            if s.end > start and s.start < end:
                out.append((max(s.start, start), min(s.end, end), self.import_price(s.start)))
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
