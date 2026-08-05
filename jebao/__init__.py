"""Python library for controlling Jebao aquarium pumps."""

from .const import (
    MODEL_MD44,
    MODEL_MDP20000,
    DeviceState,
)
from .device import JebaoDevice
from .discovery import DiscoveredDevice, JebaoDiscovery, discover_devices
from .exceptions import (
    JebaoAuthenticationError,
    JebaoCommandError,
    JebaoConnectionError,
    JebaoError,
    JebaoTimeoutError,
)
from .md44 import MD44Channel, MD44Device
from .md44_protocol import MD44Dose, MD44Status
from .mdp20000 import MDP20000Device

__version__ = "0.1.7"

__all__ = [
    "JebaoDevice",
    "MDP20000Device",
    "MD44Device",
    "MD44Channel",
    "MD44Dose",
    "MD44Status",
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
