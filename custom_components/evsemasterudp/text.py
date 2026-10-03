"""Text entities for EVSE EmProto configuration."""
from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, evse_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    client = data["client"]
    serial = data["serial"]
    base_name = data.get("base_name", f"EVSE {serial}")

    async_add_entities([EVSENicknameText(coordinator, client, serial, base_name)])


class EVSENicknameText(CoordinatorEntity, TextEntity):
    """EVSE display / app nickname."""

    def __init__(self, coordinator, client, serial: str, base_name: str):
        super().__init__(coordinator)
        self.client = client
        self.serial = serial
        self.base_name = base_name
        self._attr_name = f"{base_name} Nickname"
        self._attr_unique_id = f"{serial}_nickname"
        self._attr_icon = "mdi:rename"
        self._attr_native_max = 32
        self._attr_mode = "text"

    @property
    def device_info(self):
        return evse_device_info(self.serial, self.base_name, self.evse_data)

    @property
    def evse_data(self) -> dict:
        return self.coordinator.data.get(self.serial, {}) if self.coordinator.data else {}

    @property
    def native_value(self) -> str | None:
        name = self.evse_data.get("name") or ""
        return name if name != "EVSEMaster" else name

    @property
    def available(self) -> bool:
        data = self.evse_data
        return data.get("online", False) and data.get("logged_in", False)

    async def async_set_value(self, value: str) -> None:
        if await self.client.set_name(self.serial, value.strip()[:32]):
            await self.coordinator.async_request_refresh()
