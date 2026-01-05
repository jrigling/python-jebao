"""Base device class for Jebao pumps."""
import asyncio
import logging
from typing import Optional

from .const import DEFAULT_TIMEOUT, DeviceState
from .exceptions import JebaoConnectionError
from .protocol import JebaoProtocol

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
        self.host = host
        self.port = port
        self.device_id = device_id
        self.model = model

        self._protocol = JebaoProtocol(host, port)
        self._state: Optional[DeviceState] = None
        self._speed: Optional[int] = None
        self._last_update: Optional[float] = None

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
        """Connect to device.

        Args:
            timeout: Connection timeout

        Raises:
            JebaoConnectionError: Connection failed
            JebaoAuthenticationError: Authentication failed
        """
        await self._protocol.connect(timeout)
        _LOGGER.info("Connected to %s (%s)", self.host, self.model or "Unknown")

        # Get initial status
        await self.update()

    async def disconnect(self) -> None:
        """Disconnect from device."""
        await self._protocol.disconnect()
        _LOGGER.info("Disconnected from %s", self.host)

    async def update(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        """Update device status.

        Args:
            timeout: Request timeout

        Raises:
            JebaoConnectionError: Not connected
            JebaoTimeoutError: Request timed out
        """
        import time

        if not self.is_connected:
            raise JebaoConnectionError("Not connected")

        response = await self._protocol.request_status(timeout)
        status = self._protocol.parse_status(response)

        if status:
            self._state = DeviceState(status["state"])
            self._speed = status["speed"]
            self._last_update = time.time()
            _LOGGER.debug(
                "Status updated: state=%s, speed=%d%%",
                self._state.name,
                self._speed,
            )
        else:
            _LOGGER.warning("Failed to parse status response")

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()
