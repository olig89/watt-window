"""Watches the live grid and solar power sensors and keeps the spare-solar estimate current.

Which sensors: the ones the Energy dashboard already uses for live power (its
grid "power" sensor is positive when importing, whatever the meter itself does),
unless the user picked their own on the Settings tab. So most people set
nothing up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CAN_EXPORT,
    CONF_LOAD_W,
    CONF_SPARE_GRID_ENTITY,
    CONF_SPARE_GRID_IMPORT_NEGATIVE,
    CONF_SPARE_NEAR_ZERO_W,
    CONF_SPARE_OFF_MIN,
    CONF_SPARE_ON_MIN,
    CONF_SPARE_SMOOTH_MIN,
    CONF_SPARE_SOLAR_ENTITY,
    DEFAULT_LOAD_W,
    DEFAULT_SPARE_NEAR_ZERO_W,
    DEFAULT_SPARE_OFF_MIN,
    DEFAULT_SPARE_ON_MIN,
    DEFAULT_SPARE_SMOOTH_MIN,
    DOMAIN,
)
from .core.spare import DelayedSwitch, SpareEstimate, TimeAverage, estimate_spare

_LOGGER = logging.getLogger(__name__)
TICK = timedelta(seconds=30)  # re-check the delays even when no reading changes


def signal(entry_id: str) -> str:
    return f"{DOMAIN}_spare_{entry_id}"


async def energy_power_sensors(hass: HomeAssistant) -> tuple[str | None, list[str]]:
    """(grid power sensor, [solar power sensors]) from the Energy dashboard, if set."""
    try:
        from homeassistant.components.energy.data import async_get_manager

        manager = await async_get_manager(hass)
    except Exception as err:  # noqa: BLE001 - optional; manual choice still works
        _LOGGER.debug("Energy dashboard settings unavailable: %s", err)
        return None, []
    grid, solar = None, []
    for src in (manager.data or {}).get("energy_sources", []):
        if src.get("type") == "grid" and src.get("stat_rate") and grid is None:
            grid = src["stat_rate"]
        elif src.get("type") == "solar" and src.get("stat_rate"):
            solar.append(src["stat_rate"])
    return grid, solar


def _watts(hass: HomeAssistant, entity_id: str) -> float | None:
    state = hass.states.get(entity_id)
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN, ""):
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    unit = (state.attributes.get("unit_of_measurement") or "W").strip()
    return value * {"kW": 1000.0, "MW": 1_000_000.0}.get(unit, 1.0)


@dataclass
class SpareSources:
    grid: str | None
    grid_import_negative: bool
    solar: list[str]
    origin: str  # "energy_dashboard" | "settings" | "none"

    @property
    def usable(self) -> bool:
        return bool(self.grid and self.solar)


class SpareMonitor:
    def __init__(self, hass: HomeAssistant, coordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.entry_id = coordinator.config_entry.entry_id
        s = coordinator.settings
        self.can_export = bool(s.get(CONF_CAN_EXPORT, True))
        self.load_w = float(s.get(CONF_LOAD_W, DEFAULT_LOAD_W))
        self.near_zero_w = float(s.get(CONF_SPARE_NEAR_ZERO_W, DEFAULT_SPARE_NEAR_ZERO_W))
        self.smooth = timedelta(minutes=float(s.get(CONF_SPARE_SMOOTH_MIN, DEFAULT_SPARE_SMOOTH_MIN)))
        self.switch = DelayedSwitch(
            timedelta(minutes=float(s.get(CONF_SPARE_ON_MIN, DEFAULT_SPARE_ON_MIN))),
            timedelta(minutes=float(s.get(CONF_SPARE_OFF_MIN, DEFAULT_SPARE_OFF_MIN))),
        )
        self._grid_avg = TimeAverage(self.smooth)
        self._solar_avg = TimeAverage(self.smooth)
        self.sources = SpareSources(None, False, [], "none")
        self.estimate: SpareEstimate | None = None
        self.grid_w: float | None = None
        self.solar_w: float | None = None
        self.forecast_w: float | None = None
        self._unsubs: list[CALLBACK_TYPE] = []

    async def async_start(self) -> None:
        s = self.coordinator.settings
        energy_grid, energy_solar = await energy_power_sensors(self.hass)
        own_grid, own_solar = s.get(CONF_SPARE_GRID_ENTITY), s.get(CONF_SPARE_SOLAR_ENTITY)
        # Each sensor falls back to the Energy dashboard's on its own. The dashboard's
        # grid power is always positive when importing; a hand-picked meter may not be.
        grid = own_grid or energy_grid
        solar = [own_solar] if own_solar else energy_solar
        origin = "settings" if (own_grid or own_solar) else "energy_dashboard"
        self.sources = SpareSources(
            grid, bool(own_grid and s.get(CONF_SPARE_GRID_IMPORT_NEGATIVE, False)), solar,
            origin if grid and solar else "none",
        )
        if not self.sources.usable:
            return
        self._sample(dt_util.utcnow())
        self._unsubs.append(
            async_track_state_change_event(self.hass, [self.sources.grid, *self.sources.solar], self._on_change)
        )
        self._unsubs.append(async_track_time_interval(self.hass, self._on_tick, TICK))

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def _on_change(self, _event: Event) -> None:
        self._sample(dt_util.utcnow())

    @callback
    def _on_tick(self, now: datetime) -> None:
        self._evaluate(dt_util.utcnow())

    def _sample(self, now: datetime) -> None:
        grid = _watts(self.hass, self.sources.grid)
        solar_parts = [_watts(self.hass, e) for e in self.sources.solar]
        if grid is not None:
            self._grid_avg.add(now, -grid if self.sources.grid_import_negative else grid)
        if all(p is not None for p in solar_parts):
            self._solar_avg.add(now, sum(solar_parts))
        self._evaluate(now)

    def _forecast_now(self, now: datetime) -> float | None:
        data = self.coordinator.data
        if data is None or not data.solar_forecast:
            return None
        start = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
        return data.solar_forecast.get(start)

    def _evaluate(self, now: datetime) -> None:
        self.grid_w = self._grid_avg.value(now)
        self.solar_w = self._solar_avg.value(now)
        self.forecast_w = self._forecast_now(now)
        if self.grid_w is None or self.solar_w is None:
            self.estimate = None
        else:
            self.estimate = estimate_spare(
                self.grid_w, self.solar_w, self.forecast_w, self.can_export, self.near_zero_w
            )
        enough = bool(self.estimate and self.estimate.watts is not None and self.estimate.watts >= self.load_w)
        self.switch.update(now, enough)
        async_dispatcher_send(self.hass, signal(self.entry_id))

    def as_dict(self) -> dict:
        e = self.estimate
        return {
            "available": self.sources.usable,
            "origin": self.sources.origin,
            "grid_sensor": self.sources.grid,
            "solar_sensors": self.sources.solar,
            "watts": None if e is None or e.watts is None else round(e.watts),
            "basis": e.basis if e else None,
            "is_estimate": bool(e and e.is_guess),
            "held_back": bool(e and e.held_back),
            "enough": self.switch.is_on,
            "appliance_w": self.load_w,
            "grid_w": None if self.grid_w is None else round(self.grid_w),
            "solar_w": None if self.solar_w is None else round(self.solar_w),
            "forecast_w": None if self.forecast_w is None else round(self.forecast_w),
        }
