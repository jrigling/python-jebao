"""Protocol tests against a fake MDP-20000 that can inject the faults seen in the field.

Field failures these cover (Home Assistant logs, Nov 2026):
- pump closes idle sessions after ~10 s -> every poll found a dead socket
- "Status request timed out" / "Login timed out" -> stalled replies
- "Invalid status response: too short (9 bytes)" and "0 is not a valid
  DeviceState" -> stream desync after a cancelled read
- "readexactly() called while another coroutine is already waiting"
  -> concurrent operations sharing one socket
"""
import asyncio

import pytest

from jebao import JebaoError, MDP20000Device
from jebao.exceptions import JebaoCommandError, JebaoTimeoutError
from jebao.protocol import JebaoProtocol, build_frame, split_frame

PASSCODE = b"ABCDEFGHIJ"


def status_frame(state=0x11, speed=70):
    payload = bytes([0x03, state, speed, 0x01, 0x1E]) + bytes(18) + b"\x01\x64"
    payload += b"\xee" * (303 - len(payload) - 1) + b"\x00"
    frame = build_frame(0x0091, payload)
    assert frame[4:6] == b"\xb2\x02"  # matches the real pump's 2-byte varint length
    return frame


class FakePump:
    """Minimal GizWits server. ``faults`` is a list consumed one per connection."""

    def __init__(self):
        self.faults = []
        self.connections = 0
        self.commands = []
        self.state = 0x11
        self.speed = 70
        self.server = None
        self.port = None
        self.idle_close = None
        self.passcode = PASSCODE
        self.passcode_requests = 0
        self.binding_closed = False  # stops handing out the passcode
        self.peer_ports = []
        self.writers = set()

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self):
        self.server.close()
        # Since Python 3.12, wait_closed() also waits for open connections.
        # Cut them off so a reset the OS never delivers can't hang teardown.
        for w in list(self.writers):
            w.transport.abort()
        await self.server.wait_closed()

    async def _read(self, reader):
        hdr = await reader.readexactly(4)
        n, shift = 0, 0
        raw = b""
        while True:
            b = (await reader.readexactly(1))[0]
            raw += bytes([b])
            n |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
        body = await reader.readexactly(n)
        return split_frame(hdr + raw + body)

    async def _handle(self, reader, writer):
        self.connections += 1
        self.writers.add(writer)
        self.peer_ports.append(writer.get_extra_info("peername")[1])
        fault = self.faults.pop(0) if self.faults else None
        try:
            if fault == "refuse":
                return
            while True:
                if self.idle_close:
                    cmd, payload = await asyncio.wait_for(self._read(reader), self.idle_close)
                else:
                    cmd, payload = await self._read(reader)
                if cmd == 0x0006:
                    self.passcode_requests += 1
                    if fault == "stall_login":
                        await asyncio.sleep(2)
                    if self.binding_closed:
                        writer.write(build_frame(0x0007, b"\x00\x00"))
                    else:
                        writer.write(build_frame(0x0007, b"\x00\x0a" + self.passcode))
                elif cmd == 0x0008:
                    ok = payload == len(self.passcode).to_bytes(2, "big") + self.passcode
                    writer.write(build_frame(0x0009, b"\x00" if ok else b"\x01"))
                elif cmd == 0x0090:
                    if fault == "stall_status":
                        await asyncio.sleep(2)
                    if fault == "half_status":
                        writer.write(status_frame(self.state, self.speed)[:50])
                        await writer.drain()
                        await asyncio.sleep(2)
                    if fault == "unsolicited":
                        # unsolicited report + stray ack before the real answer
                        writer.write(status_frame(0x10, 30))
                        writer.write(build_frame(0x0094, b"\x00"))
                    if fault == "bad_status":
                        writer.write(build_frame(0x0091, b"\x03"))
                    else:
                        writer.write(status_frame(self.state, self.speed))
                elif cmd == 0x0093:
                    self.commands.append(payload[12:16])
                    if payload[12] == 0x28:  # EXIT_PROGRAM: leave schedule, run manually
                        self.state = (self.state & ~0x08) | 0x01
                    if fault == "drop_ack":
                        return
                    writer.write(build_frame(0x0094, b"\x00"))
                elif cmd == 0x0015:
                    writer.write(build_frame(0x0016))
                await writer.drain()
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            self.writers.discard(writer)
            writer.close()


@pytest.fixture
async def pump():
    p = FakePump()
    await p.start()
    yield p
    await p.stop()


def device(pump, timeout=0.5):
    d = MDP20000Device("127.0.0.1", port=pump.port, device_id="test")
    return d


async def test_basic_status(pump):
    d = device(pump)
    await d.connect()
    assert d.is_connected and d.speed == 70 and d.state == 0x11


async def test_survives_idle_close(pump):
    """Pump closes idle sessions (10 s in reality). Polls must not care."""
    pump.idle_close = 0.2
    d = device(pump)
    await d.connect()
    for _ in range(3):
        await asyncio.sleep(0.4)
        await d.update()
    # one session per op, and no failed attempt needing a retry
    assert pump.connections == 4


@pytest.mark.parametrize("fault", ["stall_status", "half_status", "stall_login", "refuse"])
async def test_transient_fault_is_retried_without_desync(pump, fault):
    pump.faults = [fault]
    d = device(pump)
    await d.update(timeout=0.3)
    assert d.speed == 70
    # Subsequent reads must be clean (no stale bytes from the failed session).
    pump.speed = 55
    await d.update(timeout=0.3)
    assert d.speed == 55


async def test_persistent_failure_raises_jebao_error(pump):
    pump.faults = ["stall_status"] * 3
    d = device(pump)
    with pytest.raises(JebaoTimeoutError):
        await d.update(timeout=0.2)
    assert not d.is_connected
    await d.update(timeout=0.2)
    assert d.is_connected


async def test_unsolicited_frames_are_skipped(pump):
    pump.faults = ["unsolicited"]
    d = device(pump)
    await d.update()
    # The first 0x0091 seen is the unsolicited one (state OFF, 30%) - accepted as
    # current status; it is a genuine report from the pump. No desync either way:
    pump.speed = 80
    await d.update()
    assert d.speed == 80


async def test_malformed_status_is_jebao_error(pump):
    pump.faults = ["bad_status"]
    d = device(pump)
    with pytest.raises(JebaoCommandError):
        await d.update()


async def test_concurrent_ops_are_serialised(pump):
    d = device(pump)
    results = await asyncio.gather(
        *[d.update() for _ in range(10)], d.set_speed(60), d.turn_on(), return_exceptions=True
    )
    assert not [r for r in results if isinstance(r, Exception)]
    assert sorted(pump.commands) == sorted([bytes([0x20, 0, 60, 0]), bytes([0x01, 0x01, 0, 0])])


async def test_command_not_resent_when_ack_lost(pump):
    pump.faults = ["drop_ack"]
    d = device(pump)
    await d.set_speed(40)
    assert pump.commands == [bytes([0x20, 0, 40, 0])]


async def test_command_session_setup_is_retried(pump):
    pump.faults = ["refuse"]
    d = device(pump)
    await d.turn_off()
    assert pump.commands == [bytes([0x01, 0x00, 0, 0])]


async def test_unreachable_host_raises_quickly():
    d = MDP20000Device("127.0.0.1", port=1, device_id="x")
    with pytest.raises(JebaoError):
        await d.update(timeout=0.2)


def test_control_frame_layout_matches_v01():
    """Byte-for-byte identical to the pre-0.2 builder (verified on hardware)."""
    p = JebaoProtocol("x")
    frame = p._build_control_command(0x20, 0x00, 70, 0x00)
    assert len(frame) == 323
    assert frame[:9] == bytes.fromhex("00000003bd02000093")
    assert frame[9:13] == (1).to_bytes(4, "big") and frame[13] == 0x01
    assert frame[21:25] == bytes([0x20, 0x00, 70, 0x00])
