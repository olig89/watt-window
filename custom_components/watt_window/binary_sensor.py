"""Binary sensors: are we inside the cheapest window of this length right now?"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .cleanup import remove_stale_entities
from .const import window_label
from .coordinator import WattWindowCoordinator
from .sensor import device_info
from .spare import signal as spare_signal


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coord: WattWindowCoordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        InWindowSensor(coord, m, kind) for kind in ("any", "day", "night") for m in sorted(coord.data.windows)
    ]
    if coord.spare.sources.usable:
        entities.append(SpareSolarEnough(coord))
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


class SpareSolarEnough(CoordinatorEntity[WattWindowCoordinator], BinarySensorEntity):
    """On when the (smoothed) spare solar covers the appliance watts, with on/off delays."""

    _attr_has_entity_name = True
    _attr_translation_key = "spare_solar_enough"

    def __init__(self, coord: WattWindowCoordinator) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{coord.config_entry.entry_id}_spare_solar_enough"
        self._attr_device_info = device_info(coord.config_entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, spare_signal(self.coordinator.config_entry.entry_id), self.async_write_ha_state)
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.spare.switch.is_on

    @property
    def extra_state_attributes(self):
        m = self.coordinator.spare
        return {
            "needed_w": m.load_w,
            "spare_w": m.as_dict()["watts"],
            "on_after_minutes": m.switch.on_after.total_seconds() / 60,
            "off_after_minutes": m.switch.off_after.total_seconds() / 60,
        }
