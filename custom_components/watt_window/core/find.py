"""Find the cheapest window on request: any length, with optional time limits.

Behind the ``watt_window.find_window`` action. Times of day ("finish by
07:00") mean their next occurrence after now, in the house's time zone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta, tzinfo

from .windows import QUARTER, Quarter, Window, cheapest_window, window_at

# Why no window was found, for the action's response.
TOO_SHORT = "too_short"  # the limits leave less time than the window needs
NO_PRICES = "no_prices"  # the time exists, but its prices aren't published yet


@dataclass(frozen=True)
class FindResult:
    window: Window | None
    start_now: Window | None  # the same length starting this quarter, to compare with
    earliest: datetime
    latest_end: datetime | None
    # True when every price the limits allow is already known, so a later
    # price publication can't move the answer.
    settled: bool
    reason: str | None = None


def next_at(value: time | datetime, now: datetime, tz: tzinfo) -> datetime:
    """A datetime as given (local time if naive); a time of day as its next
    occurrence after ``now``."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=tz)
    local = now.astimezone(tz)
    at = datetime.combine(local.date(), value, tzinfo=tz)
    if at <= local:
        at = datetime.combine(local.date() + timedelta(days=1), value, tzinfo=tz)
    return at


def quarter_ceil(t: datetime) -> datetime:
    floor = t.replace(minute=t.minute - t.minute % 15, second=0, microsecond=0)
    return floor if floor == t else floor + QUARTER


def find_window(
    quarters: Sequence[Quarter],
    now: datetime,
    length: timedelta,
    load_w: float,
    base_load_w: float,
    start_after: datetime | None = None,
    start_by: datetime | None = None,
    finish_by: datetime | None = None,
) -> FindResult:
    """The cheapest window of ``length`` from this quarter on, within the limits.

    The current quarter counts as "now": a window starting in it starts at once.
    """
    current = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
    earliest = max(current, quarter_ceil(start_after)) if start_after else current
    latest_end = finish_by
    if start_by is not None:
        end_by_start = start_by + length
        latest_end = end_by_start if latest_end is None else min(latest_end, end_by_start)
    known_until = quarters[-1].end if quarters else None
    settled = known_until is not None and latest_end is not None and latest_end <= known_until
    start_now = window_at(quarters, current, length, load_w, base_load_w)

    if latest_end is not None and latest_end - earliest < length:
        return FindResult(None, start_now, earliest, latest_end, settled, TOO_SHORT)
    w = cheapest_window(
        quarters, length, load_w, base_load_w,
        earliest=earliest, latest_end=latest_end, latest_start=start_by,
    )
    return FindResult(w, start_now, earliest, latest_end, settled, None if w else NO_PRICES)
