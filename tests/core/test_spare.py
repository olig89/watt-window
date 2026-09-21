from datetime import datetime, timedelta, timezone

import pytest

from custom_components.watt_window.core.spare import (
    FORECAST_GAP,
    HELD_BACK_NO_FORECAST,
    IMPORTING,
    MEASURED_EXPORT,
    NO_SOLAR,
    DelayedSwitch,
    TimeAverage,
    estimate_spare,
)

T0 = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)


def test_zero_export_held_back_uses_forecast_gap():
    # Oli's sunny-day pattern: ~60 W import while producing 2.3 kW, forecast 4.5 kW.
    e = estimate_spare(grid_import_w=60, solar_w=2300, forecast_w=4500, can_export=False)
    assert e.basis == FORECAST_GAP and e.held_back and e.is_guess
    assert e.watts == pytest.approx(2200)


def test_zero_export_importing_means_no_spare():
    # A dull day: panels flat out and the house still imports.
    e = estimate_spare(grid_import_w=485, solar_w=559, forecast_w=3000, can_export=False)
    assert e.basis == IMPORTING and e.watts == 0 and not e.held_back


def test_zero_export_without_a_forecast_knows_spare_exists_but_not_how_much():
    e = estimate_spare(grid_import_w=40, solar_w=2000, forecast_w=None, can_export=False)
    assert e.basis == HELD_BACK_NO_FORECAST and e.watts is None and e.held_back


def test_forecast_lower_than_actual_gives_zero_not_negative():
    e = estimate_spare(grid_import_w=40, solar_w=3000, forecast_w=2500, can_export=False)
    assert e.watts == 0


def test_export_blip_counts_as_spare_on_zero_export():
    e = estimate_spare(grid_import_w=-800, solar_w=3000, forecast_w=3000, can_export=False)
    assert e.watts == 800


def test_exporting_system_measures_its_spare():
    e = estimate_spare(grid_import_w=-1500, solar_w=4000, forecast_w=5000, can_export=True)
    assert e.basis == MEASURED_EXPORT and e.watts == 1500 and not e.is_guess
    e = estimate_spare(grid_import_w=300, solar_w=4000, forecast_w=5000, can_export=True)
    assert e.watts == 0


def test_night_is_no_solar():
    assert estimate_spare(grid_import_w=1600, solar_w=0, forecast_w=0, can_export=False).basis == NO_SOLAR


def test_time_average_weights_by_duration():
    avg = TimeAverage(timedelta(minutes=5))
    avg.add(T0, 4000)
    avg.add(T0 + 4 * MIN, 1000)  # a cloud for the last minute
    assert avg.value(T0 + 5 * MIN) == pytest.approx(3400)


def test_time_average_forgets_old_samples_but_keeps_the_value_in_force():
    avg = TimeAverage(timedelta(minutes=5))
    avg.add(T0, 100)
    avg.add(T0 + 2 * MIN, 500)
    # 20 minutes later, still 500 (no new reading), older sample dropped.
    assert avg.value(T0 + 22 * MIN) == pytest.approx(500)


def test_delayed_switch_ignores_a_short_cloud():
    sw = DelayedSwitch(on_after=3 * MIN, off_after=5 * MIN)
    assert not sw.update(T0, True)
    assert not sw.update(T0 + 2 * MIN, True)
    assert sw.update(T0 + 3 * MIN, True)  # on after 3 minutes
    assert sw.update(T0 + 4 * MIN, False)  # cloud...
    assert sw.update(T0 + 6 * MIN, True)  # ...gone before 5 minutes: never went off
    assert sw.update(T0 + 7 * MIN, False)
    assert not sw.update(T0 + 12 * MIN, False)  # 5 minutes of no spare: off
