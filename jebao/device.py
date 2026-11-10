"""Base device class for Jebao pumps."""
import asyncio
import logging
from typing import Optional

from .const import DEFAULT_TIMEOUT, DeviceState
from .exceptions import JebaoCommandError
from .protocol import JebaoProtocol
from .retry import async_retry

_LOGGER = logging.getLogger(__name__)


class JebaoDevice:
    """Base class for Jebao devices."""

    def __init__(
        self,
        host: str,
        port: int = 12416,
        device_id: Optional[str] = None,
        model: Optional[str] = None,
    ):
        """Initialize device.

        Args:
            host: Device IP address
            port: TCP port (default 12416)
            device_id: Device ID (from discovery)
            model: Device model
        """
        self.port = port
        self.device_id = device_id
        self.model = model

        self._protocol = JebaoProtocol(host, port, device_id=device_id)
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
        """Get current device state."""
        return self._state

    @property
    def speed(self) -> Optional[int]:
        """Get current speed percentage (30-100)."""
        return self._speed

    @property
    def is_on(self) -> bool:
        """Check if device is on (running or in feed mode)."""
        return self._state in (DeviceState.ON, DeviceState.FEED)

    @property
    def is_off(self) -> bool:
        """Check if device is off."""
        return self._state == DeviceState.OFF

    @property
    def is_feed_mode(self) -> bool:
        """Check if device is in feed mode."""
        return self._state == DeviceState.FEED

    @property
    def is_program_mode(self) -> bool:
        """Check if device is in program mode."""
        return self._state == DeviceState.PROGRAM

    @property
    def is_manual_mode(self) -> bool:
        """Check if device is in manual mode (ON or OFF)."""
        return self._state in (DeviceState.ON, DeviceState.OFF)

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

        try:
            state = DeviceState(status["state"])
        except ValueError as err:
            raise JebaoCommandError(
                f"Unknown device state 0x{status['state']:02x}"
            ) from err

        self._state = state
        self._speed = status["speed"]
        self._last_update = time.time()
        _LOGGER.debug(
            "%s Status updated: state=%s, speed=%d%%",
            self.device_identifier,
            self._state.name,
            self._speed,
        )

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()
