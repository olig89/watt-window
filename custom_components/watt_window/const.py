"""Constants for Watt Window."""

from __future__ import annotations

DOMAIN = "watt_window"
NAME = "Watt Window"

CONF_AREA = "area"
CONF_NORDPOOL_ENTRY = "nordpool_entry_id"  # which Nord Pool setup (several may exist)
CONF_COUNTRY = "country"
CONF_PRESET = "preset"
CONF_TARIFF = "tariff"
CONF_SOLAR_ENTRY = "solar_entry_id"  # 0.1-0.3: a single Forecast.Solar entry
CONF_SOLAR_ENTRIES = "solar_entry_ids"  # several solar forecast setups; their forecasts are added up
CONF_SOLAR_SOURCE = "solar_source"  # none | open_meteo | forecast_solar
CONF_CAN_EXPORT = "can_export"  # False: spare solar is throttled away, so using it is free
CONF_PLANES = "solar_planes"  # [{name, kwp, tilt, direction}] for open_meteo
CONF_DAY_START = "day_window_start"  # local hour the "daytime" windows start
CONF_DAY_END = "day_window_end"
CONF_BASE_LOAD_W = "base_load_w"
# Saved now; the battery logic (bank surplus solar vs use it at once) comes later.
CONF_HAS_BATTERY = "has_battery"
CONF_LOAD_W = "load_w"
CONF_WINDOWS = "windows"  # list of window lengths in minutes

DEFAULT_WINDOWS = [60, 120, 240]
DEFAULT_BASE_LOAD_W = 500
DEFAULT_LOAD_W = 1000
DEFAULT_DAY_START = 8
DEFAULT_DAY_END = 20
MAX_WINDOW_MINUTES = 24 * 60

NORDPOOL_DOMAIN = "nordpool"
FORECAST_SOLAR_DOMAIN = "forecast_solar"

PANEL_URL = "watt-window"
PANEL_COMPONENT = "watt-window-panel"
STATIC_URL = "/watt_window_static"


def window_label(minutes: int) -> str:
    """60 -> '1 h', 90 -> '1.5 h', 45 -> '45 min'."""
    if minutes % 60 == 0:
        return f"{minutes // 60} h"
    if minutes > 60 and minutes % 30 == 0:
        return f"{minutes / 60:g} h"
    return f"{minutes} min"


def settings_of(entry) -> dict:
    """The effective settings of a config entry: data overlaid by options."""
    return {**entry.data, **entry.options}


def solar_entry_ids(s: dict) -> list[str]:
    """Forecast.Solar entries in use, reading the pre-0.4 single-entry setting too."""
    if CONF_SOLAR_ENTRIES in s:
        return list(s[CONF_SOLAR_ENTRIES] or [])
    return [s[CONF_SOLAR_ENTRY]] if s.get(CONF_SOLAR_ENTRY) else []


def solar_entry_label(entry) -> str:
    """Forecast.Solar entries are created with an empty title: name them by their planes."""
    if entry.title:
        return entry.title
    planes = [sub.title for sub in getattr(entry, "subentries", {}).values() if sub.title]
    return "Forecast.Solar " + (", ".join(planes) if planes else entry.entry_id[:6])
