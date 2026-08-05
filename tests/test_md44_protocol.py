import pytest

from jebao.md44_protocol import (
    MD44Dose,
    encode_day_interval,
    encode_dose_schedule,
    encode_head_power,
    encode_login,
    encode_schedule_enabled,
    extract_passcode,
    frame_length,
    parse_status,
)


def test_authentication_and_long_frame_length():
    response = bytes.fromhex("000000030f000007000a4142434445464748494a")
    assert extract_passcode(response) == b"ABCDEFGHIJ"
    assert encode_login(b"ABCDEFGHIJ").hex() == "000000030f000008000a4142434445464748494a"
    assert frame_length(bytes.fromhex("00000003a206")) == 808


@pytest.mark.parametrize(
    "channel,on,expected",
    [
        (1, True, "00020000020000"),
        (2, True, "00040000040000"),
        (3, False, "00080000000000"),
        (4, False, "00100000000000"),
    ],
)
def test_four_verified_head_power_maps(channel, on, expected):
    assert encode_head_power(channel, on, 6)[17:24].hex() == expected


def test_schedule_frames_use_verified_head_tables():
    for channel, table, selector, flag in (
        (1, 32, 0x20, 0x02),
        (2, 128, 0x40, 0x04),
        (3, 224, 0x80, 0x08),
        (4, 320, 0x100, 0x10),
    ):
        interval = encode_day_interval(channel, channel, 1)
        assert interval[16] == 0x10 << (channel - 1)
        assert interval[22 + channel] == channel
        schedule = encode_dose_schedule(channel, [MD44Dose(14, 20 + channel, channel)], 2)
        assert int.from_bytes(schedule[14:16], "big") == selector
        assert schedule[table : table + 4] == bytes([14, 20 + channel, 0, channel])
        enabled = encode_schedule_enabled(channel, True, 3)
        assert enabled[17] == flag
        assert enabled[20] == flag


def test_status_decodes_running_and_schedule_bits():
    frame = bytes.fromhex("00000003070000910300020a")
    status = parse_status(frame)
    assert status.is_running(1)
    assert not status.is_running(2)
    assert status.is_running(3)
    assert status.is_schedule_enabled(1)


def test_schedule_validation():
    with pytest.raises(ValueError, match="20 doses"):
        encode_dose_schedule(1, [MD44Dose(1, 0, 1)] * 21, 1)
    with pytest.raises(ValueError, match="channel"):
        encode_head_power(5, True, 1)
