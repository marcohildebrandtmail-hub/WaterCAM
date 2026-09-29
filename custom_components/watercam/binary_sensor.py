"""Binary sensor platform for WaterCAM."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN, MANUFACTURER, MODEL, SW_VERSION
from .coordinator import WatercamDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up WaterCAM binary sensors."""
    coordinator: WatercamDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            WatercamStatusBinarySensor(coordinator, entry),
        ]
    )


class WatercamStatusBinarySensor(CoordinatorEntity[WatercamDataUpdateCoordinator], BinarySensorEntity):
    """OCR connectivity & health status binary sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = "status"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        coordinator: WatercamDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_status"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=DEFAULT_NAME,
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=SW_VERSION,
            configuration_url=f"http://{self.coordinator.host}:{self.coordinator.port}/api/status",
        )

    @property
    def is_on(self) -> bool:
        """Return true if OCR status is ok."""
        if not self.coordinator.data:
            return False
        return self.coordinator.data.get("status") == "ok"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "last_error": self.coordinator.data.get("error") or "",
            "samples": self.coordinator.data.get("samples") or {},
            "last_read": self.coordinator.data.get("updated_at") or "",
        }
