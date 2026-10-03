"""Sensors for the EVSE EmProto integration"""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfEnergy,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, evse_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EVSE sensors"""

    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    serial = data["serial"]
    base_name = data.get("base_name", f"EVSE {serial}")
    client = data["client"]

    entities = [
        EVSEStateSensor(coordinator, serial, base_name),
        EVSEPowerSensor(coordinator, serial, base_name),
        EVSECurrentSensor(coordinator, serial, base_name, "l1"),
        EVSECurrentSensor(coordinator, serial, base_name, "l2"),
        EVSECurrentSensor(coordinator, serial, base_name, "l3"),
        EVSEVoltageSensor(coordinator, serial, base_name, "l1"),
        EVSEVoltageSensor(coordinator, serial, base_name, "l2"),
        EVSEVoltageSensor(coordinator, serial, base_name, "l3"),
        EVSEEnergySensor(coordinator, serial, base_name),
        EVSESessionEnergySensor(coordinator, serial, base_name),
        EVSESessionDurationSensor(coordinator, serial, base_name),
        EVSEGunStateSensor(coordinator, serial, base_name),
        EVSECurrentStateSensor(coordinator, serial, base_name),
        EVSETemperatureSensor(coordinator, serial, base_name, "inner"),
        EVSETemperatureSensor(coordinator, serial, base_name, "outer"),
        EVSEChargeStatusSensor(coordinator, serial, base_name, client),
    ]

    async_add_entities(entities)


class EVSEBaseSensor(CoordinatorEntity, SensorEntity):
    """Base sensor for EVSE"""

    def __init__(self, coordinator, serial: str, base_name: str):
        super().__init__(coordinator)
        self.serial = serial
        self.base_name = base_name

    @property
    def device_info(self):
        return evse_device_info(self.serial, self.base_name, self.evse_data)

    @property
    def evse_data(self):
        """Get EVSE data"""
        return self.coordinator.data.get(self.serial, {}) if self.coordinator.data else {}

    @property
    def available(self) -> bool:
        # Require a live UDP session — Login broadcasts alone keep "online"
        # and would otherwise freeze the last electrical readings.
        data = self.evse_data
        return bool(data.get("online") and data.get("logged_in"))


class EVSEChargeStatusSensor(EVSEBaseSensor):
    """Charging / idle / soft_protection status."""

    def __init__(self, coordinator, serial: str, base_name: str, client):
        super().__init__(coordinator, serial, base_name)
        self.client = client
        self._attr_name = f"{base_name} Charge Status"
        self._attr_unique_id = f"{serial}_charge_status"
        self._attr_icon = "mdi:ev-station"

    @property
    def native_value(self):
        data = self.evse_data
        if not data:
            return None
        current_power = data.get("current_power", 0) or 0
        charging = bool(
            data.get("state") == "CHARGING"
            or data.get("output_state") == 1
            or current_power > 10
        )
        if charging:
            return "charging"
        remaining = self.client.get_cooldown_remaining(self.serial)
        if remaining.total_seconds() > 0:
            return "soft_protection"
        return "not_charging"

    @property
    def extra_state_attributes(self):
        remaining = self.client.get_cooldown_remaining(self.serial)
        return {
            "cooldown_remaining_s": int(remaining.total_seconds()),
        }


class EVSEStateSensor(EVSEBaseSensor):
    """EVSE state sensor"""

    def __init__(self, coordinator, serial: str, base_name: str):
        super().__init__(coordinator, serial, base_name)
        self._attr_name = f"{base_name} State"
        self._attr_unique_id = f"{serial}_state"
        self._attr_icon = "mdi:ev-station"

    @property
    def available(self) -> bool:
        return True  # Always show offline state

    @property
    def native_value(self) -> str | None:
        data = self.evse_data
        if not data.get("online"):
            return "offline"
        return data.get("state", "unknown").lower()

    @property
    def extra_state_attributes(self):
        data = self.evse_data
        return {
            "online": data.get("online", False),
            "logged_in": data.get("logged_in", False),
            "ip": data.get("ip"),
            "last_seen": data.get("last_seen"),
        }


class EVSEPowerSensor(EVSEBaseSensor):
    """EVSE power sensor"""

    def __init__(self, coordinator, serial: str, base_name: str):
        super().__init__(coordinator, serial, base_name)
        self._attr_name = f"{base_name} Power"
        self._attr_unique_id = f"{serial}_power"
        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfPower.WATT
        self._attr_icon = "mdi:flash"

    @property
    def native_value(self) -> float | None:
        return self.evse_data.get("current_power")


class EVSECurrentSensor(EVSEBaseSensor):
    """EVSE current sensor (per phase)."""

    def __init__(self, coordinator, serial: str, base_name: str, phase: str = "l1"):
        super().__init__(coordinator, serial, base_name)
        self.phase = phase
        label = phase.upper()
        self._attr_name = f"{base_name} Current {label}"
        self._attr_unique_id = f"{serial}_current_{phase}"
        self._attr_device_class = SensorDeviceClass.CURRENT
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
        self._attr_icon = "mdi:current-ac"
        # Keep legacy unique_id for L1 to avoid duplicating the old entity
        if phase == "l1":
            self._attr_unique_id = f"{serial}_current"
            self._attr_name = f"{base_name} Current"

    @property
    def native_value(self) -> float | None:
        return self.evse_data.get(f"current_{self.phase}")

    @property
    def entity_registry_enabled_default(self) -> bool:
        if self.phase == "l1":
            return True
        # Capture: SQW49 is single-phase (L2/L3 always 0)
        phases = self.evse_data.get("phases") or 1
        if phases < 2:
            return False
        return bool(
            self.evse_data.get(f"current_{self.phase}")
            or self.evse_data.get(f"voltage_{self.phase}")
        )


class EVSEVoltageSensor(EVSEBaseSensor):
    """EVSE voltage sensor (per phase)."""

    def __init__(self, coordinator, serial: str, base_name: str, phase: str = "l1"):
        super().__init__(coordinator, serial, base_name)
        self.phase = phase
        label = phase.upper()
        self._attr_name = f"{base_name} Voltage {label}"
        self._attr_unique_id = f"{serial}_voltage_{phase}"
        self._attr_device_class = SensorDeviceClass.VOLTAGE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
        self._attr_icon = "mdi:sine-wave"
        if phase == "l1":
            self._attr_unique_id = f"{serial}_voltage"
            self._attr_name = f"{base_name} Voltage"

    @property
    def native_value(self) -> float | None:
        return self.evse_data.get(f"voltage_{self.phase}")

    @property
    def entity_registry_enabled_default(self) -> bool:
        if self.phase == "l1":
            return True
        phases = self.evse_data.get("phases") or 1
        if phases < 2:
            return False
        return bool(self.evse_data.get(f"voltage_{self.phase}"))


class EVSEEnergySensor(EVSEBaseSensor):
    """Lifetime energy counter (TOTAL_INCREASING)."""

    def __init__(self, coordinator, serial: str, base_name: str):
        super().__init__(coordinator, serial, base_name)
        self._attr_name = f"{base_name} Energy"
        self._attr_unique_id = f"{serial}_energy"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_icon = "mdi:counter"

    @property
    def native_value(self) -> float | None:
        value = self.evse_data.get("total_kwh")
        return value if value is not None else None


class EVSESessionEnergySensor(EVSEBaseSensor):
    """Current session energy (resets between charges)."""

    def __init__(self, coordinator, serial: str, base_name: str):
        super().__init__(coordinator, serial, base_name)
        self._attr_name = f"{base_name} Session Energy"
        self._attr_unique_id = f"{serial}_session_energy"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_icon = "mdi:battery-charging"

    @property
    def native_value(self) -> float | None:
        return self.evse_data.get("charge_kwh")

    @property
    def extra_state_attributes(self):
        return {
            "charge_id": self.evse_data.get("charge_id"),
            "user_id": self.evse_data.get("user_id"),
            "charge_fee": self.evse_data.get("charge_fee"),
            "charge_price": self.evse_data.get("charge_price"),
            "session_max_electricity": self.evse_data.get("session_max_electricity"),
            "start_kwh_counter": self.evse_data.get("start_kwh_counter"),
            "current_kwh_counter": self.evse_data.get("current_kwh_counter"),
        }


class EVSESessionDurationSensor(EVSEBaseSensor):
    """Current session duration in seconds."""

    def __init__(self, coordinator, serial: str, base_name: str):
        super().__init__(coordinator, serial, base_name)
        self._attr_name = f"{base_name} Session Duration"
        self._attr_unique_id = f"{serial}_session_duration"
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfTime.SECONDS
        self._attr_icon = "mdi:timer"

    @property
    def native_value(self) -> float | None:
        return self.evse_data.get("duration_seconds")


GUN_STATE_LABELS = {
    0: "unknown",
    1: "available",        # cable out of vehicle
    2: "plugged",          # vehicle plugged, not charging
    3: "connected",
    4: "plugged_locked",   # vehicle plugged + locking/charging
}

# SingleACStatus current_state on SQW49 (confirmed by captures)
CURRENT_STATE_LABELS = {
    0: "idle",
    12: "standby",          # ready / not charging (also seen with cable out)
    13: "finished",
    14: "charging",
    15: "stopped_by_evse",  # stop from charger/app (confirmed)
    18: "stopped_by_ev",    # stop from vehicle (confirmed)
}

OUTPUT_STATE_LABELS = {
    0: "idle",
    1: "charging",   # confirmed delivering power
    2: "ready",      # plugged or available, not delivering
}


class EVSEGunStateSensor(EVSEBaseSensor):
    """Cable / gun connection state."""

    def __init__(self, coordinator, serial: str, base_name: str):
        super().__init__(coordinator, serial, base_name)
        self._attr_name = f"{base_name} Gun State"
        self._attr_unique_id = f"{serial}_gun_state"
        self._attr_icon = "mdi:ev-plug-type2"

    @property
    def native_value(self) -> str | None:
        gun = self.evse_data.get("gun_state", 0)
        return GUN_STATE_LABELS.get(gun, f"unknown_{gun}")

    @property
    def extra_state_attributes(self):
        return {"gun_state_raw": self.evse_data.get("gun_state")}


class EVSECurrentStateSensor(EVSEBaseSensor):
    """Raw protocol current_state (+ output_state) for diagnostics/automations."""

    def __init__(self, coordinator, serial: str, base_name: str):
        super().__init__(coordinator, serial, base_name)
        self._attr_name = f"{base_name} Current State"
        self._attr_unique_id = f"{serial}_current_state"
        self._attr_icon = "mdi:state-machine"

    @property
    def native_value(self) -> str | None:
        # Prefer live SingleACStatus; fall back to last session charge_state
        raw = self.evse_data.get("current_state")
        if raw in (None, 0):
            raw = self.evse_data.get("charge_state") or 0
        return CURRENT_STATE_LABELS.get(raw, f"unknown_{raw}")

    @property
    def extra_state_attributes(self):
        out = self.evse_data.get("output_state", 0)
        return {
            "current_state_raw": self.evse_data.get("current_state"),
            "charge_state_raw": self.evse_data.get("charge_state"),
            "output_state_raw": out,
            "output_state": OUTPUT_STATE_LABELS.get(out, f"unknown_{out}"),
            "meta_state": self.evse_data.get("state"),
        }


class EVSETemperatureSensor(EVSEBaseSensor):
    """EVSE temperature sensor"""

    def __init__(self, coordinator, serial: str, base_name: str, temp_type: str):
        super().__init__(coordinator, serial, base_name)
        self.temp_type = temp_type
        self._attr_name = f"{base_name} Temperature {temp_type.title()}"
        self._attr_unique_id = f"{serial}_temperature_{temp_type}"
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_icon = "mdi:thermometer"

    @property
    def native_value(self) -> float | None:
        return self.evse_data.get(f"temperature_{self.temp_type}")
