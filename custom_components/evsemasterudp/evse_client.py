"""
EVSE client for Home Assistant
"""
import logging
from typing import Optional, Dict, Any, Callable
from datetime import datetime, timedelta

from .protocol import get_communicator

_LOGGER = logging.getLogger(__name__)


class EVSEClient:
    """Client for communicating with EVSE stations via UDP"""

    def __init__(self, port: int = 28376):
        self.port = port
        self.communicator = get_communicator(port)
        self.running = False
        self.callbacks: Dict[str, Callable] = {}
        self._fast_change_protection: Dict[str, int] = {}  # serial -> minutes
        self._last_charge_change: Dict[str, datetime] = {}  # serial -> timestamp
        self._passwords: Dict[str, str] = {}  # serial -> password for reconnect

    async def start(self):
        """Start the UDP client"""
        if self.running:
            return

        try:
            await self.communicator.start()
            self.running = True
            self.communicator.add_callback("evse_client", self._handle_evse_event)
            _LOGGER.info(f"EVSE client started on port {self.port}")
        except Exception as e:
            _LOGGER.error(f"Error while starting EVSE client: {e}")
            raise
    async def stop(self):
        """Stop the client"""
        self.running = False
        self.communicator.remove_callback("evse_client")
        await self.communicator.stop()
        _LOGGER.info("EVSE client stopped")

    async def _handle_evse_event(self, event: str, evse):
        """Handle EVSE events and forward to HA callbacks"""
        # Attach stored credentials so communicator can re-login after rediscovery.
        if not evse.password and evse.info.serial in self._passwords:
            evse.password = self._passwords[evse.info.serial]
        evse_data = self._evse_to_dict(evse)
        for callback in self.callbacks.values():
            try:
                await callback(evse.info.serial, evse_data)
            except Exception as e:
                _LOGGER.error(f"Error in callback: {e}")

    def _evse_to_dict(self, evse) -> Dict[str, Any]:
        """Convert an EVSE object to a dictionary"""
        data = {
            "serial": evse.info.serial,
            "ip": evse.info.ip,
            "port": evse.info.port,
            "last_seen": evse.last_seen,
            "online": evse.is_online(),
            "logged_in": evse.is_logged_in(),
            "state": evse.get_meta_state(),
            "brand": evse.info.brand,
            "model": evse.info.model,
            "hardware_version": evse.info.hardware_version,
            "software_version": evse.info.software_version,
            "max_power": evse.info.max_power,
            "max_electricity": evse.info.max_electricity,
            "phases": evse.info.phases,
            "name": evse.config.name or "EVSEMaster",
            "configured_max_electricity": evse.config.max_electricity,
            "temperature_unit": evse.config.temperature_unit,
            "language": evse.config.language,
            "offline_charge": evse.config.offline_charge,
            "brightness": evse.config.brightness,
            "schedule": {
                day: slot.to_dict()
                for day, slot in zip(
                    (
                        "monday",
                        "tuesday",
                        "wednesday",
                        "thursday",
                        "friday",
                        "saturday",
                        "sunday",
                    ),
                    evse.config.schedule or [],
                )
            },
            # Lifetime energy counter (for TOTAL_INCREASING sensor)
            "total_kwh": 0.0,
            "charge_kwh": 0.0,
            "charge_id": "",
            "start_date": None,
            "duration_seconds": 0,
            "charge_state": 0,
            "current_power": 0,
            "voltage_l1": 0,
            "voltage_l2": 0,
            "voltage_l3": 0,
            "current_l1": 0,
            "current_l2": 0,
            "current_l3": 0,
            "temperature_inner": 0,
            "temperature_outer": 0,
            "gun_state": 0,
            "output_state": 0,
            "emergency_btn_state": 0,
            "errors": [],
            "charge_fee": 0.0,
            "charge_price": 0.0,
            "session_max_electricity": 0,
            "user_id": "",
            "start_type": 0,
            "start_kwh_counter": 0.0,
            "current_kwh_counter": 0.0,
            "current_state": 0,
            "feature": evse.info.feature,
            "support_new": evse.info.support_new,
        }

        if evse.state:
            data.update(
                {
                    "current_power": evse.state.current_power,
                    "total_kwh": evse.state.current_amount,
                    "voltage_l1": evse.state.l1_voltage,
                    "voltage_l2": evse.state.l2_voltage,
                    "voltage_l3": evse.state.l3_voltage,
                    "current_l1": evse.state.l1_electricity,
                    "current_l2": evse.state.l2_electricity,
                    "current_l3": evse.state.l3_electricity,
                    "temperature_inner": evse.state.inner_temp,
                    "temperature_outer": evse.state.outer_temp,
                    "gun_state": evse.state.gun_state,
                    "output_state": evse.state.output_state,
                    "emergency_btn_state": getattr(
                        evse.state, "emergency_btn_state", 0
                    ),
                    "errors": evse.state.errors,
                    "current_state": evse.state.current_state,
                }
            )

        if evse.current_charge:
            data.update(
                {
                    "charge_kwh": evse.current_charge.charge_kwh,
                    "charge_id": evse.current_charge.charge_id,
                    "start_date": evse.current_charge.start_date,
                    "duration_seconds": evse.current_charge.duration_seconds,
                    "charge_state": evse.current_charge.current_state,
                    "charge_fee": evse.current_charge.charge_fee,
                    "charge_price": evse.current_charge.charge_price,
                    "session_max_electricity": evse.current_charge.max_electricity,
                    "user_id": getattr(evse.current_charge, "user_id", ""),
                    "start_type": getattr(evse.current_charge, "start_type", 0),
                    "start_kwh_counter": getattr(
                        evse.current_charge, "start_kwh_counter", 0
                    ),
                    "current_kwh_counter": getattr(
                        evse.current_charge, "current_kwh_counter", 0
                    ),
                }
            )

        return data

    def add_callback(self, name: str, callback: Callable):
        """Add a callback for state changes"""
        self.callbacks[name] = callback

    def remove_callback(self, name: str):
        """Remove a callback"""
        self.callbacks.pop(name, None)

    def get_evse(self, serial: str) -> Optional[Dict[str, Any]]:
        """Get the data for an EVSE"""
        evse = self.communicator.get_evse(serial)
        if evse:
            return self._evse_to_dict(evse)
        return None

    def get_all_evses(self) -> Dict[str, Dict[str, Any]]:
        """Get all EVSEs"""
        result = {}
        for serial, evse in self.communicator.get_all_evses().items():
            result[serial] = self._evse_to_dict(evse)
        return result

    def remember_password(self, serial: str, password: str) -> None:
        """Store credentials for rediscovery / background re-login."""
        self._passwords[serial] = password
        evse = self.communicator.get_evse(serial)
        if evse:
            evse.password = password

    async def login(self, serial: str, password: str) -> bool:
        """Log in to an EVSE"""
        self.remember_password(serial, password)
        evse = self.communicator.get_evse(serial)
        if not evse:
            _LOGGER.error(f"EVSE {serial} not found")
            return False
        return await evse.login(password)
    async def start_charging(
        self, serial: str, amps: int = None, single_phase: bool = False
    ) -> bool:
        """Start charging"""
        if not self._can_start_charge(serial):
            return False

        evse = self.communicator.get_evse(serial)
        if not evse:
            _LOGGER.error(f"EVSE {serial} not found")
            return False

        if amps is None:
            if evse.config and evse.config.max_electricity > 0:
                amps = evse.config.max_electricity
            else:
                max_amps = (
                    evse.info.max_electricity if evse.info.max_electricity > 0 else 32
                )
                amps = min(max_amps, 16)

        return await evse.charge_start(amps, single_phase)

    async def stop_charging(self, serial: str) -> bool:
        """Stop charging"""
        evse = self.communicator.get_evse(serial)
        if not evse:
            _LOGGER.error(f"EVSE {serial} not found")
            return False

        was_charging = evse.get_meta_state() == "CHARGING"
        result = await evse.charge_stop()
        if result and was_charging:
            self._record_charge_state_change(serial)
        return result

    async def set_max_current(self, serial: str, amps: int) -> bool:
        """Set the maximum current"""
        evse = self.communicator.get_evse(serial)
        if not evse:
            _LOGGER.error(f"EVSE {serial} not found")
            return False
        return await evse.set_max_electricity(amps)

    async def set_brightness(self, serial: str, brightness: int) -> bool:
        """Set screen brightness (0–100)."""
        evse = self.communicator.get_evse(serial)
        if not evse:
            _LOGGER.error(f"EVSE {serial} not found")
            return False
        return await evse.set_brightness(brightness)

    async def set_language(self, serial: str, language: int) -> bool:
        """Set UI language code."""
        evse = self.communicator.get_evse(serial)
        if not evse:
            _LOGGER.error(f"EVSE {serial} not found")
            return False
        return await evse.set_language(language)

    async def set_temperature_unit(self, serial: str, unit: int) -> bool:
        """Set temperature unit (1=°C, 2=°F)."""
        evse = self.communicator.get_evse(serial)
        if not evse:
            _LOGGER.error(f"EVSE {serial} not found")
            return False
        return await evse.set_temperature_unit(unit)

    async def set_offline_charge(self, serial: str, status: int) -> bool:
        """Set offline charge mode."""
        evse = self.communicator.get_evse(serial)
        if not evse:
            _LOGGER.error(f"EVSE {serial} not found")
            return False
        return await evse.set_offline_charge(status)

    async def set_name(self, serial: str, name: str) -> bool:
        """Set the EVSE name"""
        evse = self.communicator.get_evse(serial)
        if not evse:
            _LOGGER.error(f"EVSE {serial} not found")
            return False
        return await evse.set_name(name)

    async def get_schedule(self, serial: str):
        """Fetch weekly charge schedule."""
        evse = self.communicator.get_evse(serial)
        if not evse:
            _LOGGER.error(f"EVSE {serial} not found")
            return None
        return await evse.get_schedule()

    async def set_schedule(self, serial: str, slots) -> bool:
        """Set weekly charge schedule (7 slots)."""
        evse = self.communicator.get_evse(serial)
        if not evse:
            _LOGGER.error(f"EVSE {serial} not found")
            return False
        return await evse.set_schedule(slots)

    async def set_schedule_slot(
        self,
        serial: str,
        day_index: int,
        *,
        hour: Optional[int] = None,
        minute: Optional[int] = None,
        duration_min: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> bool:
        """Update one weekday schedule slot."""
        evse = self.communicator.get_evse(serial)
        if not evse:
            _LOGGER.error(f"EVSE {serial} not found")
            return False
        return await evse.set_schedule_slot(
            day_index,
            hour=hour,
            minute=minute,
            duration_min=duration_min,
            enabled=enabled,
        )

    async def sync_time(self, serial: str) -> bool:
        """Synchronize the EVSE time"""
        evse = self.communicator.get_evse(serial)
        if not evse:
            _LOGGER.error(f"EVSE {serial} not found")
            return False
        return await evse.sync_time()

    async def set_fast_change_protection(self, serial: str, minutes: int) -> None:
        """Set rapid change protection (in minutes)"""
        self._fast_change_protection[serial] = minutes
        _LOGGER.info(f"Rapid change protection for {serial}: {minutes} minutes")

    def get_fast_change_protection(self, serial: str) -> int:
        """Get the current protection (in minutes). Default: 1 minute."""
        return self._fast_change_protection.get(serial, 1)

    def _can_start_charge(self, serial: str) -> bool:
        """Check if charging can be started (anti-wear protection)"""
        protection_minutes = self.get_fast_change_protection(serial)
        if protection_minutes == 0:
            return True

        last_change = self._last_charge_change.get(serial)
        if last_change is None:
            return True

        time_since_last = datetime.now() - last_change
        min_interval = timedelta(minutes=protection_minutes)

        if time_since_last < min_interval:
            remaining = min_interval - time_since_last
            remaining_minutes = remaining.total_seconds() / 60
            _LOGGER.warning(
                f"Start protection active for {serial}: "
                f"wait another {remaining_minutes:.1f} minutes since the last stop "
                f"(protection: {protection_minutes} min)"
            )
            return False

        return True

    def _record_charge_state_change(self, serial: str) -> None:
        """Record a charge stop (to protect the next start)"""
        self._last_charge_change[serial] = datetime.now()
        _LOGGER.debug(f"Charge stop recorded for {serial}")

    def get_cooldown_remaining(self, serial: str) -> timedelta:
        """Return the remaining time before a new start is allowed."""
        protection_minutes = self.get_fast_change_protection(serial)
        if protection_minutes == 0:
            return timedelta(0)
        last_change = self._last_charge_change.get(serial)
        if not last_change:
            return timedelta(0)
        min_interval = timedelta(minutes=protection_minutes)
        elapsed = datetime.now() - last_change
        remaining = min_interval - elapsed
        if remaining.total_seconds() < 0:
            return timedelta(0)
        return remaining


_client_instance: Optional[EVSEClient] = None


def get_evse_client(port: int = 28376) -> EVSEClient:
    """Get the singleton instance of the EVSE client"""
    global _client_instance
    if _client_instance is None:
        _client_instance = EVSEClient(port=port)
    elif not _client_instance.running and _client_instance.port != port:
        _client_instance = EVSEClient(port=port)
    return _client_instance
