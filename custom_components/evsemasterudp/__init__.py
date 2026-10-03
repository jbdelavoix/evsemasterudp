"""
EVSE Master UDP integration for Home Assistant.

Supports chargers using the EVSEMaster app UDP protocol, including Morec
and other OEM stations that speak EmProto on port 28376.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .evse_client import get_evse_client, EVSEClient

_LOGGER = logging.getLogger(__name__)

DOMAIN = "evsemasterudp"
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.TEXT,
    Platform.TIME,
]

# Update interval for HA entities (UDP push also refreshes via callback)
UPDATE_INTERVAL = timedelta(seconds=2)


def evse_device_info(serial: str, base_name: str, evse: dict | None = None) -> DeviceInfo:
    """Shared DeviceInfo so brand/model/versions stay up to date."""
    data = evse or {}
    return DeviceInfo(
        identifiers={(DOMAIN, serial)},
        name=base_name,
        manufacturer=data.get("brand") or "EVSE",
        model=data.get("model") or None,
        sw_version=data.get("software_version") or None,
        hw_version=data.get("hardware_version") or None,
    )


class EVSEDataUpdateCoordinator(DataUpdateCoordinator):
    """Coordinator to update EVSE data"""

    def __init__(self, hass: HomeAssistant, client: EVSEClient, serial: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.serial = serial

    async def _async_update_data(self):
        """Fetch EVSE data"""
        try:
            evses = self.client.get_all_evses()
            if not evses:
                _LOGGER.debug("No EVSE found during update")
                return {}
            return evses
        except Exception as err:
            raise UpdateFailed(f"Error updating EVSE data: {err}") from err


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the EVSE integration from a config entry"""

    serial = entry.data.get("serial")
    password = entry.data.get("password")
    port = entry.data.get("port", 28376)

    _LOGGER.info(f"Configuring EVSE {serial} on port {port}")

    client = get_evse_client(port)

    if not client.running:
        try:
            await client.start()
        except Exception as err:
            _LOGGER.error(f"Unable to start EVSE client: {err}")
            return False

    # Wait briefly for discovery broadcasts
    await asyncio.sleep(3)

    if serial and password:
        client.remember_password(serial, password)
        for attempt in range(3):
            success = await client.login(serial, password)

            if success:
                _LOGGER.info(f"Successfully connected to EVSE {serial}")
                break
            _LOGGER.warning(
                f"Connection attempt {attempt + 1}/3 to EVSE {serial} failed"
            )
            if attempt < 2:
                await asyncio.sleep(2)
        else:
            _LOGGER.warning(
                f"Unable to connect to EVSE {serial} after 3 attempts "
                f"(will keep retrying in the background)"
            )

    # Restore persisted fast-change protection
    protection = entry.options.get(
        "fast_change_protection",
        entry.data.get("fast_change_protection", 1),
    )
    await client.set_fast_change_protection(serial, int(protection))

    coordinator = EVSEDataUpdateCoordinator(hass, client, serial)
    await coordinator.async_config_entry_first_refresh()

    async def _on_evse_event(pushed_serial: str, data: dict) -> None:
        if pushed_serial != serial:
            return
        coordinator.async_set_updated_data(client.get_all_evses())

    callback_name = f"coordinator_{entry.entry_id}"
    client.add_callback(callback_name, _on_evse_event)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
        "serial": serial,
        "password": password,
        "base_name": entry.data.get("name") or "EVSEMaster",
        "callback_name": callback_name,
        "entry": entry,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the EVSE integration"""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        client = data["client"]
        client.remove_callback(data["callback_name"])

        if not hass.data[DOMAIN]:
            await client.stop()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the EVSE integration"""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
