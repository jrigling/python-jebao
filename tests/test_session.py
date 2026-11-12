"""Session hygiene, passcode reuse, state flags and discovery parsing (0.2.1)."""
import asyncio

import pytest

from jebao import DeviceState, MDP20000Device
from jebao.discovery import JebaoDiscovery
from jebao.exceptions import JebaoAuthenticationError

from test_protocol import PASSCODE, FakePump, pump  # noqa: F401  (fixture)


def device(pump, **kw):
    return MDP20000Device("127.0.0.1", port=pump.port, device_id="test", **kw)


async def test_passcode_learned_once_then_reused(pump):
    d = device(pump)
    await d.update()
    assert d.passcode == PASSCODE
    for _ in range(3):
        await d.update()
    assert pump.passcode_requests == 1


async def test_saved_passcode_works_after_binding_window_closes(pump):
    """Pump stopped handing out its passcode; a saved one still logs in."""
    pump.binding_closed = True
    d = device(pump, passcode=PASSCODE)
    await d.update()
    assert d.speed == 70 and pump.passcode_requests == 0


async def test_refused_saved_passcode_falls_back_to_asking(pump):
    d = device(pump, passcode=b"WRONGWRONG")
    await d.update()
    assert d.passcode == PASSCODE and pump.passcode_requests == 1


async def test_closed_binding_window_without_saved_passcode_explains(pump):
    pump.binding_closed = True
    d = device(pump)
    with pytest.raises(JebaoAuthenticationError, match="power-cycle"):
        await d.update()


async def test_fixed_local_port_is_reused(pump, unused_tcp_port):
    d = device(pump, local_port=unused_tcp_port)
    for _ in range(4):
        await d.update()
    assert pump.peer_ports == [unused_tcp_port] * 4


async def test_local_port_in_use_falls_back(pump):
    blocker = await asyncio.start_server(lambda r, w: None, "0.0.0.0", 0)
    taken = blocker.sockets[0].getsockname()[1]
    try:
        d = device(pump, local_port=taken)
        await d.update()
        assert pump.peer_ports[-1] != taken
    finally:
        blocker.close()
        await blocker.wait_closed()


async def test_sessions_closed_with_reset(pump):
    """SO_LINGER 0: the pump sees a reset, so no session is left half-open."""
    d = device(pump)
    await d.update()
    await asyncio.sleep(0.05)
    assert pump.connections == 1


@pytest.mark.parametrize(
    "flags,state,on,program,feed",
    [
        (0x10, DeviceState.OFF, False, False, False),
        (0x11, DeviceState.ON, True, False, False),
        (0x15, DeviceState.FEED, True, False, True),
        (0x19, DeviceState.PROGRAM, True, True, False),
        (0x18, DeviceState.PROGRAM, False, True, False),  # schedule, stopped slot
        (0x29, DeviceState.PROGRAM, True, True, False),  # other AutoMode bits
        (0x13, DeviceState.ON, True, False, False),  # wireless-mode bit
    ],
)
async def test_state_byte_is_flags(pump, flags, state, on, program, feed):
    pump.state = flags
    d = device(pump)
    await d.update()
    assert (d.state, d.is_on, d.is_program_mode, d.is_feed_mode) == (state, on, program, feed)
    assert d.state_flags == flags


async def test_manual_command_leaves_schedule_first(pump):
    pump.state = 0x18
    d = device(pump)
    await d.update()
    await d.set_speed(50)
    assert pump.commands[0][0] == 0x28  # EXIT_PROGRAM ...
    assert pump.commands[-1] == bytes([0x20, 0, 50, 0])  # ... then the speed


# Real discovery replies (device IDs/MACs as on the wire, 2026-11-10).
REPLY_412 = bytes.fromhex(
    "000000037a000004" "0016" + b"t4RoEiuukqgYeC8hwF7QUS".hex() + "0006" "4417932a5c5f"
    "0008" + b"04020B28".hex() + "0020" + b"02039876751049deb404d1d89221ec4b".hex()
    + "0000000275736170692e67697a776974732e636f6d3a383000342e312e32003033303330303030"
)
REPLY_414 = bytes.fromhex(
    "000000037a000004" "0016" + b"vrTf9Us8TeDaXYX9y8PBJ7".hex() + "0006" "24ec4ad5a4f0"
    "0008" + b"04D30Q34".hex() + "0020" + b"6a5c47b3ea364ecb841b47f5997a1775".hex()
    + "0000000275736170692e67697a776974732e636f6d3a383000342e312e34003033303330303030"
)
REPLY_ECP = REPLY_412.replace(
    b"02039876751049deb404d1d89221ec4b", b"1d8c63eaccac4205b92c84d77d5a08fb"
)
REPLY_MDP3500 = REPLY_414.replace(b"04D30Q34", b"04020826")


@pytest.mark.parametrize(
    "data,model,device_id,mac",
    [
        (REPLY_412, "MDP-20000", "t4RoEiuukqgYeC8hwF7QUS", "44:17:93:2a:5c:5f"),
        (REPLY_414, "MDP-20000", "vrTf9Us8TeDaXYX9y8PBJ7", "24:ec:4a:d5:a4:f0"),
        (REPLY_MDP3500, "MDP-20000", "vrTf9Us8TeDaXYX9y8PBJ7", "24:ec:4a:d5:a4:f0"),
        (REPLY_ECP, "Unknown (1d8c63eaccac4205b92c84d77d5a08fb)", None, None),
    ],
)
def test_discovery_models_by_gizwits_key(data, model, device_id, mac):
    d = JebaoDiscovery._parse_discovery_response(data, "10.0.0.5")
    assert d.model == model
    assert d.is_mdp20000 == (model == "MDP-20000")
    if device_id:
        assert (d.device_id, d.mac_address) == (device_id, mac)
    assert len(d.product_key) == 32 and len(d.firmware_version) == 8
