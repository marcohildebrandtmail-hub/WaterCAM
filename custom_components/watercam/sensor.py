"""Sensor platform for WaterCAM."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
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
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: WatercamDataUpdateCoordinator = (
        entry_data["coordinator"] if isinstance(entry_data, dict) else entry_data
    )

    async_add_entities(
        [
            WatercamZaehlerstandSensor(coordinator, entry),
            WatercamSicherheitSensor(coordinator, entry),
            WatercamRawDisplaySensor(coordinator, entry),
        ]
    )


class WatercamBaseEntity(CoordinatorEntity[WatercamDataUpdateCoordinator]):
    """Base entity for WaterCAM."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WatercamDataUpdateCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        """Initialize the base entity."""
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this WaterCAM device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=DEFAULT_NAME,
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=SW_VERSION,
            configuration_url=f"http://{self.coordinator.host}:{self.coordinator.port}/api/status",
        )


class WatercamZaehlerstandSensor(WatercamBaseEntity, SensorEntity):
    """Water meter reading sensor."""

    _attr_translation_key = "zaehlerstand"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:water"

    def __init__(
        self,
        coordinator: WatercamDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "zaehlerstand")

    @property
    def native_value(self) -> float | None:
        """Return the meter reading in m3."""
        if not self.coordinator.data:
            return None
        val = self.coordinator.data.get("value")
        return float(val) if val is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "raw_display": self.coordinator.data.get("raw"),
            "confidence_margin": self.coordinator.data.get("confidence"),
            "last_read": self.coordinator.data.get("updated_at"),
            "source": f"WaterCAM ({self.coordinator.host})",
        }


class WatercamSicherheitSensor(WatercamBaseEntity, SensorEntity):
    """OCR confidence margin sensor."""

    _attr_translation_key = "sicherheit"
    _attr_icon = "mdi:shield-check"

    def __init__(
        self,
        coordinator: WatercamDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "sicherheit")

    @property
    def native_value(self) -> float | None:
        """Return confidence margin."""
        if not self.coordinator.data:
            return None
        val = self.coordinator.data.get("confidence")
        return float(val) if val is not None else None


class WatercamRawDisplaySensor(WatercamBaseEntity, SensorEntity):
    """Raw display string sensor."""

    _attr_translation_key = "rohanzeige"
    _attr_icon = "mdi:counter"

    def __init__(
        self,
        coordinator: WatercamDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "rohanzeige")

    @property
    def native_value(self) -> str | None:
        """Return raw display string."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("raw")
