"""The heat pump advice keeps its ``since`` across a restart when the advice hasn't changed."""

import pytest
from pytest_homeassistant_custom_component.common import mock_restore_cache

from homeassistant.core import State

from .test_heat_plan import PLAN, settle, weather  # noqa: F401 - fixture
from .test_integration import setup, tallinn  # noqa: F401 - fixture

ADVICE = "sensor.watt_window_heat_pump_advice"
EARLIER = "2026-09-22T00:10:00+00:00"


def before_restart(hass, state, since=EARLIER):
    mock_restore_cache(hass, [State(ADVICE, state, {"since": since, "reason": "before the restart"})])


@pytest.mark.freeze_time("2026-09-22 01:00:00+00:00")  # look ahead says hold back here
async def test_same_advice_keeps_the_old_since(hass, tallinn, nordpool):
    before_restart(hass, "hold_back")
    await setup(hass, heat_pump_advice=True)
    st = hass.states.get(ADVICE)
    assert st.state == "hold_back" and st.attributes["since"] == EARLIER


@pytest.mark.freeze_time("2026-09-22 01:00:00+00:00")
async def test_different_advice_gets_a_new_since(hass, tallinn, nordpool):
    before_restart(hass, "boost")
    await setup(hass, heat_pump_advice=True)
    st = hass.states.get(ADVICE)
    assert st.state == "hold_back" and st.attributes["since"].startswith("2026-09-22T01:00:00")


@pytest.mark.freeze_time("2026-09-22 01:00:00+00:00")
@pytest.mark.parametrize("since", [None, "", "not a time", "2026-09-22T02:00:00+00:00"])
async def test_unusable_since_is_ignored(hass, tallinn, nordpool, since):
    # Missing, garbled, or later than now (a clock that jumped): start counting now.
    before_restart(hass, "hold_back", since)
    await setup(hass, heat_pump_advice=True)
    assert hass.states.get(ADVICE).attributes["since"].startswith("2026-09-22T01:00:00")


@pytest.mark.freeze_time("2026-09-22 00:00:00+00:00")  # the plan holds back here too
async def test_plan_mode_keeps_the_old_since_when_the_plan_agrees(hass, tallinn, nordpool, weather):
    before_restart(hass, "hold_back", "2026-09-21T23:30:00+00:00")
    await setup(hass, **PLAN)
    await settle(hass)
    st = hass.states.get(ADVICE)
    assert st.state == "hold_back" and st.attributes["reason"].startswith("Plan:")
    assert st.attributes["since"] == "2026-09-21T23:30:00+00:00"


def store_before_restart(hass, store, minutes_ago, state="hold_back", since="2026-09-21T23:30:00+00:00"):
    from datetime import datetime, timedelta, timezone

    saved = datetime(2026, 9, 22, tzinfo=timezone.utc) - timedelta(minutes=minutes_ago)
    mock_restore_cache(hass, [State(ADVICE, state, {"since": since, "store_kwh": store}, last_updated=saved)])


@pytest.mark.freeze_time("2026-09-22 00:00:00+00:00")
async def test_plan_store_comes_back_after_a_restart(hass, tallinn, nordpool, weather):
    store_before_restart(hass, -1.5, minutes_ago=2)
    await setup(hass, **PLAN)
    await settle(hass)
    assert hass.states.get(ADVICE).attributes["store_kwh"] == pytest.approx(-1.5, abs=0.05)


@pytest.mark.freeze_time("2026-09-22 00:00:00+00:00")
@pytest.mark.parametrize(("store", "minutes_ago"), [(-1.5, 5 * 60), ("garbled", 2), (None, 2), (-1.5, -10)])
async def test_plan_store_is_not_taken_back_when_stale_or_unusable(hass, tallinn, nordpool, weather, store, minutes_ago):
    # Down longer than the house can coast (4 h), unreadable, or saved "in the future".
    store_before_restart(hass, store, minutes_ago)
    await setup(hass, **PLAN)
    await settle(hass)
    assert hass.states.get(ADVICE).attributes["store_kwh"] == pytest.approx(0, abs=0.05)


@pytest.mark.freeze_time("2026-09-22 00:00:00+00:00")
async def test_plan_is_made_from_the_restored_store(hass, tallinn, nordpool, weather):
    # From an empty store the plan holds back here (cheap hours at 02:00). With the
    # house already owing all it can, it can't hold back any longer.
    store_before_restart(hass, -4.25, minutes_ago=1, state="hold_back")
    await setup(hass, **PLAN)
    await settle(hass)
    st = hass.states.get(ADVICE)
    assert st.attributes["store_kwh"] == pytest.approx(-4.25, abs=0.05)
    assert st.state != "hold_back" and st.attributes["reason"].startswith("Plan:"), st.attributes
