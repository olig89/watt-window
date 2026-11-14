from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.watt_window.core.find import NO_PRICES, TOO_SHORT, find_window, next_at
from custom_components.watt_window.core.nordpool import SpotInterval
from custom_components.watt_window.core.windows import cheapest_window, price_quarters, window_at

UTC = timezone.utc
TALLINN = ZoneInfo("Europe/Tallinn")
T0 = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)
Q = timedelta(minutes=15)
FLAT = {"network_plan": "flat", "network_rates": {"flat": 0.0}, "timezone": "UTC"}


def quarters(prices, start=T0):
    return price_quarters(
        [SpotInterval(start + i * Q, start + (i + 1) * Q, p) for i, p in enumerate(prices)], FLAT
    )


# --- next_at ---------------------------------------------------------------

def test_time_later_today_is_today():
    now = datetime(2026, 11, 13, 18, 0, tzinfo=UTC)  # 20:00 in Tallinn
    assert next_at(time(23, 0), now, TALLINN) == datetime(2026, 11, 13, 23, 0, tzinfo=TALLINN)


def test_time_already_passed_is_tomorrow():
    now = datetime(2026, 11, 13, 18, 0, tzinfo=UTC)  # 20:00 in Tallinn
    assert next_at(time(7, 0), now, TALLINN) == datetime(2026, 11, 14, 7, 0, tzinfo=TALLINN)


def test_time_equal_to_now_is_tomorrow():
    now = datetime(2026, 11, 13, 5, 0, tzinfo=UTC)  # exactly 07:00 in Tallinn
    assert next_at(time(7, 0), now, TALLINN).date().isoformat() == "2026-11-14"


def test_time_across_the_autumn_clock_change_keeps_local_time():
    now = datetime(2026, 10, 24, 20, 0, tzinfo=UTC)  # Sat 23:00 EEST; clocks go back that night
    at = next_at(time(7, 0), now, TALLINN)
    assert at.astimezone(UTC) == datetime(2026, 10, 25, 5, 0, tzinfo=UTC)  # 07:00 EET


def test_datetime_is_kept_and_naive_means_local():
    now = datetime(2026, 11, 13, 18, 0, tzinfo=UTC)
    aware = datetime(2026, 11, 15, 1, 0, tzinfo=UTC)
    assert next_at(aware, now, TALLINN) == aware
    assert next_at(datetime(2026, 11, 15, 7, 0), now, TALLINN) == datetime(2026, 11, 15, 7, 0, tzinfo=TALLINN)


# --- cheapest_window latest_start -------------------------------------------

def test_latest_start_rules_out_later_cheaper_starts():
    qs = quarters([5, 4, 9, 9, 1, 1])
    assert cheapest_window(qs, 2 * Q, 1000).start == T0 + 4 * Q
    w = cheapest_window(qs, 2 * Q, 1000, latest_start=T0 + Q)
    assert w.start == T0  # 5+4 beats 4+9


def test_latest_same_price_start_stays_within_latest_start():
    qs = quarters([1, 1, 1, 1, 1, 1])
    w = cheapest_window(qs, 2 * Q, 1000, latest_start=T0 + 2 * Q)
    assert w.latest_start == T0 + 2 * Q


# --- window_at -------------------------------------------------------------

def test_window_at_prices_a_fixed_start():
    qs = quarters([5, 3, 1, 1])
    w = window_at(qs, T0, 2 * Q, 1000)
    assert w.start == T0 and w.average_price == pytest.approx(4)


def test_window_at_needs_every_price():
    qs = quarters([5, 3])
    assert window_at(qs, T0 + Q, 2 * Q, 1000) is None
    assert window_at(qs, T0 - Q, Q, 1000) is None


# --- find_window -----------------------------------------------------------

NIGHT = [9] * 8 + [2] * 8 + [1] * 8 + [9] * 8  # 8 h: cheapest 2 h is hours 4-6


def test_finds_the_cheapest_from_this_quarter_and_compares_with_now():
    qs = quarters(NIGHT)
    r = find_window(qs, T0 + timedelta(minutes=7), timedelta(hours=2), 1000, 0)
    assert r.window.start == T0 + timedelta(hours=4)
    assert r.start_now.start == T0  # the current quarter, not the next one
    assert r.start_now.cost - r.window.cost == pytest.approx(2 * 9 - 2 * 1)
    assert r.settled is False  # no limit: a later day could still be cheaper


def test_finish_by_keeps_the_window_inside():
    qs = quarters(NIGHT)
    r = find_window(qs, T0, timedelta(hours=2), 1000, 0, finish_by=T0 + timedelta(hours=4))
    assert r.window.start == T0 + timedelta(hours=2) and r.window.end == T0 + timedelta(hours=4)
    assert r.settled is True


def test_start_by_allows_a_window_that_runs_on_past_it():
    qs = quarters(NIGHT)
    r = find_window(qs, T0, timedelta(hours=2), 1000, 0, start_by=T0 + timedelta(hours=3))
    assert r.window.start == T0 + timedelta(hours=3)  # runs 3-5, half in the cheapest hours


def test_start_after_rounds_up_to_a_quarter():
    qs = quarters([1] * 4 + [5] * 12)
    r = find_window(qs, T0, Q, 1000, 0, start_after=T0 + timedelta(minutes=50))
    assert r.window.start == T0 + 4 * Q


def test_limits_too_tight_for_the_length():
    qs = quarters(NIGHT)
    r = find_window(qs, T0, timedelta(hours=3), 1000, 0, finish_by=T0 + timedelta(hours=2))
    assert r.window is None and r.reason == TOO_SHORT


def test_limits_beyond_published_prices():
    qs = quarters(NIGHT)
    later = T0 + timedelta(hours=10)
    r = find_window(qs, T0, timedelta(hours=1), 1000, 0, start_after=later)
    assert r.window is None and r.reason == NO_PRICES
    assert find_window(qs, T0, timedelta(hours=1), 1000, 0, finish_by=later).settled is False
