"""Watt Window: the cheapest time to use electricity, with solar taken into account."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, NAME, PANEL_COMPONENT, PANEL_URL, STATIC_URL
from .coordinator import WattWindowCoordinator
from .spare import SpareMonitor
from .websocket import async_register_websocket

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type WattWindowConfigEntry = ConfigEntry[WattWindowCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_register_websocket(hass)
    return True


async def _async_register_panel(hass: HomeAssistant) -> None:
    """Sidebar page. Skipped quietly where the frontend isn't loaded (tests)."""
    if hass.data.get(f"{DOMAIN}_panel") or "frontend" not in hass.config.components:
        return
    from homeassistant.components import panel_custom
    from homeassistant.components.http import StaticPathConfig

    www = Path(__file__).parent / "www"
    await hass.http.async_register_static_paths([StaticPathConfig(STATIC_URL, str(www), False)])
    version = (await hass.async_add_executor_job((www / "watt-window-panel.js").stat)).st_mtime_ns
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title=NAME,
        sidebar_icon="mdi:timer-sand-complete",
        module_url=f"{STATIC_URL}/watt-window-panel.js?v={version}",
        require_admin=True,
    )
    hass.data[f"{DOMAIN}_panel"] = True


async def async_setup_entry(hass: HomeAssistant, entry: WattWindowConfigEntry) -> bool:
    coordinator = WattWindowCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    coordinator.spare = SpareMonitor(hass, coordinator)
    await coordinator.spare.async_start()
    entry.async_on_unload(coordinator.spare.async_stop)

    async def _tick(_now) -> None:
        await coordinator.async_refresh()

    # Recompute just after every quarter-hour, so "in window" flips on time.
    entry.async_on_unload(
        async_track_time_change(hass, _tick, minute=[0, 15, 30, 45], second=5)
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await _async_register_panel(hass)
    return True


async def _async_reload(hass: HomeAssistant, entry: WattWindowConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: WattWindowConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: WattWindowConfigEntry) -> None:
    if hass.data.pop(f"{DOMAIN}_panel", None):
        from homeassistant.components import frontend

        frontend.async_remove_panel(hass, PANEL_URL)
