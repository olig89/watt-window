"""Sensors: the price now, and the start of each cheapest window."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cleanup import remove_stale_entities
from .const import CONF_BASE_LOAD_W, CONF_LOAD_W, DEFAULT_BASE_LOAD_W, DEFAULT_LOAD_W, DOMAIN, NAME, window_label
from .coordinator import WattWindowCoordinator
from .advisor import signal as heat_pump_signal
from .core.heatpump import BOOST, HOLD_BACK, NORMAL
from .spare import signal as spare_signal


def device_info(entry_id: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name=NAME,
        manufacturer="Watt Window",
        entry_type=DeviceEntryType.SERVICE,
    )


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coord: WattWindowCoordinator = entry.runtime_data
    entities: list[SensorEntity] = [ImportPriceSensor(coord), ExportPriceSensor(coord), EffectivePriceSensor(coord)]
    if coord.data.solar_configured:
        entities.append(SolarNowSensor(coord))
    if coord.spare.sources.usable:
        entities.append(SpareSolarSensor(coord))
    if coord.heat_pump.enabled:
        entities.append(HeatPumpAdviceSensor(coord))
    for kind in ("any", "day", "night"):
        entities += [WindowStartSensor(coord, m, kind) for m in sorted(coord.data.windows)]
    remove_stale_entities(hass, entry.entry_id, "sensor", (e.unique_id for e in entities))
    async_add_entities(entities)


class _Base(CoordinatorEntity[WattWindowCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coord: WattWindowCoordinator, key: str) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{coord.config_entry.entry_id}_{key}"
        self._attr_device_info = device_info(coord.config_entry.entry_id)

    @property
    def _quarter(self):
        return self.coordinator.data.quarter_at(self.coordinator.data.now)


class _PriceBase(_Base):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4

    @property
    def native_unit_of_measurement(self) -> str:
        return f"{self.coordinator.data.currency}/kWh"


class ImportPriceSensor(_PriceBase):
    """What a kWh from the grid costs right now, all fees and VAT included."""

    _attr_translation_key = "import_price"

    def __init__(self, coord) -> None:
        super().__init__(coord, "import_price")

    @property
    def native_value(self):
        q = self._quarter
        return round(q.import_price, 5) if q else None

    @property
    def extra_state_attributes(self):
        q = self._quarter
        d = self.coordinator.data
        return {
            "tariff_period": q.tariff_key if q else None,
            "spot": round(q.spot, 5) if q else None,
            "prices_known_until": d.prices_until.isoformat() if d.prices_until else None,
        }


class ExportPriceSensor(_PriceBase):
    _attr_translation_key = "export_price"

    def __init__(self, coord) -> None:
        super().__init__(coord, "export_price")

    @property
    def native_value(self):
        q = self._quarter
        return round(q.export_price, 5) if q else None


class EffectivePriceSensor(_PriceBase):
    """What running the configured load right now costs, per kWh, after solar."""

    _attr_translation_key = "effective_price"

    def __init__(self, coord) -> None:
        super().__init__(coord, "effective_price")

    @property
    def native_value(self):
        q = self._quarter
        if not q:
            return None
        s = self.coordinator.settings
        return round(q.effective_price(float(s.get(CONF_LOAD_W, DEFAULT_LOAD_W)),
                                       float(s.get(CONF_BASE_LOAD_W, DEFAULT_BASE_LOAD_W))), 5)


class SolarNowSensor(_Base):
    _attr_translation_key = "solar_forecast"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coord) -> None:
        super().__init__(coord, "solar_forecast")

    @property
    def native_value(self):
        q = self._quarter
        return round(q.solar_w) if q else None


class WindowStartSensor(_Base):
    """Start of the cheapest window of this length (timestamp)."""

    _attr_translation_key = "window_start"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coord, minutes: int, kind: str = "any") -> None:
        super().__init__(coord, f"window_{minutes}_start" if kind == "any" else f"window_{kind}_{minutes}_start")
        self._minutes = minutes
        self._kind = kind
        if kind != "any":
            self._attr_translation_key = f"window_start_{kind}"
        self._attr_translation_placeholders = {"length": window_label(minutes)}

    @property
    def _window(self):
        return self.coordinator.data.window(self._kind, self._minutes)

    @property
    def native_value(self):
        w = self._window
        return w.start if w else None

    @property
    def extra_state_attributes(self):
        w = self._window
        return {
            "window_minutes": self._minutes,
            "end": w.end.isoformat() if w else None,
            "average_price": round(w.average_price, 5) if w else None,
            "average_import_price": round(w.average_import_price, 5) if w else None,
            "solar_share": round(w.solar_share, 3) if w else None,
            "estimated_cost": round(w.cost, 4) if w else None,
            # Any start up to here costs the same; lets an automation wait until it suits you.
            "latest_same_price_start": w.latest_start.isoformat() if w and w.latest_start else None,
        } | self._period_attributes()

    def _period_attributes(self) -> dict:
        if self._kind == "any":
            return {}
        span = self.coordinator.data.periods.get(self._kind, {}).get(self._minutes)
        return {
            "period_start": span[0].isoformat() if span else None,
            "period_end": span[1].isoformat() if span else None,
            # False while part of the period has no published prices: it may still move.
            "settled": self.coordinator.settled(self._kind, self._minutes),
        }


class SpareSolarSensor(_Base):
    """Spare solar right now, in watts. Often an estimate: see the 'basis' attribute."""

    _attr_translation_key = "spare_solar"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coord) -> None:
        super().__init__(coord, "spare_solar")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, spare_signal(self.coordinator.config_entry.entry_id), self.async_write_ha_state)
        )

    @property
    def native_value(self):
        e = self.coordinator.spare.estimate
        return None if e is None or e.watts is None else round(e.watts)

    @property
    def extra_state_attributes(self):
        d = self.coordinator.spare.as_dict()
        return {
            "basis": d["basis"],
            "is_estimate": d["is_estimate"],
            "held_back": d["held_back"],
            "grid_power_w": d["grid_w"],
            "solar_power_w": d["solar_w"],
            "forecast_w": d["forecast_w"],
            "grid_sensor": d["grid_sensor"],
            "solar_sensors": d["solar_sensors"],
        }


class HeatPumpAdviceSensor(_Base):
    """boost / normal / hold_back. Your automation decides what each means for your heat pump."""

    _attr_translation_key = "heat_pump_advice"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [BOOST, NORMAL, HOLD_BACK]

    def __init__(self, coord) -> None:
        super().__init__(coord, "heat_pump_advice")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, heat_pump_signal(self.coordinator.config_entry.entry_id), self.async_write_ha_state)
        )

    @property
    def native_value(self):
        return self.coordinator.heat_pump.mode

    @property
    def extra_state_attributes(self):
        d = self.coordinator.heat_pump.as_dict()
        return {k: d[k] for k in ("reason", "since", "price_now", "average_ahead", "cheapest_ahead",
                                  "cheapest_at", "hours_ahead_known", "store_hours", "margin")}
