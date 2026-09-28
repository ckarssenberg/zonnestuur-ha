"""Boekhouding: hoeveel stroom elk apparaat gebruikte, hoeveel daarvan eigen zonnestroom was,
en wat dat opleverde. Opgeslagen in SQLite, per dag.

Toerekening: de zonnestroom gaat eerst naar het gewone verbruik van het huis.
Wat daarna overblijft, rekenen we toe aan de gestuurde apparaten (naar rato van
hun vermogen). Zo schatten we de besparing eerder te laag dan te hoog.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS device_day (
    day TEXT NOT NULL, device_id TEXT NOT NULL,
    kwh REAL NOT NULL DEFAULT 0, kwh_solar REAL NOT NULL DEFAULT 0, eur_saved REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, device_id));
CREATE TABLE IF NOT EXISTS house_day (
    day TEXT PRIMARY KEY,
    import_start REAL, export_start REAL, import_last REAL, export_last REAL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS house_hour (
    ts INTEGER PRIMARY KEY,            -- begin van het uur (unix-tijd)
    import_kwh REAL NOT NULL DEFAULT 0, export_kwh REAL NOT NULL DEFAULT 0,
    cost_eur REAL NOT NULL DEFAULT 0, revenue_eur REAL NOT NULL DEFAULT 0,
    market_x_kwh REAL NOT NULL DEFAULT 0, dev_kwh REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sample (
    ts INTEGER PRIMARY KEY,            -- begin van het 5-minutenblok (unix-tijd)
    grid_w REAL, dev_w REAL, dev_solar_w REAL);
"""
BUCKET_S = 300


def solar_share(grid_w: float, device_powers: dict[str, float]) -> dict[str, float]:
    """Welk deel (W) van het vermogen van elk apparaat uit eigen zonnestroom komt."""
    total = sum(max(0.0, p) for p in device_powers.values())
    if total <= 0:
        return {k: 0.0 for k in device_powers}
    solar_total = min(total, max(0.0, total - grid_w))
    return {k: solar_total * max(0.0, p) / total for k, p in device_powers.items()}


class Ledger:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()
        self._pending: dict[tuple[str, str], list[float]] = {}
        self._bucket: Optional[list] = None   # [ts, n, som grid, som apparaten, som zon]
        self._hour: Optional[list] = None     # [ts, import, export, kosten, opbrengst, prijs×kWh, apparaten]

    # ---- vastleggen -----------------------------------------------------
    def record(self, now: datetime, dt: float, grid_w: Optional[float],
               device_powers: dict[str, float], value_per_kwh: float, all_counts: bool = False) -> None:
        """all_counts=True: geen zonnepanelen; al het verbruik telt als 'slim ingepland' en value_per_kwh is
        het verschil met de gemiddelde prijs van de dag."""
        if dt <= 0 or dt > 600:
            return  # gat in de data (bijv. na herstart): niet meetellen
        day = now.date().isoformat()
        if all_counts:
            shares = {k: max(0.0, p) for k, p in device_powers.items()}
        else:
            shares = solar_share(grid_w, device_powers) if grid_w is not None else {k: 0.0 for k in device_powers}
        self._add_sample(now, grid_w, sum(max(0.0, p) for p in device_powers.values()), sum(shares.values()))
        for dev, p in device_powers.items():
            kwh = max(0.0, p) * dt / 3_600_000
            kwh_solar = shares.get(dev, 0.0) * dt / 3_600_000
            acc = self._pending.setdefault((day, dev), [0.0, 0.0, 0.0])
            acc[0] += kwh
            acc[1] += kwh_solar
            acc[2] += kwh_solar * value_per_kwh

    def record_house(self, now: datetime, dt: float, grid_w: float, import_price: float, feed_price: float,
                     dev_w: float = 0.0) -> None:
        """Hele huis per uur: afname, teruglevering en wat dat kostte/opleverde (voor het verbruiksoverzicht)."""
        if dt <= 0 or dt > 600:
            return
        ts = int(now.timestamp()) // 3600 * 3600
        if self._hour is not None and self._hour[0] != ts:
            self._write_hour()
        if self._hour is None:
            self._hour = [ts, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        kwh = grid_w * dt / 3_600_000
        h = self._hour
        if kwh >= 0:
            h[1] += kwh
            h[3] += kwh * import_price
            h[5] += kwh * import_price
        else:
            h[2] += -kwh
            h[4] += -kwh * feed_price
        h[6] += max(0.0, dev_w) * dt / 3_600_000

    def _write_hour(self, keep: bool = False) -> None:
        ts, imp, exp, cost, rev, mx, dev = self._hour
        self._hour = [ts, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] if keep else None
        with self.lock:
            self.conn.execute(
                "INSERT INTO house_hour(ts, import_kwh, export_kwh, cost_eur, revenue_eur, market_x_kwh, dev_kwh) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(ts) DO UPDATE SET import_kwh=import_kwh+excluded.import_kwh, "
                "export_kwh=export_kwh+excluded.export_kwh, cost_eur=cost_eur+excluded.cost_eur, "
                "revenue_eur=revenue_eur+excluded.revenue_eur, market_x_kwh=market_x_kwh+excluded.market_x_kwh, "
                "dev_kwh=dev_kwh+excluded.dev_kwh", (ts, imp, exp, cost, rev, mx, dev))
            self.conn.execute("DELETE FROM house_hour WHERE ts < ?", (ts - 800 * 86400,))
            self.conn.commit()

    def house_hours(self, start_ts: int, end_ts: int) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT ts, import_kwh, export_kwh, cost_eur, revenue_eur, dev_kwh FROM house_hour "
                "WHERE ts >= ? AND ts < ? ORDER BY ts", (start_ts, end_ts)).fetchall()
        out = [{"ts": r[0], "import_kwh": r[1], "export_kwh": r[2], "cost": r[3], "revenue": r[4], "dev_kwh": r[5]}
               for r in rows]
        h = self._hour
        if h is not None and start_ts <= h[0] < end_ts:
            prev = next((o for o in out if o["ts"] == h[0]), None)
            add = {"ts": h[0], "import_kwh": h[1], "export_kwh": h[2], "cost": h[3], "revenue": h[4], "dev_kwh": h[6]}
            if prev:
                for k in ("import_kwh", "export_kwh", "cost", "revenue", "dev_kwh"):
                    prev[k] += add[k]
            else:
                out.append(add)
        return out

    def _add_sample(self, now: datetime, grid_w: Optional[float], dev_w: float, solar_w: float) -> None:
        ts = int(now.timestamp()) // BUCKET_S * BUCKET_S
        if self._bucket is not None and self._bucket[0] != ts:
            self._write_bucket()
        if self._bucket is None:
            self._bucket = [ts, 0, 0.0, 0, 0.0, 0.0]
        b = self._bucket
        if grid_w is not None:
            b[2] += grid_w
            b[3] += 1
        b[1] += 1
        b[4] += dev_w
        b[5] += solar_w

    def _write_bucket(self) -> None:
        ts, n, g, ng, d, sw = self._bucket
        self._bucket = None
        if n == 0:
            return
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO sample(ts, grid_w, dev_w, dev_solar_w) VALUES (?,?,?,?)",
                              (ts, g / ng if ng else None, d / n, sw / n))
            self.conn.execute("DELETE FROM sample WHERE ts < ?", (ts - 3 * 86400,))
            self.conn.commit()

    def samples(self, since_ts: int) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT ts, grid_w, dev_w, dev_solar_w FROM sample WHERE ts >= ? ORDER BY ts",
                                     (since_ts,)).fetchall()
        out = [{"ts": r[0], "grid_w": _r1(r[1]), "dev_w": _r1(r[2]), "dev_solar_w": _r1(r[3])} for r in rows]
        if self._bucket is not None and self._bucket[0] >= since_ts:
            ts, n, g, ng, d, sw = self._bucket
            if n:
                out.append({"ts": ts, "grid_w": _r1(g / ng) if ng else None, "dev_w": _r1(d / n), "dev_solar_w": _r1(sw / n)})
        return out

    def record_meter(self, now: datetime, import_kwh: Optional[float], export_kwh: Optional[float]) -> None:
        if import_kwh is None or export_kwh is None:
            return
        day = now.date().isoformat()
        with self.lock:
            self.conn.execute(
                "INSERT INTO house_day(day, import_start, export_start, import_last, export_last) VALUES (?,?,?,?,?) "
                "ON CONFLICT(day) DO UPDATE SET import_last=excluded.import_last, export_last=excluded.export_last",
                (day, import_kwh, export_kwh, import_kwh, export_kwh))
            self.conn.commit()

    def flush(self) -> None:
        if self._hour is not None:
            self._write_hour(keep=True)           # lopend uur alvast bewaren (bijv. bij herstart)
        if not self._pending:
            return
        with self.lock:
            for (day, dev), (kwh, kwh_solar, eur) in self._pending.items():
                self.conn.execute(
                    "INSERT INTO device_day(day, device_id, kwh, kwh_solar, eur_saved) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(day, device_id) DO UPDATE SET kwh=kwh+excluded.kwh, "
                    "kwh_solar=kwh_solar+excluded.kwh_solar, eur_saved=eur_saved+excluded.eur_saved",
                    (day, dev, kwh, kwh_solar, eur))
            self.conn.commit()
            self._pending.clear()

    # ---- opvragen -------------------------------------------------------
    def days(self, since: date) -> list[dict]:
        self.flush()
        with self.lock:
            rows = self.conn.execute(
                "SELECT d.day, SUM(d.kwh), SUM(d.kwh_solar), SUM(d.eur_saved), "
                "h.import_last - h.import_start, h.export_last - h.export_start "
                "FROM device_day d LEFT JOIN house_day h ON h.day = d.day "
                "WHERE d.day >= ? GROUP BY d.day ORDER BY d.day", (since.isoformat(),)).fetchall()
        return [{"day": r[0], "kwh": round(r[1], 3), "kwh_solar": round(r[2], 3), "eur_saved": round(r[3], 2),
                 "house_import_kwh": _r(r[4]), "house_export_kwh": _r(r[5])} for r in rows]

    def totals(self, since: date) -> dict:
        self.flush()
        with self.lock:
            r = self.conn.execute(
                "SELECT COALESCE(SUM(kwh),0), COALESCE(SUM(kwh_solar),0), COALESCE(SUM(eur_saved),0) "
                "FROM device_day WHERE day >= ?", (since.isoformat(),)).fetchone()
        return {"kwh": round(r[0], 2), "kwh_solar": round(r[1], 2), "eur_saved": round(r[2], 2),
                "solar_pct": round(100 * r[1] / r[0], 1) if r[0] else 0.0}

    def per_device(self, since: date) -> dict[str, dict]:
        self.flush()
        with self.lock:
            rows = self.conn.execute(
                "SELECT device_id, SUM(kwh), SUM(kwh_solar), SUM(eur_saved) FROM device_day "
                "WHERE day >= ? GROUP BY device_id", (since.isoformat(),)).fetchall()
        return {r[0]: {"kwh": round(r[1], 2), "kwh_solar": round(r[2], 2), "eur_saved": round(r[3], 2)} for r in rows}

    # ---- toestand bewaren over herstarts ---------------------------------
    def save_state(self, key: str, value: dict) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO kv(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                              (key, json.dumps(value)))
            self.conn.commit()

    def load_state(self, key: str) -> Optional[dict]:
        with self.lock:
            r = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else None


def _r1(v):
    return None if v is None else round(v, 1)


def _r(v):
    return None if v is None else round(v, 3)
