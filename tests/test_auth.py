"""Login against a fake pump that behaves like GAgent's LAN server.

GAgent only hands out the passcode in its binding window after power-up; after
that it replies with an empty passcode and closes. Login works any time.
"""
import asyncio

import pytest

from jebao.exceptions import JebaoAuthenticationError, JebaoTimeoutError
from jebao.protocol import JebaoProtocol

PASSCODE = b"JJRSBSOEEL"
PASSCODE_REQUEST = bytes.fromhex("00000003050000060100")


class FakePump:
    def __init__(self, binding: bool, answer_login: bool = True):
        self.binding = binding
        self.answer_login = answer_login
        self.passcode_requests = 0
        self.logins = []

    async def handle(self, reader, writer):
        try:
            while True:
                head = await reader.readexactly(5)
                body = await reader.readexactly(head[4])
                cmd = body[1:3]
                if cmd == b"\x00\x06":
                    self.passcode_requests += 1
                    if self.binding:
                        writer.write(bytes.fromhex("000000030f000007000a") + PASSCODE)
                        await writer.drain()
                    else:
                        writer.write(bytes.fromhex("00000003050000070000"))
                        await writer.drain()
                        break
                elif cmd == b"\x00\x08":
                    ok = body[5:15] == PASSCODE
                    self.logins.append(body[5:15])
                    if not self.answer_login:
                        continue
                    writer.write(bytes.fromhex("00000003040000090" + ("0" if ok else "1")))
                    await writer.drain()
                    if not ok:
                        break
        except asyncio.IncompleteReadError:
            pass
        writer.close()

    async def connect(self, passcode=None):
        server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        protocol = JebaoProtocol("127.0.0.1", port, passcode=passcode)
        try:
            await protocol.connect(timeout=2)
            return protocol
        finally:
            await protocol.disconnect()
            server.close()


def test_first_login_reads_and_keeps_passcode():
    pump = FakePump(binding=True)
    protocol = asyncio.run(pump.connect())
    assert protocol.passcode == PASSCODE.decode()
    assert pump.passcode_requests == 1


def test_known_passcode_logs_in_after_binding_window():
    pump = FakePump(binding=False)
    asyncio.run(pump.connect(passcode=PASSCODE.decode()))
    assert pump.passcode_requests == 0
    assert pump.logins == [PASSCODE]


def test_wrong_passcode_falls_back_to_asking():
    pump = FakePump(binding=True)
    protocol = asyncio.run(pump.connect(passcode="WRONGWRONG"))
    assert protocol.passcode == PASSCODE.decode()
    assert pump.logins == [b"WRONGWRONG", PASSCODE]


def test_closed_binding_window_without_passcode_says_so():
    pump = FakePump(binding=False)
    with pytest.raises(JebaoAuthenticationError, match="binding window"):
        asyncio.run(pump.connect())


def test_login_timeout_does_not_ask_for_passcode():
    pump = FakePump(binding=False, answer_login=False)
    with pytest.raises(JebaoTimeoutError, match="Login timed out"):
        asyncio.run(pump.connect(passcode=PASSCODE.decode()))
    assert len(pump.logins) == 1
    assert pump.passcode_requests == 0
