from datetime import date, datetime, timedelta

import pytest

from custom_components.watt_window.core.tariff import Bucket, Period, is_workday, vork2_period, vork5_bucket
from tests.core.conftest import TLL, UTC

INDEPENDENCE_DAY = date(2026, 2, 24)  # a Tuesday, Estonian public holiday


def L(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=TLL)


@pytest.mark.parametrize("t, want", [
    (L(2026, 9, 18, 6, 59), Period.NIGHT),   # Friday
    (L(2026, 9, 18, 7, 0), Period.DAY),
    (L(2026, 9, 18, 21, 45), Period.DAY),
    (L(2026, 9, 18, 22, 0), Period.NIGHT),
    (L(2026, 9, 19, 12, 0), Period.NIGHT),   # Saturday
    (L(2026, 9, 20, 12, 0), Period.NIGHT),   # Sunday
])
def test_vork2(t, want):
    assert vork2_period(t) is want


def test_vork2_holiday_is_night_all_day():
    assert vork2_period(L(2026, 2, 24, 12), holidays={INDEPENDENCE_DAY}) is Period.NIGHT
    assert vork2_period(L(2026, 2, 24, 12)) is Period.DAY


def test_boundaries_use_local_time_not_utc():
    # 04:30Z is 06:30 local in winter (night) and 07:30 local in summer (day).
    assert vork2_period(datetime(2026, 1, 15, 4, 30, tzinfo=UTC)) is Period.NIGHT
    assert vork2_period(datetime(2026, 7, 15, 4, 30, tzinfo=UTC)) is Period.DAY


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        vork2_period(datetime(2026, 1, 15, 12))


@pytest.mark.parametrize("t, want", [
    (L(2026, 1, 15, 8, 45), Bucket.DAY),            # Thursday, winter
    (L(2026, 1, 15, 9, 0), Bucket.WEEKDAY_PEAK),
    (L(2026, 1, 15, 11, 45), Bucket.WEEKDAY_PEAK),
    (L(2026, 1, 15, 12, 0), Bucket.DAY),
    (L(2026, 1, 15, 16, 0), Bucket.WEEKDAY_PEAK),
    (L(2026, 1, 15, 20, 0), Bucket.DAY),
    (L(2026, 1, 15, 22, 0), Bucket.NIGHT),
    (L(2026, 1, 17, 16, 0), Bucket.WEEKEND_PEAK),   # Saturday
    (L(2026, 1, 18, 19, 45), Bucket.WEEKEND_PEAK),  # Sunday
    (L(2026, 1, 17, 12, 0), Bucket.NIGHT),
    (L(2026, 7, 16, 10, 0), Bucket.DAY),            # summer: no peaks
    (L(2026, 7, 18, 17, 0), Bucket.NIGHT),
    (L(2026, 11, 2, 9, 0), Bucket.WEEKDAY_PEAK),    # November counts as winter
    (L(2026, 3, 31, 9, 0), Bucket.WEEKDAY_PEAK),    # so does March
    (L(2026, 4, 1, 9, 0), Bucket.DAY),
    (L(2026, 10, 30, 9, 0), Bucket.DAY),
])
def test_vork5(t, want):
    assert vork5_bucket(t) is want


def test_vork5_weekday_holiday_evening_is_night_not_a_peak():
    assert vork5_bucket(L(2026, 2, 24, 17), holidays={INDEPENDENCE_DAY}) is Bucket.NIGHT


def test_vork5_saturday_holiday_is_still_weekend_peak():
    assert vork5_bucket(L(2026, 1, 17, 17), holidays={date(2026, 1, 17)}) is Bucket.WEEKEND_PEAK


def test_buckets_nest_inside_vork2_periods():
    """Every Võrk 5 bucket sits inside its parent Võrk 2 period, all year."""
    t = datetime(2026, 1, 1, tzinfo=TLL).astimezone(UTC)
    end = datetime(2027, 1, 1, tzinfo=TLL).astimezone(UTC)
    hol = {INDEPENDENCE_DAY, date(2026, 12, 24), date(2026, 6, 23)}
    while t < end:
        assert vork5_bucket(t, hol).parent is vork2_period(t, hol), t
        t += timedelta(minutes=15)


def test_is_workday():
    assert is_workday(date(2026, 9, 18))
    assert not is_workday(date(2026, 9, 19))
    assert not is_workday(INDEPENDENCE_DAY, {INDEPENDENCE_DAY})
