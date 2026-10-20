"""Constants for Jebao library."""
from enum import IntEnum

# Network ports
TCP_PORT = 12416
UDP_DISCOVERY_PORT = 12414
UDP_LISTEN_PORT = 2415

# Timeouts (seconds)
DEFAULT_TIMEOUT = 5.0
DISCOVERY_TIMEOUT = 2.0
COMMAND_TIMEOUT = 5.0

# After login some pumps (MDP-15000) send a duplicate login-OK and a 0x62 frame.
# Over Wi-Fi they can arrive ~100 ms later, so drain for longer than a round trip.
POST_LOGIN_DRAIN_TIMEOUT = 0.5

# Keep-alive interval (seconds)
KEEPALIVE_INTERVAL = 4.0

# Message types
MSG_REQUEST_PASSCODE = 0x06
MSG_PASSCODE_RESPONSE = 0x07
MSG_LOGIN_REQUEST = 0x08
MSG_LOGIN_SUCCESS = 0x09
MSG_PING = 0x15
MSG_PONG = 0x16
MSG_DATA_REQUEST_SIMPLE = 0x90
MSG_DATA_RESPONSE_SIMPLE = 0x91
MSG_CONTROL_OR_EXTENDED_REQUEST = 0x93
MSG_CONTROL_RESPONSE_OR_EXTENDED_DATA = 0x94

# Command sizes
CONTROL_COMMAND_SIZE = 323

# Product keys
PRODUCT_KEY_MDP20000 = "04020B28"
PRODUCT_KEY_MDP20000_V2 = "04D30Q34"  # Newer MDP-20000 hardware revision
PRODUCT_KEY_MD44 = "04020B27"  # Assumed, verify if needed
PRODUCT_KEY_MDP15000 = "04D30Q37"  # Same protocol and status layout as the MDP-20000

# All known product keys that identify an MDP-20000 (across hardware revisions)
PRODUCT_KEYS_MDP20000 = frozenset({PRODUCT_KEY_MDP20000, PRODUCT_KEY_MDP20000_V2})

# Model identifiers
MODEL_MDP20000 = "MDP-20000"
MODEL_MD44 = "MD-4.4"
MODEL_MDP15000 = "MDP-15000"

# Models that speak the MDP-20000 protocol and work with MDP20000Device
MODELS_MDP20000_FAMILY = frozenset({MODEL_MDP20000, MODEL_MDP15000})


# Bits of the status byte. DeviceState values are combinations of these, but pumps
# can report other combinations (e.g. unknown bits set), so decode from the bits.
STATUS_FLAG_RUNNING = 0x01
STATUS_FLAG_FEED = 0x04
STATUS_FLAG_PROGRAM = 0x08


class DeviceState(IntEnum):
    """Device state values."""

    OFF = 0x10  # Manual mode, pump stopped
    ON = 0x11  # Manual mode, pump running
    FEED = 0x15  # Feed mode, temporary pause
    PROGRAM = 0x19  # Program mode, scheduled operation


# Command opcodes (at byte 21 in control messages)
class CommandOpcode(IntEnum):
    """Command opcodes for control messages."""

    TURN_ON_OFF = 0x01  # Byte 22: 0x01=ON, 0x00=OFF
    SET_SPEED = 0x20  # Byte 23: speed 30-100
    EXIT_PROGRAM = 0x28  # Exit Program mode
    SET_FEED_DURATION = 0x44  # Byte 24: duration 1-10 minutes
    START_FEED = 0x4C  # Start feed mode
    CANCEL_FEED = 0x6C  # Cancel feed mode


# Speed limits
MIN_SPEED = 30
MAX_SPEED = 100

# Feed duration limits (minutes)
MIN_FEED_DURATION = 1
MAX_FEED_DURATION = 10
