"""Tests for the datapoint layer, against frames captured from MDP-15000 pumps."""
from datetime import datetime

import pytest

from jebao.const import CommandOpcode
from jebao.datapoints import (
    UNUSED_SLOT,
    AutoMode,
    PumpStatus,
    ScheduleSlot,
    encode_clock,
    encode_write,
    parse_slots,
    schedule_state,
    schedule_values,
)
from jebao.protocol import JebaoProtocol

# Status reply (0x0091 action 0x03) from an MDP-15000: running at 57%, schedule off,
# one schedule slot written (00:00-00:00 run at 100%), clock last set 2026-10-15 23:34:44
STATUS_MDP15000 = bytes.fromhex(
    "00000003b2020000910311390a1e00141a0a0f0017222c000000000164eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee00"
)


def legacy_frame(opcode1, opcode2=0, param1=0, param2=0, sequence=1):
    """The control frame python-jebao 0.1.x built: fixed 323 bytes, opcodes at 21-24."""
    buffer = bytearray(323)
    buffer[0:6] = b"\x00\x00\x00\x03\xbd\x02"
    buffer[8] = 0x93
    buffer[9:13] = sequence.to_bytes(4, "big")
    buffer[13] = 0x01
    buffer[21:25] = bytes((opcode1, opcode2, param1, param2))
    return bytes(buffer)


@pytest.mark.parametrize(
    "changes, legacy",
    [
        ({"switch_on": True}, (CommandOpcode.TURN_ON_OFF, 0x01)),
        ({"switch_on": False}, (CommandOpcode.TURN_ON_OFF, 0x00)),
        ({"motor_speed": 55}, (CommandOpcode.SET_SPEED, 0, 55)),
        ({"timer_on": False, "motor_speed": 80}, (CommandOpcode.EXIT_PROGRAM, 0, 80)),
        ({"feed_switch": False, "feed_time": 5}, (CommandOpcode.SET_FEED_DURATION, 0, 0, 5)),
        (
            {"feed_switch": True, "timer_on": False, "feed_time": 5},
            (CommandOpcode.START_FEED, 0x04, 0, 5),
        ),
        (
            {"feed_switch": False, "timer_on": False, "motor_speed": 70, "feed_time": 5},
            (CommandOpcode.CANCEL_FEED, 0, 70, 5),
        ),
    ],
)
def test_classic_commands_are_datapoint_writes(changes, legacy):
    frame = JebaoProtocol._build_write(1, encode_write(changes))
    assert frame == legacy_frame(*legacy)


def test_decode_status():
    status = JebaoProtocol.parse_values(STATUS_MDP15000)
    assert status.switch_on and not status.feed_switch and not status.timer_on
    assert not status.wireless_mode
    assert status.auto_mode == AutoMode.RUN
    assert status.motor_speed == 57
    assert status.feed_time == 10
    assert status.auto_gears == 30
    assert status.clock == datetime(2026, 10, 15, 23, 34, 44)
    assert status.schedule_slots == [ScheduleSlot(0, 0, 0, 0, AutoMode.RUN, 100)]
    assert status.schedule[1] is None
    assert status.faults == ()


def test_decode_ignores_garbage_slots():
    values = bytearray(STATUS_MDP15000[10:])
    values[13 + 6 : 13 + 12] = bytes.fromhex("2cca3f0e0000")  # leftover RAM, not a slot
    values[13 + 12 : 13 + 18] = bytes.fromhex("000000ffff00")  # bad mode
    status = PumpStatus.from_values(bytes(values))
    assert status.schedule[1] is None and status.schedule[2] is None


def test_faults():
    values = bytearray(STATUS_MDP15000[10:])
    values[301] = 0b0010001
    assert PumpStatus.from_values(bytes(values)).faults == ("overcurrent", "locked_rotor")


def test_schedule_write():
    slots = parse_slots(
        [
            {"start": "08:00", "end": "12:00", "mode": "run", "speed": 70},
            {"start": "12:00", "end": "12:10", "mode": "feed"},
            {"start": "22:00", "end": "24:00", "mode": "off"},
        ]
    )
    payload = encode_write({**schedule_values(slots), "timer_on": True})
    flags, values = payload[:8], payload[8:]
    # ids 3 (TimerON) and 11-58 (AutoTime00-47)
    assert int.from_bytes(flags, "big") == (1 << 3) | (((1 << 48) - 1) << 11)
    assert values[0] == 0x08
    assert values[13:19] == bytes((8, 0, 12, 0, 1, 70))
    assert values[19:25] == bytes((12, 0, 12, 10, 2, 0))
    assert values[25:31] == bytes((22, 0, 24, 0, 0, 0))
    assert values[31:37] == UNUSED_SLOT
    assert values[295:301] == UNUSED_SLOT
    assert len(JebaoProtocol._build_write(7, payload)) == 323


def test_slot_validation():
    with pytest.raises(ValueError):
        ScheduleSlot(24, 0, 1, 0, AutoMode.RUN, 50)
    with pytest.raises(ValueError):
        ScheduleSlot(1, 0, 24, 30, AutoMode.RUN, 50)
    with pytest.raises(ValueError):
        ScheduleSlot(1, 0, 2, 0, AutoMode.FEED, 10)
    with pytest.raises(ValueError):  # overlap
        schedule_values(parse_slots([{"start": "8:00", "end": "12:00", "speed": 50},
                                     {"start": "11:00", "end": "13:00", "speed": 50}]))
    with pytest.raises(ValueError):  # no length
        schedule_values(parse_slots([{"start": "8:00", "end": "8:00", "speed": 50}]))
    with pytest.raises(ValueError):  # below the pumps' minimum speed
        schedule_values(parse_slots([{"start": "8:00", "end": "9:00", "speed": 20}]))
    with pytest.raises(ValueError):
        schedule_values([ScheduleSlot(1, 0, 2, 0, AutoMode.OFF)] * 49)


def test_clock():
    changes = encode_clock(datetime(2026, 10, 23, 16, 5, 9))
    assert changes == {"ymd": bytes((20, 26, 10, 23)), "hms": bytes((0, 16, 5, 9))}
    values = encode_write(changes)[8:]
    assert values[5:13] == bytes((20, 26, 10, 23, 0, 16, 5, 9))


def test_write_rejects_read_only_and_empty():
    with pytest.raises(KeyError):
        encode_write({"faults": 1})
    with pytest.raises(ValueError):
        encode_write({})
    with pytest.raises(ValueError):
        encode_write({"motor_speed": 300})


def test_active_slot():
    slots = parse_slots([
        {"start": "22:00", "end": "06:00", "speed": 40},  # past midnight
        {"start": "06:00", "end": "00:00", "speed": 80},  # to the end of the day
    ])
    assert schedule_state(slots, datetime(2026, 1, 1, 23, 0)) == {"auto_mode": 1, "auto_gears": 40}
    assert schedule_state(slots, datetime(2026, 1, 1, 5, 59)) == {"auto_mode": 1, "auto_gears": 40}
    assert schedule_state(slots, datetime(2026, 1, 1, 6, 0)) == {"auto_mode": 1, "auto_gears": 80}
    assert schedule_state(slots[1:], datetime(2026, 1, 1, 3, 0)) == {"auto_mode": 0}


def test_frame_command():
    from jebao.protocol import frame_command

    ack = bytes.fromhex("000000031f01009400000001")  # 1-byte length, flag 1
    assert frame_command(ack) == 0x94
    assert frame_command(STATUS_MDP15000) == 0x91
    assert frame_command(b"\x00\x00\x00\x03") is None
