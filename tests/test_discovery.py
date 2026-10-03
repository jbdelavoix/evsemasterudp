#!/usr/bin/env python3
"""Discover EVSE chargers via UDP broadcast (port 28376)."""

from __future__ import annotations

import argparse
import asyncio
import os
import select as _stdlib_select
import sys

# Pin stdlib select before adding the integration path (HA platform select.py).
sys.modules["select"] = _stdlib_select

test_dir = os.path.dirname(__file__)
project_root = os.path.dirname(test_dir)
evse_module_path = os.path.join(project_root, "custom_components", "evsemasterudp")
sys.path.insert(0, evse_module_path)


async def run_discovery(timeout: float) -> int:
    from protocol.communicator import Communicator

    print(f"Listening for EVSE broadcasts on UDP 28376 (timeout={timeout:.0f}s)...")
    print("Close the EVSE Master app first if discovery stays empty.")
    print()

    communicator = Communicator()
    port = await communicator.start()
    print(f"Local UDP port: {port}")
    print()

    seen: set[str] = set()
    deadline = asyncio.get_event_loop().time() + timeout

    try:
        while asyncio.get_event_loop().time() < deadline:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(1.0, remaining))

            for device_id, info in list(communicator.discovered_devices.items()):
                if device_id in seen:
                    continue
                seen.add(device_id)
                print(f"IP        : {info.get('ip')}")
                print(f"Port      : {info.get('port')}")
                print(f"Serial    : {device_id}")
                print(f"Brand     : {info.get('brand')}")
                print(f"Model     : {info.get('model')}")
                print(f"Type      : {info.get('type')}")
                print()
    finally:
        await communicator.stop()

    if not seen:
        print("No EVSE discovered.")
        print("Tips: same LAN/VLAN as the charger, UDP 28376 not blocked, EVSE Master app closed.")
        return 1

    print(f"Discovered {len(seen)} device(s).")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover EVSE chargers via UDP broadcast")
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Seconds to listen for broadcasts (default: 15)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run_discovery(args.timeout)))


if __name__ == "__main__":
    main()
