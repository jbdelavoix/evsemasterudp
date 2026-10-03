"""
UDP Communicator for EmProto EVSEs
"""
import asyncio
import socket
import struct
import logging
from typing import Dict, Optional, Callable, Any, List
from datetime import datetime, timedelta

from .datagram import Datagram, parse_datagrams
from .datagrams import (
    RequestLogin, LoginConfirm, PasswordErrorResponse,
    Heading, HeadingResponse, SingleACStatus, SingleACStatusResponse,
    CurrentChargeRecord, ChargeStart, ChargeStop,
    SetAndGetOutputElectricity, SetAndGetOutputElectricityResponse,
    Login, LoginResponse, SingleACChargingStatusPublicAuto, SingleACChargingStatusResponse,
    GetVersion, GetVersionResponse, SetAndGetSystemTime, SetAndGetSystemTimeResponse,
    SetAndGetOffLineCharge, SetAndGetOffLineChargeResponse,
    SetAndGetLanguage, SetAndGetLanguageResponse,
    SetAndGetTemperatureUnit, SetAndGetTemperatureUnitResponse,
    SetAndGetScreenBrightness, SetAndGetScreenBrightnessResponse,
    SetAndGetNickName, SetAndGetNickNameResponse,
    SetAndGetChargeSchedule, SetAndGetChargeScheduleResponse,
)
from .schedule import ScheduleSlot, empty_schedule

_LOGGER = logging.getLogger(__name__)

# Only these commands are reliable EVSE→network origins for learning the
# charger address. App/phone broadcasts often reuse the same serial with
# 0x01xx responses; treating those as the EVSE IP makes SET/GET flaky.
_EVSE_ADDRESS_COMMANDS = {
    0x0001,  # Login (discovery broadcast)
    0x0002,  # LoginResponse
    0x0003,  # Heading (EVSE keepalive)
    0x0004,  # SingleACStatus
    0x0005,  # SingleACChargingStatusPublicAuto
    0x0009,  # CurrentChargeRecord
    0x000A,  # UploadLocalChargeRecord
}

class EVSEInfo:
    """Information about an EVSE"""
    def __init__(self, serial: str, ip: str, port: int):
        self.serial = serial
        self.ip = ip
        self.port = port
        self.brand = "EVSE"
        self.model = ""
        self.hardware_version = ""
        self.software_version = ""
        self.max_power = 0
        self.max_electricity = 32
        self.hot_line = ""
        self.phases = 1
        self.can_force_single_phase = False
        self.feature = 0
        self.support_new = 0
        self.device_id = ""  # Device ID extracted from command 0x010c

class EVSEConfig:
    """EVSE configuration"""
    def __init__(self):
        self.name = ""
        self.language = 254
        self.offline_charge = 254
        self.max_electricity = 6
        self.temperature_unit = 1
        self.brightness = 100
        self.schedule: List[ScheduleSlot] = empty_schedule()

class EVSEState:
    """Electrical state of an EVSE"""
    def __init__(self):
        self.current_power = 0.0
        self.current_amount = 0.0
        self.l1_voltage = 0.0
        self.l1_electricity = 0.0
        self.l2_voltage = 0.0
        self.l2_electricity = 0.0
        self.l3_voltage = 0.0
        self.l3_electricity = 0.0
        self.inner_temp = 0.0
        self.outer_temp = 0.0
        self.current_state = 0
        self.gun_state = 0
        self.output_state = 0
        self.emergency_btn_state = 0
        self.errors = []

class EVSECurrentCharge:
    """Current charging session"""
    def __init__(self):
        self.port = 1
        self.current_state = 0
        self.charge_id = ""
        self.start_type = 0
        self.charge_type = 0
        self.reservation_date = datetime.fromtimestamp(0)
        self.user_id = ""
        self.max_electricity = 0
        self.start_date = datetime.fromtimestamp(0)
        self.duration_seconds = 0
        self.start_kwh_counter = 0.0
        self.current_kwh_counter = 0.0
        self.charge_kwh = 0.0
        self.charge_price = 0.0
        self.fee_type = 0
        self.charge_fee = 0.0

class EVSE:
    """Representation of an EVSE"""
    
    def __init__(self, communicator: 'Communicator', serial: str, ip: str, port: int):
        self.communicator = communicator
        self.info = EVSEInfo(serial, ip, port)
        self.config = EVSEConfig()
        self.state: Optional[EVSEState] = None
        self.current_charge: Optional[EVSECurrentCharge] = None
        
        self.last_seen = datetime.now()
        # Last proof of a live session (status / heading ack) — NOT outbound probes.
        self.last_active_login: Optional[datetime] = None
        self.last_status_at: Optional[datetime] = None
        self.last_login_ok_at: Optional[datetime] = None
        self._last_login_attempt: Optional[datetime] = None
        self.password: Optional[str] = None
        self._logged_in = False
        self._response_waiters: List[tuple] = []  # (set[command], Future)
        
        # Possible states according to the protocol
        self.GUN_STATES = {
            0: "DISCONNECTED",
            1: "CONNECTED_LOCKED", 
            2: "CONNECTED_UNLOCKED"
        }
        self.OUTPUT_STATES = {
            0: "IDLE",
            1: "CHARGING"
        }
    
    def update_ip(self, ip: str, port: int) -> bool:
        """Update IP and port"""
        self.last_seen = datetime.now()
        changed = False
        
        if ip != self.info.ip:
            self.info.ip = ip
            changed = True
        
        if port != self.info.port:
            self.info.port = port
            changed = True
        
        return changed
    
    def is_online(self) -> bool:
        """Check if the EVSE is online"""
        # Consider offline after 90 seconds (adjusted for 60s poll interval)
        return (datetime.now() - self.last_seen).total_seconds() < 90
    
    def is_logged_in(self) -> bool:
        """Check if logged in to the EVSE"""
        return self._logged_in and self.is_online()
    
    def get_meta_state(self) -> str:
        """Get the meta state of the EVSE.

        SQW49 tethered cable (confirmed by captures):
        - gun_state=1 → available (cable out)
        - gun_state=2 → vehicle plugged, not charging
        - gun_state=4 → plugged + locked while charging
        - output_state: 0=idle, 1=charging (power on), 2=ready
        - current_state: 13=finished, 14=charging/handshake, 18=stopped by EV
        - CHARGING only when output_state=1 or power>10 (14 alone is transitional)
        """
        if not self.is_online():
            return "OFFLINE"
        if not self.is_logged_in():
            return "NOT_LOGGED_IN"
        if not self.state:
            return "IDLE"
        if self.state.errors:
            return "ERROR"
        if getattr(self.state, "emergency_btn_state", 1) == 0:
            return "EMERGENCY"

        power = getattr(self.state, "current_power", 0) or 0

        if (
            self.state.output_state == 1
            or power > 10
        ):
            return "CHARGING"

        gun_state = getattr(self.state, "gun_state", 0)
        if gun_state in (2, 3, 4):
            return "PLUGGED_IN"
        return "IDLE"
    
    def touch_session(self) -> None:
        """Record that the EVSE answered / pushed live session traffic."""
        now = datetime.now()
        self.last_active_login = now
        self.last_seen = now

    def session_is_stale(self, *, idle_seconds: float = 90.0) -> bool:
        """True when we should re-authenticate (no live session traffic)."""
        if not self.password:
            return False
        if not self._logged_in or not self.is_online():
            return True
        if self.last_active_login is None:
            return True
        return (datetime.now() - self.last_active_login).total_seconds() > idle_seconds

    async def send_datagram(self, datagram: Datagram) -> int:
        """Send a datagram to the EVSE"""
        return await self.communicator.send(datagram, self)

    def _resolve_waiters(self, datagram: Datagram) -> None:
        """Resolve any pending response waiters matching this datagram command."""
        command = datagram.get_command()
        remaining = []
        for expected, fut in self._response_waiters:
            if fut.done():
                continue
            if command in expected:
                fut.set_result(datagram)
            else:
                remaining.append((expected, fut))
        self._response_waiters = remaining
    
    async def login(self, password: str) -> bool:
        """Log in to the EVSE following the TypeScript sequence"""
        try:
            _LOGGER.info(f"Attempting to connect to {self.info.serial} with password")
            
            # Send RequestLogin with password (do not clear session until auth fails)
            login_request = RequestLogin()
            login_request.set_device_serial(self.info.serial)
            login_request.set_device_password(password)
            
            await self.send_datagram(login_request)
            _LOGGER.debug(f"RequestLogin sent to {self.info.serial}")
            
            response = await self._wait_for_response(
                [LoginResponse.COMMAND, PasswordErrorResponse.COMMAND], 3.0
            )
            
            if response and response.get_command() == PasswordErrorResponse.COMMAND:
                _LOGGER.error(f"Incorrect password for {self.info.serial}")
                self._logged_in = False
                return False
            
            if not response or response.get_command() != LoginResponse.COMMAND:
                _LOGGER.error(f"No login response from {self.info.serial}")
                return False
            
            self.password = password
            _LOGGER.info(f"Password accepted for {self.info.serial}")
            
            login_confirm = LoginConfirm()
            login_confirm.set_device_serial(self.info.serial)
            login_confirm.set_device_password(password)
            
            await self.send_datagram(login_confirm)
            _LOGGER.debug(f"LoginConfirm sent to {self.info.serial}")
            
            self._logged_in = True
            self.touch_session()
            self.last_login_ok_at = datetime.now()
            _LOGGER.info(f"Connection established with {self.info.serial}")

            # Do not block the keepalive/re-login loop on slow config GETs.
            asyncio.create_task(self._post_login_setup())
            return True
                
        except Exception as e:
            _LOGGER.error(f"Error while connecting to {self.info.serial}: {e}")
            return False

    async def _post_login_setup(self) -> None:
        """Keepalive + config fetch after auth (background)."""
        try:
            await self.send_keepalive()
            await self._fetch_device_info()
        except Exception as e:
            _LOGGER.warning(f"Post-login setup failed for {self.info.serial}: {e}")
    
    async def _wait_for_response(self, expected_commands: list, timeout: float):
        """Wait for a response with specific commands (future-based, no race)."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        expected = set(expected_commands)
        self._response_waiters.append((expected, fut))
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._response_waiters = [
                (cmds, f) for cmds, f in self._response_waiters if f is not fut
            ]
    
    async def send_keepalive(self) -> None:
        """Send Heading keepalive (App → EVSE)."""
        heading = Heading()
        heading.set_device_serial(self.info.serial)
        heading.set_device_password(self.password)
        await self.send_datagram(heading)

    async def _fetch_device_info(self) -> None:
        """Request version + config (amps, language, temp, offline, brightness, name)."""
        get_ver = GetVersion()
        get_ver.set_device_serial(self.info.serial)
        get_ver.set_device_password(self.password)
        await self.send_datagram(get_ver)

        async def _get(datagram_cls, response_cmd: int, action: int = 2):
            req = datagram_cls()
            req.set_device_serial(self.info.serial)
            req.set_device_password(self.password)
            if hasattr(req, "action"):
                req.action = action
            await self.send_datagram(req)
            return await self._wait_for_response([response_cmd], 3.0)

        amps = await _get(
            SetAndGetOutputElectricity, SetAndGetOutputElectricityResponse.COMMAND, 2
        )
        if amps and hasattr(amps, "electricity"):
            self.config.max_electricity = amps.electricity
            _LOGGER.info(
                f"Configured max current for {self.info.serial}: {amps.electricity}A"
            )

        lang = await _get(SetAndGetLanguage, SetAndGetLanguageResponse.COMMAND)
        if lang and hasattr(lang, "language"):
            self.config.language = lang.language

        temp = await _get(
            SetAndGetTemperatureUnit, SetAndGetTemperatureUnitResponse.COMMAND
        )
        if temp and hasattr(temp, "temperature_unit"):
            self.config.temperature_unit = temp.temperature_unit

        offline = await _get(
            SetAndGetOffLineCharge, SetAndGetOffLineChargeResponse.COMMAND
        )
        if offline and hasattr(offline, "status"):
            self.config.offline_charge = offline.status

        bright = await _get(
            SetAndGetScreenBrightness,
            SetAndGetScreenBrightnessResponse.COMMAND,
            action=1,
        )
        if bright and hasattr(bright, "brightness"):
            self.config.brightness = int(bright.brightness)

        nick = await _get(SetAndGetNickName, SetAndGetNickNameResponse.COMMAND)
        if nick and getattr(nick, "nick_name", None):
            self.config.name = nick.nick_name

        schedule = await _get(
            SetAndGetChargeSchedule, SetAndGetChargeScheduleResponse.COMMAND
        )
        if schedule and getattr(schedule, "slots", None):
            self.config.schedule = list(schedule.slots)
    
    async def charge_start(self, max_amps: int = 6, single_phase: bool = False, 
                          user_id: str = "", charge_id: str = "") -> bool:
        """Start charging"""
        if not self.is_logged_in():
            _LOGGER.error(f"EVSE {self.info.serial} not connected")
            return False
        
        try:
            charge_start = ChargeStart()
            charge_start.set_device_serial(self.info.serial)
            charge_start.set_device_password(self.password)
            charge_start.set_max_electricity(max_amps)
            charge_start.set_single_phase(single_phase)
            
            if user_id:
                charge_start.set_user_id(user_id)
            if charge_id:
                charge_start.set_charge_id(charge_id)
            else:
                import time
                charge_start.set_charge_id(f"{int(time.time())}")
            
            await self.send_datagram(charge_start)
            _LOGGER.info(f"Charge command sent: {max_amps}A")
            return True
            
        except Exception as e:
            _LOGGER.error(f"Error while starting charge: {e}")
            return False
    
    async def charge_stop(self, user_id: str = "") -> bool:
        """Stop charging"""
        if not self.is_logged_in():
            _LOGGER.error(f"EVSE {self.info.serial} not connected")
            return False
        
        try:
            charge_stop = ChargeStop()
            charge_stop.set_device_serial(self.info.serial)
            charge_stop.set_device_password(self.password)
            
            await self.send_datagram(charge_stop)
            _LOGGER.info("Charge stop command sent")
            return True
            
        except Exception as e:
            _LOGGER.error(f"Error while stopping charge: {e}")
            return False
    
    async def set_max_electricity(self, amps: int) -> bool:
        """Set the maximum current"""
        if not self.is_logged_in():
            _LOGGER.error(f"EVSE {self.info.serial} not connected")
            return False
        
        try:
            _LOGGER.info(f"Setting max current to {amps}A for {self.info.serial}")
            
            set_current = SetAndGetOutputElectricity()
            set_current.set_device_serial(self.info.serial)
            set_current.set_device_password(self.password)
            set_current.action = 1  # SET
            set_current.electricity = amps

            await self.send_datagram(set_current)
            _LOGGER.debug(f"SetAndGetOutputElectricity sent to {self.info.serial}")

            response = await self._wait_for_response(
                [SetAndGetOutputElectricityResponse.COMMAND], 5.0
            )

            if not response:
                _LOGGER.error(f"No response for set_max_electricity from {self.info.serial}")
                return False

            if hasattr(response, "electricity") and response.electricity == amps:
                self.config.max_electricity = amps
                _LOGGER.info(f"Max current confirmed at {amps}A for {self.info.serial}")
                return True
            else:
                _LOGGER.error(
                    f"Current not confirmed: requested {amps}A, "
                    f"received {getattr(response, 'electricity', 'unknown')}"
                )
                return False

        except Exception as e:
            _LOGGER.error(f"Error while setting current for {self.info.serial}: {e}")
            return False

    async def _set_config_byte(
        self,
        datagram_cls,
        response_cls,
        field: str,
        value: int,
        *,
        action: int = 1,
        config_attr: str,
    ) -> bool:
        """Generic SET helper for simple action+value config datagrams."""
        if not self.is_logged_in():
            _LOGGER.error(f"EVSE {self.info.serial} not connected")
            return False
        try:
            req = datagram_cls()
            req.set_device_serial(self.info.serial)
            req.set_device_password(self.password)
            req.action = action
            setattr(req, field, value)
            await self.send_datagram(req)
            response = await self._wait_for_response([response_cls.COMMAND], 5.0)
            if not response:
                _LOGGER.error(
                    f"No response setting {config_attr}={value} on {self.info.serial}"
                )
                return False
            reported = getattr(response, field, None)
            if reported != value:
                _LOGGER.error(
                    f"{config_attr} not confirmed: requested {value}, got {reported}"
                )
                return False
            setattr(self.config, config_attr, value)
            await self.communicator._notify_callbacks("evse_changed", self)
            return True
        except Exception as e:
            _LOGGER.error(f"Error setting {config_attr} on {self.info.serial}: {e}")
            return False

    async def set_language(self, language: int) -> bool:
        """Set UI language (1=EN … 6=HE)."""
        return await self._set_config_byte(
            SetAndGetLanguage,
            SetAndGetLanguageResponse,
            "language",
            language,
            config_attr="language",
        )

    async def set_temperature_unit(self, unit: int) -> bool:
        """Set temperature unit (1=°C, 2=°F)."""
        return await self._set_config_byte(
            SetAndGetTemperatureUnit,
            SetAndGetTemperatureUnitResponse,
            "temperature_unit",
            unit,
            config_attr="temperature_unit",
        )

    async def set_offline_charge(self, status: int) -> bool:
        """Set offline charge mode (0=enabled, 1=disabled, 2=app_only)."""
        return await self._set_config_byte(
            SetAndGetOffLineCharge,
            SetAndGetOffLineChargeResponse,
            "status",
            status,
            config_attr="offline_charge",
        )

    async def set_brightness(self, brightness: int) -> bool:
        """Set screen brightness 0–100."""
        brightness = max(0, min(100, int(brightness)))
        return await self._set_config_byte(
            SetAndGetScreenBrightness,
            SetAndGetScreenBrightnessResponse,
            "brightness",
            brightness,
            action=2,
            config_attr="brightness",
        )

    async def set_name(self, name: str) -> bool:
        """Set the EVSE nickname."""
        if not self.is_logged_in():
            _LOGGER.error(f"EVSE {self.info.serial} not connected")
            return False
        try:
            req = SetAndGetNickName()
            req.set_device_serial(self.info.serial)
            req.set_device_password(self.password)
            req.action = 1
            req.nick_name = (name or "")[:32]
            await self.send_datagram(req)
            response = await self._wait_for_response(
                [SetAndGetNickNameResponse.COMMAND], 5.0
            )
            if not response:
                return False
            self.config.name = getattr(response, "nick_name", name) or name
            await self.communicator._notify_callbacks("evse_changed", self)
            return True
        except Exception as e:
            _LOGGER.error(f"Error setting name on {self.info.serial}: {e}")
            return False

    async def get_schedule(self) -> Optional[List[ScheduleSlot]]:
        """Fetch weekly charge schedule (7 day slots)."""
        if not self.is_logged_in():
            _LOGGER.error(f"EVSE {self.info.serial} not connected")
            return None
        try:
            req = SetAndGetChargeSchedule()
            req.set_device_serial(self.info.serial)
            req.set_device_password(self.password)
            req.action = 2
            await self.send_datagram(req)
            response = await self._wait_for_response(
                [SetAndGetChargeScheduleResponse.COMMAND], 5.0
            )
            if not response or not getattr(response, "slots", None):
                return None
            self.config.schedule = list(response.slots)
            await self.communicator._notify_callbacks("evse_changed", self)
            return self.config.schedule
        except Exception as e:
            _LOGGER.error(f"Error getting schedule on {self.info.serial}: {e}")
            return None

    async def set_schedule(self, slots: List[ScheduleSlot]) -> bool:
        """Set weekly charge schedule (must send all 7 day slots)."""
        if not self.is_logged_in():
            _LOGGER.error(f"EVSE {self.info.serial} not connected")
            return False
        try:
            from .schedule import SLOT_COUNT

            padded = list(slots[:SLOT_COUNT])
            while len(padded) < SLOT_COUNT:
                padded.append(ScheduleSlot.disabled())
            req = SetAndGetChargeSchedule()
            req.set_device_serial(self.info.serial)
            req.set_device_password(self.password)
            req.action = 1
            req.slots = padded
            await self.send_datagram(req)
            response = await self._wait_for_response(
                [SetAndGetChargeScheduleResponse.COMMAND], 5.0
            )
            if not response:
                _LOGGER.error(f"No response setting schedule on {self.info.serial}")
                return False
            if getattr(response, "slots", None):
                self.config.schedule = list(response.slots)
            else:
                self.config.schedule = padded
            await self.communicator._notify_callbacks("evse_changed", self)
            return True
        except Exception as e:
            _LOGGER.error(f"Error setting schedule on {self.info.serial}: {e}")
            return False

    async def set_schedule_slot(
        self,
        day_index: int,
        *,
        hour: Optional[int] = None,
        minute: Optional[int] = None,
        duration_min: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> bool:
        """Update one weekday slot and write the full weekly schedule."""
        from .schedule import SLOT_COUNT

        if day_index < 0 or day_index >= SLOT_COUNT:
            return False
        slots = list(self.config.schedule or empty_schedule())
        while len(slots) < SLOT_COUNT:
            slots.append(ScheduleSlot.disabled())
        current = slots[day_index]

        if enabled is False:
            slots[day_index] = ScheduleSlot.disabled()
        else:
            from .schedule import MAX_DURATION_MIN, ScheduleSlot as Slot

            h = current.hour if hour is None else max(0, min(23, int(hour)))
            m = current.minute if minute is None else max(0, min(59, int(minute)))
            if duration_min is None:
                dur = current.duration_min if current.enabled else 60
            else:
                dur = max(0, min(MAX_DURATION_MIN, int(duration_min)))
            if dur <= 0:
                slots[day_index] = ScheduleSlot.disabled()
            else:
                slots[day_index] = Slot.active(h, m, dur)
        return await self.set_schedule(slots)
    
    async def sync_time(self) -> bool:
        """Synchronize EVSE clock so its Shanghai wall clock matches local time."""
        if not self.is_logged_in():
            _LOGGER.error(f"EVSE {self.info.serial} not connected")
            return False
        try:
            from .em_time import date_to_em_timestamp

            sync = SetAndGetSystemTime()
            sync.set_device_serial(self.info.serial)
            sync.set_device_password(self.password)
            sync.action = 1
            sync.timestamp = date_to_em_timestamp()
            await self.send_datagram(sync)
            response = await self._wait_for_response(
                [SetAndGetSystemTimeResponse.COMMAND], 3.0
            )
            if response:
                _LOGGER.info(
                    f"Time synchronized for {self.info.serial} "
                    f"(em_ts={sync.timestamp})"
                )
                return True
            _LOGGER.warning(f"No time sync response from {self.info.serial}")
            return False
        except Exception as e:
            _LOGGER.error(f"Error during time sync: {e}")
            return False

class Communicator:
    """Main UDP communicator"""
    
    def __init__(self, port: int = 28376):
        self.port = port
        self.socket: Optional[socket.socket] = None
        self.running = False
        self.evses: Dict[str, EVSE] = {}
        self.callbacks: Dict[str, Callable] = {}
        self._periodic_task: Optional[asyncio.Task] = None
        self._listen_task: Optional[asyncio.Task] = None
    
    async def start(self) -> int:
        """Start the communicator"""
        if self.running:
            return self.port
        
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setblocking(False)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind(('', self.port))
            
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except OSError:
                _LOGGER.warning("Broadcast not supported")
            
            self.running = True
            _LOGGER.info(f"Communicator started on port {self.port}")
            
            self._listen_task = asyncio.create_task(self._listen_loop())
            self._periodic_task = asyncio.create_task(self._periodic_checks())
            
            return self.port
            
        except Exception as e:
            _LOGGER.error(f"Error while starting: {e}")
            raise
    
    async def stop(self):
        """Stop the communicator"""
        self.running = False
        
        if self._periodic_task:
            self._periodic_task.cancel()
            self._periodic_task = None
        
        if self._listen_task:
            self._listen_task.cancel()
            self._listen_task = None
        
        if self.socket:
            self.socket.close()
            self.socket = None
        
        _LOGGER.info("Communicator stopped")
    
    async def _listen_loop(self):
        """UDP listen loop using asyncio socket API"""
        loop = asyncio.get_running_loop()
        while self.running and self.socket:
            try:
                data, addr = await loop.sock_recvfrom(self.socket, 2048)
                await self._handle_message(data, addr)
            except asyncio.CancelledError:
                break
            except (BlockingIOError, InterruptedError):
                await asyncio.sleep(0.05)
            except OSError as e:
                if self.running:
                    _LOGGER.debug(f"UDP recv error: {e}")
                    await asyncio.sleep(0.5)
            except Exception as e:
                if self.running:
                    _LOGGER.error(f"Error in listen loop: {e}")
                await asyncio.sleep(1)
        
        _LOGGER.debug("UDP listen loop ended")
    
    async def _handle_message(self, data: bytes, addr: tuple):
        """Handle a received message"""
        try:
            datagrams = parse_datagrams(data)
            
            for datagram in datagrams:
                await self._process_datagram(datagram, addr)
                
        except Exception as e:
            _LOGGER.debug(f"Error while handling message: {e}")
    
    async def _process_datagram(self, datagram: Datagram, addr: tuple):
        """Handle a received datagram"""
        ip, port = addr
        serial = datagram.get_device_serial()
        
        if not serial:
            return

        cmd = datagram.get_command()
        from_evse_addr = cmd in _EVSE_ADDRESS_COMMANDS
        
        evse = self.evses.get(serial)
        if not evse:
            # Discover only from real EVSE origin packets (not phone echoes)
            if not from_evse_addr:
                return
            evse = EVSE(self, serial, ip, port)
            self.evses[serial] = evse
            _LOGGER.info(f"New EVSE discovered: {serial} @ {ip}:{port}")
            await self._notify_callbacks('evse_added', evse)
            if evse.password and evse.session_is_stale():
                asyncio.create_task(self._ensure_session(evse))
        else:
            # Drop app/phone echoes that reuse the EVSE serial on another host.
            # Those were hijacking info.ip and faking SET/GET acknowledgements.
            if not from_evse_addr and ip != evse.info.ip:
                _LOGGER.debug(
                    f"Ignoring cmd 0x{cmd:04x} for {serial} from {ip}:{port} "
                    f"(EVSE is {evse.info.ip}:{evse.info.port})"
                )
                return
            if from_evse_addr:
                if evse.update_ip(ip, port):
                    _LOGGER.info(f"EVSE {serial} address → {ip}:{port}")
                    await self._notify_callbacks('evse_changed', evse)
            evse.last_seen = datetime.now()

        evse._resolve_waiters(datagram)

        if isinstance(datagram, Login):
            await self._handle_login(evse, datagram)
        elif isinstance(datagram, LoginResponse):
            await self._handle_login_response(evse, datagram)
        elif isinstance(datagram, SingleACStatus):
            await self._handle_status(evse, datagram)
        elif isinstance(datagram, SingleACChargingStatusPublicAuto):
            await self._handle_charging_status(evse, datagram)
        elif isinstance(datagram, CurrentChargeRecord):
            await self._handle_charge_record(evse, datagram)
        elif isinstance(datagram, Heading):
            # Real EVSEs (capture) send Heading → App; we must ack with HeadingResponse
            await self._handle_heading(evse, datagram)
        elif isinstance(datagram, HeadingResponse):
            await self._handle_heading_response(evse, datagram)
        elif isinstance(datagram, SetAndGetOutputElectricityResponse):
            await self._handle_output_electricity_response(evse, datagram)
        elif isinstance(datagram, GetVersionResponse):
            await self._handle_version_response(evse, datagram)
        elif isinstance(datagram, SetAndGetSystemTimeResponse):
            _LOGGER.debug(f"System time response from {serial}: {datagram.timestamp}")
        elif isinstance(datagram, SetAndGetLanguageResponse):
            evse.config.language = datagram.language
            await self._notify_callbacks("evse_changed", evse)
        elif isinstance(datagram, SetAndGetTemperatureUnitResponse):
            evse.config.temperature_unit = datagram.temperature_unit
            await self._notify_callbacks("evse_changed", evse)
        elif isinstance(datagram, SetAndGetOffLineChargeResponse):
            evse.config.offline_charge = datagram.status
            await self._notify_callbacks("evse_changed", evse)
        elif isinstance(datagram, SetAndGetScreenBrightnessResponse):
            evse.config.brightness = int(datagram.brightness)
            await self._notify_callbacks("evse_changed", evse)
        elif isinstance(datagram, SetAndGetNickNameResponse):
            if datagram.nick_name:
                evse.config.name = datagram.nick_name
                await self._notify_callbacks("evse_changed", evse)
        elif isinstance(datagram, SetAndGetChargeScheduleResponse):
            if getattr(datagram, "slots", None):
                evse.config.schedule = list(datagram.slots)
                await self._notify_callbacks("evse_changed", evse)
        elif isinstance(datagram, PasswordErrorResponse):
            _LOGGER.warning(
                f"PasswordErrorResponse for {serial} — marking session invalid"
            )
            evse._logged_in = False
            evse.last_active_login = None
            await self._notify_callbacks("evse_changed", evse)
            if evse.password:
                asyncio.create_task(self._ensure_session(evse))
    
    async def _handle_login_response(self, evse: EVSE, datagram: LoginResponse):
        """Handle login/discovery response (0x0002) — same info fields as Login."""
        _LOGGER.info(f"LoginResponse received from {evse.info.serial}")
        if getattr(datagram, "brand", None):
            evse.info.brand = datagram.brand
        if getattr(datagram, "model", None):
            evse.info.model = datagram.model
        if getattr(datagram, "hardware_version", None):
            evse.info.hardware_version = datagram.hardware_version
        if getattr(datagram, "max_power", 0):
            evse.info.max_power = datagram.max_power
        if getattr(datagram, "max_electricity", 0):
            evse.info.max_electricity = datagram.max_electricity
        if getattr(datagram, "hot_line", None):
            evse.info.hot_line = datagram.hot_line
        await self._notify_callbacks("evse_discovered", evse)    
    async def _handle_login(self, evse: EVSE, datagram: Login):
        """Handle an EVSE discovery broadcast — update info; re-auth if needed."""
        evse.info.brand = datagram.brand
        evse.info.model = datagram.model
        evse.info.hardware_version = datagram.hardware_version
        evse.info.max_power = datagram.max_power
        evse.info.max_electricity = datagram.max_electricity
        evse.info.hot_line = datagram.hot_line
        evse.info.phases = datagram.phases
        evse.info.can_force_single_phase = datagram.can_force_single_phase
        evse.info.feature = datagram.feature
        evse.info.support_new = datagram.support_new
        await self._notify_callbacks('evse_discovered', evse)
        # Broadcasts alone do not keep a UDP session — reclaim if we have creds.
        if evse.password and evse.session_is_stale():
            asyncio.create_task(self._ensure_session(evse))
    
    async def _handle_status(self, evse: EVSE, datagram: SingleACStatus):
        """Handle an AC status"""
        if not evse.state:
            evse.state = EVSEState()

        evse.state.current_power = datagram.current_power
        evse.state.current_amount = datagram.total_kwh_counter
        evse.state.l1_voltage = datagram.l1_voltage
        evse.state.l1_electricity = datagram.l1_electricity
        evse.state.l2_voltage = datagram.l2_voltage
        evse.state.l2_electricity = datagram.l2_electricity
        evse.state.l3_voltage = datagram.l3_voltage
        evse.state.l3_electricity = datagram.l3_electricity
        evse.state.inner_temp = datagram.inner_temp
        evse.state.outer_temp = datagram.outer_temp
        evse.state.current_state = datagram.current_state
        evse.state.gun_state = datagram.gun_state
        evse.state.output_state = datagram.output_state
        evse.state.emergency_btn_state = datagram.emergency_btn_state
        evse.state.errors = datagram.errors
        evse.touch_session()
        evse.last_status_at = datetime.now()
        _LOGGER.debug(
            f"Status received for {evse.info.serial}: "
            f"L1={datagram.l1_voltage}V, Temp={datagram.inner_temp}°C"
        )
        response = SingleACStatusResponse()
        response.set_device_serial(evse.info.serial)
        response.set_device_password(evse.password)
        await evse.send_datagram(response)
        await self._notify_callbacks('evse_state_changed', evse)
    
    async def _handle_charging_status(self, evse: EVSE, datagram: SingleACChargingStatusPublicAuto):
        """Handle automatic AC charging status (command 0x0005)"""
        _LOGGER.debug(f"Charge status received for {evse.info.serial}")
        if not evse.current_charge:
            evse.current_charge = EVSECurrentCharge()
        evse.current_charge.charge_id = datagram.charge_id
        evse.current_charge.current_state = datagram.current_state
        evse.current_charge.start_type = datagram.start_type
        evse.current_charge.charge_type = datagram.charge_type
        evse.current_charge.max_duration_minutes = datagram.max_duration_minutes
        evse.current_charge.max_energy_kwh = datagram.max_energy_kwh
        evse.current_charge.max_electricity = datagram.max_electricity
        evse.current_charge.start_date = datagram.start_date
        evse.current_charge.duration_seconds = datagram.duration_seconds
        evse.current_charge.start_kwh_counter = datagram.start_kwh_counter
        evse.current_charge.current_kwh_counter = datagram.current_kwh_counter
        evse.current_charge.charge_kwh = datagram.charge_kwh
        evse.current_charge.charge_price = datagram.charge_price
        evse.current_charge.charge_fee = datagram.charge_fee
        evse.touch_session()
        evse.current_charge.user_id = getattr(datagram, "user_id", "") or ""
        # Keep status current_state in sync when only charge packets arrive
        if evse.state:
            evse.state.current_state = datagram.current_state
        response = SingleACChargingStatusResponse()
        response.set_device_serial(evse.info.serial)
        response.set_device_password(evse.password)
        await evse.send_datagram(response)
        await self._notify_callbacks('evse_charge_status_changed', evse)
    
    async def _handle_charge_record(self, evse: EVSE, datagram: CurrentChargeRecord):
        """Handle a charge record"""
        if not evse.current_charge:
            evse.current_charge = EVSECurrentCharge()
        
        evse.current_charge.port = datagram.line_id
        evse.current_charge.charge_id = datagram.charge_id
        evse.current_charge.start_type = datagram.start_type
        evse.current_charge.charge_type = datagram.charge_type
        evse.current_charge.reservation_date = datagram.reservation_data
        evse.current_charge.user_id = datagram.start_user_id
        evse.current_charge.start_date = datagram.start_date
        evse.current_charge.duration_seconds = datagram.charged_time
        evse.current_charge.start_kwh_counter = datagram.charge_start_power
        evse.current_charge.current_kwh_counter = datagram.charge_stop_power
        evse.current_charge.charge_kwh = datagram.charge_power
        evse.current_charge.charge_price = datagram.charge_price
        evse.current_charge.fee_type = datagram.fee_type
        evse.current_charge.charge_fee = datagram.charge_fee
        await self._notify_callbacks('evse_charge_changed', evse)
    
    async def _handle_heading(self, evse: EVSE, datagram: Heading):
        """EVSE → App keepalive (observed on real hardware). Ack with HeadingResponse."""
        if not evse.password:
            # Still refresh last_seen; cannot auth-ack without password
            return
        response = HeadingResponse()
        response.set_device_serial(evse.info.serial)
        response.set_device_password(evse.password)
        await evse.send_datagram(response)
        evse.touch_session()

    async def _handle_heading_response(self, evse: EVSE, datagram: HeadingResponse):
        """Handle HeadingResponse keepalive ack from EVSE (App → EVSE Heading)."""
        evse.touch_session()
        _LOGGER.debug(f"Keepalive ack from {evse.info.serial}")

    async def _handle_version_response(self, evse: EVSE, datagram: GetVersionResponse):
        """Handle GetVersionResponse — fill hardware/software/features."""
        if datagram.hardware_version:
            evse.info.hardware_version = datagram.hardware_version
        if datagram.software_version:
            evse.info.software_version = datagram.software_version
        if datagram.feature:
            evse.info.feature = datagram.feature
        evse.info.support_new = datagram.support_new
        _LOGGER.info(
            f"Version for {evse.info.serial}: "
            f"hw={datagram.hardware_version} sw={datagram.software_version}"
        )
        await self._notify_callbacks("evse_changed", evse)
    
    async def _handle_output_electricity_response(self, evse: EVSE, datagram: SetAndGetOutputElectricityResponse):
        """Handle a current configuration response"""
        _LOGGER.debug(
            f"Output current response from {evse.info.serial}: {datagram.electricity}A"
        )
        if hasattr(datagram, "electricity") and datagram.electricity > 0:
            evse.config.max_electricity = datagram.electricity
            await self._notify_callbacks("evse_changed", evse)
    
    async def send(self, datagram: Datagram, evse: EVSE) -> int:
        """Send a datagram"""
        if not self.running or not self.socket:
            raise RuntimeError("Communicator not started")
        
        if not datagram.get_device_serial():
            datagram.set_device_serial(evse.info.serial)
        
        if datagram.get_device_password() is None and evse.password:
            datagram.set_device_password(evse.password)
        
        buffer = datagram.pack()
        
        loop = asyncio.get_running_loop()
        await loop.sock_sendto(
            self.socket,
            buffer,
            (evse.info.ip, evse.info.port),
        )
        
        return len(buffer)
    
    async def _ensure_session(self, evse: EVSE) -> None:
        """Re-login when the UDP session is dead or silent (with backoff)."""
        if not evse.password or not evse.session_is_stale():
            return
        now = datetime.now()
        if evse._last_login_attempt and (now - evse._last_login_attempt).total_seconds() < 30:
            return
        evse._last_login_attempt = now
        # Avoid looking "logged in" while we know the session is dead.
        evse._logged_in = False
        _LOGGER.warning(
            f"Session stale for {evse.info.serial} "
            f"(online={evse.is_online()}, last_active={evse.last_active_login}), "
            f"re-authenticating"
        )
        try:
            await evse.login(evse.password)
        except Exception as e:
            _LOGGER.error(f"Re-login failed for {evse.info.serial}: {e}")

    async def _periodic_checks(self):
        """Keepalive while healthy; re-login when the session goes silent."""
        while self.running:
            try:
                await asyncio.sleep(15)

                for evse in list(self.evses.values()):
                    if not evse.password:
                        continue

                    if evse.session_is_stale():
                        await self._ensure_session(evse)
                        continue

                    try:
                        await evse.send_keepalive()
                    except Exception as e:
                        _LOGGER.debug(f"Keepalive failed for {evse.info.serial}: {e}")

                    # Logged in but no SingleACStatus for 2 min (incl. never) → reclaim.
                    # Heading acks alone must not keep a frozen electrical state forever.
                    status_ref = evse.last_status_at or evse.last_login_ok_at
                    if status_ref:
                        status_idle = (datetime.now() - status_ref).total_seconds()
                        if status_idle > 120:
                            _LOGGER.warning(
                                f"No status from {evse.info.serial} for "
                                f"{int(status_idle)}s, re-authenticating"
                            )
                            evse._logged_in = False
                            evse.last_active_login = None
                            await self._ensure_session(evse)

            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"Error in periodic checks: {e}")
    
    async def _notify_callbacks(self, event: str, evse: EVSE):
        """Notify callbacks"""
        for callback in self.callbacks.values():
            try:
                await callback(event, evse)
            except Exception as e:
                _LOGGER.error(f"Error in callback: {e}")
    
    def add_callback(self, name: str, callback: Callable):
        """Add a callback"""
        self.callbacks[name] = callback
    
    def remove_callback(self, name: str):
        """Remove a callback"""
        self.callbacks.pop(name, None)
    
    def get_evse(self, serial: str) -> Optional[EVSE]:
        """Get an EVSE by its serial number"""
        return self.evses.get(serial)
    
    def get_all_evses(self) -> Dict[str, EVSE]:
        """Get all EVSEs"""
        return self.evses.copy()
    
    def close(self):
        """Close the communicator and release resources"""
        _LOGGER.debug("Closing UDP communicator")
        self.running = False

        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
        if self._periodic_task and not self._periodic_task.done():
            self._periodic_task.cancel()

        if self.socket:
            try:
                self.socket.close()
            except Exception as e:
                _LOGGER.debug(f"Error while closing socket: {e}")
            finally:
                self.socket = None

        _LOGGER.debug("UDP communicator closed")

# Global singleton
_communicator_instance: Optional[Communicator] = None

def get_communicator(port: int = 28376) -> Communicator:
    """Get the singleton instance of the communicator"""
    global _communicator_instance
    if _communicator_instance is None:
        _communicator_instance = Communicator(port=port)
    elif (
        not _communicator_instance.running
        and _communicator_instance.port != port
    ):
        _communicator_instance = Communicator(port=port)
    return _communicator_instance