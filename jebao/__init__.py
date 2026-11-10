"""Python library for controlling Jebao aquarium pumps."""

from .device import JebaoDevice
from .mdp20000 import MDP20000Device
from .ecp150m import ECP150MSegment, ECP150MStatus
from .discovery import JebaoDiscovery, DiscoveredDevice, discover_devices
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
    MODEL_ECP150M,
)

__version__ = "0.2.0"

__all__ = [
    "JebaoDevice",
    "MDP20000Device",
    "ECP150MStatus",
    "ECP150MSegment",
    "JebaoDiscovery",
    "DiscoveredDevice",
    "discover_devices",
    "JebaoError",
    "JebaoConnectionError",
    "JebaoAuthenticationError",
    "JebaoCommandError",
    "JebaoTimeoutError",
    "DeviceState",
    "MODEL_MDP20000",
    "MODEL_MD44",
    "MODEL_ECP150M",
]
