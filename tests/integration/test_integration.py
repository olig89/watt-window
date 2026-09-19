from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.watt_window.const import DOMAIN
from custom_components.watt_window.core.presets import tariff_from_preset

from .conftest import default_price

NOW = "2026-09-21 08:07:00+00:00"  # Monday, 11:07 in Tallinn


@pytest.fixture
async def tallinn(hass: HomeAssistant):
    await hass.config.async_set_time_zone("Europe/Tallinn")


def entry_data(**over):
    data = {
        "area": "EE",
        "country": "EE",
        "preset": "ee_vork2",
        "tariff": tariff_from_preset("ee_vork2"),
        "solar_entry_id": None,
        "base_load_w": 500,
        "load_w": 1000,
        "windows": [60, 120],
    }
    data.update(over)
    return data


async def setup(hass, **over):
    entry = MockConfigEntry(domain=DOMAIN, title="Watt Window", data=entry_data(**over))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.freeze_time(NOW)
async def test_config_flow_creates_entry(hass, tallinn, nordpool):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"area": "EE", "preset": "ee_vork2", "country": "ee"})
    assert result["step_id"] == "tariff"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"rate_day": 0.0607, "rate_night": 0.0351, "day_start": 7, "day_end": 22,
         "weekends_night": True, "holidays_night": True, "vat_percent": 24,
         "margin": 0.006, "other_per_kwh": 0.02181, "export_fee": 0},
    )
    assert result["step_id"] == "solar"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"base_load_w": 500, "load_w": 1000})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data["country"] == "EE"
    assert data["tariff"]["vat"] == pytest.approx(0.24)
    assert data["windows"] == [60, 120, 240, 360]
    assert data["has_battery"] is False  # no battery is the default


async def test_config_flow_needs_nordpool(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "no_nordpool"


@pytest.mark.freeze_time(NOW)
async def test_config_flow_rejects_bad_day_hours(hass, tallinn, nordpool):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"area": "EE", "preset": "custom_day_night", "country": ""})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"rate_day": 0.1, "rate_night": 0.05, "day_start": 22, "day_end": 7,
         "weekends_night": False, "holidays_night": False, "vat_percent": 0,
         "margin": 0, "other_per_kwh": 0, "export_fee": 0},
    )
    assert result["errors"] == {"base": "bad_hours"}


@pytest.mark.freeze_time(NOW)
async def test_sensors_find_the_cheap_night_window(hass, tallinn, nordpool):
    await setup(hass)
    # Tomorrow isn't fetched before 10:30 UTC, so the horizon ends tonight and
    # the only cheap hours left in it are none: prices are flat 100 until then.
    # Today's cheap block (02:00-04:00 UTC) is already past.
    price = hass.states.get("sensor.watt_window_price_now")
    assert price is not None and price.attributes["tariff_period"] == "day"
    # 100 EUR/MWh spot + 0.0607 day + 0.02181 fees, x1.24
    assert float(price.state) == pytest.approx((0.1 + 0.0607 + 0.02181) * 1.24, abs=1e-4)
    assert hass.states.get("sensor.watt_window_cheapest_1_h_window") is not None
    assert hass.states.get("binary_sensor.watt_window_in_cheapest_1_h_window").state == "off"


@pytest.mark.freeze_time("2026-09-21 12:00:00+00:00")
async def test_window_moves_to_tomorrows_cheap_block_once_published(hass, tallinn, nordpool):
    await setup(hass)
    st = hass.states.get("sensor.watt_window_cheapest_2_h_window")
    # Tomorrow 02:00-04:00 UTC is the cheap block (05:00-07:00 Tallinn, night rate).
    assert st.state == "2026-09-22T02:00:00+00:00"
    assert st.attributes["end"] == "2026-09-22T04:00:00+00:00"
    assert "2026-09-22" in nordpool


@pytest.mark.freeze_time("2026-09-22 02:14:00+00:00")
async def test_in_window_turns_on_inside_the_window(hass, tallinn, nordpool, freezer):
    await setup(hass)
    # 02:14: the current quarter (02:00) counts, so the cheap block is "now".
    assert hass.states.get("binary_sensor.watt_window_in_cheapest_1_h_window").state == "on"


@pytest.mark.freeze_time("2026-09-21 12:00:00+00:00")
async def test_published_prices_are_fetched_once(hass, tallinn, nordpool, freezer):
    await setup(hass)
    first = len(nordpool)
    freezer.tick(timedelta(minutes=15))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(nordpool) == first  # every date was cached


def sunny_day_price(t):
    """Night block at 10 EUR/MWh; midday at 50; the rest 100."""
    if 2 <= t.hour < 4:
        return 10.0
    return 50.0 if 10 <= t.hour < 12 else 100.0


@pytest.mark.freeze_time("2026-09-21 12:00:00+00:00")
@pytest.mark.parametrize("price_fn", [default_price])
async def test_grid_night_beats_solar_when_export_is_worth_more(hass, tallinn, nordpool, forecast_solar):
    await setup(hass, solar_entry_id=forecast_solar.entry_id)
    # Using the sun forgoes a 0.10 export; the night block costs
    # (0.01 + 0.0351 + 0.02181) * 1.24 = 0.083 delivered. Night wins.
    st = hass.states.get("sensor.watt_window_cheapest_1_h_window")
    assert st.state.startswith("2026-09-22T02:")
    assert st.attributes["solar_share"] == 0.0


@pytest.mark.freeze_time("2026-09-21 12:00:00+00:00")
@pytest.mark.parametrize("price_fn", [sunny_day_price])
async def test_solar_surplus_wins_when_export_is_worth_less(hass, tallinn, nordpool, forecast_solar):
    await setup(hass, solar_entry_id=forecast_solar.entry_id, base_load_w=500, load_w=1000)
    # 4 kW of sun covers the 1 kW load: it costs the 0.05 export price, below
    # the night block's 0.083.
    st = hass.states.get("sensor.watt_window_cheapest_1_h_window")
    assert st.state == "2026-09-22T10:00:00+00:00"
    assert st.attributes["solar_share"] == 1.0
    assert hass.states.get("sensor.watt_window_solar_forecast_now") is not None


@pytest.mark.freeze_time("2026-09-21 12:00:00+00:00")
async def test_panel_api_reads_and_saves_settings(hass, tallinn, nordpool, hass_ws_client):
    entry = await setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "watt_window/data"})
    msg = await ws.receive_json()
    assert msg["success"], msg
    data = msg["result"]
    assert [w["minutes"] for w in data["windows"]] == [60, 120]
    assert len(data["quarters"]) >= 96

    await ws.send_json({"id": 2, "type": "watt_window/save", "windows": [60, 90, 360]})
    msg = await ws.receive_json()
    assert msg["success"], msg
    await hass.async_block_till_done()
    assert entry.options["windows"] == [60, 90, 360]
    assert hass.states.get("sensor.watt_window_cheapest_1_5_h_window") is not None

    await ws.send_json({"id": 3, "type": "watt_window/save", "windows": [7]})
    msg = await ws.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "bad_window"

    await ws.send_json({"id": 4, "type": "watt_window/save", "has_battery": True})
    msg = await ws.receive_json()
    assert msg["success"], msg
    await hass.async_block_till_done()
    assert entry.options["has_battery"] is True
    await ws.send_json({"id": 5, "type": "watt_window/data"})
    msg = await ws.receive_json()
    assert msg["result"]["settings"]["has_battery"] is True


@pytest.mark.freeze_time(NOW)
async def test_options_flow_edits_windows(hass, tallinn, nordpool):
    entry = await setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"preset": "keep", "country": "EE"})
    assert result["step_id"] == "tariff"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"rate_day": 0.0607, "rate_night": 0.0351, "day_start": 7, "day_end": 22,
         "weekends_night": True, "holidays_night": True, "vat_percent": 24,
         "margin": 0.006, "other_per_kwh": 0.02181, "export_fee": 0},
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"base_load_w": 400, "load_w": 2000})
    assert result["step_id"] == "windows"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"hours": "1, 3"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["windows"] == [60, 180]
    assert entry.options["load_w"] == 2000


@pytest.mark.freeze_time("2026-09-21 12:00:00+00:00")
async def test_panel_saves_a_tariff_and_prices_follow(hass, tallinn, nordpool, hass_ws_client):
    entry = await setup(hass)
    ws = await hass_ws_client(hass)
    tariff = dict(tariff_from_preset("ee_vork2"), network_rates={"day": 0.07, "night": 0.0351}, vat=0.22)
    await ws.send_json({"id": 1, "type": "watt_window/save", "tariff": tariff, "load_w": 2000})
    msg = await ws.receive_json()
    assert msg["success"], msg
    await hass.async_block_till_done()
    assert entry.options["tariff"]["vat"] == pytest.approx(0.22)
    assert entry.options["load_w"] == 2000
    price = float(hass.states.get("sensor.watt_window_price_now").state)
    assert price == pytest.approx((0.1 + 0.07 + 0.02181) * 1.22, abs=1e-4)

    bad = dict(tariff, vat=24)  # a percentage where a fraction belongs
    await ws.send_json({"id": 2, "type": "watt_window/save", "tariff": bad})
    msg = await ws.receive_json()
    assert not msg["success"]
