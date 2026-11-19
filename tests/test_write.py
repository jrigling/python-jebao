"""Datapoint writes (schedule, clock, feed time) over short-lived sessions."""
from datetime import datetime

import pytest

from jebao.datapoints import encode_write, parse_slots, schedule_values
from jebao.exceptions import JebaoCommandError
from jebao.protocol import build_frame

from test_protocol import FakePump, device, pump, status_frame  # noqa: F401  (fixture)


class WritablePump(FakePump):
    """Fake pump that also applies datapoint writes and pushes its new status."""

    def __init__(self, push=True):
        super().__init__()
        self.push = push
        self.writes = []

    async def _handle(self, reader, writer):
        original = writer.write

        def write(data):
            original(data)
            # After acking a write, push the new status like real pumps do
            if self.push and data == build_frame(0x0094, b"\x00") and self.writes:
                original(status_frame(self.state, self.speed).replace(b"\x91\x03", b"\x91\x04", 1))

        writer.write = write
        await super()._handle(reader, writer)

    async def _read(self, reader):
        cmd, payload = await super()._read(reader)
        if cmd == 0x0093:
            self.writes.append(payload[5:])  # attr flags + values
            flags = payload[5:13]
            if flags[7] & 0x20:  # Motor_Speed
                self.speed = payload[13 + 1]
        return cmd, payload


@pytest.fixture
async def wpump():
    p = WritablePump()
    await p.start()
    yield p
    await p.stop()


async def test_write_is_sent_once_and_pushed_status_applied(wpump):
    d = device(wpump)
    await d.update()
    connections = wpump.connections
    await d.write(motor_speed=45, timer_on=False)
    assert wpump.writes == [encode_write({"motor_speed": 45, "timer_on": False})]
    assert d.speed == 45
    # The pushed report was used; no extra status read
    assert wpump.connections == connections + 1


async def test_write_without_pushed_status_reads_it(pump):
    d = device(pump)
    await d.write(motor_speed=60)
    assert d.speed == 70  # this fake pump doesn't apply writes; its read wins
    assert d.status is not None


async def test_write_not_resent_when_ack_lost(pump):
    pump.faults = ["drop_ack"]
    d = device(pump)
    await d.write(feed_time=5, feed_switch=False)
    assert len(pump.commands) == 1


async def test_write_rejects_bad_payloads():
    from jebao.protocol import JebaoProtocol

    p = JebaoProtocol("127.0.0.1", port=1)
    with pytest.raises(JebaoCommandError):
        await p.send_write(b"")
    with pytest.raises(JebaoCommandError):
        await p.send_write(bytes(309))


async def test_status_decodes_datapoints(pump):
    d = device(pump)
    await d.update()
    status = d.status
    assert status is not None and status.motor_speed == 70 and status.feed_time == 1
    assert status.faults == ()


async def test_schedule_and_clock(wpump):
    d = device(wpump)
    await d.update()
    slots = parse_slots([{"start": "08:00", "end": "20:00", "speed": 60}])
    await d.set_schedule(slots, enabled=True, now=datetime(2026, 1, 1, 9, 0))
    await d.set_clock(datetime(2026, 1, 1, 9, 0, 5))
    sched, enable, clock = wpump.writes
    assert sched == encode_write(schedule_values(slots))
    assert enable == encode_write(
        {"timer_on": True, "feed_switch": False, "auto_mode": 1, "auto_gears": 60}
    )
    assert clock[8 + 5 : 8 + 13] == bytes((20, 26, 1, 1, 0, 9, 0, 5))


async def test_feed_time_kept_by_feed_commands(pump):
    pump.state = 0x11
    d = device(pump)
    await d.update()
    await d.start_feed(9)
    assert pump.commands[-1] == bytes([0x4C, 0x04, 0x00, 9])
    # Without a duration, the pump's own FeedTime (1 here) is resent, not the last one
    await d.start_feed()
    assert pump.commands[-1] == bytes([0x4C, 0x04, 0x00, 1])
