"""Python library for controlling Jebao aquarium pumps."""

from .device import JebaoDevice
from .mdp20000 import MDP20000Device
from .md45 import MD45Device
from .ecp150m import ECP150MDevice
from .datapoints import (
    AutoMode,
    ECP150MSegment,
    ECP150MStatus,
    Linkage,
    PumpStatus,
    ScheduleSlot,
    WaveAutoMode,
    WaveMode,
    parse_slots,
)
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
    MODEL_ECP150M,
)

__version__ = "0.3.2"

__all__ = [
    "JebaoDevice",
    "MDP20000Device",
    "MD45Device",
    "ECP150MDevice",
    "JebaoDiscovery",
    "DiscoveredDevice",
    "discover_devices",
    "query_device",
    "AutoMode",
    "PumpStatus",
    "ScheduleSlot",
    "ECP150MStatus",
    "ECP150MSegment",
    "WaveMode",
    "WaveAutoMode",
    "Linkage",
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
    "MODEL_ECP150M",
]
