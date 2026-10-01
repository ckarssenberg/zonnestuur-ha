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
CREATE TABLE IF NOT EXISTS battery_day (
    day TEXT NOT NULL, battery_id TEXT NOT NULL,
    charged_kwh REAL NOT NULL DEFAULT 0, grid_kwh REAL NOT NULL DEFAULT 0,
    discharged_kwh REAL NOT NULL DEFAULT 0, eur REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, battery_id));
CREATE TABLE IF NOT EXISTS event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL, end_ts INTEGER, device TEXT NOT NULL, name TEXT NOT NULL, action TEXT NOT NULL,
    code TEXT NOT NULL, text TEXT NOT NULL, inputs TEXT NOT NULL DEFAULT '{}',
    kwh REAL, kwh_solar REAL, eur REAL);
CREATE INDEX IF NOT EXISTS event_ts ON event(ts);
CREATE TABLE IF NOT EXISTS daystat (
    day TEXT NOT NULL, key TEXT NOT NULL, val REAL NOT NULL DEFAULT 0, PRIMARY KEY (day, key));
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
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(house_hour)")}
        for col, ddl in (("batt_kwh", "REAL NOT NULL DEFAULT 0"),    # netto geladen door de thuisbatterij
                         ("pv_kwh", "REAL NOT NULL DEFAULT 0"),      # zonne-opwek (gemeten of geschat)
                         ("pv_src", "INTEGER NOT NULL DEFAULT 0"),   # 2 gemeten, 1 geschat, 0 onbekend
                         ("fc_kwh", "REAL")):                         # voorspelde opwek (voor het bijstellen)
            if col not in cols:
                self.conn.execute(f"ALTER TABLE house_hour ADD COLUMN {col} {ddl}")
        self.conn.commit()
        self._batt: dict[tuple[str, str], list[float]] = {}
        self.lock = threading.Lock()
        self._pending: dict[tuple[str, str], list[float]] = {}
        self._bucket: Optional[list] = None   # [ts, n, som grid, som apparaten, som zon]
        self._hour: Optional[list] = None     # [ts, import, export, kosten, opbrengst, prijs×kWh, apparaten]

    # ---- vastleggen -----------------------------------------------------
    def record(self, now: datetime, dt: float, grid_w: Optional[float],
               device_powers: dict[str, float], value_per_kwh: float, all_counts: bool = False,
               grid_value: float = 0.0) -> dict[str, tuple]:
        """all_counts=True: geen zonnepanelen; al het verbruik telt als 'slim ingepland' en value_per_kwh is
        het verschil met de gemiddelde prijs van de dag.

        Besparing per apparaat = zonnestroom × (prijs zonder Zonnestuur − gemiste terugleververgoeding)
                               + netstroom × (prijs zonder Zonnestuur − prijs nu)          (grid_value)
        Geeft per apparaat (kWh, kWh zon, €) van deze ronde terug, voor het logboek."""
        out: dict[str, tuple] = {}
        if dt <= 0 or dt > 600:
            return out  # gat in de data (bijv. na herstart): niet meetellen
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
            eur = kwh_solar * value_per_kwh + (0.0 if all_counts else (kwh - kwh_solar) * grid_value)
            acc[0] += kwh
            acc[1] += kwh_solar
            acc[2] += eur
            out[dev] = (kwh, kwh_solar, eur)
        return out

    def record_house(self, now: datetime, dt: float, grid_w: float, import_price: float, feed_price: float,
                     dev_w: float = 0.0, batt_w: float = 0.0, pv_w: Optional[float] = None,
                     pv_src: int = 0, fc_w: Optional[float] = None) -> None:
        """Hele huis per uur: afname, teruglevering en wat dat kostte/opleverde (voor het verbruiksoverzicht)."""
        if dt <= 0 or dt > 600:
            return
        ts = int(now.timestamp()) // 3600 * 3600
        if self._hour is not None and self._hour[0] != ts:
            self._write_hour()
        if self._hour is None:
            self._hour = [ts, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, None]
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
        h[7] += batt_w * dt / 3_600_000
        if pv_w is not None and pv_src:
            h[8] += max(0.0, pv_w) * dt / 3_600_000
            h[9] = max(h[9], pv_src)
        if fc_w is not None:
            h[10] = (h[10] or 0.0) + max(0.0, fc_w) * dt / 3_600_000

    def record_battery(self, now: datetime, dt: float, battery_id: str, power_w: float, grid_w: Optional[float],
                       import_price: float, feed_price: float) -> None:
        """Per dag: geladen (waarvan van het net), ontladen, en wat de batterij opleverde t.o.v. geen batterij."""
        if dt <= 0 or dt > 600:
            return
        kwh = power_w * dt / 3_600_000
        rec = self._batt.setdefault((now.date().isoformat(), battery_id), [0.0, 0.0, 0.0, 0.0])
        if kwh > 0:
            from_grid = min(kwh, max(0.0, (grid_w or 0.0) * dt / 3_600_000))
            rec[0] += kwh
            rec[1] += from_grid
            rec[3] -= from_grid * import_price + (kwh - from_grid) * feed_price
        else:
            rec[2] += -kwh
            rec[3] += -kwh * import_price

    def battery_totals(self, since: date) -> dict[str, dict]:
        self.flush()
        with self.lock:
            rows = self.conn.execute(
                "SELECT battery_id, SUM(charged_kwh), SUM(grid_kwh), SUM(discharged_kwh), SUM(eur) FROM battery_day "
                "WHERE day >= ? GROUP BY battery_id", (since.isoformat(),)).fetchall()
        return {r[0]: {"charged_kwh": round(r[1], 2), "grid_kwh": round(r[2], 2), "discharged_kwh": round(r[3], 2),
                       "eur": round(r[4], 2)} for r in rows}

    def _write_hour(self, keep: bool = False) -> None:
        ts, imp, exp, cost, rev, mx, dev, batt, pv, src, fc = self._hour
        self._hour = [ts, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, src, None] if keep else None
        with self.lock:
            self.conn.execute(
                "INSERT INTO house_hour(ts, import_kwh, export_kwh, cost_eur, revenue_eur, market_x_kwh, dev_kwh, batt_kwh, pv_kwh, pv_src, fc_kwh) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ts) DO UPDATE SET import_kwh=import_kwh+excluded.import_kwh, "
                "export_kwh=export_kwh+excluded.export_kwh, cost_eur=cost_eur+excluded.cost_eur, "
                "revenue_eur=revenue_eur+excluded.revenue_eur, market_x_kwh=market_x_kwh+excluded.market_x_kwh, "
                "dev_kwh=dev_kwh+excluded.dev_kwh, batt_kwh=batt_kwh+excluded.batt_kwh, pv_kwh=pv_kwh+excluded.pv_kwh, "
                "pv_src=MAX(pv_src, excluded.pv_src), fc_kwh=CASE WHEN excluded.fc_kwh IS NULL THEN fc_kwh "
                "ELSE COALESCE(fc_kwh, 0) + excluded.fc_kwh END", (ts, imp, exp, cost, rev, mx, dev, batt, pv, src, fc))
            self.conn.execute("DELETE FROM house_hour WHERE ts < ?", (ts - 800 * 86400,))
            self.conn.commit()

    def insert_house_hours(self, rows: list[dict]) -> int:
        """Historie invoegen (uit Home Assistant); bestaande uren blijven staan."""
        with self.lock:
            cur = self.conn.executemany(
                "INSERT OR IGNORE INTO house_hour(ts, import_kwh, export_kwh, cost_eur, revenue_eur, market_x_kwh, dev_kwh, pv_kwh, pv_src) "
                "VALUES (?,?,?,?,?,0,0,?,?)", [(r["ts"], r["import_kwh"], r["export_kwh"], r["cost"], r["revenue"],
                                               r.get("pv_kwh", 0.0), r.get("pv_src", 0)) for r in rows])
            self.conn.commit()
            return cur.rowcount

    def house_hours_count(self) -> int:
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM house_hour").fetchone()[0]

    def house_hours(self, start_ts: int, end_ts: int) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT ts, import_kwh, export_kwh, cost_eur, revenue_eur, dev_kwh, batt_kwh, pv_kwh, pv_src, fc_kwh FROM house_hour "
                "WHERE ts >= ? AND ts < ? ORDER BY ts", (start_ts, end_ts)).fetchall()
        out = [{"ts": r[0], "import_kwh": r[1], "export_kwh": r[2], "cost": r[3], "revenue": r[4], "dev_kwh": r[5],
                "batt_kwh": r[6], "pv_kwh": r[7], "pv_src": r[8], "fc_kwh": r[9]} for r in rows]
        h = self._hour
        if h is not None and start_ts <= h[0] < end_ts:
            prev = next((o for o in out if o["ts"] == h[0]), None)
            add = {"ts": h[0], "import_kwh": h[1], "export_kwh": h[2], "cost": h[3], "revenue": h[4], "dev_kwh": h[6],
                   "batt_kwh": h[7], "pv_kwh": h[8], "pv_src": h[9], "fc_kwh": h[10]}
            if prev:
                for k in ("import_kwh", "export_kwh", "cost", "revenue", "dev_kwh", "batt_kwh", "pv_kwh"):
                    prev[k] += add[k]
                prev["pv_src"] = max(prev["pv_src"] or 0, add["pv_src"] or 0)
                if add["fc_kwh"] is not None:
                    prev["fc_kwh"] = (prev["fc_kwh"] or 0.0) + add["fc_kwh"]
            else:
                out.append(add)
        return out

    def device_daily(self, since: date) -> dict[str, list[float]]:
        """kWh per dag per gestuurd apparaat, oud naar nieuw (dagen zonder verbruik tellen als 0)."""
        self.flush()
        with self.lock:
            rows = self.conn.execute("SELECT day, device_id, kwh FROM device_day WHERE day >= ? ORDER BY day",
                                     (since.isoformat(),)).fetchall()
        days = sorted({r[0] for r in rows})
        out: dict[str, dict] = {}
        for day, dev, kwh in rows:
            out.setdefault(dev, {})[day] = kwh
        return {dev: [v.get(d, 0.0) for d in days] for dev, v in out.items()}

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
        if self._batt:
            with self.lock:
                for (day, bid), (ch, gr, dis, eur) in self._batt.items():
                    self.conn.execute(
                        "INSERT INTO battery_day(day, battery_id, charged_kwh, grid_kwh, discharged_kwh, eur) VALUES (?,?,?,?,?,?) "
                        "ON CONFLICT(day, battery_id) DO UPDATE SET charged_kwh=charged_kwh+excluded.charged_kwh, "
                        "grid_kwh=grid_kwh+excluded.grid_kwh, discharged_kwh=discharged_kwh+excluded.discharged_kwh, "
                        "eur=eur+excluded.eur", (day, bid, ch, gr, dis, eur))
                self.conn.commit()
                self._batt.clear()
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

    # ---- logboek: waarom schakelde Zonnestuur? ----------------------------
    def add_event(self, ts: int, device: str, name: str, action: str, code: str, text: str, inputs: dict) -> int:
        with self.lock:
            cur = self.conn.execute("INSERT INTO event(ts, device, name, action, code, text, inputs) VALUES (?,?,?,?,?,?,?)",
                                    (ts, device, name, action, code, text, json.dumps(inputs)))
            self.conn.commit()
            return int(cur.lastrowid)

    def close_event(self, event_id: int, end_ts: int, kwh: float, kwh_solar: float, eur: float) -> None:
        with self.lock:
            self.conn.execute("UPDATE event SET end_ts=?, kwh=?, kwh_solar=?, eur=? WHERE id=?",
                              (end_ts, kwh, kwh_solar, eur, event_id))
            self.conn.commit()

    def events(self, since_ts: int, limit: int = 200, device: str = "") -> list[dict]:
        q = "SELECT id, ts, end_ts, device, name, action, code, text, inputs, kwh, kwh_solar, eur FROM event WHERE ts >= ?"
        args: list = [since_ts]
        if device:
            q += " AND device = ?"
            args.append(device)
        with self.lock:
            rows = self.conn.execute(q + " ORDER BY ts DESC, id DESC LIMIT ?", (*args, limit)).fetchall()
        return [{"id": r[0], "ts": r[1], "end_ts": r[2], "device": r[3], "name": r[4], "action": r[5], "code": r[6],
                 "text": r[7], "inputs": json.loads(r[8] or "{}"), "kwh": _r(r[9]), "kwh_solar": _r(r[10]),
                 "eur": None if r[11] is None else round(r[11], 2)} for r in rows]

    # ---- dagtellers (meetplan proef) ---------------------------------------
    def inc(self, day: str, key: str, val: float = 1.0) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO daystat(day, key, val) VALUES (?,?,?) ON CONFLICT(day, key) "
                              "DO UPDATE SET val=val+excluded.val", (day, key, val))
            self.conn.commit()

    def set_stat(self, day: str, key: str, val: float) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO daystat(day, key, val) VALUES (?,?,?) ON CONFLICT(day, key) "
                              "DO UPDATE SET val=excluded.val", (day, key, val))
            self.conn.commit()

    def stats(self, since: str) -> dict[str, dict[str, float]]:
        with self.lock:
            rows = self.conn.execute("SELECT day, key, val FROM daystat WHERE day >= ?", (since,)).fetchall()
        out: dict[str, dict[str, float]] = {}
        for d, k, v in rows:
            out.setdefault(d, {})[k] = v
        return out

    def device_days(self, since: date) -> list[dict]:
        self.flush()
        with self.lock:
            rows = self.conn.execute("SELECT day, device_id, kwh, kwh_solar, eur_saved FROM device_day WHERE day >= ? "
                                     "ORDER BY day", (since.isoformat(),)).fetchall()
        return [{"day": r[0], "device": r[1], "kwh": r[2], "kwh_solar": r[3], "eur": r[4]} for r in rows]

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
