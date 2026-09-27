"""Het brein: beslist per apparaat of het aan of uit moet.

Volgorde van beslissen per apparaat:
1. Handbediening (aan/uit voor een aantal uur) gaat altijd voor.
2. Garantie: op elke 'klaar-tijd' (ready_time) moet het apparaat kort daarvoor
   'vol' zijn geweest (boiler warm, auto geladen). Zo niet, dan gaat het in de
   garantieminuten vóór die tijd aan, ook op netstroom. Bij een dynamisch
   contract kiezen we daarvoor eerder op de dag de goedkoopste uren als de zon
   het naar verwachting niet redt.
3. Zonne-overschot: aan als er lang genoeg overschot is, uit als het huis te
   lang stroom van het net haalt. Prioriteit 1 gaat eerst aan en als laatste uit.

'Vol' herkennen we zonder sensor: het apparaat staat aan maar neemt al tien
minuten geen stroom op (de thermostaat van de boiler of de lader van de auto
is klaar).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .config import Config, DeviceConfig

RUNNING_W = 50.0      # boven dit vermogen is het apparaat echt bezig
IDLE_W = 20.0         # onder dit vermogen staat een aan-geschakeld apparaat stil
FULL_AFTER_S = 600    # zo lang stil terwijl hij aan staat = vol
MODES = ("auto", "on", "off")


@dataclass
class DeviceState:
    on: bool = False
    power_w: float = 0.0
    energy_wh: Optional[float] = None
    online: bool = True
    last_switch: float = -math.inf       # monotone tijd (s) van laatste schakeling door ons
    reason: str = "start"
    surplus_since: Optional[float] = None
    import_since: Optional[float] = None
    idle_on_since: Optional[float] = None
    full_at: Optional[datetime] = None    # laatste moment dat 'vol' is gezien
    run_seconds_today: float = 0.0
    day: Optional[object] = None
    mode: str = "auto"
    override_until: Optional[datetime] = None
    setpoint_w: Optional[float] = None      # traploze apparaten: ingesteld vermogen
    setpoint_at: float = -math.inf
    offline_reason: str = ""
    targets: list = field(default_factory=list)   # recente gewenste vermogens (voor apparaten die zelden bijsturen)                  # waarom niet beschikbaar (bijv. 'wacht tot je hem klaarzet')

    def to_dict(self) -> dict:
        return {
            "on": self.on, "power_w": round(self.power_w, 1), "online": self.online,
            "reason": self.reason, "run_minutes_today": round(self.run_seconds_today / 60, 1),
            "full_at": self.full_at.isoformat(timespec="minutes") if self.full_at else None,
            "mode": self.mode, "setpoint_w": None if self.setpoint_w is None else round(self.setpoint_w),
            "override_until": self.override_until.isoformat(timespec="minutes") if self.override_until else None,
        }


@dataclass
class Decision:
    device_id: str
    on: bool
    reason: str
    power_w: Optional[float] = None         # traploze apparaten: gewenst vermogen


@dataclass
class Context:
    """Alles wat het brein weet over dit moment."""

    now: datetime                  # lokale tijd
    mono: float                    # monotone klok in seconden (voor wachttijden)
    grid_w: Optional[float]        # None = P1-meter niet bereikbaar
    dt: float                      # seconden sinds vorige ronde
    cheapest_hours: dict[str, set] = field(default_factory=dict)  # device_id -> uren (lokale tijd) om bij te laden
    sunny_hours: dict[str, set] = field(default_factory=dict)     # device_id -> beste zonuren volgens de voorspelling


class Controller:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.states: dict[str, DeviceState] = {d.id: DeviceState() for d in cfg.devices}

    # ------------------------------------------------------------------
    def set_mode(self, device_id: str, mode: str, now: datetime, hours: Optional[float] = None) -> None:
        if mode not in MODES:
            raise ValueError(f"Onbekende stand: {mode}")
        st = self.states[device_id]
        st.mode = mode
        st.override_until = now + timedelta(hours=hours) if (hours and mode != "auto") else None

    def update_measurements(self, device_id: str, on: bool, power_w: float,
                            energy_wh: Optional[float], online: bool = True, offline_reason: str = "") -> None:
        st = self.states[device_id]
        st.on, st.power_w, st.energy_wh, st.online = on, power_w, energy_wh, online
        st.offline_reason = "" if online else offline_reason

    # ---- garantie --------------------------------------------------------
    @staticmethod
    def ready_datetimes(d: DeviceConfig, now: datetime) -> list[datetime]:
        """Komende klaar-tijden binnen 24 uur, oplopend."""
        out = []
        for t in d.ready_times:
            hh, mm = (int(x) for x in t.split(":"))
            dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if dt <= now:
                dt += timedelta(days=1)
            out.append(dt)
        return sorted(out)

    def is_satisfied(self, d: DeviceConfig, st: DeviceState, ready: datetime) -> bool:
        return st.full_at is not None and st.full_at >= ready - timedelta(hours=d.full_lookback_h)

    def next_unsatisfied(self, d: DeviceConfig, st: DeviceState, now: datetime) -> Optional[datetime]:
        for ready in self.ready_datetimes(d, now):
            if not self.is_satisfied(d, st, ready):
                return ready
        return None

    # ------------------------------------------------------------------
    def step(self, ctx: Context) -> list[Decision]:
        """Eén regelronde. Geeft de gewenste schakelacties terug (alleen wijzigingen)."""
        devices = sorted(self.cfg.devices, key=lambda d: d.priority)
        wanted: dict[str, tuple[bool, str]] = {}

        for d in devices:
            self._bookkeeping(d, self.states[d.id], ctx)

        free: list[DeviceConfig] = []
        for d in devices:
            forced = self._forced(d, self.states[d.id], ctx)
            if forced is not None:
                wanted[d.id] = forced
            else:
                free.append(d)

        if ctx.grid_w is None:
            for d in free:
                wanted[d.id] = (False, "geen meterdata")
        else:
            wanted.update(self._solar(free, ctx))

        decisions = []
        for d in devices:
            st = self.states[d.id]
            if not st.online:
                st.reason = st.offline_reason or "offline"
                continue
            on, reason = wanted.get(d.id, (st.on, st.reason))
            if d.one_shot and st.on:
                on, reason = True, "programma loopt"          # een gestart programma breken we nooit af
            setpoint = self._setpoint(d, st, ctx, on, reason) if d.modulating else None
            if on != st.on:
                decisions.append(Decision(d.id, on, reason, setpoint))
                st.last_switch = ctx.mono
                st.on = on
                st.surplus_since = None
                st.import_since = None
                st.idle_on_since = None
            elif on and setpoint is not None:
                decisions.append(Decision(d.id, on, reason, setpoint))
            st.reason = reason
        return decisions

    def _setpoint(self, d: DeviceConfig, st: DeviceState, ctx: Context, on: bool, reason: str) -> Optional[float]:
        """Traploos: stel het vermogen zo in dat het overschot precies wordt opgebruikt.

        Geeft alleen een nieuwe waarde terug als die genoeg verschilt en er niet te vaak wordt bijgestuurd.
        """
        if not on:
            st.setpoint_w = None
            return None
        forced = reason.startswith(("garantie", "handmatig", "goedkoop"))
        if forced or ctx.grid_w is None:
            target = d.max_w
        else:
            current = st.power_w if st.power_w > 0 else (st.setpoint_w or 0.0)   # net gestart: nog niets
            target = current - ctx.grid_w - 100          # 100 W marge zodat we net niet van het net halen
        step = d.w_per_step
        target = max(d.min_w, min(d.max_w, math.floor(target / step) * step))
        interval = float((d.params or {}).get("min_interval_s", 30))
        if interval > 60 and not forced:
            # Zelden bijsturen (bijv. Zaptec: eens per 15 min): kies het laagste wat de hele periode paste,
            # zodat een wolk niet meteen stroom van het net kost.
            st.targets = [(m, v) for m, v in st.targets if ctx.mono - m <= interval] + [(ctx.mono, target)]
            target = min(v for _, v in st.targets)
        first = st.setpoint_w is None
        changed = first or abs(target - st.setpoint_w) >= step
        if interval > 60:
            calm = first or forced or ctx.mono - st.setpoint_at >= interval
        else:
            calm = first or forced or ctx.mono - st.setpoint_at >= interval or target < st.setpoint_w   # omlaag mag direct
        if changed and calm:
            st.setpoint_w, st.setpoint_at = target, ctx.mono
            return target
        return None

    # ------------------------------------------------------------------
    def _bookkeeping(self, d: DeviceConfig, st: DeviceState, ctx: Context) -> None:
        today = ctx.now.date()
        if st.day != today:
            st.day = today
            st.run_seconds_today = 0.0
        if st.power_w > RUNNING_W:
            st.run_seconds_today += ctx.dt
            st.idle_on_since = None
        elif st.on and st.power_w < IDLE_W and d.detect_full:
            if st.idle_on_since is None:
                st.idle_on_since = ctx.mono
            elif ctx.mono - st.idle_on_since >= FULL_AFTER_S:
                st.full_at = ctx.now
        else:
            st.idle_on_since = None
        if st.override_until and ctx.now >= st.override_until:
            st.mode, st.override_until = "auto", None

    def _forced(self, d: DeviceConfig, st: DeviceState, ctx: Context) -> Optional[tuple[bool, str]]:
        if st.mode == "on":
            return True, "handmatig aan"
        if st.mode == "off":
            return False, "handmatig uit"
        if not d.ready_times or d.guarantee_min <= 0:
            return None
        ready = self.next_unsatisfied(d, st, ctx.now)
        if ready is None:
            return None
        left_s = (ready - ctx.now).total_seconds()
        if left_s <= d.guarantee_min * 60:
            return True, f"garantie: klaar om {ready:%H:%M}"
        hours = ctx.cheapest_hours.get(d.id)
        if hours and ctx.now.replace(minute=0, second=0, microsecond=0) in hours:
            return True, f"goedkoop uur, klaar om {ready:%H:%M}"
        return None

    def _solar(self, devices: list[DeviceConfig], ctx: Context) -> dict[str, tuple[bool, str]]:
        out: dict[str, tuple[bool, str]] = {}
        grid = ctx.grid_w

        # Uitschakelen: laagste prioriteit eerst, hoogstens één per ronde
        stopped = False
        for d in sorted(devices, key=lambda x: -x.priority):
            st = self.states[d.id]
            if not st.on:
                st.import_since = None
                continue
            in_best = self._in_best(d, ctx)
            stop_w = d.power_w * 0.6 if in_best else d.stop_import_w
            if grid > stop_w:
                if st.import_since is None:
                    st.import_since = ctx.mono
                long_enough = ctx.mono - st.import_since >= d.stop_delay_s
                min_on_done = ctx.mono - st.last_switch >= d.min_on_s
                if long_enough and min_on_done and not stopped:
                    out[d.id] = (False, "te weinig zon")
                    stopped = True
            else:
                st.import_since = None

        # Inschakelen: hoogste prioriteit eerst, zolang er overschot over is
        available = -grid
        for d in devices:
            st = self.states[d.id]
            if d.id in out:
                continue
            in_best = self._in_best(d, ctx)
            if st.on:
                label = "beste zonuren" if in_best else "zonne-overschot"
                stop_w = d.power_w * 0.6 if in_best else d.stop_import_w
                out[d.id] = (True, label if grid <= stop_w else "aan, wacht op meer zon")
                continue
            threshold = d.power_w * 0.25 if in_best else d.start_threshold_w
            if available >= threshold:
                if st.surplus_since is None:
                    st.surplus_since = ctx.mono
                long_enough = ctx.mono - st.surplus_since >= d.start_delay_s
                min_off_done = ctx.mono - st.last_switch >= d.min_off_s
                if long_enough and min_off_done:
                    out[d.id] = (True, "beste zonuren" if in_best else "zonne-overschot")
                    available -= d.power_w
                else:
                    out[d.id] = (False, "wacht op stabiel overschot")
            else:
                st.surplus_since = None
                out[d.id] = (False, "geen overschot")
        return out


    @staticmethod
    def _in_best(d: DeviceConfig, ctx: Context) -> bool:
        hours = ctx.sunny_hours.get(d.id)
        return bool(hours) and ctx.now.replace(minute=0, second=0, microsecond=0) in hours


def plan_sunny_hours(surplus_by_hour: list[tuple[datetime, float]], run_minutes: float) -> set:
    """Kies het aaneengesloten blok uren met het meeste verwachte overschot.

    Een boiler heeft per dag een vaste hoeveelheid warmte nodig. Die kun je het
    best in één keer maken in de zonnigste uren, in plaats van al bij het eerste
    beetje zon te beginnen en dan vol te zitten als de zon op zijn hoogst staat.
    """
    n = max(1, math.ceil(run_minutes / 60))
    if len(surplus_by_hour) < n:
        return {h for h, _ in surplus_by_hour}
    best, best_sum = None, -math.inf
    for i in range(len(surplus_by_hour) - n + 1):
        block = surplus_by_hour[i:i + n]
        total = sum(max(0.0, w) for _, w in block)
        if total > best_sum:
            best, best_sum = block, total
    if best_sum <= 0:
        return set()
    return {h for h, _ in best}


def plan_cheapest_hours(need_s: float, candidates: list[tuple[datetime, datetime, float]]) -> set:
    """Kies de goedkoopste blokken die samen minstens need_s seconden dekken. Geeft de starttijden terug."""
    chosen: set = set()
    covered = 0.0
    for start, end, _price in sorted(candidates, key=lambda c: c[2]):
        if covered >= need_s:
            break
        chosen.add(start)
        covered += (end - start).total_seconds()
    return chosen
