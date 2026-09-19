from datetime import datetime, timedelta, timezone

import pytest

from custom_components.watt_window.core.cost import export_price, import_price
from custom_components.watt_window.core.nordpool import SpotInterval
from custom_components.watt_window.core.presets import PRESETS, tariff_from_preset
from custom_components.watt_window.core.solar import quarter_watts
from custom_components.watt_window.core.tariff import tariff_key
from custom_components.watt_window.core.windows import cheapest_window, price_quarters

UTC = timezone.utc
T0 = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)  # a Monday
Q = timedelta(minutes=15)
FLAT = {"network_plan": "flat", "network_rates": {"flat": 0.0}, "timezone": "UTC"}


def spots(prices, step=Q, start=T0):
    """Spot intervals from a list of EUR/kWh prices."""
    return [SpotInterval(start + i * step, start + (i + 1) * step, p) for i, p in enumerate(prices)]


def test_cheapest_window_finds_the_lowest_average_run():
    qs = price_quarters(spots([5, 5, 1, 2, 1, 5, 5, 0.5]), FLAT)
    w = cheapest_window(qs, timedelta(minutes=45), load_w=1000)
    assert w.start == T0 + 2 * Q and w.end == T0 + 5 * Q
    assert w.average_price == pytest.approx(4 / 3)


def test_single_cheap_quarter_does_not_win_a_long_window():
    # The 0.5 at the very end cannot anchor a 45-min window: nothing follows it.
    qs = price_quarters(spots([5, 5, 1, 2, 1, 5, 5, 0.5]), FLAT)
    assert cheapest_window(qs, timedelta(minutes=45), 1000).start == T0 + 2 * Q


def test_ties_go_to_the_earliest_start():
    qs = price_quarters(spots([1, 1, 1, 1]), FLAT)
    assert cheapest_window(qs, timedelta(minutes=30), 1000).start == T0


def test_horizon_shorter_than_window_gives_none():
    qs = price_quarters(spots([1, 1, 1]), FLAT)
    assert cheapest_window(qs, timedelta(hours=1), 1000) is None


def test_earliest_excludes_the_past():
    qs = price_quarters(spots([0, 0, 9, 9, 3, 3]), FLAT)
    w = cheapest_window(qs, timedelta(minutes=30), 1000, earliest=T0 + 2 * Q)
    assert w.start == T0 + 4 * Q


def test_gaps_in_the_data_are_never_bridged():
    qs = price_quarters(spots([1, 1]) + spots([0, 0], start=T0 + 4 * Q), FLAT)
    w = cheapest_window(qs, timedelta(minutes=45), 1000)
    assert w is None  # no three contiguous quarters exist


def test_hourly_spot_prices_split_into_quarters():
    qs = price_quarters(spots([0.1, 0.2], step=timedelta(hours=1)), FLAT)
    assert len(qs) == 8 and qs[3].spot == 0.1 and qs[4].spot == 0.2
    assert all(b.start == a.end for a, b in zip(qs, qs[1:]))


def test_solar_surplus_makes_a_quarter_cost_only_the_export_price():
    cfg = {**FLAT, "network_rates": {"flat": 0.10}, "vat": 0.0}
    sw = {T0: 3000.0}
    q = price_quarters(spots([0.05]), cfg, solar_w=sw)[0]
    assert q.import_price == pytest.approx(0.15)
    assert q.export_price == pytest.approx(0.05)
    # 1 kW load, 500 W base load, 3 kW sun: fully covered, costs the export price.
    assert q.effective_price(1000, 500) == pytest.approx(0.05)
    # 3 kW load: 2.5 kW covered, 0.5 kW bought.
    assert q.effective_price(3000, 500) == pytest.approx((2500 * 0.05 + 500 * 0.15) / 3000)


def test_solar_can_beat_a_cheaper_spot_hour():
    cfg = {**FLAT, "network_rates": {"flat": 0.10}}
    qs = price_quarters(spots([0.02, 0.06]), cfg, solar_w={T0 + Q: 5000.0})
    w = cheapest_window(qs, Q, load_w=1000, base_load_w=500)
    assert w.start == T0 + Q and w.solar_share == 1.0


def test_window_cost_is_energy_times_price():
    qs = price_quarters(spots([0.2] * 4), FLAT)
    w = cheapest_window(qs, timedelta(hours=1), load_w=2000)
    assert w.cost == pytest.approx(2.0 * 0.2)  # 2 kWh at 0.2


def test_quarter_watts_spreads_hourly_energy():
    qw = quarter_watts({"2026-09-21T12:00:00+03:00": 2000})
    assert len(qw) == 4
    assert qw[datetime(2026, 9, 21, 9, 0, tzinfo=UTC)] == 2000.0
    assert qw[datetime(2026, 9, 21, 9, 45, tzinfo=UTC)] == 2000.0


def test_every_preset_has_a_rate_for_every_period_of_its_plan():
    from custom_components.watt_window.core.tariff import rate_keys
    for name in PRESETS:
        cfg = tariff_from_preset(name)
        assert set(rate_keys(cfg["network_plan"])) == set(cfg["network_rates"]), name


def test_vork1_preset_reproduces_the_august_2026_invoice_rate():
    cfg = {**tariff_from_preset("ee_vork1"), "timezone": "Europe/Tallinn"}
    # 0.0772 network + 0.02181 fees, x1.24 VAT, on a zero spot.
    assert import_price(0.0, T0, cfg) == pytest.approx((0.0772 + 0.02181) * 1.24, abs=1e-6)


def test_custom_day_night_hours_are_respected():
    cfg = {**tariff_from_preset("custom_day_night"), "timezone": "UTC", "day_start": 8, "day_end": 20}
    assert tariff_key(T0 + timedelta(hours=7, minutes=45), cfg) == "night"
    assert tariff_key(T0 + timedelta(hours=8), cfg) == "day"
    assert tariff_key(T0 + timedelta(hours=20), cfg) == "night"


def test_export_fee_reduces_export_value():
    assert export_price(0.10, {"export_fee": 0.01}) == pytest.approx(0.09)


def test_flat_cheap_stretch_reports_how_late_you_can_start():
    """Sunday 2026-09-20 in EE: spot ~0 from 01:00 to 08:45, so every start ties."""
    from datetime import datetime, timedelta, timezone
    from custom_components.watt_window.core.windows import Quarter, cheapest_window

    t0 = datetime(2026, 9, 19, 22, 0, tzinfo=timezone.utc)
    qs = []
    for k in range(40):  # 10 h
        price = 0.0706 if k < 28 else 0.09  # flat for 7 h, then dearer
        qs.append(Quarter(start=t0 + timedelta(minutes=15 * k), end=t0 + timedelta(minutes=15 * (k + 1)),
                          spot=0.0, import_price=price, export_price=0.0, solar_w=0.0, tariff_key="night"))
    w = cheapest_window(qs, timedelta(hours=2), load_w=1000)
    assert w.start == t0  # ties go to the earliest start
    assert w.latest_start == t0 + timedelta(hours=5)  # 2 h run still inside the flat 7 h
    one_off = cheapest_window(qs[:8] + [], timedelta(hours=2), load_w=1000)
    assert one_off.latest_start == one_off.start  # only one start fits
