from datetime import datetime, timedelta, timezone

from custom_components.watt_window.core.heatpump import BOOST, HOLD_BACK, NORMAL, StickyMode, advise
from custom_components.watt_window.core.windows import Quarter

T0 = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
Q = timedelta(minutes=15)


def day(prices_by_hour):
    """Quarters from hourly import prices (c/kWh), starting at T0."""
    qs = []
    for h, c in enumerate(prices_by_hour):
        for k in range(4):
            t = T0 + timedelta(hours=h) + k * Q
            qs.append(Quarter(start=t, end=t + Q, spot=0, import_price=c / 100, export_price=0,
                              solar_w=0, tariff_key="day"))
    return qs


def test_boost_when_now_is_clearly_cheaper_than_the_hours_ahead():
    a = advise(day([8, 15, 15, 15, 15]), T0, 2000, 500, store_hours=4, margin=0.02)
    assert a.mode == BOOST and a.average_ahead > a.price_now


def test_hold_back_when_a_clearly_cheaper_stretch_is_coming():
    a = advise(day([15, 15, 7, 15, 15]), T0, 2000, 500, store_hours=4, margin=0.02)
    assert a.mode == HOLD_BACK
    assert a.cheapest_at == T0 + timedelta(hours=2)


def test_hold_back_beats_boost_when_it_gets_even_cheaper():
    # Now 8c is below the average ahead, but 3c is coming: wait.
    a = advise(day([8, 15, 3, 15, 15]), T0, 2000, 500, store_hours=4, margin=0.02)
    assert a.mode == HOLD_BACK


def test_cheap_stretch_beyond_what_heat_can_store_is_ignored():
    a = advise(day([12, 12, 12, 12, 12, 12, 2]), T0, 2000, 500, store_hours=4, margin=0.02)
    assert a.mode == NORMAL  # the 2c hour is 6 h away; heat doesn't keep that long


def test_small_differences_are_not_worth_shifting_for():
    a = advise(day([12, 13, 11, 13, 12]), T0, 2000, 500, store_hours=4, margin=0.02)
    assert a.mode == NORMAL


def test_spare_solar_means_boost():
    assert advise(day([30, 5, 5, 5, 5]), T0, 2000, 500, 4, 0.02, spare_enough=True).mode == BOOST


def test_needs_an_hour_of_prices_ahead():
    assert advise(day([10]), T0, 2000, 500, 4, 0.02).mode == NORMAL


def test_sticky_mode_protects_the_compressor():
    s = StickyMode(timedelta(minutes=60))
    assert s.update(T0, BOOST) == BOOST
    assert s.update(T0 + timedelta(minutes=30), HOLD_BACK) == BOOST  # too soon to change
    assert s.update(T0 + timedelta(minutes=60), HOLD_BACK) == HOLD_BACK
