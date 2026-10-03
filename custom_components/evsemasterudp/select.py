"""Select entities for EVSE EmProto configuration."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, evse_device_info
from .protocol.config_maps import (
    LANGUAGE_BY_CODE,
    LANGUAGE_OPTIONS,
    START_MODE_BY_CODE,
    START_MODE_OPTIONS,
    TEMP_UNIT_BY_CODE,
    TEMP_UNIT_OPTIONS,
)

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

    async_add_entities(
        [
            EVSELanguageSelect(coordinator, client, serial, base_name),
            EVSETemperatureUnitSelect(coordinator, client, serial, base_name),
            EVSEStartModeSelect(coordinator, client, serial, base_name),
        ]
    )


class EVSEBaseSelect(CoordinatorEntity, SelectEntity):
    def __init__(self, coordinator, client, serial: str, base_name: str):
        super().__init__(coordinator)
        self.client = client
        self.serial = serial
        self.base_name = base_name

    @property
    def device_info(self):
        return evse_device_info(self.serial, self.base_name, self.evse_data)

    @property
    def evse_data(self) -> dict:
        return self.coordinator.data.get(self.serial, {}) if self.coordinator.data else {}

    @property
    def available(self) -> bool:
        data = self.evse_data
        return data.get("online", False) and data.get("logged_in", False)


class EVSELanguageSelect(EVSEBaseSelect):
    def __init__(self, coordinator, client, serial: str, base_name: str):
        super().__init__(coordinator, client, serial, base_name)
        self._attr_name = f"{base_name} Language"
        self._attr_unique_id = f"{serial}_language"
        self._attr_icon = "mdi:translate"
        self._attr_options = list(LANGUAGE_OPTIONS.keys())

    @property
    def current_option(self) -> str | None:
        code = self.evse_data.get("language")
        return LANGUAGE_BY_CODE.get(code)

    async def async_select_option(self, option: str) -> None:
        code = LANGUAGE_OPTIONS.get(option)
        if code is None:
            return
        if await self.client.set_language(self.serial, code):
            await self.coordinator.async_request_refresh()


class EVSETemperatureUnitSelect(EVSEBaseSelect):
    def __init__(self, coordinator, client, serial: str, base_name: str):
        super().__init__(coordinator, client, serial, base_name)
        self._attr_name = f"{base_name} Temperature Unit"
        self._attr_unique_id = f"{serial}_temperature_unit"
        self._attr_icon = "mdi:thermometer"
        self._attr_options = list(TEMP_UNIT_OPTIONS.keys())

    @property
    def current_option(self) -> str | None:
        code = self.evse_data.get("temperature_unit")
        return TEMP_UNIT_BY_CODE.get(code)

    async def async_select_option(self, option: str) -> None:
        code = TEMP_UNIT_OPTIONS.get(option)
        if code is None:
            return
        if await self.client.set_temperature_unit(self.serial, code):
            await self.coordinator.async_request_refresh()


class EVSEStartModeSelect(EVSEBaseSelect):
    """How charging can be started: app&button / app / auto."""

    def __init__(self, coordinator, client, serial: str, base_name: str):
        super().__init__(coordinator, client, serial, base_name)
        self._attr_name = f"{base_name} Start Mode"
        self._attr_unique_id = f"{serial}_start_mode"
        self._attr_icon = "mdi:play-circle-outline"
        self._attr_options = list(START_MODE_OPTIONS.keys())

    @property
    def current_option(self) -> str | None:
        code = self.evse_data.get("offline_charge")
        return START_MODE_BY_CODE.get(code)

    async def async_select_option(self, option: str) -> None:
        code = START_MODE_OPTIONS.get(option)
        if code is None:
            return
        if await self.client.set_offline_charge(self.serial, code):
            await self.coordinator.async_request_refresh()
