"""MDP-20000 variable-speed circulation pump."""
import logging
from datetime import datetime
from typing import Optional, Sequence

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
from .datapoints import (
    MAX_FEED_TIME,
    MIN_FEED_TIME,
    ScheduleSlot,
    encode_clock,
    encode_write,
    schedule_state,
    schedule_values,
)
from .device import JebaoDevice
from .exceptions import JebaoCommandError, JebaoInvalidStateError

_LOGGER = logging.getLogger(__name__)


class MDP20000Device(JebaoDevice):
    """MDP-20000 variable-speed circulation pump.

    This class provides control for the Jebao MDP-20000 pump with features:
    - On/Off control
    - Variable speed (30-100%)
    - Feed mode (temporary pause, 1-60 minutes)
    - Program mode detection and exit
    - Schedule (48 slots), clock and fault read-out (see datapoints.py)

    Also used for the MDP-15000, MDP-3500 and MDP-2500, which share the
    MDP-20000's datapoints.
    """

    def __init__(
        self,
        host: str,
        port: int = 12416,
        device_id: Optional[str] = None,
        passcode: Optional[bytes] = None,
        local_port: Optional[int] = None,
    ):
        """Initialize MDP-20000 device.

        Args:
            host: Device IP address
            port: TCP port (default 12416)
            device_id: Device ID (from discovery)
            passcode: Passcode saved from an earlier session (``device.passcode``)
            local_port: Fixed TCP source port for this pump
        """
        super().__init__(
            host, port, device_id, MODEL_MDP20000, passcode=passcode, local_port=local_port
        )
        self._feed_duration: int = 1  # Last configured feed duration

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

    async def _leave_program_mode(self, timeout: float) -> None:
        """A manual command was asked for: leave the pump's own schedule first.

        Uses the state from the last update; HA no longer forces manual mode at
        startup, so a pump can keep running its schedule until someone takes
        manual control.
        """
        if self.is_program_mode:
            await self.ensure_manual_mode(timeout)

    async def turn_on(self, timeout: float = COMMAND_TIMEOUT) -> None:
        """Turn pump on.

        Args:
            timeout: Command timeout

        Raises:
            JebaoCommandError: Command failed

        Note:
            Session setup is retried; the command itself is sent at most once.
        """
        await self._leave_program_mode(timeout)
        _LOGGER.info("%s Turning on pump", self.device_identifier)

        await self._protocol.send_control_command(
            CommandOpcode.TURN_ON_OFF,
            opcode2=0x01,  # 0x01 = ON
            timeout=timeout,
        )

        # Note: Call update() after a delay to see new state
        # The pump needs time to process the command

    async def turn_off(self, timeout: float = COMMAND_TIMEOUT) -> None:
        """Turn pump off.

        Args:
            timeout: Command timeout

        Raises:
            JebaoCommandError: Command failed

        Note:
            Session setup is retried; the command itself is sent at most once.
        """
        await self._leave_program_mode(timeout)
        _LOGGER.info("%s Turning off pump", self.device_identifier)

        await self._protocol.send_control_command(
            CommandOpcode.TURN_ON_OFF,
            opcode2=0x00,  # 0x00 = OFF
            timeout=timeout,
        )

        # Note: Call update() after a delay to see new state

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
            Session setup is retried; the command itself is sent at most once.
        """
        if not MIN_SPEED <= percentage <= MAX_SPEED:
            raise ValueError(
                f"Speed must be between {MIN_SPEED} and {MAX_SPEED}, got {percentage}"
            )

        await self._leave_program_mode(timeout)
        _LOGGER.info("%s Setting speed to %d%%", self.device_identifier, percentage)

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
            minutes: Duration in minutes (1-60)
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

    async def start_feed(
        self, minutes: Optional[int] = None, timeout: float = COMMAND_TIMEOUT
    ) -> None:
        """Start feed mode.

        Feed mode temporarily stops the pump for the specified duration,
        then automatically resumes at the previous speed.

        Args:
            minutes: Duration in minutes (1-60). If None, uses the pump's own FeedTime.
            timeout: Command timeout

        Raises:
            ValueError: Invalid duration
            JebaoCommandError: Command failed
            JebaoInvalidStateError: Device not in valid state for feed mode

        Note:
            Session setup is retried; the command itself is sent at most once.
        """
        await self.update()
        if self.is_program_mode:
            await self.ensure_manual_mode(timeout)
        if self.is_feed_mode:
            raise JebaoInvalidStateError("Already in feed mode")

        if minutes is not None:
            if not MIN_FEED_DURATION <= minutes <= MAX_FEED_DURATION:
                raise ValueError(
                    f"Feed duration must be between {MIN_FEED_DURATION} and "
                    f"{MAX_FEED_DURATION} minutes, got {minutes}"
                )
            self._feed_duration = minutes
        else:
            self._feed_duration = self._current_feed_time()

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
            Session setup is retried; the command itself is sent at most once.
        """
        # Verify device is in feed mode
        await self.update()
        if not self.is_feed_mode:
            _LOGGER.warning("Device not in feed mode, ignoring cancel_feed")
            return

        if resume_speed is None:
            resume_speed = self._speed or MAX_SPEED
        # CANCEL_FEED also writes FeedTime; resend the pump's own value
        self._feed_duration = self._current_feed_time()

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

    def _current_feed_time(self) -> int:
        """The pump's own FeedTime if known (so commands keep it), else the last one set."""
        if self._status is not None and MIN_FEED_TIME <= self._status.feed_time <= MAX_FEED_TIME:
            return self._status.feed_time
        return self._feed_duration

    async def write(self, timeout: float = COMMAND_TIMEOUT, **changes) -> None:
        """Set datapoints by name (see ``datapoints.encode_write``) and refresh state.

        Example: ``await pump.write(motor_speed=60, timer_on=False)``

        The write is sent at most once; only opening the session is retried.

        Raises:
            KeyError/ValueError: Unknown datapoint or value out of range
            JebaoError: Communication failed
        """
        payload = encode_write(changes)
        _LOGGER.info("%s Writing %s", self.device_identifier, ", ".join(sorted(changes)))
        status = await self._protocol.send_write(payload, timeout)
        if not self._apply_status_frame(status):
            await self.update()

    async def set_feed_time(self, minutes: int, timeout: float = COMMAND_TIMEOUT) -> None:
        """Set how long feed mode pauses the pump (FeedTime), without starting it."""
        if not MIN_FEED_TIME <= minutes <= MAX_FEED_TIME:
            raise ValueError(
                f"Feed time must be between {MIN_FEED_TIME} and {MAX_FEED_TIME} "
                f"minutes, got {minutes}"
            )
        # The app resends the current FeedSwitch with it
        feeding = self._status.feed_switch if self._status is not None else False
        await self.write(timeout=timeout, feed_time=minutes, feed_switch=feeding)
        self._feed_duration = minutes

    async def set_schedule_enabled(
        self,
        enabled: bool,
        now: Optional[datetime] = None,
        timeout: float = COMMAND_TIMEOUT,
        _slots: Optional[Sequence[Optional[ScheduleSlot]]] = None,
    ) -> None:
        """Turn the pump's own schedule (TimerON, the app's Program) on or off.

        As the app does: turning it on ends feed mode and sets AutoMode/AutoGears
        to the slot active at ``now`` (default: local time); turning it off keeps
        the current speed.
        """
        if self._status is None:
            await self.update()
        status = self._status
        if status is None:
            raise JebaoCommandError("Pump status has no schedule datapoints")
        if enabled:
            changes = {"timer_on": True, "feed_switch": False}
            slots = status.schedule if _slots is None else _slots
            changes.update(schedule_state(slots, now or datetime.now()))
        else:
            changes = {"timer_on": False, "motor_speed": status.motor_speed}
        await self.write(timeout=timeout, **changes)

    async def set_schedule(
        self,
        slots: Sequence[ScheduleSlot],
        enabled: Optional[bool] = None,
        now: Optional[datetime] = None,
        timeout: float = COMMAND_TIMEOUT,
    ) -> None:
        """Replace the whole schedule (up to 48 slots; the rest are cleared).

        Args:
            slots: the new schedule entries (not overlapping, speeds 30-100)
            enabled: then turn the schedule on or off (None leaves it as it is)
            now: the time used to pick the active slot (default: local time)

        Raises:
            ValueError: the schedule isn't one the app would save
        """
        slots = list(slots)
        await self.write(timeout=timeout, **schedule_values(slots))
        if enabled is None:
            enabled = self._status is not None and self._status.timer_on
            if not enabled:
                return
        # Like the app: with the schedule on, also tell the pump which slot is active
        # (from the slots just written, in case the pump's report lags behind)
        await self.set_schedule_enabled(enabled, now=now, timeout=timeout, _slots=slots)

    async def set_clock(
        self, when: Optional[datetime] = None, timeout: float = COMMAND_TIMEOUT
    ) -> None:
        """Set the pump's clock, which its schedule runs on (default: now, local time)."""
        await self.write(timeout=timeout, **encode_clock(when or datetime.now()))

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"MDP20000Device(host={self.host!r}, "
            f"device_id={self.device_id!r}, "
            f"state={self._state.name if self._state else 'Unknown'}, "
            f"speed={self._speed}%)"
        )
