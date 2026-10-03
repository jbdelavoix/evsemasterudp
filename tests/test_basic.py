#!/usr/bin/env python3
"""Offline smoke tests for the EVSE protocol stack (no hardware required)."""

from __future__ import annotations

import asyncio
import os
import select as _stdlib_select
import socket
import sys

# Pin stdlib select before adding the integration path (HA platform select.py).
sys.modules["select"] = _stdlib_select

test_dir = os.path.dirname(__file__)
project_root = os.path.dirname(test_dir)
evse_module_path = os.path.join(project_root, "custom_components", "evsemasterudp")
sys.path.insert(0, evse_module_path)


async def test_basic_import():
    print("  Importing modules...")
    from protocol.datagram import Datagram  # noqa: F401
    from protocol.communicator import Communicator  # noqa: F401
    from protocol.datagrams import RequestLogin, Heading, SingleACStatus  # noqa: F401
    print("  OK")
    return True, None


async def test_datagram_creation():
    print("  Creating datagrams...")
    from protocol.datagrams import RequestLogin, Heading

    login = RequestLogin()
    print(f"  RequestLogin command=0x{login.COMMAND:04x}")
    heading = Heading()
    print(f"  Heading command=0x{heading.COMMAND:04x}")
    return True, None


async def test_datagram_packing():
    print("  Packing datagram...")
    from protocol.datagrams import RequestLogin

    login = RequestLogin()
    login.serial = "1368844619649410"
    login.password = "123456"
    packed = login.pack()
    print(f"  Encoded {len(packed)} bytes: {packed.hex()}")
    return True, None


async def test_communicator_creation():
    print("  Creating communicator...")
    from protocol.communicator import Communicator

    comm = Communicator()
    print(f"  Port default={comm.port}")
    return True, None


async def test_network_socket():
    print("  Creating UDP socket...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    sock.close()
    print(f"  Bound ephemeral port {port}")
    return True, None


async def main() -> int:
    print("=== EVSE protocol smoke tests ===\n")

    tests = [
        ("Module imports", test_basic_import),
        ("Datagram creation", test_datagram_creation),
        ("Datagram packing", test_datagram_packing),
        ("Communicator", test_communicator_creation),
        ("UDP socket", test_network_socket),
    ]

    results = []
    for name, fn in tests:
        print(f"{name}...")
        try:
            ok, err = await fn()
            if ok:
                print(f"  PASS\n")
                results.append((name, True, None))
            else:
                print(f"  FAIL: {err}\n")
                results.append((name, False, err))
        except Exception as exc:
            print(f"  FAIL: {exc}\n")
            results.append((name, False, str(exc)))

    print("=" * 40)
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, err in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {err}" if err else ""))

    print(f"\n{passed}/{len(tests)} passed")
    if passed == len(tests):
        print("Next: python tests/test_discovery.py")
        return 0

    print("Fix the failures above before running against hardware.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
