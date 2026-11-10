"""Protocol layer for Jebao devices.

This implementation is inspired by the original Node.js library:
https://github.com/tancou/jebao-dosing-pump-md-4.4

Jebao pumps use the GizWits LAN protocol on TCP port 12416. Each frame is:

    00 00 00 03 | <length: LEB128 varint> | <flag: 1 byte> | <cmd: 2 bytes BE> | payload

``length`` counts every byte after the varint (flag + cmd + payload). Small
frames use a 1-byte length; the 306-byte status report uses 2 (``b2 02``).

Connection model: one short-lived session per operation
-------------------------------------------------------
Measured against an MDP-20000 (firmware on 04020B28 hardware), the pump closes
any TCP session that has been idle for ~10 seconds. A persistent connection
polled every 30 s is therefore always dead by the next poll. Instead, every
operation opens a fresh connection, authenticates (~30 ms on a LAN), performs
one request/response exchange and closes. Any error simply discards that
socket, so a timeout or partial read can never desynchronise a later request.

All operations on one device are serialised by a lock so polls and commands
never interleave on the wire.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Tuple

from .const import (
    COMMAND_TIMEOUT,
    CONTROL_COMMAND_SIZE,
    DEFAULT_TIMEOUT,
    MSG_CONTROL_OR_EXTENDED_REQUEST,
    MSG_CONTROL_RESPONSE_OR_EXTENDED_DATA,
    MSG_DATA_RESPONSE_SIMPLE,
    MSG_LOGIN_SUCCESS,
    MSG_PASSCODE_RESPONSE,
    MSG_PONG,
)
from .exceptions import (
    JebaoAuthenticationError,
    JebaoCommandError,
    JebaoConnectionError,
    JebaoTimeoutError,
)

_LOGGER = logging.getLogger(__name__)

FRAME_HEADER = b"\x00\x00\x00\x03"
MAX_FRAME_LENGTH = 4096

# Raw request frames
PASSCODE_REQUEST = bytes.fromhex("00000003 03 00 0006")
STATUS_REQUEST = bytes.fromhex("00000003 04 00 0090 02")
PING_REQUEST = bytes.fromhex("00000003 03 00 0015")

# Kept for backwards compatibility (older callers referenced this name).
MSG_EXTENDED_DATA = 0x00


def encode_varint(value: int) -> bytes:
    """Encode an unsigned int as LEB128 (GizWits length field)."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def build_frame(cmd: int, payload: bytes = b"", flag: int = 0x00) -> bytes:
    """Build a GizWits frame."""
    body = bytes([flag]) + cmd.to_bytes(2, "big") + payload
    return FRAME_HEADER + encode_varint(len(body)) + body


def split_frame(frame: bytes) -> Tuple[int, bytes]:
    """Return (cmd, payload) for a complete frame.

    Raises:
        JebaoCommandError: frame is malformed
    """
    if len(frame) < 8 or frame[:4] != FRAME_HEADER:
        raise JebaoCommandError(f"Malformed frame: {frame[:16].hex()}")
    pos = 4
    length = 0
    shift = 0
    while True:
        if pos >= len(frame) or shift > 21:
            raise JebaoCommandError("Malformed frame length")
        byte = frame[pos]
        pos += 1
        length |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            break
    body = frame[pos:]
    if len(body) != length or length < 3:
        raise JebaoCommandError(
            f"Frame length mismatch: header says {length}, got {len(body)}"
        )
    cmd = int.from_bytes(body[1:3], "big")
    return cmd, body[3:]


class JebaoProtocol:
    """Low-level protocol implementation for Jebao devices."""

    def __init__(self, host: str, port: int = 12416, device_id: Optional[str] = None):
        """Initialize protocol handler."""
        self.host = host
        self.port = port
        self.device_id = device_id
        self._sequence = 1
        self._lock = asyncio.Lock()
        # True after the most recent operation succeeded. There is no
        # long-lived socket; this reflects whether the device is reachable.
        self._available = False

    @property
    def device_identifier(self) -> str:
        """Get device identifier for logging."""
        return f"[{self.device_id or self.host}]"

    @property
    def is_connected(self) -> bool:
        """Whether the last operation with the device succeeded."""
        return self._available

    # ------------------------------------------------------------------ public

    async def connect(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        """Verify the device is reachable and accepts our login.

        Raises:
            JebaoConnectionError / JebaoTimeoutError / JebaoAuthenticationError
        """
        async with self._lock:
            await self._run(self._noop, timeout)

    async def disconnect(self) -> None:
        """Mark the device as disconnected. No socket is held between operations."""
        self._available = False

    async def ping(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        """Send a GizWits ping and wait for the pong."""

        async def _op(reader, writer):
            await self._send(writer, PING_REQUEST)
            await self._read_until(reader, MSG_PONG)

        async with self._lock:
            await self._run(_op, timeout)

    async def request_status(self, timeout: float = COMMAND_TIMEOUT) -> bytes:
        """Request device status.

        Returns:
            The complete raw status frame (feed it to ``parse_status``).
        """

        async def _op(reader, writer):
            await self._send(writer, STATUS_REQUEST)
            return await self._read_until(reader, MSG_DATA_RESPONSE_SIMPLE)

        async with self._lock:
            return await self._run(_op, timeout)

    async def send_control_command(
        self,
        opcode1: int,
        opcode2: int = 0,
        param1: int = 0,
        param2: int = 0,
        timeout: float = COMMAND_TIMEOUT,
    ) -> None:
        """Send a control command.

        Establishing the session is retried once (nothing has been sent to the
        pump yet, so that is always safe). Once the command bytes are written
        it is never resent, so non-idempotent commands (e.g. start feed) can't
        be applied twice.
        """
        command = self._build_control_command(opcode1, opcode2, param1, param2)
        _LOGGER.debug(
            "%s Control command opcode1=0x%02x opcode2=0x%02x p1=%d p2=%d",
            self.device_identifier, opcode1, opcode2, param1, param2,
        )

        async with self._lock:
            session = None
            last_err: Optional[Exception] = None
            for attempt in range(2):
                try:
                    session = await asyncio.wait_for(self._open(timeout), timeout * 2)
                    break
                except asyncio.TimeoutError:
                    last_err = JebaoTimeoutError(f"Connecting to {self.host} timed out")
                except (JebaoConnectionError, JebaoTimeoutError, JebaoAuthenticationError) as err:
                    last_err = err
                if attempt == 0:
                    await asyncio.sleep(0.5)
            if session is None:
                self._available = False
                assert last_err is not None
                raise last_err

            reader, writer = session
            try:
                await self._send(writer, command)
                try:
                    await asyncio.wait_for(
                        self._read_until(reader, MSG_CONTROL_RESPONSE_OR_EXTENDED_DATA),
                        timeout,
                    )
                except (asyncio.TimeoutError, JebaoConnectionError) as err:
                    # The command was delivered; the pump usually applies it
                    # even when the ACK is lost. Don't raise (and don't retry).
                    _LOGGER.warning(
                        "%s Control command sent but no ACK received (%s)",
                        self.device_identifier, type(err).__name__,
                    )
                self._available = True
            finally:
                await self._close(writer)

    @staticmethod
    def parse_status(data: bytes) -> Optional[dict]:
        """Parse a status frame (cmd 0x0091) into ``{"state", "speed"}``.

        Payload layout: [action=0x03][state][speed]...
        """
        try:
            cmd, payload = split_frame(data)
        except JebaoCommandError:
            return None
        if cmd != MSG_DATA_RESPONSE_SIMPLE or len(payload) < 3:
            return None
        return {"state": payload[1], "speed": payload[2]}

    # ---------------------------------------------------------------- internals

    async def _run(self, op, timeout: float):
        """Open a session, run ``op(reader, writer)``, always close. Caller holds lock."""
        writer = None
        try:
            async def _whole():
                nonlocal writer
                reader, writer = await self._open(timeout)
                return await op(reader, writer)

            # Overall deadline: connect + 2 auth round-trips + op.
            result = await asyncio.wait_for(_whole(), timeout * 2)
            self._available = True
            return result
        except asyncio.TimeoutError as err:
            self._available = False
            raise JebaoTimeoutError(f"{self.device_identifier} Request to {self.host} timed out") from err
        except Exception:
            self._available = False
            raise
        finally:
            if writer is not None:
                await self._close(writer)

    async def _noop(self, reader, writer) -> None:
        return None

    async def _open(self, timeout: float):
        """Open a TCP connection and authenticate. Returns (reader, writer)."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=timeout
            )
        except asyncio.TimeoutError as err:
            raise JebaoTimeoutError(f"Connection to {self.host} timed out") from err
        except OSError as err:
            raise JebaoConnectionError(f"Failed to connect to {self.host}: {err}") from err

        try:
            await self._send(writer, PASSCODE_REQUEST)
            payload = await self._read_until(reader, MSG_PASSCODE_RESPONSE)
            # payload: [len hi][len lo][passcode...]
            plen = int.from_bytes(payload[0:2], "big") if len(payload) >= 2 else 0
            passcode = payload[2:2 + plen]
            if plen == 0 or len(passcode) != plen:
                raise JebaoAuthenticationError(f"Invalid passcode response: {payload.hex()}")

            await self._send(
                writer,
                build_frame(0x0008, len(passcode).to_bytes(2, "big") + passcode),
            )
            payload = await self._read_until(reader, MSG_LOGIN_SUCCESS)
            if payload[:1] not in (b"", b"\x00"):
                raise JebaoAuthenticationError(f"Login rejected (result {payload[:1].hex()})")
            _LOGGER.debug("%s Authenticated", self.device_identifier)
            return reader, writer
        except BaseException:
            await self._close(writer)
            raise

    async def _send(self, writer: asyncio.StreamWriter, data: bytes) -> None:
        try:
            writer.write(data)
            await writer.drain()
        except (OSError, RuntimeError) as err:
            raise JebaoConnectionError(f"Send failed: {err}") from err

    async def _read_frame(self, reader: asyncio.StreamReader) -> bytes:
        """Read exactly one frame."""
        try:
            header = await reader.readexactly(4)
            if header != FRAME_HEADER:
                raise JebaoCommandError(f"Unexpected frame header {header.hex()}")
            length = 0
            shift = 0
            raw_len = bytearray()
            while True:
                byte = (await reader.readexactly(1))[0]
                raw_len.append(byte)
                length |= (byte & 0x7F) << shift
                shift += 7
                if not byte & 0x80:
                    break
                if shift > 21:
                    raise JebaoCommandError("Frame length varint too long")
            if length > MAX_FRAME_LENGTH:
                raise JebaoCommandError(f"Frame length {length} too large")
            body = await reader.readexactly(length)
        except asyncio.IncompleteReadError as err:
            raise JebaoConnectionError("Connection closed by device") from err
        except (OSError, RuntimeError) as err:
            raise JebaoConnectionError(f"Read failed: {err}") from err
        return header + bytes(raw_len) + body

    async def _read_until(self, reader: asyncio.StreamReader, want_cmd: int) -> bytes:
        """Read frames until one with ``want_cmd`` arrives; return its payload.

        The pump occasionally pushes unsolicited status reports (cmd 0x0091);
        those are skipped when waiting for something else.
        """
        for _ in range(16):
            frame = await self._read_frame(reader)
            cmd, payload = split_frame(frame)
            if cmd == want_cmd:
                if want_cmd == MSG_DATA_RESPONSE_SIMPLE:
                    return frame
                return payload
            _LOGGER.debug(
                "%s Skipping frame cmd=0x%04x (%d bytes) while waiting for 0x%04x",
                self.device_identifier, cmd, len(frame), want_cmd,
            )
        raise JebaoCommandError(f"No 0x{want_cmd:04x} response from device")

    @staticmethod
    async def _close(writer: asyncio.StreamWriter) -> None:
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), 1.0)
        except Exception:  # noqa: BLE001 - best effort
            pass

    def _build_control_command(
        self, opcode1: int, opcode2: int, param1: int, param2: int
    ) -> bytes:
        """Build the 323-byte control command frame (cmd 0x0093).

        Payload: [seq: 4 bytes BE][0x01][7 zero bytes][op1][op2][p1][p2][zeros...]
        """
        payload = bytearray(CONTROL_COMMAND_SIZE - 9)  # 4 hdr + 2 varint + 3 flag/cmd
        payload[0:4] = self._sequence.to_bytes(4, "big")
        self._sequence = (self._sequence + 1) & 0xFFFFFFFF or 1
        payload[4] = 0x01
        payload[12] = opcode1
        payload[13] = opcode2
        payload[14] = param1
        payload[15] = param2
        frame = build_frame(MSG_CONTROL_OR_EXTENDED_REQUEST, bytes(payload))
        assert len(frame) == CONTROL_COMMAND_SIZE
        return frame
