"""DataUpdateCoordinator for WaterCAM."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL, DEFAULT_HOST, DEFAULT_PORT, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class WatercamDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching WaterCAM data from container API."""

    def __init__(self, hass: HomeAssistant, entry_data: dict[str, Any]) -> None:
        """Initialize."""
        self.host = entry_data.get(CONF_HOST, DEFAULT_HOST)
        self.port = entry_data.get(CONF_PORT, DEFAULT_PORT)
        scan_interval = entry_data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        self.base_url = f"http://{self.host}:{self.port}"
        self.session = async_get_clientsession(hass)

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
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

    async def async_trigger_measure(self) -> bool:
        """Trigger an on-demand measurement via POST /api/measure."""
        url = f"{self.base_url}/api/measure"
        try:
            async with self.session.post(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                return response.status in (200, 202)
        except Exception as err:
            _LOGGER.warning("Fehler beim Triggern der manuellen Messung: %s", err)
            return False
