"""Nord Pool service responses -> a local-day price horizon.

Two traps this module exists to handle:

1. **Units.** ``nordpool.get_prices_for_date`` returns EUR/**MWh**. The HA
   sensors report EUR/kWh. Everything leaving this module is EUR/kWh.

2. **A market "date" is not a local calendar day.** Nord Pool market days run
   midnight-to-midnight CET/CEST. In Estonia (one hour ahead) ``date:
   2026-09-18`` covers 01:00 -> 01:00 local, so the local hour 00:00-01:00
   comes from the *previous* market date. Responses are therefore always
   spliced by timestamp, never by date.

Getting (2) wrong shifts every price by an hour and raises no error, which is
why it is the highest-value fixture in the test suite.

Settlement is 15-minute (96 intervals on a normal day, 92/100 across DST).
Nothing here averages to hourly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

DEFAULT_TZ = "UTC"

# The day-ahead auction closes at 12:00 CET and results are published at about
# 12:45 CET. The market day runs midnight to midnight CET.
MARKET_TZ = "Europe/Berlin"
PUBLISH_TIME = (12, 45)


def next_publication(now: datetime, published_tomorrow: bool) -> datetime:
    """When the next market day's prices should appear (aware datetime, UTC)."""
    zone = ZoneInfo(MARKET_TZ)
    local = now.astimezone(zone)
    day = local.date() + timedelta(days=1 if published_tomorrow else 0)
    return datetime.combine(day, time(*PUBLISH_TIME), tzinfo=zone).astimezone(timezone.utc)


MWH_PER_KWH = 1 / 1000


@dataclass(frozen=True)
class SpotInterval:
    start: datetime  # aware, UTC
    end: datetime
    spot: float  # EUR/kWh


def _parse_ts(s: str) -> datetime:
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError(f"naive timestamp from Nord Pool: {s!r}")
    return t.astimezone(ZoneInfo("UTC"))


def parse_response(response: Mapping, area: str = "EE") -> list[SpotInterval]:
    """Parse one ``get_prices_for_date`` response ({area: [{start,end,price}]})."""
    rows = response.get(area)
    if rows is None:
        raise KeyError(f"area {area!r} missing from Nord Pool response")
    return [
        SpotInterval(_parse_ts(r["start"]), _parse_ts(r["end"]), r["price"] * MWH_PER_KWH)
        for r in rows
    ]


def market_dates_for_local_day(local_day: date) -> tuple[date, date, date]:
    """Market dates whose responses together cover ``local_day`` in any zone.

    A zone ahead of CET needs the previous market date for its first hours
    (Estonia: 00:00-01:00 local), a zone behind it needs the next one. Asking
    for all three and splicing by timestamp is correct everywhere.
    """
    return (local_day - timedelta(days=1), local_day, local_day + timedelta(days=1))


def local_day_bounds(local_day: date, tz: str = DEFAULT_TZ) -> tuple[datetime, datetime]:
    z = ZoneInfo(tz)
    start = datetime.combine(local_day, time(0), tzinfo=z)
    end = datetime.combine(local_day + timedelta(days=1), time(0), tzinfo=z)
    return start.astimezone(ZoneInfo("UTC")), end.astimezone(ZoneInfo("UTC"))


def splice(
    intervals: Iterable[SpotInterval], start: datetime, end: datetime
) -> list[SpotInterval]:
    """Intervals with start in [start, end), deduplicated, sorted by time.

    Overlapping responses are expected (two market dates for one local day).
    A duplicate start with a *different* price means the inputs disagree, and
    that is raised rather than silently resolved.
    """
    by_start: dict[datetime, SpotInterval] = {}
    for iv in intervals:
        if not (start <= iv.start < end):
            continue
        seen = by_start.get(iv.start)
        if seen is not None and seen != iv:
            raise ValueError(f"conflicting prices for interval starting {iv.start}")
        by_start[iv.start] = iv
    return [by_start[k] for k in sorted(by_start)]


def local_day(
    responses: Sequence[Mapping], day: date, tz: str = DEFAULT_TZ, area: str = "EE"
) -> list[SpotInterval]:
    start, end = local_day_bounds(day, tz)
    parsed = [iv for r in responses for iv in parse_response(r, area)]
    return splice(parsed, start, end)


def gaps(intervals: Sequence[SpotInterval], start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Uncovered spans in [start, end). Empty list means the horizon is complete.

    The planner uses this to shorten its horizon (tomorrow's prices are not
    published until ~13:00 CET) instead of planning over missing data.
    """
    out, cursor = [], start
    for iv in intervals:
        if iv.start > cursor:
            out.append((cursor, iv.start))
        cursor = max(cursor, iv.end)
    if cursor < end:
        out.append((cursor, end))
    return out
