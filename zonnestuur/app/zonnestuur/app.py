"""Zonnestuur-dienst: regellus + lokaal dashboard.

Start met:  python -m zonnestuur --config config.json
"""
from __future__ import annotations

import argparse
import json
import os
import logging
import signal
import threading
import time
from datetime import date as date_cls, date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from . import __version__
from .adapters import DeviceError, HomeWizardP1, ShellySwitch, NotReady, http_get_json
from .config import Config, load_config, merge_public, public_dict, save_config, effective_ha
from .discovery import PROBLEMS, Scanner, identify
from .drivers import HomeAssistant, ha_candidates, make_meter, make_switch, ha_client
from .controller import Context, Controller, plan_cheapest_hours, plan_sunny_hours, plan_cheapest_block
from .forecast import SolarForecast
from .ledger import Ledger
from .prices import PriceProvider

log = logging.getLogger("zonnestuur")
WEB_DIR = Path(__file__).parent / "web"
STATIC = {
    "/fonts/inter.woff2": ("fonts/inter.woff2", "font/woff2"),
    "/fonts/inter-tight.woff2": ("fonts/inter-tight.woff2", "font/woff2"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
}
PAGES = {"/": "index.html", "/index.html": "index.html", "/setup": "setup.html", "/instellingen": "setup.html"}


class Engine:
    def __init__(self, cfg: Config, meter=None, switches: Optional[dict] = None,
                 prices: Optional[PriceProvider] = None, forecast: Optional[SolarForecast] = None,
                 ledger: Optional[Ledger] = None, config_path: Optional[str] = None):
        self.cfg = cfg
        self.config_path = config_path
        self.tz = ZoneInfo(cfg.timezone)
        self.meter = meter or self._safe_meter(cfg)
        self.switches = switches or self._make_switches(cfg)
        self.prices = prices or PriceProvider(cfg.contract, enabled=cfg.use_prices)
        self.forecast = forecast or SolarForecast(cfg.solar, enabled=cfg.use_forecast)
        self.ledger = ledger or Ledger(cfg.db_path)
        self.controller = Controller(cfg)
        self.scanner = Scanner(extra_hosts=cfg.scan_extra)
        self.lock = threading.RLock()
        self.grid_w: Optional[float] = None
        self.meter_online = False
        self.meter_fail_since: Optional[float] = None
        self.last_tick_mono: Optional[float] = None
        self.last_error = ""
        self._last_save = 0.0
        self._last_on: dict[str, Optional[bool]] = {}
        self._restore_state()

    # ---- regelronde ------------------------------------------------------
    def tick(self, now: Optional[datetime] = None, mono: Optional[float] = None) -> None:
        now = now or datetime.now(self.tz)
        mono = time.monotonic() if mono is None else mono
        with self.lock:
            dt = 0.0 if self.last_tick_mono is None else mono - self.last_tick_mono
            self.last_tick_mono = mono

            if not self.cfg.configured:
                return                      # nog niets gekoppeld: de koppel-assistent is aan zet

            # 1. meter uitlezen
            reading = None
            try:
                if self.cfg.strategy == "price" and not self.cfg.has_meter:
                    raise _NoMeter()
                reading = self.meter.read()
                self.grid_w = reading.grid_w
                self.meter_online = True
                self.meter_fail_since = None
            except _NoMeter:
                self.grid_w, self.meter_online = None, False
            except DeviceError as exc:
                self.meter_online = False
                self.meter_fail_since = self.meter_fail_since or mono
                self.last_error = f"Meter: {exc}"
                log.warning(self.last_error)
                # korte storing overbruggen met de laatste waarde, daarna veilig terugvallen
                if mono - self.meter_fail_since > 120:
                    self.grid_w = None

            # 2. apparaten uitlezen
            powers: dict[str, float] = {}
            for d in self.cfg.devices:
                if d.id not in self.switches:
                    self.controller.update_measurements(d.id, False, 0.0, None, online=False)
                    continue
                try:
                    s = self.switches[d.id].status()
                    self._last_on[d.id] = s.on
                    self.controller.update_measurements(d.id, s.on, s.power_w, s.energy_wh, True)
                    powers[d.id] = s.power_w
                except NotReady as exc:
                    self.controller.update_measurements(d.id, False, 0.0, None, online=False, offline_reason=str(exc))
                except (DeviceError, KeyError, ValueError) as exc:
                    self.controller.update_measurements(d.id, False, 0.0, None, online=False)
                    self.last_error = f"{d.name}: {exc}"
                    log.warning(self.last_error)

            # 3. prijzen en zonvoorspelling bijwerken, garantie-planning maken
            self.prices.refresh(now)
            self._read_live_price(now, mono)
            self.forecast.refresh()
            cheapest = self._plan_guarantee(now)
            sunny = self._plan_sunny(now)

            # 4. beslissen en schakelen
            price_hours = self._plan_price(now)
            price_now = self.prices.import_price(now) if self.cfg.contract.type == "dynamic" and self.prices.slots else None
            ctx = Context(now=now, mono=mono, grid_w=self.grid_w, dt=dt, cheapest_hours=cheapest, sunny_hours=sunny,
                          price_hours=price_hours, price_now=price_now)
            for dec in self.controller.step(ctx):
                try:
                    sw = self.switches[dec.device_id]
                    if dec.on != self._last_on.get(dec.device_id):
                        sw.set(dec.on)
                        log.info("%s -> %s (%s)", dec.device_id, "AAN" if dec.on else "UIT", dec.reason)
                    if dec.on and dec.power_w is not None and hasattr(sw, "set_power"):
                        sw.set_power(dec.power_w)
                        log.info("%s -> %d W", dec.device_id, dec.power_w)
                    self._last_on[dec.device_id] = dec.on
                except DeviceError as exc:
                    self.last_error = f"schakelen {dec.device_id}: {exc}"
                    log.warning(self.last_error)
                    self.controller.states[dec.device_id].on = not dec.on

            # 5. boekhouding
            if self.cfg.strategy == "price":
                # Besparing zonder panelen: wat je betaalt tegenover de gemiddelde prijs van vandaag
                avg = self._avg_price_today(now)
                saving = (avg - price_now) if (avg is not None and price_now is not None) else 0.0
                self.ledger.record(now, dt, None, powers, saving, all_counts=True)
            else:
                self.ledger.record(now, dt, self.grid_w, powers, self.prices.value_of_own_kwh(now))
            if reading is not None:
                self.ledger.record_meter(now, reading.import_kwh, reading.export_kwh)
            if mono - self._last_save > 60:
                self.ledger.flush()
                self._save_state()
                self._last_save = mono

    def _plan_guarantee(self, now: datetime) -> dict[str, set]:
        """Dynamisch contract: kies goedkope uren om bij te laden als de zon het niet redt."""
        if self.cfg.contract.type != "dynamic":
            return {}
        out: dict[str, set] = {}
        for d in self.cfg.devices:
            if not d.ready_times or d.guarantee_min <= 0:
                continue
            st = self.controller.states[d.id]
            ready = self.controller.next_unsatisfied(d, st, now)
            if ready is None:
                continue
            window_start = ready - timedelta(minutes=d.guarantee_min)
            plan_start = max(now, ready - timedelta(hours=d.full_lookback_h))
            if plan_start >= window_start:
                continue
            need = d.guarantee_min * 60
            expected = self.forecast.expected_surplus_seconds(now, window_start, d.start_threshold_w) or 0.0
            if 0.8 * expected >= need:
                continue
            candidates = self.prices.upcoming(plan_start.astimezone(timezone.utc), window_start.astimezone(timezone.utc))
            hours = plan_cheapest_hours(need - 0.8 * expected, candidates)
            out[d.id] = {h.astimezone(self.tz).replace(minute=0, second=0, microsecond=0) for h in hours}
        return out

    def _best_hours(self, device_id: str, now: datetime) -> list:
        if self.cfg.strategy == "price":
            plan = getattr(self, "_price_plans", {}).get(device_id)
            return sorted(h.strftime("%H:00") for h in plan["hours"]) if plan else []
        return sorted(h.strftime("%H:00") for h in getattr(self, "_sunny", {}).get(device_id, set()))

    def _avg_price_today(self, now: datetime) -> Optional[float]:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        slots = self.prices.upcoming(start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc))
        return sum(p for _, _, p in slots) / len(slots) if slots else None

    def _plan_price(self, now: datetime) -> dict[str, set]:
        """Zonder zonnepanelen: kies per apparaat de goedkoopste uren tot zijn klaar-tijd.

        Een plan blijft staan tot de klaar-tijd voorbij is, zodat het niet steeds opschuift. Alleen als er
        nieuwe prijzen bij komen (rond 13:00 die van morgen) en het plan nog niet begonnen is, plannen we opnieuw.
        """
        if self.cfg.strategy != "price" or self.cfg.contract.type != "dynamic" or not self.prices.slots:
            return {}
        plans = getattr(self, "_price_plans", {})
        hour = now.replace(minute=0, second=0, microsecond=0)
        out: dict[str, set] = {}
        for d in self.cfg.devices:
            ready = self.controller.ready_datetimes(d, now)
            end = ready[0] if ready else (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
            key = (end.isoformat(), len(self.prices.slots))
            old = plans.get(d.id)
            if old and old["end"] == end.isoformat() and (old["key"] == key or any(h <= hour for h in old["hours"])):
                out[d.id] = old["hours"]
                continue
            run_s = max(d.expected_run_min, d.guarantee_min if d.ready_times else 0) * 60 or 3600
            candidates = self.prices.upcoming(hour.astimezone(timezone.utc), end.astimezone(timezone.utc))
            if d.one_shot:
                start = plan_cheapest_block(run_s, candidates)
                hours = {start} if start else set()
            else:
                hours = plan_cheapest_hours(run_s, candidates)
            hours = {h.astimezone(self.tz).replace(minute=0, second=0, microsecond=0) for h in hours}
            plans[d.id] = {"end": end.isoformat(), "key": key, "hours": hours}
            out[d.id] = hours
        self._price_plans = plans
        return out

    def _plan_sunny(self, now: datetime) -> dict[str, set]:
        """Kies per apparaat de zonnigste uren van vandaag (eens per uur opnieuw)."""
        key = now.strftime("%Y-%m-%d %H")
        if getattr(self, "_sunny_key", None) == key:
            return self._sunny
        out: dict[str, set] = {}
        if self.forecast.hours:
            for d in self.cfg.devices:
                if d.expected_run_min <= 0:
                    continue
                end = now.replace(hour=22, minute=0, second=0, microsecond=0)
                ready = self.controller.ready_datetimes(d, now)
                if ready and ready[0].date() == now.date():
                    end = ready[0] - timedelta(minutes=d.guarantee_min)
                hours = []
                t = now.replace(hour=5, minute=0, second=0, microsecond=0)
                while t < end:
                    prod = self.forecast.production_w(t + timedelta(minutes=30))
                    if prod is not None:
                        hours.append((t, prod - self.cfg.solar.base_load_w))
                    t += timedelta(hours=1)
                chosen = plan_sunny_hours(hours, d.expected_run_min)
                out[d.id] = chosen
        self._sunny_key, self._sunny = key, out
        return out

    # ---- instellingen wijzigen vanuit de app ---------------------------------
    def apply_config(self, new: Config) -> None:
        """Nieuwe instellingen opslaan en meteen gebruiken, zonder herstart en zonder data te verliezen."""
        with self.lock:
            old_states = self.controller.states
            self.cfg = new
            self.meter = self._safe_meter(new)
            self.switches = self._make_switches(new)
            self.prices = PriceProvider(new.contract, enabled=new.use_prices)
            self.forecast = SolarForecast(new.solar, enabled=new.use_forecast)
            self.controller = Controller(new)
            for dev_id, st in old_states.items():
                if dev_id in self.controller.states:
                    self.controller.states[dev_id] = st
            self._sunny_key = None
            self.meter_fail_since, self.last_error = None, ""
            self.scanner.extra_hosts = new.scan_extra
            if self.config_path:
                save_config(new, self.config_path)
            self._save_state()
        log.info("Nieuwe instellingen actief: %d apparaat/apparaten", len(new.devices))

    @staticmethod
    def _safe_meter(cfg: Config):
        try:
            return make_meter(cfg)
        except ValueError as exc:
            log.warning("Meter nog niet bruikbaar: %s", exc)
            return HomeWizardP1(cfg.p1_host)

    @staticmethod
    def _make_switches(cfg: Config) -> dict:
        out = {}
        for d in cfg.devices:
            try:
                out[d.id] = make_switch(cfg, d)
            except (ValueError, KeyError) as exc:
                log.warning("%s: koppeling niet bruikbaar: %s", d.name, exc)
        return out

    def _read_live_price(self, now: datetime, mono: float) -> None:
        """Actuele prijs uit een Home Assistant-sensor van je leverancier (bijv. Tibber, Frank, Zonneplan)."""
        ent = self.cfg.contract.price_entity
        if not ent or mono - getattr(self, "_price_read_mono", -1e9) < 60:
            return
        self._price_read_mono = mono
        try:
            st = ha_client(self.cfg).state(ent)
            v = float(st.get("state"))
            unit = str((st.get("attributes") or {}).get("unit_of_measurement", "")).lower()
            if "ct" in unit or "cent" in unit or v > 5:           # soms in centen
                v = v / 100
            self.prices.set_live_price(v, now)
        except (DeviceError, ValueError, TypeError) as exc:
            self.prices.set_live_price(None, now)
            log.warning("Prijssensor %s niet te lezen: %s", ent, exc)

    def test_switch(self, spec: dict, seconds: float = 8.0) -> dict:
        """Zet een apparaat kort aan en meet wat het opneemt. Zet daarna de oude stand terug."""
        from .config import DeviceConfig
        d = DeviceConfig(id="test", name="test", host=str(spec.get("host", "")), driver=str(spec.get("driver", "shelly")),
                         switch_id=int(spec.get("switch_id", 0)), params=dict(spec.get("params") or {}))
        cfg = Config(homeassistant=self.cfg.homeassistant)
        sw = make_switch(cfg, d)
        if d.driver == "ha_start_button":             # nooit een wasprogramma starten om te testen
            try:
                sw.status()
                return {"max_power_w": 0, "metering": False, "restored": True, "no_test": True, "ready": True}
            except NotReady as exc:
                return {"max_power_w": 0, "metering": False, "restored": True, "no_test": True, "ready": False, "note": str(exc)}
        if d.driver == "ha_current":
            try:
                sw.status()
            except NotReady as exc:
                return {"max_power_w": 0, "metering": False, "restored": True, "no_car": True, "note": str(exc)}
        with self.lock:                          # regelaar even pauzeren tijdens de test
            before = sw.status()
            sw.set(True)
            if getattr(sw, "modulating", False):
                sw.set_power(sw.min_w)
            peak, t_end = 0.0, time.monotonic() + seconds
            try:
                while time.monotonic() < t_end:
                    time.sleep(1.0)
                    st = sw.status()
                    peak = max(peak, st.power_w)
                    if peak > 50 and time.monotonic() > t_end - seconds + 3:
                        break
            finally:
                sw.set(before.on)
        has_power = d.driver in ("shelly", "shelly_gen1", "homewizard_socket", "tasmota") or bool((d.params or {}).get("power_entity"))
        return {"max_power_w": round(peak), "metering": has_power, "restored": before.on}

    def ha_connect(self, url: str, token: str) -> dict:
        ha = HomeAssistant(url, token)
        try:
            ha.ping()
            states = ha.states()
        except DeviceError as exc:
            msg = str(exc)
            if "401" in msg:
                return {"ok": False, "error": "Home Assistant weigert het token. Maak een nieuw langlevend toegangstoken aan (profiel > Beveiliging)."}
            return {"ok": False, "error": "Home Assistant is niet bereikbaar op dit adres. Klopt het, inclusief poort (meestal :8123)?"}
        return {"ok": True, "entities": len(states), **ha_candidates(states)}

    def ha_candidates(self) -> dict:
        ha = effective_ha(self.cfg)
        if not ha.get("url"):
            return {"ok": False, "error": "Home Assistant is nog niet gekoppeld"}
        return self.ha_connect(ha["url"], ha.get("token", ""))

    def read_meter(self, spec: dict) -> dict:
        """Meter proberen uit te lezen voordat hij wordt opgeslagen (koppel-assistent)."""
        cfg = Config(p1_host=str(spec.get("host", "")), meter=dict(spec.get("meter") or {}), homeassistant=self.cfg.homeassistant)
        try:
            r = make_meter(cfg).read()
            return {"ok": True, "grid_w": round(r.grid_w)}
        except (DeviceError, ValueError) as exc:
            if cfg.meter_driver == "homewizard":
                return self.read_p1(cfg.p1_host)
            return {"ok": False, "problem": "meter", "problem_text": f"Deze meter geeft geen waarde: {exc}"}

    def read_p1(self, host: str) -> dict:
        try:
            r = HomeWizardP1(host).read()
            return {"ok": True, "grid_w": round(r.grid_w)}
        except DeviceError:
            found = identify(host)
            problem = found.problem if found else "not_found"
            return {"ok": False, "problem": problem,
                    "problem_text": PROBLEMS.get(problem, "Geen P1-meter gevonden op dit adres.")}

    # ---- toestand bewaren --------------------------------------------------
    def _save_state(self) -> None:
        data = {}
        for dev_id, st in self.controller.states.items():
            data[dev_id] = {"day": st.day.isoformat() if st.day else None, "run": st.run_seconds_today,
                            "full_at": st.full_at.isoformat() if st.full_at else None, "mode": st.mode,
                            "until": st.override_until.isoformat() if st.override_until else None}
        self.ledger.save_state("controller", data)

    def _restore_state(self) -> None:
        data = self.ledger.load_state("controller") or {}
        today = datetime.now(self.tz).date()
        for dev_id, v in data.items():
            st = self.controller.states.get(dev_id)
            if not st:
                continue
            st.mode = v.get("mode", "auto")
            st.override_until = datetime.fromisoformat(v["until"]) if v.get("until") else None
            st.full_at = datetime.fromisoformat(v["full_at"]) if v.get("full_at") else None
            if v.get("day") == today.isoformat():
                st.day = today
                st.run_seconds_today = float(v.get("run", 0))

    # ---- voor het dashboard ------------------------------------------------
    def status(self) -> dict:
        now = datetime.now(self.tz)
        with self.lock:
            today = now.date()
            month_start = today.replace(day=1)
            year_start = today.replace(month=1, day=1)
            per_dev = self.ledger.per_device(month_start)
            devices = []
            for d in self.cfg.devices:
                st = self.controller.states[d.id]
                ready = self.controller.ready_datetimes(d, now)
                devices.append({"id": d.id, "name": d.name, "kind": d.kind, "power_nominal_w": d.power_w,
                                "ready_times": d.ready_times, "guarantee_min": d.guarantee_min, "driver": d.driver,
                                "modulating": d.modulating, "min_w": round(d.min_w), "max_w": round(d.max_w),
                                "w_per_step": round(d.w_per_step),
                                "next_ready": ready[0].isoformat(timespec="minutes") if ready else None,
                                "next_ready_ok": (self.controller.is_satisfied(d, self.controller.states[d.id], ready[0])
                                                  if ready else None),
                                "best_hours": self._best_hours(d.id, now),
                                **st.to_dict(), "month": per_dev.get(d.id, {"kwh": 0, "kwh_solar": 0, "eur_saved": 0})})
            return {
                "version": __version__,
                "configured": self.cfg.configured,
                "now": now.isoformat(timespec="seconds"),
                "grid_w": None if self.grid_w is None else round(self.grid_w),
                "meter_online": self.meter_online,
                "contract": self.cfg.contract.type,
                "strategy": self.cfg.strategy,
                "supplier": _supplier_name(self.cfg.contract),
                "price_now": round(self.prices.import_price(now), 4),
                "value_own_kwh": round(self.prices.value_of_own_kwh(now), 4),
                "forecast_now_w": self._rounded(self.forecast.production_w(now)),
                "devices": devices,
                "today": self.ledger.totals(today),
                "month": self.ledger.totals(month_start),
                "year": self.ledger.totals(year_start),
                "last_error": self.last_error,
            }

    def today(self) -> dict:
        """Verloop van vandaag per 5 minuten, plus de zonvoorspelling per uur."""
        now = datetime.now(self.tz)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        with self.lock:
            samples = self.ledger.samples(int(start.timestamp()))
            forecast = []
            for h in range(24):
                t = start + timedelta(hours=h)
                pv = self.forecast.production_w(t)
                if pv is not None:
                    forecast.append({"ts": int(t.timestamp()), "pv_w": round(pv)})
        return {"start": int(start.timestamp()), "samples": samples, "forecast": forecast}

    @staticmethod
    def _rounded(v):
        return None if v is None else round(v)

    def set_mode(self, device_id: str, mode: str, hours: Optional[float]) -> None:
        with self.lock:
            self.controller.set_mode(device_id, mode, datetime.now(self.tz), hours)
            self._save_state()


# ---- webserver --------------------------------------------------------------
def make_handler(engine: Engine):

    INGRESS_ONLY = os.environ.get("ZONNESTUUR_INGRESS_ONLY") == "1"
    INGRESS_PEERS = ("172.30.32.2", "127.0.0.1")

    class Handler(BaseHTTPRequestHandler):
        server_version = "Zonnestuur"

        def log_message(self, fmt, *args):  # stil, we loggen zelf
            log.debug("web: " + fmt, *args)

        def _authorized(self, query: dict) -> bool:
            token = engine.cfg.web_token
            if not token:
                return True
            return self.headers.get("X-Token") == token or query.get("token", [""])[0] == token

        def _blocked(self) -> bool:
            """Als add-on: alleen via Home Assistant (ingress) openen, tenzij er een wachtwoord is ingesteld."""
            if not INGRESS_ONLY or engine.cfg.web_token or self.client_address[0] in INGRESS_PEERS:
                return False
            self._send(403, "Open Zonnestuur via Home Assistant (zijbalk), of stel eerst een wachtwoord in.".encode(),
                       "text/plain; charset=utf-8")
            return True

        def _send(self, code: int, body: bytes, ctype: str, cache: bool = False) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

        def _body(self) -> dict:
            length = min(int(self.headers.get("Content-Length") or 0), 256_000)
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("Verwacht een JSON-object")
            return data

        def do_GET(self):
            if self._blocked():
                return
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if url.path in PAGES:
                page = PAGES[url.path]
                if page == "index.html" and not engine.cfg.configured:
                    self.send_response(302)
                    self.send_header("Location", "setup")
                    self.end_headers()
                    return
                return self._send(200, (WEB_DIR / page).read_bytes(), "text/html; charset=utf-8")
            if url.path in STATIC:
                f, ctype = STATIC[url.path]
                return self._send(200, (WEB_DIR / f).read_bytes(), ctype, cache=True)
            if not self._authorized(q):
                return self._json(401, {"error": "token vereist"})
            if url.path == "/api/status":
                return self._json(200, engine.status())
            if url.path == "/api/today":
                return self._json(200, engine.today())
            if url.path == "/api/history":
                days = min(400, max(1, int(q.get("days", ["31"])[0])))
                since = datetime.now(engine.tz).date() - timedelta(days=days - 1)
                return self._json(200, engine.ledger.days(since))
            if url.path == "/api/config":
                return self._json(200, public_dict(engine.cfg))
            if url.path == "/api/setup/scan":
                return self._json(200, engine.scanner.snapshot())
            if url.path == "/api/setup/p1":
                host = q.get("host", [""])[0].strip()
                if not host:
                    return self._json(400, {"error": "Geef het adres van de P1-meter"})
                return self._json(200, engine.read_p1(host))
            if url.path == "/api/setup/ha":
                return self._json(200, engine.ha_candidates())
            if url.path == "/api/history/prices":
                return self._json(200, _price_history(q.get("start", [""])[0], q.get("end", [""])[0]))
            if url.path == "/api/history/solar":
                return self._json(200, _solar_history(engine.cfg, q.get("start", [""])[0], q.get("end", [""])[0]))
            if url.path == "/api/suppliers":
                from .suppliers import catalog
                return self._json(200, catalog())
            return self._json(404, {"error": "niet gevonden"})

        def do_PUT(self):
            return self.do_POST()

        def do_POST(self):
            if self._blocked():
                return
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if not self._authorized(q):
                return self._json(401, {"error": "token vereist"})
            try:
                body = self._body()
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json(400, {"error": f"Ongeldige invoer: {exc}"})
            parts = url.path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "device"] and parts[3] == "mode":
                try:
                    engine.set_mode(parts[2], body.get("mode", "auto"), body.get("hours"))
                except KeyError:
                    return self._json(404, {"error": "onbekend apparaat"})
                except ValueError as exc:
                    return self._json(400, {"error": str(exc)})
                return self._json(200, {"ok": True})
            if url.path == "/api/config":
                try:
                    new = merge_public(engine.cfg, body)
                except ValueError as exc:
                    return self._json(400, {"error": str(exc)})
                engine.apply_config(new)
                return self._json(200, public_dict(engine.cfg))
            if url.path == "/api/setup/scan":
                engine.scanner.start()
                return self._json(200, engine.scanner.snapshot())
            if url.path == "/api/setup/identify":
                host = str(body.get("host", "")).strip()
                if not host:
                    return self._json(400, {"error": "Geef een IP-adres"})
                found = identify(host)
                if not found:
                    return self._json(200, {"found": None})
                return self._json(200, {"found": found.to_dict() | {"problem_text": PROBLEMS.get(found.problem, "")}})
            if url.path == "/api/setup/test-switch":
                try:
                    return self._json(200, engine.test_switch(body))
                except (DeviceError, ValueError, KeyError) as exc:
                    return self._json(502, {"error": f"Het apparaat reageert niet: {exc}"})
            if url.path == "/api/setup/meter":
                return self._json(200, engine.read_meter(body))
            if url.path == "/api/setup/ha":
                url_ = str(body.get("url", "")).strip()
                token_ = str(body.get("token", "")).strip() or (engine.cfg.homeassistant or {}).get("token", "")
                if not url_ or not token_:
                    return self._json(400, {"error": "Vul het adres van Home Assistant en een toegangstoken in"})
                if not url_.startswith("http"):
                    url_ = "http://" + url_
                res = engine.ha_connect(url_, token_)
                if res.get("ok"):
                    engine.apply_config(merge_public(engine.cfg, {"homeassistant": {"url": url_, "token": token_}}))
                return self._json(200, res)
            return self._json(404, {"error": "niet gevonden"})

    return Handler


def _check_range(start: str, end: str) -> tuple[date_cls, date_cls]:
    d0, d1 = date_cls.fromisoformat(start), date_cls.fromisoformat(end)
    if not (d0 < d1 and (d1 - d0).days <= 400):
        raise ValueError("periode moet 1 tot 400 dagen zijn")
    return d0, d1


def _price_history(start: str, end: str) -> dict:
    """Uurprijzen (kale marktprijs incl. btw, EnergyZero) voor een periode: voor de jaarberekening in de app."""
    from .prices import ENERGYZERO_URL, _iso
    try:
        d0, d1 = _check_range(start, end)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    out: dict[str, float] = {}
    cur = d0
    while cur < d1:
        nxt = min(d1, cur + timedelta(days=31))
        a = datetime(cur.year, cur.month, cur.day, tzinfo=timezone.utc) - timedelta(hours=2)
        b = datetime(nxt.year, nxt.month, nxt.day, tzinfo=timezone.utc)
        url = f"{ENERGYZERO_URL}?fromDate={_iso(a)}&tillDate={_iso(b)}&interval=4&usageType=1&inclBtw=true"
        try:
            for p in http_get_json(url, timeout=20).get("Prices", []):
                out[p["readingDate"]] = round(float(p["price"]), 5)
        except DeviceError as exc:
            return {"ok": False, "error": str(exc)}
        cur = nxt
    keys = sorted(out)
    return {"ok": True, "source": "EnergyZero", "hours": [[k, out[k]] for k in keys]}


def _solar_history(cfg: Config, start: str, end: str) -> dict:
    """Zoninstraling per uur op het paneel (W/m², Open-Meteo archief) voor de ingestelde plek en stand."""
    try:
        d0, d1 = _check_range(start, end)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    s = cfg.solar
    url = ("https://archive-api.open-meteo.com/v1/archive"
           f"?latitude={s.latitude:.2f}&longitude={s.longitude:.2f}&start_date={d0}&end_date={d1 - timedelta(days=1)}"
           f"&hourly=global_tilted_irradiance,temperature_2m&tilt={s.tilt:.0f}&azimuth={s.azimuth:.0f}&timezone=UTC")
    try:
        data = http_get_json(url, timeout=30)
    except DeviceError as exc:
        return {"ok": False, "error": str(exc)}
    h = data.get("hourly", {})
    return {"ok": True, "source": "Open-Meteo archief", "time": h.get("time", []),
            "gti": h.get("global_tilted_irradiance", []), "temp": h.get("temperature_2m", [])}


class _NoMeter(Exception):
    """Geen meter nodig: zonder zonnepanelen sturen we alleen op de prijs."""


def _supplier_name(c) -> str:
    from .suppliers import DYNAMIC, FIXED
    table = DYNAMIC if c.type == "dynamic" else FIXED
    return table.get(c.supplier, {}).get("name", "") if c.supplier else ""


def run(cfg: Config, config_path: Optional[str] = None) -> None:
    engine = Engine(cfg, config_path=config_path)
    try:
        server = ThreadingHTTPServer((cfg.web_host, cfg.web_port), make_handler(engine))
    except OSError as exc:
        log.error("Poort %d is al in gebruik door een ander programma (%s). Kies een andere web_port.", cfg.web_port, exc)
        raise SystemExit(1)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("Dashboard op http://%s:%d", cfg.web_host, cfg.web_port)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        started = time.monotonic()
        try:
            engine.tick()
        except Exception:  # de regellus mag nooit stoppen
            log.exception("Onverwachte fout in regelronde")
        stop.wait(max(0.5, engine.cfg.interval_s - (time.monotonic() - started)))
    engine.ledger.flush()
    engine._save_state()
    server.shutdown()
    log.info("Gestopt")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Zonnestuur: apparaten laten draaien op eigen zonnestroom")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    if not cfg.configured:
        log.info("Nog niet ingesteld: open de app om apparaten te koppelen")
    run(cfg, args.config)
