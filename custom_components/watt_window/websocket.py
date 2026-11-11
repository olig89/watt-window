"""The panel's data and settings API (websocket commands)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.loader import async_get_integration

from .const import (
    CONF_BASE_LOAD_W,
    CONF_CAN_EXPORT,
    CONF_DAY_END,
    CONF_DAY_START,
    CONF_HAS_BATTERY,
    CONF_LOAD_W,
    CONF_PLANES,
    CONF_SOLAR_ENTRIES,
    CONF_HP_BALANCE_TEMP,
    CONF_HP_BANK_HOURS,
    CONF_HP_BANK_SHARE,
    CONF_HP_BOOST_PENALTY,
    CONF_HP_BRINE_ENTITY,
    CONF_HP_BRINE_MIN,
    CONF_HP_ENABLED,
    CONF_HP_ENERGY_ENTITY,
    CONF_HP_KWH_PER_DD,
    CONF_HP_MARGIN,
    CONF_HP_MAX_BOOST_HOURS,
    CONF_HP_MIN_BLOCK_HOURS,
    CONF_HP_OUTDOOR_ENTITY,
    CONF_HP_PLANNER,
    CONF_HP_WEATHER_ENTITY,
    DEFAULT_HP_BALANCE_TEMP,
    DEFAULT_HP_BANK_HOURS,
    DEFAULT_HP_BANK_SHARE,
    DEFAULT_HP_BOOST_PENALTY,
    DEFAULT_HP_BRINE_MIN,
    DEFAULT_HP_KWH_PER_DD,
    DEFAULT_HP_MAX_BOOST_HOURS,
    DEFAULT_HP_MIN_BLOCK_HOURS,
    PLANNER_LOOK_AHEAD,
    PLANNERS,
    CONF_HP_MIN_MINUTES,
    CONF_HP_POWER_W,
    CONF_HP_STORE_HOURS,
    DEFAULT_HP_MARGIN,
    DEFAULT_HP_MIN_MINUTES,
    DEFAULT_HP_POWER_W,
    DEFAULT_HP_STORE_HOURS,
    CONF_SPARE_GRID_ENTITY,
    CONF_SPARE_GRID_IMPORT_NEGATIVE,
    CONF_SPARE_NEAR_ZERO_W,
    CONF_SPARE_OFF_MIN,
    CONF_SPARE_ON_MIN,
    CONF_SPARE_SMOOTH_MIN,
    CONF_SPARE_SOLAR_ENTITY,
    DEFAULT_SPARE_NEAR_ZERO_W,
    DEFAULT_SPARE_OFF_MIN,
    DEFAULT_SPARE_ON_MIN,
    DEFAULT_SPARE_SMOOTH_MIN,
    CONF_SOLAR_SOURCE,
    CONF_TARIFF,
    CONF_USE_SOLAR,
    CONF_WINDOWS,
    DEFAULT_BASE_LOAD_W,
    DEFAULT_DAY_END,
    DEFAULT_DAY_START,
    DEFAULT_LOAD_W,
    DOMAIN,
    FORECAST_SOLAR_DOMAIN,
    settings_of,
    solar_entry_ids,
    solar_entry_label,
    window_label,
)
from .core.heatplan import fit_heat_need
from .sources.solar import solar_source_kind
from .validation import SOLAR_SOURCES, SettingsError, clean_day_hours, clean_planes, clean_tariff, clean_windows


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_data)
    websocket_api.async_register_command(hass, ws_save)
    websocket_api.async_register_command(hass, ws_heat_need_fit)


def _entry(hass: HomeAssistant):
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0] if entries else None


def _power_sensors(hass: HomeAssistant) -> list[dict]:
    """Live power sensors (W/kW) the user can pick for spare solar."""
    out = []
    for st in hass.states.async_all("sensor"):
        if st.attributes.get("device_class") == "power" or st.attributes.get("unit_of_measurement") in ("W", "kW"):
            out.append({"entity_id": st.entity_id, "name": st.attributes.get("friendly_name") or st.entity_id})
    return sorted(out, key=lambda o: o["name"].lower())


def _entities(hass: HomeAssistant, domain: str, keep=lambda st: True) -> list[dict]:
    out = [
        {"entity_id": st.entity_id, "name": st.attributes.get("friendly_name") or st.entity_id}
        for st in hass.states.async_all(domain)
        if keep(st)
    ]
    return sorted(out, key=lambda o: o["name"].lower())


def _heat_pump_sensors(hass: HomeAssistant) -> dict:
    """Entities the heat pump settings can pick from."""
    return {
        "weather": _entities(hass, "weather"),
        "energy": _entities(hass, "sensor", lambda st: st.attributes.get("device_class") == "energy"
                            or st.attributes.get("unit_of_measurement") in ("kWh", "Wh", "MWh")),
        "temperature": _entities(hass, "sensor", lambda st: st.attributes.get("device_class") == "temperature"
                                 or st.attributes.get("unit_of_measurement") in ("°C", "°F")),
    }


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
    solar_options = [
        {"entry_id": e.entry_id, "title": solar_entry_label(e)}
        for e in hass.config_entries.async_entries(FORECAST_SOLAR_DOMAIN)
    ]
    chosen = solar_entry_ids(s)

    def brief(w, kind, m):
        if w is None:
            return None
        return {
            "settled": coord.settled(kind, m),
            "start": _iso(w.start),
            "end": _iso(w.end),
            "latest_start": _iso(w.latest_start),
            "average_price": round(w.average_price, 5),
            "active": w.contains(data.now),
        }
    connection.send_result(
        msg["id"],
        {
            "entry_id": entry.entry_id,
            # The page compares this with its own version: after an update a browser
            # tab keeps the old page code until it is reloaded.
            "version": str((await async_get_integration(hass, DOMAIN)).version),
            "now": _iso(data.now),
            "currency": data.currency,
            "prices_until": _iso(data.prices_until),
            "next_prices_at": _iso(data.next_prices_at),
            "price_source": data.price_source,
            "solar": {
                "configured": data.solar_configured,
                "ok": data.solar_ok,
                "title": data.solar_title,
                "credit": data.solar_credit,
                "paused": data.solar_paused,
            },
            "warnings": data.warnings,
            "spare": coord.spare.as_dict(),
            "heat_pump": coord.heat_pump.as_dict(),
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
                    "latest_start": _iso(w.latest_start) if w and w.latest_start else None,
                    "average_price": round(w.average_price, 5) if w else None,
                    "average_import_price": round(w.average_import_price, 5) if w else None,
                    "solar_share": round(w.solar_share, 3) if w else None,
                    "cost": round(w.cost, 4) if w else None,
                    "active": bool(w and w.contains(data.now)),
                    "day": brief(data.window("day", m), "day", m),
                    "night": brief(data.window("night", m), "night", m),
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
                "has_battery": bool(s.get(CONF_HAS_BATTERY, False)),
                "day_start": int(s.get(CONF_DAY_START, DEFAULT_DAY_START)),
                "day_end": int(s.get(CONF_DAY_END, DEFAULT_DAY_END)),
                "solar_source": solar_source_kind(s),
                "can_export": bool(s.get(CONF_CAN_EXPORT, True)),
                "use_solar": bool(s.get(CONF_USE_SOLAR, True)),
                "heat_pump_advice": bool(s.get(CONF_HP_ENABLED, False)),
                "heat_pump_power_w": s.get(CONF_HP_POWER_W, DEFAULT_HP_POWER_W),
                "heat_pump_store_hours": s.get(CONF_HP_STORE_HOURS, DEFAULT_HP_STORE_HOURS),
                "heat_pump_margin": s.get(CONF_HP_MARGIN, DEFAULT_HP_MARGIN),
                "heat_pump_min_minutes": s.get(CONF_HP_MIN_MINUTES, DEFAULT_HP_MIN_MINUTES),
                "heat_pump_planner": s.get(CONF_HP_PLANNER, PLANNER_LOOK_AHEAD),
                "heat_pump_weather_entity": s.get(CONF_HP_WEATHER_ENTITY) or coord.heat_pump.weather_entity,
                "heat_pump_kwh_per_degree_day": s.get(CONF_HP_KWH_PER_DD, DEFAULT_HP_KWH_PER_DD),
                "heat_pump_balance_temp": s.get(CONF_HP_BALANCE_TEMP, DEFAULT_HP_BALANCE_TEMP),
                "heat_pump_boost_penalty": s.get(CONF_HP_BOOST_PENALTY, DEFAULT_HP_BOOST_PENALTY),
                "heat_pump_min_block_hours": s.get(CONF_HP_MIN_BLOCK_HOURS, DEFAULT_HP_MIN_BLOCK_HOURS),
                "heat_pump_brine_entity": s.get(CONF_HP_BRINE_ENTITY),
                "heat_pump_max_boost_hours": s.get(CONF_HP_MAX_BOOST_HOURS, DEFAULT_HP_MAX_BOOST_HOURS),
                "heat_pump_brine_min": s.get(CONF_HP_BRINE_MIN, DEFAULT_HP_BRINE_MIN),
                "heat_pump_bank_share": s.get(CONF_HP_BANK_SHARE, DEFAULT_HP_BANK_SHARE),
                "heat_pump_bank_hours": s.get(CONF_HP_BANK_HOURS, DEFAULT_HP_BANK_HOURS),
                "heat_pump_energy_entity": s.get(CONF_HP_ENERGY_ENTITY),
                "heat_pump_outdoor_entity": s.get(CONF_HP_OUTDOOR_ENTITY),
                "heat_pump_sensors": _heat_pump_sensors(hass),
                "spare_grid_entity": s.get(CONF_SPARE_GRID_ENTITY),
                "spare_grid_import_negative": bool(s.get(CONF_SPARE_GRID_IMPORT_NEGATIVE, False)),
                "spare_solar_entity": s.get(CONF_SPARE_SOLAR_ENTITY),
                "spare_smoothing_minutes": s.get(CONF_SPARE_SMOOTH_MIN, DEFAULT_SPARE_SMOOTH_MIN),
                "spare_on_after_minutes": s.get(CONF_SPARE_ON_MIN, DEFAULT_SPARE_ON_MIN),
                "spare_off_after_minutes": s.get(CONF_SPARE_OFF_MIN, DEFAULT_SPARE_OFF_MIN),
                "spare_near_zero_w": s.get(CONF_SPARE_NEAR_ZERO_W, DEFAULT_SPARE_NEAR_ZERO_W),
                "power_sensors": _power_sensors(hass),
                "solar_planes": s.get(CONF_PLANES) or [],
                "solar_entry_ids": chosen,
                "solar_options": solar_options,
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
        vol.Optional("has_battery"): bool,
        vol.Optional("day_start"): vol.Coerce(int),
        vol.Optional("day_end"): vol.Coerce(int),
        vol.Optional("solar_entry_ids"): [str],
        vol.Optional("solar_source"): vol.In(SOLAR_SOURCES),
        vol.Optional("can_export"): bool,
        vol.Optional("use_solar"): bool,
        vol.Optional("heat_pump_advice"): bool,
        vol.Optional("heat_pump_power_w"): vol.Coerce(float),
        vol.Optional("heat_pump_store_hours"): vol.Coerce(float),
        vol.Optional("heat_pump_margin"): vol.Coerce(float),
        vol.Optional("heat_pump_min_minutes"): vol.Coerce(float),
        vol.Optional("heat_pump_planner"): vol.In(PLANNERS),
        vol.Optional("heat_pump_weather_entity"): vol.Any(None, str),
        vol.Optional("heat_pump_kwh_per_degree_day"): vol.Coerce(float),
        vol.Optional("heat_pump_balance_temp"): vol.Coerce(float),
        vol.Optional("heat_pump_boost_penalty"): vol.Coerce(float),
        vol.Optional("heat_pump_min_block_hours"): vol.Coerce(float),
        vol.Optional("heat_pump_brine_entity"): vol.Any(None, str),
        vol.Optional("heat_pump_max_boost_hours"): vol.Coerce(float),
        vol.Optional("heat_pump_brine_min"): vol.Coerce(float),
        vol.Optional("heat_pump_bank_share"): vol.Coerce(float),
        vol.Optional("heat_pump_bank_hours"): vol.Coerce(float),
        vol.Optional("heat_pump_energy_entity"): vol.Any(None, str),
        vol.Optional("heat_pump_outdoor_entity"): vol.Any(None, str),
        vol.Optional("spare_grid_entity"): vol.Any(None, str),
        vol.Optional("spare_grid_import_negative"): bool,
        vol.Optional("spare_solar_entity"): vol.Any(None, str),
        vol.Optional("spare_smoothing_minutes"): vol.Coerce(float),
        vol.Optional("spare_on_after_minutes"): vol.Coerce(float),
        vol.Optional("spare_off_after_minutes"): vol.Coerce(float),
        vol.Optional("spare_near_zero_w"): vol.Coerce(float),
        vol.Optional("solar_planes"): [dict],
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
        if "has_battery" in msg:
            options[CONF_HAS_BATTERY] = msg["has_battery"]
        if "day_start" in msg or "day_end" in msg:
            current = settings_of(entry)
            start, end = clean_day_hours(
                msg.get("day_start", current.get(CONF_DAY_START, DEFAULT_DAY_START)),
                msg.get("day_end", current.get(CONF_DAY_END, DEFAULT_DAY_END)),
            )
            options[CONF_DAY_START], options[CONF_DAY_END] = start, end
        if "solar_entry_ids" in msg:
            known = {e.entry_id for e in hass.config_entries.async_entries(FORECAST_SOLAR_DOMAIN)}
            ids = list(dict.fromkeys(msg["solar_entry_ids"]))
            if any(i not in known for i in ids):
                raise SettingsError("bad_solar")
            options[CONF_SOLAR_ENTRIES] = ids
        for key, lo, hi in (
            (CONF_SPARE_SMOOTH_MIN, 0, 60), (CONF_SPARE_ON_MIN, 0, 60),
            (CONF_SPARE_OFF_MIN, 0, 60), (CONF_SPARE_NEAR_ZERO_W, 0, 5000),
            (CONF_HP_POWER_W, 100, 50000), (CONF_HP_STORE_HOURS, 1, 24),
            (CONF_HP_MARGIN, 0, 1), (CONF_HP_MIN_MINUTES, 0, 240),
            (CONF_HP_KWH_PER_DD, 0.05, 100), (CONF_HP_BALANCE_TEMP, 5, 25),
            (CONF_HP_BOOST_PENALTY, 0, 0.5), (CONF_HP_MIN_BLOCK_HOURS, 0.25, 8),
            (CONF_HP_MAX_BOOST_HOURS, 1, 24), (CONF_HP_BRINE_MIN, -15, 15),
            (CONF_HP_BANK_SHARE, 0, 1), (CONF_HP_BANK_HOURS, 0.5, 12),
        ):
            if key in msg:
                if not lo <= msg[key] <= hi:
                    raise SettingsError("bad_spare", key)
                options[key] = msg[key]
        if "heat_pump_planner" in msg:
            options[CONF_HP_PLANNER] = msg["heat_pump_planner"]
        for key in (CONF_SPARE_GRID_ENTITY, CONF_SPARE_SOLAR_ENTITY, CONF_HP_WEATHER_ENTITY,
                    CONF_HP_BRINE_ENTITY, CONF_HP_ENERGY_ENTITY, CONF_HP_OUTDOOR_ENTITY):
            if key in msg:
                if msg[key] and hass.states.get(msg[key]) is None:
                    raise SettingsError("bad_spare_sensor", key)
                options[key] = msg[key] or None
        if "heat_pump_advice" in msg:
            options[CONF_HP_ENABLED] = msg["heat_pump_advice"]
        if "spare_grid_import_negative" in msg:
            options[CONF_SPARE_GRID_IMPORT_NEGATIVE] = msg["spare_grid_import_negative"]
        if "use_solar" in msg:
            options[CONF_USE_SOLAR] = msg["use_solar"]
        if "can_export" in msg:
            options[CONF_CAN_EXPORT] = msg["can_export"]
        if "solar_planes" in msg:
            options[CONF_PLANES] = clean_planes(msg["solar_planes"])
        if "solar_source" in msg:
            kind = msg["solar_source"]
            if kind == "open_meteo" and not options.get(CONF_PLANES, settings_of(entry).get(CONF_PLANES)):
                raise SettingsError("bad_planes")
            if kind == "forecast_solar" and not options.get(CONF_SOLAR_ENTRIES, solar_entry_ids(settings_of(entry))):
                raise SettingsError("bad_solar")
            options[CONF_SOLAR_SOURCE] = kind
    except SettingsError as err:
        connection.send_error(msg["id"], err.key, str(err))
        return
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"saved": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/heat_need_fit",
        vol.Required("energy_entity"): str,
        vol.Required("outdoor_entity"): str,
        vol.Optional("days", default=45): vol.All(vol.Coerce(int), vol.Range(min=7, max=365)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_heat_need_fit(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Suggest kWh per degree-day and the heating cut-off from the recorder's daily statistics."""
    try:
        days = await async_daily_heat_use(hass, msg["energy_entity"], msg["outdoor_entity"], msg["days"])
    except Exception as err:  # noqa: BLE001 - recorder missing or no statistics
        connection.send_error(msg["id"], "no_history", str(err))
        return
    fit = fit_heat_need(days)
    connection.send_result(
        msg["id"],
        {
            "days_found": len(days),
            "fit": None if fit is None else {
                "kwh_per_degree_day": round(fit.kwh_per_degree_day, 2),
                "balance_temp": round(fit.balance_temp, 1),
                "r2": round(fit.r2, 2),
                "days": fit.days,
            },
        },
    )


async def async_daily_heat_use(hass: HomeAssistant, energy: str, outdoor: str, days: int) -> list[tuple[float, float]]:
    """(mean outdoor °C, heating kWh) per whole local day, from long-term statistics."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period
    from homeassistant.util import dt as dt_util

    end = dt_util.start_of_local_day()
    start = end - timedelta(days=days)
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, end, {energy, outdoor}, "day", None, {"change", "mean"}
    )
    temps = {row["start"]: row.get("mean") for row in stats.get(outdoor, [])}
    out = []
    for row in stats.get(energy, []):
        t = temps.get(row["start"])
        if t is not None and row.get("change") is not None:
            out.append((float(t), float(row["change"])))
    return out
