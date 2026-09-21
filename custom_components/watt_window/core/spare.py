"""Spare solar right now: an estimate, smoothed, with on/off delays.

Two kinds of system need different reasoning:

* **Exporting** systems send spare power to the grid, so the spare is simply
  what's going out (measured).
* **Zero-export** systems hold the panels back instead: panel output follows
  the house, so the spare never shows up in any reading. What does show is the
  grid sitting at about zero while the panels produce - that means they're
  being held back and could give more. How much more is a guess: the solar
  forecast for right now, minus what they're actually making.

Readings are smoothed over a few minutes, and the on/off decision waits a
little before changing, so a passing cloud doesn't flick it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

# Basis values, reported with every estimate so nobody mistakes a guess for a measurement.
MEASURED_EXPORT = "measured_export"  # exporting system: what's going to the grid
FORECAST_GAP = "forecast_minus_production"  # zero-export, held back: forecast - actual
HELD_BACK_NO_FORECAST = "held_back_amount_unknown"  # zero-export, held back, no forecast to size it
IMPORTING = "importing"  # the house is drawing from the grid: panels are flat out
NO_SOLAR = "no_solar"  # panels (nearly) idle

MIN_SOLAR_W = 50.0  # below this the panels are effectively off


@dataclass(frozen=True)
class SpareEstimate:
    watts: float | None  # None: some spare exists but its size is unknown
    basis: str
    held_back: bool  # zero-export only: the inverter is limiting the panels

    @property
    def is_guess(self) -> bool:
        return self.basis in (FORECAST_GAP, HELD_BACK_NO_FORECAST)


def estimate_spare(
    grid_import_w: float,
    solar_w: float,
    forecast_w: float | None,
    can_export: bool,
    near_zero_w: float = 150.0,
) -> SpareEstimate:
    """``grid_import_w`` is positive when importing, negative when exporting."""
    if solar_w < MIN_SOLAR_W:
        return SpareEstimate(0.0, NO_SOLAR, False)
    exporting = max(-grid_import_w, 0.0)
    if can_export:
        return SpareEstimate(exporting, MEASURED_EXPORT, False)
    if grid_import_w > near_zero_w:
        return SpareEstimate(0.0, IMPORTING, False)
    if forecast_w is None:
        return SpareEstimate(None, HELD_BACK_NO_FORECAST, True)
    # Brief export blips (a load switching off before the inverter reacts) are spare too.
    return SpareEstimate(max(forecast_w - solar_w, 0.0) + exporting, FORECAST_GAP, True)


class TimeAverage:
    """Time-weighted average of a reading over the last ``window``.

    Each sample holds until the next one, which is how power sensors behave
    (they report on change, not on a clock).
    """

    def __init__(self, window: timedelta) -> None:
        self.window = window
        self._samples: deque[tuple[datetime, float]] = deque()

    def add(self, t: datetime, value: float) -> None:
        self._samples.append((t, float(value)))

    def value(self, now: datetime) -> float | None:
        start = now - self.window
        # Keep the last sample from before the window: it's what was in force at its start.
        while len(self._samples) >= 2 and self._samples[1][0] <= start:
            self._samples.popleft()
        if not self._samples:
            return None
        total = weight = 0.0
        for i, (t, v) in enumerate(self._samples):
            seg_start = max(t, start)
            seg_end = self._samples[i + 1][0] if i + 1 < len(self._samples) else now
            seconds = (seg_end - seg_start).total_seconds()
            if seconds > 0:
                total += v * seconds
                weight += seconds
        if weight == 0:
            return self._samples[-1][1]
        return total / weight


class DelayedSwitch:
    """On once a condition has held for ``on_after``; off once it has failed for ``off_after``."""

    def __init__(self, on_after: timedelta, off_after: timedelta) -> None:
        self.on_after = on_after
        self.off_after = off_after
        self.is_on = False
        self._since: datetime | None = None  # when the condition started disagreeing with is_on

    def update(self, now: datetime, condition: bool) -> bool:
        if condition == self.is_on:
            self._since = None
            return self.is_on
        if self._since is None:
            self._since = now
        if now - self._since >= (self.on_after if condition else self.off_after):
            self.is_on = condition
            self._since = None
        return self.is_on
