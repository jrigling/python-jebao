"""Base device class for Jebao pumps."""
import asyncio
import logging
from typing import Optional

from .const import (
    DEFAULT_TIMEOUT,
    STATE_BIT_FEED,
    STATE_BIT_ON,
    STATE_BIT_PROGRAM,
    DeviceState,
)
from .exceptions import JebaoCommandError
from .protocol import JebaoProtocol
from .retry import async_retry

_LOGGER = logging.getLogger(__name__)


def state_from_flags(flags: int) -> DeviceState:
    """Simplify the state byte to a DeviceState (feed > program > on/off)."""
    if flags & STATE_BIT_FEED:
        return DeviceState.FEED
    if flags & STATE_BIT_PROGRAM:
        return DeviceState.PROGRAM
    if flags & STATE_BIT_ON:
        return DeviceState.ON
    return DeviceState.OFF


class JebaoDevice:
    """Base class for Jebao devices."""

    def __init__(
        self,
        host: str,
        port: int = 12416,
        device_id: Optional[str] = None,
        model: Optional[str] = None,
        passcode: Optional[bytes] = None,
        local_port: Optional[int] = None,
    ):
        """Initialize device.

        Args:
            host: Device IP address
            port: TCP port (default 12416)
            device_id: Device ID (from discovery)
            model: Device model
            passcode: Passcode saved from an earlier session (``device.passcode``)
            local_port: Fixed TCP source port for this pump (see protocol docs)
        """
        self.port = port
        self.device_id = device_id
        self.model = model

        self._protocol = JebaoProtocol(
            host, port, device_id=device_id, passcode=passcode, local_port=local_port
        )
        self._flags: Optional[int] = None
        self._state: Optional[DeviceState] = None
        self._speed: Optional[int] = None
        self._last_update: Optional[float] = None

    @property
    def host(self) -> str:
        """Device IP address. Setting it takes effect on the next operation."""
        return self._protocol.host

    @host.setter
    def host(self, value: str) -> None:
        self._protocol.host = value

    @property
    def passcode(self) -> Optional[bytes]:
        """Passcode learned from the pump (save it and pass it back on restart)."""
        return self._protocol.passcode

    @property
    def device_identifier(self) -> str:
        """Get device identifier for logging."""
        if self.device_id:
            return f"[{self.device_id}]"
        return f"[{self.host}]"

    @property
    def is_connected(self) -> bool:
        """Check if device is connected."""
        return self._protocol.is_connected

    @property
    def state(self) -> Optional[DeviceState]:
        """Current state, simplified to one of the DeviceState values.

        Feed wins over Program, which wins over On/Off. The raw flags are in
        ``state_flags``.
        """
        return self._state

    @property
    def state_flags(self) -> Optional[int]:
        """Raw state byte: bit 0 on, bit 2 feed, bit 3 schedule (Program)."""
        return self._flags

    @property
    def speed(self) -> Optional[int]:
        """Get current speed percentage (30-100)."""
        return self._speed

    def _flag(self, bit: int) -> bool:
        return self._flags is not None and bool(self._flags & bit)

    @property
    def is_on(self) -> bool:
        """Whether the pump is switched on (running, or paused for feeding)."""
        return self._flag(STATE_BIT_ON) or self._flag(STATE_BIT_FEED)

    @property
    def is_off(self) -> bool:
        """Check if device is off."""
        return self._flags is not None and not self.is_on

    @property
    def is_feed_mode(self) -> bool:
        """Check if device is in feed mode."""
        return self._flag(STATE_BIT_FEED)

    @property
    def is_program_mode(self) -> bool:
        """Whether the pump is running its own schedule (the app's Program)."""
        return self._flag(STATE_BIT_PROGRAM)

    @property
    def is_manual_mode(self) -> bool:
        """Check if device is in manual mode (not feeding, not on its schedule)."""
        return self._flags is not None and not (self.is_feed_mode or self.is_program_mode)

    async def connect(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        """Verify the device is reachable and fetch initial status.

        No connection is held open afterwards; every operation opens its own
        short-lived session (the pump drops idle sessions after ~10 s).

        Raises:
            JebaoConnectionError: Connection failed
            JebaoAuthenticationError: Authentication failed
            JebaoTimeoutError: Operation timed out
        """
        await self.update(timeout)
        _LOGGER.debug("%s Connected (%s)", self.device_identifier, self.model or "Unknown")

    async def disconnect(self) -> None:
        """Disconnect from device."""
        await self._protocol.disconnect()
        _LOGGER.debug("%s Disconnected", self.device_identifier)

    @async_retry(max_attempts=3, delay=0.5)
    async def update(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        """Update device status.

        Args:
            timeout: Request timeout

        Raises:
            JebaoConnectionError: Not connected
            JebaoTimeoutError: Request timed out
        """
        import time

        response = await self._protocol.request_status(timeout)
        status = self._protocol.parse_status(response)
        if not status:
            raise JebaoCommandError(f"Unparseable status response: {response[:24].hex()}")

        self._flags = status["state"]
        self._state = state_from_flags(self._flags)
        self._speed = status["speed"]
        self._last_update = time.time()
        _LOGGER.debug(
            "%s Status updated: state=%s (0x%02x), speed=%d%%",
            self.device_identifier,
            self._state.name,
            self._flags,
            self._speed,
        )

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()
