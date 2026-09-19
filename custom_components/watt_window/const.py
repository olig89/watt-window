"""Constants for Watt Window."""

from __future__ import annotations

DOMAIN = "watt_window"
NAME = "Watt Window"

CONF_AREA = "area"
CONF_COUNTRY = "country"
CONF_PRESET = "preset"
CONF_TARIFF = "tariff"
CONF_SOLAR_ENTRY = "solar_entry_id"
CONF_BASE_LOAD_W = "base_load_w"
CONF_LOAD_W = "load_w"
CONF_WINDOWS = "windows"  # list of window lengths in minutes

DEFAULT_WINDOWS = [60, 120, 240, 360]
DEFAULT_BASE_LOAD_W = 500
DEFAULT_LOAD_W = 1000
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
