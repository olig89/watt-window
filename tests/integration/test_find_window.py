"""The watt_window.find_window action, end to end on the fake Nord Pool.

Prices (conftest): 10 EUR/MWh 02:00-04:00 UTC, 100 otherwise. Tariff Võrk 2:
night rate from 22:00 Tallinn (19:00 UTC on these dates).
"""

import pytest

from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component

from custom_components.watt_window.const import DOMAIN

from .test_integration import setup

EVENING = "2026-09-21 18:07:00+00:00"  # Monday 21:07 Tallinn, tomorrow's prices published


@pytest.fixture
async def tallinn(hass):
    await hass.config.async_set_time_zone("Europe/Tallinn")


async def find(hass, **data):
    return await hass.services.async_call(
        DOMAIN, "find_window", data, blocking=True, return_response=True
    )


@pytest.mark.freeze_time(EVENING)
async def test_finds_tomorrows_cheap_block_and_how_long_to_wait(hass, tallinn, nordpool):
    await setup(hass)
    r = await find(hass, duration="02:00:00")
    assert r["found"] is True
    assert r["start"] == "2026-09-22T05:00:00+03:00"
    assert r["end"] == "2026-09-22T07:00:00+03:00"
    assert r["starts_in_seconds"] == (7 * 60 + 53) * 60
    assert r["starts_in_minutes"] == 7 * 60 + 53
    assert r["saving"] > 0 and r["start_now_cost"] > r["estimated_cost"]
    assert r["settled"] is False and r["currency"] == "EUR"


@pytest.mark.freeze_time(EVENING)
async def test_finish_by_a_time_of_day(hass, tallinn, nordpool):
    await setup(hass)
    r = await find(hass, duration={"hours": 2}, finish_by="04:00")
    # Has to end by 04:00 Tallinn, before the cheap block: first night-rate start wins.
    assert r["start"] == "2026-09-21T22:00:00+03:00"
    assert r["latest_same_price_start"] == "2026-09-22T02:00:00+03:00"
    assert r["search_until"] == "2026-09-22T04:00:00+03:00"
    assert r["settled"] is True


@pytest.mark.freeze_time(EVENING)
async def test_start_by_a_time_of_day(hass, tallinn, nordpool):
    await setup(hass)
    r = await find(hass, duration="02:00:00", start_by="21:30")
    assert r["start"] == "2026-09-21T21:30:00+03:00"


@pytest.mark.freeze_time(EVENING)
async def test_limits_too_tight(hass, tallinn, nordpool):
    await setup(hass)
    r = await find(hass, duration="04:00:00", finish_by="23:00")
    assert r["found"] is False and r["reason"] == "too_short"
    assert "start" not in r


@pytest.mark.freeze_time(EVENING)
async def test_duration_must_be_sensible(hass, tallinn, nordpool):
    await setup(hass)
    with pytest.raises(Exception):
        await find(hass, duration="00:05:00")
    with pytest.raises(Exception):
        await find(hass, duration="25:00:00")


async def test_not_set_up(hass):
    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(ServiceValidationError):
        await find(hass, duration="01:00:00")
