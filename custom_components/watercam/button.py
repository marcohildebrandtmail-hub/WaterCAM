"""Button platform for WaterCAM."""

from __future__ import annotations

import asyncio

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEFAULT_NAME, DOMAIN, MANUFACTURER, MODEL, SW_VERSION
from .coordinator import WatercamDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up WaterCAM buttons."""
    coordinator: WatercamDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            WatercamMeasureButton(coordinator, entry),
        ]
    )


class WatercamMeasureButton(ButtonEntity):
    """Button to trigger an immediate measurement."""

    _attr_has_entity_name = True
    _attr_translation_key = "measure_now"
    _attr_icon = "mdi:camera-metering-center"

    def __init__(
        self,
        coordinator: WatercamDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the button."""
        self.coordinator = coordinator
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_measure_now"

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

    async def async_press(self) -> None:
        """Trigger immediate measurement and refresh data."""
        await self.coordinator.async_trigger_measure()
        # Wait a few seconds for OCR capture & analysis to finish, then refresh
        await asyncio.sleep(6)
        await self.coordinator.async_request_refresh()
