"""MD-4.5 5-channel WiFi/BLE dosing pump."""
import logging
from typing import Optional

from .const import COMMAND_TIMEOUT, MD45_NUM_CHANNELS, MODEL_MD45
from .device import JebaoDevice
from .exceptions import JebaoCommandError
from .retry import async_retry

_LOGGER = logging.getLogger(__name__)


class MD45Device(JebaoDevice):
    """Jebao MD-4.5 5-channel WiFi/BLE dosing pump.

    Unlike the MDP-20000/MD-4.4 (a single variable-speed pump), the MD-4.5
    exposes 5 independent dosing channels, each started/stopped
    independently. There is no shared "main pump" state, speed control, or
    feed mode - just per-channel on/off.

    Status is read via the same generic status poll used by the other
    models (``JebaoProtocol.request_status``); the response's live-state
    byte is a bitmask where each channel's bit matches its command mask
    (``1 << channel``) OR'd with a constant 0x01 baseline bit. This was
    confirmed by capturing the real app's own status pushes immediately
    after each of its channel on/off commands, across all 5 channels.

    Note: the device takes roughly a second to reflect a just-sent command
    in its status - polling immediately after turn_on_channel()/
    turn_off_channel() can return the pre-command state. Wait briefly (or
    just trust the optimistic state set by those methods) before calling
    update() if you need to confirm a change took effect.
    """

    def __init__(self, host: str, port: int = 12416, device_id: Optional[str] = None):
        """Initialize MD-4.5 device.

        Args:
            host: Device IP address
            port: TCP port (default 12416)
            device_id: Device ID (from discovery)
        """
        super().__init__(host, port, device_id, MODEL_MD45)
        self._channel_state: list[bool] = [False] * (MD45_NUM_CHANNELS + 1)  # 1-indexed

    def is_channel_on(self, channel: int) -> bool:
        """Return the channel's state as of the last update()/command."""
        self._validate_channel(channel)
        return self._channel_state[channel]

    async def update(self, timeout: float = COMMAND_TIMEOUT) -> None:
        """Poll the device and refresh all 5 channels' on/off state.

        Overrides JebaoDevice.update() - the base class's status parser
        assumes the MDP-20000's single state/speed byte pair, which doesn't
        apply here.

        The pump occasionally emits a short unsolicited heartbeat-like frame
        (9 bytes, type 0x62, undocumented) that can race with the reply to
        our status request - most often right after connecting. When that
        happens ``request_status()`` returns that frame instead (raising
        JebaoCommandError since it's too short to be a real status response).
        This is retried a couple of times rather than surfaced, since it's
        just message-ordering noise, not a real failure.
        """
        live_bits = None
        for attempt in range(3):
            try:
                response = await self._protocol.request_status(timeout)
            except JebaoCommandError as err:
                _LOGGER.debug(
                    "%s Status request attempt %d got a short/unexpected frame (%s), retrying",
                    self.device_identifier, attempt + 1, err,
                )
                continue
            live_bits = self._parse_live_bits(response)
            if live_bits is not None:
                break

        if live_bits is None:
            _LOGGER.warning("%s Failed to parse status response", self.device_identifier)
            return

        for channel in range(1, MD45_NUM_CHANNELS + 1):
            self._channel_state[channel] = bool(live_bits & (1 << channel))

    @staticmethod
    def _parse_live_bits(response: bytes) -> Optional[int]:
        """Extract the live per-channel state byte from a status response.

        The response is a type-0x00 frame whose payload starts with a
        sub-type marker (0x91) at byte 8; the live-state byte is 4 bytes
        after that marker, i.e. absolute offset 12.
        """
        if len(response) < 13 or response[8] != 0x91:
            return None
        return response[12]

    @async_retry(max_attempts=3, delay=0.5)
    async def turn_on_channel(self, channel: int, timeout: float = COMMAND_TIMEOUT) -> None:
        """Turn a dosing channel on.

        Args:
            channel: Channel number (1-5)
            timeout: Command timeout

        Raises:
            ValueError: Invalid channel number
        """
        self._validate_channel(channel)
        _LOGGER.info("%s Turning on channel %d", self.device_identifier, channel)
        await self._protocol.send_channel_command(channel, True, timeout=timeout)
        self._channel_state[channel] = True

    @async_retry(max_attempts=3, delay=0.5)
    async def turn_off_channel(self, channel: int, timeout: float = COMMAND_TIMEOUT) -> None:
        """Turn a dosing channel off.

        Args:
            channel: Channel number (1-5)
            timeout: Command timeout

        Raises:
            ValueError: Invalid channel number
        """
        self._validate_channel(channel)
        _LOGGER.info("%s Turning off channel %d", self.device_identifier, channel)
        await self._protocol.send_channel_command(channel, False, timeout=timeout)
        self._channel_state[channel] = False

    @staticmethod
    def _validate_channel(channel: int) -> None:
        if not 1 <= channel <= MD45_NUM_CHANNELS:
            raise ValueError(f"Channel must be 1-{MD45_NUM_CHANNELS}, got {channel}")

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"MD45Device(host={self.host!r}, device_id={self.device_id!r}, "
            f"channels={self._channel_state[1:]})"
        )
