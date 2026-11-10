"""High-level support for the four-channel Jebao MD-4.4 doser."""

from collections.abc import Sequence
from typing import Optional

from .const import COMMAND_TIMEOUT, DEFAULT_TIMEOUT, MODEL_MD44
from .exceptions import JebaoConnectionError
from .md44_protocol import MD44Dose, MD44Protocol, MD44Status


class MD44Channel:
    """One physical dosing head on an MD-4.4."""

    def __init__(self, device: "MD44Device", number: int) -> None:
        self.device = device
        self.number = number

    @property
    def is_running(self) -> bool:
        return bool(self.device.status and self.device.status.is_running(self.number))

    @property
    def is_schedule_enabled(self) -> bool:
        return bool(self.device.status and self.device.status.is_schedule_enabled(self.number))

    async def start(self, timeout: float = COMMAND_TIMEOUT) -> None:
        self.device._status = await self.device.protocol.set_head_power(self.number, True, timeout)

    async def stop(self, timeout: float = COMMAND_TIMEOUT) -> None:
        self.device._status = await self.device.protocol.set_head_power(self.number, False, timeout)

    async def set_schedule(
        self,
        day_interval: int,
        doses: Sequence[MD44Dose],
        enabled: bool = True,
        timeout: float = COMMAND_TIMEOUT,
    ) -> None:
        """Replace this head's native schedule and optionally enable it."""
        self.device._status = await self.device.protocol.set_schedule(
            self.number, day_interval, doses, enabled, timeout
        )


class MD44Device:
    """Jebao MD-4.4 dosing pump with four independently controlled heads."""

    def __init__(self, host: str, port: int = 12416, device_id: Optional[str] = None) -> None:
        self.host = host
        self.port = port
        self.device_id = device_id
        self.model = MODEL_MD44
        self.protocol = MD44Protocol(host, port)
        self.channels: tuple[MD44Channel, ...] = tuple(
            MD44Channel(self, number) for number in range(1, 5)
        )
        self._status: Optional[MD44Status] = None

    @property
    def is_connected(self) -> bool:
        return self.protocol.is_connected

    @property
    def status(self) -> Optional[MD44Status]:
        return self._status

    async def connect(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        await self.protocol.connect(timeout)
        await self.update()

    async def disconnect(self) -> None:
        await self.protocol.disconnect()

    async def update(self, timeout: float = COMMAND_TIMEOUT) -> MD44Status:
        if not self.is_connected:
            raise JebaoConnectionError("Not connected")
        self._status = await self.protocol.request_status(timeout)
        return self._status

    async def __aenter__(self) -> "MD44Device":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
