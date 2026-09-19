"""Price each quarter-hour, then find the cheapest window of a given length.

A quarter's *effective* price is what running an extra load in it would cost:
the part of the load covered by forecast solar surplus costs only the export
price you give up; the rest is bought at the delivered import price.

    surplus   = max(solar_w - base_load_w, 0)
    covered   = min(load_w, surplus)
    effective = (covered * export + (load_w - covered) * import) / load_w
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .cost import export_price, import_price
from .nordpool import SpotInterval
from .tariff import tariff_key

QUARTER = timedelta(minutes=15)


@dataclass(frozen=True)
class Quarter:
    start: datetime
    end: datetime
    spot: float
    import_price: float
    export_price: float
    solar_w: float
    tariff_key: str

    def effective_price(self, load_w: float, base_load_w: float) -> float:
        if load_w <= 0:
            return self.import_price
        surplus = max(self.solar_w - base_load_w, 0.0)
        covered = min(load_w, surplus)
        return (covered * self.export_price + (load_w - covered) * self.import_price) / load_w


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    average_price: float  # effective, per kWh
    average_import_price: float
    solar_share: float  # 0..1 of the load expected to be covered by solar
    cost: float  # for the whole window at load_w

    def contains(self, t: datetime) -> bool:
        return self.start <= t < self.end


def price_quarters(
    spots: Sequence[SpotInterval],
    cfg: Mapping,
    holidays: Collection[date] = (),
    solar_w: Mapping[datetime, float] | None = None,
) -> list[Quarter]:
    """Delivered prices for each spot interval, split to quarter-hours.

    Hourly spot data is repeated across its four quarters, so windows can
    always start on any quarter-hour.
    """
    solar_w = solar_w or {}
    out: list[Quarter] = []
    for iv in spots:
        t = iv.start
        while t < iv.end:
            end = min(t + QUARTER, iv.end)
            out.append(
                Quarter(
                    start=t,
                    end=end,
                    spot=iv.spot,
                    import_price=import_price(iv.spot, t, cfg, holidays),
                    export_price=export_price(iv.spot, cfg),
                    solar_w=float(solar_w.get(t, 0.0)),
                    tariff_key=tariff_key(t, cfg, holidays),
                )
            )
            t = end
    return out


def cheapest_window(
    quarters: Sequence[Quarter],
    length: timedelta,
    load_w: float,
    base_load_w: float = 0.0,
    earliest: datetime | None = None,
) -> Window | None:
    """The contiguous run of quarters of total ``length`` with the lowest
    average effective price, starting at or after ``earliest``.

    Ties go to the earliest start. Returns None when the known horizon is
    shorter than the window.
    """
    n = max(1, round(length / QUARTER))
    qs = [q for q in quarters if earliest is None or q.start >= earliest]
    best: tuple[float, int] | None = None
    prices = [q.effective_price(load_w, base_load_w) for q in qs]
    for i in range(len(qs) - n + 1):
        run = qs[i : i + n]
        if any(b.start != a.end for a, b in zip(run, run[1:])):
            continue  # a gap in the data: not a real contiguous window
        avg = sum(prices[i : i + n]) / n
        if best is None or avg < best[0] - 1e-12:
            best = (avg, i)
    if best is None:
        return None
    avg, i = best
    run = qs[i : i + n]
    kwh_per_quarter = load_w / 1000 * 0.25
    covered = [
        min(load_w, max(q.solar_w - base_load_w, 0.0)) / load_w if load_w > 0 else 0.0
        for q in run
    ]
    return Window(
        start=run[0].start,
        end=run[-1].end,
        average_price=avg,
        average_import_price=sum(q.import_price for q in run) / n,
        solar_share=sum(covered) / n,
        cost=sum(p * kwh_per_quarter for p in prices[i : i + n]),
    )
