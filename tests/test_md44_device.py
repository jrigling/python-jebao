import asyncio

import pytest

from jebao import MD44Device


def status_frame(running_bitmap: int = 0, schedule_bitmap: int = 0) -> bytes:
    return bytes.fromhex("000000030700009103") + bytes([0, schedule_bitmap, running_bitmap])


@pytest.mark.asyncio
async def test_device_authenticates_updates_and_confirms_manual_control():
    running = False

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        nonlocal running
        assert await reader.readexactly(8) == bytes.fromhex("0000000303000006")
        writer.write(bytes.fromhex("000000030f000007000a4142434445464748494a"))
        await writer.drain()

        assert (await reader.readexactly(20))[7] == 0x08
        writer.write(bytes.fromhex("0000000303000009"))
        await writer.drain()

        while True:
            try:
                prefix = await reader.readexactly(9)
            except asyncio.IncompleteReadError:
                break
            if prefix == bytes.fromhex("000000030400009002"):
                writer.write(status_frame(0x02 if running else 0))
            else:
                assert prefix[:9] == bytes.fromhex("00000003a206000093")
                await reader.readexactly(799)
                running = True
                writer.write(bytes.fromhex("0000000303000094"))
            await writer.drain()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        async with MD44Device("127.0.0.1", port=port) as doser:
            assert not doser.channels[0].is_running
            await doser.channels[0].start()
            assert doser.channels[0].is_running
