"""Koppelingen met de hardware: HomeWizard P1-meter en Shelly-schakelaars.

Beide apparaten hebben een lokale HTTP-API, dus er is geen cloud nodig.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional


class DeviceError(Exception):
    """Een apparaat reageert niet of geeft onverwachte data terug."""


class NotReady(DeviceError):
    """Het apparaat werkt, maar is (nog) niet klaargezet, bijv. een wasmachine zonder 'start op afstand'."""


def http_get_json(url: str, timeout: float = 4.0) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # netwerkfout, timeout, geen JSON
        raise DeviceError(f"{url}: {exc}") from exc


@dataclass
class MeterReading:
    grid_w: float                 # positief = afname van het net, negatief = teruglevering
    import_kwh: Optional[float]   # meterstand afname (totaal)
    export_kwh: Optional[float]   # meterstand teruglevering (totaal)


class HomeWizardP1:
    """HomeWizard P1-meter via de lokale API v1 (inschakelen in de HomeWizard-app)."""

    def __init__(self, host: str):
        self.base = host if host.startswith("http") else f"http://{host}"

    def read(self) -> MeterReading:
        data = http_get_json(f"{self.base}/api/v1/data")
        if "active_power_w" not in data:
            raise DeviceError("P1-meter gaf geen active_power_w terug")
        return MeterReading(
            grid_w=float(data["active_power_w"]),
            import_kwh=_opt_float(data.get("total_power_import_kwh")),
            export_kwh=_opt_float(data.get("total_power_export_kwh")),
        )


@dataclass
class SwitchStatus:
    on: bool
    power_w: float
    energy_wh: Optional[float]


class ShellySwitch:
    """Shelly Gen2/Gen3-schakelaar (bijv. 1PM Gen3, Plug S Gen3, Pro 1PM) via RPC over HTTP."""

    def __init__(self, host: str, switch_id: int = 0):
        self.base = host if host.startswith("http") else f"http://{host}"
        self.switch_id = switch_id

    def status(self) -> SwitchStatus:
        data = http_get_json(f"{self.base}/rpc/Switch.GetStatus?id={self.switch_id}")
        aenergy = data.get("aenergy") or {}
        return SwitchStatus(
            on=bool(data.get("output", False)),
            power_w=float(data.get("apower") or 0.0),
            energy_wh=_opt_float(aenergy.get("total")),
        )

    def set(self, on: bool) -> None:
        q = urllib.parse.urlencode({"id": self.switch_id, "on": "true" if on else "false"})
        http_get_json(f"{self.base}/rpc/Switch.Set?{q}")


def _opt_float(v) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
