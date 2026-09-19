"""Drop entities a settings change made obsolete (solar turned off, a window length removed)."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er


def remove_stale_entities(hass: HomeAssistant, entry_id: str, platform: str, keep: Iterable[str]) -> None:
    keep = set(keep)
    reg = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(reg, entry_id):
        if ent.domain == platform and ent.unique_id not in keep:
            reg.async_remove(ent.entity_id)
