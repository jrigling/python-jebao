"""MD-4.5 5-channel WiFi/BLE dosing pump."""
import logging
import time
from typing import List, Optional

from .const import (
    COMMAND_TIMEOUT,
    DEFAULT_TIMEOUT,
    MD45_NUM_CHANNELS,
    MODEL_MD45,
    MSG_DATA_RESPONSE_SIMPLE,
)
from .device import JebaoDevice
from .exceptions import JebaoCommandError
from .protocol import split_frame
from .retry import async_retry

_LOGGER = logging.getLogger(__name__)


class MD45Device(JebaoDevice):
    """Jebao MD-4.5 5-channel WiFi/BLE dosing pump (GizWits key ``5ab6019f...``).

    Five independent dosing channels, each switched on or off. There is no
    speed, feed mode or program mode, so the pump-oriented properties of
    ``JebaoDevice`` (``state``, ``speed``, ``is_feed_mode``...) stay ``None``
    / False; use ``is_channel_on`` and ``channel_states`` instead.

    Status: the byte after the status frame's first three payload bytes is a
    bitmask. Bit 0 is set whenever the pump is powered (the main switch) and
    bit N is channel N, matching the ``1 << N`` mask used to command it.
    Confirmed by capturing the app's status pushes after on/off commands on
    all five channels.

    The pump takes about a second to reflect a command in its status, so an
    ``update()`` right after ``turn_on_channel()`` can still report the old
    state. The command methods set the expected state optimistically.
    """

    def __init__(
        self,
        host: str,
        port: int = 12416,
        device_id: Optional[str] = None,
        passcode: Optional[bytes] = None,
        local_port: Optional[int] = None,
    ):
        """Initialize MD-4.5 device.

        Args:
            host: Device IP address
            port: TCP port (default 12416)
            device_id: Device ID (from discovery)
            passcode: Passcode saved from an earlier session (``device.passcode``)
            local_port: Fixed TCP source port for this pump
        """
        super().__init__(
            host, port, device_id, MODEL_MD45, passcode=passcode, local_port=local_port
        )
        self._channel_bits: Optional[int] = None

    @property
    def channel_bits(self) -> Optional[int]:
        """Raw channel bitmask from the last status (bit 0 main switch, bit N channel N)."""
        return self._channel_bits

    @property
    def channel_states(self) -> List[bool]:
        """On/off state of channels 1-5, as of the last update or command."""
        return [self.is_channel_on(ch) for ch in range(1, MD45_NUM_CHANNELS + 1)]

    def is_channel_on(self, channel: int) -> bool:
        """Whether ``channel`` (1-5) is on, as of the last update or command."""
        self._validate_channel(channel)
        return self._channel_bits is not None and bool(self._channel_bits & (1 << channel))

    @staticmethod
    def parse_channel_bits(frame: bytes) -> Optional[int]:
        """Extract the channel bitmask from a raw status frame (cmd 0x0091)."""
        try:
            cmd, payload = split_frame(frame)
        except JebaoCommandError:
            return None
        if cmd != MSG_DATA_RESPONSE_SIMPLE or len(payload) < 4:
            return None
        return payload[3]

    @async_retry(max_attempts=3, delay=0.5)
    async def update(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        """Poll the pump and refresh all five channels.

        Raises:
            JebaoConnectionError / JebaoTimeoutError: pump unreachable
            JebaoCommandError: status frame couldn't be parsed
        """
        response = await self._protocol.request_status(timeout)
        bits = self.parse_channel_bits(response)
        if bits is None:
            raise JebaoCommandError(f"Unparseable status response: {response[:24].hex()}")
        self._channel_bits = bits
        self._last_update = time.time()
        _LOGGER.debug(
            "%s Status updated: bits=0x%02x channels=%s",
            self.device_identifier, bits, self.channel_states,
        )

    async def turn_on_channel(self, channel: int, timeout: float = COMMAND_TIMEOUT) -> None:
        """Turn a dosing channel (1-5) on. Sent at most once.

        Raises:
            ValueError: Invalid channel number
        """
        await self._set_channel(channel, True, timeout)

    async def turn_off_channel(self, channel: int, timeout: float = COMMAND_TIMEOUT) -> None:
        """Turn a dosing channel (1-5) off. Sent at most once.

        Raises:
            ValueError: Invalid channel number
        """
        await self._set_channel(channel, False, timeout)

    async def _set_channel(self, channel: int, on: bool, timeout: float) -> None:
        self._validate_channel(channel)
        _LOGGER.info(
            "%s Turning %s channel %d", self.device_identifier, "on" if on else "off", channel
        )
        await self._protocol.send_channel_command(channel, on, timeout=timeout)
        bit = 1 << channel
        bits = self._channel_bits or 0
        self._channel_bits = (bits | bit) if on else (bits & ~bit)

    @staticmethod
    def _validate_channel(channel: int) -> None:
        if not 1 <= channel <= MD45_NUM_CHANNELS:
            raise ValueError(f"Channel must be 1-{MD45_NUM_CHANNELS}, got {channel}")

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"MD45Device(host={self.host!r}, device_id={self.device_id!r}, "
            f"channels={self.channel_states})"
        )
