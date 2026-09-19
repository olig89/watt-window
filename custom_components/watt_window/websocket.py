"""The panel's data and settings API (websocket commands)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_BASE_LOAD_W,
    CONF_LOAD_W,
    CONF_SOLAR_ENTRY,
    CONF_TARIFF,
    CONF_WINDOWS,
    DEFAULT_BASE_LOAD_W,
    DEFAULT_LOAD_W,
    DOMAIN,
    settings_of,
    window_label,
)
from .validation import SettingsError, clean_tariff, clean_windows


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_data)
    websocket_api.async_register_command(hass, ws_save)


def _entry(hass: HomeAssistant):
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0] if entries else None


def _iso(t) -> str | None:
    return t.isoformat() if t else None


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/data"})
@websocket_api.async_response
async def ws_data(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Watt Window is not set up")
        return
    coord = entry.runtime_data
    data = coord.data
    s = settings_of(entry)
    load_w = float(s.get(CONF_LOAD_W, DEFAULT_LOAD_W))
    base_w = float(s.get(CONF_BASE_LOAD_W, DEFAULT_BASE_LOAD_W))
    solar_title = None
    if s.get(CONF_SOLAR_ENTRY) and (se := hass.config_entries.async_get_entry(s[CONF_SOLAR_ENTRY])):
        solar_title = se.title
    connection.send_result(
        msg["id"],
        {
            "entry_id": entry.entry_id,
            "now": _iso(data.now),
            "currency": data.currency,
            "prices_until": _iso(data.prices_until),
            "solar": {"configured": data.solar_configured, "ok": data.solar_ok, "title": solar_title},
            "warnings": data.warnings,
            "quarters": [
                {
                    "start": _iso(q.start),
                    "import": round(q.import_price, 5),
                    "export": round(q.export_price, 5),
                    "effective": round(q.effective_price(load_w, base_w), 5),
                    "solar_w": round(q.solar_w),
                    "period": q.tariff_key,
                }
                for q in data.quarters
            ],
            "windows": [
                {
                    "minutes": m,
                    "label": window_label(m),
                    "start": _iso(w.start) if w else None,
                    "end": _iso(w.end) if w else None,
                    "average_price": round(w.average_price, 5) if w else None,
                    "average_import_price": round(w.average_import_price, 5) if w else None,
                    "solar_share": round(w.solar_share, 3) if w else None,
                    "cost": round(w.cost, 4) if w else None,
                    "active": bool(w and w.contains(data.now)),
                }
                for m, w in sorted(data.windows.items())
            ],
            "settings": {
                "area": s.get("area"),
                "country": s.get("country"),
                "preset": s.get("preset"),
                "tariff": s.get(CONF_TARIFF),
                "windows": s.get(CONF_WINDOWS),
                "base_load_w": base_w,
                "load_w": load_w,
            },
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/save",
        vol.Optional("tariff"): dict,
        vol.Optional("windows"): [vol.Coerce(int)],
        vol.Optional("base_load_w"): vol.Coerce(float),
        vol.Optional("load_w"): vol.Coerce(float),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Watt Window is not set up")
        return
    options = dict(entry.options)
    try:
        if "tariff" in msg:
            options[CONF_TARIFF] = clean_tariff(msg["tariff"])
        if "windows" in msg:
            options[CONF_WINDOWS] = clean_windows(msg["windows"])
        for key in ("base_load_w", "load_w"):
            if key in msg:
                if not 0 <= msg[key] <= 100_000:
                    raise SettingsError("bad_watts", key)
                options[key] = msg[key]
    except SettingsError as err:
        connection.send_error(msg["id"], err.key, str(err))
        return
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"saved": True})
