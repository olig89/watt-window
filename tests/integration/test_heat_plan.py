"""Heat pump planning mode, end to end with a fake weather forecast."""

from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant, SupportsResponse

from .test_integration import setup, tallinn  # noqa: F401 - fixture

ADVICE = "sensor.watt_window_heat_pump_advice"
PLAN = dict(heat_pump_advice=True, heat_pump_planner="plan")


@pytest.fixture
def weather(hass: HomeAssistant):
    """weather.home with an hourly forecast of 5 °C for two days."""
    hass.states.async_set("weather.home", "cloudy", {"temperature": 5})
    calls: list[str] = []

    async def get_forecasts(call):
        calls.append(call.data["type"])
        base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        rows = [{"datetime": (base + timedelta(hours=h)).isoformat(), "temperature": 5.0} for h in range(48)]
        return {"weather.home": {"forecast": rows if call.data["type"] == "hourly" else []}}

    hass.services.async_register("weather", "get_forecasts", get_forecasts, supports_response=SupportsResponse.ONLY)
    return calls


async def settle(hass):
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.freeze_time("2026-09-22 00:00:00+00:00")  # cheap block 02-04 UTC is 2 h away
async def test_plan_holds_back_for_the_cheap_block_then_boosts_in_it(hass, tallinn, nordpool, weather, freezer):
    await setup(hass, **PLAN)
    await settle(hass)
    st = hass.states.get(ADVICE)
    assert st.state == "hold_back", st.attributes
    assert st.attributes["planner"] == "plan" and st.attributes["reason"].startswith("Plan:")
    assert weather == ["hourly"]
    blocks = st.attributes["plan"]
    assert any(b["mode"] == "boost" and b["start"].startswith("2026-09-22T02:00") for b in blocks), blocks
    assert st.attributes["store_limit_kwh"] == pytest.approx(4.25) and st.attributes["bank_limit_kwh"] == pytest.approx(2.12, abs=0.01)
    assert st.attributes["need_kwh"] > 0 and st.attributes["outdoor_average"] == 5.0
    assert st.attributes["plan_saving"] > 0

    freezer.tick(timedelta(hours=2, minutes=1))
    async_fire_time_changed(hass)
    await settle(hass)
    st = hass.states.get(ADVICE)
    assert st.state == "boost", st.attributes
    assert st.attributes["store_kwh"] < 0  # the hold back left the house owing heat


@pytest.mark.freeze_time("2026-09-22 01:00:00+00:00")
async def test_plan_without_a_forecast_falls_back_to_look_ahead(hass, tallinn, nordpool):
    await setup(hass, **PLAN)
    await settle(hass)
    st = hass.states.get(ADVICE)
    assert st.state == "hold_back"  # what look ahead says here
    assert "looking ahead instead" in st.attributes["reason"]
    assert st.attributes["plan"] == []


@pytest.mark.parametrize("start", ["2026-09-22 00:00:00+00:00", "2026-09-22 01:00:00+00:00",
                                   "2026-09-22 01:30:00+00:00", "2026-09-22 04:30:00+00:00"])
async def test_first_plan_replaces_the_start_up_fallback_at_once(hass, tallinn, nordpool, weather, freezer, start):
    # At start-up the advice comes before the forecast, from look ahead. The first plan must
    # take over straight away, not after the minimum time in a mode.
    freezer.move_to(start)
    await setup(hass, **PLAN)
    await settle(hass)
    st = hass.states.get(ADVICE)
    plan_now = next(b for b in st.attributes["plan"] if b["start"] <= st.attributes["plan_made_at"] < b["end"])
    assert st.state == plan_now["mode"], st.attributes
    assert st.attributes["reason"].startswith("Plan:"), st.attributes["reason"]


@pytest.mark.freeze_time("2026-09-22 00:00:00+00:00")
async def test_saving_settings_keeps_the_mode_and_since(hass, tallinn, nordpool, weather, freezer, hass_ws_client):
    entry = await setup(hass, **PLAN)
    await settle(hass)
    before = hass.states.get(ADVICE)
    assert before.state == "hold_back"
    freezer.tick(timedelta(minutes=20))
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "watt_window/save", "heat_pump_boost_penalty": 0.06})
    assert (await ws.receive_json())["success"]
    await settle(hass)
    assert entry.options["heat_pump_boost_penalty"] == 0.06
    after = hass.states.get(ADVICE)
    assert after.state == before.state and after.attributes["since"] == before.attributes["since"]


@pytest.mark.freeze_time("2026-09-22 01:00:00+00:00")
async def test_look_ahead_has_no_plan_attributes(hass, tallinn, nordpool):
    await setup(hass, heat_pump_advice=True)
    st = hass.states.get(ADVICE)
    assert st.attributes["planner"] == "look_ahead" and "plan" not in st.attributes


@pytest.mark.freeze_time("2026-09-22 00:00:00+00:00")
async def test_cold_brine_turns_a_planned_boost_to_normal(hass, tallinn, nordpool, weather, freezer):
    hass.states.async_set("sensor.brine_out", "-1.5")
    await setup(hass, **PLAN, heat_pump_brine_entity="sensor.brine_out")
    await settle(hass)
    freezer.tick(timedelta(hours=2, minutes=1))
    async_fire_time_changed(hass)
    await settle(hass)
    st = hass.states.get(ADVICE)
    # Without the brine sensor this is a boost (first test). The re-plan that follows the
    # guard starts from normal, so the reason may read as the plan's own.
    assert st.state == "normal", st.attributes
    entry = hass.config_entries.async_entries("watt_window")[0]
    assert entry.runtime_data.heat_pump.as_dict()["brine_cold"] is True


@pytest.mark.freeze_time("2026-09-21 12:00:00+00:00")
async def test_panel_saves_planner_settings_and_suggests_heat_need(hass, tallinn, nordpool, weather, hass_ws_client, monkeypatch):
    entry = await setup(hass, heat_pump_advice=True)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "watt_window/save", "heat_pump_planner": "plan",
                        "heat_pump_weather_entity": "weather.home", "heat_pump_kwh_per_degree_day": 1.4,
                        "heat_pump_balance_temp": 18, "heat_pump_boost_penalty": 0.06,
                        "heat_pump_min_block_hours": 2.5})
    msg = await ws.receive_json()
    assert msg["success"], msg
    await settle(hass)
    assert entry.options["heat_pump_planner"] == "plan" and entry.options["heat_pump_balance_temp"] == 18

    await ws.send_json({"id": 2, "type": "watt_window/save", "heat_pump_boost_penalty": 0.9})
    msg = await ws.receive_json()
    assert not msg["success"]
    await ws.send_json({"id": 3, "type": "watt_window/save", "heat_pump_brine_entity": "sensor.nope"})
    msg = await ws.receive_json()
    assert not msg["success"]

    await ws.send_json({"id": 4, "type": "watt_window/data"})
    msg = await ws.receive_json()
    s = msg["result"]["settings"]
    assert s["heat_pump_planner"] == "plan" and s["heat_pump_kwh_per_degree_day"] == 1.4
    assert {"entity_id": "weather.home", "name": "weather.home"} in s["heat_pump_sensors"]["weather"]
    assert msg["result"]["heat_pump"]["planner"] == "plan"

    from custom_components.watt_window import websocket

    async def fake_days(hass_, energy, outdoor, days):
        assert (energy, outdoor, days) == ("sensor.hp_heating_energy", "sensor.outdoor", 45)
        return [(t, 1.4 * (18 - t)) for t in (2, 4, 6, 8, 10, 12, 14)]

    monkeypatch.setattr(websocket, "async_daily_heat_use", fake_days)
    await ws.send_json({"id": 5, "type": "watt_window/heat_need_fit",
                        "energy_entity": "sensor.hp_heating_energy", "outdoor_entity": "sensor.outdoor"})
    msg = await ws.receive_json()
    assert msg["success"], msg
    assert msg["result"]["fit"] == {"kwh_per_degree_day": 1.4, "balance_temp": 18.0, "r2": 1.0, "days": 7}
