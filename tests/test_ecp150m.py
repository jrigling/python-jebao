"""Tests for ECP-150M status decoding and discovery."""

import datetime

import pytest

from jebao.const import MODEL_ECP150M, MODEL_MDP20000
from jebao.discovery import JebaoDiscovery
from jebao.ecp150m import (
    AutoMode,
    ECP150MSegment,
    ECP150MStatus,
    Fault,
    Linkage,
    Mode,
)
from jebao.exceptions import JebaoCommandError

# Full 401-byte statuses read from the two pumps
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


def with_flags(flags: int) -> bytes:
    return flags.to_bytes(2, "big") + PUMP2[2:]


def test_pump1_program():
    status = ECP150MStatus.from_bytes(PUMP1)
    assert status.is_on
    assert status.is_program_mode
    assert status.is_auto_pulse_tide
    assert not status.is_feed_active
    assert status.mode is Mode.SINE
    assert status.linkage is Linkage.INDEPENDENT
    assert status.auto_mode is AutoMode.SINE
    assert (status.flow, status.frequency, status.feed_time) == (30, 100, 10)
    assert (status.auto_flow, status.auto_frequency) == (30, 100)
    assert status.segments == (
        ECP150MSegment(0, 10, 10, 10, 0, 2, 30, 100, 1),
        ECP150MSegment(1, 10, 0, 10, 10, 2, 30, 100, 0),
    )
    assert status.segments[0].auto_mode is AutoMode.SINE
    assert status.date == datetime.date(2024, 5, 19)
    assert status.time == datetime.time(4, 23, 52)
    assert status.faults == Fault(0)


def test_pump2_manual_feed():
    status = ECP150MStatus.from_bytes(PUMP2)
    assert status.is_on
    assert status.is_feed_active
    assert not status.is_program_mode
    assert status.mode is Mode.CONSTANT
    assert status.linkage is Linkage.INDEPENDENT
    assert status.auto_mode is AutoMode.STOP
    assert (status.flow, status.frequency, status.feed_time) == (57, 50, 20)
    assert status.segments == (ECP150MSegment(0, 9, 0, 22, 0, 3, 100, 100, 0),)
    # Segment mode 0x03 is the app's red mode
    assert status.segments[0].auto_mode is AutoMode.RANDOM
    assert status.time == datetime.time(4, 28, 35)
    assert status.raw == PUMP2


def test_linkage_master():
    # Read once while pump 1 was switched to master
    status = ECP150MStatus.from_bytes(with_flags(0x04B1))
    assert status.linkage is Linkage.MASTER
    assert not status.is_program_mode
    assert status.mode is Mode.SINE


@pytest.mark.parametrize("flags,auto_mode", [(0x0121, AutoMode.STOP), (0x0721, AutoMode.RANDOM)])
def test_linkage_slave(flags, auto_mode):
    # Pump 2 as slave read with both words; only bits 9-11 differ
    status = ECP150MStatus.from_bytes(with_flags(flags))
    assert status.linkage is Linkage.SLAVE
    assert status.auto_mode is auto_mode
    assert status.mode is Mode.SINE


@pytest.mark.parametrize(
    "flags,mode",
    [(0x01, Mode.CLASSIC), (0x21, Mode.SINE), (0x41, Mode.RANDOM), (0x61, Mode.CONSTANT)],
)
def test_mode_bits(flags, mode):
    assert ECP150MStatus.from_bytes(with_flags(flags)).mode is mode


def test_values_outside_schema():
    status = ECP150MStatus.from_bytes(with_flags(0x0E00 | 0x0180))
    assert status.linkage is None
    assert status.auto_mode is None


def test_all_48_segments():
    status = bytes(8) + bytes([6, 0, 7, 0, 2, 50, 50, 0]) * 48 + PUMP2[392:]
    segments = ECP150MStatus.from_bytes(status).segments
    assert [segment.index for segment in segments] == list(range(48))


def test_unused_segments_are_skipped():
    status = bytearray(PUMP1)
    status[8:16] = b"\xee" * 8
    assert [segment.index for segment in ECP150MStatus.from_bytes(bytes(status)).segments] == [1]


def test_faults():
    status = PUMP2[:400] + bytes([0x51])
    faults = ECP150MStatus.from_bytes(status).faults
    assert faults == Fault.OVERCURRENT | Fault.LOCKED_ROTOR | Fault.UART


def test_zeroed_date_and_time():
    status = ECP150MStatus.from_bytes(PUMP2[:392] + bytes(9))
    assert status.date is None
    assert status.time == datetime.time(0, 0, 0)


def test_rejects_wrong_length():
    with pytest.raises(JebaoCommandError):
        ECP150MStatus.from_bytes(PUMP2[:-1])


def discovery_response(module: bytes, product_key: bytes) -> bytes:
    def field(value: bytes) -> bytes:
        return len(value).to_bytes(2, "big") + value

    body = (
        bytes.fromhex("000004")
        + field(b"A" * 22)
        + field(bytes.fromhex("020000000001"))
        + field(module)
        + field(product_key)
        + bytes(8)
        + b"euapi.gizwits.com:80\x004.1.2\x0003030000\x00"
    )
    return bytes.fromhex("00000003") + bytes([len(body)]) + body


def test_discovery_recognizes_ecp150m():
    response = discovery_response(b"04020B28", b"1d8c63eaccac4205b92c84d77d5a08fb")
    device = JebaoDiscovery._parse_discovery_response(response, "192.0.2.10")
    assert device is not None
    assert device.model == MODEL_ECP150M
    assert device.is_ecp150m
    assert not device.is_mdp20000
    assert device.mac_address == "02:00:00:00:00:01"


def test_discovery_keeps_mdp20000():
    # Same third field as the ECP-150M, different product key
    response = discovery_response(b"04020B28", b"0" * 32)
    device = JebaoDiscovery._parse_discovery_response(response, "192.0.2.10")
    assert device is not None
    assert device.model == MODEL_MDP20000
    assert not device.is_ecp150m
