"""The dashboard card registers itself as a dashboard resource."""

from types import SimpleNamespace

from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.core import HomeAssistant

from custom_components.watt_window import _async_register_card, _async_remove_card

CARD = "/watt_window_static/watt-window-card.js"


class _NoDashboard:
    """A fresh install: no old dashboard config to import resources from."""

    async def async_load(self, force):
        return {}


async def test_card_resource_added_once_updated_and_removed(hass: HomeAssistant):
    resources = ResourceStorageCollection(hass, _NoDashboard())
    hass.data[LOVELACE_DATA] = SimpleNamespace(resources=resources)
    await resources.async_create_item({"res_type": "module", "url": "/hacsfiles/other-card.js"})

    await _async_register_card(hass, f"{CARD}?v=1")
    await _async_register_card(hass, f"{CARD}?v=1")
    ours = [i for i in resources.async_items() if i["url"].startswith(CARD)]
    assert [(i["type"], i["url"]) for i in ours] == [("module", f"{CARD}?v=1")]

    # a new release: same resource, new version so browsers fetch it again
    await _async_register_card(hass, f"{CARD}?v=2")
    assert [i["url"] for i in resources.async_items() if i["url"].startswith(CARD)] == [f"{CARD}?v=2"]

    await _async_remove_card(hass, f"{CARD}?v=2")
    assert [i["url"] for i in resources.async_items()] == ["/hacsfiles/other-card.js"]
