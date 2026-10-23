"""Datapoint layer of Jebao MDP pumps.

The pumps carry a standard GizWits datapoint block. Gizwits publishes its layout for
product key 6a5c47b3ea364ecb841b47f5997a1775 (the key MDP-15000/MDP-3500/MDP-5000 and
newer MDP-20000 pumps report in discovery) at
https://api.gizwits.com/app/datapoint?product_key=6a5c47b3ea364ecb841b47f5997a1775

Status frames (0x0091) carry the 302-byte values area after the action byte:

    byte 0      bit 0 SwitchON, bit 1 Mode (0 front panel, 1 wireless), bit 2 FeedSwitch,
                bit 3 TimerON (schedule enabled), bits 4-5 AutoMode (schedule's current
                mode: 0 off, 1 run, 2 feed; the app writes it when turning the schedule on)
    byte 1      Motor_Speed, percent (pumps run at 30-100; 0 stops the motor)
    byte 2      FeedTime, minutes (1-60)
    byte 3      AutoGears, the schedule's current speed (written with AutoMode)
    byte 4      AutoFeedTime (unused by the app)
    bytes 5-8   YMDData [century, year, month, day]
    bytes 9-12  HMSData [0, hour, minute, second]  (the app's "Set Time"; reads back
                the last value written, it doesn't tick)
    bytes 13-300  AutoTime00..AutoTime47, 6-byte schedule slots
    byte 301    fault bits (read only)

Control frames (0x0093, action 0x01) carry 8 bytes of attribute flags, one bit per
writable datapoint, then the writable part of the values area (bytes 0-300) with
only the flagged datapoints filled in. The flags are a big-endian bit set: datapoint
id N is bit N % 8 of byte 7 - N // 8, so byte 7 holds SwitchON..Motor_Speed..FeedTime.
The opcodes python-jebao has always sent (0x01 on/off, 0x20 speed, 0x4C start feed...)
are exactly this flags byte, so the classic commands encode the same frames.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

# Product key from the discovery reply of pumps that use this layout
PRODUCT_KEY_RETURN_PUMP = "6a5c47b3ea364ecb841b47f5997a1775"

VALUES_SIZE = 302  # values area in a status frame
WRITABLE_SIZE = 301  # values area in a control frame (no fault byte)
ATTR_FLAGS_SIZE = 8  # one bit per writable datapoint (59 of them)

SCHEDULE_SLOTS = 48
SLOT_SIZE = 6
SCHEDULE_OFFSET = 13
# Slots never written read back as this filler (the pump's erased-flash value)
UNUSED_SLOT = b"\xee" * SLOT_SIZE

# Datapoint ids (bit numbers in the attribute flags)
DP_SWITCH_ON = 0
DP_MODE = 1
DP_FEED_SWITCH = 2
DP_TIMER_ON = 3
DP_AUTO_MODE = 4
DP_MOTOR_SPEED = 5
DP_FEED_TIME = 6
DP_AUTO_GEARS = 7
DP_AUTO_FEED_TIME = 8
DP_YMD = 9
DP_HMS = 10
DP_SCHEDULE_FIRST = 11  # AutoTime00; AutoTimeNN is id 11 + NN

MIN_FEED_TIME = 1
MAX_FEED_TIME = 60

FAULT_NAMES = (
    "overcurrent",
    "overvoltage",
    "overtemperature",
    "undervoltage",
    "locked_rotor",
    "no_load",
    "communication",
)


class AutoMode(IntEnum):
    """What the schedule is doing right now (and the mode of a schedule slot).

    The app calls these Stop, Auto and Feed.
    """

    OFF = 0  # pump stopped
    RUN = 1  # running at the slot's speed
    FEED = 2  # paused for feeding


@dataclass(frozen=True)
class _Field:
    """Position of one datapoint in the values area."""

    dp_id: int
    byte: int
    bit: int = 0
    bits: int = 0  # bit field width; 0 for whole-byte fields
    size: int = 1  # bytes, for whole-byte fields


_BOOL = {
    "switch_on": _Field(DP_SWITCH_ON, 0, bit=0, bits=1),
    "wireless_mode": _Field(DP_MODE, 0, bit=1, bits=1),
    "feed_switch": _Field(DP_FEED_SWITCH, 0, bit=2, bits=1),
    "timer_on": _Field(DP_TIMER_ON, 0, bit=3, bits=1),
}
_FIELDS: Dict[str, _Field] = {
    **_BOOL,
    "auto_mode": _Field(DP_AUTO_MODE, 0, bit=4, bits=2),
    "motor_speed": _Field(DP_MOTOR_SPEED, 1),
    "feed_time": _Field(DP_FEED_TIME, 2),
    "auto_gears": _Field(DP_AUTO_GEARS, 3),
    "auto_feed_time": _Field(DP_AUTO_FEED_TIME, 4),
    "ymd": _Field(DP_YMD, 5, size=4),
    "hms": _Field(DP_HMS, 9, size=4),
}
for _n in range(SCHEDULE_SLOTS):
    _FIELDS[f"slot{_n:02d}"] = _Field(
        DP_SCHEDULE_FIRST + _n, SCHEDULE_OFFSET + SLOT_SIZE * _n, size=SLOT_SIZE
    )


@dataclass(frozen=True)
class ScheduleSlot:
    """One schedule entry: from start to end the pump runs, pauses for feeding, or stops.

    value is the speed (30-100) for RUN and 0 otherwise. The schedule repeats daily;
    a slot whose end is before its start runs past midnight, and an end of 00:00 or
    24:00 means the end of the day.
    """

    start_hour: int
    start_minute: int
    end_hour: int
    end_minute: int
    mode: AutoMode
    value: int = 0

    def __post_init__(self) -> None:
        problem = self.problem()
        if problem:
            raise ValueError(f"Invalid schedule slot {self}: {problem}")

    def problem(self) -> Optional[str]:
        """Why this slot isn't valid, or None."""
        if not (0 <= self.start_hour < 24 and 0 <= self.start_minute < 60):
            return "start must be 00:00-23:59"
        if not (0 <= self.end_hour <= 24 and 0 <= self.end_minute < 60):
            return "end must be 00:00-24:00"
        if self.end_hour == 24 and self.end_minute:
            return "end must be 00:00-24:00"
        if self.mode not in tuple(AutoMode):
            return f"unknown mode {self.mode}"
        if self.mode == AutoMode.RUN and not 0 <= self.value <= 100:
            return "speed must be 0-100"
        if self.mode != AutoMode.RUN and self.value:
            return "only run slots have a speed"
        return None

    def validate_for_write(self) -> None:
        """Stricter checks the app applies to slots it saves."""
        if (self.start_hour, self.start_minute) == (self.end_hour % 24, self.end_minute):
            raise ValueError(f"Schedule slot {self.start}-{self.end} has no length")
        if self.mode == AutoMode.RUN and not 30 <= self.value <= 100:
            raise ValueError(f"Schedule speed must be 30-100, got {self.value}")

    def _minutes(self) -> Tuple[int, int]:
        start = self.start_hour * 60 + self.start_minute
        end = self.end_hour * 60 + self.end_minute
        return start, end or 24 * 60  # an end of 00:00 is the end of the day

    def covers(self, hour: int, minute: int) -> bool:
        """Whether the slot is active at hour:minute (as the app works it out)."""
        start, end = self._minutes()
        now = hour * 60 + minute
        if start < end:
            return start <= now < end
        return now >= start or now < end  # runs past midnight

    @property
    def start(self) -> str:
        return f"{self.start_hour:02d}:{self.start_minute:02d}"

    @property
    def end(self) -> str:
        return f"{self.end_hour:02d}:{self.end_minute:02d}"

    def to_bytes(self) -> bytes:
        return bytes(
            (self.start_hour, self.start_minute, self.end_hour, self.end_minute,
             int(self.mode), self.value)
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> Optional["ScheduleSlot"]:
        """Decode a slot; None if it's unused (0xEE filler) or doesn't make sense.

        Pumps don't clear slots, so besides the filler they can hold leftovers that
        were never a schedule entry.
        """
        if len(data) != SLOT_SIZE or data == UNUSED_SLOT or data[4] not in (0, 1, 2):
            return None
        try:
            return cls(data[0], data[1], data[2], data[3], AutoMode(data[4]), data[5])
        except ValueError:
            return None

    def as_dict(self) -> dict:
        slot = {"start": self.start, "end": self.end, "mode": self.mode.name.lower()}
        if self.mode == AutoMode.RUN:
            slot["speed"] = self.value
        return slot


@dataclass(frozen=True)
class PumpStatus:
    """Decoded status (values area of a 0x0091 frame)."""

    switch_on: bool
    wireless_mode: bool
    feed_switch: bool
    timer_on: bool
    auto_mode: Optional[AutoMode]
    motor_speed: int
    feed_time: int
    auto_gears: int
    auto_feed_time: int
    clock: Optional[datetime]
    schedule: Tuple[Optional[ScheduleSlot], ...]
    faults: Tuple[str, ...]
    raw: bytes = field(repr=False, default=b"")

    @property
    def schedule_slots(self) -> List[ScheduleSlot]:
        """The slots that hold a valid entry, in slot order."""
        return [slot for slot in self.schedule if slot is not None]

    @classmethod
    def from_values(cls, values: bytes) -> "PumpStatus":
        if len(values) < VALUES_SIZE:
            raise ValueError(f"Status values too short: {len(values)} bytes")

        def read(name: str) -> Union[int, bytes]:
            f = _FIELDS[name]
            if f.bits:
                return (values[f.byte] >> f.bit) & ((1 << f.bits) - 1)
            if f.size == 1:
                return values[f.byte]
            return bytes(values[f.byte : f.byte + f.size])

        auto_mode = read("auto_mode")
        fault_bits = values[VALUES_SIZE - 1]
        return cls(
            switch_on=bool(read("switch_on")),
            wireless_mode=bool(read("wireless_mode")),
            feed_switch=bool(read("feed_switch")),
            timer_on=bool(read("timer_on")),
            auto_mode=AutoMode(auto_mode) if auto_mode in tuple(AutoMode) else None,
            motor_speed=read("motor_speed"),
            feed_time=read("feed_time"),
            auto_gears=read("auto_gears"),
            auto_feed_time=read("auto_feed_time"),
            clock=decode_clock(read("ymd"), read("hms")),
            schedule=tuple(
                ScheduleSlot.from_bytes(read(f"slot{n:02d}")) for n in range(SCHEDULE_SLOTS)
            ),
            faults=tuple(
                name for bit, name in enumerate(FAULT_NAMES) if fault_bits & (1 << bit)
            ),
            raw=bytes(values[:VALUES_SIZE]),
        )


def decode_clock(ymd: bytes, hms: bytes) -> Optional[datetime]:
    """YMDData [century, year, month, day] + HMSData [0, h, m, s], plain binary."""
    try:
        return datetime(ymd[0] * 100 + ymd[1], ymd[2], ymd[3], hms[1], hms[2], hms[3])
    except ValueError:
        return None


def encode_clock(when: datetime) -> Dict[str, bytes]:
    return {
        "ymd": bytes((when.year // 100, when.year % 100, when.month, when.day)),
        "hms": bytes((0, when.hour, when.minute, when.second)),
    }


def active_slot(slots: Iterable[Optional[ScheduleSlot]], when: datetime) -> Optional[ScheduleSlot]:
    """The first slot that covers `when`, or None."""
    for slot in slots:
        if slot is not None and slot.covers(when.hour, when.minute):
            return slot
    return None


def schedule_state(slots: Iterable[Optional[ScheduleSlot]], when: datetime) -> Dict[str, int]:
    """AutoMode/AutoGears for the slot active at `when`, as the app writes them when it
    turns the schedule on (AutoMode off when no slot covers the time)."""
    slot = active_slot(slots, when)
    if slot is None:
        return {"auto_mode": int(AutoMode.OFF)}
    return {"auto_mode": int(slot.mode), "auto_gears": slot.value}


def schedule_values(slots: Sequence[ScheduleSlot]) -> Dict[str, bytes]:
    """All 48 slot datapoints for a schedule: the given slots, then unused filler.

    Raises:
        ValueError: too many slots, a slot the app wouldn't save, or overlapping slots
    """
    if len(slots) > SCHEDULE_SLOTS:
        raise ValueError(f"A schedule has at most {SCHEDULE_SLOTS} slots, got {len(slots)}")
    for slot in slots:
        slot.validate_for_write()
    for i, a in enumerate(slots):
        for b in slots[i + 1 :]:
            if a.covers(b.start_hour, b.start_minute) or b.covers(a.start_hour, a.start_minute):
                raise ValueError(f"Schedule slots {a.start}-{a.end} and {b.start}-{b.end} overlap")
    values = {f"slot{n:02d}": slot.to_bytes() for n, slot in enumerate(slots)}
    for n in range(len(slots), SCHEDULE_SLOTS):
        values[f"slot{n:02d}"] = UNUSED_SLOT
    return values


def encode_write(changes: Mapping[str, Union[bool, int, bytes]]) -> bytes:
    """Attribute flags + values for a control frame that sets only `changes`.

    Keys are switch_on, wireless_mode, feed_switch, timer_on, auto_mode, motor_speed,
    feed_time, auto_gears, auto_feed_time, ymd, hms, slot00..slot47.
    """
    if not changes:
        raise ValueError("Nothing to write")
    flags = bytearray(ATTR_FLAGS_SIZE)
    values = bytearray(WRITABLE_SIZE)
    for name, value in changes.items():
        f = _FIELDS.get(name)
        if f is None:
            raise KeyError(f"Unknown or read-only datapoint: {name}")
        flags[ATTR_FLAGS_SIZE - 1 - f.dp_id // 8] |= 1 << (f.dp_id % 8)
        if f.bits:
            value = int(value)
            if not 0 <= value < (1 << f.bits):
                raise ValueError(f"{name} out of range: {value}")
            values[f.byte] |= value << f.bit
        elif f.size == 1:
            value = int(value)
            if not 0 <= value <= 0xFF:
                raise ValueError(f"{name} out of range: {value}")
            values[f.byte] = value
        else:
            value = bytes(value)
            if len(value) != f.size:
                raise ValueError(f"{name} must be {f.size} bytes, got {len(value)}")
            values[f.byte : f.byte + f.size] = value
    return bytes(flags + values)


def parse_slots(slots: Iterable[Mapping]) -> List[ScheduleSlot]:
    """ScheduleSlots from dicts like {"start": "08:00", "end": "20:00", "mode": "run",
    "speed": 70} (the as_dict() form; mode is run, feed or off, default run)."""
    result = []
    for item in slots:
        mode = item.get("mode", "run")
        mode = AutoMode[mode.upper()] if isinstance(mode, str) else AutoMode(mode)
        start_h, start_m = _parse_hhmm(item["start"])
        end_h, end_m = _parse_hhmm(item["end"])
        value = int(item["speed"]) if mode == AutoMode.RUN else 0
        result.append(ScheduleSlot(start_h, start_m, end_h, end_m, mode, value))
    return result


def _parse_hhmm(value) -> Tuple[int, int]:
    if hasattr(value, "hour"):  # datetime.time
        return value.hour, value.minute
    hour, _, minute = str(value).partition(":")
    return int(hour), int(minute[:2] or 0)
