"""DataUpdateCoordinator for WaterCAM with Live-Push support."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_HOST, CONF_PORT, DEFAULT_HOST, DEFAULT_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)


class WatercamDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching & receiving WaterCAM data."""

    def __init__(self, hass: HomeAssistant, entry_data: dict[str, Any]) -> None:
        """Initialize."""
        self.host = entry_data.get(CONF_HOST, DEFAULT_HOST)
        self.port = entry_data.get(CONF_PORT, DEFAULT_PORT)
        self.base_url = f"http://{self.host}:{self.port}"
        self.session = async_get_clientsession(hass)

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            # Passive safety heartbeat once every hour; active updates arrive instantly via Live-Push
            update_interval=timedelta(hours=1),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from WaterCAM status endpoint."""
        url = f"{self.base_url}/api/status"
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200:
                    raise UpdateFailed(f"HTTP Fehler {response.status} von {url}")
                data = await response.json()
                return data
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Verbindungsfehler zu {url}: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unerwarteter Fehler beim Abruf von {url}: {err}") from err

    async def async_register_webhook(self, webhook_url: str) -> bool:
        """Register the Home Assistant webhook URL on the WaterCAM container."""
        url = f"{self.base_url}/api/webhook_register"
        try:
            payload = {"webhook_url": webhook_url}
            async with self.session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    _LOGGER.info("WaterCAM Live-Push Webhook erfolgreich registriert: %s", webhook_url)
                    return True
                _LOGGER.warning("WaterCAM Webhook-Registrierung antwortete mit HTTP %s", response.status)
        except Exception as err:
            _LOGGER.warning("Konnte Webhook nicht bei WaterCAM registrieren (%s): %s", url, err)
        return False

    async def async_trigger_measure(self) -> bool:
        """Trigger an on-demand measurement via POST /api/measure."""
        url = f"{self.base_url}/api/measure"
        try:
            async with self.session.post(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                return response.status in (200, 202)
        except Exception as err:
            _LOGGER.warning("Fehler beim Triggern der manuellen Messung: %s", err)
            return False

    async def async_set_interval(self, seconds: int) -> bool:
        """Set OCR measurement interval on the WaterCAM container."""
        url = f"{self.base_url}/api/interval"
        try:
            payload = {"interval": int(seconds)}
            async with self.session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    interval = data.get("interval", seconds)
                    if self.data is not None:
                        new_data = dict(self.data)
                        new_data["interval"] = interval
                        self.async_set_updated_data(new_data)
                    _LOGGER.info("WaterCAM Messintervall erfolgreich auf %ss gesetzt", interval)
                    return True
                _LOGGER.warning("WaterCAM Intervall-Aktualisierung antwortete mit HTTP %s", response.status)
        except Exception as err:
            _LOGGER.warning("Fehler beim Setzen des Messintervalls (%s): %s", url, err)
        return False

    async def async_set_camera_settings(self, settings: dict[str, Any]) -> bool:
        """Persist camera controls and trigger a fresh OCR measurement."""
        url = f"{self.base_url}/api/camera"
        try:
            async with self.session.post(
                url, json=settings, timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status in (200, 202):
                    result = await response.json()
                    camera = result.get("camera", settings)
                    new_data = dict(self.data or {})
                    new_data["camera"] = camera
                    self.async_set_updated_data(new_data)
                    return True
                _LOGGER.warning(
                    "WaterCAM Kameraeinstellung antwortete mit HTTP %s", response.status
                )
        except Exception as err:
            _LOGGER.warning("Fehler beim Setzen der Kameraeinstellung (%s): %s", url, err)
        return False

    def async_receive_push(self, data: dict[str, Any]) -> None:
        """Process real-time data pushed from the WaterCAM container."""
        _LOGGER.debug("WaterCAM Live-Push empfangen: %s", data)
        merged = dict(self.data or {})
        merged.update(data)
        self.async_set_updated_data(merged)
