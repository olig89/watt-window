from datetime import date, datetime, timedelta

import pytest

from custom_components.watt_window.core import nordpool as np
from tests.core.conftest import TLL, UTC, market_day


def _local(iv):
    return iv.start.astimezone(TLL)


def test_live_shape_market_date_is_01_to_01_local():
    # Verified live: date 2026-09-18 -> 2026-09-17T22:00Z .. 2026-09-18T22:00Z.
    ivs = np.parse_response(market_day(date(2026, 9, 18)))
    assert ivs[0].start == datetime(2026, 9, 17, 22, 0, tzinfo=UTC)
    assert ivs[-1].end == datetime(2026, 9, 18, 22, 0, tzinfo=UTC)
    assert _local(ivs[0]).hour == 1


def test_units_mwh_to_kwh():
    resp = {"EE": [{"start": "2026-09-17T22:00:00Z", "end": "2026-09-17T22:15:00Z", "price": 94.30}]}
    assert np.parse_response(resp)[0].spot == pytest.approx(0.0943)


@pytest.mark.parametrize("day", [date(2026, 9, 18), date(2026, 1, 15)])  # summer and winter time
def test_cet_splice_local_midnight_comes_from_previous_market_date(day):
    prev, cur, _next = np.market_dates_for_local_day(day)
    ivs = np.local_day([market_day(prev), market_day(cur)], day, tz="Europe/Tallinn")

    assert len(ivs) == 96
    assert _local(ivs[0]).replace(tzinfo=None) == datetime.combine(day, datetime.min.time())
    # The first local hour is priced from the PREVIOUS market date's response...
    prev_starts = {iv.start for iv in np.parse_response(market_day(prev))}
    cur_starts = {iv.start for iv in np.parse_response(market_day(cur))}
    assert all(iv.start in prev_starts and iv.start not in cur_starts for iv in ivs[:4])
    assert all(iv.start in cur_starts for iv in ivs[4:])
    # ...and every price belongs to its exact UTC instant, not one shifted an hour.
    for iv in ivs:
        u = iv.start
        assert iv.spot == pytest.approx((u.day * 10000 + u.hour * 100 + u.minute) / 1000)


def test_current_market_date_alone_misses_the_first_local_hour():
    day = date(2026, 9, 18)
    ivs = np.local_day([market_day(day)], day, tz="Europe/Tallinn")
    start, end = np.local_day_bounds(day, tz="Europe/Tallinn")
    assert np.gaps(ivs, start, end) == [(start, start + timedelta(hours=1))]


def test_quarter_hours_are_kept_not_averaged():
    quarters = [94.30, 64.34, 45.75, 26.00]  # verified intra-hour spread

    def price(t):
        return quarters[t.minute // 15]

    ivs = np.parse_response(market_day(date(2026, 9, 18), price=price))
    assert [iv.spot * 1000 for iv in ivs[:4]] == pytest.approx(quarters)


@pytest.mark.parametrize(
    "day, n",
    [(date(2026, 3, 29), 92), (date(2026, 10, 25), 100)],  # spring forward / fall back
)
def test_dst_days_have_92_or_100_intervals(day, n):
    prev, cur, _next = np.market_dates_for_local_day(day)
    ivs = np.local_day([market_day(prev), market_day(cur)], day, tz="Europe/Tallinn")
    start, end = np.local_day_bounds(day, tz="Europe/Tallinn")
    assert len(ivs) == n
    assert np.gaps(ivs, start, end) == []
    assert (end - start) == timedelta(minutes=15 * n)


def test_conflicting_duplicate_raises():
    a = market_day(date(2026, 9, 18))
    b = market_day(date(2026, 9, 18), price=lambda t: 1.0)
    with pytest.raises(ValueError, match="conflicting"):
        np.local_day([a, b], date(2026, 9, 18))


def test_missing_tomorrow_is_a_gap_not_a_crash():
    # At noon, before tomorrow's auction is published.
    today = date(2026, 9, 18)
    tomorrow = today + timedelta(1)
    ivs = np.local_day([market_day(today - timedelta(1)), market_day(today)], tomorrow, tz="Europe/Tallinn")
    start, end = np.local_day_bounds(tomorrow, tz="Europe/Tallinn")
    # Only tomorrow's 00:00-01:00 local is known (it belongs to market date `today`).
    assert len(ivs) == 4
    assert np.gaps(ivs, start, end) == [(start + timedelta(hours=1), end)]


def test_missing_area_raises():
    with pytest.raises(KeyError):
        np.parse_response({"LV": []})


def test_zone_behind_cet_needs_the_next_market_date():
    # London is an hour behind CET: its local day ends inside the NEXT market date.
    day = date(2026, 9, 18)
    dates = np.market_dates_for_local_day(day)
    ivs = np.local_day([market_day(d) for d in dates], day, tz="Europe/London")
    start, end = np.local_day_bounds(day, tz="Europe/London")
    assert len(ivs) == 96 and np.gaps(ivs, start, end) == []
    two = np.local_day([market_day(d) for d in dates[:2]], day, tz="Europe/London")
    assert np.gaps(two, start, end) == [(end - timedelta(hours=1), end)]



def test_next_publication_is_1245_cet_today_or_tomorrow():
    from datetime import datetime, timezone
    from custom_components.watt_window.core.nordpool import next_publication

    morning = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)
    assert next_publication(morning, False) == datetime(2026, 9, 21, 10, 45, tzinfo=timezone.utc)
    assert next_publication(morning, True) == datetime(2026, 9, 22, 10, 45, tzinfo=timezone.utc)
    winter = datetime(2026, 12, 1, 8, 0, tzinfo=timezone.utc)
    assert next_publication(winter, False) == datetime(2026, 12, 1, 11, 45, tzinfo=timezone.utc)
