"""Shared fixtures: synthetic Nord Pool responses in the verified live shape.

Prices encode their own UTC start time (EUR/MWh = day*10000 + hour*100 +
minute), so any splice or timezone error shows up as a wrong number rather
than a plausible-looking one.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

UTC = ZoneInfo("UTC")
CET = ZoneInfo("Europe/Berlin")  # stands in for Nord Pool's CET/CEST market clock
TLL = ZoneInfo("Europe/Tallinn")


def encoded_price(t_utc: datetime) -> float:
    return t_utc.day * 10000 + t_utc.hour * 100 + t_utc.minute


def market_day(d: date, price=encoded_price, step_min: int = 15) -> dict:
    """What ``nordpool.get_prices_for_date`` returns for market date ``d``."""
    start = datetime.combine(d, time(0), tzinfo=CET).astimezone(UTC)
    end = datetime.combine(d + timedelta(days=1), time(0), tzinfo=CET).astimezone(UTC)
    rows, t = [], start
    while t < end:
        nxt = t + timedelta(minutes=step_min)
        rows.append({
            "start": t.isoformat().replace("+00:00", "Z"),
            "end": nxt.isoformat().replace("+00:00", "Z"),
            "price": price(t),
        })
        t = nxt
    return {"EE": rows}
