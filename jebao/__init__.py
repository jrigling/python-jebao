"""Python library for controlling Jebao aquarium pumps."""

from .device import JebaoDevice
from .mdp20000 import MDP20000Device
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
)

__version__ = "0.1.3"

__all__ = [
    "JebaoDevice",
    "MDP20000Device",
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
]
