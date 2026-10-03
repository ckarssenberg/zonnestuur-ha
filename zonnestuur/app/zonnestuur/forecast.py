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
        # Voorzichtige voorspelling (P10): voor apparaten die op tijd klaar móeten zijn. Geleerd uit je eigen
        # opwek (zie learn.py); tot die tijd 0,6 × de verwachting.
        self.p10_ratio: Callable[[], float] = lambda: 0.6
        self.nowcast: Optional[tuple[float, datetime]] = None   # (gemeten / voorspeld, wanneer) voor de komende 2 uur
        self.daily: dict = {}        # datum (lokaal, ISO) -> {"kwh", "rain_mm", "sun_h", "code"}

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
               f"&daily=precipitation_sum,sunshine_duration,weather_code&timezone=UTC&forecast_days=4")
        try:
            data = self.fetcher(url)
            hourly = data["hourly"]
            hours = {}
            for t, gti in zip(hourly["time"], hourly["global_tilted_irradiance"]):
                start = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
                # Open-Meteo geeft het gemiddelde over het voorgaande uur
                hours[start - timedelta(hours=1)] = (gti or 0.0) / 1000.0 * s.kwp * 1000.0 * SYSTEM_EFFICIENCY
            self.hours = hours
            daily = {}
            d = data.get("daily") or {}
            for i, day in enumerate(d.get("time") or []):
                def g(k):
                    v = (d.get(k) or [None] * (i + 1))
                    return v[i] if i < len(v) else None
                kwh = sum(w for t, w in hours.items() if t.date().isoformat() == day) / 1000
                daily[day] = {"kwh": round(kwh, 1), "rain_mm": g("precipitation_sum"),
                              "sun_h": None if g("sunshine_duration") is None else round(g("sunshine_duration") / 3600, 1),
                              "code": g("weather_code")}
            self.daily = daily
            self._last_fetch = time.time()
            log.info("Zonvoorspelling opgehaald: %d uren", len(hours))
        except Exception as exc:
            log.warning("Zonvoorspelling ophalen mislukt: %s", exc)
            self._last_fetch = time.time() - 3 * 3600 + 900

    def raw_production_w(self, when: datetime) -> Optional[float]:
        """Voorspelling zoals Open-Meteo hem geeft (nog niet bijgesteld)."""
        hour = when.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        return self.hours.get(hour)

    def production_w(self, when: datetime, quantile: str = "p50") -> Optional[float]:
        """Verwachte opwek, bijgesteld met wat je panelen in de praktijk leveren.

        quantile="p10": voorzichtig (9 van de 10 keer wordt het minstens dit). De komende 2 uur schuift de
        voorspelling mee met wat er het laatste uur echt gemeten is (nowcast)."""
        raw = self.raw_production_w(when)
        if raw is None:
            return None
        v = raw * self.calibration(when)
        if self.nowcast:
            ratio, at = self.nowcast
            dt_h = (when - at).total_seconds() / 3600
            if -1 <= dt_h <= 2:
                w = max(0.0, 1 - max(0.0, dt_h) / 2)
                v *= 1 + (ratio - 1) * w
        if quantile == "p10":
            v *= max(0.2, min(1.0, self.p10_ratio()))
        return v

    def day_kwh(self, day: str) -> Optional[float]:
        """Verwachte opwek van een hele dag (kWh), bijgesteld."""
        hs = [t for t in self.hours if t.date().isoformat() == day]
        if not hs:
            return None
        return sum(self.production_w(t) or 0 for t in hs) / 1000

    def expected_surplus_seconds(self, start: datetime, end: datetime, needed_w: float,
                                 quantile: str = "p50") -> Optional[float]:
        """Hoeveel seconden tussen start en end er naar verwachting genoeg overschot is voor een apparaat.

        Geeft None als er geen voorspelling is.
        """
        if not self.hours:
            return None
        total = 0.0
        t = start
        while t < end:
            nxt = min(end, t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            prod = self.production_w(t, quantile)
            if prod is None:
                return None if total == 0 else total
            surplus = prod - self.base_load(t)
            if surplus >= needed_w:
                total += (nxt - t).total_seconds()
            elif surplus > needed_w * 0.6:
                total += (nxt - t).total_seconds() * 0.5   # wisselvallig: half meetellen
            t = nxt
        return total
