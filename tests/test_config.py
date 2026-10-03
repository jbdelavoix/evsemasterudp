#!/usr/bin/env python3
"""Live GET/SET of EVSE config — no Home Assistant.

Close the EVSEMaster app first, run a command, then reopen the app to verify.

Examples:
    EVSE_PASSWORD=123456 python tests/test_config.py
    EVSE_PASSWORD=123456 python tests/test_config.py --brightness 50
    EVSE_PASSWORD=123456 python tests/test_config.py --temp F
    EVSE_PASSWORD=123456 python tests/test_config.py --start-mode auto
    EVSE_PASSWORD=123456 python tests/test_config.py --language french
    EVSE_PASSWORD=123456 python tests/test_config.py --nickname Garage
    EVSE_PASSWORD=123456 python tests/test_config.py --amps 16
    EVSE_PASSWORD=123456 python tests/test_config.py --schedule all=03:00/60
    EVSE_PASSWORD=123456 python tests/test_config.py --schedule tue=23:59/22:58,thu=23:59/22:58,sat=23:59/22:58
    EVSE_PASSWORD=123456 python tests/test_config.py --schedule-clear
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
import select as _stdlib_select
import sys
from pathlib import Path

sys.modules["select"] = _stdlib_select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "evsemasterudp"))

from protocol.config_maps import (  # noqa: E402
    LANGUAGE_OPTIONS,
    START_MODE_BY_CODE,
    START_MODE_CLI_OPTIONS,
    TEMP_UNIT_BY_CODE,
    TEMP_UNIT_OPTIONS,
)
from protocol.communicator import Communicator  # noqa: E402
from protocol.schedule import (  # noqa: E402
    WEEKDAYS,
    ScheduleSlot,
    empty_schedule,
    schedule_to_dict,
)

_SLOT_RE = re.compile(
    r"^(?:(\d{1,2}):(\d{2})(?:/(\d{1,2}):(\d{2})|/(\d{1,4}))?|off)$",
    re.IGNORECASE,
)
_DAY_ALIASES = {
    "mon": "monday",
    "tue": "tuesday",
    "wed": "wednesday",
    "thu": "thursday",
    "fri": "friday",
    "sat": "saturday",
    "sun": "sunday",
    "all": "all",
    **{d: d for d in WEEKDAYS},
}


def _parse_slot(spec: str) -> ScheduleSlot:
    from protocol.schedule import MAX_DURATION_MIN

    raw = spec.strip()
    m = _SLOT_RE.match(raw)
    if not m:
        raise argparse.ArgumentTypeError(
            f"Bad slot {spec!r}; use HH:MM[/minutes|/HH:MM duration] or off"
        )
    if raw.lower() == "off":
        return ScheduleSlot.disabled()
    hour = int(m.group(1))
    minute = int(m.group(2))
    if m.group(3) is not None:
        duration = int(m.group(3)) * 60 + int(m.group(4))
    elif m.group(5) is not None:
        duration = int(m.group(5))
    else:
        duration = 60
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= duration <= MAX_DURATION_MIN):
        raise argparse.ArgumentTypeError(f"Out of range slot {spec!r}")
    if duration <= 0:
        return ScheduleSlot.disabled()
    return ScheduleSlot.active(hour, minute, duration)


def _parse_schedule_arg(value: str) -> list[ScheduleSlot]:
    """Parse ``mon=03:00/60,tue=23:59/22:58`` into 7 slots (unmentioned = off)."""
    slots = empty_schedule()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise argparse.ArgumentTypeError(
                f"Expected day=HH:MM[/dur], got {part!r}"
            )
        day_raw, spec = part.split("=", 1)
        day_key = _DAY_ALIASES.get(day_raw.strip().lower())
        if day_key is None:
            raise argparse.ArgumentTypeError(f"Unknown day {day_raw!r}")
        slot = _parse_slot(spec)
        if day_key == "all":
            slots = [
                ScheduleSlot.active(slot.hour, slot.minute, slot.duration_min)
                if slot.enabled
                else ScheduleSlot.disabled()
                for _ in range(7)
            ]
        else:
            slots[WEEKDAYS.index(day_key)] = slot
    return slots


def _print_schedule(evse) -> None:
    slots = evse.config.schedule or empty_schedule()
    print("\nWeekly schedule (AlarmChargeStrategy)")
    for day, info in schedule_to_dict(slots).items():
        print(f"  {day:10s}: {info['label']}")


def _print_config(evse) -> None:
    cfg = evse.config
    lang_name = next(
        (k for k, v in LANGUAGE_OPTIONS.items() if v == cfg.language),
        f"unknown({cfg.language})",
    )
    start = START_MODE_BY_CODE.get(
        cfg.offline_charge, f"unknown({cfg.offline_charge})"
    )
    print("\nCurrent config")
    print(f"  address         : {evse.info.ip}:{evse.info.port}")
    print(f"  nickname        : {cfg.name!r}")
    print(f"  max current     : {cfg.max_electricity} A")
    print(f"  brightness      : {cfg.brightness} %")
    print(
        f"  temperature unit: "
        f"{TEMP_UNIT_BY_CODE.get(cfg.temperature_unit, f'unknown({cfg.temperature_unit})')}"
    )
    print(f"  language        : {lang_name}")
    print(f"  start mode      : {start}")
    _print_schedule(evse)


async def _discover(comm: Communicator, timeout: int, serial: str | None):
    print(f"Discovery ({timeout}s) — close EVSEMaster app…")
    for i in range(timeout):
        await asyncio.sleep(1)
        if not comm.evses:
            print(f"  … {i + 1}/{timeout}s", end="\r")
            continue
        if serial:
            evse = comm.evses.get(serial)
            if evse:
                print(f"\n  Found requested {evse.info.serial} @ {evse.info.ip}")
                return evse
        else:
            evse = next(iter(comm.evses.values()))
            print(f"\n  Found {evse.info.serial} @ {evse.info.ip}")
            return evse
    print()
    return None


async def run(args: argparse.Namespace) -> int:
    password = args.password or os.environ.get("EVSE_PASSWORD")
    if not password:
        password = getpass.getpass("EVSE password: ")

    comm = Communicator(port=args.port)
    await comm.start()
    try:
        evse = await _discover(comm, args.timeout, args.serial)
        if not evse:
            print("No EVSE found")
            return 1

        print("Login…")
        if not await evse.login(password):
            print("Auth FAILED")
            return 1
        print("Auth OK")
        _print_config(evse)

        did_set = False

        if args.brightness is not None:
            print(f"\nSET brightness → {args.brightness}")
            ok = await evse.set_brightness(args.brightness)
            print(f"  {'OK' if ok else 'FAILED'}")
            did_set = True

        if args.temp is not None:
            code = TEMP_UNIT_OPTIONS[args.temp]
            print(f"\nSET temperature unit → {args.temp} ({code})")
            ok = await evse.set_temperature_unit(code)
            print(f"  {'OK' if ok else 'FAILED'}")
            did_set = True

        if args.start_mode is not None:
            code = START_MODE_CLI_OPTIONS[args.start_mode]
            print(f"\nSET start mode → {args.start_mode} ({code})")
            ok = await evse.set_offline_charge(code)
            print(f"  {'OK' if ok else 'FAILED'}")
            did_set = True

        if args.language is not None:
            code = LANGUAGE_OPTIONS[args.language]
            print(f"\nSET language → {args.language} ({code})")
            ok = await evse.set_language(code)
            print(f"  {'OK' if ok else 'FAILED'}")
            did_set = True

        if args.nickname is not None:
            print(f"\nSET nickname → {args.nickname!r}")
            ok = await evse.set_name(args.nickname)
            print(f"  {'OK' if ok else 'FAILED'}")
            did_set = True

        if args.amps is not None:
            print(f"\nSET max current → {args.amps} A")
            ok = await evse.set_max_electricity(args.amps)
            print(f"  {'OK' if ok else 'FAILED'}")
            did_set = True

        if args.schedule_clear:
            print("\nSET schedule → all off")
            ok = await evse.set_schedule(empty_schedule())
            print(f"  {'OK' if ok else 'FAILED'}")
            did_set = True
        elif args.schedule is not None:
            print("\nSET schedule →")
            for day, info in schedule_to_dict(args.schedule).items():
                print(f"  {day:10s}: {info['label']}")
            ok = await evse.set_schedule(args.schedule)
            print(f"  {'OK' if ok else 'FAILED'}")
            did_set = True

        if did_set:
            _print_config(evse)
            print(
                "\nRe-open EVSEMaster and check the value on the charger."
                "\n(Keep this script/HA disconnected while the app is open.)"
            )
        else:
            print(
                "\nNo SET requested (GET only). Pass e.g. --brightness 40"
                " or --schedule all=03:00/60"
            )

        return 0
    finally:
        await comm.stop()


def main() -> int:
    p = argparse.ArgumentParser(description="Live EVSE config GET/SET (no Home Assistant)")
    p.add_argument("--port", type=int, default=28376)
    p.add_argument("--timeout", type=int, default=15, help="Discovery timeout seconds")
    p.add_argument("--serial", help="Target serial if several chargers")
    p.add_argument("--password", help="Or set EVSE_PASSWORD")
    p.add_argument("--brightness", type=int, metavar="0-100")
    p.add_argument("--temp", choices=sorted(TEMP_UNIT_OPTIONS))
    p.add_argument(
        "--start-mode",
        choices=sorted(START_MODE_CLI_OPTIONS),
        help="Use app_and_button in bash (same as app&button)",
    )
    p.add_argument("--language", choices=sorted(LANGUAGE_OPTIONS))
    p.add_argument("--nickname", type=str)
    p.add_argument("--amps", type=int, metavar="A")
    p.add_argument(
        "--schedule",
        type=_parse_schedule_arg,
        metavar="SPEC",
        help="Weekly schedule, e.g. all=03:00/60 or sat=23:59/22:58",
    )
    p.add_argument(
        "--schedule-clear",
        action="store_true",
        help="Disable all 7 schedule slots",
    )
    return asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
