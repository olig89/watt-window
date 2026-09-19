"""Solar forecast -> average watts per quarter-hour.

Forecast.Solar reports energy per period (Wh produced during the period that
starts at each timestamp). We spread each period's energy evenly across the
quarter-hours inside it. Coarse, but honest: a forecast is not more precise
than the period it was made for.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

QUARTER = timedelta(minutes=15)


def _parse(ts: str | datetime) -> datetime:
    t = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError(f"naive timestamp in solar forecast: {ts!r}")
    return t.astimezone(timezone.utc)


def quarter_watts(wh_by_period: Mapping, period: timedelta = timedelta(hours=1)) -> dict[datetime, float]:
    """{period start: Wh} -> {quarter start (UTC): average W}.

    Periods not aligned to a quarter-hour are floored onto the grid.
    """
    out: dict[datetime, float] = {}
    hours = period.total_seconds() / 3600
    steps = max(1, int(period / QUARTER))
    for ts, wh in wh_by_period.items():
        start = _parse(ts)
        start = start - timedelta(minutes=start.minute % 15, seconds=start.second,
                                  microseconds=start.microsecond)
        watts = float(wh) / hours
        for i in range(steps):
            out[start + i * QUARTER] = watts
    return out
