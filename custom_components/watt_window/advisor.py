"""Keeps the heat pump advice current: on every price refresh, every spare-solar change, and each minute.

Sensors only: Watt Window says boost / normal / hold back; the user's own
automation decides what that means for their heat pump (e.g. +2 degrees on the
room target, or a hot-water top-up).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BASE_LOAD_W,
    CONF_HP_ENABLED,
    CONF_HP_MARGIN,
    CONF_HP_MIN_MINUTES,
    CONF_HP_POWER_W,
    CONF_HP_STORE_HOURS,
    DEFAULT_BASE_LOAD_W,
    DEFAULT_HP_MARGIN,
    DEFAULT_HP_MIN_MINUTES,
    DEFAULT_HP_POWER_W,
    DEFAULT_HP_STORE_HOURS,
    DOMAIN,
)
from .core.heatpump import Advice, StickyMode, advise
from .spare import signal as spare_signal


def signal(entry_id: str) -> str:
    return f"{DOMAIN}_heat_pump_{entry_id}"


class HeatPumpAdvisor:
    def __init__(self, hass: HomeAssistant, coordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.entry_id = coordinator.config_entry.entry_id
        s = coordinator.settings
        self.enabled = bool(s.get(CONF_HP_ENABLED, False))
        self.power_w = float(s.get(CONF_HP_POWER_W, DEFAULT_HP_POWER_W))
        self.base_w = float(s.get(CONF_BASE_LOAD_W, DEFAULT_BASE_LOAD_W))
        self.store_hours = float(s.get(CONF_HP_STORE_HOURS, DEFAULT_HP_STORE_HOURS))
        self.margin = float(s.get(CONF_HP_MARGIN, DEFAULT_HP_MARGIN))
        self.sticky = StickyMode(timedelta(minutes=float(s.get(CONF_HP_MIN_MINUTES, DEFAULT_HP_MIN_MINUTES))))
        self.advice: Advice | None = None
        self._unsubs: list[CALLBACK_TYPE] = []

    @callback
    def async_start(self) -> None:
        if not self.enabled:
            return
        self._unsubs.append(self.coordinator.async_add_listener(self._update))
        self._unsubs.append(async_dispatcher_connect(self.hass, spare_signal(self.entry_id), self._update))
        self._unsubs.append(async_track_time_interval(self.hass, self._on_tick, timedelta(minutes=1)))
        self._update()

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self._update()

    @callback
    def _update(self) -> None:
        data = self.coordinator.data
        if data is None:
            return
        now = dt_util.utcnow()
        spare = getattr(self.coordinator, "spare", None)
        self.advice = advise(
            data.quarters, now, self.power_w, self.base_w, self.store_hours, self.margin,
            spare_enough=bool(spare and spare.switch.is_on),
        )
        self.sticky.update(now, self.advice.mode)
        async_dispatcher_send(self.hass, signal(self.entry_id))

    @property
    def mode(self) -> str | None:
        return self.sticky.mode

    def as_dict(self) -> dict:
        a = self.advice
        held = a is not None and self.sticky.mode != a.mode
        return {
            "enabled": self.enabled,
            "mode": self.sticky.mode,
            "wanted": a.mode if a else None,
            "reason": (a.reason if a else None) if not held else f"Holding for now so the heat pump isn't switched too often (would be: {a.mode.replace('_', ' ')})",
            "since": self.sticky.since.isoformat() if self.sticky.since else None,
            "price_now": None if not a or a.price_now is None else round(a.price_now, 5),
            "average_ahead": None if not a or a.average_ahead is None else round(a.average_ahead, 5),
            "cheapest_ahead": None if not a or a.cheapest_ahead is None else round(a.cheapest_ahead, 5),
            "cheapest_at": a.cheapest_at.isoformat() if a and a.cheapest_at else None,
            "hours_ahead_known": a.hours_ahead_known if a else 0,
            "store_hours": self.store_hours,
            "margin": self.margin,
            "power_w": self.power_w,
        }
