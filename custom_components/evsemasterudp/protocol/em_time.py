"""EVSE system-time helpers (EmProto / EVSEMaster clock).

The charger keeps wall-clock time as if it were in Asia/Shanghai (UTC+8,
no DST). The official app therefore sends a shifted Unix timestamp so that
``fromtimestamp(ts, Asia/Shanghai)`` equals the user's local wall clock.

Captured SET ``0x8101`` payload: ``action(1) + ts_be + zero pad → 16 bytes``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

# China Standard Time — EVSE firmware reference zone (no DST).
EM_TZ = timezone(timedelta(hours=8))


def date_to_em_timestamp(dt: Optional[datetime] = None) -> int:
    """Encode local wall clock for the EVSE Shanghai-based clock."""
    if dt is None:
        dt = datetime.now().astimezone()
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    else:
        dt = dt.astimezone()

    # Same Y-M-D h:m:s digits, interpreted in China TZ.
    china_wall = datetime(
        dt.year,
        dt.month,
        dt.day,
        dt.hour,
        dt.minute,
        dt.second,
        tzinfo=EM_TZ,
    )
    return int(china_wall.timestamp())


def em_timestamp_to_local(ts: int) -> datetime:
    """Decode an EmProto timestamp into a timezone-aware local datetime."""
    china = datetime.fromtimestamp(ts, EM_TZ)
    local_tz = datetime.now().astimezone().tzinfo
    return datetime(
        china.year,
        china.month,
        china.day,
        china.hour,
        china.minute,
        china.second,
        tzinfo=local_tz,
    )
