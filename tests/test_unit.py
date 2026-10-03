#!/usr/bin/env python3
"""Fast offline unit tests (no Home Assistant, no hardware).

Run:
    python tests/test_unit.py
    python -m unittest tests.test_unit -v
"""

from __future__ import annotations

import select as _stdlib_select
import struct
import sys
import unittest
from pathlib import Path

# Home Assistant platform file `select.py` would shadow the stdlib `select`
# module once the integration root is on sys.path — pin stdlib first.
sys.modules["select"] = _stdlib_select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "evsemasterudp"))

from protocol.config_maps import (  # noqa: E402
    LANGUAGE_BY_CODE,
    LANGUAGE_OPTIONS,
    START_MODE_BY_CODE,
    START_MODE_OPTIONS,
    TEMP_UNIT_BY_CODE,
    TEMP_UNIT_OPTIONS,
)
from protocol.datagram import parse_datagrams  # noqa: E402
from protocol.datagrams import (  # noqa: E402
    SetAndGetLanguage,
    SetAndGetLanguageResponse,
    SetAndGetNickName,
    SetAndGetNickNameResponse,
    SetAndGetOffLineCharge,
    SetAndGetOffLineChargeResponse,
    SetAndGetOutputElectricity,
    SetAndGetOutputElectricityResponse,
    SetAndGetScreenBrightness,
    SetAndGetScreenBrightnessResponse,
    SetAndGetTemperatureUnit,
    SetAndGetTemperatureUnitResponse,
    SetAndGetChargeSchedule,
    SetAndGetChargeScheduleResponse,
)
from protocol.schedule import (  # noqa: E402
    ScheduleSlot,
    empty_schedule,
    pack_schedule_payload,
    unpack_schedule_payload,
    schedule_to_dict,
)
from protocol.communicator import EVSE, Communicator  # noqa: E402

SERIAL = "8662888793459659"
PASSWORD = "123456"


def _wire(datagram) -> bytes:
    datagram.set_device_serial(SERIAL)
    datagram.set_device_password(PASSWORD)
    return datagram.pack()


class TestConfigMaps(unittest.TestCase):
    def test_language_roundtrip(self):
        for name, code in LANGUAGE_OPTIONS.items():
            self.assertEqual(LANGUAGE_BY_CODE[code], name)

    def test_temp_unit_c_f(self):
        self.assertEqual(TEMP_UNIT_OPTIONS["C"], 1)
        self.assertEqual(TEMP_UNIT_OPTIONS["F"], 2)
        self.assertEqual(TEMP_UNIT_BY_CODE[1], "C")
        self.assertEqual(TEMP_UNIT_BY_CODE[2], "F")

    def test_start_mode_labels(self):
        self.assertEqual(START_MODE_OPTIONS["app&button"], 0)
        self.assertEqual(START_MODE_OPTIONS["app"], 1)
        self.assertEqual(START_MODE_OPTIONS["auto"], 2)
        self.assertNotIn("button", START_MODE_OPTIONS)
        self.assertEqual(START_MODE_BY_CODE[0], "app&button")
        self.assertEqual(START_MODE_BY_CODE[2], "auto")
        self.assertEqual(set(START_MODE_OPTIONS), {"app", "app&button", "auto"})


class TestBrightness(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(SetAndGetScreenBrightness.COMMAND, 0x8162)
        self.assertEqual(SetAndGetScreenBrightnessResponse.COMMAND, 0x0162)

    def test_set_payload(self):
        req = SetAndGetScreenBrightness()
        req.action = 2
        req.brightness = 100
        self.assertEqual(req.pack_payload(), bytes([0x00, 0x02, 100]))

    def test_get_payload(self):
        req = SetAndGetScreenBrightness()
        req.action = 1
        self.assertEqual(req.pack_payload(), bytes([0x00, 0x01, 0x00, 0x01, 0x00, 0x03]))

    def test_response_unpack_capture(self):
        # From brightness.pcap phone ack: 00 03 64 02 00 01 00 00
        resp = SetAndGetScreenBrightnessResponse()
        resp.unpack_payload(bytes.fromhex("0003640200010000"))
        self.assertEqual(resp.action, 3)
        self.assertEqual(resp.brightness, 100)

    def test_response_unpack_mid(self):
        resp = SetAndGetScreenBrightnessResponse()
        resp.unpack_payload(bytes.fromhex("0001370200010000"))
        self.assertEqual(resp.brightness, 0x37)  # 55

    def test_pack_parse_roundtrip(self):
        req = SetAndGetScreenBrightness()
        req.action = 2
        req.brightness = 12
        wire = _wire(req)
        parsed = parse_datagrams(bytearray(wire))
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].get_command(), 0x8162)
        self.assertEqual(parsed[0].brightness, 12)


class TestLanguage(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(SetAndGetLanguage.COMMAND, 0x810F)
        self.assertEqual(SetAndGetLanguageResponse.COMMAND, 0x010F)

    def test_set_french(self):
        req = SetAndGetLanguage()
        req.action = 1
        req.language = LANGUAGE_OPTIONS["french"]
        self.assertEqual(req.pack_payload(), bytes([1, 4]))

    def test_get(self):
        req = SetAndGetLanguage()
        req.action = 2
        self.assertEqual(req.pack_payload(), bytes([2, 0]))

    def test_response_unpack(self):
        resp = SetAndGetLanguageResponse()
        resp.unpack_payload(bytes([2, 4]))
        self.assertEqual(resp.language, 4)
        self.assertEqual(LANGUAGE_BY_CODE[resp.language], "french")


class TestTemperatureUnit(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(SetAndGetTemperatureUnit.COMMAND, 0x8112)
        self.assertEqual(SetAndGetTemperatureUnitResponse.COMMAND, 0x0112)

    def test_set_c_and_f(self):
        for label, code in TEMP_UNIT_OPTIONS.items():
            req = SetAndGetTemperatureUnit()
            req.action = 1
            req.temperature_unit = code
            self.assertEqual(req.pack_payload(), bytes([1, code]), label)

    def test_response_unpack(self):
        resp = SetAndGetTemperatureUnitResponse()
        resp.unpack_payload(bytes([1, 2]))
        self.assertEqual(TEMP_UNIT_BY_CODE[resp.temperature_unit], "F")


class TestStartMode(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(SetAndGetOffLineCharge.COMMAND, 0x810D)
        self.assertEqual(SetAndGetOffLineChargeResponse.COMMAND, 0x010D)

    def test_set_each_mode(self):
        for label, code in START_MODE_OPTIONS.items():
            req = SetAndGetOffLineCharge()
            req.action = 1
            req.status = code
            self.assertEqual(req.pack_payload(), bytes([1, code]), label)

    def test_get(self):
        req = SetAndGetOffLineCharge()
        req.action = 2
        self.assertEqual(req.pack_payload(), bytes([2, 0]))

    def test_response_unpack(self):
        resp = SetAndGetOffLineChargeResponse()
        resp.unpack_payload(bytes([1, 2]))
        self.assertEqual(START_MODE_BY_CODE[resp.status], "auto")


class TestNickname(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(SetAndGetNickName.COMMAND, 0x8108)
        self.assertEqual(SetAndGetNickNameResponse.COMMAND, 0x0108)

    def test_set_and_unpack(self):
        req = SetAndGetNickName()
        req.action = 1
        req.nick_name = "EVSEMaster"
        payload = req.pack_payload()
        self.assertEqual(payload[0], 1)
        self.assertTrue(payload[1:].startswith(b"EVSEMaster"))

        resp = SetAndGetNickNameResponse()
        resp.unpack_payload(payload)
        self.assertEqual(resp.nick_name, "EVSEMaster")


class TestChargeSchedule(unittest.TestCase):
    """AlarmChargeStrategy 0x810e / 0x010e — format from schedule.pcap."""

    def test_commands(self):
        self.assertEqual(SetAndGetChargeSchedule.COMMAND, 0x810e)
        self.assertEqual(SetAndGetChargeScheduleResponse.COMMAND, 0x010e)

    def test_pack_00_00_60m(self):
        # Early capture ``030000003cffffffff`` was ON + 00:00 + 60 min (not 03:00).
        slots = [ScheduleSlot.active(0, 0, 60) for _ in range(7)]
        payload = pack_schedule_payload(1, slots)
        self.assertEqual(payload[0], 1)
        self.assertEqual(len(payload), 1 + 7 * 9)
        self.assertEqual(payload[1:10], bytes.fromhex("030000003cffffffff"))

        req = SetAndGetChargeSchedule()
        req.action = 1
        req.slots = slots
        self.assertEqual(req.pack_payload(), payload)

    def test_pack_off_slot(self):
        slots = [ScheduleSlot.disabled() for _ in range(7)]
        payload = pack_schedule_payload(1, slots)
        self.assertEqual(payload[1:10], bytes.fromhex("0100000000ffffffff"))

    def test_pack_23_59_22h58_capture(self):
        # User: start 23:59, end 22:57, duration 22h58 on Tue/Thu/Sat
        slot = ScheduleSlot.active(23, 59, 22 * 60 + 58)
        self.assertEqual(slot.pack(), bytes.fromhex("03173b0562ffffffff"))
        self.assertEqual(slot.end_hour, 22)
        self.assertEqual(slot.end_minute, 57)
        self.assertEqual(slot.label, "23:59→22:57/22h58m")

        slots = empty_schedule()
        for i in (1, 3, 5):  # tue, thu, sat
            slots[i] = ScheduleSlot.active(23, 59, 22 * 60 + 58)
        payload = pack_schedule_payload(1, slots)
        # Matches capture SET body after action
        expected = bytes.fromhex(
            "01"
            "0100000000ffffffff"  # mon off
            "03173b0562ffffffff"  # tue
            "0100000000ffffffff"  # wed
            "03173b0562ffffffff"  # thu
            "0100000000ffffffff"  # fri
            "03173b0562ffffffff"  # sat
            "0100000000ffffffff"  # sun
        )
        self.assertEqual(payload, expected)

    def test_get_payload(self):
        req = SetAndGetChargeSchedule()
        req.action = 2
        self.assertEqual(req.pack_payload(), bytes([2]) + bytes(63))

    def test_unpack_roundtrip(self):
        slots = empty_schedule()
        slots[0] = ScheduleSlot.active(23, 59, 1378)
        slots[1] = ScheduleSlot.disabled()
        payload = pack_schedule_payload(1, slots)
        action, decoded = unpack_schedule_payload(payload)
        self.assertEqual(action, 1)
        self.assertTrue(decoded[0].enabled)
        self.assertEqual(decoded[0].hour, 23)
        self.assertEqual(decoded[0].minute, 59)
        self.assertEqual(decoded[0].duration_min, 1378)
        self.assertFalse(decoded[1].enabled)
        self.assertEqual(decoded[1].label, "off")

        resp = SetAndGetChargeScheduleResponse()
        resp.unpack_payload(payload)
        as_dict = schedule_to_dict(resp.slots)
        self.assertEqual(as_dict["monday"]["hour"], 23)
        self.assertEqual(as_dict["monday"]["duration_min"], 1378)
        self.assertFalse(as_dict["tuesday"]["enabled"])


class TestSystemTime(unittest.TestCase):
    """0x8101 / 0x0101 — China-shifted timestamp from schedule.pcap."""

    def test_commands(self):
        from protocol.datagrams import SetAndGetSystemTime, SetAndGetSystemTimeResponse

        self.assertEqual(SetAndGetSystemTime.COMMAND, 0x8101)
        self.assertEqual(SetAndGetSystemTimeResponse.COMMAND, 0x0101)

    def test_pack_matches_capture_shape(self):
        from protocol.datagrams import SetAndGetSystemTime

        req = SetAndGetSystemTime()
        req.action = 1
        req.timestamp = 1790956085  # capture: local wall → China digits
        payload = req.pack_payload()
        self.assertEqual(len(payload), 16)
        self.assertEqual(payload[:5], bytes.fromhex("016abfd235"))
        self.assertEqual(payload[5:], bytes(11))

    def test_response_unpack_capture(self):
        from protocol.datagrams import SetAndGetSystemTimeResponse

        resp = SetAndGetSystemTimeResponse()
        resp.unpack_payload(bytes.fromhex("006abfd235"))
        self.assertEqual(resp.action, 0)
        self.assertEqual(resp.timestamp, 1790956085)

    def test_em_timestamp_roundtrip_wall_clock(self):
        from datetime import datetime, timezone, timedelta
        from protocol.em_time import date_to_em_timestamp, em_timestamp_to_local, EM_TZ

        # Simulate Paris summer UTC+2 wall 23:48:05
        local = datetime(2026, 10, 2, 23, 48, 5, tzinfo=timezone(timedelta(hours=2)))
        ts = date_to_em_timestamp(local)
        self.assertEqual(ts, 1790956085)
        china = datetime.fromtimestamp(ts, EM_TZ)
        self.assertEqual((china.hour, china.minute, china.second), (23, 48, 5))
        decoded = em_timestamp_to_local(ts)
        self.assertEqual((decoded.hour, decoded.minute, decoded.second), (23, 48, 5))


class TestOutputElectricity(unittest.TestCase):
    def test_set_amps(self):
        req = SetAndGetOutputElectricity()
        req.action = 1
        req.electricity = 32
        self.assertEqual(req.pack_payload(), bytes([1, 32]))

    def test_response(self):
        resp = SetAndGetOutputElectricityResponse()
        resp.unpack_payload(bytes([2, 32]))
        self.assertEqual(resp.electricity, 32)


class TestSessionHealth(unittest.TestCase):
    def test_outbound_heading_does_not_refresh_session(self):
        from datetime import datetime, timedelta
        from protocol.datagrams import Heading

        comm = Communicator(port=0)
        evse = EVSE(comm, SERIAL, "192.168.20.97", 28376)
        evse.password = PASSWORD
        evse._logged_in = True
        stale = datetime.now() - timedelta(seconds=120)
        evse.last_active_login = stale
        evse.last_seen = datetime.now()
        self.assertTrue(evse.session_is_stale())

        # Sending Heading must not fake a live session (bug that blocked re-login).
        heading = Heading()
        # send_datagram would need a socket; just assert helper contract:
        # touch_session is the only way outbound path should refresh — and
        # send_datagram no longer calls it for Heading.
        self.assertTrue(evse.session_is_stale())
        evse.touch_session()
        self.assertFalse(evse.session_is_stale())

    def test_offline_is_stale_even_if_flag_logged_in(self):
        from datetime import datetime, timedelta

        comm = Communicator(port=0)
        evse = EVSE(comm, SERIAL, "192.168.20.97", 28376)
        evse.password = PASSWORD
        evse._logged_in = True
        evse.last_seen = datetime.now() - timedelta(seconds=200)
        evse.last_active_login = datetime.now()
        self.assertTrue(evse.session_is_stale())
        self.assertFalse(evse.is_logged_in())

    def test_login_broadcast_alone_does_not_count_as_session(self):
        from datetime import datetime, timedelta

        # Discovery Login keeps last_seen fresh, but without status/heading
        # the session must still be considered stale.
        comm = Communicator(port=0)
        evse = EVSE(comm, SERIAL, "192.168.20.97", 28376)
        evse.password = PASSWORD
        evse._logged_in = True
        evse.last_seen = datetime.now()
        evse.last_active_login = datetime.now() - timedelta(seconds=120)
        evse.last_status_at = None
        evse.last_login_ok_at = datetime.now() - timedelta(seconds=120)
        self.assertTrue(evse.is_online())
        self.assertTrue(evse.session_is_stale())


class TestMetaState(unittest.TestCase):
    def test_charging_requires_power_or_output(self):
        comm = Communicator(port=0)
        evse = EVSE(comm, SERIAL, "192.168.20.97", 28376)
        evse._logged_in = True
        evse.last_seen = __import__("datetime").datetime.now()
        from protocol.communicator import EVSEState

        evse.state = EVSEState()
        evse.state.emergency_btn_state = 1  # 1=OK, 0=pressed
        evse.state.current_state = 14
        evse.state.output_state = 0
        evse.state.current_power = 0
        evse.state.gun_state = 1
        self.assertEqual(evse.get_meta_state(), "IDLE")

        evse.state.output_state = 1
        evse.state.current_power = 5000
        self.assertEqual(evse.get_meta_state(), "CHARGING")

        evse.state.output_state = 2
        evse.state.current_power = 0
        evse.state.gun_state = 2
        self.assertEqual(evse.get_meta_state(), "PLUGGED_IN")


class TestAddressTrust(unittest.IsolatedAsyncioTestCase):
    async def test_phone_echo_does_not_hijack_evse_ip(self):
        from protocol.datagrams import Login, SetAndGetScreenBrightnessResponse

        comm = Communicator(port=28376)
        login = Login()
        login.set_device_serial(SERIAL)
        login.brand = "EVSE"
        login.model = "SQW49"
        login.hardware_version = "x"
        login.max_power = 7360
        login.max_electricity = 32
        login.hot_line = ""
        login.phases = 1
        login.can_force_single_phase = False
        login.feature = 0
        login.support_new = 0

        await comm._process_datagram(login, ("192.168.20.97", 39576))
        evse = comm.evses[SERIAL]
        self.assertEqual(evse.info.ip, "192.168.20.97")
        self.assertEqual(evse.info.port, 39576)

        echo = SetAndGetScreenBrightnessResponse()
        echo.set_device_serial(SERIAL)
        echo.brightness = 12
        echo.action = 3
        await comm._process_datagram(echo, ("192.168.20.37", 23898))
        self.assertEqual(evse.info.ip, "192.168.20.97")
        self.assertEqual(evse.info.port, 39576)
        self.assertEqual(evse.config.brightness, 100)


class TestCaptureSamples(unittest.TestCase):
    """Replay payload snippets from brightness.pcap."""

    def test_parse_brightness_set_wire(self):
        # EVSE→app style SET brightness=100 body after rebuilding a minimal frame
        req = SetAndGetScreenBrightness()
        req.action = 2
        req.brightness = 100
        wire = _wire(req)
        self.assertEqual(struct.unpack(">H", wire[19:21])[0], 0x8162)
        dgrams = parse_datagrams(bytearray(wire))
        self.assertEqual(dgrams[0].brightness, 100)

    def test_parse_start_mode_set_app(self):
        req = SetAndGetOffLineCharge()
        req.action = 1
        req.status = 2
        dgrams = parse_datagrams(bytearray(_wire(req)))
        self.assertEqual(dgrams[0].status, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
