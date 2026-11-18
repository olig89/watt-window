"""Keeps the heat pump advice current: on every price refresh, every spare-solar change, and each minute.

Sensors only: Watt Window says boost / normal / hold back; the user's own
automation decides what that means for their heat pump (e.g. +2 degrees on the
room target, or a hot-water top-up).

Two planners give the same states:

* **look ahead** (default): compares now with the next ``store_hours``;
* **plan**: plans every known price with a heat-need model and the house as a
  heat store (``core.heatplan``). It needs a weather forecast; without one it
  falls back to look ahead.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

from homeassistant.const import UnitOfTemperature
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, restore_state
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import (
    CONF_BASE_LOAD_W,
    CONF_HP_BALANCE_TEMP,
    CONF_HP_BANK_HOURS,
    CONF_HP_BANK_SHARE,
    CONF_HP_BOOST_PENALTY,
    CONF_HP_BRINE_ENTITY,
    CONF_HP_BRINE_MIN,
    CONF_HP_ENABLED,
    CONF_HP_KWH_PER_DD,
    CONF_HP_MARGIN,
    CONF_HP_MAX_BOOST_HOURS,
    CONF_HP_MIN_BLOCK_HOURS,
    CONF_HP_MIN_MINUTES,
    CONF_HP_PLANNER,
    CONF_HP_POWER_W,
    CONF_HP_STORE_HOURS,
    CONF_HP_WEATHER_ENTITY,
    DEFAULT_BASE_LOAD_W,
    DEFAULT_HP_BALANCE_TEMP,
    DEFAULT_HP_BANK_HOURS,
    DEFAULT_HP_BANK_SHARE,
    DEFAULT_HP_BOOST_PENALTY,
    DEFAULT_HP_BRINE_MIN,
    DEFAULT_HP_KWH_PER_DD,
    DEFAULT_HP_MARGIN,
    DEFAULT_HP_MAX_BOOST_HOURS,
    DEFAULT_HP_MIN_BLOCK_HOURS,
    DEFAULT_HP_MIN_MINUTES,
    DEFAULT_HP_POWER_W,
    DEFAULT_HP_STORE_HOURS,
    DOMAIN,
    PLANNER_LOOK_AHEAD,
    PLANNER_PLAN,
)
from .core.heatplan import BrineGuard, Forecast, Plan, PlanSettings, make_plan, quarters_in_mode, step
from .core.heatpump import BOOST, NORMAL, Advice, StickyMode, advise
from .core.windows import QUARTER
from .spare import signal as spare_signal

_LOGGER = logging.getLogger(__name__)
FORECAST_EVERY = timedelta(minutes=30)
FORECAST_STALE = timedelta(hours=6)  # no fresh forecast for this long: stop planning on the old one
PLAN_REASONS = {
    "boost": "Plan: store heat while it's cheap",
    "normal": "Plan: no shift worth making now",
    "hold_back": "Plan: coast through dearer hours",
}


def signal(entry_id: str) -> str:
    return f"{DOMAIN}_heat_pump_{entry_id}"


def default_weather_entity(hass: HomeAssistant) -> str | None:
    ids = sorted(hass.states.async_entity_ids("weather"))
    return ids[0] if ids else None


def _float(hass: HomeAssistant, entity_id: str | None) -> float | None:
    st = hass.states.get(entity_id) if entity_id else None
    try:
        return float(st.state) if st else None
    except (TypeError, ValueError):
        return None


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
        min_minutes = float(s.get(CONF_HP_MIN_MINUTES, DEFAULT_HP_MIN_MINUTES))
        self.sticky = StickyMode(timedelta(minutes=min_minutes))
        self.advice: Advice | None = None
        self._unsubs: list[CALLBACK_TYPE] = []

        self.planner = s.get(CONF_HP_PLANNER, PLANNER_LOOK_AHEAD)
        self.weather_entity = s.get(CONF_HP_WEATHER_ENTITY)
        self.brine_entity = s.get(CONF_HP_BRINE_ENTITY) or None
        min_normal = max(1, math.ceil(min_minutes / 15))
        min_block = max(min_normal, round(float(s.get(CONF_HP_MIN_BLOCK_HOURS, DEFAULT_HP_MIN_BLOCK_HOURS)) * 4))
        self.plan_settings = PlanSettings(
            kwh_per_degree_day=float(s.get(CONF_HP_KWH_PER_DD, DEFAULT_HP_KWH_PER_DD)),
            balance_temp=float(s.get(CONF_HP_BALANCE_TEMP, DEFAULT_HP_BALANCE_TEMP)),
            store_hours=self.store_hours,
            boost_penalty=float(s.get(CONF_HP_BOOST_PENALTY, DEFAULT_HP_BOOST_PENALTY)),
            margin=self.margin,
            min_block_quarters=min_block,
            min_normal_quarters=min_normal,
            max_boost_quarters=max(min_block, round(float(s.get(CONF_HP_MAX_BOOST_HOURS, DEFAULT_HP_MAX_BOOST_HOURS)) * 4)),
            bank_share=float(s.get(CONF_HP_BANK_SHARE, DEFAULT_HP_BANK_SHARE)),
            bank_hours=float(s.get(CONF_HP_BANK_HOURS, DEFAULT_HP_BANK_HOURS)),
        )
        self.brine_guard = BrineGuard(float(s.get(CONF_HP_BRINE_MIN, DEFAULT_HP_BRINE_MIN)))
        self.brine_cold = False
        self.forecast: Forecast | None = None
        self.forecast_error: str | None = None
        self._forecast_at: datetime | None = None
        # The weather entity the user picked; None means "the first one there is", looked
        # up on every fetch (after a restart the weather integration may load later).
        self._weather_choice = self.weather_entity
        # (mode, since) from before a restart, until the first plan has decided the mode.
        self._restored: tuple[str, datetime | None] | None = None
        self.plan: Plan | None = None
        self._planning = False
        self._replan_again = False
        # A mode the plan didn't choose (the look-ahead fallback while the first forecast
        # loads, or the other planner's mode before a switch): the first plan may replace it
        # at once, without waiting out the minimum time, and plans ignore it.
        self._provisional = True
        self._from_plan = False
        # The store and the current mode survive a reload (saving settings reloads the
        # entry), not a restart.
        saved = hass.data.get(f"{DOMAIN}_hp_store", {}).get(self.entry_id)
        self._restarted = saved is None  # nothing in memory: a fresh start, so look at restore data
        self.store = saved["store"] if saved else 0.0
        self._store_at: datetime | None = saved["store_at"] if saved else None
        if saved and saved["mode"]:
            self.sticky.mode, self.sticky.since = saved["mode"], saved["since"]
            self._provisional = saved["provisional"] or not saved["planning"]

    @property
    def planning(self) -> bool:
        return self.enabled and self.planner == PLANNER_PLAN

    @callback
    def async_start(self) -> None:
        if not self.enabled:
            return
        # After a restart, take the store back first: Home Assistant starts tasks eagerly,
        # so the forecast task below can make the first plan before this method returns.
        # The old since only once the first advice is known.
        last = self._last_state() if self._restarted else None
        if last is not None:
            # store_kwh changes every minute while it is not 0, so last_updated is when it was last true.
            self.restore_store(last.attributes.get("store_kwh"), last.last_updated)
        self._unsubs.append(self.coordinator.async_add_listener(self._on_prices))
        self._unsubs.append(async_dispatcher_connect(self.hass, spare_signal(self.entry_id), self._update))
        self._unsubs.append(async_track_time_interval(self.hass, self._on_tick, timedelta(minutes=1)))
        if self.planning:
            self.weather_entity = self._weather_choice or default_weather_entity(self.hass)
            self._unsubs.append(async_track_time_interval(self.hass, self._on_forecast_tick, FORECAST_EVERY))
            self.hass.async_create_background_task(self._async_refresh_forecast(), f"{DOMAIN} forecast")
        self._update()
        if last is not None:
            since = dt_util.parse_datetime(str(last.attributes.get("since") or ""))
            since = dt_util.as_utc(since) if since else None
            self.restore_since(last.state, since)
            if self.planning and self._provisional:
                # The first plan may still change the mode; try again once it has.
                self._restored = (last.state, since)

    def _last_state(self):
        """The advice sensor's state from before the restart (Home Assistant's restore data)."""
        entity_id = er.async_get(self.hass).async_get_entity_id("sensor", DOMAIN, f"{self.entry_id}_heat_pump_advice")
        stored = restore_state.async_get(self.hass).last_states.get(entity_id) if entity_id else None
        return stored.state if stored else None

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()
        self.hass.data.setdefault(f"{DOMAIN}_hp_store", {})[self.entry_id] = {
            "store": self.store, "store_at": self._store_at, "mode": self.sticky.mode,
            "since": self.sticky.since, "provisional": self._provisional, "planning": self.planning,
        }

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self._update()

    @callback
    def _on_prices(self) -> None:
        self._update()
        self._schedule_replan()

    @callback
    def _on_forecast_tick(self, _now: datetime) -> None:
        self.hass.async_create_background_task(self._async_refresh_forecast(), f"{DOMAIN} forecast")

    async def _async_refresh_forecast(self) -> None:
        points = await self._async_fetch_forecast()
        now = dt_util.utcnow()
        if points:
            self.forecast, self.forecast_error, self._forecast_at = Forecast(points), None, now
        elif self.forecast is not None and self._forecast_at is not None and now - self._forecast_at > FORECAST_STALE:
            # Don't plan on an old forecast for ever: fall back to look ahead until a new one comes.
            self.forecast, self.plan = None, None
        self._schedule_replan()
        self._update()

    async def _async_fetch_forecast(self) -> list[tuple[datetime, float]]:
        entity = self.weather_entity = self._weather_choice or default_weather_entity(self.hass)
        state = self.hass.states.get(entity) if entity else None
        if state is None:
            self.forecast_error = "No weather entity for the temperature forecast"
            return []
        fahrenheit = state.attributes.get("temperature_unit") == UnitOfTemperature.FAHRENHEIT
        for kind in ("hourly", "twice_daily", "daily"):
            try:
                resp = await self.hass.services.async_call(
                    "weather", "get_forecasts", {"type": kind}, target={"entity_id": entity},
                    blocking=True, return_response=True,
                )
            except Exception as err:  # noqa: BLE001 - a weather entity may not offer every kind
                _LOGGER.debug("No %s forecast from %s: %s", kind, entity, err)
                continue
            points = []
            for row in (resp or {}).get(entity, {}).get("forecast", []) or []:
                t = dt_util.parse_datetime(str(row.get("datetime", "")))
                temp = row.get("temperature")
                if t is not None and isinstance(temp, (int, float)):
                    if fahrenheit:  # heat need settings are in °C
                        temp = TemperatureConverter.convert(temp, UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.CELSIUS)
                    points.append((dt_util.as_utc(t), float(temp)))
            if points:
                return points
        self.forecast_error = f"{entity} gave no temperature forecast"
        return []

    @callback
    def _schedule_replan(self) -> None:
        if not self.planning:
            return
        if self._planning:
            self._replan_again = True
            return
        self._planning = True
        self.hass.async_create_background_task(self._async_replan(), f"{DOMAIN} heat pump plan")

    async def _async_replan(self) -> None:
        try:
            while True:
                self._replan_again = False
                data = self.coordinator.data
                if data is None or not self.forecast:
                    return
                now = dt_util.utcnow()
                self._advance_store(now)
                forecast, store = self.forecast, self.store
                mode = None if self._provisional else self.sticky.mode
                age = 0 if self._provisional else quarters_in_mode(self.sticky.since, now)
                self.plan = await self.hass.async_add_executor_job(
                    lambda: make_plan(
                        data.quarters, now, forecast, self.plan_settings,
                        store_now=store, mode_now=mode, mode_quarters=age,
                        power_w=self.power_w, base_load_w=self.base_w,
                    )
                )
                if not self._replan_again:
                    break
        finally:
            self._planning = False
        self._update()

    def _advance_store(self, now: datetime) -> None:
        """Add up the store from the advice given since the last update."""
        if self._store_at is not None and now > self._store_at and self.planning and self.forecast:
            fraction = min((now - self._store_at) / QUARTER, 4 * 6)  # at most 6 h after a gap
            need = self.plan_settings.need_per_quarter(self.forecast.at(now))
            _, self.store = step(self.plan_settings, self.sticky.mode or NORMAL, self.store, need, fraction)
        self._store_at = now

    @callback
    def _update(self) -> None:
        data = self.coordinator.data
        if data is None:
            return
        now = dt_util.utcnow()
        self._advance_store(now)
        spare = getattr(self.coordinator, "spare", None)
        spare_now = bool(spare and spare.switch.is_on)
        advice = advise(data.quarters, now, self.power_w, self.base_w, self.store_hours, self.margin, spare_enough=spare_now)
        self._from_plan = False
        if self.planning and not spare_now:
            advice = self._planned(advice, now)
        self.advice = advice
        if not self.planning:
            self.sticky.update(now, advice.mode)
        elif self._from_plan and self._provisional:
            # The first plan replaces a mode it didn't choose, without the minimum wait.
            if advice.mode != self.sticky.mode:
                self.sticky.force(now, advice.mode)
            self._provisional = False
            if self._restored is not None:
                # After a restart: if the plan carries on with the advice from before it, keep that since.
                self.restore_since(*self._restored)
                self._restored = None
        else:
            since = self.sticky.since
            self.sticky.update(now, advice.mode)
            if not self._from_plan and not spare_now and self.sticky.since != since:
                self._provisional = True  # set by the look-ahead fallback
        async_dispatcher_send(self.hass, signal(self.entry_id))

    def _planned(self, look: Advice, now: datetime) -> Advice:
        """The plan's mode for now, keeping the look-ahead's price figures as attributes."""
        block = self.plan.block_at(now) if self.plan else None
        if block is None:
            why = self.forecast_error if not self.forecast else "No plan yet"
            return Advice(look.mode, f"{why or 'Waiting for the forecast'}: looking ahead instead. {look.reason}",
                          look.price_now, look.average_ahead, look.cheapest_ahead, look.cheapest_at,
                          look.hours_ahead_known)
        self._from_plan = True
        mode = block.mode
        until = dt_util.as_local(block.end).strftime("%H:%M")
        reason = f"{PLAN_REASONS[mode]} (until {until})"
        if self.brine_entity:
            self.brine_cold = self.brine_guard.update(_float(self.hass, self.brine_entity))
            if mode == BOOST and self.brine_cold:
                mode, reason = NORMAL, "Plan says boost, but the ground loop is cold"
        return Advice(mode, reason, look.price_now, look.average_ahead, look.cheapest_ahead,
                      look.cheapest_at, look.hours_ahead_known)

    @property
    def mode(self) -> str | None:
        return self.sticky.mode

    @callback
    def restore_since(self, mode: str | None, since: datetime | None) -> bool:
        """After a restart: keep the old ``since`` when the advice is still the same.

        Automations time boost and hold back caps from ``since``; a restart that
        changes nothing about the advice shouldn't restart their clocks. A different
        mode keeps its new ``since``. Returns True when the old one was kept.
        """
        if mode is None or since is None or mode != self.sticky.mode or self.sticky.since is None:
            return False
        if not since < self.sticky.since <= dt_util.utcnow() + timedelta(minutes=1):
            return False  # nothing older to keep, or a clock that jumped
        self.sticky.since = since
        return True

    @callback
    def restore_store(self, store: object, saved_at: datetime | None) -> bool:
        """After a restart: pick the plan's heat store up where it was.

        Without it the plan starts from an empty store and can end a boost or
        hold back that is half done. After a long gap (more than ``store_hours``)
        the house has drifted back to normal anyway, so the store stays at 0.
        Returns True when the saved store was taken back.
        """
        if not self.planning or saved_at is None:
            return False
        try:
            value = float(store)
        except (TypeError, ValueError):
            return False
        gap = dt_util.utcnow() - saved_at
        if not timedelta(0) <= gap <= timedelta(hours=self.store_hours) or value != value:
            return False
        lo, hi = -self.plan_settings.store_limit, self.plan_settings.bank_limit
        self.store, self._store_at = max(lo, min(hi, value)), dt_util.utcnow()
        return True

    def plan_dict(self) -> dict:
        if not self.planning:
            return {}
        p = self.plan
        now = dt_util.utcnow()
        return {
            "plan": [
                {"start": b.start.isoformat(), "end": b.end.isoformat(), "mode": b.mode, "price": round(b.price, 5)}
                for b in (p.blocks if p else []) if b.end > now
            ],
            "plan_made_at": p.made_at.isoformat() if p else None,
            "store_kwh": round(self.store, 2),
            "store_limit_kwh": round(self.plan_settings.store_limit, 2),
            "bank_limit_kwh": round(self.plan_settings.bank_limit, 2),
            "need_kwh": round(p.need_kwh, 1) if p else None,
            "outdoor_average": round(p.outdoor_average, 1) if p else None,
            "plan_saving": round(p.saving, 3) if p else None,
            "weather_entity": self.weather_entity,
            "brine_cold": self.brine_cold if self.brine_entity else None,
        }

    def as_dict(self) -> dict:
        a = self.advice
        held = a is not None and self.sticky.mode != a.mode
        return {
            "enabled": self.enabled,
            "planner": self.planner,
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
            **self.plan_dict(),
        }
