"""Binary sensors for the EVSE EmProto integration"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, evse_device_info

# SQW49 tethered: gun_state=1 observed with cable OUT of the vehicle (= available).
# Treat 2+ as vehicle connected until a plug-in capture proves otherwise.
GUN_VEHICLE_CONNECTED_STATES = {2, 3, 4}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    serial = data["serial"]
    base_name = data.get("base_name", f"EVSE {serial}")

    async_add_entities(
        [
            EVSEVehicleConnectedBinary(coordinator, serial, base_name),
            EVSEChargingBinary(coordinator, serial, base_name),
            EVSEErrorBinary(coordinator, serial, base_name),
            EVSEEmergencyBinary(coordinator, serial, base_name),
        ]
    )


class EVSEBaseBinary(CoordinatorEntity, BinarySensorEntity):
    def __init__(self, coordinator, serial: str, base_name: str):
        super().__init__(coordinator)
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
        return bool(data.get("online") and data.get("logged_in"))


class EVSEVehicleConnectedBinary(EVSEBaseBinary):
    def __init__(self, coordinator, serial: str, base_name: str):
        super().__init__(coordinator, serial, base_name)
        self._attr_name = f"{base_name} Vehicle Connected"
        self._attr_unique_id = f"{serial}_vehicle_connected"
        self._attr_device_class = BinarySensorDeviceClass.PLUG
        self._attr_icon = "mdi:ev-plug-type2"

    @property
    def is_on(self) -> bool | None:
        data = self.evse_data
        gun = data.get("gun_state", 0)
        power = data.get("current_power", 0) or 0
        if gun in GUN_VEHICLE_CONNECTED_STATES:
            return True
        # Strong charging signals imply the vehicle is connected
        if (
            data.get("output_state") == 1
            or power > 10
        ):
            return True
        return False

    @property
    def extra_state_attributes(self):
        return {
            "gun_state": self.evse_data.get("gun_state"),
            "note": "gun_state=1 on tethered SQW49 is available/idle, not vehicle connected",
        }


class EVSEChargingBinary(EVSEBaseBinary):
    def __init__(self, coordinator, serial: str, base_name: str):
        super().__init__(coordinator, serial, base_name)
        self._attr_name = f"{base_name} Charging"
        self._attr_unique_id = f"{serial}_charging"
        self._attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING
        self._attr_icon = "mdi:battery-charging"

    @property
    def is_on(self) -> bool | None:
        data = self.evse_data
        power = data.get("current_power", 0) or 0
        # Match communicator meta-state: current_state 14 alone is transitional
        return bool(
            data.get("state") == "CHARGING"
            or data.get("output_state") == 1
            or power > 10
        )


class EVSEErrorBinary(EVSEBaseBinary):
    def __init__(self, coordinator, serial: str, base_name: str):
        super().__init__(coordinator, serial, base_name)
        self._attr_name = f"{base_name} Error"
        self._attr_unique_id = f"{serial}_error"
        self._attr_device_class = BinarySensorDeviceClass.PROBLEM
        self._attr_icon = "mdi:alert-circle"

    @property
    def is_on(self) -> bool | None:
        errors = self.evse_data.get("errors") or []
        return bool(errors) or self.evse_data.get("state") == "ERROR"

    @property
    def extra_state_attributes(self):
        return {"errors": self.evse_data.get("errors") or []}


class EVSEEmergencyBinary(EVSEBaseBinary):
    """Emergency stop. Capture shows emergency_btn_state=1 while operating → 1=OK, 0=pressed."""

    def __init__(self, coordinator, serial: str, base_name: str):
        super().__init__(coordinator, serial, base_name)
        self._attr_name = f"{base_name} Emergency"
        self._attr_unique_id = f"{serial}_emergency"
        self._attr_device_class = BinarySensorDeviceClass.SAFETY
        self._attr_icon = "mdi:alert-octagon"

    @property
    def is_on(self) -> bool | None:
        # on = emergency active (problem)
        return self.evse_data.get("emergency_btn_state", 1) == 0

    @property
    def extra_state_attributes(self):
        return {"emergency_btn_state": self.evse_data.get("emergency_btn_state")}
