"""Heat pump advice: use the house (or hot-water tank) as a battery.

Heat stored now is heat you don't buy later, but only for a few hours: a warm
house and a hot tank lose it again. So the advice only looks as far ahead as
the heat keeps (``store_hours``), and only shifts when the saving beats a
margin that covers those losses.

* **Hold back** when a cheaper stretch comes within the next ``store_hours``,
  cheaper by at least the margin: waiting pays, and the house can coast.
* **Boost** when there's spare solar now, or when now is cheaper than the
  average of the next ``store_hours`` by at least the margin: store heat now.
* **Normal** otherwise.

Hold back wins over boost: if now is cheap but a clearly cheaper stretch is
coming, heat then (when that stretch arrives it is itself "boost").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .windows import QUARTER, Quarter

BOOST = "boost"
NORMAL = "normal"
HOLD_BACK = "hold_back"


@dataclass(frozen=True)
class Advice:
    mode: str
    reason: str
    price_now: float | None = None
    average_ahead: float | None = None
    cheapest_ahead: float | None = None
    cheapest_at: datetime | None = None
    hours_ahead_known: float = 0.0


def _hour_averages(prices: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    """Rolling one-hour averages, so a single cheap quarter doesn't drive the advice."""
    n = 4
    if len(prices) < n:
        return [(prices[0][0], sum(p for _, p in prices) / len(prices))] if prices else []
    return [(prices[i][0], sum(p for _, p in prices[i : i + n]) / n) for i in range(len(prices) - n + 1)]


def advise(
    quarters: list[Quarter],
    now: datetime,
    power_w: float,
    base_load_w: float,
    store_hours: float,
    margin: float,
    spare_enough: bool = False,
) -> Advice:
    """``margin`` is per kWh, in the price currency (0.02 = 2 c/kWh)."""
    current = next((q for q in quarters if q.start <= now < q.end), None)
    if current is None:
        return Advice(NORMAL, "No price for now yet")
    price_now = current.effective_price(power_w, base_load_w)
    if spare_enough:
        return Advice(BOOST, "Spare solar right now", price_now)

    horizon = current.end + timedelta(hours=store_hours)
    ahead = [(q.start, q.effective_price(power_w, base_load_w)) for q in quarters if current.end <= q.start < horizon]
    known = len(ahead) * QUARTER.total_seconds() / 3600
    if known < 1:
        return Advice(NORMAL, "Not enough prices known ahead", price_now, hours_ahead_known=known)
    average = sum(p for _, p in ahead) / len(ahead)
    cheapest_at, cheapest = min(_hour_averages(ahead), key=lambda tp: (tp[1], tp[0]))

    common = dict(price_now=price_now, average_ahead=average, cheapest_ahead=cheapest,
                  cheapest_at=cheapest_at, hours_ahead_known=known)
    if price_now >= cheapest + margin:
        return Advice(HOLD_BACK, "A cheaper stretch is coming", **common)
    if price_now <= average - margin:
        return Advice(BOOST, "Cheaper now than the hours ahead", **common)
    return Advice(NORMAL, "No saving worth shifting for", **common)


class StickyMode:
    """Keeps a mode for at least ``min_duration`` so the compressor isn't cycled."""

    def __init__(self, min_duration: timedelta) -> None:
        self.min_duration = min_duration
        self.mode: str | None = None
        self.since: datetime | None = None

    def update(self, now: datetime, wanted: str) -> str:
        if self.mode is None or (wanted != self.mode and now - self.since >= self.min_duration):
            if wanted != self.mode:
                self.mode, self.since = wanted, now
        return self.mode
