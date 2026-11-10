"""Protocol support for the four-channel Jebao MD-4.4 dosing pump."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Callable, Optional

from .const import COMMAND_TIMEOUT, DEFAULT_TIMEOUT
from .exceptions import (
    JebaoAuthenticationError,
    JebaoCommandError,
    JebaoConnectionError,
    JebaoTimeoutError,
)

MAGIC = b"\x00\x00\x00\x03"
PASSCODE_REQUEST = bytes.fromhex("0000000303000006")
STATUS_REQUEST = bytes.fromhex("000000030400009002")
MSG_PASSCODE = 0x07
MSG_LOGIN_SUCCESS = 0x09
MSG_STATUS = 0x91
MSG_ACTION_ACK = 0x94


@dataclass(frozen=True)
class MD44Dose:
    """One native MD-4.4 dose slot."""

    hour: int
    minute: int
    milliliters: int

    def __post_init__(self) -> None:
        if not 0 <= self.hour <= 23 or not 0 <= self.minute <= 59:
            raise ValueError("Dose time must be between 00:00 and 23:59")
        if not 1 <= self.milliliters <= 0xFFFF:
            raise ValueError("Dose volume must be a whole number from 1 to 65535 mL")


@dataclass(frozen=True)
class MD44Status:
    """Verified fields from an MD-4.4 status response."""

    running_bitmap: int
    schedule_enabled_bitmap: int
    raw_payload: bytes

    def is_running(self, channel: int) -> bool:
        _validate_channel(channel)
        return bool(self.running_bitmap & (1 << channel))

    def is_schedule_enabled(self, channel: int) -> bool:
        _validate_channel(channel)
        return bool(self.schedule_enabled_bitmap & (0x02 << (channel - 1)))


def encode_login(passcode: bytes) -> bytes:
    if not 10 <= len(passcode) <= 32:
        raise ValueError("MD-4.4 passcode must contain 10 through 32 bytes")
    return MAGIC + bytes([5 + len(passcode), 0, 0, 0x08, 0, len(passcode)]) + passcode


def extract_passcode(frame: bytes) -> bytes:
    offset = message_type_offset(frame)
    if frame[offset] != MSG_PASSCODE:
        raise JebaoAuthenticationError("Invalid MD-4.4 passcode response")
    length_offset = offset + 2
    if length_offset >= len(frame):
        raise JebaoAuthenticationError("MD-4.4 passcode response is truncated")
    length = frame[length_offset]
    passcode = frame[length_offset + 1 : length_offset + 1 + length]
    if not 10 <= len(passcode) <= 32:
        raise JebaoAuthenticationError("Invalid MD-4.4 passcode length")
    return passcode


def encode_head_power(channel: int, on: bool, sequence: int) -> bytes:
    """Encode the 808-byte per-head command used by firmware 4.1.4."""
    _validate_channel(channel)
    selector = 1 << channel
    frame = _action_frame(sequence)
    frame[18] = selector
    frame[21] = selector if on else 0
    return bytes(frame)


def encode_day_interval(channel: int, day_interval: int, sequence: int) -> bytes:
    _validate_channel(channel)
    if not 1 <= day_interval <= 255:
        raise ValueError("Day interval must be from 1 through 255")
    frame = _action_frame(sequence)
    frame[16] = 0x10 << (channel - 1)
    frame[22 + channel] = day_interval
    return bytes(frame)


def encode_dose_schedule(channel: int, doses: Sequence[MD44Dose], sequence: int) -> bytes:
    _validate_channel(channel)
    if len(doses) > 20:
        raise ValueError("MD-4.4 supports at most 20 doses per day")
    frame = _action_frame(sequence)
    frame[14:16] = (0x20 << (channel - 1)).to_bytes(2, "big")
    table_offset = 32 + (channel - 1) * 96
    for index, dose in enumerate(doses):
        offset = table_offset + index * 4
        frame[offset] = dose.hour
        frame[offset + 1] = dose.minute
        frame[offset + 2 : offset + 4] = dose.milliliters.to_bytes(2, "big")
    return bytes(frame)


def encode_schedule_enabled(channel: int, enabled: bool, sequence: int) -> bytes:
    _validate_channel(channel)
    flag = 0x02 << (channel - 1)
    frame = _action_frame(sequence)
    frame[17] = flag
    frame[20] = flag if enabled else 0
    return bytes(frame)


def parse_status(frame: bytes) -> MD44Status:
    offset = message_type_offset(frame)
    if frame[offset] != MSG_STATUS:
        raise JebaoCommandError("Invalid MD-4.4 status response")
    payload = frame[offset + 2 :]
    if len(payload) < 3:
        raise JebaoCommandError("MD-4.4 status response is truncated")
    return MD44Status(payload[2], payload[1], payload)


def frame_length(data: bytes) -> Optional[int]:
    if len(data) < 5 or data[:4] != MAGIC:
        return None
    decoded = _decode_varint(data, 4)
    if decoded is None:
        return None
    value, width = decoded
    return 4 + width + value


def message_type_offset(frame: bytes) -> int:
    decoded = _decode_varint(frame, 4)
    if decoded is None:
        raise JebaoCommandError("Invalid MD-4.4 frame length")
    value, width = decoded
    if len(frame) < 4 + width + value:
        raise JebaoCommandError("Incomplete MD-4.4 frame")
    offset = 6 + width
    if offset >= len(frame):
        raise JebaoCommandError("MD-4.4 frame has no message type")
    return offset


class MD44Protocol:
    """Serialized asyncio transport for an MD-4.4."""

    def __init__(self, host: str, port: int = 12416) -> None:
        self.host = host
        self.port = port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._lock = asyncio.Lock()
        self._sequence = 1

    @property
    def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout
            )
            await self._write(PASSCODE_REQUEST)
            passcode = extract_passcode(await self._read_until((MSG_PASSCODE,), timeout))
            await self._write(encode_login(passcode))
            await self._read_until((MSG_LOGIN_SUCCESS,), timeout)
        except asyncio.TimeoutError as err:
            await self.disconnect()
            raise JebaoTimeoutError("MD-4.4 connection timed out") from err
        except OSError as err:
            await self.disconnect()
            raise JebaoConnectionError(f"Failed to connect to {self.host}: {err}") from err
        except Exception:
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
        self._reader = None
        self._writer = None

    async def request_status(self, timeout: float = COMMAND_TIMEOUT) -> MD44Status:
        async with self._lock:
            await self._write(STATUS_REQUEST)
            return parse_status(await self._read_until((MSG_STATUS,), timeout))

    async def set_head_power(
        self, channel: int, on: bool, timeout: float = COMMAND_TIMEOUT
    ) -> MD44Status:
        async with self._lock:
            await self._write(encode_head_power(channel, on, self._next_sequence()))
            await self._read_until((MSG_ACTION_ACK,), timeout)
            return await self._confirm(lambda status: status.is_running(channel) == on, timeout)

    async def set_schedule(
        self,
        channel: int,
        day_interval: int,
        doses: Sequence[MD44Dose],
        enabled: bool = True,
        timeout: float = COMMAND_TIMEOUT,
    ) -> MD44Status:
        async with self._lock:
            frames = (
                encode_day_interval(channel, day_interval, self._next_sequence()),
                encode_dose_schedule(channel, doses, self._next_sequence()),
                encode_schedule_enabled(channel, False, self._next_sequence()),
                encode_schedule_enabled(channel, enabled, self._next_sequence()),
            )
            for frame in frames:
                await self._write(frame)
                await self._read_until((MSG_ACTION_ACK,), timeout)
            return await self._confirm(
                lambda status: status.is_schedule_enabled(channel) == enabled, timeout
            )

    async def _confirm(self, predicate: Callable[[MD44Status], bool], timeout: float) -> MD44Status:
        for _ in range(5):
            await self._write(STATUS_REQUEST)
            status = parse_status(await self._read_until((MSG_STATUS,), timeout))
            if predicate(status):
                return status
            await asyncio.sleep(0.4)
        raise JebaoCommandError("MD-4.4 did not confirm the requested state")

    async def _write(self, frame: bytes) -> None:
        if not self._writer or self._writer.is_closing():
            raise JebaoConnectionError("MD-4.4 is not connected")
        self._writer.write(frame)
        await self._writer.drain()

    async def _read_until(self, types: tuple[int, ...], timeout: float) -> bytes:
        async def receive() -> bytes:
            for _ in range(8):
                frame = await self._read_frame()
                if frame[message_type_offset(frame)] in types:
                    return frame
            raise JebaoCommandError("Expected MD-4.4 response was not received")

        try:
            return await asyncio.wait_for(receive(), timeout)
        except asyncio.TimeoutError as err:
            raise JebaoTimeoutError("Timed out waiting for MD-4.4 response") from err

    async def _read_frame(self) -> bytes:
        if not self._reader:
            raise JebaoConnectionError("MD-4.4 is not connected")
        header = await self._reader.readexactly(4)
        if header != MAGIC:
            raise JebaoConnectionError("Invalid MD-4.4 frame signature")
        length_bytes = bytearray()
        while len(length_bytes) < 5:
            byte = await self._reader.readexactly(1)
            length_bytes.extend(byte)
            if byte[0] & 0x80 == 0:
                break
        decoded = _decode_varint(length_bytes, 0)
        if decoded is None:
            raise JebaoConnectionError("Invalid MD-4.4 frame length")
        body_length, _ = decoded
        return header + bytes(length_bytes) + await self._reader.readexactly(body_length)

    def _next_sequence(self) -> int:
        sequence = self._sequence
        self._sequence = 1 if sequence == 0xFFFFFFFF else sequence + 1
        return sequence


def _action_frame(sequence: int) -> bytearray:
    if not 0 <= sequence <= 0xFFFFFFFF:
        raise ValueError("Sequence must be an unsigned 32-bit integer")
    frame = bytearray(808)
    frame[:9] = bytes.fromhex("00000003a206000093")
    frame[9:13] = sequence.to_bytes(4, "big")
    frame[13] = 1
    return frame


def _validate_channel(channel: int) -> None:
    if channel not in (1, 2, 3, 4):
        raise ValueError("MD-4.4 channel must be 1 through 4")


def _decode_varint(data: Sequence[int], offset: int) -> Optional[tuple[int, int]]:
    value = 0
    multiplier = 1
    for index in range(offset, min(len(data), offset + 5)):
        byte = data[index]
        value += (byte & 0x7F) * multiplier
        if byte & 0x80 == 0:
            return value, index - offset + 1
        multiplier *= 128
    return None
