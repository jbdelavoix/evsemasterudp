"""Weekly charge schedule start-time entities."""
from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, evse_device_info
from .protocol.schedule import WEEKDAYS


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
            EVSEScheduleStartTime(coordinator, client, serial, base_name, i, day)
            for i, day in enumerate(WEEKDAYS)
        ]
    )


class EVSEScheduleStartTime(CoordinatorEntity, TimeEntity):
    """Start time for one weekday schedule slot (protocol 0x810e)."""

    def __init__(self, coordinator, client, serial: str, base_name: str, day_index: int, day: str):
        super().__init__(coordinator)
        self.client = client
        self.serial = serial
        self.base_name = base_name
        self.day_index = day_index
        self.day = day
        label = day.capitalize()
        self._attr_name = f"{base_name} Schedule {label} Start"
        self._attr_unique_id = f"{serial}_schedule_{day}_start"
        self._attr_icon = "mdi:clock-start"

    @property
    def device_info(self):
        return evse_device_info(self.serial, self.base_name, self.evse_data)

    @property
    def evse_data(self) -> dict:
        return self.coordinator.data.get(self.serial, {}) if self.coordinator.data else {}

    @property
    def _slot(self) -> dict | None:
        schedule = self.evse_data.get("schedule") or {}
        return schedule.get(self.day)

    @property
    def native_value(self) -> time | None:
        slot = self._slot
        if not slot or not slot.get("enabled"):
            return None
        return time(int(slot.get("hour", 0)), int(slot.get("minute", 0)))

    @property
    def available(self) -> bool:
        data = self.evse_data
        return data.get("online", False) and data.get("logged_in", False)

    async def async_set_value(self, value: time) -> None:
        ok = await self.client.set_schedule_slot(
            self.serial,
            self.day_index,
            hour=value.hour,
            minute=value.minute,
            enabled=True,
        )
        if ok:
            await self.coordinator.async_request_refresh()
