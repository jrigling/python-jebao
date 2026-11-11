"""MD-4.5 5-channel doser: command frame, status bits, discovery."""
import asyncio

import pytest

from jebao import MD45Device
from jebao.discovery import JebaoDiscovery
from jebao.protocol import JebaoProtocol, build_frame, split_frame

from test_protocol import PASSCODE
from test_session import REPLY_414


def doser_status(bits):
    """Status frame with the channel bitmask at payload[3]; 2-byte varint like the real pump."""
    payload = bytes([0x03, 0x00, 0x00, bits]) + bytes(296)
    return build_frame(0x0091, payload)


class FakeDoser:
    """Minimal MD-4.5: answers login/status, applies channel commands to ``bits``."""

    def __init__(self):
        self.bits = 0x01  # main switch on, all channels off
        self.frames = []
        self.unsolicited = False
        self.connections = 0

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
        frame = hdr + raw + await reader.readexactly(n)
        return frame, *split_frame(frame)

    async def _handle(self, reader, writer):
        self.connections += 1
        try:
            while True:
                frame, cmd, payload = await self._read(reader)
                if cmd == 0x0006:
                    writer.write(build_frame(0x0007, b"\x00\x0a" + PASSCODE))
                elif cmd == 0x0008:
                    writer.write(build_frame(0x0009, b"\x00"))
                    if self.unsolicited:  # the 9-byte 0x62 frame seen after login
                        writer.write(build_frame(0x0062, b"\x00\x00"))
                elif cmd == 0x0090:
                    writer.write(doser_status(self.bits))
                elif cmd == 0x0093:
                    self.frames.append(frame)
                    mask, state = payload[9], payload[12]
                    self.bits = (self.bits | mask) if state == mask else (self.bits & ~mask)
                    writer.write(build_frame(0x0094, b"\x00"))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


@pytest.fixture
async def doser():
    d = FakeDoser()
    await d.start()
    yield d
    await d.stop()


def device(doser):
    return MD45Device("127.0.0.1", port=doser.port, device_id="md45")


def test_channel_frame_matches_hardware_verified_layout():
    """Byte-for-byte the frame the pre-0.2 MD-4.5 code sent to real hardware."""
    p = JebaoProtocol("x")
    frame = p._build_channel_command(1 << 3, 1 << 3)
    expected = bytearray(808)
    expected[0:9] = bytes.fromhex("00000003a206000093")
    expected[9:13] = (1).to_bytes(4, "big")
    expected[13] = 0x01
    expected[18] = 0x08  # mask, channel 3
    expected[21] = 0x08  # state = mask -> on
    assert frame == bytes(expected)
    off = p._build_channel_command(1 << 3, 0x00)
    assert off[12] == 2 and off[18] == 0x08 and off[21] == 0x00  # seq advanced


def test_status_bits_offset():
    # Real capture shape: frame[8] == 0x91 (2-byte varint) and bits at frame[12].
    frame = doser_status(0x05)
    assert frame[8] == 0x91 and frame[12] == 0x05
    assert MD45Device.parse_channel_bits(frame) == 0x05
    assert MD45Device.parse_channel_bits(build_frame(0x0091, b"\x03\x00")) is None


async def test_status_and_channel_control(doser):
    d = device(doser)
    await d.connect()
    assert d.channel_states == [False] * 5 and d.channel_bits == 0x01

    await d.turn_on_channel(2)
    await d.turn_on_channel(5)
    assert d.channel_states == [False, True, False, False, True]  # optimistic
    assert doser.bits == 0x01 | 0x04 | 0x20

    await d.turn_off_channel(2)
    await d.update()
    assert d.channel_states == [False, False, False, False, True]
    assert d.passcode == PASSCODE
    assert d.state is None and d.speed is None  # no pump semantics


async def test_unsolicited_frame_after_login_is_skipped(doser):
    doser.unsolicited = True
    doser.bits = 0x01 | 0x02
    d = device(doser)
    await d.update()
    assert d.is_channel_on(1)
    assert doser.connections == 1  # no retry needed


async def test_invalid_channel(doser):
    d = device(doser)
    for ch in (0, 6):
        with pytest.raises(ValueError):
            await d.turn_on_channel(ch)
    assert doser.frames == []


def test_discovery_identifies_md45():
    data = REPLY_414.replace(
        b"6a5c47b3ea364ecb841b47f5997a1775", b"5ab6019f2dbb4ae7a42b48d2b8ce0530"
    )
    found = JebaoDiscovery._parse_discovery_response(data, "10.0.0.7")
    assert found.model == "MD-4.5" and found.is_md45 and not found.is_mdp20000
    assert found.product_key == "5ab6019f2dbb4ae7a42b48d2b8ce0530"
