"""Sensors per car: logbook (for the dashboard), fuel used, fuel cost, tank price, consumption."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .manager import Car


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    car: Car = entry.runtime_data
    async_add_entities(
        [
            LogbookSensor(car),
            FuelUsedSensor(car),
            FuelCostSensor(car),
            TankPriceSensor(car),
            ConsumptionSensor(car),
        ]
    )


class _Base(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    key = ""

    def __init__(self, car: Car) -> None:
        self.car = car
        self._attr_unique_id = f"{car.entry.entry_id}_{self.key}"
        self._attr_translation_key = self.key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, car.entry.entry_id)},
            name=car.name,
            manufacturer="Auto logboek",
            model="Ritten en tankbeurten",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.car.signal, self._update)
        )

    @callback
    def _update(self) -> None:
        self.async_write_ha_state()


class LogbookSensor(_Base):
    """State = refuels without an amount; attributes = everything the dashboard needs."""

    key = "logboek"
    _attr_icon = "mdi:car-clock"
    _attr_native_unit_of_measurement = "open"
    _unrecorded_attributes = frozenset(
        {"maanden", "ritten", "tankbeurten", "geladen_maanden", "versie"}
    )

    @property
    def native_value(self) -> int:
        return self.car.state["open_bedragen"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.car.snapshot()


class FuelUsedSensor(_Base):
    """Liters driven (trip km x consumption). Gas class in L: usable in the energy dashboard."""

    key = "brandstof_verbruikt"
    _attr_icon = "mdi:gas-station"
    _attr_device_class = SensorDeviceClass.GAS
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float:
        return self.car.state["liters_totaal"]


class FuelCostSensor(_Base):
    """Fuel cost of all trips; changes back in time when an amount is filled in later."""

    key = "brandstofkosten"
    _attr_icon = "mdi:cash"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "EUR"
    _attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float:
        return self.car.state["kosten_totaal"]


class TankPriceSensor(_Base):
    """Average price per liter of what is in the tank now."""

    key = "tankprijs"
    _attr_icon = "mdi:currency-eur"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "EUR/L"
    _attr_suggested_display_precision = 3

    @property
    def native_value(self) -> float | None:
        return self.car.state["tankprijs"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"voorlopig": self.car.state["voorlopig"]}


class ConsumptionSensor(_Base):
    """Learned consumption (fuel gauge drop per km), else the configured default."""

    key = "verbruik"
    _attr_icon = "mdi:speedometer"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "L/100 km"
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float:
        return self.car.state["verbruik"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"geleerd": self.car.state["verbruik_geleerd"]}
