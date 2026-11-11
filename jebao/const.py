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

# All known product keys that identify an MDP-20000 (across hardware revisions)
PRODUCT_KEYS_MDP20000 = frozenset({PRODUCT_KEY_MDP20000, PRODUCT_KEY_MDP20000_V2})

# The 8-character values above are really the Wi-Fi module's firmware version,
# which is shared across products (an ECP-150M wave pump reports 04020B28 too).
# The GizWits product key proper is the 32-character field that follows it in
# the discovery reply; models are identified by that when it is present.
GIZWITS_KEY_MDP20000 = "02039876751049deb404d1d89221ec4b"  # MDP-20000, module 4.1.2
GIZWITS_KEY_MDP_V2 = "6a5c47b3ea364ecb841b47f5997a1775"  # newer MDP-20000/15000/3500/2500
GIZWITS_KEYS_MDP20000 = frozenset({GIZWITS_KEY_MDP20000, GIZWITS_KEY_MDP_V2})
GIZWITS_KEY_MD45 = "5ab6019f2dbb4ae7a42b48d2b8ce0530"  # MD-4.5 5-channel WiFi/BLE doser

# Model identifiers
MODEL_MDP20000 = "MDP-20000"
MODEL_MD44 = "MD-4.4"
MODEL_MD45 = "MD-4.5"

# MD-4.5: 5 independent on/off dosing channels (no speed or feed mode). Its
# channel command is an 808-byte 0x0093 frame, longer than the pumps' 323.
MD45_NUM_CHANNELS = 5
MD45_CHANNEL_COMMAND_SIZE = 808


# The status state byte is a set of flags (GizWits datapoints): bit 0 SwitchON,
# bit 1 Mode (front panel / wireless), bit 2 FeedSwitch, bit 3 TimerON (the
# app's Program schedule), bits 4-5 the schedule's current AutoMode.
STATE_BIT_ON = 0x01
STATE_BIT_FEED = 0x04
STATE_BIT_PROGRAM = 0x08


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
