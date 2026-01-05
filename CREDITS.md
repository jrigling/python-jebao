# Credits and Acknowledgments

## Original Work

This library builds upon and extends the excellent work from:

### jebao-dosing-pump-md-4.4
- **Repository:** https://github.com/tancou/jebao-dosing-pump-md-4.4
- **Author:** [@tancou](https://github.com/tancou)
- **Language:** Node.js
- **License:** ISC

**What we learned from this project:**
- GizWits IoT platform protocol structure
- Authentication flow (passcode request/response, login)
- TCP message framing (header structure, length calculation)
- Keepalive mechanism (ping/pong)
- MD 4.4 dosing pump control commands

**Adaptations made:**
- Ported from Node.js to Python with asyncio
- Extended for MDP-20000 variable-speed pumps
- Added multi-subnet UDP discovery
- Implemented additional features (feed mode, program mode)
- Added type hints and modern Python patterns

## MDP-20000 Protocol Research

Protocol specifics for the MDP-20000 were independently reverse-engineered through:

### Packet Capture Analysis
- Captured traffic from official Jebao mobile app
- Tools used: tcpdump, tshark, Wireshark
- Analysis scripts created for command extraction
- Testing with 3 physical MDP-20000 pumps

### Key Discoveries
- UDP discovery protocol (same as MD 4.4)
- MDP-20000 specific command structure
- Feed mode commands (set duration, start, cancel)
- Program mode detection and exit
- State values (OFF, ON, FEED, PROGRAM)
- Speed control range (30-100%)
- Automatic retry logic for handling garbage byte accumulation
- Multi-subnet discovery for IoT VLANs

## Protocol Compatibility

Confirmed ~80% protocol compatibility between:
- **MD 4.4** (4-channel dosing pump)
- **MDP-20000** (variable-speed circulation pump)

**Shared components:**
- GizWits platform foundation
- TCP port (12416) and UDP discovery (12414)
- Authentication mechanism
- Message framing and keepalive
- Basic command structure

**Model-specific differences:**
- Data packet size (MD 4.4: smaller, MDP-20000: 183 bytes)
- Control command payloads
- Device-specific features (dosing schedules vs. speed control)

## Technical Stack

**Python Libraries:**
- `asyncio` - Async I/O
- `netifaces` - Network interface enumeration for multi-subnet discovery
- Standard library: `socket`, `logging`, `dataclasses`

**Development Tools:**
- Wireshark / tcpdump - Protocol analysis
- Python 3.9+ - Implementation
- Home Assistant - Integration testing

## Future Work

Planned MD 4.4 support in this library will leverage both:
1. Original Node.js implementation as reference
2. Our Python protocol foundation
3. Unified device architecture

This will create a single Python library supporting both pump types.

## Community

Thanks to:
- [@tancou](https://github.com/tancou) for pioneering the MD 4.4 reverse-engineering
- Home Assistant community for integration feedback
- Jebao users who tested and provided feedback

## Legal

This is an unofficial library, not affiliated with:
- Jebao / Jecod
- GizWits IoT platform
- The original jebao-dosing-pump-md-4.4 project

Both this library and the original work are independent reverse-engineering efforts
for educational and interoperability purposes.

**Protocol information:** Obtained through legitimate packet capture of devices
owned by the developers.

**No official documentation used:** All findings from observation and analysis.

**Device warranty:** May be affected by third-party control software. Use at own risk.

---

**python-jebao** - Python library for Jebao pumps
Copyright (c) 2026 Justin Rigling
MIT License
