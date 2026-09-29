"""Switch platform for WaterCAM."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import WatercamDataUpdateCoordinator
from .sensor import WatercamBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up WaterCAM switch entities."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: WatercamDataUpdateCoordinator = (
        entry_data["coordinator"] if isinstance(entry_data, dict) else entry_data
    )
    async_add_entities([WatercamExposureAutoSwitch(coordinator, entry)])


class WatercamExposureAutoSwitch(WatercamBaseEntity, SwitchEntity):
    """Enable or disable automatic exposure without enabling autofocus."""

    _attr_translation_key = "exposure_auto"
    _attr_icon = "mdi:brightness-auto"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: WatercamDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the automatic exposure switch."""
        super().__init__(coordinator, entry, "exposure_auto")

    @property
    def is_on(self) -> bool:
        """Return whether automatic exposure is enabled."""
        camera = (self.coordinator.data or {}).get("camera", {})
        return bool(camera.get("exposure_auto", True))

    async def async_turn_on(self, **kwargs) -> None:
        """Enable automatic exposure."""
        await self.coordinator.async_set_camera_settings({"exposure_auto": True})

    async def async_turn_off(self, **kwargs) -> None:
        """Enable manual exposure using the configured exposure time."""
        await self.coordinator.async_set_camera_settings({"exposure_auto": False})
