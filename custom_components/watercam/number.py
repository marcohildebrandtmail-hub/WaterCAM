"""Number platform for WaterCAM."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEFAULT_INTERVAL,
    DEFAULT_BRIGHTNESS,
    DEFAULT_EXPOSURE_TIME,
    DEFAULT_FOCUS,
    DOMAIN,
    MAX_INTERVAL,
    MIN_INTERVAL,
    STEP_INTERVAL,
)
from .coordinator import WatercamDataUpdateCoordinator
from .sensor import WatercamBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up WaterCAM number entities."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: WatercamDataUpdateCoordinator = (
        entry_data["coordinator"] if isinstance(entry_data, dict) else entry_data
    )

    async_add_entities(
        [
            WatercamMessintervallNumber(coordinator, entry),
            WatercamCameraNumber(
                coordinator,
                entry,
                key="focus",
                default=DEFAULT_FOCUS,
                minimum=0,
                maximum=40,
                step=1,
                icon="mdi:focus-field",
            ),
            WatercamCameraNumber(
                coordinator,
                entry,
                key="brightness",
                default=DEFAULT_BRIGHTNESS,
                minimum=30,
                maximum=255,
                step=1,
                icon="mdi:brightness-6",
            ),
            WatercamCameraNumber(
                coordinator,
                entry,
                key="exposure_time",
                default=DEFAULT_EXPOSURE_TIME / 10,
                minimum=0.1,
                maximum=1000,
                step=0.1,
                icon="mdi:camera-timer",
                unit=UnitOfTime.MILLISECONDS,
                scale=10,
            ),
        ]
    )


class WatercamMessintervallNumber(WatercamBaseEntity, NumberEntity):
    """Number entity to configure the OCR measurement interval in seconds."""

    _attr_translation_key = "messintervall"
    _attr_native_min_value = MIN_INTERVAL
    _attr_native_max_value = MAX_INTERVAL
    _attr_native_step = STEP_INTERVAL
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-cog-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: WatercamDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, entry, "messintervall")

    @property
    def native_value(self) -> float | None:
        """Return the current measurement interval in seconds."""
        if not self.coordinator.data:
            return DEFAULT_INTERVAL
        return float(self.coordinator.data.get("interval", DEFAULT_INTERVAL))

    async def async_set_native_value(self, value: float) -> None:
        """Set new measurement interval in seconds."""
        await self.coordinator.async_set_interval(int(value))


class WatercamCameraNumber(WatercamBaseEntity, NumberEntity):
    """Slider for a persistent V4L2 camera setting."""

    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: WatercamDataUpdateCoordinator,
        entry: ConfigEntry,
        *,
        key: str,
        default: float,
        minimum: float,
        maximum: float,
        step: float,
        icon: str,
        unit: str | None = None,
        scale: float = 1,
    ) -> None:
        """Initialize a camera setting slider."""
        super().__init__(coordinator, entry, key)
        self._key = key
        self._default = default
        self._scale = scale
        self._attr_translation_key = key
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon

    @property
    def native_value(self) -> float:
        """Return the current camera setting."""
        camera = (self.coordinator.data or {}).get("camera", {})
        value = camera.get(self._key)
        if value is None:
            return float(self._default)
        return float(value) / self._scale

    async def async_set_native_value(self, value: float) -> None:
        """Persist the camera setting and trigger a fresh image."""
        raw_value = round(value * self._scale)
        await self.coordinator.async_set_camera_settings({self._key: raw_value})
