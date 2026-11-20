"""ECP-150M wave pump (read-only)."""
import logging
import time
from typing import Optional

from .const import DEFAULT_TIMEOUT, MODEL_ECP150M
from .datapoints import ECP150M_VALUES_SIZE, ECP150MStatus
from .device import JebaoDevice, state_from_flags
from .exceptions import JebaoCommandError
from .retry import async_retry

_LOGGER = logging.getLogger(__name__)

READ_REPLY_ACTION = 0x03
READ_REPLY_HEADER = 5  # sequence (4 bytes) + action


class ECP150MDevice(JebaoDevice):
    """Jebao ECP-150M wave pump (GizWits key ``1d8c63ea...``). Read-only for now.

    ``update()`` reads every datapoint with a GizWits read (cmd 0x0093, action
    0x02) and decodes it into ``status`` (an ``ECP150MStatus``; see
    ``datapoints.py`` for the layout). The low bits of the flag word mean the
    same as the MDP state byte, so ``state``, ``is_on``, ``is_feed_mode`` and
    ``is_program_mode`` work as on the other pumps, and ``speed`` is the flow
    percentage.

    No commands are sent: writes (on/off, flow, frequency) need checking on
    the hardware first.
    """

    def __init__(
        self,
        host: str,
        port: int = 12416,
        device_id: Optional[str] = None,
        passcode: Optional[bytes] = None,
        local_port: Optional[int] = None,
    ):
        """Initialize ECP-150M device.

        Args:
            host: Device IP address
            port: TCP port (default 12416)
            device_id: Device ID (from discovery)
            passcode: Passcode saved from an earlier session (``device.passcode``)
            local_port: Fixed TCP source port for this pump
        """
        super().__init__(
            host, port, device_id, MODEL_ECP150M, passcode=passcode, local_port=local_port
        )
        self._ecp_status: Optional[ECP150MStatus] = None

    @property
    def status(self) -> Optional[ECP150MStatus]:  # type: ignore[override]
        """Every datapoint from the last read, or None before the first update."""
        return self._ecp_status

    @staticmethod
    def parse_read_reply(payload: bytes) -> Optional[ECP150MStatus]:
        """Decode the payload of a 0x0094 read reply; None if it isn't one."""
        if (
            len(payload) < READ_REPLY_HEADER + ECP150M_VALUES_SIZE
            or payload[4] != READ_REPLY_ACTION
        ):
            return None
        return ECP150MStatus.from_values(payload[READ_REPLY_HEADER:])

    @async_retry(max_attempts=3, delay=0.5)
    async def update(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        """Read the pump's status.

        Raises:
            JebaoConnectionError / JebaoTimeoutError: pump unreachable
            JebaoCommandError: the reply couldn't be decoded
        """
        payload = await self._protocol.request_datapoints(timeout)
        status = self.parse_read_reply(payload)
        if status is None:
            raise JebaoCommandError(f"Unparseable ECP-150M status: {payload[:24].hex()}")
        self._ecp_status = status
        self._flags = status.flags
        self._state = state_from_flags(status.flags)
        self._speed = status.flow
        self._last_update = time.time()
        _LOGGER.debug(
            "%s Status updated: state=%s (0x%04x), mode=%s, flow=%d%%, frequency=%d",
            self.device_identifier,
            self._state.name,
            status.flags,
            status.mode.name,
            status.flow,
            status.frequency,
        )

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"ECP150MDevice(host={self.host!r}, device_id={self.device_id!r}, "
            f"state={self._state!r})"
        )
