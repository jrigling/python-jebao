"""MDP-20000 variable-speed circulation pump."""
import logging
from typing import Optional

from .const import (
    COMMAND_TIMEOUT,
    CommandOpcode,
    DeviceState,
    MAX_FEED_DURATION,
    MAX_SPEED,
    MIN_FEED_DURATION,
    MIN_SPEED,
    MODEL_MDP20000,
)
from .device import JebaoDevice
from .exceptions import JebaoCommandError, JebaoInvalidStateError
from .retry import async_retry

_LOGGER = logging.getLogger(__name__)


class MDP20000Device(JebaoDevice):
    """MDP-20000 variable-speed circulation pump.

    This class provides control for the Jebao MDP-20000 pump with features:
    - On/Off control
    - Variable speed (30-100%)
    - Feed mode (temporary pause, 1-10 minutes)
    - Program mode detection and exit
    """

    def __init__(self, host: str, port: int = 12416, device_id: Optional[str] = None):
        """Initialize MDP-20000 device.

        Args:
            host: Device IP address
            port: TCP port (default 12416)
            device_id: Device ID (from discovery)
        """
        super().__init__(host, port, device_id, MODEL_MDP20000)
        self._feed_duration: int = 1  # Last configured feed duration

    @async_retry(max_attempts=3, delay=0.5)
    async def ensure_manual_mode(self, timeout: float = COMMAND_TIMEOUT) -> None:
        """Ensure device is in manual mode.

        If device is in Program mode, exit to manual mode.
        This should be called on connection before allowing user control.

        Args:
            timeout: Command timeout

        Raises:
            JebaoCommandError: Command failed
        """
        await self.update()

        if self.is_program_mode:
            _LOGGER.warning("Device in Program mode, switching to Manual")
            current_speed = self._speed or MAX_SPEED

            await self._protocol.send_control_command(
                CommandOpcode.EXIT_PROGRAM,
                opcode2=0x00,
                param1=current_speed,
                param2=0x00,
                timeout=timeout,
            )

            # Verify state changed
            await self.update()
            if self.is_program_mode:
                raise JebaoCommandError("Failed to exit Program mode")

            _LOGGER.info("Successfully exited Program mode")
        else:
            _LOGGER.debug("Device already in manual mode")

    @async_retry(max_attempts=3, delay=0.5)
    async def turn_on(self, timeout: float = COMMAND_TIMEOUT) -> None:
        """Turn pump on.

        Args:
            timeout: Command timeout

        Raises:
            JebaoCommandError: Command failed

        Note:
            This method automatically retries up to 3 times on transient
            failures (garbage bytes, connection issues).
        """
        _LOGGER.info("Turning on pump")

        await self._protocol.send_control_command(
            CommandOpcode.TURN_ON_OFF,
            opcode2=0x01,  # 0x01 = ON
            timeout=timeout,
        )

        # Note: Call update() after a delay to see new state
        # The pump needs time to process the command

    @async_retry(max_attempts=3, delay=0.5)
    async def turn_off(self, timeout: float = COMMAND_TIMEOUT) -> None:
        """Turn pump off.

        Args:
            timeout: Command timeout

        Raises:
            JebaoCommandError: Command failed

        Note:
            This method automatically retries up to 3 times on transient
            failures (garbage bytes, connection issues).
        """
        _LOGGER.info("Turning off pump")

        await self._protocol.send_control_command(
            CommandOpcode.TURN_ON_OFF,
            opcode2=0x00,  # 0x00 = OFF
            timeout=timeout,
        )

        # Note: Call update() after a delay to see new state

    @async_retry(max_attempts=3, delay=0.5)
    async def set_speed(
        self, percentage: int, timeout: float = COMMAND_TIMEOUT
    ) -> None:
        """Set pump speed.

        Args:
            percentage: Speed percentage (30-100)
            timeout: Command timeout

        Raises:
            ValueError: Invalid speed value
            JebaoCommandError: Command failed

        Note:
            This method automatically retries up to 3 times on transient
            failures (garbage bytes, connection issues).
        """
        if not MIN_SPEED <= percentage <= MAX_SPEED:
            raise ValueError(
                f"Speed must be between {MIN_SPEED} and {MAX_SPEED}, got {percentage}"
            )

        _LOGGER.info("Setting speed to %d%%", percentage)

        await self._protocol.send_control_command(
            CommandOpcode.SET_SPEED,
            opcode2=0x00,
            param1=percentage,
            param2=0x00,
            timeout=timeout,
        )

        # Note: Call update() after a delay to see new state

    async def set_feed_duration(
        self, minutes: int, timeout: float = COMMAND_TIMEOUT
    ) -> None:
        """Set feed mode duration.

        This configures the feed timer but does not start feed mode.
        Use start_feed() to begin feed mode.

        Args:
            minutes: Duration in minutes (1-10)
            timeout: Command timeout

        Raises:
            ValueError: Invalid duration
            JebaoCommandError: Command failed
        """
        if not MIN_FEED_DURATION <= minutes <= MAX_FEED_DURATION:
            raise ValueError(
                f"Feed duration must be between {MIN_FEED_DURATION} and "
                f"{MAX_FEED_DURATION} minutes, got {minutes}"
            )

        _LOGGER.info("Setting feed duration to %d minute(s)", minutes)

        await self._protocol.send_control_command(
            CommandOpcode.SET_FEED_DURATION,
            opcode2=0x00,
            param1=0x00,
            param2=minutes,
            timeout=timeout,
        )

        self._feed_duration = minutes

    @async_retry(max_attempts=3, delay=1.0, backoff=1.5)
    async def start_feed(
        self, minutes: Optional[int] = None, timeout: float = COMMAND_TIMEOUT
    ) -> None:
        """Start feed mode.

        Feed mode temporarily stops the pump for the specified duration,
        then automatically resumes at the previous speed.

        Args:
            minutes: Duration in minutes (1-10). If None, uses last configured duration.
            timeout: Command timeout

        Raises:
            ValueError: Invalid duration
            JebaoCommandError: Command failed
            JebaoInvalidStateError: Device not in valid state for feed mode

        Note:
            This method automatically retries up to 3 times on transient
            failures (garbage bytes, connection issues) with longer delays
            due to increased garbage accumulation risk.
        """
        # Verify device is in manual mode
        await self.update()
        if not self.is_manual_mode:
            raise JebaoInvalidStateError(
                f"Cannot start feed from state {self._state.name}"
            )

        if minutes is not None:
            if not MIN_FEED_DURATION <= minutes <= MAX_FEED_DURATION:
                raise ValueError(
                    f"Feed duration must be between {MIN_FEED_DURATION} and "
                    f"{MAX_FEED_DURATION} minutes, got {minutes}"
                )
            self._feed_duration = minutes

        _LOGGER.info("Starting feed mode for %d minute(s)", self._feed_duration)

        await self._protocol.send_control_command(
            CommandOpcode.START_FEED,
            opcode2=0x04,
            param1=0x00,
            param2=self._feed_duration,
            timeout=timeout,
        )

        # Update status
        await self.update()

    @async_retry(max_attempts=3, delay=1.0, backoff=1.5)
    async def cancel_feed(
        self, resume_speed: Optional[int] = None, timeout: float = COMMAND_TIMEOUT
    ) -> None:
        """Cancel feed mode and resume pump.

        Args:
            resume_speed: Speed to resume at (30-100). If None, uses current speed.
            timeout: Command timeout

        Raises:
            ValueError: Invalid speed
            JebaoCommandError: Command failed
            JebaoInvalidStateError: Device not in feed mode

        Note:
            This method automatically retries up to 3 times on transient
            failures (garbage bytes, connection issues) with longer delays
            due to increased garbage accumulation risk.
        """
        # Verify device is in feed mode
        await self.update()
        if not self.is_feed_mode:
            _LOGGER.warning("Device not in feed mode, ignoring cancel_feed")
            return

        if resume_speed is None:
            resume_speed = self._speed or MAX_SPEED

        if not MIN_SPEED <= resume_speed <= MAX_SPEED:
            raise ValueError(
                f"Resume speed must be between {MIN_SPEED} and {MAX_SPEED}, "
                f"got {resume_speed}"
            )

        _LOGGER.info("Canceling feed mode, resuming at %d%%", resume_speed)

        await self._protocol.send_control_command(
            CommandOpcode.CANCEL_FEED,
            opcode2=0x00,
            param1=resume_speed,
            param2=self._feed_duration,
            timeout=timeout,
        )

        # Update status
        await self.update()

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"MDP20000Device(host={self.host!r}, "
            f"device_id={self.device_id!r}, "
            f"state={self._state.name if self._state else 'Unknown'}, "
            f"speed={self._speed}%)"
        )
