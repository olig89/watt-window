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
from .advisor import HeatPumpAdvisor
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
    card = await hass.async_add_executor_job((www / "watt-window-card.js").stat)
    card_url = f"{STATIC_URL}/watt-window-card.js?v={card.st_mtime_ns}"
    await _async_register_card(hass, card_url)
    hass.data[f"{DOMAIN}_panel"] = card_url


def _card_resources(hass: HomeAssistant):
    """The dashboards' resource list, or None where it is kept in YAML."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    lovelace = hass.data.get(LOVELACE_DATA)
    resources = getattr(lovelace, "resources", None)
    return resources if hasattr(resources, "async_create_item") else None


async def _async_register_card(hass: HomeAssistant, url: str) -> None:
    """Add the dashboard card as a dashboard resource, so no one adds it by hand.

    A resource, not an extra frontend module: dashboards wait for resources before
    building cards, but an extra module can arrive after the card was built, and
    then the card shows a configuration error until the page is reloaded.
    """
    resources = _card_resources(hass)
    if resources is None:
        # Resources kept in YAML: an extra module is the best we can do here.
        from homeassistant.components.frontend import add_extra_js_url

        add_extra_js_url(hass, url)
        return
    await resources.async_get_info()  # loads the stored list
    path = url.split("?")[0]
    for item in resources.async_items():
        if item.get("url", "").split("?")[0] == path:
            if item["url"] != url:
                await resources.async_update_item(item["id"], {"res_type": "module", "url": url})
            return
    await resources.async_create_item({"res_type": "module", "url": url})


async def _async_remove_card(hass: HomeAssistant, url: str) -> None:
    resources = _card_resources(hass)
    if resources is None:
        from homeassistant.components.frontend import remove_extra_js_url

        remove_extra_js_url(hass, url)
        return
    await resources.async_get_info()
    path = url.split("?")[0]
    for item in list(resources.async_items()):
        if item.get("url", "").split("?")[0] == path:
            await resources.async_delete_item(item["id"])


async def async_setup_entry(hass: HomeAssistant, entry: WattWindowConfigEntry) -> bool:
    coordinator = WattWindowCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    coordinator.spare = SpareMonitor(hass, coordinator)
    await coordinator.spare.async_start()
    entry.async_on_unload(coordinator.spare.async_stop)
    coordinator.heat_pump = HeatPumpAdvisor(hass, coordinator)
    coordinator.heat_pump.async_start()
    entry.async_on_unload(coordinator.heat_pump.async_stop)

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
    if card_url := hass.data.pop(f"{DOMAIN}_panel", None):
        from homeassistant.components import frontend

        frontend.async_remove_panel(hass, PANEL_URL)
        await _async_remove_card(hass, card_url)
