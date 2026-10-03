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
    learn_run: bool = True         # looptijd per dag zelf leren uit wat het apparaat echt gebruikt

    @property
    def one_shot(self) -> bool:
        """Alleen starten, nooit uitzetten: witgoed dat een programma afmaakt (wasmachine, droger, vaatwasser)."""
        return self.driver == "ha_start_button"

    @property
    def modulating(self) -> bool:
        """Traploos regelbaar (laadstroom instellen) in plaats van alleen aan/uit."""
        return self.driver in ("ha_current", "ocpp", "ha_power")

    @property
    def w_per_step(self) -> float:
        p = self.params or {}
        if self.driver == "ha_power":
            return float(p.get("step_w", 50))
        return float(p.get("volts", 230)) * int(p.get("phases_active") or p.get("phases", 1))

    @property
    def min_w(self) -> float:
        if self.driver == "ha_power":
            return float((self.params or {}).get("min_w", 100))
        return float((self.params or {}).get("min_a", 6)) * self.w_per_step if self.modulating else self.power_w

    @property
    def max_w(self) -> float:
        if self.driver == "ha_power":
            return self.power_w
        return float((self.params or {}).get("max_a", 16)) * self.w_per_step if self.modulating else self.power_w

    @property
    def solar_share(self) -> float:
        """Deel van het minimale laadvermogen dat uit zon moet komen (1 = alleen zon; 0,5 = de helft mag van het net)."""
        try:
            return max(0.0, min(1.0, float((self.params or {}).get("solar_share", getattr(self, "profile_share", 100))) / 100))
        except (TypeError, ValueError):
            return 1.0

    @property
    def grid_allowance_w(self) -> float:
        """Zoveel mag een traploos apparaat op zon van het net bijnemen (zon-aandeel onder 100%)."""
        return (1 - self.solar_share) * self.min_w if self.modulating else 0.0

    @property
    def start_threshold_w(self) -> float:
        if self.start_surplus_w is not None:
            return self.start_surplus_w
        return self.min_w * self.solar_share if self.modulating else 0.9 * self.power_w


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
    fixed_monthly: float = 0.0          # vaste leveringskosten €/maand (incl. btw)
    grid_monthly: float = 0.0           # netbeheerkosten €/maand (incl. btw)
    tax_credit_year: Optional[float] = None  # vermindering energiebelasting €/jaar incl. btw; leeg = automatisch
    # Terugleverkosten vanaf 2027: per kWh (return_cost), een staffel per jaarteruglevering, of een vast bedrag
    return_cost_mode: str = "per_kwh"   # per_kwh | staffel | vast
    return_cost_tiers: list = field(default_factory=list)   # [[vanaf kWh per jaar, € per maand], ...]
    export_kwh_year: float = 0.0        # verwachte teruglevering per jaar (0 = Zonnestuur schat zelf)
    # Tijdsafhankelijk nettarief (verwacht vanaf 2028): [{"from": "07:00", "to": "11:00", "eur_kwh": 0.03}, ...]
    grid_tou: list = field(default_factory=list)
    grid_tou_from: str = "2028-01-01"


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
    pv_entity: str = ""         # optioneel: sensor met de echte opwek (W of kW) in Home Assistant
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
    mqtt: dict = field(default_factory=dict)               # {"host", "port", "username", "password"}
    homey: dict = field(default_factory=dict)              # {"url", "token"} (Homey Pro, lokale API-sleutel)
    ocpp: dict = field(default_factory=dict)               # {"enabled": true, "port": 8887}
    auto_update: bool = True                               # Zonnestuur Box: nieuwe versies zelf installeren
    trial: dict = field(default_factory=dict)              # meetdagen: {"enabled": bool, "every": 7, "devices": []}
    goal: dict = field(default_factory=dict)               # {"type": "pct"|"eur", "extra_pp": 10, "eur_month": 25}
    motivation: str = ""                                   # "geld" | "milieu" | "allebei" (bepaalt het hoofdcijfer)
    profile: str = ""                                      # sturing: "" (zelfconsumptie) | "prijs" | "netvriendelijk"
    kiosk: dict = field(default_factory=dict)              # {"ha_sensors": true}

    @property
    def meter_driver(self) -> str:
        return (self.meter or {}).get("driver") or "homewizard"

    @property
    def strategy(self) -> str:
        """'solar' = draaien op eigen zonnestroom, 'price' = geen panelen, draaien in de goedkoopste uren."""
        return "solar" if self.solar.has_panels else "price"

    @property
    def has_meter(self) -> bool:
        if self.meter_driver == "p1_serial":
            return True
        if self.meter_driver in ("homewizard", "shelly_em", "youless"):
            return bool(self.p1_host or (self.meter or {}).get("host"))
        return bool(self.meter)

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


_SUP_MQTT: dict = {}


def effective_mqtt(cfg: "Config") -> dict:
    """MQTT-broker: wat de gebruiker instelde, of als add-on automatisch de Mosquitto-broker van Home Assistant."""
    m = dict(getattr(cfg, "mqtt", None) or {})
    if m.get("host"):
        return m
    tok = supervisor_token()
    if not tok:
        return {}
    if not _SUP_MQTT:
        try:
            import json as _j
            import urllib.request as _u
            base = SUPERVISOR_URL.rsplit("/core", 1)[0]
            req = _u.Request(f"{base}/services/mqtt", headers={"Authorization": f"Bearer {tok}"})
            with _u.urlopen(req, timeout=4) as r:
                d = (_j.loads(r.read().decode()).get("data") or {})
            if d.get("host"):
                import socket as _s
                try:
                    _s.gethostbyname(d["host"])
                except OSError:
                    d["host"] = "127.0.0.1"         # host-netwerk: Mosquitto luistert ook op de host zelf
                _SUP_MQTT.update({"host": d["host"], "port": int(d.get("port") or 1883), "username": d.get("username", ""),
                                  "password": d.get("password", ""), "addon": True})
        except Exception:
            return {}
    return dict(_SUP_MQTT)


DRIVERS = ("shelly", "shelly_gen1", "homewizard_socket", "tasmota", "ha_switch", "ha_setpoint", "ha_current",
           "ha_start_button", "esphome", "mqtt_switch", "homey_switch", "homey_setpoint", "sg_ready", "ocpp", "ha_power",
           "ha_charge_buttons")
METER_DRIVERS = ("p1_serial", "homewizard", "shelly_em", "ha", "youless", "dsmr_reader", "esphome", "mqtt", "homey")
HOST_DRIVERS = ("shelly", "shelly_gen1", "homewizard_socket", "tasmota", "esphome")
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
    d["mqtt_addon"] = bool(not (cfg.mqtt or {}).get("host") and effective_mqtt(cfg).get("host"))
    d["ha_addon"] = bool(eff.get("addon"))
    if eff.get("addon") and not ha.get("url"):
        ha = {"url": "Home Assistant (deze installatie)"}
    ha.pop("token", None)
    d["homeassistant"] = ha
    for key, secret in (("mqtt", "password"), ("homey", "token")):
        sec = dict(d.get(key) or {})
        d[f"has_{key}_{secret}"] = bool(sec.pop(secret, ""))
        d[key] = sec
    from .notify import public as _np
    d["notify"] = _np(d.get("notify") or {})
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
    for key, secret in (("mqtt", "password"), ("homey", "token")):
        if key in incoming:
            sec = dict(incoming[key] or {})
            if not sec.get(secret):
                sec[secret] = (getattr(current, key) or {}).get(secret, "")   # geheim blijft bewaard
            incoming[key] = sec
    if "notify" in incoming:
        from .notify import merge as _nm
        incoming["notify"] = _nm(current.notify or {}, incoming["notify"] or {})
    incoming = {k: v for k, v in incoming.items() if not (k.startswith("has_") and k.endswith(("_password", "_token")))}
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
        if d.driver in HOST_DRIVERS and not d.host:
            raise ValueError(f"{d.name} heeft een adres nodig")
        if d.driver == "mqtt_switch" and not effective_mqtt(cfg).get("host"):
            raise ValueError(f"{d.name} gaat via MQTT: vul eerst de MQTT-broker in")
        if d.driver.startswith("homey_") and not (cfg.homey or {}).get("token"):
            raise ValueError(f"{d.name} gaat via Homey: koppel eerst je Homey Pro")
        if d.driver == "ocpp" and not (d.params or {}).get("cp_id"):
            raise ValueError(f"{d.name}: vul de naam (ID) van de laadpaal in zoals ingesteld in de laadpaal")
        if d.driver == "sg_ready" and not ((d.params or {}).get("a") and (d.params or {}).get("b")):
            raise ValueError(f"{d.name}: kies twee relais voor SG-ready")
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
