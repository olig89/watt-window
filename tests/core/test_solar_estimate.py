from datetime import datetime, timezone

import pytest

from custom_components.watt_window.core.solar import add_watts, open_meteo_azimuth, plane_watts


@pytest.mark.parametrize("compass, om", [(180, 0), (90, -90), (270, 90), (0, 180), (135, -45), (225, 45)])
def test_compass_to_open_meteo_azimuth(compass, om):
    assert open_meteo_azimuth(compass) == om


def test_values_belong_to_the_quarter_before_their_stamp():
    t = int(datetime(2026, 9, 21, 10, 15, tzinfo=timezone.utc).timestamp())
    w = plane_watts([t], [800.0], kwp=5.0)
    # 800 W/m2 on 5 kWp at 0.85 -> 3.4 kW, for 10:00-10:15.
    assert w == {datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc): pytest.approx(3400.0)}


def test_gaps_and_night_are_handled():
    t0 = int(datetime(2026, 9, 21, 0, 15, tzinfo=timezone.utc).timestamp())
    w = plane_watts([t0, t0 + 900], [None, -1.0], kwp=5.0)
    assert list(w.values()) == [0.0]  # missing value skipped, negative clamped


def test_planes_add_up():
    t = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    assert add_watts({t: 1000.0}, {t: 500.0}) == {t: 1500.0}
