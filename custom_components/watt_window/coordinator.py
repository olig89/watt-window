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
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BASE_LOAD_W,
    CONF_CAN_EXPORT,
    CONF_COUNTRY,
    CONF_DAY_END,
    CONF_DAY_START,
    CONF_LOAD_W,
    CONF_TARIFF,
    CONF_WINDOWS,
    DEFAULT_BASE_LOAD_W,
    DEFAULT_DAY_END,
    DEFAULT_DAY_START,
    DEFAULT_LOAD_W,
    DEFAULT_WINDOWS,
    DOMAIN,
    settings_of,
)
from .core.periods import KINDS, period_for
from .core.windows import Quarter, Window, cheapest_window, price_quarters
from .sources import PriceSourceError, price_source
from .sources.solar import solar_source

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
    # "day" / "night" -> minutes -> cheapest window inside that period.
    period_windows: dict[str, dict[int, Window | None]] = field(default_factory=dict)
    # Honest horizon: prices beyond ``prices_until`` don't exist yet anywhere.
    next_prices_at: datetime | None = None
    price_source: str = ""
    solar_title: str | None = None
    solar_credit: str | None = None
    periods: dict[str, dict[int, tuple[datetime, datetime] | None]] = field(default_factory=dict)

    def window(self, kind: str, minutes: int) -> Window | None:
        if kind == "any":
            return self.windows.get(minutes)
        return self.period_windows.get(kind, {}).get(minutes)

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
        # Owned here so it survives refreshes; the price source decides what goes in it.
        self._price_cache: dict = {}
        self._solar_cache: dict = {}
        self._committed: dict[tuple[str, int], Window] = {}
        self._holidays: dict[tuple[str, int], set[date]] = {}

    @property
    def settings(self) -> dict:
        return settings_of(self.config_entry)

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

    async def _async_update_data(self) -> WattWindowData:
        s = self.settings
        tz = self.hass.config.time_zone
        now = dt_util.utcnow()
        today = dt_util.as_local(now).date()
        local_midnight = dt_util.as_utc(dt_util.start_of_local_day(today))
        try:
            source = price_source(self.hass, s, self._price_cache)
            fetched = await source.async_fetch(now, local_midnight, local_midnight + timedelta(days=3))
        except PriceSourceError as err:
            raise UpdateFailed(str(err)) from err
        spots, currency = fetched.spots, fetched.currency
        years = {today.year, (today + timedelta(days=2)).year}
        holidays = await self._holiday_dates(s.get(CONF_COUNTRY) or "", years) if s.get(CONF_COUNTRY) else set()

        warnings: list[str] = []
        solar = solar_source(self.hass, s, self._solar_cache)
        solar_w = await solar.async_watts(now) if solar else None
        if solar:
            warnings += solar.warnings
            if solar_w is None:
                warnings.append("Solar forecast unavailable: Watt Windows use grid prices only.")

        tariff = {**s[CONF_TARIFF], "timezone": tz}
        quarters = price_quarters(spots, tariff, holidays, solar_w, bool(s.get(CONF_CAN_EXPORT, True)))
        load_w = float(s.get(CONF_LOAD_W, DEFAULT_LOAD_W))
        base_w = float(s.get(CONF_BASE_LOAD_W, DEFAULT_BASE_LOAD_W))
        current = quarter_floor(now)

        def pick(key: tuple[str, int], earliest: datetime, latest_end: datetime | None) -> Window | None:
            kept = self._committed.get(key)
            if kept is not None and kept.contains(now):
                return kept  # never move a window that has started
            w = cheapest_window(
                quarters, timedelta(minutes=key[1]), load_w, base_w, earliest=earliest, latest_end=latest_end
            )
            if w is not None:
                self._committed[key] = w
            else:
                self._committed.pop(key, None)
            return w

        lengths = [int(m) for m in s.get(CONF_WINDOWS) or DEFAULT_WINDOWS]
        windows = {m: pick(("any", m), current, None) for m in lengths}
        day_start = int(s.get(CONF_DAY_START, DEFAULT_DAY_START))
        day_end = int(s.get(CONF_DAY_END, DEFAULT_DAY_END))
        period_windows: dict[str, dict[int, Window | None]] = {}
        periods: dict[str, dict[int, tuple[datetime, datetime] | None]] = {}
        for kind in KINDS:
            period_windows[kind], periods[kind] = {}, {}
            for m in lengths:
                span = period_for(now, tz, day_start, day_end, kind, timedelta(minutes=m))
                periods[kind][m] = span
                period_windows[kind][m] = pick((kind, m), max(current, span[0]), span[1]) if span else None

        return WattWindowData(
            quarters=quarters,
            windows=windows,
            now=now,
            currency=currency,
            prices_until=quarters[-1].end if quarters else None,
            solar_configured=solar is not None,
            solar_ok=solar_w is not None,
            warnings=warnings,
            period_windows=period_windows,
            periods=periods,
            next_prices_at=fetched.next_prices_at,
            price_source=source.name,
            solar_title=solar.title if solar else None,
            solar_credit=solar.credit if solar else None,
        )

    def settled(self, kind: str, minutes: int) -> bool:
        """True when every price the window could use is already published."""
        data = self.data
        if kind == "any" or data is None or data.prices_until is None:
            return False  # a later day could always be cheaper
        span = data.periods.get(kind, {}).get(minutes)
        return bool(span) and span[1] <= data.prices_until
