"""Daytime and overnight periods, for "cheapest window during the day / night".

The day runs from ``day_start`` to ``day_end`` (local hours, 0-24); the night is
the rest. A window of a given length is searched in the current period if enough
of it is left, otherwise in the next one of that kind.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

KINDS = ("day", "night")


def period_for(
    now: datetime,
    tz: str,
    day_start: int,
    day_end: int,
    kind: str,
    length: timedelta,
) -> tuple[datetime, datetime] | None:
    """(start, end) of the current-or-next ``kind`` period with room for ``length``."""
    zone = ZoneInfo(tz)
    today = now.astimezone(zone).date()
    candidates = []
    for offset in range(-1, 3):
        midnight = datetime.combine(today + timedelta(days=offset), time(0), tzinfo=zone)
        if kind == "day":
            start, end = midnight + timedelta(hours=day_start), midnight + timedelta(hours=day_end)
        else:
            start, end = midnight + timedelta(hours=day_end), midnight + timedelta(days=1, hours=day_start)
        if end > start:
            candidates.append((start, end))
    for start, end in sorted(candidates):
        if end - max(start, now) >= length:
            return start, end
    return None
