"""Fetch prices and the solar forecast; price every quarter-hour; pick windows.

All the arithmetic lives in ``core/`` (pure Python, unit-tested). This file
only moves data in and out of Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AREA,
    CONF_BASE_LOAD_W,
    CONF_COUNTRY,
    CONF_LOAD_W,
    CONF_SOLAR_ENTRY,
    CONF_TARIFF,
    CONF_WINDOWS,
    DEFAULT_BASE_LOAD_W,
    DEFAULT_LOAD_W,
    DEFAULT_WINDOWS,
    DOMAIN,
    FORECAST_SOLAR_DOMAIN,
    NORDPOOL_DOMAIN,
    settings_of,
)
from .core import nordpool
from .core.solar import quarter_watts
from .core.windows import Quarter, Window, cheapest_window, price_quarters

_LOGGER = logging.getLogger(__name__)
QUARTER = timedelta(minutes=15)


def quarter_floor(t: datetime) -> datetime:
    return t.replace(minute=t.minute - t.minute % 15, second=0, microsecond=0)


@dataclass
class WattWindowData:
    quarters: list[Quarter]
    windows: dict[int, Window | None]
    now: datetime
    currency: str
    prices_until: datetime | None
    solar_configured: bool
    solar_ok: bool
    warnings: list[str] = field(default_factory=list)

    def quarter_at(self, t: datetime) -> Quarter | None:
        for q in self.quarters:
            if q.start <= t < q.end:
                return q
        return None


class WattWindowCoordinator(DataUpdateCoordinator[WattWindowData]):
    """Recomputes on every quarter-hour (scheduled from __init__)."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=None)
        # Prices for a market date never change once published: cache them.
        self._price_cache: dict[date, list[dict]] = {}
        self._committed: dict[int, Window] = {}
        self._holidays: dict[tuple[str, int], set[date]] = {}

    @property
    def settings(self) -> dict:
        return settings_of(self.config_entry)

    def _nordpool_entry(self) -> ConfigEntry | None:
        entries = self.hass.config_entries.async_loaded_entries(NORDPOOL_DOMAIN)
        return entries[0] if entries else None

    async def _fetch_market_date(self, entry: ConfigEntry, area: str, d: date) -> list[dict]:
        if self._price_cache.get(d):
            return self._price_cache[d]
        try:
            resp = await self.hass.services.async_call(
                NORDPOOL_DOMAIN,
                "get_prices_for_date",
                {"config_entry": entry.entry_id, "date": d.isoformat(), "areas": [area]},
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.debug("Nord Pool had no prices for %s yet: %s", d, err)
            return []
        rows = list((resp or {}).get(area) or [])
        if rows:
            self._price_cache[d] = rows
        return rows

    async def _holiday_dates(self, country: str, years: set[int]) -> set[date]:
        out: set[date] = set()
        missing = [y for y in years if (country, y) not in self._holidays]
        if missing:
            def load() -> dict[int, set[date]]:
                import holidays  # heavy import: keep it off the event loop

                result: dict[int, set[date]] = {}
                for y in missing:
                    try:
                        result[y] = set(holidays.country_holidays(country, years=y).keys())
                    except (NotImplementedError, KeyError):
                        result[y] = set()
                return result

            for y, dates in (await self.hass.async_add_executor_job(load)).items():
                self._holidays[(country, y)] = dates
        for y in years:
            out |= self._holidays.get((country, y), set())
        return out

    async def _solar(self, entry_id: str) -> dict[datetime, float] | None:
        """Quarter-hour watts from Forecast.Solar, or None if unavailable."""
        if self.hass.config_entries.async_get_entry(entry_id) is None:
            return None
        if self.hass.services.has_service(FORECAST_SOLAR_DOMAIN, "get_forecast"):
            try:
                resp = await self.hass.services.async_call(
                    FORECAST_SOLAR_DOMAIN,
                    "get_forecast",
                    {"config_entry": entry_id, "resolution": "hourly"},
                    blocking=True,
                    return_response=True,
                )
                return quarter_watts((resp or {}).get("wh_period") or {})
            except HomeAssistantError as err:
                _LOGGER.debug("Forecast.Solar get_forecast failed: %s", err)
                return None
        # Older HA: the energy-dashboard hook (hourly Wh).
        try:
            from homeassistant.components.forecast_solar.energy import async_get_solar_forecast

            data = await async_get_solar_forecast(self.hass, entry_id)
        except Exception as err:  # noqa: BLE001 - optional source, never fatal
            _LOGGER.debug("Forecast.Solar energy hook failed: %s", err)
            return None
        return quarter_watts((data or {}).get("wh_hours") or {})

    async def _async_update_data(self) -> WattWindowData:
        s = self.settings
        np_entry = self._nordpool_entry()
        if np_entry is None:
            raise UpdateFailed("The Nord Pool integration is not set up")
        area = s[CONF_AREA]
        currency = np_entry.data.get("currency", "EUR")
        tz = self.hass.config.time_zone
        now = dt_util.utcnow()
        today = dt_util.as_local(now).date()

        # Yesterday + today cover today's local day in zones at or ahead of CET
        # (every Nord Pool area). Tomorrow's auction is published around
        # 12:45 CET: don't ask for it before 10:30 UTC, and a published date is
        # cached forever, so a normal day costs a handful of calls.
        wanted = [today - timedelta(days=1), today]
        if now.hour * 60 + now.minute >= 10 * 60 + 30 or today + timedelta(days=1) in self._price_cache:
            wanted.append(today + timedelta(days=1))
        responses = []
        for d in wanted:
            rows = await self._fetch_market_date(np_entry, area, d)
            if rows:
                responses.append({area: rows})
        for old in [d for d in self._price_cache if d < today - timedelta(days=1)]:
            del self._price_cache[old]
        if not responses:
            raise UpdateFailed(f"No Nord Pool prices for area {area}")

        day_start = dt_util.as_utc(dt_util.start_of_local_day(today))
        horizon_end = day_start + timedelta(days=3)
        spots = nordpool.splice(
            [iv for r in responses for iv in nordpool.parse_response(r, area)], day_start, horizon_end
        )
        years = {today.year, (today + timedelta(days=2)).year}
        holidays = await self._holiday_dates(s.get(CONF_COUNTRY) or "", years) if s.get(CONF_COUNTRY) else set()

        warnings: list[str] = []
        solar_entry = s.get(CONF_SOLAR_ENTRY)
        solar_w = None
        if solar_entry:
            solar_w = await self._solar(solar_entry)
            if solar_w is None:
                warnings.append("Solar forecast unavailable: windows use grid prices only.")

        tariff = {**s[CONF_TARIFF], "timezone": tz}
        quarters = price_quarters(spots, tariff, holidays, solar_w)
        load_w = float(s.get(CONF_LOAD_W, DEFAULT_LOAD_W))
        base_w = float(s.get(CONF_BASE_LOAD_W, DEFAULT_BASE_LOAD_W))
        current = quarter_floor(now)

        windows: dict[int, Window | None] = {}
        for minutes in s.get(CONF_WINDOWS) or DEFAULT_WINDOWS:
            minutes = int(minutes)
            kept = self._committed.get(minutes)
            if kept is not None and kept.contains(now):
                windows[minutes] = kept  # never move a window that has started
                continue
            w = cheapest_window(quarters, timedelta(minutes=minutes), load_w, base_w, earliest=current)
            windows[minutes] = w
            if w is not None:
                self._committed[minutes] = w
            else:
                self._committed.pop(minutes, None)

        return WattWindowData(
            quarters=quarters,
            windows=windows,
            now=now,
            currency=currency,
            prices_until=quarters[-1].end if quarters else None,
            solar_configured=bool(solar_entry),
            solar_ok=solar_w is not None,
            warnings=warnings,
        )
