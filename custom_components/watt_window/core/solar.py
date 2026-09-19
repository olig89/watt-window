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


# --- Estimating output from a weather forecast (Open-Meteo) -------------------

# Share of rated panel power that reaches the socket in real conditions: heat,
# wiring, inverter and dirt losses. 0.85 is a common planning figure; tuning
# against real production (later) replaces it per plane.
DEFAULT_PERFORMANCE_RATIO = 0.85

# Defaults for a plane nobody has described yet: a typical pitched roof facing
# south. Good enough to start; the user refines them when they know better.
DEFAULT_TILT = 35
DEFAULT_DIRECTION = 180  # compass degrees: 90 east, 180 south, 270 west


def open_meteo_azimuth(compass_degrees: float) -> float:
    """Compass bearing (0 N, 90 E, 180 S, 270 W) -> Open-Meteo's (0 S, -90 E, 90 W)."""
    a = (float(compass_degrees) - 180.0) % 360.0
    return a - 360.0 if a > 180.0 else a


def plane_watts(
    times: list[int],
    tilted_irradiance: list[float | None],
    kwp: float,
    performance_ratio: float = DEFAULT_PERFORMANCE_RATIO,
    step: timedelta = QUARTER,
) -> dict[datetime, float]:
    """Open-Meteo sunlight on the plane (W/m2) -> expected watts per quarter-hour.

    Open-Meteo stamps each value at the END of the interval it averages
    ("preceding 15 minutes mean"), so a value stamped 10:15 belongs to the
    quarter starting 10:00. Rated panel power is defined at 1000 W/m2.
    Hourly input (``step`` 1 h) is spread over its four quarters.
    """
    out: dict[datetime, float] = {}
    steps = max(1, int(step / QUARTER))
    for ts, g in zip(times, tilted_irradiance):
        if g is None:
            continue
        start = datetime.fromtimestamp(int(ts), tz=timezone.utc) - step
        watts = max(0.0, float(g)) / 1000.0 * float(kwp) * 1000.0 * performance_ratio
        for i in range(steps):
            out[start + i * QUARTER] = watts
    return out


def add_watts(*parts: Mapping[datetime, float]) -> dict[datetime, float]:
    total: dict[datetime, float] = {}
    for part in parts:
        for t, w in part.items():
            total[t] = total.get(t, 0.0) + w
    return total
