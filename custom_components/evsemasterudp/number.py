"""Numeric controls for the EVSE EmProto integration"""
from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent, UnitOfTime
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
    """Set up EVSE numeric controls"""

    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    client = data["client"]
    serial = data["serial"]
    base_name = data.get("base_name", f"EVSE {serial}")
    entry = data.get("entry", config_entry)

    entities = [
        EVSECurrentControl(coordinator, client, serial, base_name),
        EVSEScreenBrightness(coordinator, client, serial, base_name),
        EVSEFastChangeProtection(coordinator, client, serial, base_name, entry),
    ]
    entities.extend(
        EVSEScheduleDuration(coordinator, client, serial, base_name, i, day)
        for i, day in enumerate(WEEKDAYS)
    )

    async_add_entities(entities)


class EVSECurrentControl(CoordinatorEntity, NumberEntity):
    """Control for EVSE maximum current"""

    def __init__(self, coordinator, client, serial: str, base_name: str):
        super().__init__(coordinator)
        self.client = client
        self.serial = serial
        self.base_name = base_name
        self._attr_name = f"{base_name} Max Current"
        self._attr_unique_id = f"{serial}_max_current"
        self._attr_icon = "mdi:current-ac"
        self._attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
        self._attr_native_min_value = 6
        self._attr_native_max_value = 32
        self._attr_native_step = 1

    @property
    def device_info(self):
        return evse_device_info(self.serial, self.base_name, self.evse_data)

    @property
    def evse_data(self):
        return self.coordinator.data.get(self.serial, {}) if self.coordinator.data else {}

    @property
    def native_max_value(self) -> float:
        max_amps = self.evse_data.get("max_electricity") or 32
        return float(max(6, max_amps))

    @property
    def native_value(self) -> float | None:
        return self.evse_data.get("configured_max_electricity", 6)

    @property
    def available(self) -> bool:
        data = self.evse_data
        return data.get("online", False) and data.get("logged_in", False)

    async def async_set_native_value(self, value: float) -> None:
        success = await self.client.set_max_current(self.serial, int(value))
        if success:
            await self.coordinator.async_request_refresh()


class EVSEScreenBrightness(CoordinatorEntity, NumberEntity):
    """Screen brightness 0–100 (protocol 0x8162)."""

    def __init__(self, coordinator, client, serial: str, base_name: str):
        super().__init__(coordinator)
        self.client = client
        self.serial = serial
        self.base_name = base_name
        self._attr_name = f"{base_name} Screen Brightness"
        self._attr_unique_id = f"{serial}_screen_brightness"
        self._attr_icon = "mdi:brightness-6"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "%"

    @property
    def device_info(self):
        return evse_device_info(self.serial, self.base_name, self.evse_data)

    @property
    def evse_data(self):
        return self.coordinator.data.get(self.serial, {}) if self.coordinator.data else {}

    @property
    def native_value(self) -> float | None:
        val = self.evse_data.get("brightness")
        return float(val) if val is not None else None

    @property
    def available(self) -> bool:
        data = self.evse_data
        return data.get("online", False) and data.get("logged_in", False)

    async def async_set_native_value(self, value: float) -> None:
        success = await self.client.set_brightness(self.serial, int(value))
        if success:
            await self.coordinator.async_request_refresh()


class EVSEFastChangeProtection(CoordinatorEntity, NumberEntity):
    """Control for fast change protection (persisted in config entry options)."""

    def __init__(self, coordinator, client, serial: str, base_name: str, entry: ConfigEntry):
        super().__init__(coordinator)
        self.client = client
        self.serial = serial
        self.base_name = base_name
        self.entry = entry
        self._attr_name = f"{base_name} Fast Change Protection"
        self._attr_unique_id = f"{serial}_fast_change_protection"
        self._attr_icon = "mdi:shield-alert"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 60
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
        self._protection_minutes = self.client.get_fast_change_protection(serial)

    @property
    def device_info(self):
        return evse_device_info(self.serial, self.base_name, self.evse_data)

    @property
    def evse_data(self) -> dict:
        return self.coordinator.data.get(self.serial, {}) if self.coordinator.data else {}

    @property
    def native_value(self) -> float | None:
        return self.client.get_fast_change_protection(self.serial)

    @property
    def available(self) -> bool:
        return True

    async def async_set_native_value(self, value: float) -> None:
        minutes = int(value)
        self._protection_minutes = minutes
        await self.client.set_fast_change_protection(self.serial, minutes)
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={**self.entry.options, "fast_change_protection": minutes},
        )


class EVSEScheduleDuration(CoordinatorEntity, NumberEntity):
    """Charge duration for one weekday schedule slot (0 = off, up to 23h59)."""

    def __init__(self, coordinator, client, serial: str, base_name: str, day_index: int, day: str):
        super().__init__(coordinator)
        self.client = client
        self.serial = serial
        self.base_name = base_name
        self.day_index = day_index
        self.day = day
        label = day.capitalize()
        self._attr_name = f"{base_name} Schedule {label} Duration"
        self._attr_unique_id = f"{serial}_schedule_{day}_duration"
        self._attr_icon = "mdi:timer-outline"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 24 * 60 - 1
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = UnitOfTime.MINUTES

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
    def native_value(self) -> float | None:
        slot = self._slot
        if not slot or not slot.get("enabled"):
            return 0.0
        dur = slot.get("duration_min")
        return float(dur) if dur is not None else 0.0

    @property
    def available(self) -> bool:
        data = self.evse_data
        return data.get("online", False) and data.get("logged_in", False)

    async def async_set_native_value(self, value: float) -> None:
        duration = int(value)
        if duration <= 0:
            ok = await self.client.set_schedule_slot(
                self.serial, self.day_index, enabled=False
            )
        else:
            ok = await self.client.set_schedule_slot(
                self.serial, self.day_index, duration_min=duration, enabled=True
            )
        if ok:
            await self.coordinator.async_request_refresh()
