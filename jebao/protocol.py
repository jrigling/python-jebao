"""Protocol layer for Jebao devices.

This implementation is inspired by the original Node.js library:
https://github.com/tancou/jebao-dosing-pump-md-4.4

The GizWits protocol structure and authentication flow were adapted from
that work, with extensions for MDP-20000 support and asyncio implementation.
"""
import asyncio
import logging
from typing import Dict, Optional, Tuple

from .const import (
    COMMAND_TIMEOUT,
    CONTROL_COMMAND_SIZE,
    DEFAULT_TIMEOUT,
    MSG_CONTROL_OR_EXTENDED_REQUEST,
    MSG_CONTROL_RESPONSE_OR_EXTENDED_DATA,
    MSG_DATA_REQUEST_SIMPLE,
    MSG_DATA_RESPONSE_SIMPLE,
    MSG_LOGIN_REQUEST,
    MSG_LOGIN_SUCCESS,
    MSG_PASSCODE_RESPONSE,
    MSG_PING,
    MSG_PONG,
    MSG_REQUEST_PASSCODE,
    POST_LOGIN_DRAIN_TIMEOUT,
    STATUS_FLAG_FEED,
    STATUS_FLAG_PROGRAM,
    STATUS_FLAG_RUNNING,
    DeviceState,
)
from .exceptions import (
    JebaoAuthenticationError,
    JebaoCommandError,
    JebaoConnectionError,
    JebaoTimeoutError,
)

_LOGGER = logging.getLogger(__name__)

# Every GizWits frame starts with this prefix, followed by a varint length
# (7 bits per byte, high bit = continuation) and that many bytes:
# flag (1), command (2), then the command's payload.
FRAME_PREFIX = b"\x00\x00\x00\x03"

# Bytes to skip looking for FRAME_PREFIX before giving up on the stream
MAX_RESYNC_BYTES = 512

# How many non-status frames request_status() will skip before giving up
MAX_SKIPPED_FRAMES = 10


class JebaoProtocol:
    """Low-level protocol implementation for Jebao devices."""

    def __init__(self, host: str, port: int = 12416, device_id: Optional[str] = None):
        """Initialize protocol handler.

        Args:
            host: Device IP address
            port: TCP port (default 12416)
            device_id: Device ID for logging (optional)
        """
        self.host = host
        self.port = port
        self.device_id = device_id
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._sequence = 1
        self._keepalive_task: Optional[asyncio.Task] = None
        self._passcode: Optional[bytes] = None

        # Locks for thread-safe operation
        self._request_lock = asyncio.Lock()  # Serialize entire request-response cycles
        self._read_lock = asyncio.Lock()     # Low-level read serialization

        # Message dispatcher
        self._message_queues: Dict[int, asyncio.Queue] = {}
        self._message_reader_task: Optional[asyncio.Task] = None
        self._dispatcher_running = False

        # Read buffer for handling garbage bytes
        self._read_buffer = bytearray()

    @property
    def device_identifier(self) -> str:
        """Get device identifier for logging."""
        if self.device_id:
            return f"[{self.device_id}]"
        return f"[{self.host}]"

    @property
    def is_connected(self) -> bool:
        """Check if connection is established."""
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        """Connect to device and authenticate.

        Args:
            timeout: Connection timeout in seconds

        Raises:
            JebaoConnectionError: Connection failed
            JebaoAuthenticationError: Authentication failed
            JebaoTimeoutError: Operation timed out
        """
        try:
            _LOGGER.debug("%s Connecting to %s:%d", self.device_identifier, self.host, self.port)
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=timeout
            )
        except asyncio.TimeoutError as err:
            raise JebaoTimeoutError(f"Connection to {self.host} timed out") from err
        except OSError as err:
            raise JebaoConnectionError(
                f"Failed to connect to {self.host}: {err}"
            ) from err

        try:
            await self._authenticate(timeout)
            # Note: Message dispatcher disabled - causes issues with pump's padding bytes
            # Using synchronous request-response pattern instead
            # self._start_message_reader()
            # Note: Keepalive disabled for now as it interferes with command responses
            # The connection stays alive without explicit pings in most cases
            # self._start_keepalive()
        except Exception:
            await self.disconnect()
            raise

    async def _authenticate(self, timeout: float) -> None:
        """Perform authentication handshake.

        Args:
            timeout: Operation timeout

        Raises:
            JebaoAuthenticationError: Authentication failed
        """
        _LOGGER.debug("%s Starting authentication", self.device_identifier)

        # Step 1: Request passcode
        request = self._build_passcode_request()
        await self._send_raw(request)

        # Step 2: Receive passcode
        try:
            response = await asyncio.wait_for(self._read_raw(), timeout=timeout)
        except asyncio.TimeoutError as err:
            raise JebaoAuthenticationError("Passcode request timed out") from err

        _LOGGER.debug(f"Passcode response length: {len(response)}, data: {response[:30].hex() if len(response) >= 30 else response.hex()}")

        # Message type is at byte 7, not byte 8
        if len(response) < 20 or response[7] != MSG_PASSCODE_RESPONSE:
            _LOGGER.error(f"Invalid response - length: {len(response)}, type at [7]: {response[7] if len(response) > 7 else 'N/A'}")
            raise JebaoAuthenticationError("Invalid passcode response")

        self._passcode = response[10:20]  # Extract 10-character passcode
        _LOGGER.debug("Received passcode: %s", self._passcode.decode("ascii"))

        # Step 3: Login with passcode
        login = self._build_login_request(self._passcode)
        _LOGGER.debug(f"Sending login request: {login.hex()}")
        await self._send_raw(login)

        # Step 4: Verify login success
        try:
            response = await asyncio.wait_for(self._read_raw(), timeout=timeout)
            _LOGGER.debug(f"Login response: {response.hex()}")
        except asyncio.TimeoutError as err:
            _LOGGER.error("Login timed out - pump may be busy or rate-limited")
            raise JebaoAuthenticationError("Login timed out") from err

        # Message type is at byte 7
        if len(response) < 8:
            _LOGGER.error(f"Login response too short: {len(response)} bytes")
            raise JebaoAuthenticationError("Login failed - response too short")

        if response[7] != MSG_LOGIN_SUCCESS:
            _LOGGER.error(f"Login failed - expected type 0x{MSG_LOGIN_SUCCESS:02x} at byte 7, got 0x{response[7]:02x}")
            raise JebaoAuthenticationError("Login failed")

        _LOGGER.info("%s Authentication successful", self.device_identifier)

        # Some pumps (MDP-15000) follow the login-OK with a duplicate login-OK and a
        # 0x62 frame. Consume them now so the first status read doesn't get them.
        await self._drain_garbage_bytes(timeout=POST_LOGIN_DRAIN_TIMEOUT)

    async def disconnect(self) -> None:
        """Disconnect from device."""
        # Stop message reader
        await self._stop_message_reader()

        # Stop keepalive
        if self._keepalive_task:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
            self._keepalive_task = None

        # Close connection
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception as err:
                _LOGGER.debug("Error closing connection: %s", err)
            finally:
                self._writer = None
                self._reader = None

        _LOGGER.debug("%s Disconnected", self.device_identifier)

    def _start_keepalive(self) -> None:
        """Start keepalive task."""
        if self._keepalive_task is None or self._keepalive_task.done():
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        """Send periodic ping messages to keep connection alive."""
        from .const import KEEPALIVE_INTERVAL

        while self.is_connected:
            try:
                await asyncio.sleep(KEEPALIVE_INTERVAL)
                if not self.is_connected:
                    break

                # Send ping
                ping = bytes.fromhex("0000000303000015")
                await self._send_raw(ping)

                # Wait for pong
                try:
                    response = await asyncio.wait_for(
                        self._read_raw(), timeout=2.0
                    )
                    # Message type at byte 7
                    if len(response) >= 8 and response[7] == MSG_PONG:
                        _LOGGER.debug("Keepalive OK")
                    else:
                        _LOGGER.warning("Invalid pong response")
                except asyncio.TimeoutError:
                    _LOGGER.warning("Keepalive pong timeout")
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.error("Keepalive error: %s", err)
                break

    def _start_message_reader(self) -> None:
        """Start background message reader task."""
        if self._message_reader_task is None or self._message_reader_task.done():
            self._dispatcher_running = True
            self._message_reader_task = asyncio.create_task(self._message_reader_loop())
            _LOGGER.debug("Message reader started for %s", self.host)

    async def _stop_message_reader(self) -> None:
        """Stop background message reader task."""
        self._dispatcher_running = False

        if self._message_reader_task:
            self._message_reader_task.cancel()
            try:
                await self._message_reader_task
            except asyncio.CancelledError:
                pass
            self._message_reader_task = None

        # Clear all queues
        for queue in self._message_queues.values():
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        self._message_queues.clear()

        _LOGGER.debug("Message reader stopped for %s", self.host)

    async def _message_reader_loop(self) -> None:
        """Background loop that reads messages and routes them to queues."""
        _LOGGER.debug("Message reader loop started for %s", self.host)

        while self._dispatcher_running and self.is_connected:
            try:
                # Read next message from socket
                message = await self._read_raw()
                msg_type = message[7] if len(message) > 7 else None

                if msg_type is None:
                    _LOGGER.warning("Received message with no type from %s", self.host)
                    continue

                # Validate message type - skip obviously invalid messages
                # Valid types: 0x00, 0x06-0x09, 0x15-0x16, 0x90-0x94
                if not (
                    msg_type == 0x00
                    or (0x06 <= msg_type <= 0x09)
                    or (0x15 <= msg_type <= 0x16)
                    or (0x90 <= msg_type <= 0x94)
                ):
                    _LOGGER.warning(
                        "Ignoring invalid message type 0x%02x from %s",
                        msg_type,
                        self.host,
                    )
                    continue

                _LOGGER.debug(
                    "Dispatcher received message type 0x%02x (%d bytes) from %s",
                    msg_type,
                    len(message),
                    self.host,
                )

                # Create queue for this message type if it doesn't exist
                if msg_type not in self._message_queues:
                    self._message_queues[msg_type] = asyncio.Queue()

                # Route message to appropriate queue
                await self._message_queues[msg_type].put(message)

            except asyncio.CancelledError:
                _LOGGER.debug("Message reader cancelled for %s", self.host)
                break
            except JebaoConnectionError as err:
                # Connection closed or read failed
                _LOGGER.debug("Connection closed in message reader: %s", err)
                break
            except Exception as err:
                if self._dispatcher_running:
                    _LOGGER.error(
                        "Message reader error for %s: %s", self.host, err
                    )
                break

        _LOGGER.debug("Message reader loop ended for %s", self.host)

    async def _wait_for_message_type(
        self, msg_type: int, timeout: float
    ) -> bytes:
        """Wait for a specific message type from the dispatcher.

        Args:
            msg_type: Message type to wait for (byte 7 of message)
            timeout: Timeout in seconds

        Returns:
            The message bytes

        Raises:
            JebaoTimeoutError: Timeout waiting for message
        """
        # Create queue for this message type if it doesn't exist
        if msg_type not in self._message_queues:
            self._message_queues[msg_type] = asyncio.Queue()

        try:
            message = await asyncio.wait_for(
                self._message_queues[msg_type].get(), timeout=timeout
            )
            _LOGGER.debug(
                "Got message type 0x%02x from queue for %s", msg_type, self.host
            )
            return message
        except asyncio.TimeoutError as err:
            raise JebaoTimeoutError(
                f"Timeout waiting for message type 0x{msg_type:02x}"
            ) from err

    async def request_status(
        self, timeout: float = COMMAND_TIMEOUT
    ) -> bytes:
        """Request device status.

        Args:
            timeout: Request timeout

        Returns:
            Raw status data

        Raises:
            JebaoConnectionError: Not connected
            JebaoTimeoutError: Request timed out
        """
        # Serialize requests - only one operation at a time
        async with self._request_lock:
            if not self.is_connected:
                raise JebaoConnectionError("Not connected")

            # Drain any unexpected leftover bytes as safety net (very short timeout)
            # Normally every frame is read whole, but this catches edge cases
            await self._drain_garbage_bytes(timeout=0.01)

            # Send simple status request (0x90)
            # Format: 00 00 00 03 04 00 00 90 02
            # The last byte (02) requests values, and response will be message type 91 or 0x00
            request = bytes.fromhex("0000000304000090 02")
            _LOGGER.debug(f"Sending status request: {request.hex()}")
            await self._send_raw(request)
            _LOGGER.debug("Status request sent, waiting for response...")

            # Read frames directly (no dispatcher) until a status frame arrives.
            # Pumps can interleave other frames (late login-OKs, 0x62, ACKs), so skip
            # anything that isn't a status frame instead of failing on it.
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            for _ in range(MAX_SKIPPED_FRAMES + 1):
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    response = await asyncio.wait_for(self._read_raw(), timeout=remaining)
                except asyncio.TimeoutError as err:
                    raise JebaoTimeoutError("Status request timed out") from err

                if self.is_status_frame(response):
                    _LOGGER.debug(
                        "Status response: %d bytes, action 0x%02x, data: %s",
                        len(response),
                        response[9],
                        response[10:20].hex(),
                    )
                    return response

                _LOGGER.debug(
                    "%s Skipping non-status frame while waiting for status: %d bytes, %s",
                    self.device_identifier,
                    len(response),
                    response[:12].hex(),
                )

            raise JebaoTimeoutError("No status response received")

    async def send_control_command(
        self,
        opcode1: int,
        opcode2: int = 0,
        param1: int = 0,
        param2: int = 0,
        timeout: float = COMMAND_TIMEOUT,
    ) -> None:
        """Send control command to device.

        Args:
            opcode1: Primary opcode (at byte 21)
            opcode2: Secondary opcode (at byte 22)
            param1: Parameter 1 (at byte 23)
            param2: Parameter 2 (at byte 24)
            timeout: Command timeout

        Raises:
            JebaoConnectionError: Not connected
            JebaoCommandError: Command failed
            JebaoTimeoutError: Command timed out
        """
        # Serialize requests - only one operation at a time
        async with self._request_lock:
            if not self.is_connected:
                raise JebaoConnectionError("Not connected")

            # Drain any unexpected leftover bytes as safety net (very short timeout)
            # Normally every frame is read whole, but this catches edge cases
            await self._drain_garbage_bytes(timeout=0.01)

            command = self._build_control_command(opcode1, opcode2, param1, param2)
            _LOGGER.debug(
                "%s Sending control command: opcode1=0x%02x, opcode2=0x%02x, param1=%d, param2=%d",
                self.device_identifier,
                opcode1,
                opcode2,
                param1,
                param2,
            )
            await self._send_raw(command)

            # Read ACK (message type 0x94) directly
            # The pump sends an ACK for control commands
            try:
                ack = await asyncio.wait_for(self._read_raw(), timeout=timeout)
                ack_type = ack[7] if len(ack) > 7 else 0
                _LOGGER.debug("Control command ACK received: type=0x%02x, %d bytes", ack_type, len(ack))

                # After ACK, pump may send unsolicited status update
                # Try to read it with short timeout
                try:
                    status_update = await asyncio.wait_for(self._read_raw(), timeout=0.5)
                    status_type = status_update[7] if len(status_update) > 7 else 0
                    _LOGGER.info(
                        "Pump sent unsolicited status update after control command: type=0x%02x",
                        status_type,
                    )
                except asyncio.TimeoutError:
                    # No status update sent, that's OK
                    _LOGGER.debug("No unsolicited status update after control command")

            except asyncio.TimeoutError:
                _LOGGER.warning("Control command sent but no ACK received (pump may still process it)")
                # Don't raise - pump may still process the command even without ACK

    def _build_control_command(
        self, opcode1: int, opcode2: int, param1: int, param2: int
    ) -> bytes:
        """Build control command message.

        Args:
            opcode1: Primary opcode
            opcode2: Secondary opcode
            param1: Parameter 1
            param2: Parameter 2

        Returns:
            323-byte command message
        """
        buffer = bytearray(CONTROL_COMMAND_SIZE)

        # Header
        buffer[0:4] = b"\x00\x00\x00\x03"
        buffer[4] = 0xBD
        buffer[5] = 0x02

        # Message type and sequence
        buffer[8] = MSG_CONTROL_OR_EXTENDED_REQUEST
        buffer[9:13] = self._sequence.to_bytes(4, "big")
        self._sequence += 1
        buffer[13] = 0x01

        # Command payload
        buffer[21] = opcode1
        buffer[22] = opcode2
        buffer[23] = param1
        buffer[24] = param2

        # Rest is zeros (already initialized)
        return bytes(buffer)

    @staticmethod
    def _build_passcode_request() -> bytes:
        """Build passcode request message."""
        return bytes.fromhex("00000003050000060100")

    @staticmethod
    def _build_login_request(passcode: bytes) -> bytes:
        """Build login request message.

        Args:
            passcode: 10-byte passcode

        Returns:
            Login request message
        """
        buffer = bytearray.fromhex("00000003 0f 000008 000a")
        buffer.extend(passcode)
        return bytes(buffer)

    async def _drain_garbage_bytes(self, timeout: float = 0.1) -> None:
        """Consume frames the pump sent without being asked (status broadcasts,
        duplicate login-OKs, etc.) so they don't answer the next request.

        Args:
            timeout: How long to wait for each further frame (default 0.1s)
        """
        reader = self._reader
        if reader is None:
            return

        try:
            while True:
                try:
                    frame = await asyncio.wait_for(self._read_frame(reader), timeout=timeout)
                except asyncio.TimeoutError:
                    # No more data - we're done
                    break
                _LOGGER.debug(
                    "Drained unsolicited frame: %d bytes, %s", len(frame), frame[:12].hex()
                )
        except Exception as err:
            _LOGGER.debug("Error draining garbage: %s", err)

    async def _send_raw(self, data: bytes) -> None:
        """Send raw data to device.

        Args:
            data: Data to send

        Raises:
            JebaoConnectionError: Send failed
        """
        if not self._writer:
            raise JebaoConnectionError("Not connected")

        try:
            self._writer.write(data)
            await self._writer.drain()
        except Exception as err:
            _LOGGER.error("Send failed, closing connection: %s", err)
            await self.disconnect()
            raise JebaoConnectionError(f"Send failed: {err}") from err

    @staticmethod
    async def _read_frame(reader: asyncio.StreamReader) -> bytes:
        """Read one complete GizWits frame: prefix, varint length, then the body.

        Large frames (e.g. 312-byte status frames: length bytes b2 02 = 306) have
        a two-byte length. Reading only the first byte and treating the rest as
        fixed padding happened to work on the MDP-20000, but those bytes are
        data, and other models (MDP-15000) don't fill them with 0xee/0x00.

        Raises:
            JebaoConnectionError: Stream could not be resynchronized
            asyncio.IncompleteReadError: Connection closed mid-frame
        """
        prefix = await reader.readexactly(len(FRAME_PREFIX))
        for skipped in range(MAX_RESYNC_BYTES):
            if prefix == FRAME_PREFIX:
                if skipped:
                    _LOGGER.warning("Skipped %d stray bytes to re-sync message stream", skipped)
                break
            prefix = prefix[1:] + await reader.readexactly(1)
        else:
            raise JebaoConnectionError("Lost message synchronization")

        length_bytes = bytearray()
        length = 0
        for shift in range(0, 28, 7):
            byte = (await reader.readexactly(1))[0]
            length_bytes.append(byte)
            length |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
        else:
            raise JebaoConnectionError("Invalid frame length")

        body = await reader.readexactly(length)
        return prefix + bytes(length_bytes) + body

    async def _read_raw(self) -> bytes:
        """Read one frame from the device.

        Returns:
            Received frame (prefix, length bytes, and body)

        Raises:
            JebaoConnectionError: Read failed
        """
        async with self._read_lock:
            # Bind the reader locally under the lock. disconnect() (called from
            # our own IncompleteReadError handler, from _send_raw's error path,
            # and from the coordinator) sets self._reader = None from another
            # task; re-reading self._reader across the awaits below would raise
            # AttributeError. Reading from a captured, closed StreamReader raises
            # IncompleteReadError/ConnectionResetError instead, which we handle.
            reader = self._reader
            if reader is None:
                raise JebaoConnectionError("Not connected")
            try:
                frame = await self._read_frame(reader)
                _LOGGER.debug("Message complete: %d bytes, %s", len(frame), frame[:12].hex())
                return frame
            except (asyncio.IncompleteReadError, ConnectionResetError) as err:
                # Expected when the pump drops an idle connection. The reconnect
                # is logged by the coordinator/retry layer, so keep this at debug
                # to avoid flooding the log every poll.
                _LOGGER.debug(
                    "%s Connection closed by peer (%s) - will reconnect",
                    self.device_identifier,
                    type(err).__name__,
                )
                # Mark connection as closed so reconnection can happen
                await self.disconnect()
                raise JebaoConnectionError("Connection closed") from err
            except JebaoConnectionError:
                raise
            except Exception as err:
                _LOGGER.error(f"Read exception: {type(err).__name__}: {err}")
                raise JebaoConnectionError(f"Read failed: {err}") from err

    @staticmethod
    def is_status_frame(frame: bytes) -> bool:
        """Check whether a frame is a device status frame.

        Status frames have a two-byte length, command 0x0091, and GizWits action
        0x03 (reply to our read) or 0x04 (unsolicited report). Both carry the
        same payload: state flags at offset 10, speed at offset 11.
        """
        return (
            len(frame) >= 12
            and frame[4] & 0x80 != 0
            and frame[5] & 0x80 == 0
            and frame[7] == 0x00
            and frame[8] == MSG_DATA_RESPONSE_SIMPLE
            and frame[9] in (0x03, 0x04)
        )

    @staticmethod
    def parse_status(data: bytes) -> Optional[dict]:
        """Parse status data from response.

        Args:
            data: Raw status frame (see is_status_frame)

        Returns:
            Dictionary with state (a DeviceState value) and speed, or None if
            the frame isn't a status frame
        """
        # Byte 10: state flags (0x01 running, 0x04 feed, 0x08 program; the
        #          MDP-20000 also sets 0x10, giving 0x10/0x11/0x15/0x19)
        # Byte 11: speed percentage (0-100)
        if not JebaoProtocol.is_status_frame(data):
            return None

        flags = data[10]
        if flags & STATUS_FLAG_PROGRAM:
            state = DeviceState.PROGRAM
        elif flags & STATUS_FLAG_FEED:
            state = DeviceState.FEED
        elif flags & STATUS_FLAG_RUNNING:
            state = DeviceState.ON
        else:
            state = DeviceState.OFF

        return {
            "state": int(state),
            "speed": data[11],
        }
