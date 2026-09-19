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


def np_area(hass, area="EE") -> str:
    """The setup screen's value for a Nord Pool setup + area."""
    return f"{hass.config_entries.async_entries('nordpool')[0].entry_id}|{area}"


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
        result["flow_id"], {"area": np_area(hass), "preset": "ee_vork2", "country": "ee"})
    assert result["step_id"] == "tariff"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"rate_day": 0.0607, "rate_night": 0.0351, "day_start": 7, "day_end": 22,
         "weekends_night": True, "holidays_night": True, "vat_percent": 24,
         "margin": 0.006, "other_per_kwh": 0.02181, "export_fee": 0},
    )
    # No Forecast.Solar set up, so skipping is the only choice offered.
    assert result["type"] is FlowResultType.MENU and result["menu_options"] == ["estimate", "skip_solar"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "skip_solar"})
    assert result["step_id"] == "battery_menu"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "skip_battery"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data["country"] == "EE"
    assert data["tariff"]["vat"] == pytest.approx(0.24)
    assert data["windows"] == [60, 120, 240]
    assert data["has_battery"] is False
    assert data["solar_source"] == "none"
    assert data["load_w"] == 1000 and data["base_load_w"] == 500  # defaults, never asked


async def test_config_flow_needs_nordpool(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "no_nordpool"


@pytest.mark.freeze_time(NOW)
async def test_config_flow_rejects_bad_day_hours(hass, tallinn, nordpool):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"area": np_area(hass), "preset": "custom_day_night", "country": ""})
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
    # 2 h was removed: its sensors go, rather than lingering as "unavailable".
    assert hass.states.get("sensor.watt_window_cheapest_2_h_window") is None
    assert hass.states.get("binary_sensor.watt_window_in_cheapest_2_h_window") is None

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
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "skip_solar"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "skip_battery"})
    assert result["step_id"] == "windows"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"hours": "1, 3"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["windows"] == [60, 180]
    assert entry.options["load_w"] == 1000  # kept; edited on the sidebar page


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


@pytest.mark.freeze_time(NOW)
async def test_config_flow_with_solar_and_battery(hass, tallinn, nordpool, forecast_solar):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"area": np_area(hass), "preset": "ee_vork1", "country": "EE"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"rate_flat": 0.0772, "vat_percent": 24, "margin": 0.006, "other_per_kwh": 0.02181, "export_fee": 0},
    )
    assert result["menu_options"] == ["estimate", "solar", "skip_solar"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "solar"})
    assert result["step_id"] == "solar"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"solar_entry_ids": [forecast_solar.entry_id], "base_load_w": 300})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "battery"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["solar_entry_ids"] == [forecast_solar.entry_id]
    assert result["data"]["has_battery"] is True
    assert result["data"]["base_load_w"] == 300


@pytest.mark.freeze_time(NOW)
async def test_options_flow_can_turn_solar_off(hass, tallinn, nordpool, forecast_solar):
    entry = await setup(hass, solar_entry_id=forecast_solar.entry_id)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"preset": "keep", "country": "EE"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"rate_day": 0.0607, "rate_night": 0.0351, "day_start": 7, "day_end": 22,
         "weekends_night": True, "holidays_night": True, "vat_percent": 24,
         "margin": 0, "other_per_kwh": 0.02181, "export_fee": 0},
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "skip_solar"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "skip_battery"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"hours": "1, 2"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["solar_source"] == "none"
    assert hass.states.get("sensor.watt_window_solar_forecast_now") is None


@pytest.mark.freeze_time("2026-09-21 12:00:00+00:00")  # Monday 15:00 Tallinn
async def test_daytime_and_overnight_windows(hass, tallinn, nordpool):
    await setup(hass)
    # Cheap block is 02:00-04:00 UTC = 05:00-07:00 Tallinn: overnight (20:00-08:00).
    night = hass.states.get("sensor.watt_window_cheapest_overnight_2_h_window")
    assert night.state == "2026-09-22T02:00:00+00:00"
    assert night.attributes["period_start"].startswith("2026-09-21T20:00:00+03:00")
    assert night.attributes["period_end"].startswith("2026-09-22T08:00:00+03:00")
    # The rest of today's day (15:00-20:00) is flat 100 and fits 2 h: first start wins.
    day = hass.states.get("sensor.watt_window_cheapest_daytime_2_h_window")
    assert day.state == "2026-09-21T12:00:00+00:00"
    assert day.attributes["period_end"].startswith("2026-09-21T20:00:00+03:00")
    # 6 h no longer fits in today's daytime: it moves to tomorrow's.
    day6 = hass.states.get("sensor.watt_window_cheapest_daytime_6_h_window")
    assert day6 is None  # 6 h isn't one of this entry's lengths
    assert hass.states.get("binary_sensor.watt_window_in_cheapest_daytime_2_h_window").state == "on"
    assert hass.states.get("binary_sensor.watt_window_in_cheapest_overnight_2_h_window").state == "off"


@pytest.mark.freeze_time("2026-09-21 12:00:00+00:00")
async def test_daytime_window_moves_to_tomorrow_when_today_is_too_short(hass, tallinn, nordpool):
    await setup(hass, windows=[360])
    day = hass.states.get("sensor.watt_window_cheapest_daytime_6_h_window")
    assert day.attributes["period_start"].startswith("2026-09-22T08:00:00+03:00")


@pytest.mark.freeze_time("2026-09-21 12:00:00+00:00")
async def test_two_solar_forecasts_are_added_up(hass, tallinn, nordpool, forecast_solar):
    second = MockConfigEntry(domain="forecast_solar", title="West roof", data={})
    second.add_to_hass(hass)
    await setup(hass, solar_entry_ids=[forecast_solar.entry_id, second.entry_id])
    ws_state = hass.states.get("sensor.watt_window_solar_forecast_now")
    assert ws_state is not None
    coord = hass.config_entries.async_entries("watt_window")[0].runtime_data
    q = next(q for q in coord.data.quarters if q.solar_w)
    assert q.solar_w == 8000  # 4 kW from each fake forecast


@pytest.mark.freeze_time("2026-09-21 12:00:00+00:00")
async def test_old_single_solar_setting_still_works(hass, tallinn, nordpool, forecast_solar):
    await setup(hass, solar_entry_id=forecast_solar.entry_id)
    assert hass.states.get("sensor.watt_window_solar_forecast_now") is not None


@pytest.mark.freeze_time("2026-09-21 12:00:00+00:00")
async def test_panel_saves_solar_and_day_hours(hass, tallinn, nordpool, forecast_solar, hass_ws_client):
    entry = await setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "watt_window/data"})
    msg = await ws.receive_json()
    settings = msg["result"]["settings"]
    assert settings["solar_entry_ids"] == [] and settings["day_start"] == 8 and settings["day_end"] == 20
    assert [o["entry_id"] for o in settings["solar_options"]] == [forecast_solar.entry_id]

    await ws.send_json({"id": 2, "type": "watt_window/save", "solar_entry_ids": [forecast_solar.entry_id],
                        "day_start": 7, "day_end": 21})
    msg = await ws.receive_json()
    assert msg["success"], msg
    await hass.async_block_till_done()
    assert entry.options["solar_entry_ids"] == [forecast_solar.entry_id]
    assert (entry.options["day_window_start"], entry.options["day_window_end"]) == (7, 21)
    assert hass.states.get("sensor.watt_window_solar_forecast_now") is not None

    await ws.send_json({"id": 3, "type": "watt_window/save", "solar_entry_ids": ["nope"]})
    msg = await ws.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "bad_solar"
    await ws.send_json({"id": 4, "type": "watt_window/save", "day_start": 20, "day_end": 8})
    msg = await ws.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "bad_day_hours"


@pytest.mark.freeze_time(NOW)
async def test_setup_remembers_which_nord_pool_setup(hass, tallinn, nordpool):
    other = MockConfigEntry(domain="nordpool", title="Finland", data={"areas": ["FI"], "currency": "EUR"})
    other.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    labels = [o["label"] for o in result["data_schema"].schema["area"].config["options"]]
    assert labels[1] == "FI (Finland)"  # named only because there are two setups
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"area": np_area(hass), "preset": "ee_vork1", "country": "EE"})
    assert result["step_id"] == "tariff"


@pytest.mark.freeze_time("2026-09-21 08:00:00+00:00")  # 10:00 CEST: tomorrow not published
async def test_horizon_is_reported_honestly(hass, tallinn, nordpool, hass_ws_client):
    await setup(hass)
    night = hass.states.get("sensor.watt_window_cheapest_overnight_2_h_window")
    assert night.attributes["settled"] is False  # tonight runs past the known prices
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "watt_window/data"})
    data = (await ws.receive_json())["result"]
    assert data["next_prices_at"] == "2026-09-21T10:45:00+00:00"  # 12:45 CEST
    assert data["price_source"] == "Nord Pool"


@pytest.mark.freeze_time("2026-09-21 12:00:00+00:00")  # tomorrow published
async def test_overnight_window_settles_once_prices_cover_the_night(hass, tallinn, nordpool):
    await setup(hass)
    night = hass.states.get("sensor.watt_window_cheapest_overnight_2_h_window")
    assert night.attributes["settled"] is True


def open_meteo_reply(url_params) -> dict:
    """Fake Open-Meteo: 500 W/m2 on the plane 10:00-12:00 UTC each day, night otherwise."""
    from datetime import datetime, timedelta, timezone

    start = datetime(2026, 9, 20, tzinfo=timezone.utc)
    times, values = [], []
    for k in range(4 * 24 * 4):
        end = start + timedelta(minutes=15 * (k + 1))  # stamped at the END of the quarter
        times.append(int(end.timestamp()))
        values.append(500.0 if 10 <= (end - timedelta(minutes=15)).hour < 12 else 0.0)
    return {"minutely_15": {"time": times, "global_tilted_irradiance": values}}


@pytest.fixture
def open_meteo(aioclient_mock):
    aioclient_mock.get("https://api.open-meteo.com/v1/forecast", json=open_meteo_reply(None))
    return aioclient_mock


@pytest.mark.freeze_time(NOW)
async def test_setup_can_estimate_solar_from_one_number(hass, tallinn, nordpool, open_meteo):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"area": np_area(hass), "preset": "ee_vork1", "country": "EE"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"rate_flat": 0.0772, "vat_percent": 24, "margin": 0.006, "other_per_kwh": 0.02181, "export_fee": 0},
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "estimate"})
    assert result["step_id"] == "estimate"
    # Only the size is needed; house load and exporting come pre-set.
    assert set(result["data_schema"].schema) == {"kwp", "base_load_w", "can_export"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"kwp": 4.8, "base_load_w": 500})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "skip_battery"})
    data = result["data"]
    assert data["solar_source"] == "open_meteo"
    assert data["solar_planes"] == [{"name": "Panels", "kwp": 4.8, "tilt": 35, "direction": 180}]
    assert data["can_export"] is True


@pytest.mark.freeze_time("2026-09-21 10:30:00+00:00")
async def test_open_meteo_estimate_feeds_the_windows(hass, tallinn, nordpool, open_meteo):
    await setup(hass, solar_source="open_meteo",
                solar_planes=[{"name": "South", "kwp": 4.0, "tilt": 35, "direction": 180},
                              {"name": "West", "kwp": 2.0, "tilt": 20, "direction": 270}])
    # 500 W/m2 x 6 kWp x 0.85 = 2550 W
    assert float(hass.states.get("sensor.watt_window_solar_forecast_now").state) == pytest.approx(2550)
    params = [call[1].query for call in open_meteo.mock_calls]
    assert {p["azimuth"] for p in params} == {"0.0", "90.0"}  # south -> 0, west -> 90
    assert {p["tilt"] for p in params} == {"35.0", "20.0"}


@pytest.mark.freeze_time("2026-09-21 10:30:00+00:00")
async def test_open_meteo_is_asked_at_most_hourly(hass, tallinn, nordpool, open_meteo, freezer):
    await setup(hass, solar_source="open_meteo", solar_planes=[{"name": "P", "kwp": 4.0, "tilt": 35, "direction": 180}])
    first = open_meteo.call_count
    freezer.tick(timedelta(minutes=15))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert open_meteo.call_count == first


@pytest.mark.freeze_time("2026-09-21 12:00:00+00:00")
async def test_panel_edits_roof_planes(hass, tallinn, nordpool, open_meteo, hass_ws_client):
    entry = await setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "watt_window/save", "solar_source": "open_meteo",
                        "solar_planes": [{"name": "East", "kwp": "3", "direction": 90}]})
    msg = await ws.receive_json()
    assert msg["success"], msg
    await hass.async_block_till_done()
    assert entry.options["solar_planes"] == [{"name": "East", "kwp": 3.0, "tilt": 35.0, "direction": 90.0}]
    await ws.send_json({"id": 2, "type": "watt_window/data"})
    solar = (await ws.receive_json())["result"]["solar"]
    assert solar["configured"] and "Open-Meteo" in solar["credit"]

    await ws.send_json({"id": 3, "type": "watt_window/save", "solar_planes": [{"kwp": 0}]})
    msg = await ws.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "bad_planes"


@pytest.mark.freeze_time("2026-09-21 12:00:00+00:00")
@pytest.mark.parametrize("price_fn", [default_price])
async def test_zero_export_makes_spare_solar_free(hass, tallinn, nordpool, forecast_solar):
    # With export, the night block (0.083) beat using solar (worth a 0.10 export).
    # Without export, spare solar would be lost, so using it costs nothing: midday wins.
    await setup(hass, solar_entry_id=forecast_solar.entry_id, can_export=False)
    st = hass.states.get("sensor.watt_window_cheapest_1_h_window")
    assert st.state == "2026-09-22T10:00:00+00:00"
    assert st.attributes["average_price"] == 0.0
    assert st.attributes["solar_share"] == 1.0


@pytest.mark.freeze_time("2026-09-21 12:00:00+00:00")
async def test_panel_api_reports_the_running_version(hass, tallinn, nordpool, hass_ws_client):
    import json
    from pathlib import Path

    await setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "watt_window/data"})
    data = (await ws.receive_json())["result"]
    manifest = Path(__file__).resolve().parents[2] / "custom_components" / "watt_window" / "manifest.json"
    assert data["version"] == json.loads(manifest.read_text(encoding="utf-8"))["version"]
