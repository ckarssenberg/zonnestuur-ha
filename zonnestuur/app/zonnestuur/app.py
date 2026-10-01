"""Zonnestuur-dienst: regellus + lokaal dashboard.

Start met:  python -m zonnestuur --config config.json
"""
from __future__ import annotations

import argparse
import json
import os
import logging
import re
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
from . import license as lic
from . import netsetup
from . import failsafe
from .events import EventTracker, Health, price_rank, sun_hours_ahead
from .guard import InverterLimiter, Notifier, negative_window
from .learn import HouseModel, learn as learn_house
from .battery import BatteryConfig, BatteryRuntime, HourIn, hourly_profile, import_profile, plan as plan_battery, plan_value
from .drivers import HomeAssistant, ha_candidates, make_meter, make_switch, ha_client
from .controller import Context, Controller, plan_cheapest_hours, plan_sunny_hours, plan_cheapest_block, plan_day, Need
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
PAGES = {"/": "index.html", "/index.html": "index.html", "/setup": "setup.html", "/instellingen": "setup.html",
         "/rapport": "rapport.html", "/kiosk": "kiosk.html"}


class Engine:
    def __init__(self, cfg: Config, meter=None, switches: Optional[dict] = None,
                 prices: Optional[PriceProvider] = None, forecast: Optional[SolarForecast] = None,
                 ledger: Optional[Ledger] = None, config_path: Optional[str] = None):
        self.cfg = cfg
        self.config_path = config_path
        self.tz = ZoneInfo(cfg.timezone)
        self.meter = meter or self._safe_meter(cfg)
        self.switches = switches or self._make_switches(cfg)
        # warmwater-vangnet op Shelly's (niet bij nagebootste apparaten in tests)
        self.failsafe = failsafe.Guard() if switches is None and os.environ.get("ZONNESTUUR_NO_FAILSAFE") != "1" else None
        if self.failsafe:
            self.failsafe.sync(cfg.devices)
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
        self.limiter = InverterLimiter.from_config(cfg.inverter)
        self.notifier = Notifier.from_config(cfg.notify)
        self.events = EventTracker(self.ledger)
        self.health = Health()
        self.batteries = {b["id"]: BatteryRuntime(BatteryConfig.from_dict(b)) for b in cfg.batteries}
        self._lic_cache: Optional[tuple] = None
        self.model = HouseModel(fallback_w=cfg.solar.base_load_w)
        self._model_key = ""
        self.pv_w: Optional[float] = None
        self._pv_read = -1e9
        self._wire_model()
        self._start_ocpp()
        self._restore_state()

    # Laatste fout, met tijdstip: het dashboard toont alleen wat nu nog speelt.
    @property
    def last_error(self) -> str:
        return getattr(self, "_last_error", "")

    @last_error.setter
    def last_error(self, v: str) -> None:
        self._last_error = v
        self._last_error_at = time.monotonic() if v else None

    def problems(self) -> list[dict]:
        """Wat de gebruiker nu moet weten, in gewone taal (zonder URL's of foutcodes)."""
        out = []
        if self.cfg.has_meter and not self.meter_online:
            out.append({"level": "warn", "text": "Meter even niet bereikbaar: Zonnestuur stuurt nu op tijd en prijs"})
        for d in self.cfg.devices:
            st = self.controller.states.get(d.id)
            if st and not st.online and not st.offline_reason:
                out.append({"level": "warn", "text": f"{d.name} reageert niet"})
        for b in self.batteries.values():
            if not b.online:
                out.append({"level": "warn", "text": f"{b.cfg.name}: {b.error or 'niet bereikbaar'}"})
        at = getattr(self, "_last_error_at", None)
        if not out and self.last_error and at is not None and time.monotonic() - at < 180:
            msg = re.sub(r"https?://\S+", "", self.last_error).replace("  ", " ").strip(" :")
            for k, v in (("timed out", "reageerde even niet"), ("Connection refused", "niet bereikbaar"),
                         ("Name or service not known", "niet gevonden op het netwerk"), (": :", ":")):
                msg = msg.replace(k, v)
            out.append({"level": "info", "text": msg})
        return out

    # ---- andere systemen ---------------------------------------------------
    def _start_ocpp(self) -> None:
        o = self.cfg.ocpp or {}
        if o.get("enabled") or any(d.driver == "ocpp" for d in self.cfg.devices):
            try:
                from .ocpp import central_system
                central_system(int(o.get("port") or 8887))
            except OSError as exc:
                self.last_error = f"OCPP-server: poort {o.get('port') or 8887} niet beschikbaar ({exc})"
                log.warning(self.last_error)

    def ocpp_view(self) -> dict:
        from .ocpp import _SYSTEMS
        port = int((self.cfg.ocpp or {}).get("port") or 8887)
        cs = _SYSTEMS.get(port)
        return {"enabled": cs is not None, "port": port, "points": cs.overview() if cs else []}

    def mqtt_test(self, conf: dict) -> dict:
        """Verbinden met de broker en kijken welke apparaten er zijn (Zigbee2MQTT, Tasmota, Shelly)."""
        from .mqtt import MQTTClient
        from .config import effective_mqtt
        if not conf.get("host"):
            conf = effective_mqtt(self.cfg)                # add-on: de Mosquitto-broker van Home Assistant
        if not conf.get("host"):
            return {"ok": False, "error": "Vul het adres van je MQTT-broker in (bijv. 192.168.1.10 of core-mosquitto)"}
        c = MQTTClient(conf["host"], int(conf.get("port") or 1883), conf.get("username", ""), conf.get("password", ""),
                       client_id=f"zonnestuur-test-{int(time.time()) % 10000}")
        try:
            if not c.connected.wait(5):
                return {"ok": False, "error": f"Geen verbinding met de broker: {c.error or 'geen antwoord'}"}
            for t in ("zigbee2mqtt/bridge/devices", "tele/+/LWT", "tele/+/SENSOR", "+/online", "dsmr/reading/#", "homeassistant/sensor/+/+/config"):
                c.subscribe(t)
            time.sleep(2.5)
            z2m = []
            raw = c.get("zigbee2mqtt/bridge/devices")
            if raw:
                try:
                    for d in json.loads(raw):
                        exposes = json.dumps((d.get("definition") or {}).get("exposes") or [])
                        if d.get("type") != "Coordinator" and '"state"' in exposes:
                            z2m.append({"name": d.get("friendly_name"), "power": '"power"' in exposes,
                                        "model": ((d.get("definition") or {}).get("model") or "")})
                except ValueError:
                    pass
            tas = sorted({t.split("/")[1] for t in c.topics("tele/+/LWT")})
            shelly = sorted({t.split("/")[0] for t, v in c.topics("+/online").items() if t.split("/")[0].startswith("shelly")})
            dsmr = bool(c.topics("dsmr/reading/#"))
            return {"ok": True, "zigbee2mqtt": z2m, "tasmota": tas, "shelly": shelly, "dsmr_reader": dsmr,
                    "power_topics": sorted(t for t in c.topics("tele/+/SENSOR"))[:30]}
        finally:
            c.close()

    def homey_test(self, url: str, token: str) -> dict:
        from .drivers_extra import Homey, homey_candidates
        if not url or not token:
            return {"ok": False, "error": "Vul het adres van je Homey Pro en een API-sleutel in"}
        try:
            devs = Homey(url, token).devices()
        except DeviceError as exc:
            msg = str(exc)
            return {"ok": False, "error": "Homey weigert de API-sleutel" if "401" in msg or "403" in msg else f"Homey niet bereikbaar: {msg}"}
        return {"ok": True, "count": len(devs), **homey_candidates(devs)}

    # ---- leren -------------------------------------------------------------
    def _wire_model(self) -> None:
        """De planning rekent met het geleerde huis in plaats van met vaste aannames."""
        self.forecast.base_load = lambda when: self.model.base_w(when.astimezone(self.tz))
        self.forecast.calibration = lambda when: self.model.pv_calibration(when.astimezone(self.tz))

    def relearn(self, now: datetime, force: bool = False) -> None:
        key = f"{now:%Y%m%d%H}"
        if key == self._model_key and not force:
            return
        self._model_key = key
        from .learn import LEARN_DAYS, RUN_DAYS
        rows = self.ledger.house_hours(int((now - timedelta(days=LEARN_DAYS)).timestamp()), int(now.timestamp()) + 3600)
        power = {d.id: (d.max_w if d.modulating else d.power_w) for d in self.cfg.devices}
        self.model = learn_house(rows, self.tz, self.cfg.solar.has_panels, self.cfg.solar.base_load_w,
                                 self.ledger.device_daily((now - timedelta(days=RUN_DAYS)).date()), power)

    def run_min(self, d) -> float:
        """Hoe lang dit apparaat per dag nodig heeft: geleerd als dat kan, anders de instelling."""
        learned = self.model.run_min(d.id) if d.learn_run else None
        if learned:
            return max(15.0, min(12 * 60.0, learned))
        return float(d.expected_run_min)

    def _read_pv(self, mono: float) -> tuple[Optional[float], int]:
        """Zonne-opwek nu: gemeten (sensor) of geschat uit de bijgestelde voorspelling."""
        ent = self.cfg.solar.pv_entity
        if ent and mono - self._pv_read >= 30:
            self._pv_read = mono
            try:
                st = ha_client(self.cfg).state(ent)
                v = float(st.get("state"))
                if str((st.get("attributes") or {}).get("unit_of_measurement", "W")).lower() == "kw":
                    v *= 1000
                self.pv_w = max(0.0, v)
            except (DeviceError, ValueError, TypeError, OSError):
                self.pv_w = None
        if ent and self.pv_w is not None:
            return self.pv_w, 2
        if self.cfg.solar.has_panels:
            est = self.forecast.production_w(datetime.now(self.tz))
            return est, (1 if est is not None else 0)
        return 0.0, 2

    # ---- licentie ----------------------------------------------------------
    def license_state(self, force: bool = False) -> "lic.LicenseState":
        today = datetime.now(self.tz).date()
        if force or not self._lic_cache or self._lic_cache[0] != today:
            stored = self.ledger.load_state("license")
            state, st = lic.evaluate(stored, today)
            if st != stored:
                self.ledger.save_state("license", st)
            self._lic_cache = (today, state)
        return self._lic_cache[1]

    def set_license(self, key: str) -> dict:
        key = (key or "").strip()
        if key and lic.verify(key) is None:
            return {"ok": False, "error": "Deze sleutel klopt niet. Kopieer hem in zijn geheel, beginnend met ZS1."}
        st = dict(self.ledger.load_state("license") or {})
        if key:
            st["key"] = key
        else:
            st.pop("key", None)
        self.ledger.save_state("license", st)
        state = self.license_state(force=True)
        if state.plan != "pro" and key:
            return {"ok": False, "error": state.error or "Deze sleutel is niet (meer) geldig", **state.to_dict()}
        return {"ok": True, **state.to_dict()}

    def _basic_device(self) -> Optional[str]:
        """Zonnestuur Basis stuurt één apparaat automatisch: dat met de hoogste prioriteit."""
        return min(self.cfg.devices, key=lambda d: d.priority).id if self.cfg.devices else None

    def _device(self, device_id: str):
        return next((d for d in self.cfg.devices if d.id == device_id), None)

    # ---- betrouwbaarheid en logboek -----------------------------------------
    def _confirm_switch(self, now: datetime, mono: float, d, actual_on: bool) -> None:
        res = self.health.check(now, mono, d, actual_on)
        day = now.date().isoformat()
        if res == "retry":
            want = not actual_on
            try:
                self.switches[d.id].set(want)
                log.info("%s: stand klopte niet, opnieuw %s", d.name, "AAN" if want else "UIT")
            except DeviceError as exc:
                log.warning("%s: opnieuw schakelen mislukt: %s", d.name, exc)
        elif res == "failed":
            self.ledger.inc(day, "switch_failed")
            self.events.note(now, d.id, d.name, "MISLUKT",
                             f"Schakelen lukte niet: {d.name} bleef {'aan' if actual_on else 'uit'}, ook na een tweede poging. "
                             "Staat hij aan en in het netwerk?")
            if self.notifier:
                self.notifier.queue_alert(f"fail:{d.id}", f"{d.name} schakelt niet",
                                          f"Zonnestuur probeerde {d.name} {'uit' if actual_on else 'aan'} te zetten, maar de stand veranderde niet. "
                                          "Kijk of het apparaat aan staat en verbinding heeft.")
        if res is None and d.id not in self.health.pending and self._confirmed_count.get(day, 0) != self.health.ok:
            self._confirmed_count = {day: self.health.ok}
            self.ledger.set_stat(day, "switch_ok", self.health.ok)

    _confirmed_count: dict = {}

    def _event_inputs(self, now: datetime, d, ctx) -> dict:
        price = self.prices.import_price(now)
        rank = None
        if self.cfg.contract.type == "dynamic" and self.prices.slots:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            day = [p for _, _, p in self.prices.upcoming(start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc))]
            rank = price_rank(price, day)
        st = self.controller.states.get(d.id)
        inp = {"surplus_w": None if ctx.grid_w is None else -ctx.grid_w, "price_now": price, "price_rank": rank,
               "until": st.override_until.strftime("%H:%M") if st and st.override_until else None,
               "not_before": (d.params or {}).get("not_before") or None}
        if self.cfg.solar.has_panels and self.forecast.hours:
            try:
                inp["sun_hours"] = sun_hours_ahead(now, self.forecast.production_w, lambda t: self.model.base_w(t.astimezone(self.tz)),
                                                   d.start_threshold_w)
            except Exception:
                pass
        return inp

    def events_view(self, days: int = 2) -> dict:
        now = datetime.now(self.tz)
        since = int((now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)).timestamp())
        with self.lock:
            return {"events": self.events.view(since, 150), "health": self.health.view(now, time.monotonic())}

    # ---- doel, weekrapport, moment, contractcheck, uitleg --------------------
    def goal_view(self, now: Optional[datetime] = None) -> dict:
        from .report import goal
        now = now or datetime.now(self.tz)
        m0 = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        rows = self.ledger.house_hours(int(m0.timestamp()), int(now.timestamp()) + 3600)
        tot = self.ledger.totals(m0.date())
        bat = sum(v["eur"] for v in self.ledger.battery_totals(m0.date()).values())
        return goal(self.cfg.goal, rows, tot["kwh_solar"], tot["eur_saved"] + bat, self.cfg.solar.has_panels, now.date())

    def report_view(self, offset: int = 0) -> dict:
        from .report import self_use, week_report
        now = datetime.now(self.tz)
        monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(weeks=offset)
        end = monday + timedelta(days=7)
        prev = monday - timedelta(days=7)

        def eur_between(a: datetime, b: datetime) -> tuple[float, dict]:
            per: dict[str, dict] = {}
            total = 0.0
            for r in self.ledger.device_days(a.date()):
                if r["day"] >= b.date().isoformat():
                    continue
                p = per.setdefault(r["device"], {"eur": 0.0, "kwh": 0.0, "kwh_solar": 0.0})
                p["eur"] += r["eur"]
                p["kwh"] += r["kwh"]
                p["kwh_solar"] += r["kwh_solar"]
                total += r["eur"]
            with self.ledger.lock:
                b_eur = self.ledger.conn.execute("SELECT COALESCE(SUM(eur),0) FROM battery_day WHERE day >= ? AND day < ?",
                                                 (a.date().isoformat(), b.date().isoformat())).fetchone()[0]
            return total + b_eur, per

        e_week, per = eur_between(monday, end)
        e_prev, _ = eur_between(prev, monday)
        rows = self.ledger.house_hours(int(monday.timestamp()), int(end.timestamp()))
        stats = self.ledger.stats(monday.date().isoformat())
        in_week = {d: v for d, v in stats.items() if d < end.date().isoformat()}
        overrides = int(sum(v.get("override", 0) for v in in_week.values()))
        failed = int(sum(v.get("switch_failed", 0) for v in in_week.values()))
        meetdagen = sum(1 for v in in_week.values() for k in v if k.startswith("meetdag:"))
        outlook = ""
        if offset == 0:
            try:
                wins = self.coach_view().get("windows") or []
                tw = [w for w in wins if w.get("tomorrow")]
                if self.cfg.solar.has_panels and tw:
                    outlook = f"Morgen zon over tussen {tw[0]['from']} en {tw[0]['to']}, ± {round(tw[0]['kwh'])} kWh."
                elif self.cfg.solar.has_panels and self.forecast.hours:
                    outlook = "Morgen weinig zon verwacht: Zonnestuur vult aan in de goedkoopste uren."
            except Exception:
                pass
        tips = []
        try:
            tips = self.coach_view().get("tips") or []
        except Exception:
            pass
        r = week_report(week_start=monday.date(), eur_week=e_week, eur_prev=e_prev, su_week=self_use(rows),
                        goal_v=self.goal_view(now), per_device=per, names={d.id: d.name for d in self.cfg.devices},
                        solar_to_devices=sum(p["kwh_solar"] for p in per.values()), outlook=outlook,
                        action=tips[0] if tips else None, overrides=overrides, failed=failed, meetdagen=meetdagen,
                        motivation=self.cfg.motivation)
        r["days"] = []
        for i in range(7):
            d = (monday + timedelta(days=i)).date()
            if d > now.date():
                break
            dr = [x for x in rows if datetime.fromtimestamp(x["ts"], self.tz).date() == d]
            su = self_use(dr)
            r["days"].append({"day": d.isoformat(), "self_use_pct": None if su["pct"] is None else round(su["pct"]),
                              "pv_kwh": round(su["pv"], 1), "eur": round(sum(x["eur"] for x in self.ledger.device_days(d) if x["day"] == d.isoformat()), 2)})
        r["offset"] = offset
        return r

    def moment_view(self) -> dict:
        from .report import moment
        now = datetime.now(self.tz)
        with self.lock:
            nxt = None
            if self.cfg.solar.has_panels and self.forecast.hours:
                try:
                    wins = self.coach_view().get("windows") or []
                    w = next((w for w in wins if not w.get("tomorrow")), None)
                    if w and w["from"] > now.strftime("%H:%M"):
                        nxt = w["from"]
                except Exception:
                    pass
            surplus_next = None
            if self.cfg.solar.has_panels:
                p = self.forecast.production_w(now + timedelta(hours=1))
                if p is not None:
                    surplus_next = p - self.model.base_w(now + timedelta(hours=1))
            price = self.prices.import_price(now)
            rank = None
            if self.cfg.contract.type == "dynamic" and self.prices.slots:
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                day = [p for _, _, p in self.prices.upcoming(start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc))]
                rank = price_rank(price, day)
                if nxt is None:
                    future = [(s, p) for s, _, p in self.prices.upcoming(now.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc))]
                    if future:
                        cheapest = min(future, key=lambda x: x[1])
                        if cheapest[1] < price - 0.03:
                            nxt = cheapest[0].astimezone(self.tz).strftime("%H:%M")
            m = moment(now, self.grid_w, surplus_next, price if self.cfg.contract.type == "dynamic" else None, rank, nxt,
                       self.cfg.solar.has_panels)
            m.update(at=now.isoformat(timespec="minutes"), price_now=round(price, 4), grid_w=None if self.grid_w is None else round(self.grid_w))
            return m

    def compare_view(self) -> dict:
        from .report import compare
        from .suppliers import DYNAMIC, FIXED, CHECKED_ON, energy_tax_for
        now = datetime.now(self.tz)
        cache = self.ledger.load_state("compare")
        if cache and cache.get("day") == now.date().isoformat():
            return cache["data"]
        rows = self.ledger.house_hours(int((now - timedelta(days=365)).timestamp()), int(now.timestamp()))
        rows = [r for r in rows if r["import_kwh"] or r["export_kwh"]]
        if len(rows) < 24 * 7:
            return {"ok": False, "error": "Na een week meten kan Zonnestuur uitrekenen welk contract het goedkoopst is voor jouw huis."}
        market: dict[int, float] = {}
        try:
            hist = _price_history(datetime.fromtimestamp(rows[0]["ts"], self.tz).date().isoformat(), now.date().isoformat())
            for k, v in hist.get("hours", []):
                market[int(datetime.fromisoformat(k.replace("Z", "+00:00")).timestamp())] = v
        except Exception as exc:
            log.warning("prijshistorie voor vergelijking niet op te halen: %s", exc)
        c = self.cfg.contract
        own_imp = c.import_price if c.type != "dynamic" else (sum(r["cost"] for r in rows) / max(0.1, sum(r["import_kwh"] for r in rows)))
        data = compare(rows, market, lambda ts: energy_tax_for(2027), DYNAMIC, FIXED, own_imp,
                       {"fixed_monthly": c.fixed_monthly})
        if data.get("ok"):
            data["checked_on"] = CHECKED_ON
            data["own_import_price"] = round(own_imp, 4)
            data["current"] = _supplier_name(c)
        self.ledger.save_state("compare", {"day": now.date().isoformat(), "data": data})
        return data

    def howcalc_view(self) -> dict:
        """'Zo rekenen we': alle bedragen waarmee Zonnestuur rekent, in gewone taal."""
        from .report import tax_credit_for
        now = datetime.now(self.tz)
        c = self.cfg.contract
        imp, feed = self.prices.import_price(now), self.prices.feed_in_price(now)
        base = self.baseline_price(now)
        credit = c.tax_credit_year if c.tax_credit_year is not None else tax_credit_for(now.year)
        kinds = {d.kind for d in self.cfg.devices}
        lines = []
        if c.type == "dynamic":
            lines.append(f"Stroom kopen kost nu € {imp:.3f} per kWh: marktprijs + energiebelasting (€ {self.prices.energy_tax(now):.4f}) + opslag leverancier (€ {c.supplier_markup:.4f}).")
            lines.append(f"Terugleveren levert nu € {feed:.3f} per kWh op (kale marktprijs zonder btw, plus of min wat je leverancier doet).")
            lines.append(f"Zonder Zonnestuur draait een apparaat op een willekeurig moment. Daarom rekenen we met de gemiddelde prijs van vandaag: € {base:.3f} per kWh.")
        else:
            lines.append(f"Stroom kopen kost € {c.import_price:.3f} per kWh (je vaste of variabele tarief, inclusief belastingen).")
            lines.append(f"Terugleveren levert € {c.feed_in_price:.3f} per kWh op, min € {c.return_cost:.3f} terugleverkosten: netto € {feed:.3f}.")
        lines.append(f"Elke kWh eigen zonnestroom die een apparaat gebruikt in plaats van terug te leveren, scheelt dus ± € {base - feed:.2f}.")
        if c.type == "dynamic":
            lines.append("Stroom van het net op een goedkoop uur telt als besparing (t.o.v. het daggemiddelde); op een duur uur, bijvoorbeeld voor de klaar-tijd, als extra kosten.")
        per = []
        if "boiler" in kinds:
            per.append("Boiler: zonder Zonnestuur verwarmt hij als zijn eigen thermostaat dat wil. Extra warmteverlies door een warmer vat rekenen we niet mee; dat is een paar procent.")
        if "ev" in kinds:
            per.append("Auto: zonder Zonnestuur laadt hij meteen bij aansluiten, met vol vermogen.")
        if "heatpump" in kinds:
            per.append("Warmtepomp: zonder Zonnestuur geen extra opwarmen bij zon of goedkope stroom.")
        if self.batteries:
            per.append("Thuisbatterij: vergeleken met de batterij die alleen op 'nul op de meter' staat.")
        if self.limiter:
            per.append("Omvormer begrenzen: de teruglevering bij een negatieve prijs die je niet hoeft te betalen.")
        fixed_year = 12 * (c.fixed_monthly + c.grid_monthly)
        bill = [f"Vaste kosten: € {c.fixed_monthly:.2f} leveringskosten + € {c.grid_monthly:.2f} netbeheer per maand"
                + ("" if fixed_year else " (nog niet ingevuld)") + f"; vermindering energiebelasting € {credit:.2f} per jaar"
                + (" (automatisch, Belastingplan 2027)" if c.tax_credit_year is None else "") + "."]
        return {"lines": lines, "per_device": per, "bill": bill,
                "note": "Alle bedragen zijn schattingen. Leveranciers passen hun tarieven een paar keer per jaar aan: controleer ze bij Instellingen → Energiecontract.",
                "baseline_price": round(base, 4), "value_own_kwh": round(base - feed, 4)}

    def _publish_ha(self) -> None:
        """Sensoren in Home Assistant: goed moment nu, zelf gebruikt, besparing deze maand."""
        try:
            ha = ha_client(self.cfg)
            m = self.moment_view()
            ha.set_state("sensor.zonnestuur_moment", m["state"], {"friendly_name": "Zonnestuur: goed moment", "icon":
                         {"groen": "mdi:white-balance-sunny", "oranje": "mdi:weather-partly-cloudy", "rood": "mdi:timer-sand"}[m["state"]],
                         "advies": m["word"], "uitleg": m["text"]})
            now = datetime.now(self.tz)
            month = now.replace(day=1).date()
            tot = self.ledger.totals(month)
            bat = sum(v["eur"] for v in self.ledger.battery_totals(month).values())
            ha.set_state("sensor.zonnestuur_besparing_maand", round(tot["eur_saved"] + bat, 2),
                         {"friendly_name": "Zonnestuur: opgeleverd deze maand", "unit_of_measurement": "EUR",
                          "device_class": "monetary", "icon": "mdi:piggy-bank-outline"})
            g = self.goal_view(now)
            if g.get("now") is not None and g.get("type") == "pct":
                ha.set_state("sensor.zonnestuur_zelf_gebruikt", g["now"],
                             {"friendly_name": "Zonnestuur: zon zelf gebruikt (maand)", "unit_of_measurement": "%",
                              "icon": "mdi:solar-power", "doel": g.get("target"), "status": g.get("status")})
        except Exception as exc:                     # Home Assistant even weg: volgende keer opnieuw
            log.debug("sensoren naar Home Assistant: %s", exc)

    def export_csv(self, days: int = 60) -> str:
        """Meetdata voor de proef, per dag (anoniem: geen namen of adressen, alleen apparaat-id's)."""
        import csv
        import io
        from .report import self_use
        now = datetime.now(self.tz)
        start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        rows = self.ledger.house_hours(int(start.timestamp()), int(now.timestamp()) + 3600)
        by_day: dict[str, list] = {}
        for r in rows:
            by_day.setdefault(datetime.fromtimestamp(r["ts"], self.tz).date().isoformat(), []).append(r)
        dev = {}
        for r in self.ledger.device_days(start.date()):
            dev.setdefault(r["day"], {})[r["device"]] = r
        stats = self.ledger.stats(start.date().isoformat())
        ids = [d.id for d in self.cfg.devices]
        keys = sorted({k for v in stats.values() for k in v if not k.startswith(("meetdag:", "override:"))})
        out = io.StringIO()
        w = csv.writer(out, delimiter=";")
        w.writerow(["dag", "opwek_kwh", "opwek_gemeten", "afname_kwh", "teruglevering_kwh", "zelf_gebruikt_pct", "kosten_eur", "opbrengst_eur"]
                   + [f"{i}_{x}" for i in ids for x in ("kwh", "kwh_zon", "eur", "meetdag", "ingrepen")] + keys)
        d = start.date()
        while d <= now.date():
            k = d.isoformat()
            hr = by_day.get(k, [])
            su = self_use(hr)
            st = stats.get(k, {})
            row = [k, round(su["pv"], 3), int(su["measured"]), round(sum(r["import_kwh"] for r in hr), 3), round(su["export"], 3),
                   "" if su["pct"] is None else round(su["pct"], 1), round(sum(r["cost"] for r in hr), 3), round(sum(r["revenue"] for r in hr), 3)]
            for i in ids:
                x = dev.get(k, {}).get(i, {})
                row += [round(x.get("kwh", 0), 3), round(x.get("kwh_solar", 0), 3), round(x.get("eur", 0), 3),
                        int(st.get(f"meetdag:{i}", 0)), int(st.get(f"override:{i}", 0))]
            row += [st.get(kk, 0) for kk in keys]
            w.writerow([str(v).replace(".", ",") if isinstance(v, float) else v for v in row])
            d += timedelta(days=1)
        return out.getvalue()

    # ---- eerlijke besparing en meetdagen ------------------------------------
    def baseline_price(self, now: datetime) -> float:
        """Wat een kWh zonder Zonnestuur had gekost: vast contract = je tarief; dynamisch = gemiddelde prijs van de dag
        (zonder sturing draait een apparaat gemiddeld op een willekeurig moment)."""
        if self.cfg.contract.type == "dynamic" and self.prices.slots:
            avg = self._avg_price_today(now)
            if avg is not None:
                return avg
        return self.prices.import_price(now, live=False) if self.cfg.contract.type != "dynamic" else self.prices.import_price(now)

    def baseline_ids(self, now: datetime) -> set:
        """Meetdagen: op willekeurige dagen (gemiddeld 1 op de 7) een apparaat een dag niet sturen."""
        t = self.cfg.trial or {}
        if not t.get("enabled"):
            return set()
        every = max(2, int(t.get("every", 7) or 7))
        only = set(t.get("devices") or [])
        out = set()
        import hashlib
        for d in self.cfg.devices:
            if only and d.id not in only:
                continue
            h = int(hashlib.sha256(f"{now.date().isoformat()}:{d.id}".encode()).hexdigest(), 16)
            if h % every == 0:
                out.add(d.id)
        day = now.date().isoformat()
        if out and getattr(self, "_baseline_logged", "") != day:
            self._baseline_logged = day
            for i in out:
                self.ledger.set_stat(day, f"meetdag:{i}", 1)
        return out

    # ---- regelronde ------------------------------------------------------
    def tick(self, now: Optional[datetime] = None, mono: Optional[float] = None) -> None:
        now = now or datetime.now(self.tz)
        mono = time.monotonic() if mono is None else mono
        with self.lock:
            dt = 0.0 if self.last_tick_mono is None else mono - self.last_tick_mono
            self.last_tick_mono = mono

            if not self.cfg.configured:
                return                      # nog niets gekoppeld: de koppel-assistent is aan zet
            if self.failsafe:
                self.failsafe.beat(self.cfg.devices)

            # 1. meter uitlezen
            reading = None
            try:
                if self.cfg.strategy == "price" and not self.cfg.has_meter:
                    raise _NoMeter()
                reading = self.meter.read()
                self.grid_w = reading.grid_w
                self.meter_online = True
                self.meter_fail_since = None
                self.health.meter_ok_mono = mono
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
                    self._confirm_switch(now, mono, d, s.on)
                except NotReady as exc:
                    self.controller.update_measurements(d.id, False, 0.0, None, online=False, offline_reason=str(exc))
                except (DeviceError, KeyError, ValueError) as exc:
                    self.controller.update_measurements(d.id, False, 0.0, None, online=False)
                    self.last_error = f"{d.name}: {exc}"
                    log.warning(self.last_error)

            # 2b. thuisbatterijen uitlezen
            self._read_batteries()

            # 3. leren, prijzen en zonvoorspelling bijwerken, garantie-planning maken
            self.relearn(now)
            self.prices.refresh(now)
            self._read_live_price(now, mono)
            self.forecast.refresh()
            cheapest = self._plan_guarantee(now)
            sunny = self._plan_sunny(now)

            # 4. beslissen en schakelen
            price_hours = self._plan_price(now)
            price_now = self.prices.import_price(now) if self.cfg.contract.type == "dynamic" and self.prices.slots else None
            pro = self.license_state().pro
            if not pro:                     # Basis: alleen zon-overschot en de klaar-tijd-garantie
                cheapest, sunny, price_hours, price_now = {}, {}, {}, None
            basic_id = None if pro else self._basic_device()
            ctx = Context(now=now, mono=mono, grid_w=self._grid_for_controller(now), dt=dt, cheapest_hours=cheapest, sunny_hours=sunny,
                          price_hours=price_hours, price_now=price_now, baseline=self.baseline_ids(now))
            for dec in self.controller.step(ctx):
                if (basic_id and dec.on and dec.device_id != basic_id
                        and not dec.reason.startswith(("handmatig", "garantie"))):
                    self.controller.states[dec.device_id].on = False
                    self.controller.states[dec.device_id].reason = "automatisch sturen: met Zonnestuur Pro"
                    continue
                dev = self._device(dec.device_id)
                try:
                    sw = self.switches[dec.device_id]
                    if dec.on != self._last_on.get(dec.device_id):
                        sw.set(dec.on)
                        log.info("%s -> %s (%s)", dec.device_id, "AAN" if dec.on else "UIT", dec.reason)
                        if dev:
                            self.health.sent(now, mono, dev, dec.on)
                            self.events.switched(now, dev, dec.on, dec.reason, self._event_inputs(now, dev, ctx))
                    if dec.on and dec.power_w is not None and hasattr(sw, "set_power"):
                        sw.set_power(dec.power_w)
                        log.info("%s -> %d W", dec.device_id, dec.power_w)
                    self._last_on[dec.device_id] = dec.on
                except DeviceError as exc:
                    self.last_error = f"schakelen {dec.device_id}: {exc}"
                    log.warning(self.last_error)
                    self.controller.states[dec.device_id].on = not dec.on
                    if dev and dec.on != self._last_on.get(dec.device_id):
                        self.health.send_failed(now, dev, str(exc))
                        self.ledger.inc(now.date().isoformat(), "switch_failed")

            if basic_id:
                for d in self.cfg.devices:
                    st = self.controller.states[d.id]
                    if d.id != basic_id and st.mode == "auto" and not st.on:
                        st.reason = "automatisch sturen: met Zonnestuur Pro"

            # 4a. thuisbatterijen: plan per uur, nu uitvoeren (na de apparaten: die gaan voor)
            # 4b. omvormer begrenzen als terugleveren geld kost, en meldingen
            if pro:
                self._run_batteries(now, mono)
                self._limit_inverter(now, mono)
                self._notify(now, mono)
            else:
                for b in self.batteries.values():
                    b.reason = "batterij regelt zichzelf; slim plannen met Zonnestuur Pro"
                if self.limiter and self.limiter.active:
                    self._limit_inverter_release(mono)

            # 5. boekhouding
            if self.grid_w is not None:
                batt_w = sum(b.power_w or 0.0 for b in self.batteries.values() if b.online)
                pv, pv_src = self._read_pv(mono)
                self.ledger.record_house(now, dt, self.grid_w, self.prices.import_price(now),
                                         self.prices.feed_in_price(now), sum(max(0.0, p) for p in powers.values()), batt_w,
                                         pv, pv_src, self.forecast.raw_production_w(now) if self.cfg.solar.has_panels else None)
            for b in self.batteries.values():
                if b.online and b.power_w is not None:
                    self.ledger.record_battery(now, dt, b.cfg.id, b.power_w, self.grid_w, self.prices.import_price(now),
                                               self.prices.feed_in_price(now))
            if self.cfg.strategy == "price":
                # Besparing zonder panelen: wat je betaalt tegenover de gemiddelde prijs van vandaag
                avg = self._avg_price_today(now)
                saving = (avg - price_now) if (avg is not None and price_now is not None) else 0.0
                self.events.energy(self.ledger.record(now, dt, None, powers, saving, all_counts=True))
            else:
                base = self.baseline_price(now)
                per = self.ledger.record(now, dt, self.grid_w, powers, base - self.prices.feed_in_price(now),
                                         grid_value=base - self.prices.import_price(now))
                self.events.energy(per)
            if reading is not None:
                self.ledger.record_meter(now, reading.import_kwh, reading.export_kwh)
            if mono - getattr(self, "_ha_pub", -1e9) > 60 and (self.cfg.kiosk or {}).get("ha_sensors", True) \
                    and effective_ha(self.cfg).get("token") and self.switches is not None and not getattr(self, "_testing", False):
                self._ha_pub = mono
                threading.Thread(target=self._publish_ha, daemon=True, name="ha-sensoren").start()
            if mono - self._last_save > 60:
                self.ledger.flush()
                self._save_state()
                self._last_save = mono

    def _grid_for_controller(self, now: datetime) -> Optional[float]:
        """Is de omvormer afgeknepen, dan ziet de meter geen overschot meer. Voor de apparaten tellen we het
        weggeknepen deel (volgens de zonvoorspelling) als overschot mee: liever zelf gebruiken dan weggooien."""
        g = self.grid_w
        if g is None:
            return g
        # Wat de batterij nu van de zon laadt, is voor de apparaten nog beschikbaar (die gaan voor);
        # wat hij ontlaadt, telt als afname (de batterij mag geen boiler verwarmen).
        g -= sum(b.power_w or 0.0 for b in self.batteries.values() if b.online and b.action in ("auto", "save"))
        if not (self.limiter and self.limiter.active):
            return g
        fc = self.forecast.production_w(now)
        if fc is None:
            return g
        return g - max(0.0, min(self.limiter.max_w, fc) - self.limiter.limit_w)

    # ---- thuisbatterijen ----------------------------------------------------
    def _read_batteries(self) -> None:
        if not self.batteries:
            return
        try:
            ha = ha_client(self.cfg)
        except ValueError:
            return
        for b in self.batteries.values():
            b.read(ha)

    def _battery_hours(self, now: datetime, b: BatteryRuntime) -> list[HourIn]:
        start = now.replace(minute=0, second=0, microsecond=0)
        rows = self.ledger.house_hours(int((now - timedelta(days=15)).timestamp()), int(now.timestamp()) + 3600)
        net = hourly_profile(rows, self.tz)
        imp = import_profile(rows, self.tz)
        base = self.cfg.solar.base_load_w / 1000
        if net is None:
            net = [base] * 24
            imp = [base] * 24
        if self.cfg.contract.type == "dynamic" and self.prices.slots:
            per_hour: dict[datetime, list[float]] = {}
            for s, e, p in self.prices.upcoming(start, start + timedelta(hours=36)):
                per_hour.setdefault(s.astimezone(self.tz).replace(minute=0, second=0, microsecond=0), []).append(p)
            times = sorted(t for t in per_hour if t >= start)[:36]
            price = {t: sum(v) / len(v) for t, v in per_hour.items()}
        else:
            times = [start + timedelta(hours=i) for i in range(24)]
            price = {t: self.prices.import_price(t, live=False) for t in times}
        learned = self.model.days >= 3
        hours = []
        for t in times:
            h = t.hour
            n = net[h]
            load = self.model.base_w(t) / 1000 if learned else max(imp[h], base)   # geleerd eigen verbruik van dit uur
            if self.cfg.solar.has_panels:
                fc = self.forecast.production_w(t)
                if fc is not None:                   # zonvoorspelling (bijgesteld) vervangt het zon-deel
                    n = load - fc / 1000
            elif learned:
                n = load
            hours.append(HourIn(t, price[t], self.prices.feed_in_price(t), n))
        return hours

    def _run_batteries(self, now: datetime, mono: float) -> None:
        if not self.batteries:
            return
        cheap_devices = any(st.on and (st.reason.startswith("goedkoop") or st.reason.startswith("garantie"))
                            for st in self.controller.states.values())
        try:
            ha = ha_client(self.cfg)
        except ValueError:
            return
        for b in self.batteries.values():
            if not b.online or b.soc is None:
                continue
            key = f"{now:%Y%m%d%H}:{len(self.prices.slots)}:{self.forecast.production_w(now) is not None}"
            if key != b.plan_key:
                hours = self._battery_hours(now, b)
                b.plan = plan_battery(b.cfg, b.soc, hours)
                b.value = plan_value(b.cfg, b.soc, hours, b.plan)
                b.plan_key = key
            prev = b.action
            b.decide_action(now, cheap_devices)
            if b.action != prev and prev is not None:
                word = {"auto": "levert aan het huis en laadt met overschot", "save": "spaart voor later (laadt alleen met zon)",
                        "charge": "laadt van het net", "idle": "staat stil"}.get(b.action, b.action)
                self.events.note(now, f"batterij:{b.cfg.id}", b.cfg.name, "BATTERIJ", f"{b.cfg.name} {word} vanaf {now:%H:%M}: {b.reason}.")
            try:
                msg = b.apply(ha, mono, self.grid_w)
                if msg:
                    log.info("%s: %s -> %s", b.cfg.name, b.reason, msg)
            except (DeviceError, ValueError, OSError) as exc:
                self.last_error = f"{b.cfg.name}: {exc}"
                log.warning(self.last_error)

    def _limit_inverter_release(self, mono: float) -> None:
        self.limiter._last = -1e9
        value = self.limiter.decide(mono, self.grid_w, 1.0)             # 'terugleveren loont': vol vermogen
        if value is not None:
            try:
                self.limiter.apply(ha_client(self.cfg), value)
            except (DeviceError, ValueError, OSError) as exc:
                log.warning("omvormer terugzetten mislukt: %s", exc)

    def _limit_inverter(self, now: datetime, mono: float) -> None:
        if not self.limiter:
            return
        was = self.limiter.active
        value = self.limiter.decide(mono, self.grid_w, self.prices.feed_in_price(now))
        if value is None:
            return
        try:
            self.limiter.apply(ha_client(self.cfg), value)
            log.info("omvormer %s -> %s %s (%s)", self.limiter.entity, value, self.limiter.unit, self.limiter.reason)
            if self.limiter.active != was:
                fp = self.prices.feed_in_price(now)
                self.events.note(now, "omvormer", "Omvormer", "OMVORMER_AF" if self.limiter.active else "OMVORMER_VOL",
                                 f"Panelen begrensd om {now:%H:%M}: terugleveren kost nu geld ({'−' if fp < 0 else ''}€ {abs(fp):.3f} per kWh). "
                                 "Je panelen leveren precies wat je huis gebruikt." if self.limiter.active
                                 else f"Panelen om {now:%H:%M} weer op vol vermogen: terugleveren kost geen geld meer.")
        except (DeviceError, ValueError, OSError) as exc:
            self.last_error = f"omvormer: {exc}"
            log.warning(self.last_error)

    def _notify(self, now: datetime, mono: float) -> None:
        if not self.notifier:
            return
        neg = expensive = None
        if self.cfg.contract.type == "dynamic" and self.prices.slots:
            tomorrow = (now + timedelta(days=1)).date()
            start = datetime.combine(tomorrow, datetime.min.time(), self.tz)
            neg = negative_window([(s.astimezone(self.tz), e.astimezone(self.tz), p)
                                   for s, e, p in self.prices.upcoming(start, start + timedelta(days=1))], tomorrow)
            ev0 = now.replace(hour=18, minute=0, second=0, microsecond=0)
            evening = [(s.astimezone(self.tz), e.astimezone(self.tz), p) for s, e, p in self.prices.upcoming(ev0, ev0 + timedelta(hours=3))]
            dear = [x for x in evening if x[2] > 0.50]
            if dear:
                expensive = (dear[0][0], dear[-1][1], max(p for _, _, p in dear))
        sunny = None
        if self.cfg.solar.has_panels and now.hour >= 19:
            try:
                w = next((w for w in self.coach_view().get("windows") or [] if w.get("tomorrow")), None)
                if w and w["peak_kw"] >= 2 and w["kwh"] >= 4:
                    sunny = dict(w, day=(now + timedelta(days=1)).date().isoformat())
            except Exception:
                sunny = None
        risk = []
        for d in self.cfg.devices:
            st = self.controller.states.get(d.id)
            if not st or st.online or st.offline_reason or not d.ready_times:
                continue
            nxt = self.controller.next_unsatisfied(d, st, now)
            if nxt and (nxt - now) <= timedelta(minutes=max(60, d.guarantee_min)):
                risk.append((d, nxt))
        items = self.notifier.evaluate(now, mono, grid_w=self.grid_w, meter_online=self.meter_online or not self.cfg.has_meter,
                                       has_panels=self.cfg.solar.has_panels, devices=self.cfg.devices,
                                       states=self.controller.states, tomorrow_negative=neg, tomorrow_sunny=sunny,
                                       evening_expensive=expensive, guarantee_risk=risk,
                                       value_kwh=max(0.05, self.baseline_price(now) - self.prices.feed_in_price(now)))
        if self.notifier.morning and self.notifier.tip_allowed(now) and self.cfg.solar.has_panels and 8 <= now.hour < 10 and not any(
                i[1] in ("surplus", "negative_tomorrow", "sunny_tomorrow", "expensive_evening") for i in items):
            key = f"zon:{now:%Y-%m-%d}"
            if self.notifier._may(key, "morning", now):
                from .coach import morning_message
                try:
                    wins = self.coach_view().get("windows") or []
                except Exception:
                    wins = []
                msg = morning_message(wins[0] if wins else None)
                if msg:
                    items.append((key, "morning", msg[0], msg[1]))
        # weekrapport: zondag vanaf 19:00
        if self.notifier.reports and now.weekday() == 6 and 19 <= now.hour < 22:
            key = f"week:{now:%G-%V}"
            if self.notifier._may(key, "week", now):
                try:
                    r = self.report_view(0)
                    items.append((key, "week", r["title"], r["message"]))
                except Exception as exc:
                    log.warning("weekrapport maken mislukt: %s", exc)
        self.notifier.send(self._ha_or_none(), now, items, click=self._public_url("rapport"))
        for k, kind, *_ in items:
            self.ledger.inc(now.date().isoformat(), f"melding_{kind}")

    def _ha_or_none(self):
        return (lambda: ha_client(self.cfg)) if effective_ha(self.cfg).get("token") else None

    def _public_url(self, page: str) -> str:
        base = str((self.cfg.notify or {}).get("link") or "")
        if not base and not effective_ha(self.cfg).get("addon"):
            base = "http://zonnestuur.local/"
        return (base.rstrip("/") + "/" + page) if base else ""

    def notify_test(self, conf: dict) -> dict:
        from .notify import channels_of, merge, send_all
        conf = merge(self.cfg.notify or {}, conf or {})
        if not channels_of(conf):
            return {"ok": False, "error": "Kies eerst hoe je meldingen wilt krijgen"}
        res = send_all(conf, self._ha_or_none(), "Zonnestuur",
                       "Zo ziet een melding van Zonnestuur eruit. Je krijgt er alleen een als er iets misgaat, "
                       "als er een kans is om geld te besparen (hoogstens één per dag) en op zondag je weekrapport.")
        bad = {k: v for k, v in res.items() if v != "ok"}
        names = {"ha": "Home Assistant-app", "ntfy": "ntfy", "telegram": "Telegram", "email": "e-mail"}
        if bad:
            return {"ok": False, "results": res, "error": "; ".join(f"{names[k]}: {v}" for k, v in bad.items())}
        return {"ok": True, "results": res, "sent": [names[k] for k in res]}

    def _plan_guarantee(self, now: datetime) -> dict[str, set]:
        """Zon + goedkope stroom (dynamisch contract, met zonnepanelen).

        Per apparaat: hoeveel moet er nog gebeuren vóór de klaar-tijd (of vandaag, zonder klaar-tijd), min wat de
        zon naar verwachting levert. Het tekort komt in de goedkoopste uren van het hele venster, dus ook 's nachts
        of op een grijze dag. Het plan blijft staan (verschuift niet elke ronde); alleen als er nieuwe prijzen bij
        komen en het plan nog niet begonnen is, wordt opnieuw gepland.
        """
        if self.cfg.contract.type != "dynamic" or self.cfg.strategy != "solar" or not self.prices.slots:
            return {}
        plans = getattr(self, "_combo_plans", {})
        hour = now.replace(minute=0, second=0, microsecond=0)
        out: dict[str, set] = {}
        for d in self.cfg.devices:
            st = self.controller.states[d.id]
            if d.ready_times and d.guarantee_min > 0:
                end = self.controller.next_unsatisfied(d, st, now)
                if end is None:
                    plans.pop(d.id, None)
                    continue
                start = end - timedelta(hours=d.full_lookback_h)
                need_s = d.guarantee_min * 60
            elif self.run_min(d) > 0 or d.one_shot:
                end = now.replace(hour=23, minute=0, second=0, microsecond=0)
                if end <= now:
                    continue
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                need_s = (self.run_min(d) or 120) * 60 - (0 if d.one_shot else st.run_seconds_today)
            else:
                continue
            old = plans.get(d.id)
            key = (end.isoformat(), len(self.prices.slots))
            if old and old["end"] == end.isoformat() and (old["key"] == key or any(h <= hour for h in old["hours"])):
                out[d.id] = old["hours"]
                continue
            sun_s = self.forecast.expected_surplus_seconds(now, end, d.start_threshold_w) or 0.0
            remaining = need_s - 0.8 * sun_s
            if remaining <= 0:
                plans[d.id] = {"end": end.isoformat(), "key": key, "hours": set()}
                continue
            plan_start = max(hour, start)
            candidates = self.prices.upcoming(plan_start.astimezone(timezone.utc), end.astimezone(timezone.utc))
            if d.one_shot:
                b = plan_cheapest_block(remaining, candidates)
                hours = {b} if b else set()
            else:
                hours = plan_cheapest_hours(remaining, candidates)
            hours = {h.astimezone(self.tz).replace(minute=0, second=0, microsecond=0) for h in hours}
            plans[d.id] = {"end": end.isoformat(), "key": key, "hours": hours}
            out[d.id] = hours
        self._combo_plans = plans
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
            run_s = max(self.run_min(d), d.guarantee_min if d.ready_times else 0) * 60 or 3600
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
        """Dagplanning voor zelfverbruik: het verwachte overschot van vandaag, verdeeld over alle apparaten.

        Eens per uur opnieuw, met wat elk apparaat vandaag al gedraaid heeft.
        """
        key = now.strftime("%Y-%m-%d %H")
        if getattr(self, "_sunny_key", None) == key:
            return self._sunny
        out: dict[str, set] = {}
        if self.forecast.hours and self.cfg.strategy == "solar":
            hour = now.replace(minute=0, second=0, microsecond=0)
            surplus, t = [], max(hour, now.replace(hour=5, minute=0, second=0, microsecond=0))
            end_day = now.replace(hour=22, minute=0, second=0, microsecond=0)
            while t < end_day:
                prod = self.forecast.production_w(t + timedelta(minutes=30))
                if prod is not None:
                    surplus.append((t, prod - self.model.base_w(t.astimezone(self.tz))))
                t += timedelta(hours=1)
            needs = []
            for d in sorted(self.cfg.devices, key=lambda x: x.priority):
                st = self.controller.states[d.id]
                if self.run_min(d) <= 0 and not d.one_shot:
                    continue
                if d.ready_times and self.controller.next_unsatisfied(d, st, now) is None:
                    continue                                  # al vol / klaar
                left_h = max(0.0, (self.run_min(d) or 120) / 60 - (0 if d.one_shot else st.run_seconds_today / 3600))
                ready = self.controller.ready_datetimes(d, now)
                deadline = ready[0] - timedelta(minutes=d.guarantee_min) if ready and ready[0].date() == now.date() else None
                needs.append(Need(d.id, d.max_w, left_h, d.min_w if d.modulating else 0.0, d.modulating, d.one_shot, deadline))
            out = plan_day(needs, surplus)
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
            if self.failsafe:
                self.failsafe.sync(new.devices)
            self.prices = PriceProvider(new.contract, enabled=new.use_prices)
            self.forecast = SolarForecast(new.solar, enabled=new.use_forecast)
            self.model.fallback_w = new.solar.base_load_w
            self._wire_model()
            self._model_key = ""                     # opnieuw leren met de nieuwe instellingen
            self.controller = Controller(new)
            for dev_id, st in old_states.items():
                if dev_id in self.controller.states:
                    self.controller.states[dev_id] = st
            self._sunny_key = None
            self.meter_fail_since, self.last_error = None, ""
            self.scanner.extra_hosts = new.scan_extra
            old_lim, self.limiter = self.limiter, InverterLimiter.from_config(new.inverter)
            if old_lim and self.limiter and old_lim.entity == self.limiter.entity:
                self.limiter.limit_w, self.limiter._last, self.limiter.reason = old_lim.limit_w, old_lim._last, old_lim.reason
            if old_lim and old_lim.active and (not self.limiter or self.limiter.entity != old_lim.entity):
                try:                                     # begrenzing uitgezet: omvormer terug naar vol vermogen
                    old_lim.apply(ha_client(new), old_lim._value(old_lim.max_w))
                except Exception as exc:
                    log.warning("omvormer terugzetten mislukt: %s", exc)
            self._start_ocpp()
            old_b, self.batteries = self.batteries, {b["id"]: BatteryRuntime(BatteryConfig.from_dict(b)) for b in new.batteries}
            for bid, rt in self.batteries.items():
                if bid in old_b:
                    rt.soc, rt.power_w, rt.online = old_b[bid].soc, old_b[bid].power_w, old_b[bid].online
            old_n, self.notifier = self.notifier, Notifier.from_config(new.notify)
            if old_n and self.notifier:
                self.notifier.sent, self.notifier.history = old_n.sent, old_n.history
                self.notifier.active, self.notifier.tip_day = old_n.active, old_n.tip_day
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
        try:
            notify = ha.notify_services()
        except DeviceError:
            notify = []
        return {"ok": True, "entities": len(states), **ha_candidates(states), "notify_services": notify}

    def ha_candidates(self) -> dict:
        ha = effective_ha(self.cfg)
        if not ha.get("url"):
            return {"ok": False, "error": "Home Assistant is nog niet gekoppeld"}
        return self.ha_connect(ha["url"], ha.get("token", ""))

    def read_meter(self, spec: dict) -> dict:
        """Meter proberen uit te lezen voordat hij wordt opgeslagen (koppel-assistent)."""
        cfg = Config(p1_host=str(spec.get("host", "")), meter=dict(spec.get("meter") or {}), homeassistant=self.cfg.homeassistant,
                     mqtt=self.cfg.mqtt, homey=self.cfg.homey)
        try:
            meter = make_meter(cfg)
            for _ in range(30 if cfg.meter_driver == "mqtt" else 1):     # MQTT: even wachten op het eerste bericht
                try:
                    r = meter.read()
                    break
                except DeviceError:
                    if cfg.meter_driver != "mqtt":
                        raise
                    time.sleep(0.1)
            else:
                r = meter.read()
            return {"ok": True, "grid_w": round(r.grid_w)}
        except (DeviceError, ValueError, KeyError) as exc:
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
                                "ready_times": d.ready_times, "ready_days": d.ready_days, "guarantee_min": d.guarantee_min, "driver": d.driver,
                                "modulating": d.modulating, "min_w": round(d.min_w), "max_w": round(d.max_w),
                                "w_per_step": round(d.w_per_step),
                                "failsafe": (self.failsafe.status.get(d.id, "") if self.failsafe else ""),
                                "next_ready": ready[0].isoformat(timespec="minutes") if ready else None,
                                "next_ready_ok": (self.controller.is_satisfied(d, self.controller.states[d.id], ready[0])
                                                  if ready else None),
                                "best_hours": self._best_hours(d.id, now),
                                "last_event": next(iter(self.events.view_device(d.id, int((now - timedelta(days=2)).timestamp()))), None),
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
                "value": self._value_view(today, month_start, year_start),
                "last_error": self.last_error,
                "problems": self.problems(),
                "license": self.license_state().to_dict(),
                "has_panels": self.cfg.solar.has_panels,
                "solar_kwp": self.cfg.solar.kwp,
                "inverter": ({"entity": self.limiter.entity, "limited": self.limiter.active,
                              "limit_w": self._rounded(self.limiter.limit_w), "reason": self.limiter.reason}
                             if self.limiter else None),
                "notifications": self.notifier.history[:5] if self.notifier else [],
                "batteries": self._batteries_view(today),
                "learned": self.learned_view(),
                "health": self.health.view(now, time.monotonic()),
                "last_events": self.events.view(int((now - timedelta(hours=36)).timestamp()), 4),
                "baseline_today": sorted(self.baseline_ids(now)),
                "motivation": self.cfg.motivation,
                "goal": self.goal_view(now),
            }

    def _value_view(self, today, month_start, year_start) -> dict:
        """Wat Zonnestuur oplevert: apparaten (zon en goedkope uren) plus de thuisbatterij."""
        from datetime import date as _d
        out = {}
        for key, since in (("today", today), ("month", month_start), ("year", year_start), ("total", _d(2000, 1, 1))):
            dev = self.ledger.totals(since)["eur_saved"]
            bat = sum(v["eur"] for v in self.ledger.battery_totals(since).values())
            out[key] = {"devices": round(dev, 2), "battery": round(bat, 2), "eur": round(dev + bat, 2)}
        with self.ledger.lock:
            row = self.ledger.conn.execute("SELECT MIN(day) FROM device_day").fetchone()
        out["since"] = row[0] if row and row[0] else None
        return out

    def learned_view(self) -> dict:
        out = self.model.to_dict()
        out["devices"] = [{"id": d.id, "name": d.name, "learned_min": (out["device_run_min"].get(d.id) or {}).get("min"),
                           "kwh": (out["device_run_min"].get(d.id) or {}).get("kwh"), "days": (out["device_run_min"].get(d.id) or {}).get("days"),
                           "setting_min": d.expected_run_min, "learn": d.learn_run, "used_min": round(self.run_min(d))}
                          for d in self.cfg.devices]
        out["pv_sensor"] = bool(self.cfg.solar.pv_entity)
        out["fallback_w"] = round(self.cfg.solar.base_load_w)
        return out

    def _batteries_view(self, today) -> list:
        if not self.batteries:
            return []
        t_day = self.ledger.battery_totals(today)
        t_month = self.ledger.battery_totals(today.replace(day=1))
        out = []
        for b in self.batteries.values():
            d = b.to_dict(self.tz)
            d["today"] = t_day.get(b.cfg.id, {"charged_kwh": 0, "grid_kwh": 0, "discharged_kwh": 0, "eur": 0})
            d["month"] = t_month.get(b.cfg.id, {"charged_kwh": 0, "grid_kwh": 0, "discharged_kwh": 0, "eur": 0})
            out.append(d)
        return out

    def backfill_history(self, days: int = 365) -> dict:
        """Verbruik en teruglevering van het afgelopen jaar uit Home Assistant halen (meter-sensoren)."""
        from .hastats import hourly_means
        m = self.cfg.meter or {}
        ha = effective_ha(self.cfg)
        if m.get("driver") != "ha" or not ha.get("token"):
            return {"ok": False, "error": "alleen mogelijk met een meter-sensor uit Home Assistant"}
        ids = [x for x in (m.get("entity"), m.get("import_entity"), m.get("export_entity")) if x]
        pv_ent = self.cfg.solar.pv_entity if self.cfg.solar.has_panels else ""
        if pv_ent:
            ids.append(pv_ent)
        now = datetime.now(self.tz)
        start = (now - timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
        try:
            client = ha_client(self.cfg)
            factor = {}
            for e in ids:
                unit = str((client.state(e).get("attributes") or {}).get("unit_of_measurement", "W")).lower()
                factor[e] = 1000.0 if unit == "kw" else 1.0
            data = hourly_means(ha["url"], ha["token"], ids, start)
        except Exception as exc:  # netwerk, rechten, oude HA-versie
            return {"ok": False, "error": f"Home Assistant-statistieken niet op te halen: {exc}"}
        sign = -1.0 if m.get("invert") else 1.0
        hours: dict[int, list] = {}
        if m.get("entity"):
            for ts, v in data.get(m["entity"], []):
                w = v * factor[m["entity"]] * sign
                hours[ts] = [max(w, 0) / 1000, max(-w, 0) / 1000]
        else:
            for ts, v in data.get(m.get("import_entity"), []):
                hours.setdefault(ts, [0.0, 0.0])[0] = max(0.0, v * factor[m["import_entity"]]) / 1000
            for ts, v in data.get(m.get("export_entity"), []):
                hours.setdefault(ts, [0.0, 0.0])[1] = max(0.0, v * factor[m["export_entity"]]) / 1000
        if not hours:
            return {"ok": False, "error": "Home Assistant heeft voor deze sensor(en) geen langetermijnstatistieken"}
        # Prijzen per uur erbij, zodat de euro's kloppen met je contract
        c = self.cfg.contract
        market: dict[int, float] = {}
        if c.type == "dynamic":
            first = min(hours)
            hist = _price_history(datetime.fromtimestamp(first, self.tz).date().isoformat(),
                                  (now + timedelta(days=1)).date().isoformat())
            for k, v in hist.get("hours", []):
                market[int(datetime.fromisoformat(k.replace("Z", "+00:00")).timestamp())] = v
        pv_h = {ts: max(0.0, v * factor[pv_ent]) / 1000 for ts, v in data.get(pv_ent, [])} if pv_ent else {}
        rows = []
        for ts, (imp, exp) in sorted(hours.items()):
            when = datetime.fromtimestamp(ts, self.tz)
            if c.type == "dynamic" and ts in market:
                ip = market[ts] + self.prices.energy_tax(when) + c.supplier_markup
                fp = market[ts] / 1.21 + self.prices._feed_in_adjust()
            else:
                ip, fp = c.import_price, c.feed_in_price - (c.return_cost if c.type == "fixed" else 0)
            rows.append({"ts": ts, "import_kwh": imp, "export_kwh": exp, "cost": imp * ip, "revenue": exp * fp,
                         "pv_kwh": pv_h.get(ts, 0.0), "pv_src": 2 if ts in pv_h else (0 if self.cfg.solar.has_panels else 2)})
        added = self.ledger.insert_house_hours(rows)
        self.ledger.save_state("backfill", {"done": now.isoformat(), "hours": len(rows)})
        self._model_key = ""                         # meteen opnieuw leren met de historie
        log.info("Historie uit Home Assistant: %d uren toegevoegd", added)
        return {"ok": True, "hours": len(rows), "added": added, "from": datetime.fromtimestamp(min(hours), self.tz).date().isoformat()}

    def coach_view(self) -> dict:
        """Zonnecoach: zelf gebruikt %, zonnevenster en persoonlijke tips (gecachet: 5 minuten)."""
        from . import coach
        from .insight import compute
        now = datetime.now(self.tz)
        cached = getattr(self, "_coach_cache", None)
        if cached and (now - cached[0]).total_seconds() < 300:
            return cached[1]
        with self.lock:
            rows = self.ledger.house_hours(int((now - timedelta(days=30)).timestamp()), int(now.timestamp()) + 3600)
            has_panels = self.cfg.solar.has_panels
            sc = coach.scores(rows, self.tz, now, has_panels)
            windows = coach.solar_windows(now, self.forecast.production_w, lambda t: self.model.base_w(t.astimezone(self.tz))) \
                if has_panels and self.forecast.hours else []
            ins = compute(rows[-14 * 24:], self.tz, has_panels, self.cfg.devices, bool((self.cfg.inverter or {}).get("entity"))) if rows else {"ok": False}
            devs = []
            for d in self.cfg.devices:
                P = d.params or {}
                bd = (float(P["boost_temp"]) - float(P["normal_temp"])) if d.driver == "ha_setpoint" and "boost_temp" in P and "normal_temp" in P else None
                devs.append({"id": d.id, "name": d.name, "kind": d.kind, "driver": d.driver, "modulating": d.modulating,
                             "min_w": d.min_w if d.modulating else d.power_w, "power_w": d.power_w, "start_surplus_w": d.start_surplus_w,
                             "boost_delta": bd})
            evening = 0.0
            if rows:
                ev = [r["import_kwh"] for r in rows[-14 * 24:] if datetime.fromtimestamp(r["ts"], self.tz).hour >= 17]
                evening = sum(ev) / max(1, len({datetime.fromtimestamp(r["ts"], self.tz).date() for r in rows[-14 * 24:]}))
            batts = ins.get("batteries") if ins.get("ok") else None
            ctx = {"has_panels": has_panels, "export_per_day": ins.get("export_per_day", 0.0) if ins.get("ok") else 0.0,
                   "import_evening_per_day": evening, "peak_hours": ins.get("peak_hours") if ins.get("ok") else [],
                   "value_kwh": (ins.get("avg_import_price", 0.25) - ins.get("avg_export_value", 0.05)) if ins.get("ok") else 0.2,
                   "devices": devs, "batteries": bool(self.batteries), "inverter": bool(self.limiter),
                   "negative_feed": any(sl.market < 0 for sl in self.prices.slots),
                   "night_w": self.model.night_w, "window": windows[0] if windows else None,
                   "best_battery": min((b for b in batts if b.get("payback_years")), key=lambda b: b["payback_years"], default=None) if batts else None}
            out = {"has_panels": has_panels, "scores": sc, "windows": windows, "tips": coach.tips(ctx),
                   "export_per_day": round(ctx["export_per_day"], 1)}
        self._coach_cache = (now, out)
        return out

    def insight_view(self, days: int = 365) -> dict:
        from .insight import compute
        now = datetime.now(self.tz)
        rows = self.ledger.house_hours(int((now - timedelta(days=days)).timestamp()), int(now.timestamp()) + 3600)
        out = compute(rows, self.tz, self.cfg.solar.has_panels, self.cfg.devices, bool((self.cfg.inverter or {}).get("entity")))
        out["can_backfill"] = (self.cfg.meter or {}).get("driver") == "ha"
        return out

    def prices_view(self) -> dict:
        """Prijzen voor het dashboard: all-in per uur voor vandaag en morgen (dynamisch) of de vaste tarieven."""
        now = datetime.now(self.tz)
        c = self.cfg.contract
        if c.type != "dynamic":
            return {"type": "fixed", "import": c.import_price, "feed_in": c.feed_in_price, "return_cost": c.return_cost,
                    "net_feed_in": round(c.feed_in_price - c.return_cost, 4), "supplier": _supplier_name(c)}
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=2)
        with self.lock:
            slots = [s for s in self.prices.slots if start.astimezone(timezone.utc) <= s.start < end.astimezone(timezone.utc)]
            planned: dict[int, list] = {}
            names = {d.id: d.name for d in self.cfg.devices}
            for plans in (getattr(self, "_price_plans", {}), getattr(self, "_combo_plans", {})):
                for dev_id, p in plans.items():
                    for h in p.get("hours", ()):
                        planned.setdefault(int(h.timestamp()), []).append(names.get(dev_id, dev_id))
            rows = [{"ts": int(s.start.timestamp()), "end": int(s.end.timestamp()),
                     "all_in": round(self.prices.import_price(s.start, live=False), 4), "market": round(s.market, 4),
                     "feed_in": round(self.prices.feed_in_price(s.start), 4),
                     "planned": sorted(set(planned.get(int(s.start.timestamp()), [])))} for s in slots]
            live = self.prices._live(now)
        today = [r for r in rows if r["ts"] < int((start + timedelta(days=1)).timestamp())]
        avg = sum(r["all_in"] for r in today) / len(today) if today else None
        return {"type": "dynamic", "supplier": _supplier_name(c), "start": int(start.timestamp()), "slots": rows,
                "avg_today": None if avg is None else round(avg, 4), "live_now": live,
                "tomorrow_known": any(r["ts"] >= int((start + timedelta(days=1)).timestamp()) for r in rows)}

    def usage_view(self, period: str, offset: int) -> dict:
        """Verbruik van het hele huis per uur (dag), per dag (week, maand) of per maand (jaar), met euro's."""
        now = datetime.now(self.tz)
        day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "day":
            start = day0 - timedelta(days=offset)
            end = start + timedelta(days=1)
            steps = [start + timedelta(hours=h) for h in range(24)]
            key = lambda ts: datetime.fromtimestamp(ts, self.tz).replace(minute=0, second=0, microsecond=0)
            label = lambda t: t.strftime("%H:00")
            title = start.strftime("%d-%m-%Y")
        elif period == "week":
            monday = day0 - timedelta(days=day0.weekday())
            start = monday - timedelta(weeks=offset)
            end = start + timedelta(days=7)
            steps = [start + timedelta(days=i) for i in range(7)]
            key = lambda ts: datetime.fromtimestamp(ts, self.tz).replace(hour=0, minute=0, second=0, microsecond=0)
            label = lambda t: ["ma", "di", "wo", "do", "vr", "za", "zo"][t.weekday()] + f" {t.day}"
            title = f"week {start.isocalendar()[1]}"
        elif period == "month":
            m = (day0.year * 12 + day0.month - 1) - offset
            start = day0.replace(year=m // 12, month=m % 12 + 1, day=1)
            nm = m + 1
            end = start.replace(year=nm // 12, month=nm % 12 + 1)
            steps, t = [], start
            while t < end:
                steps.append(t)
                t = (t + timedelta(days=1)).replace(hour=0)
            key = lambda ts: datetime.fromtimestamp(ts, self.tz).replace(hour=0, minute=0, second=0, microsecond=0)
            label = lambda t: str(t.day)
            title = start.strftime("%m-%Y")
        else:  # year
            start = day0.replace(year=day0.year - offset, month=1, day=1)
            end = start.replace(year=start.year + 1)
            steps = [start.replace(month=i) for i in range(1, 13)]
            key = lambda ts: datetime.fromtimestamp(ts, self.tz).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            label = lambda t: ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"][t.month - 1]
            title = str(start.year)
        rows = self.ledger.house_hours(int(start.timestamp()), int(end.timestamp()))
        buckets = {t: {"label": label(t), "ts": int(t.timestamp()), "import_kwh": 0.0, "export_kwh": 0.0,
                       "cost": 0.0, "revenue": 0.0, "dev_kwh": 0.0} for t in steps}
        for r in rows:
            b = buckets.get(key(r["ts"]))
            if b:
                for k in ("import_kwh", "export_kwh", "cost", "revenue", "dev_kwh"):
                    b[k] += r[k]
        out = [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in b.items()} for b in buckets.values()]
        tot = {k: round(sum(b[k] for b in out), 3) for k in ("import_kwh", "export_kwh", "cost", "revenue", "dev_kwh")}
        tot["net"] = round(tot["cost"] - tot["revenue"], 2)
        c = self.cfg.contract
        from .report import tax_credit_for
        days = (end - start).total_seconds() / 86400
        credit = c.tax_credit_year if c.tax_credit_year is not None else tax_credit_for(start.year)
        fixed = (12 * (c.fixed_monthly + c.grid_monthly) - credit) * days / 365.0
        tot["fixed"] = round(fixed, 2)
        tot["fixed_known"] = bool(c.fixed_monthly or c.grid_monthly)
        tot["bill"] = round(tot["net"] + fixed, 2) if tot["fixed_known"] else None
        tot["avg_paid"] = round(tot["cost"] / tot["import_kwh"], 4) if tot["import_kwh"] > 0.05 else None
        return {"period": period, "offset": offset, "title": title, "buckets": out, "totals": tot,
                "has_data": bool(rows), "has_meter": self.cfg.has_meter}

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
        now = datetime.now(self.tz)
        with self.lock:
            self.controller.set_mode(device_id, mode, now, hours)
            self._save_state()
            if mode != "auto":
                self.ledger.inc(now.date().isoformat(), "override")
                self.ledger.inc(now.date().isoformat(), f"override:{device_id}")


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
            if netsetup.portal_active() and url.path not in ("/wifi", "/api/wifi") and url.path not in STATIC:
                self.send_response(302)                     # instel-wifi: elke pagina (ook de telefoontest) naar de wifi-keuze
                self.send_header("Location", f"http://{netsetup.HOTSPOT_IP}/wifi")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if url.path == "/wifi":
                return self._send(200, (WEB_DIR / "wifi.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/wifi":
                return self._json(200, netsetup.portal_view())
            if url.path in PAGES:
                page = PAGES[url.path]
                if page == "index.html" and not engine.cfg.configured:
                    self.send_response(302)
                    self.send_header("Location", "setup")
                    self.end_headers()
                    return
                if page in ("index.html", "rapport.html", "kiosk.html"):
                    try:
                        engine.ledger.inc(datetime.now(engine.tz).date().isoformat(), "open_" + page.split(".")[0])
                    except Exception:
                        pass
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
            if url.path == "/api/simulate":
                return self._json(200, _simulate(engine.cfg, q))
            if url.path == "/api/license":
                return self._json(200, engine.license_state(force=True).to_dict())
            if url.path == "/api/ocpp":
                return self._json(200, engine.ocpp_view())
            if url.path == "/api/report":
                return self._json(200, engine.report_view(max(0, min(52, int(q.get("offset", ["0"])[0] or 0)))))
            if url.path == "/api/moment":
                return self._json(200, engine.moment_view())
            if url.path == "/api/compare":
                return self._json(200, engine.compare_view())
            if url.path == "/api/howcalc":
                return self._json(200, engine.howcalc_view())
            if url.path == "/api/export.csv":
                body = engine.export_csv(min(400, max(1, int(q.get("days", ["60"])[0] or 60)))).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="zonnestuur-meetdata.csv"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if url.path == "/api/events":
                return self._json(200, engine.events_view(min(14, max(1, int(q.get("days", ["2"])[0])))))
            if url.path == "/api/coach":
                return self._json(200, engine.coach_view())
            if url.path == "/api/learned":
                with engine.lock:
                    return self._json(200, engine.learned_view())
            if url.path == "/api/insight":
                return self._json(200, engine.insight_view())
            if url.path == "/api/prices":
                return self._json(200, engine.prices_view())
            if url.path == "/api/usage":
                period = q.get("period", ["day"])[0]
                if period not in ("day", "week", "month", "year"):
                    period = "day"
                try:
                    offset = max(0, min(400, int(q.get("offset", ["0"])[0])))
                except ValueError:
                    offset = 0
                return self._json(200, engine.usage_view(period, offset))
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
            if url.path == "/api/wifi":                     # alleen via de instel-wifi, dus wie bij de Box is
                if not netsetup.portal_active():
                    return self._json(409, {"error": "De Box heeft al netwerk"})
                try:
                    body = self._body()
                    netsetup.submit(str(body.get("ssid", "")), str(body.get("password", "")))
                except (ValueError, json.JSONDecodeError) as exc:
                    return self._json(400, {"error": str(exc)})
                return self._json(200, {"ok": True})
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
            if url.path == "/api/license":
                return self._json(200, engine.set_license(str(body.get("key", ""))))
            if url.path == "/api/setup/mqtt":
                conf = dict(body)
                if not conf.get("password") and (engine.cfg.mqtt or {}).get("host") == conf.get("host"):
                    conf["password"] = (engine.cfg.mqtt or {}).get("password", "")
                return self._json(200, engine.mqtt_test(conf))
            if url.path == "/api/setup/homey":
                tok = str(body.get("token", "")) or (engine.cfg.homey or {}).get("token", "")
                return self._json(200, engine.homey_test(str(body.get("url", "")), tok))
            if url.path == "/api/notify/test":
                return self._json(200, engine.notify_test(body.get("notify") or body))
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
            if url.path == "/api/history/backfill":
                return self._json(200, engine.backfill_history(int(body.get("days", 365))))
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


_SIM_CACHE: dict = {}


def _simulate(cfg: Config, q: dict) -> dict:
    """Jaarberekening met echte uurprijzen en zoninstraling. Data wordt per periode en plek bewaard."""
    from dataclasses import replace
    from .yearsim import build_days, household_from_query, run_all
    start, end = q.get("start", [""])[0], q.get("end", [""])[0]
    s = cfg.solar
    loc = {k: float(q.get(k, [v])[0]) for k, v in (("lat", s.latitude), ("lon", s.longitude), ("tilt", s.tilt),
                                                     ("azimuth", s.azimuth))}
    key = (start, end, tuple(sorted(loc.items())))
    if key not in _SIM_CACHE:
        prices = _price_history(start, end)
        if not prices.get("ok"):
            return prices
        solar_cfg = replace(cfg, solar=replace(s, latitude=loc["lat"], longitude=loc["lon"], tilt=loc["tilt"],
                                               azimuth=loc["azimuth"]))
        solar = _solar_history(solar_cfg, start, end)
        _SIM_CACHE.clear()
        _SIM_CACHE[key] = (prices, solar)
    prices, solar = _SIM_CACHE[key]
    hh = household_from_query(q, cfg)
    days = build_days(hh, prices["hours"], solar if solar.get("ok") else None)
    out = run_all(hh, days)
    out.update({"ok": True, "solar_ok": bool(solar.get("ok")), "solar_error": solar.get("error"),
                "household": hh.__dict__, "location": loc,
                "avg_price": round(sum(p for d in days for p in d.price) / max(1, 24 * len(days)), 4)})
    return out


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

    def _auto_backfill():
        # Eenmalig: historie uit Home Assistant, zodat het verbruiksoverzicht en advies meteen gevuld zijn
        if (engine.cfg.meter or {}).get("driver") == "ha" and not engine.ledger.load_state("backfill"):
            time.sleep(20)
            res = engine.backfill_history()
            if not res.get("ok"):
                log.info("Historie niet opgehaald: %s", res.get("error"))
    threading.Thread(target=_auto_backfill, daemon=True).start()

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
