"""Zonvoorspelling via Open-Meteo (gratis, geen API-sleutel).

We vragen de instraling op het vlak van de panelen op en rekenen die om naar
een geschat overschot: opwek min het sluipverbruik van het huis.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .adapters import http_get_json
from .config import SolarConfig

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
SYSTEM_EFFICIENCY = 0.85  # omvormer, kabels, temperatuur, vervuiling


class SolarForecast:
    def __init__(self, solar: SolarConfig, enabled: bool = True,
                 fetcher: Optional[Callable[[str], dict]] = None):
        self.solar = solar
        self.enabled = enabled
        self.fetcher = fetcher or http_get_json
        self.hours: dict[datetime, float] = {}   # uur (UTC) -> geschatte opwek in W
        self._last_fetch = 0.0
        # Wat Zonnestuur over het huis geleerd heeft (zie learn.py); standaard de vaste aannames.
        self.base_load: Callable[[datetime], float] = lambda when: self.solar.base_load_w
        self.calibration: Callable[[datetime], float] = lambda when: 1.0

    def due(self) -> bool:
        return self.enabled and time.time() - self._last_fetch >= 3 * 3600

    def refresh(self, force: bool = False) -> None:
        if not self.enabled:
            return
        if not force and not self.due():
            return
        s = self.solar
        url = (f"{s.forecast_url or OPEN_METEO_URL}?latitude={s.latitude}&longitude={s.longitude}"
               f"&hourly=global_tilted_irradiance&tilt={s.tilt}&azimuth={s.azimuth}"
               f"&timezone=UTC&forecast_days=2")
        try:
            data = self.fetcher(url)
            hourly = data["hourly"]
            hours = {}
            for t, gti in zip(hourly["time"], hourly["global_tilted_irradiance"]):
                start = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
                # Open-Meteo geeft het gemiddelde over het voorgaande uur
                hours[start - timedelta(hours=1)] = (gti or 0.0) / 1000.0 * s.kwp * 1000.0 * SYSTEM_EFFICIENCY
            self.hours = hours
            self._last_fetch = time.time()
            log.info("Zonvoorspelling opgehaald: %d uren", len(hours))
        except Exception as exc:
            log.warning("Zonvoorspelling ophalen mislukt: %s", exc)
            self._last_fetch = time.time() - 3 * 3600 + 900

    def raw_production_w(self, when: datetime) -> Optional[float]:
        """Voorspelling zoals Open-Meteo hem geeft (nog niet bijgesteld)."""
        hour = when.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        return self.hours.get(hour)

    def production_w(self, when: datetime) -> Optional[float]:
        """Verwachte opwek, bijgesteld met wat je panelen in de praktijk leveren."""
        raw = self.raw_production_w(when)
        return None if raw is None else raw * self.calibration(when)

    def expected_surplus_seconds(self, start: datetime, end: datetime, needed_w: float) -> Optional[float]:
        """Hoeveel seconden tussen start en end er naar verwachting genoeg overschot is voor een apparaat.

        Geeft None als er geen voorspelling is.
        """
        if not self.hours:
            return None
        total = 0.0
        t = start
        while t < end:
            nxt = min(end, t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            prod = self.production_w(t)
            if prod is None:
                return None if total == 0 else total
            surplus = prod - self.base_load(t)
            if surplus >= needed_w:
                total += (nxt - t).total_seconds()
            elif surplus > needed_w * 0.6:
                total += (nxt - t).total_seconds() * 0.5   # wisselvallig: half meetellen
            t = nxt
        return total
