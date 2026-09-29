"""Camera platform for WaterCAM."""

from __future__ import annotations

import logging

import aiohttp
from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEFAULT_NAME, DOMAIN, MANUFACTURER, MODEL, SW_VERSION
from .coordinator import WatercamDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up WaterCAM cameras."""
    coordinator: WatercamDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            WatercamLiveCamera(coordinator, entry, "live_snapshot", "/snapshot.jpg"),
            WatercamLiveCamera(coordinator, entry, "lcd_display", "/display.jpg"),
        ]
    )


class WatercamLiveCamera(Camera):
    """Camera entity fetching live snapshots from WaterCAM container."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WatercamDataUpdateCoordinator,
        entry: ConfigEntry,
        key: str,
        image_path: str,
    ) -> None:
        """Initialize the camera."""
        super().__init__()
        self.coordinator = coordinator
        self.entry = entry
        self._image_path = image_path
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key

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

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Fetch latest camera image bytes."""
        url = f"{self.coordinator.base_url}{self._image_path}"
        try:
            async with self.coordinator.session.get(
                url, timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status == 200:
                    return await response.read()
        except Exception as err:
            _LOGGER.warning("Kamerabild konnte nicht von %s geladen werden: %s", url, err)
        return None
