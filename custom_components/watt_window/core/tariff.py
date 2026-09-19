"""Timestamp -> network tariff period.

Pure Python: no Home Assistant imports, no entity IDs, no clock reads. Every
function takes the instant it is asked about, so the same code labels "now"
for a live sensor and every quarter-hour of a two-day horizon.

All boundaries are evaluated on the LOCAL wall clock (the config's timezone),
never UTC, so daylight-saving days fall out correctly.

Plans:

* ``flat``      -- one rate all the time. Key: ``flat``.
* ``day_night`` -- a day rate between two hours, night otherwise. Weekends
  and public holidays can be all-night. Keys: ``day`` / ``night``.
* ``vork5``     -- Elektrilevi (Estonia) Võrk 5: day/night plus winter
  (Nov-Mar) weekday and weekend peaks carved out of them. Keys: ``day``,
  ``night``, ``weekday_peak``, ``weekend_peak``.

Holidays arrive as plain data (a collection of dates), never looked up here.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import date, datetime, time
from enum import Enum
from zoneinfo import ZoneInfo

DEFAULT_TZ = "UTC"
WINTER_MONTHS = frozenset({11, 12, 1, 2, 3})


class Period(str, Enum):
    DAY = "day"
    NIGHT = "night"


class Bucket(str, Enum):
    NIGHT = "night"
    DAY = "day"
    WEEKDAY_PEAK = "weekday_peak"
    WEEKEND_PEAK = "weekend_peak"

    @property
    def parent(self) -> Period:
        """The day/night period this bucket is carved out of."""
        if self in (Bucket.DAY, Bucket.WEEKDAY_PEAK):
            return Period.DAY
        return Period.NIGHT


def _local(t: datetime, tz: str) -> datetime:
    if t.tzinfo is None:
        raise ValueError("naive datetime passed to tariff; pass an aware timestamp")
    return t.astimezone(ZoneInfo(tz))


def _in(t: time, start: time, end: time) -> bool:
    """Half-open [start, end) on the wall clock."""
    return start <= t < end


def is_workday(d: date, holidays: Collection[date] = ()) -> bool:
    return d.weekday() < 5 and d not in holidays


def day_night_period(
    t: datetime,
    holidays: Collection[date] = (),
    tz: str = DEFAULT_TZ,
    day_start: int = 7,
    day_end: int = 22,
    weekends_night: bool = True,
    holidays_night: bool = True,
) -> Period:
    lt = _local(t, tz)
    d = lt.date()
    if weekends_night and d.weekday() >= 5:
        return Period.NIGHT
    if holidays_night and d in holidays:
        return Period.NIGHT
    if _in(lt.time(), time(day_start), time(day_end)):
        return Period.DAY
    return Period.NIGHT


def vork2_period(
    t: datetime, holidays: Collection[date] = (), tz: str = "Europe/Tallinn"
) -> Period:
    """Elektrilevi Võrk 2/4: day 07-22 on workdays, night otherwise."""
    return day_night_period(t, holidays, tz)


def vork5_bucket(
    t: datetime, holidays: Collection[date] = (), tz: str = "Europe/Tallinn"
) -> Bucket:
    lt = _local(t, tz)
    d, clock = lt.date(), lt.time()
    winter = lt.month in WINTER_MONTHS
    evening = _in(clock, time(16), time(20))

    if winter and is_workday(d, holidays) and (_in(clock, time(9), time(12)) or evening):
        return Bucket.WEEKDAY_PEAK
    # Sat/Sun only, not "non-workday": a weekday holiday evening is plain night.
    if winter and d.weekday() >= 5 and evening:
        return Bucket.WEEKEND_PEAK
    if vork2_period(t, holidays, tz) is Period.NIGHT:
        return Bucket.NIGHT
    return Bucket.DAY


PLANS = ("flat", "day_night", "vork5")


def tariff_key(t: datetime, cfg: Mapping, holidays: Collection[date] = ()) -> str:
    """The network rate key in force at ``t`` for a tariff config dict."""
    plan = cfg.get("network_plan", "flat")
    tz = cfg.get("timezone", DEFAULT_TZ)
    if plan == "flat":
        return "flat"
    if plan == "day_night":
        return day_night_period(
            t,
            holidays,
            tz,
            day_start=int(cfg.get("day_start", 7)),
            day_end=int(cfg.get("day_end", 22)),
            weekends_night=bool(cfg.get("weekends_night", True)),
            holidays_night=bool(cfg.get("holidays_night", True)),
        ).value
    if plan == "vork5":
        return vork5_bucket(t, holidays, tz).value
    raise ValueError(f"unknown network_plan {plan!r}; expected one of {PLANS}")


def rate_keys(plan: str) -> tuple[str, ...]:
    """The network rates a plan needs."""
    return {
        "flat": ("flat",),
        "day_night": ("day", "night"),
        "vork5": ("day", "night", "weekday_peak", "weekend_peak"),
    }[plan]
