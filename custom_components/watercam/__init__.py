"""The WaterCAM Home Assistant integration with Live-Push support."""

from __future__ import annotations

import logging
from aiohttp import web

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import get_url

from .const import DOMAIN
from .coordinator import WatercamDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up WaterCAM from a config entry with Live-Push."""
    coordinator = WatercamDataUpdateCoordinator(hass, entry.data)
    await coordinator.async_config_entry_first_refresh()

    webhook_id = entry.data.get("webhook_id") or f"watercam_{entry.entry_id}"

    async def handle_webhook(
        hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        """Handle incoming Live-Push data from WaterCAM."""
        try:
            payload = await request.json()
            coordinator.async_receive_push(payload)
            return web.Response(text="OK")
        except Exception as err:
            _LOGGER.warning("Fehler beim Verarbeiten des WaterCAM Webhooks: %s", err)
            return web.Response(text="Error", status=400)

    # Register webhook handler in Home Assistant
    try:
        webhook.async_register(
            hass,
            DOMAIN,
            "WaterCAM Live Push",
            webhook_id,
            handle_webhook,
        )
    except Exception as err:
        _LOGGER.debug("Webhook eventuell bereits registriert: %s", err)

    # Determine HA internal URL and register it on the WaterCAM container
    try:
        base_url = get_url(hass, allow_internal=True, allow_ip=True).rstrip("/")
    except Exception:
        base_url = "http://192.168.10.59:8123"

    webhook_url = f"{base_url}/api/webhook/{webhook_id}"
    await coordinator.async_register_webhook(webhook_url)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "webhook_id": webhook_id,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    entry_data = hass.data[DOMAIN].get(entry.entry_id, {})
    webhook_id = entry_data.get("webhook_id")
    if webhook_id:
        webhook.async_unregister(hass, webhook_id)

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
