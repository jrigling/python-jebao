"""ECP-150M wave pump status decoding (read-only).

The layout follows the data points Gizwits publishes for the ECP-150M product
key (https://euapi.gizwits.com/app/datapoint?product_key=1d8c63eaccac4205b92c84d77d5a08fb)
and was checked against status reads (message 0x93 with action 0x02, answered
by 0x94 with action 0x03) on two pumps, see
https://github.com/jrigling/python-jebao/issues/5. This module only decodes
status bytes; it sends nothing to the pump.

Status (401 bytes after the 0x94 header, sn and action):
- bytes 0-1: big-endian flag word
- bytes 2-7: flow, frequency, feed time, and the same three for the current segment
- bytes 8-391: 48 program segments (AutoTime00-47), 8 bytes each, 0xEE when unused
- bytes 392-399: date and time (YMDData, HMSData)
- byte 400: fault bits
"""

import datetime
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Optional

from .exceptions import JebaoCommandError

STATUS_SIZE = 401
SEGMENTS_OFFSET = 8
SEGMENT_SIZE = 8
SEGMENT_COUNT = 48
DATE_OFFSET = 392
TIME_OFFSET = 396
FAULTS_OFFSET = 400
UNUSED_SEGMENT = 0xEE

# Flag word (bytes 0-1), bits numbered from the least significant bit
FLAG_SWITCH = 0x0001  # SwitchON
FLAG_PULSE_TIDE = 0x0002  # PulseTide
FLAG_FEED = 0x0004  # FeedSwitch
FLAG_TIMER = 0x0008  # TimerON, Program mode
FLAG_AUTO_PULSE_TIDE = 0x0010  # AutoPulseTide, pulse tide of the current segment
MODE_SHIFT = 5  # Mode, 2 bits
LINKAGE_SHIFT = 7  # Linkage, 2 bits
AUTO_MODE_SHIFT = 9  # AutoMode, 3 bits


class Mode(IntEnum):
    """Wave mode (flag word bits 5-6)."""

    CLASSIC = 0
    SINE = 1
    RANDOM = 2
    CONSTANT = 3


class Linkage(IntEnum):
    """Linkage role (flag word bits 7-8)."""

    INDEPENDENT = 0
    MASTER = 1
    SLAVE = 2


class AutoMode(IntEnum):
    """Mode of a program segment (flag word bits 9-11 for the current one)."""

    STOP = 0
    CLASSIC = 1
    SINE = 2
    RANDOM = 3
    CONSTANT = 4
    FEED = 5


class Fault(IntFlag):
    """Fault bits (byte 400)."""

    OVERCURRENT = 0x01
    OVERVOLTAGE = 0x02
    OVER_TEMPERATURE = 0x04
    UNDERVOLTAGE = 0x08
    LOCKED_ROTOR = 0x10
    NO_LOAD = 0x20
    UART = 0x40


def _auto_mode(value: int) -> Optional[AutoMode]:
    return AutoMode(value) if value <= AutoMode.FEED else None


@dataclass(frozen=True)
class ECP150MSegment:
    """One stored program segment (AutoTimeNN).

    The schema only gives each segment as 8 binary bytes; the meaning of each
    byte below comes from the captures.
    """

    index: int  # 0-47, the NN in AutoTimeNN
    start_hour: int
    start_minute: int
    end_hour: int
    end_minute: int
    mode: int
    flow: int
    frequency: int
    flag: int  # Meaning unknown

    @property
    def auto_mode(self) -> Optional[AutoMode]:
        """Get segment mode, or None for a value outside the schema."""
        return _auto_mode(self.mode)


@dataclass(frozen=True)
class ECP150MStatus:
    """Decoded ECP-150M status."""

    flags: int
    flow: int
    frequency: int
    feed_time: int
    auto_flow: int
    auto_frequency: int
    auto_feed_time: int
    segments: tuple[ECP150MSegment, ...]
    faults: Fault
    raw: bytes

    @classmethod
    def from_bytes(cls, status: bytes) -> "ECP150MStatus":
        """Decode the status bytes that follow the 0x94 header, sn and action.

        Args:
            status: Status bytes (401)

        Returns:
            Decoded status

        Raises:
            JebaoCommandError: Status has the wrong length
        """
        if len(status) != STATUS_SIZE:
            raise JebaoCommandError(
                f"ECP-150M status is {len(status)} bytes, expected {STATUS_SIZE}"
            )

        segments = []
        for index in range(SEGMENT_COUNT):
            offset = SEGMENTS_OFFSET + index * SEGMENT_SIZE
            record = status[offset : offset + SEGMENT_SIZE]
            if record[0] == UNUSED_SEGMENT:
                continue
            segments.append(ECP150MSegment(index, *record))

        return cls(
            flags=int.from_bytes(status[0:2], "big"),
            flow=status[2],
            frequency=status[3],
            feed_time=status[4],
            auto_flow=status[5],
            auto_frequency=status[6],
            auto_feed_time=status[7],
            segments=tuple(segments),
            faults=Fault(status[FAULTS_OFFSET] & 0x7F),
            raw=bytes(status),
        )

    @property
    def is_on(self) -> bool:
        """Check if pump is switched on."""
        return bool(self.flags & FLAG_SWITCH)

    @property
    def is_pulse_tide(self) -> bool:
        """Check if pulse tide is on."""
        return bool(self.flags & FLAG_PULSE_TIDE)

    @property
    def is_feed_active(self) -> bool:
        """Check if feed mode is active."""
        return bool(self.flags & FLAG_FEED)

    @property
    def is_program_mode(self) -> bool:
        """Check if Program mode (the timer) is on."""
        return bool(self.flags & FLAG_TIMER)

    @property
    def is_auto_pulse_tide(self) -> bool:
        """Check if pulse tide is on for the current segment."""
        return bool(self.flags & FLAG_AUTO_PULSE_TIDE)

    @property
    def mode(self) -> Mode:
        """Get wave mode."""
        return Mode((self.flags >> MODE_SHIFT) & 0x03)

    @property
    def linkage(self) -> Optional[Linkage]:
        """Get Linkage role, or None for a value outside the schema."""
        value = (self.flags >> LINKAGE_SHIFT) & 0x03
        return Linkage(value) if value <= Linkage.SLAVE else None

    @property
    def auto_mode(self) -> Optional[AutoMode]:
        """Get mode of the current program segment, or None outside the schema."""
        return _auto_mode((self.flags >> AUTO_MODE_SHIFT) & 0x07)

    @property
    def date(self) -> Optional[datetime.date]:
        """Get YMDData as a date.

        The schema gives 4 binary bytes; reading them as century, year, month
        and day is inferred from the captures. None if they are not a valid date.
        """
        century, year, month, day = self.raw[DATE_OFFSET : DATE_OFFSET + 4]
        try:
            return datetime.date(century * 100 + year, month, day)
        except ValueError:
            return None

    @property
    def time(self) -> Optional[datetime.time]:
        """Get HMSData as a time.

        The schema gives 4 binary bytes; reading bytes 1-3 as hour, minute and
        second is inferred from the captures. Byte 0 has read 0 so far, so it
        may be the weekday (0 = Sunday) or padding, and it is ignored. None if
        the bytes are not a valid time.
        """
        _, hour, minute, second = self.raw[TIME_OFFSET : TIME_OFFSET + 4]
        try:
            return datetime.time(hour, minute, second)
        except ValueError:
            return None
