"""Sensor platform for WaterCAM."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

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
            WatercamLastSuccessfulReadSensor(coordinator, entry),
            WatercamLastAttemptSensor(coordinator, entry),
            WatercamVerbrauchSensor(
                coordinator, entry, "verbrauch_tag", "day", "mdi:calendar-today"
            ),
            WatercamVerbrauchSensor(
                coordinator, entry, "verbrauch_woche", "week", "mdi:calendar-week"
            ),
            WatercamVerbrauchSensor(
                coordinator, entry, "verbrauch_monat", "month", "mdi:calendar-month"
            ),
            WatercamVerbrauchSensor(
                coordinator, entry, "verbrauch_jahr", "year", "mdi:calendar-range"
            ),
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
            "last_read": self.coordinator.data.get("last_success_at"),
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


class WatercamLastSuccessfulReadSensor(WatercamBaseEntity, SensorEntity):
    """Timestamp of the last successfully accepted meter reading."""

    _attr_translation_key = "letzte_auslesung"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check-outline"

    def __init__(
        self,
        coordinator: WatercamDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the timestamp sensor."""
        super().__init__(coordinator, entry, "letzte_auslesung")

    @property
    def native_value(self) -> datetime | None:
        """Return the last successful reading as a timezone-aware datetime."""
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get("last_success_at")
        if not value and self.coordinator.data.get("status") == "ok":
            value = self.coordinator.data.get("updated_at")
        return dt_util.parse_datetime(value) if value else None


class WatercamLastAttemptSensor(WatercamBaseEntity, SensorEntity):
    """Timestamp of the last measurement attempt, successful or not."""

    _attr_translation_key = "letzter_versuch"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-outline"

    def __init__(
        self,
        coordinator: WatercamDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the timestamp sensor."""
        super().__init__(coordinator, entry, "letzter_versuch")

    @property
    def native_value(self) -> datetime | None:
        """Return the last attempted reading as a timezone-aware datetime."""
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get("updated_at")
        return dt_util.parse_datetime(value) if value else None


class WatercamVerbrauchSensor(WatercamBaseEntity, RestoreEntity, SensorEntity):
    """Sensor tracking water consumption in Liters for a specific time period."""

    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: WatercamDataUpdateCoordinator,
        entry: ConfigEntry,
        key: str,
        period_type: str,
        icon: str,
    ) -> None:
        """Initialize the periodic consumption sensor."""
        super().__init__(coordinator, entry, key)
        self._attr_translation_key = key
        self._attr_icon = icon
        self._period_type = period_type
        self._period_key: str | None = None
        self._baseline_reading: float | None = None
        self._consumption_liters: float = 0.0

    def _get_current_period_key(self) -> str:
        """Return the period key according to local time."""
        now = dt_util.now()
        if self._period_type == "day":
            return now.strftime("%Y-%m-%d")
        if self._period_type == "week":
            iso = now.isocalendar()
            return f"{iso.year}-W{iso.week:02d}"
        if self._period_type == "month":
            return now.strftime("%Y-%m")
        if self._period_type == "year":
            return now.strftime("%Y")
        return now.strftime("%Y-%m-%d")

    async def async_added_to_hass(self) -> None:
        """Restore last state and baseline upon startup."""
        await super().async_added_to_hass()
        current_period = self._get_current_period_key()
        last_state = await self.async_get_last_state()

        if last_state and last_state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            attrs = last_state.attributes
            stored_period = attrs.get("period_key")
            stored_baseline = attrs.get("baseline_reading_m3")

            if stored_period == current_period and stored_baseline is not None:
                try:
                    self._period_key = stored_period
                    self._baseline_reading = float(stored_baseline)
                    self._consumption_liters = float(last_state.state)
                except (ValueError, TypeError):
                    self._period_key = current_period
                    self._baseline_reading = None
                    self._consumption_liters = 0.0
            else:
                self._period_key = current_period
                self._baseline_reading = None
                self._consumption_liters = 0.0
        else:
            self._period_key = current_period
            self._baseline_reading = None
            self._consumption_liters = 0.0

        self._update_consumption()

    def _update_consumption(self) -> None:
        """Calculate consumption in Liters from coordinator reading."""
        if not self.coordinator.data:
            return
        val = self.coordinator.data.get("value")
        if val is None:
            return
        try:
            current_reading = float(val)
        except (ValueError, TypeError):
            return

        current_period = self._get_current_period_key()
        if self._period_key != current_period or self._baseline_reading is None:
            self._period_key = current_period
            self._baseline_reading = current_reading
            self._consumption_liters = 0.0
        else:
            delta = current_reading - self._baseline_reading
            if delta < 0:
                self._baseline_reading = current_reading
                self._consumption_liters = 0.0
            else:
                self._consumption_liters = round(delta * 1000.0, 1)

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_consumption()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float | None:
        """Return current period consumption in Liters."""
        return self._consumption_liters

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return period and baseline attributes."""
        return {
            "period": self._period_type,
            "period_key": self._period_key,
            "baseline_reading_m3": self._baseline_reading,
            "unit": "L",
        }
