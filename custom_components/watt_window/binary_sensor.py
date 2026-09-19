"""Binary sensors: are we inside the cheapest window of this length right now?"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cleanup import remove_stale_entities
from .const import window_label
from .coordinator import WattWindowCoordinator
from .sensor import device_info


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coord: WattWindowCoordinator = entry.runtime_data
    entities = [
        InWindowSensor(coord, m, kind) for kind in ("any", "day", "night") for m in sorted(coord.data.windows)
    ]
    remove_stale_entities(hass, entry.entry_id, "binary_sensor", (e.unique_id for e in entities))
    async_add_entities(entities)


class InWindowSensor(CoordinatorEntity[WattWindowCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "in_window"

    def __init__(self, coord: WattWindowCoordinator, minutes: int, kind: str = "any") -> None:
        super().__init__(coord)
        self._minutes = minutes
        self._kind = kind
        suffix = f"{minutes}" if kind == "any" else f"{kind}_{minutes}"
        self._attr_unique_id = f"{coord.config_entry.entry_id}_window_{suffix}_active"
        if kind != "any":
            self._attr_translation_key = f"in_window_{kind}"
        self._attr_device_info = device_info(coord.config_entry.entry_id)
        self._attr_translation_placeholders = {"length": window_label(minutes)}

    @property
    def is_on(self) -> bool:
        w = self.coordinator.data.window(self._kind, self._minutes)
        return bool(w and w.contains(self.coordinator.data.now))

    @property
    def extra_state_attributes(self):
        w = self.coordinator.data.window(self._kind, self._minutes)
        return {
            "window_minutes": self._minutes,
            "start": w.start.isoformat() if w else None,
            "end": w.end.isoformat() if w else None,
        }
