"""Configuratie van Zonnestuur.

Alle instellingen staan in één JSON-bestand (zie config.example.json).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class DeviceConfig:
    """Eén apparaat dat Zonnestuur aan en uit zet via een Shelly-schakelaar."""

    id: str
    name: str
    host: str = ""                 # IP-adres of hostnaam (niet nodig bij Home Assistant)
    kind: str = "boiler"           # boiler | ev | heatpump | generic
    driver: str = "shelly"         # shelly | shelly_gen1 | homewizard_socket | tasmota | ha_switch | ha_setpoint | ha_current
    params: dict = field(default_factory=dict)   # extra gegevens voor de koppeling (bijv. Home Assistant-entiteiten)
    switch_id: int = 0             # kanaal op de Shelly (meestal 0)
    power_w: float = 2000.0        # opgenomen vermogen als het apparaat aan staat
    priority: int = 1              # 1 = eerst aan, laatst uit
    start_surplus_w: Optional[float] = None  # benodigd overschot om te starten (standaard 90% van power_w)
    start_delay_s: int = 60        # zo lang moet het overschot er zijn voor we starten
    stop_import_w: float = 300.0   # bij meer afname dan dit ...
    stop_delay_s: int = 120        # ... gedurende zo lang, zetten we het apparaat uit
    min_on_s: int = 600            # minimaal zo lang aan laten
    min_off_s: int = 300           # minimaal zo lang uit laten
    # Garantie: op deze tijden moet het apparaat 'vol' zijn (boiler warm, auto geladen).
    # Is het dat niet, dan mag het in de garantieminuten ervoor van het net bijladen.
    ready_times: list[str] = field(default_factory=list)   # bijv. ["06:30", "18:30"]
    ready_days: list[int] = field(default_factory=list)   # 0=ma … 6=zo; leeg = elke dag
    guarantee_min: int = 120       # zoveel minuten voor elke ready_time mag hij van het net bijladen
    full_lookback_h: float = 4.0   # 'vol' telt als dat binnen zoveel uur voor de ready_time gemeten is
    detect_full: bool = True       # herken 'vol': aan geschakeld maar neemt geen stroom meer op
    expected_run_min: int = 150    # zoveel minuten draait hij gemiddeld per dag (voor het kiezen van de beste zonuren)

    @property
    def one_shot(self) -> bool:
        """Alleen starten, nooit uitzetten: witgoed dat een programma afmaakt (wasmachine, droger, vaatwasser)."""
        return self.driver == "ha_start_button"

    @property
    def modulating(self) -> bool:
        """Traploos regelbaar (laadstroom instellen) in plaats van alleen aan/uit."""
        return self.driver == "ha_current"

    @property
    def w_per_step(self) -> float:
        p = self.params or {}
        return float(p.get("volts", 230)) * int(p.get("phases", 1))

    @property
    def min_w(self) -> float:
        return float((self.params or {}).get("min_a", 6)) * self.w_per_step if self.modulating else self.power_w

    @property
    def max_w(self) -> float:
        return float((self.params or {}).get("max_a", 16)) * self.w_per_step if self.modulating else self.power_w

    @property
    def start_threshold_w(self) -> float:
        if self.start_surplus_w is not None:
            return self.start_surplus_w
        return self.min_w if self.modulating else 0.9 * self.power_w


@dataclass
class ContractConfig:
    """Energiecontract: bepaalt wat een kWh eigen zonnestroom waard is."""

    type: str = "fixed"                 # fixed | dynamic
    supplier: str = ""                  # sleutel uit suppliers.py (vult de bedragen hieronder voor)
    import_price: float = 0.29          # €/kWh incl. belastingen (vast contract)
    feed_in_price: float = 0.01         # €/kWh netto terugleververgoeding (vast contract)
    energy_tax: Optional[float] = None  # €/kWh energiebelasting incl. btw; leeg = automatisch per jaar
    supplier_markup: float = 0.02       # €/kWh inkoopvergoeding leverancier incl. btw (dynamisch)
    feed_in_cost: float = 0.02          # (oud) €/kWh die de leverancier inhoudt op teruglevering (dynamisch)
    feed_in_adjust: Optional[float] = None  # €/kWh bovenop (+) of van (−) de marktprijs bij teruglevering (dynamisch)
    return_cost: float = 0.0            # terugleverkosten per kWh (vast/variabel contract, vanaf 2027)
    price_entity: str = ""              # Home Assistant-sensor met je actuele stroomprijs (bijv. van Tibber)


@dataclass
class SolarConfig:
    """Gegevens van de zonnepanelen, voor de zonvoorspelling."""

    latitude: float = 52.1
    longitude: float = 5.1
    kwp: float = 4.0            # piekvermogen van alle panelen samen
    tilt: float = 35.0          # hellingshoek in graden
    azimuth: float = 0.0        # 0 = zuid, -90 = oost, 90 = west
    base_load_w: float = 350.0  # gemiddeld sluipverbruik van het huis overdag
    has_panels: bool = True     # False = geen zonnepanelen: sturen op de goedkoopste uren
    forecast_url: str = "https://api.open-meteo.com/v1/forecast"


@dataclass
class Config:
    p1_host: str = ""
    devices: list[DeviceConfig] = field(default_factory=list)
    contract: ContractConfig = field(default_factory=ContractConfig)
    solar: SolarConfig = field(default_factory=SolarConfig)
    interval_s: int = 10
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    web_token: str = ""          # optioneel wachtwoord voor het dashboard
    db_path: str = "zonnestuur.db"
    timezone: str = "Europe/Amsterdam"
    use_forecast: bool = True
    use_prices: bool = True
    scan_extra: list[str] = field(default_factory=list)   # extra adressen om te proberen bij het zoeken
    meter: dict = field(default_factory=dict)              # {"driver": "homewizard"|"shelly_em"|"ha", ...}
    homeassistant: dict = field(default_factory=dict)      # {"url": ..., "token": ...}
    inverter: dict = field(default_factory=dict)           # {"entity": number.x, "max_w": 5000, "unit": "W"|"%"}
    notify: dict = field(default_factory=dict)             # {"service": "notify.mobile_app_x", "tips": true, "alerts": true}
    batteries: list = field(default_factory=list)          # thuisbatterijen, zie battery.BatteryConfig

    @property
    def meter_driver(self) -> str:
        return (self.meter or {}).get("driver") or "homewizard"

    @property
    def strategy(self) -> str:
        """'solar' = draaien op eigen zonnestroom, 'price' = geen panelen, draaien in de goedkoopste uren."""
        return "solar" if self.solar.has_panels else "price"

    @property
    def has_meter(self) -> bool:
        return bool(self.p1_host) if self.meter_driver in ("homewizard", "shelly_em") else bool(self.meter)

    @property
    def configured(self) -> bool:
        return (self.has_meter or self.strategy == "price") and (len(self.devices) > 0 or len(self.batteries) > 0)

    def device(self, device_id: str) -> DeviceConfig:
        for d in self.devices:
            if d.id == device_id:
                return d
        raise KeyError(device_id)

    def to_dict(self) -> dict:
        return asdict(self)


SUPERVISOR_URL = __import__("os").environ.get("ZONNESTUUR_SUPERVISOR_URL", "http://supervisor/core")


def supervisor_token() -> str:
    """Token dat Home Assistant meegeeft als Zonnestuur als add-on draait (anders leeg)."""
    import os
    return os.environ.get("SUPERVISOR_TOKEN", "")


def effective_ha(cfg: "Config") -> dict:
    """Home Assistant-verbinding: wat de gebruiker instelde, of automatisch als add-on."""
    ha = dict(getattr(cfg, "homeassistant", None) or {})
    if ha.get("url") and ha.get("token"):
        return ha
    tok = supervisor_token()
    return {"url": SUPERVISOR_URL, "token": tok, "addon": True} if tok else {}


DRIVERS = ("shelly", "shelly_gen1", "homewizard_socket", "tasmota", "ha_switch", "ha_setpoint", "ha_current",
           "ha_start_button")
SERVER_KEYS = ("interval_s", "web_host", "web_port", "web_token", "db_path", "timezone", "scan_extra")


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if not p.exists():
        return Config()
    return config_from_dict(json.loads(p.read_text(encoding="utf-8")))


def save_config(cfg: Config, path: str | Path) -> None:
    """Schrijf de configuratie veilig weg (eerst naar een tijdelijk bestand, dan vervangen)."""
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def public_dict(cfg: Config) -> dict:
    """Configuratie zoals de app hem mag zien en bewerken (zonder wachtwoord en serverinstellingen)."""
    d = cfg.to_dict()
    for k in SERVER_KEYS:
        d.pop(k, None)
    d["configured"] = cfg.configured
    d["has_token"] = bool(cfg.web_token)
    ha = dict(d.get("homeassistant") or {})
    eff = effective_ha(cfg)
    d["has_ha"] = bool(eff.get("url") and eff.get("token"))
    d["ha_addon"] = bool(eff.get("addon"))
    if eff.get("addon") and not ha.get("url"):
        ha = {"url": "Home Assistant (deze installatie)"}
    ha.pop("token", None)
    d["homeassistant"] = ha
    return d


def merge_public(current: Config, incoming: dict) -> Config:
    """Maak een nieuwe configuratie uit wat de app stuurt; serverinstellingen blijven zoals ze waren."""
    base = current.to_dict()
    new_token = incoming.get("new_token")
    incoming = {k: v for k, v in incoming.items()
                if k not in SERVER_KEYS and k not in ("configured", "has_token", "new_token", "has_ha", "ha_addon")}
    if "homeassistant" in incoming:
        ha = dict(incoming["homeassistant"] or {})
        if not ha.get("token"):
            ha["token"] = (current.homeassistant or {}).get("token", "")   # token blijft bewaard
        if not ha.get("url") or not str(ha["url"]).startswith("http"):
            ha = {}
        incoming["homeassistant"] = ha
    base.update(incoming)
    if isinstance(new_token, str):
        base["web_token"] = new_token.strip()      # leeg = wachtwoord uit
    return config_from_dict(base)


def _known(cls, d: dict) -> dict:
    names = set(cls.__dataclass_fields__)
    return {k: v for k, v in d.items() if k in names}


def config_from_dict(raw: dict) -> Config:
    raw = dict(raw)
    try:
        devices = [DeviceConfig(**_known(DeviceConfig, d)) for d in raw.pop("devices", [])]
        contract = ContractConfig(**_known(ContractConfig, raw.pop("contract", {})))
        solar = SolarConfig(**_known(SolarConfig, raw.pop("solar", {})))
        cfg = Config(devices=devices, contract=contract, solar=solar, **_known(Config, raw))
    except TypeError as exc:
        raise ValueError(f"Onvolledige instelling: {exc}") from exc
    for d in cfg.devices:
        if d.driver not in DRIVERS:
            raise ValueError(f"Onbekende koppeling voor {d.name}: {d.driver}")
        if not d.id or not d.name:
            raise ValueError("Elk apparaat heeft een id en naam nodig")
        if not d.driver.startswith("ha_") and not d.host:
            raise ValueError(f"{d.name} heeft een adres nodig")
        if d.driver.startswith("ha_") and not effective_ha(cfg).get("token"):
            raise ValueError(f"{d.name} gebruikt Home Assistant, maar Home Assistant is nog niet gekoppeld")
        if not (100 <= d.power_w <= 25000):
            raise ValueError(f"Vermogen van {d.name} moet tussen 100 en 25.000 W liggen")
        if d.kind not in ("boiler", "ev", "heatpump", "generic"):
            raise ValueError(f"Onbekend soort apparaat: {d.kind}")
    ids = [d.id for d in cfg.devices]
    if len(ids) != len(set(ids)):
        raise ValueError("Elk apparaat moet een uniek id hebben")
    if cfg.contract.type not in ("fixed", "dynamic"):
        raise ValueError("contract.type moet 'fixed' of 'dynamic' zijn")
    if cfg.strategy == "price" and cfg.contract.type != "dynamic" and cfg.devices:
        raise ValueError("Zonder zonnepanelen stuurt Zonnestuur op de stroomprijs: daarvoor is een dynamisch contract nodig")
    inv = cfg.inverter or {}
    if inv.get("entity"):
        if inv.get("unit", "W") not in ("W", "kW", "%"):
            raise ValueError("Omvormer: eenheid moet W, kW of % zijn")
        if not (100 <= float(inv.get("max_w") or 0) <= 100000):
            raise ValueError("Omvormer: vul het maximale vermogen in (100–100.000 W)")
        if not effective_ha(cfg).get("token"):
            raise ValueError("Omvormer begrenzen gaat via Home Assistant, maar die is nog niet gekoppeld")
    from .battery import BatteryConfig
    for b in cfg.batteries:
        bc = BatteryConfig.from_dict(b)
        bc.validate()
        if not effective_ha(cfg).get("token"):
            raise ValueError(f"{bc.name} gaat via Home Assistant, maar die is nog niet gekoppeld")
    if len({b.get("id") for b in cfg.batteries}) != len(cfg.batteries):
        raise ValueError("Elke batterij moet een uniek id hebben")
    if (cfg.notify or {}).get("service") and not str(cfg.notify["service"]).startswith("notify."):
        raise ValueError("Meldingen: kies een dienst die met 'notify.' begint")
    for d in cfg.devices:
        if any(not isinstance(x, int) or not 0 <= x <= 6 for x in d.ready_days):
            raise ValueError(f"Ongeldige dagen voor {d.name}")
        for t in d.ready_times:
            hh, mm = t.split(":")
            if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
                raise ValueError(f"Ongeldige tijd voor {d.id}: {t}")
        if d.guarantee_min < 0 or d.guarantee_min > 720:
            raise ValueError(f"guarantee_min voor {d.id} moet tussen 0 en 720 liggen")
    return cfg
