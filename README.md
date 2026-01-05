# python-jebao

Python library for controlling Jebao aquarium pumps over local network.

## Supported Models

- **MDP-20000**: Variable-speed circulation pump (30-100% speed control)
- **MD-4.4**: 4-channel dosing pump (planned)

## Features

- 🔍 **UDP Discovery** - Automatic device discovery with multi-subnet support
- 🔌 **Local Control** - Direct TCP control, no cloud dependency
- ⚡ **Full Protocol** - On/Off, speed control, feed mode, status monitoring
- 🏠 **Home Assistant Ready** - Designed for HA integration
- 🔒 **Type Safe** - Full type hints for better IDE support

## Installation

```bash
pip install python-jebao
```

For development:
```bash
git clone https://github.com/jrigling/python-jebao.git
cd python-jebao
pip install -e .
```

## Quick Start

### Discovery

```python
import asyncio
from jebao import discover_devices

async def main():
    # Discover all Jebao devices on network
    devices = await discover_devices()

    for device in devices:
        print(f"Found: {device.model} at {device.ip_address}")
        print(f"  Device ID: {device.device_id}")
        print(f"  MAC: {device.mac_address}")

asyncio.run(main())
```

### Multi-Subnet Discovery

If you have multiple network interfaces (e.g., IoT VLAN, main network):

```python
# Discover on all interfaces
devices = await discover_devices()

# Or specify interfaces explicitly
devices = await discover_devices(interfaces=['eth0', 'eth1'])
```

### Basic Control

```python
from jebao import MDP20000Device

async def main():
    # Connect to device
    async with MDP20000Device(host="10.20.20.13") as pump:
        # Ensure in manual mode (exits Program mode if needed)
        await pump.ensure_manual_mode()

        # Turn on
        await pump.turn_on()

        # Set speed to 75%
        await pump.set_speed(75)

        # Check status
        await pump.update()
        print(f"State: {pump.state.name}")
        print(f"Speed: {pump.speed}%")
        print(f"Is on: {pump.is_on}")

        # Turn off
        await pump.turn_off()

asyncio.run(main())
```

### Feed Mode

```python
async def feed_mode_example():
    async with MDP20000Device(host="10.20.20.13") as pump:
        await pump.ensure_manual_mode()
        await pump.turn_on()
        await pump.set_speed(75)

        # Start 2-minute feed (pump stops temporarily)
        await pump.start_feed(minutes=2)
        print("Feed started, pump will auto-resume in 2 minutes")

        # Check status during feed
        await pump.update()
        print(f"In feed mode: {pump.is_feed_mode}")

        # Or cancel early
        # await pump.cancel_feed(resume_speed=75)
```

### Long-Running Connection

```python
async def monitor_pump():
    pump = MDP20000Device(host="10.20.20.13")

    try:
        await pump.connect()
        await pump.ensure_manual_mode()

        # Control pump
        await pump.turn_on()
        await pump.set_speed(50)

        # Monitor status periodically
        while True:
            await pump.update()
            print(f"Status: {pump.state.name} @ {pump.speed}%")
            await asyncio.sleep(30)

    finally:
        await pump.disconnect()
```

## Device States

```python
from jebao import DeviceState

# State values
DeviceState.OFF      # 0x10 - Manual mode, stopped
DeviceState.ON       # 0x11 - Manual mode, running
DeviceState.FEED     # 0x15 - Feed mode (temporary pause)
DeviceState.PROGRAM  # 0x19 - Program mode (scheduled)

# Check state
if pump.state == DeviceState.FEED:
    print("Pump is in feed mode")

# Helper properties
pump.is_on           # True if ON or FEED
pump.is_off          # True if OFF
pump.is_feed_mode    # True if FEED
pump.is_program_mode # True if PROGRAM
pump.is_manual_mode  # True if ON or OFF
```

## API Reference

### MDP20000Device

#### Connection
- `async connect(timeout=5.0)` - Connect and authenticate
- `async disconnect()` - Disconnect from device
- `is_connected` - Check connection status
- `async update()` - Refresh device status

#### Control
- `async turn_on()` - Turn pump on
- `async turn_off()` - Turn pump off
- `async set_speed(percentage)` - Set speed (30-100%)

#### Feed Mode
- `async set_feed_duration(minutes)` - Configure feed timer (1-10 minutes)
- `async start_feed(minutes=None)` - Start feed mode
- `async cancel_feed(resume_speed=None)` - Cancel feed and resume

#### Program Mode
- `async ensure_manual_mode()` - Exit Program mode if active

#### Properties
- `state: DeviceState` - Current state
- `speed: int` - Current speed (30-100)
- `is_on: bool` - Pump is running
- `is_off: bool` - Pump is stopped
- `is_feed_mode: bool` - In feed mode
- `is_program_mode: bool` - In program mode
- `is_manual_mode: bool` - In manual mode

### Discovery

```python
from jebao import JebaoDiscovery, discover_devices

# Convenience function
devices = await discover_devices(timeout=2.0, interfaces=['eth0'])

# Or use class directly
discovery = JebaoDiscovery()
devices = await discovery.discover(timeout=2.0, interfaces=['eth0'])

# Filter by model
mdp20000_devices = [d for d in devices if d.is_mdp20000]
md44_devices = [d for d in devices if d.is_md44]
```

### DiscoveredDevice

```python
device.device_id      # Device ID (e.g., "POAKSFXJNJ")
device.ip_address     # IP address
device.mac_address    # MAC address
device.product_key    # Product key
device.model          # Model name (e.g., "MDP-20000")
device.is_mdp20000    # True if MDP-20000
device.is_md44        # True if MD-4.4
```

## Home Assistant Integration

This library is designed for use with Home Assistant. See the separate
[homeassistant-jebao](https://github.com/jrigling/homeassistant-jebao) repository
for the ready-to-use HA integration.

## Reliability Features

The library includes automatic retry logic for enhanced reliability:
- Commands automatically retry up to 3 times on transient failures
- Handles garbage byte accumulation from pump firmware
- Exponential backoff for retry delays
- Typical success rate >97% even with flaky network conditions

## Multi-Subnet Considerations

When Home Assistant runs on a system with multiple network interfaces:

1. **Automatic Discovery**: The library will scan all interfaces by default
2. **Interface Selection**: You can specify which interfaces to scan
3. **Broadcast Support**: Each interface gets its own broadcast address
4. **IoT VLANs**: Works great with isolated IoT networks

Example: HA connected to both `192.168.1.0/24` (main) and `10.20.20.0/24` (IoT):

```python
# Discovers on both networks automatically
devices = await discover_devices()

# Or be explicit
devices = await discover_devices(interfaces=['eth0', 'eth1'])
```

## Error Handling

```python
from jebao import (
    JebaoError,
    JebaoConnectionError,
    JebaoAuthenticationError,
    JebaoCommandError,
    JebaoTimeoutError,
    JebaoInvalidStateError,
)

try:
    async with MDP20000Device(host="10.20.20.13") as pump:
        await pump.turn_on()
except JebaoConnectionError:
    print("Failed to connect - check IP and network")
except JebaoAuthenticationError:
    print("Authentication failed")
except JebaoCommandError:
    print("Command execution failed")
except JebaoTimeoutError:
    print("Operation timed out")
except JebaoError as e:
    print(f"General error: {e}")
```

## Logging

The library uses Python's standard logging:

```python
import logging

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)

# Or just for jebao
logging.getLogger('jebao').setLevel(logging.DEBUG)
```

## Requirements

- Python 3.9+
- `netifaces` - For multi-interface network discovery

## License

MIT License - See LICENSE file

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## Credits

This library builds upon the excellent work from:
- [jebao-dosing-pump-md-4.4](https://github.com/tancou/jebao-dosing-pump-md-4.4) by [@tancou](https://github.com/tancou) - Original Node.js implementation for MD 4.4 pumps that provided the foundation for understanding the GizWits protocol

Protocol for MDP-20000 was reverse-engineered through packet capture analysis of the official Jebao mobile app.

## Disclaimer

This is an unofficial library not affiliated with Jebao. Use at your own risk.
Device warranty may be affected by third-party control software.
