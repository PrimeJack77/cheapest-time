"""Binary sensor platform for the Cheapest Time integration.

Note: this file is not part of the minimal file list requested by the
user, but it is required to expose the boolean output ("is it the right
moment to run the usage right now?") as a proper Home Assistant boolean
entity (binary_sensor), instead of overloading a regular sensor.
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import OptimalStartCoordinator
from .const import CONF_PROFILE, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the boolean "is optimal now" entity for one usage."""
    coordinator: OptimalStartCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OptimalPeriodBinarySensor(coordinator, entry)])


class OptimalPeriodBinarySensor(CoordinatorEntity[OptimalStartCoordinator], BinarySensorEntity):
    """True when the usage should be running/started right now.

    - Automatic profile: true between the optimal start time and the end
      of the run (start + load curve duration).
    - Manual profile: true only during the single 15-minute slot that was
      identified as the optimal moment to press "start".
    """

    _attr_has_entity_name = True
    _attr_translation_key = "is_optimal_period"
    _attr_icon = "mdi:timer-check-outline"

    def __init__(self, coordinator: OptimalStartCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_is_optimal_period"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Cheapest Time",
            model="Usage optimizer",
        )

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if not data:
            return None
        return data.is_optimal_period

    @property
    def extra_state_attributes(self):
        return {CONF_PROFILE: self.coordinator.profile}
