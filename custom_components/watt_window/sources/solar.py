"""Where the solar forecast comes from.

- ``open_meteo``: we estimate it ourselves from Open-Meteo's sunlight forecast
  and a description of each roof plane. Free, no account, and it starts from a
  single rough number (total panel size); tilt and direction default sensibly and
  can be refined later.
- ``forecast_solar``: Home Assistant's Forecast.Solar integration, for people
  who already use it.

Each source returns average watts per quarter-hour (UTC) for all planes added
together, or None when it has nothing usable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from datetime import datetime, timedelta
import logging

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import CONF_PLANES, CONF_SOLAR_SOURCE, FORECAST_SOLAR_DOMAIN, solar_entry_ids, solar_entry_label
from ..core.solar import DEFAULT_DIRECTION, DEFAULT_TILT, add_watts, open_meteo_azimuth, plane_watts, quarter_watts

_LOGGER = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_CREDIT = "Weather data by Open-Meteo.com (CC BY 4.0)"
# Weather models update every hour or so; asking more often learns nothing.
OPEN_METEO_REFRESH = timedelta(minutes=60)
# If Open-Meteo is briefly unreachable, a recent forecast beats none.
OPEN_METEO_KEEP = timedelta(hours=6)


def solar_source_kind(settings: dict) -> str:
    """'none', 'open_meteo' or 'forecast_solar' (pre-0.7 setups: from what they set)."""
    kind = settings.get(CONF_SOLAR_SOURCE)
    if kind:
        return kind
    return "forecast_solar" if solar_entry_ids(settings) else "none"


class SolarSource(ABC):
    title: str = ""
    credit: str | None = None

    def __init__(self, hass: HomeAssistant, settings: dict, cache: dict) -> None:
        self.hass = hass
        self.settings = settings
        self.cache = cache  # owned by the coordinator, survives refreshes
        self.warnings: list[str] = []

    @abstractmethod
    async def async_watts(self, now: datetime) -> dict[datetime, float] | None: ...


class ForecastSolarSource(SolarSource):
    def __init__(self, hass, settings, cache) -> None:
        super().__init__(hass, settings, cache)
        self.entry_ids = solar_entry_ids(settings)
        labels = []
        for eid in self.entry_ids:
            entry = hass.config_entries.async_get_entry(eid)
            labels.append(solar_entry_label(entry) if entry else "missing Forecast.Solar setup")
        self.title = ", ".join(labels) or "Forecast.Solar"

    async def _one(self, entry_id: str) -> dict[datetime, float] | None:
        if self.hass.config_entries.async_get_entry(entry_id) is None:
            return None
        if self.hass.services.has_service(FORECAST_SOLAR_DOMAIN, "get_forecast"):
            try:
                resp = await self.hass.services.async_call(
                    FORECAST_SOLAR_DOMAIN,
                    "get_forecast",
                    {"config_entry": entry_id, "resolution": "hourly"},
                    blocking=True,
                    return_response=True,
                )
                return quarter_watts((resp or {}).get("wh_period") or {})
            except HomeAssistantError as err:
                _LOGGER.debug("Forecast.Solar get_forecast failed: %s", err)
                return None
        # Older HA: the energy-dashboard hook (hourly Wh).
        try:
            from homeassistant.components.forecast_solar.energy import async_get_solar_forecast

            data = await async_get_solar_forecast(self.hass, entry_id)
        except Exception as err:  # noqa: BLE001 - optional source, never fatal
            _LOGGER.debug("Forecast.Solar energy hook failed: %s", err)
            return None
        return quarter_watts((data or {}).get("wh_hours") or {})

    async def async_watts(self, now):
        parts, failed = [], 0
        for eid in self.entry_ids:
            part = await self._one(eid)
            if part is None:
                failed += 1
            else:
                parts.append(part)
        if not parts:
            return None
        if failed:
            self.warnings.append(f"{failed} of {len(self.entry_ids)} solar forecasts unavailable: the total is too low.")
        return add_watts(*parts)


class OpenMeteoSource(SolarSource):
    credit = OPEN_METEO_CREDIT

    def __init__(self, hass, settings, cache) -> None:
        super().__init__(hass, settings, cache)
        self.planes = list(settings.get(CONF_PLANES) or [])
        total = sum(float(p.get("kwp") or 0) for p in self.planes)
        self.title = f"Estimated from Open-Meteo, {total:g} kWp"

    async def _plane(self, plane: dict) -> dict[datetime, float]:
        params = {
            "latitude": round(self.hass.config.latitude, 4),
            "longitude": round(self.hass.config.longitude, 4),
            "minutely_15": "global_tilted_irradiance",
            "tilt": float(plane.get("tilt", DEFAULT_TILT)),
            "azimuth": open_meteo_azimuth(plane.get("direction", DEFAULT_DIRECTION)),
            "forecast_days": 3,
            "past_days": 1,
            "timeformat": "unixtime",
            "timezone": "UTC",
        }
        session = async_get_clientsession(self.hass)
        async with asyncio.timeout(20):
            async with session.get(OPEN_METEO_URL, params=params) as resp:
                resp.raise_for_status()
                body = await resp.json()
        series = body["minutely_15"]
        return plane_watts(series["time"], series["global_tilted_irradiance"], float(plane["kwp"]))

    async def async_watts(self, now):
        if not self.planes:
            return None
        key = tuple((p.get("kwp"), p.get("tilt"), p.get("direction")) for p in self.planes)
        cached = self.cache.get("open_meteo")
        if cached and cached["key"] == key and now - cached["at"] < OPEN_METEO_REFRESH:
            return cached["watts"]
        try:
            parts = [await self._plane(p) for p in self.planes]
        except (aiohttp.ClientError, TimeoutError, KeyError, ValueError) as err:
            _LOGGER.warning("Open-Meteo solar forecast failed: %s", err)
            if cached and cached["key"] == key and now - cached["at"] < OPEN_METEO_KEEP:
                self.warnings.append("Couldn't reach Open-Meteo: using the solar forecast from earlier.")
                return cached["watts"]
            return None
        watts = add_watts(*parts)
        self.cache["open_meteo"] = {"key": key, "at": now, "watts": watts}
        return watts


def solar_source(hass: HomeAssistant, settings: dict, cache: dict) -> SolarSource | None:
    kind = solar_source_kind(settings)
    if kind == "open_meteo":
        return OpenMeteoSource(hass, settings, cache)
    if kind == "forecast_solar":
        return ForecastSolarSource(hass, settings, cache)
    return None
