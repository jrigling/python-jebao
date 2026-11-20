"""ECP-150M: status decoding, the read request, discovery."""

import asyncio
from datetime import datetime

import pytest

from jebao import DeviceState, ECP150MDevice
from jebao.datapoints import (
    ECP150MSegment,
    ECP150MStatus,
    Linkage,
    WaveAutoMode,
    WaveMode,
)
from jebao.discovery import JebaoDiscovery
from jebao.exceptions import JebaoCommandError
from jebao.protocol import build_frame, split_frame

from test_protocol import PASSCODE
from test_session import REPLY_ECP

# Full 401-byte values areas read from two pumps (issue #5)
# Pump 1: Program, Sine, current segment Sine 30 %, frequency 100, feed 10 min
PUMP1 = (
    bytes.fromhex("04391e640a1e64000a0a0a00021e64010a000a0a021e6400")
    + b"\xee" * 368
    + bytes.fromhex("141805130004173400")
)
# Pump 2: Manual, Constant, 57 %, frequency 50, feed 20 min, feed running
PUMP2 = (
    bytes.fromhex("00653932140064000900160003646400")
    + b"\xee" * 376
    + bytes.fromhex("1418051300041c2300")
)


def decode(values: bytes) -> ECP150MStatus:
    return ECP150MStatus.from_values(values)


def with_flags(flags: int) -> bytes:
    return flags.to_bytes(2, "big") + PUMP2[2:]


def test_pump1_program():
    status = decode(PUMP1)
    assert status.switch_on and status.timer_on and status.auto_pulse_tide
    assert not status.feed_switch and not status.pulse_tide
    assert status.mode is WaveMode.SINE
    assert status.linkage is Linkage.INDEPENDENT
    assert status.auto_mode is WaveAutoMode.SINE
    assert (status.flow, status.frequency, status.feed_time) == (30, 100, 10)
    assert (status.auto_flow, status.auto_frequency, status.auto_feed_time) == (30, 100, 0)
    assert len(status.schedule) == 48
    assert status.schedule_slots == [
        ECP150MSegment(10, 10, 10, 0, 2, 30, 100, 1),
        ECP150MSegment(10, 0, 10, 10, 2, 30, 100, 0),
    ]
    assert status.schedule_slots[0].auto_mode is WaveAutoMode.SINE
    assert status.schedule_slots[0].start == "10:10"
    assert status.clock == datetime(2024, 5, 19, 4, 23, 52)
    assert status.faults == ()


def test_pump2_manual_feed():
    status = decode(PUMP2)
    assert status.switch_on and status.feed_switch and not status.timer_on
    assert status.mode is WaveMode.CONSTANT
    assert status.linkage is Linkage.INDEPENDENT
    assert status.auto_mode is WaveAutoMode.STOP
    assert (status.flow, status.frequency, status.feed_time) == (57, 50, 20)
    # Segment mode 0x03 is the app's red mode
    assert status.schedule_slots == [ECP150MSegment(9, 0, 22, 0, 3, 100, 100, 0)]
    assert status.schedule_slots[0].auto_mode is WaveAutoMode.RANDOM
    assert status.clock == datetime(2024, 5, 19, 4, 28, 35)
    assert status.raw == PUMP2


def test_linkage_master():
    # Read once while pump 1 was switched to master
    status = decode(with_flags(0x04B1))
    assert status.linkage is Linkage.MASTER
    assert not status.timer_on
    assert status.mode is WaveMode.SINE


@pytest.mark.parametrize(
    "flags,auto_mode", [(0x0121, WaveAutoMode.STOP), (0x0721, WaveAutoMode.RANDOM)]
)
def test_linkage_slave(flags, auto_mode):
    # Pump 2 as slave read with both words; only bits 9-11 differ
    status = decode(with_flags(flags))
    assert status.linkage is Linkage.SLAVE
    assert status.auto_mode is auto_mode
    assert status.mode is WaveMode.SINE


@pytest.mark.parametrize(
    "flags,mode",
    [
        (0x01, WaveMode.CLASSIC),
        (0x21, WaveMode.SINE),
        (0x41, WaveMode.RANDOM),
        (0x61, WaveMode.CONSTANT),
    ],
)
def test_mode_bits(flags, mode):
    assert decode(with_flags(flags)).mode is mode


def test_values_outside_definition():
    status = decode(with_flags(0x0E00 | 0x0180))
    assert status.linkage is None
    assert status.auto_mode is None
    segment = ECP150MSegment.from_bytes(bytes([6, 0, 7, 0, 9, 50, 50, 0]))
    assert segment.mode == 9 and segment.auto_mode is None


def test_all_48_segments():
    values = bytes(8) + bytes([6, 0, 7, 0, 2, 50, 50, 0]) * 48 + PUMP2[392:]
    assert len(decode(values).schedule_slots) == 48


def test_unused_segments_are_none():
    values = bytearray(PUMP1)
    values[8:16] = b"\xee" * 8
    schedule = decode(bytes(values)).schedule
    assert schedule[0] is None and schedule[1] is not None


def test_faults():
    status = decode(PUMP2[:400] + bytes([0x51]))
    assert status.faults == ("overcurrent", "locked_rotor", "communication")


def test_zeroed_clock():
    assert decode(PUMP2[:392] + bytes(9)).clock is None


def test_rejects_short_values():
    with pytest.raises(ValueError):
        decode(PUMP2[:-1])


def test_parse_read_reply():
    assert ECP150MDevice.parse_read_reply(bytes(4) + b"\x03" + PUMP1).flow == 30
    assert ECP150MDevice.parse_read_reply(bytes(4) + b"\x04" + PUMP1) is None
    assert ECP150MDevice.parse_read_reply(bytes(4) + b"\x03" + PUMP1[:-1]) is None


def test_discovery_identifies_ecp150m():
    found = JebaoDiscovery._parse_discovery_response(REPLY_ECP, "10.0.0.8")
    assert found.model == "ECP-150M" and found.is_ecp150m and not found.is_mdp20000
    assert found.product_key == "1d8c63eaccac4205b92c84d77d5a08fb"
    assert found.firmware_version == "04020B28"


class FakeWavePump:
    """Minimal ECP-150M: answers login and GizWits reads; records every request."""

    def __init__(self, values: bytes):
        self.values = values
        self.requests = []

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def _read(self, reader):
        hdr = await reader.readexactly(4)
        n, shift, raw = 0, 0, b""
        while True:
            b = (await reader.readexactly(1))[0]
            raw += bytes([b])
            n |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
        return split_frame(hdr + raw + await reader.readexactly(n))

    async def _handle(self, reader, writer):
        try:
            while True:
                cmd, payload = await self._read(reader)
                if cmd == 0x0006:
                    writer.write(build_frame(0x0007, b"\x00\x0a" + PASSCODE))
                elif cmd == 0x0008:
                    writer.write(build_frame(0x0009, b"\x00"))
                elif cmd == 0x0093:
                    self.requests.append(payload)
                    writer.write(build_frame(0x0094, payload[:4] + b"\x03" + self.values))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


@pytest.fixture
async def wave_pump():
    pump = FakeWavePump(PUMP1)
    await pump.start()
    yield pump
    await pump.stop()


async def test_update_reads_status(wave_pump):
    device = ECP150MDevice("127.0.0.1", port=wave_pump.port, device_id="ecp")
    await device.connect()
    # One read: sequence + action 0x02, nothing else
    assert len(wave_pump.requests) == 1
    assert len(wave_pump.requests[0]) == 5 and wave_pump.requests[0][4] == 0x02
    assert device.status.mode is WaveMode.SINE
    assert device.state is DeviceState.PROGRAM
    assert device.is_on and device.is_program_mode and not device.is_feed_mode
    assert device.speed == 30
    assert device.passcode == PASSCODE


async def test_update_rejects_bad_reply(wave_pump):
    wave_pump.values = PUMP1[:100]
    device = ECP150MDevice("127.0.0.1", port=wave_pump.port)
    with pytest.raises(JebaoCommandError):
        await device.update()
