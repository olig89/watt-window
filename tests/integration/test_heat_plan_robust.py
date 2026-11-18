"""Planning mode when things around it are late, stale or unusual."""

from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import SupportsResponse

from .test_heat_plan import PLAN, settle, weather  # noqa: F401 - fixture
from .test_heat_pump_restart import ADVICE, setup as restarted_setup, store_before_restart
from .test_integration import setup, tallinn  # noqa: F401 - fixture


async def tick(hass, freezer, delta):
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await settle(hass)


@pytest.mark.freeze_time("2026-09-22 01:00:00+00:00")
async def test_restored_since_is_kept_when_the_plan_overrides_the_start_up_fallback(hass, tallinn, nordpool, weather):
    # Look ahead says hold back here, the plan says normal. Before the restart it was normal
    # since 00:20, so after the plan takes over it is still normal since 00:20.
    store_before_restart(hass, 0, minutes_ago=62, state="normal", since="2026-09-22T00:20:00+00:00")
    await restarted_setup(hass, **PLAN)
    await settle(hass)
    st = hass.states.get(ADVICE)
    assert st.state == "normal" and st.attributes["reason"].startswith("Plan:"), st.attributes
    assert st.attributes["since"] == "2026-09-22T00:20:00+00:00"


@pytest.mark.freeze_time("2026-09-22 00:00:00+00:00")
async def test_a_weather_entity_that_appears_later_is_picked_up(hass, tallinn, nordpool, weather, freezer):
    hass.states.async_remove("weather.home")  # the weather integration hasn't loaded yet
    await setup(hass, **PLAN)
    await settle(hass)
    assert "looking ahead instead" in hass.states.get(ADVICE).attributes["reason"]
    hass.states.async_set("weather.home", "cloudy", {"temperature": 5})
    await tick(hass, freezer, timedelta(minutes=31))
    assert hass.states.get(ADVICE).attributes["reason"].startswith("Plan:")


@pytest.mark.freeze_time("2026-09-22 00:00:00+00:00")
async def test_a_stale_forecast_is_dropped(hass, tallinn, nordpool, weather, freezer):
    await setup(hass, **PLAN)
    await settle(hass)
    assert hass.states.get(ADVICE).attributes["reason"].startswith("Plan:")

    async def no_forecast(call):
        return {"weather.home": {"forecast": []}}

    hass.services.async_register("weather", "get_forecasts", no_forecast, supports_response=SupportsResponse.ONLY)
    await tick(hass, freezer, timedelta(hours=2))
    assert hass.states.get(ADVICE).attributes["reason"].startswith("Plan:")  # 2 h old: still fine
    await tick(hass, freezer, timedelta(hours=5))
    st = hass.states.get(ADVICE)
    assert "looking ahead instead" in st.attributes["reason"] and st.attributes["plan"] == [], st.attributes


@pytest.mark.freeze_time("2026-09-22 00:00:00+00:00")
async def test_a_fahrenheit_forecast_is_read_in_celsius(hass, tallinn, nordpool):
    hass.states.async_set("weather.home", "cloudy", {"temperature": 41, "temperature_unit": "°F"})

    async def get_forecasts(call):
        base = datetime.now(timezone.utc)
        rows = [{"datetime": (base + timedelta(hours=h)).isoformat(), "temperature": 41.0} for h in range(48)]
        return {"weather.home": {"forecast": rows}}

    hass.services.async_register("weather", "get_forecasts", get_forecasts, supports_response=SupportsResponse.ONLY)
    await setup(hass, **PLAN)
    await settle(hass)
    assert hass.states.get(ADVICE).attributes["outdoor_average"] == pytest.approx(5.0)


@pytest.mark.freeze_time("2026-09-21 12:00:00+00:00")
async def test_entity_pickers_check_the_domain(hass, tallinn, nordpool, weather, hass_ws_client):
    hass.states.async_set("sensor.outdoor", "5")
    await setup(hass, heat_pump_advice=True)
    ws = await hass_ws_client(hass)
    for i, (key, value) in enumerate([("heat_pump_brine_entity", "weather.home"),
                                      ("heat_pump_weather_entity", "sensor.outdoor")], start=1):
        await ws.send_json({"id": i, "type": "watt_window/save", key: value})
        assert not (await ws.receive_json())["success"], key
    await ws.send_json({"id": 9, "type": "watt_window/save", "heat_pump_brine_entity": "sensor.outdoor"})
    assert (await ws.receive_json())["success"]
