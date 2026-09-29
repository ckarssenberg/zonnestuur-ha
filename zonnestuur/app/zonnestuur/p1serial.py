"""Slimme meter rechtstreeks via de P1-kabel (USB) in het kastje: geen HomeWizard of andere dongel nodig.

Nederlandse slimme meters (DSMR 4 en 5) sturen elke 1–10 seconden een 'telegram' met o.a.:
    1-0:1.7.0(00.350*kW)   vermogen dat je nu afneemt
    1-0:2.7.0(01.200*kW)   vermogen dat je nu teruglevert
    1-0:1.8.1 / 1.8.2      meterstand afname (tarief 1 en 2, kWh)
    1-0:2.8.1 / 2.8.2      meterstand teruglevering

Instelling DSMR 4/5: 115200 baud, 8N1. Oudere meters (DSMR 2.2/3): 9600 baud, 7E1.
De P1-kabel met FTDI-chip keert het signaal zelf om. Alleen standaardbibliotheek (termios).
"""
from __future__ import annotations

import glob
import os
import re
import threading
import time
from typing import Optional

from .adapters import DeviceError, MeterReading

_OBIS = re.compile(r"^(\d-\d:\d+\.\d+\.\d+)\(([-\d.]+)\*?(\w*)\)")


def parse_telegram(text: str) -> dict:
    vals = {}
    for line in text.splitlines():
        m = _OBIS.match(line.strip())
        if m:
            try:
                vals[m.group(1)] = float(m.group(2))
            except ValueError:
                pass
    return vals


def reading_from(vals: dict) -> Optional[MeterReading]:
    if "1-0:1.7.0" not in vals:
        return None
    grid = (vals.get("1-0:1.7.0", 0.0) - vals.get("1-0:2.7.0", 0.0)) * 1000
    imp = vals.get("1-0:1.8.1", 0.0) + vals.get("1-0:1.8.2", 0.0)
    exp = vals.get("1-0:2.8.1", 0.0) + vals.get("1-0:2.8.2", 0.0)
    return MeterReading(grid, imp or None, exp or None)


def serial_ports() -> list[str]:
    return sorted(set(glob.glob("/dev/serial/by-id/*") + glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")))


def _configure(fd: int, baud: int, seven_even: bool) -> None:
    import termios
    attrs = termios.tcgetattr(fd)
    speed = {115200: termios.B115200, 9600: termios.B9600}[baud]
    attrs[0] = 0                                                 # iflag
    attrs[1] = 0                                                 # oflag
    cflag = termios.CREAD | termios.CLOCAL
    cflag |= (termios.CS7 | termios.PARENB) if seven_even else termios.CS8
    attrs[2] = cflag
    attrs[3] = 0                                                 # lflag: ruw
    attrs[4] = attrs[5] = speed
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 10
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


class P1SerialMeter:
    """Leest de P1-poort op de achtergrond; read() geeft het laatste telegram."""

    def __init__(self, port: str, baud: int = 115200, invert: bool = False, configure: bool = True):
        self.port, self.baud, self.sign, self.configure = port, int(baud or 115200), -1.0 if invert else 1.0, configure
        self.last: Optional[MeterReading] = None
        self.last_at = 0.0
        self.error = ""
        self._stop = False
        threading.Thread(target=self._run, daemon=True, name="p1").start()

    def _run(self) -> None:
        while not self._stop:
            try:
                fd = os.open(self.port, os.O_RDONLY | os.O_NOCTTY)
            except OSError as exc:
                self.error = f"P1-kabel niet gevonden op {self.port} ({exc.strerror})"
                time.sleep(5)
                continue
            try:
                if self.configure:
                    try:
                        _configure(fd, self.baud, self.baud == 9600)
                    except Exception:
                        pass                                        # geen echte seriële poort (bijv. test): gewoon lezen
                buf = ""
                while not self._stop:
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        time.sleep(0.2)
                        continue
                    buf += chunk.decode("ascii", errors="replace")
                    while "!" in buf and "/" in buf:
                        start = buf.index("/")
                        end = buf.find("!", start)
                        if end < 0:
                            break
                        r = reading_from(parse_telegram(buf[start:end]))
                        if r:
                            self.last, self.last_at, self.error = r, time.time(), ""
                        buf = buf[end + 1:]
                    buf = buf[-8192:]
            except OSError as exc:
                self.error = f"P1-kabel: {exc.strerror or exc}"
                time.sleep(2)
            finally:
                os.close(fd)

    def read(self) -> MeterReading:
        if self.last is None or time.time() - self.last_at > 60:
            raise DeviceError(self.error or "nog geen telegram van de slimme meter (zit de kabel in de P1-poort?)")
        r = self.last
        return MeterReading(self.sign * r.grid_w, r.import_kwh, r.export_kwh)

    def close(self) -> None:
        self._stop = True


_METERS: dict[str, P1SerialMeter] = {}


def meter_for(port: str, baud: int = 115200, invert: bool = False) -> P1SerialMeter:
    m = _METERS.get(port)
    if m is None or m.baud != int(baud or 115200):
        m = _METERS[port] = P1SerialMeter(port, baud)
    m.sign = -1.0 if invert else 1.0
    return m


def probe(timeout: float = 12.0) -> list[dict]:
    """Welke P1-kabels zijn aangesloten, en geven ze al een meting?"""
    out = []
    for port in serial_ports():
        m = meter_for(port)
        end = time.time() + timeout
        while time.time() < end and m.last is None and not m.error.startswith("P1-kabel niet"):
            time.sleep(0.2)
        out.append({"port": port, "ok": m.last is not None, "grid_w": None if m.last is None else round(m.last.grid_w),
                    "error": "" if m.last else (m.error or "geen telegram ontvangen")})
    return out
