"""Fake Nord Pool and Forecast.Solar so the real integration runs offline."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, SupportsResponse

CET = ZoneInfo("Europe/Berlin")
UTC = ZoneInfo("UTC")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def market_day(d: date, price_fn) -> list[dict]:
    """A Nord Pool market day (CET midnight to midnight) at 15-min resolution, EUR/MWh."""
    t = datetime.combine(d, time(0), tzinfo=CET).astimezone(UTC)
    end = datetime.combine(d + timedelta(days=1), time(0), tzinfo=CET).astimezone(UTC)
    rows = []
    while t < end:
        rows.append({"start": t.isoformat(), "end": (t + timedelta(minutes=15)).isoformat(),
                     "price": price_fn(t)})
        t += timedelta(minutes=15)
    return rows


def default_price(t: datetime) -> float:
    """Cheap at 02:00-04:00 UTC, expensive otherwise (EUR/MWh)."""
    return 10.0 if 2 <= t.hour < 4 else 100.0


@pytest.fixture
def price_fn():
    return default_price


@pytest.fixture
def nordpool(hass: HomeAssistant, price_fn):
    """A loaded Nord Pool entry and a fake get_prices_for_date service."""
    entry = MockConfigEntry(domain="nordpool", data={"areas": ["EE"], "currency": "EUR"})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    calls: list[str] = []

    async def get_prices(call):
        d = date.fromisoformat(str(call.data["date"]))
        calls.append(d.isoformat())
        return {"EE": market_day(d, price_fn)}

    hass.services.async_register("nordpool", "get_prices_for_date", get_prices,
                                 supports_response=SupportsResponse.ONLY)
    yield calls
    # Never let HA try to unload the real Nord Pool integration at teardown.
    entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


@pytest.fixture
def forecast_solar(hass: HomeAssistant):
    """A Forecast.Solar entry whose forecast is 4 kW, 10:00-12:00 UTC."""
    entry = MockConfigEntry(domain="forecast_solar", title="Roof", data={})
    entry.add_to_hass(hass)

    async def get_forecast(call):
        base = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        wh = {}
        for day in range(2):
            for h in (10, 11):
                wh[(base + timedelta(days=day, hours=h)).isoformat()] = 4000
        return {"watts": {}, "wh_period": wh}

    hass.services.async_register("forecast_solar", "get_forecast", get_forecast,
                                 supports_response=SupportsResponse.ONLY)
    return entry
