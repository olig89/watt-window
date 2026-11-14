"""The ``watt_window.find_window`` action: the cheapest window of any length, on request.

Answers with response data, so a script can start an appliance's delay timer
from it ("start the dishwasher in the cheapest 3.5 h, done by 07:00").
"""

from __future__ import annotations

from datetime import timedelta

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import CONF_BASE_LOAD_W, CONF_LOAD_W, DEFAULT_BASE_LOAD_W, DEFAULT_LOAD_W, DOMAIN
from .core.find import find_window, next_at

SERVICE_FIND_WINDOW = "find_window"
MAX_LENGTH = timedelta(hours=24)

_WHEN = vol.Any(cv.time, cv.datetime)


def _length(value) -> timedelta:
    length = cv.positive_time_period(value)
    if not timedelta(minutes=15) <= length <= MAX_LENGTH:
        raise vol.Invalid("duration must be between 15 minutes and 24 hours")
    return length


FIND_WINDOW_SCHEMA = vol.Schema(
    {
        vol.Required("duration"): _length,
        vol.Optional("start_after"): _WHEN,
        vol.Optional("start_by"): _WHEN,
        vol.Optional("finish_by"): _WHEN,
        vol.Optional("watts"): vol.All(vol.Coerce(float), vol.Range(min=1, max=50000)),
    }
)


def _iso(t):
    return None if t is None else dt_util.as_local(t).isoformat()


def _money(x):
    return None if x is None else round(x, 4)


async def _async_find_window(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    coordinator = entries[0].runtime_data if entries else None
    data = coordinator.data if coordinator else None
    if data is None:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")

    s = coordinator.settings
    tz = dt_util.get_default_time_zone()
    now = dt_util.utcnow()
    length: timedelta = call.data["duration"]
    load_w = float(call.data.get("watts") or s.get(CONF_LOAD_W, DEFAULT_LOAD_W))
    base_w = float(s.get(CONF_BASE_LOAD_W, DEFAULT_BASE_LOAD_W))
    when = {k: next_at(call.data[k], now, tz) if k in call.data else None
            for k in ("start_after", "start_by", "finish_by")}

    r = find_window(data.quarters, now, length, load_w, base_w, **when)
    w = r.window
    out: dict = {
        "found": w is not None,
        "reason": r.reason,
        "duration_minutes": round(length.total_seconds() / 60),
        "search_from": _iso(r.earliest),
        "search_until": _iso(r.latest_end),
        "prices_until": _iso(data.prices_until),
        "settled": r.settled,
        "currency": data.currency,
        "watts": load_w,
        "start_now_cost": _money(r.start_now.cost) if r.start_now else None,
    }
    if w is None:
        return out
    starts_in = max(0, round((w.start - now).total_seconds()))
    out |= {
        "start": _iso(w.start),
        "end": _iso(w.end),
        "latest_same_price_start": _iso(w.latest_start or w.start),
        "starts_in_seconds": starts_in,
        "starts_in_minutes": round(starts_in / 60),
        "average_price": _money(w.average_price),
        "average_import_price": _money(w.average_import_price),
        "solar_share": round(w.solar_share, 3),
        "estimated_cost": _money(w.cost),
        "saving": _money(r.start_now.cost - w.cost) if r.start_now else None,
    }
    return out


def async_setup_services(hass: HomeAssistant) -> None:
    async def handle(call: ServiceCall) -> ServiceResponse:
        return await _async_find_window(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_FIND_WINDOW, handle,
        schema=FIND_WINDOW_SCHEMA, supports_response=SupportsResponse.ONLY,
    )
