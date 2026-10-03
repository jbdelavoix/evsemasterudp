#!/usr/bin/env python3
"""
Full live EVSE test + UDP payload capture.

Usage:
    python tests/test_full.py
    python tests/test_full.py --timeout 30 --listen 20
    EVSE_PASSWORD=xxxxxx python tests/test_full.py

Writes a JSONL dump under tests/captures/ (raw hex + decoded fields)
for protocol analysis and Home Assistant entity decisions.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import select as _stdlib_select
import sys
from datetime import datetime, timezone
from pathlib import Path

# Pin stdlib select before adding the integration path (HA platform select.py).
sys.modules["select"] = _stdlib_select

test_dir = Path(__file__).resolve().parent
project_root = test_dir.parent
evse_module_path = project_root / "custom_components" / "evsemasterudp"
sys.path.insert(0, str(evse_module_path))

CAPTURE_DIR = test_dir / "captures"


def _json_default(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.hex()
    if isinstance(obj, set):
        return list(obj)
    return str(obj)


def datagram_to_dict(datagram) -> dict:
    """Serialize a parsed datagram (public attrs + command)."""
    data = {
        "class": datagram.__class__.__name__,
        "command": datagram.get_command(),
        "command_hex": f"0x{datagram.get_command():04x}",
        "serial": datagram.get_device_serial(),
    }
    skip = {
        "COMMAND",
        "PACKET_HEADER",
        "PACKET_TAIL",
        "key_type",
        "device_serial",
        "device_password",
    }
    for key, value in vars(datagram).items():
        if key.startswith("_") or key in skip:
            continue
        data[key] = value
    return data


class PayloadCapture:
    """Intercepts every inbound datagram and writes JSONL + console summary."""

    def __init__(self, out_path: Path):
        self.out_path = out_path
        self.counts: dict[str, int] = {}
        self.samples: dict[str, dict] = {}
        self._file = out_path.open("w", encoding="utf-8")
        self._orig_process = None

    def install(self, communicator) -> None:
        self._orig_handle = communicator._handle_message
        self._orig_process = communicator._process_datagram
        self._pending_raw: bytes | None = None

        async def _wrapped_handle(data: bytes, addr):
            self._pending_raw = data
            await self._orig_handle(data, addr)
            self._pending_raw = None

        async def _wrapped_process(datagram, addr):
            await self._record(datagram, addr, self._pending_raw)
            await self._orig_process(datagram, addr)

        communicator._handle_message = _wrapped_handle
        communicator._process_datagram = _wrapped_process

    async def _record(self, datagram, addr, raw: bytes | None = None) -> None:
        name = datagram.__class__.__name__
        self.counts[name] = self.counts.get(name, 0) + 1
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "addr": {"ip": addr[0], "port": addr[1]},
            "datagram": datagram_to_dict(datagram),
        }
        if raw:
            entry["wire_hex"] = raw.hex()
            entry["wire_len"] = len(raw)
        elif hasattr(datagram, "raw_data") and datagram.raw_data:
            entry["payload_hex"] = datagram.raw_data.hex()

        self._file.write(json.dumps(entry, default=_json_default) + "\n")
        self._file.flush()

        if name not in self.samples:
            self.samples[name] = entry["datagram"]
            print(f"   First {name} (cmd {entry['datagram']['command_hex']})")
            interesting = {
                k: v
                for k, v in entry["datagram"].items()
                if k not in ("class", "command", "command_hex", "serial")
                and v not in (None, "", 0, 0.0, [], {})
            }
            if interesting:
                print(f"      → {interesting}")
            if raw:
                print(f"      wire[{len(raw)}]: {raw.hex()[:64]}...")

    def close(self) -> None:
        self._file.close()

    def print_summary(self) -> None:
        print("\nCapture summary")
        print(f"   File: {self.out_path}")
        if not self.counts:
            print("   (no datagrams captured)")
            return
        for name, count in sorted(self.counts.items(), key=lambda x: (-x[1], x[0])):
            print(f"   • {name}: {count}x")


def print_evse_snapshot(evse) -> None:
    print("\nEVSE snapshot")
    info = evse.info
    print(f"   serial={info.serial} ip={info.ip}:{info.port}")
    print(f"   brand={info.brand!r} model={info.model!r}")
    print(f"   hw={info.hardware_version!r} sw={info.software_version!r}")
    print(f"   max_power={info.max_power}W max_amps={info.max_electricity}A phases={info.phases}")
    print(f"   online={evse.is_online()} logged_in={evse.is_logged_in()} meta={evse.get_meta_state()}")

    if evse.state:
        s = evse.state
        print("   --- state ---")
        print(f"   power={s.current_power}W total_kwh={s.current_amount}")
        print(
            f"   V=[{s.l1_voltage}, {s.l2_voltage}, {s.l3_voltage}] "
            f"A=[{s.l1_electricity}, {s.l2_electricity}, {s.l3_electricity}]"
        )
        print(
            f"   temp_in={s.inner_temp} temp_out={s.outer_temp} "
            f"gun={s.gun_state} output={s.output_state} "
            f"current_state={s.current_state} errors={s.errors} "
            f"emergency={getattr(s, 'emergency_btn_state', 'N/A')}"
        )
    else:
        print("   (no SingleACStatus received)")

    if evse.current_charge:
        c = evse.current_charge
        print("   --- session ---")
        print(
            f"   id={c.charge_id!r} state={c.current_state} "
            f"kwh={c.charge_kwh} duration={c.duration_seconds}s "
            f"max_A={c.max_electricity} fee={c.charge_fee}"
        )
    else:
        print("   (no session / charge status)")


async def run(args: argparse.Namespace) -> bool:
    from protocol.communicator import Communicator
    from protocol.datagrams import (
        GetVersion,
        SetAndGetOutputElectricity,
        SetAndGetSystemTime,
    )

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    capture_path = CAPTURE_DIR / f"evse_capture_{stamp}.jsonl"

    print("EVSE full test + payload capture")
    print(f"  Capture → {capture_path}")
    print("  Tip: close the EVSE Master app while testing\n")

    comm = Communicator(port=args.port)
    capture = PayloadCapture(capture_path)
    capture.install(comm)

    await comm.start()
    print(f"  Listening UDP :{args.port}")

    print(f"  Discovery ({args.timeout}s)...")
    evse = None
    for i in range(args.timeout):
        await asyncio.sleep(1)
        if comm.evses:
            evse = list(comm.evses.values())[0]
            print(f"  Found: {evse.info.serial} @ {evse.info.ip}")
            break
        print(f"  ... {i + 1}/{args.timeout}s", end="\r")
    print()

    if not evse:
        print("No EVSE discovered")
        capture.print_summary()
        capture.close()
        await comm.stop()
        return False

    password = args.password or os.environ.get("EVSE_PASSWORD")
    if not password:
        password = getpass.getpass("EVSE password: ")

    print("Authenticating...")
    auth_ok = await evse.login(password)
    print(f"  Auth: {'OK' if auth_ok else 'FAILED'}")

    if auth_ok:
        # Probe extra protocol commands to populate capture + state
        print("Follow-up requests (version, current, sync time)...")
        try:
            ver = GetVersion()
            ver.set_device_serial(evse.info.serial)
            ver.set_device_password(password)
            await evse.send_datagram(ver)
        except Exception as err:
            print(f"  GetVersion: {err}")

        try:
            get_amps = SetAndGetOutputElectricity()
            get_amps.set_device_serial(evse.info.serial)
            get_amps.set_device_password(password)
            get_amps.action = 0  # GET
            await evse.send_datagram(get_amps)
        except Exception as err:
            print(f"  GetOutputElectricity: {err}")

        try:
            sync = SetAndGetSystemTime()
            sync.set_device_serial(evse.info.serial)
            sync.set_device_password(password)
            await evse.send_datagram(sync)
        except Exception as err:
            print(f"  SetSystemTime: {err}")

    print(f"Listening {args.listen}s (status / charge / keepalive)...")
    for i in range(args.listen):
        await asyncio.sleep(1)
        print(f"  ... {i + 1}/{args.listen}s", end="\r")
    print()

    print_evse_snapshot(evse)
    capture.print_summary()
    capture.close()
    await comm.stop()

    # Suggest HA entities based on what we actually received
    print("\nSuggested HA entities (from this capture)")
    suggestions = []
    if "SingleACStatus" in capture.counts:
        suggestions += [
            "binary_sensor: vehicle connected (gun_state)",
            "binary_sensor: charging (output_state)",
            "binary_sensor: error (errors[])",
            "sensor: L2/L3 voltage/current if non-zero",
            "sensor: session duration",
            "sensor: emergency button",
        ]
    if "SingleACChargingStatusPublicAuto" in capture.counts:
        suggestions += [
            "sensor: session energy / duration / charge_id",
            "sensor: charge fee / price",
        ]
    if "GetVersionResponse" in capture.counts:
        suggestions.append("device sw/hw version from GetVersionResponse")
    if "SetAndGetOutputElectricityResponse" in capture.counts:
        suggestions.append("number max current already present — confirm GET at login")
    if not suggestions:
        suggestions.append("Re-run with --listen 30 and the charger powered on")
    for s in suggestions:
        print(f"   • {s}")

    print(f"\nOpen the dump to inspect real fields:\n  {capture_path}")
    return auth_ok and bool(evse.state)


def main() -> int:
    parser = argparse.ArgumentParser(description="EVSE full test + payload capture")
    parser.add_argument("--port", type=int, default=28376)
    parser.add_argument("--timeout", type=int, default=15, help="Discovery wait (s)")
    parser.add_argument("--listen", type=int, default=20, help="Listen after login (s)")
    parser.add_argument("--password", default=None, help="Or set EVSE_PASSWORD")
    args = parser.parse_args()
    ok = asyncio.run(run(args))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
