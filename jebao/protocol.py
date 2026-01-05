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
)
from .exceptions import (
    JebaoAuthenticationError,
    JebaoCommandError,
    JebaoConnectionError,
    JebaoTimeoutError,
)

_LOGGER = logging.getLogger(__name__)

# Extended data response message type (183-byte status responses)
MSG_EXTENDED_DATA = 0x00


class JebaoProtocol:
    """Low-level protocol implementation for Jebao devices."""

    def __init__(self, host: str, port: int = 12416):
        """Initialize protocol handler.

        Args:
            host: Device IP address
            port: TCP port (default 12416)
        """
        self.host = host
        self.port = port
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
            _LOGGER.debug("Connecting to %s:%d", self.host, self.port)
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
        _LOGGER.debug("Starting authentication")

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

        _LOGGER.info("Authentication successful for %s", self.host)

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

        _LOGGER.debug("Disconnected from %s", self.host)

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
            # Normally padding is read explicitly, but this catches edge cases
            await self._drain_garbage_bytes(timeout=0.01)

            # Send simple status request (0x90)
            # Format: 00 00 00 03 04 00 00 90 02
            # The last byte (02) requests values, and response will be message type 91 or 0x00
            request = bytes.fromhex("0000000304000090 02")
            _LOGGER.debug(f"Sending status request: {request.hex()}")
            await self._send_raw(request)
            _LOGGER.debug("Status request sent, waiting for response...")

            # Read response directly (no dispatcher)
            try:
                response = await asyncio.wait_for(self._read_raw(), timeout=timeout)
            except asyncio.TimeoutError as err:
                raise JebaoTimeoutError("Status request timed out") from err

            _LOGGER.debug(f"Status response length: {len(response)}, data: {response.hex()[:100]}...")

            # Note: Don't drain here - pump may send unsolicited updates that we shouldn't consume

            if len(response) < 10:
                raise JebaoCommandError(f"Invalid status response: too short ({len(response)} bytes)")

            # Log the message type and first few bytes for debugging
            msg_type = response[7] if len(response) > 7 else 0
            _LOGGER.debug(f"Status response type: 0x{msg_type:02x}, bytes 10-12: {response[10:12].hex() if len(response) > 11 else 'N/A'}")

            return response

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
            # Normally padding is read explicitly, but this catches edge cases
            await self._drain_garbage_bytes(timeout=0.01)

            command = self._build_control_command(opcode1, opcode2, param1, param2)
            _LOGGER.debug(
                "Sending control command: opcode1=0x%02x, opcode2=0x%02x, param1=%d, param2=%d",
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
        """Drain any garbage/padding bytes from the socket buffer.

        The MDP-20000 pump appears to send extra padding bytes (0xee) after
        valid responses. This method drains those bytes to prevent them from
        corrupting subsequent reads.

        Args:
            timeout: How long to wait for garbage bytes (default 0.1s)
        """
        if not self._reader:
            return

        try:
            # Try to read with short timeout - if there's garbage, consume it
            while True:
                try:
                    # Try to read header of next message with short timeout
                    header = await asyncio.wait_for(
                        self._reader.read(5), timeout=timeout
                    )
                    if not header:
                        # No more data
                        break
                    if len(header) < 5:
                        # Partial header, probably garbage
                        _LOGGER.debug("Drained %d garbage bytes", len(header))
                        continue
                    # Check if it's a valid header
                    if header[0:4] == b"\x00\x00\x00\x03":
                        # Valid header found - this is a real message!
                        # Put it back somehow... actually, we can't put it back easily
                        # This is a problem - we've consumed a valid message header
                        _LOGGER.warning(
                            "Found valid header while draining - this shouldn't happen! "
                            "Pump may have sent unsolicited message."
                        )
                        # Read the rest of this message to consume it
                        length = header[4]
                        remaining = await self._reader.read(length)
                        _LOGGER.warning(
                            "Consumed unsolicited message: type=0x%02x, %d bytes total",
                            header[7] if len(header) > 7 else 0,
                            len(header) + len(remaining),
                        )
                    else:
                        # Garbage header - log and continue draining
                        _LOGGER.debug("Drained garbage header: %s", header.hex())
                except asyncio.TimeoutError:
                    # Timeout means no more data - we're done
                    break
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

    async def _read_raw(self) -> bytes:
        """Read raw data from device.

        Returns:
            Received data

        Raises:
            JebaoConnectionError: Read failed
        """
        if not self._reader:
            raise JebaoConnectionError("Not connected")

        async with self._read_lock:
            try:
                # Read header (4 bytes) + length byte (1 byte) = 5 bytes total
                # Format: 00 00 00 03 [LENGTH]
                _LOGGER.debug("Reading next message header...")
                header = await self._reader.readexactly(5)

                # Validate header - if invalid, try to re-sync
                max_resync_attempts = 50
                for attempt in range(max_resync_attempts):
                    if header[0:4] == b"\x00\x00\x00\x03":
                        # Valid header found
                        if attempt > 0:
                            # Log when we had to re-sync (shouldn't happen often now)
                            _LOGGER.warning(
                                "Had to re-sync message stream after %d attempt(s) - "
                                "garbage bytes not properly drained",
                                attempt
                            )
                        break

                    # Invalid header - try to re-sync by reading one more byte
                    _LOGGER.debug(
                        "Invalid header %s, attempting re-sync (attempt %d/%d)",
                        header[0:4].hex(),
                        attempt + 1,
                        max_resync_attempts,
                    )
                    # Shift header left by 1 byte and read 1 new byte
                    next_byte = await self._reader.readexactly(1)
                    header = header[1:] + next_byte
                else:
                    # Failed to re-sync after max attempts
                    _LOGGER.error("Failed to re-sync after %d attempts", max_resync_attempts)
                    raise JebaoConnectionError("Lost message synchronization")

                # Extract length byte (byte 4) - indicates bytes that follow
                length = header[4]
                _LOGGER.debug(f"Valid header found, reading {length} more bytes")

                # Read the remaining bytes based on length field
                remaining = await self._reader.readexactly(length)

                result = header + remaining
                msg_type = result[7] if len(result) > 7 else 0

                # Only message type 0x00 (extended status responses) has 129-byte padding
                # All other message types have no padding
                if msg_type == MSG_EXTENDED_DATA:
                    # Read fixed padding (129 bytes) that pump sends for extended responses
                    # The pump uses fixed-frame protocol with padding (0xee or 0x00)
                    padding = await self._reader.readexactly(129)

                    # Validate padding - should be mostly 0xee or 0x00 patterns
                    # Check first 4 bytes and last byte
                    padding_start = padding[0:4]
                    padding_end = padding[-1]

                    # Valid patterns: eeeeeeee or 00000000 at start, 00 at end
                    is_valid_padding = (
                        (padding_start == b'\xee\xee\xee\xee' or padding_start == b'\x00\x00\x00\x00')
                        and padding_end == 0x00
                    )

                    if not is_valid_padding:
                        _LOGGER.error(
                            "Invalid padding detected! Start: %s, End: 0x%02x - possible protocol corruption",
                            padding_start.hex(),
                            padding_end
                        )
                        # Drain any additional garbage as fallback
                        await self._drain_garbage_bytes(timeout=0.05)
                        raise JebaoConnectionError(
                            f"Protocol error: invalid padding (start={padding_start.hex()}, end=0x{padding_end:02x})"
                        )

                    _LOGGER.debug(
                        "Read 129-byte padding frame for msg type 0x00 (start: %s, end: 0x%02x)",
                        padding_start.hex(),
                        padding_end
                    )

                _LOGGER.debug(
                    f"Message complete: {len(result)} bytes, type=0x{msg_type:02x}"
                )
                return result
            except asyncio.IncompleteReadError as err:
                _LOGGER.error("IncompleteReadError - connection closed by peer")
                # Mark connection as closed so reconnection can happen
                await self.disconnect()
                raise JebaoConnectionError("Connection closed") from err
            except JebaoConnectionError:
                raise
            except Exception as err:
                _LOGGER.error(f"Read exception: {type(err).__name__}: {err}")
                raise JebaoConnectionError(f"Read failed: {err}") from err

    @staticmethod
    def parse_status(data: bytes) -> Optional[dict]:
        """Parse status data from response.

        Args:
            data: Raw response data

        Returns:
            Dictionary with state and speed, or None if parsing failed
        """
        # For MDP-20000, state and speed are at fixed positions
        # Byte 10: State (0x10=OFF, 0x11=ON, 0x15=FEED, 0x19=PROGRAM)
        # Byte 11: Speed percentage (0-100)
        if len(data) >= 12:
            state_byte = data[10]
            speed = data[11]

            return {
                "state": state_byte,
                "speed": speed,
            }

        return None
