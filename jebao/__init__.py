"""Python library for controlling Jebao aquarium pumps."""

from .device import JebaoDevice
from .mdp20000 import MDP20000Device
from .md45 import MD45Device
from .datapoints import AutoMode, PumpStatus, ScheduleSlot, parse_slots
from .discovery import JebaoDiscovery, DiscoveredDevice, discover_devices, query_device
from .exceptions import (
    JebaoError,
    JebaoConnectionError,
    JebaoAuthenticationError,
    JebaoCommandError,
    JebaoTimeoutError,
)
from .const import (
    DeviceState,
    MODEL_MDP20000,
    MODEL_MD44,
    MODEL_MD45,
)

__version__ = "0.3.1"

__all__ = [
    "JebaoDevice",
    "MDP20000Device",
    "MD45Device",
    "JebaoDiscovery",
    "DiscoveredDevice",
    "discover_devices",
    "query_device",
    "AutoMode",
    "PumpStatus",
    "ScheduleSlot",
    "parse_slots",
    "JebaoError",
    "JebaoConnectionError",
    "JebaoAuthenticationError",
    "JebaoCommandError",
    "JebaoTimeoutError",
    "DeviceState",
    "MODEL_MDP20000",
    "MODEL_MD44",
    "MODEL_MD45",
]
