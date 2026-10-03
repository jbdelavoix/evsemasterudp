"""Weekly charge schedule (AlarmChargeStrategy 0x810e / 0x010e).

Wire format per day slot (9 bytes), from live captures::

    mode  hour  minute  duration_min_be16  flags[4]

- ``mode`` ``1`` = off, ``3`` = weekly on
- ``duration_min`` is a big-endian uint16 (e.g. 22h58 = 1378 = ``0x0562``)
- ``flags`` usually ``ffffffff``
- One-shot / app « unique » is **not** supported (broken in app; not this datagram)

Example (Tue/Thu/Sat start 23:59 lasting 22h58, weekly)::

    03 17 3b 05 62 ff ff ff ff
"""
from __future__ import annotations

from dataclasses import dataclass, field

WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

SLOT_LEN = 9
SLOT_COUNT = 7
MODE_OFF = 1
MODE_REPEAT = 3
MAX_DURATION_MIN = 24 * 60 - 1  # 23h59


@dataclass
class ScheduleSlot:
    mode: int = MODE_OFF
    hour: int = 0
    minute: int = 0
    duration_min: int = 0
    flags: bytes = field(default_factory=lambda: b"\xff\xff\xff\xff")

    @property
    def enabled(self) -> bool:
        # mode 2 also appears "on" on the wire but behaves like weekly; treat >=3 or
        # any non-off with duration as enabled for reads.
        return self.mode != MODE_OFF and self.duration_min > 0

    @property
    def end_hour(self) -> int:
        """Wall-clock end hour (may wrap past midnight)."""
        total = self.hour * 60 + self.minute + self.duration_min
        return (total // 60) % 24

    @property
    def end_minute(self) -> int:
        total = self.hour * 60 + self.minute + self.duration_min
        return total % 60

    @staticmethod
    def format_duration(minutes: int) -> str:
        if minutes < 60:
            return f"{minutes}m"
        hours, mins = divmod(minutes, 60)
        return f"{hours}h{mins:02d}m" if mins else f"{hours}h"

    @property
    def label(self) -> str:
        if not self.enabled:
            return "off"
        return (
            f"{self.hour:02d}:{self.minute:02d}"
            f"→{self.end_hour:02d}:{self.end_minute:02d}"
            f"/{self.format_duration(self.duration_min)}"
        )

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "hour": self.hour,
            "minute": self.minute,
            "duration_min": self.duration_min if self.enabled else None,
            "end_hour": self.end_hour if self.enabled else None,
            "end_minute": self.end_minute if self.enabled else None,
            "enabled": self.enabled,
            "label": self.label,
        }

    def pack(self) -> bytes:
        flags = (self.flags or b"\xff\xff\xff\xff")[:4]
        if len(flags) < 4:
            flags = flags + b"\xff" * (4 - len(flags))
        if not self.enabled:
            return bytes([MODE_OFF, 0, 0, 0, 0]) + flags
        dur = max(0, min(0xFFFF, int(self.duration_min)))
        return (
            bytes(
                [
                    MODE_REPEAT,
                    self.hour & 0xFF,
                    self.minute & 0xFF,
                    (dur >> 8) & 0xFF,
                    dur & 0xFF,
                ]
            )
            + flags
        )

    @classmethod
    def unpack(cls, raw: bytes) -> ScheduleSlot:
        if len(raw) < SLOT_LEN:
            raw = raw + b"\x00" * (SLOT_LEN - len(raw))
        mode = raw[0]
        hour = raw[1]
        minute = raw[2]
        duration_min = (raw[3] << 8) | raw[4]
        return cls(
            mode=mode,
            hour=hour,
            minute=minute,
            duration_min=duration_min,
            flags=raw[5:9],
        )

    @classmethod
    def disabled(cls) -> ScheduleSlot:
        return cls(mode=MODE_OFF, hour=0, minute=0, duration_min=0, flags=b"\xff\xff\xff\xff")

    @classmethod
    def active(cls, hour: int, minute: int, duration_min: int) -> ScheduleSlot:
        return cls(
            mode=MODE_REPEAT,
            hour=max(0, min(23, int(hour))),
            minute=max(0, min(59, int(minute))),
            duration_min=max(0, min(MAX_DURATION_MIN, int(duration_min))),
            flags=b"\xff\xff\xff\xff",
        )


def empty_schedule() -> list[ScheduleSlot]:
    return [ScheduleSlot.disabled() for _ in range(SLOT_COUNT)]


def unpack_schedule_payload(buffer: bytes) -> tuple[int, list[ScheduleSlot]]:
    """Return (action, slots) from a 0x810e/0x010e payload."""
    if not buffer:
        return 0, empty_schedule()
    action = buffer[0]
    slots: list[ScheduleSlot] = []
    rest = buffer[1:]
    for i in range(SLOT_COUNT):
        off = i * SLOT_LEN
        chunk = rest[off : off + SLOT_LEN]
        if len(chunk) < SLOT_LEN:
            slots.append(ScheduleSlot.disabled())
        else:
            slots.append(ScheduleSlot.unpack(chunk))
    return action, slots


def pack_schedule_payload(action: int, slots: list[ScheduleSlot]) -> bytes:
    padded = list(slots[:SLOT_COUNT])
    while len(padded) < SLOT_COUNT:
        padded.append(ScheduleSlot.disabled())
    body = bytearray([action & 0xFF])
    for slot in padded:
        body.extend(slot.pack())
    return bytes(body)


def schedule_to_dict(slots: list[ScheduleSlot]) -> dict[str, dict]:
    return {day: slots[i].to_dict() for i, day in enumerate(WEEKDAYS)}
