"""Sensor platform for the Cheapest Time integration."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CheapestTimeCoordinator
from .const import (
    ATTR_COST,
    ATTR_FORECAST_COST,
    ATTR_FORECAST_KWH,
    ATTR_RUN_DURATION_MINUTES,
    ATTR_RUN_END_TIME,
    DOMAIN,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the sensor entities for one Cheapest Time usage."""
    coordinator: CheapestTimeCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            CheapestTimeTimeSensor(coordinator, entry),
            CheapestTimeCostSensor(coordinator, entry),
            CheapestTimeConsumptionSensor(coordinator, entry),
        ]
    )


class CheapestTimeEntityBase(CoordinatorEntity[CheapestTimeCoordinator], SensorEntity):
    """Common base for all Cheapest Time sensors of a given usage."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CheapestTimeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Cheapest Time",
            model="Usage optimizer",
        )


class CheapestTimeTimeSensor(CheapestTimeEntityBase):
    """The computed optimal start time for the usage."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "start_time"
    _attr_icon = "mdi:clock-start"

    def __init__(self, coordinator: CheapestTimeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_start_time"

    @property
    def native_value(self):
        return self.coordinator.data.cheapest_time if self.coordinator.data else None

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        if not data or not data.cheapest_time:
            return {}
        run_end = data.cheapest_time + timedelta(minutes=data.run_duration_minutes)
        return {
            ATTR_RUN_END_TIME: run_end.isoformat(),
            ATTR_RUN_DURATION_MINUTES: data.run_duration_minutes,
            ATTR_COST: round(data.optimal_cost, 4) if data.optimal_cost is not None else None,
        }


class CheapestTimeCostSensor(CheapestTimeEntityBase):
    """Cost of the usage if it were launched right now (current 15-minute slot).

    The `forecast_cost` attribute lists the cost for every later slot, from
    the one right after "now" up to the last slot for which prices are
    known (the price horizon) — independent of the optimal start selection.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "cost_now"
    _attr_icon = "mdi:cash"
    _attr_suggested_display_precision = 4

    def __init__(self, coordinator: CheapestTimeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_cost_at_optimal"

    @property
    def native_value(self):
        data = self.coordinator.data
        if not data or data.cost_now is None:
            return None
        return round(data.cost_now, 4)

    @property
    def native_unit_of_measurement(self):
        data = self.coordinator.data
        # The price entity's own unit is typically "<currency>/kWh"; the
        # total cost is expressed in that currency alone.
        if data and data.currency and "/" in data.currency:
            return data.currency.split("/")[0]
        return data.currency if data else None

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        if not data:
            return {}
        return {ATTR_FORECAST_COST: data.forecast_cost}


class CheapestTimeConsumptionSensor(CheapestTimeEntityBase):
    """Expected consumption of the usage for the current 15-minute slot."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_translation_key = "current_consumption"
    _attr_icon = "mdi:flash"
    _attr_suggested_display_precision = 3

    def __init__(self, coordinator: CheapestTimeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_current_consumption"

    @property
    def native_value(self):
        data = self.coordinator.data
        return round(data.current_consumption_kwh, 4) if data else None

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        if not data:
            return {}
        return {ATTR_FORECAST_KWH: data.forecast_kwh}
