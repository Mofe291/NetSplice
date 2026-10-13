import asyncio
import unittest

from netsplice.config import parse_config
from netsplice.server import _relay, serve

SOCKS_OK = 0x00
SOCKS_NOT_ALLOWED = 0x02
SOCKS_HOST_UNREACHABLE = 0x04


async def start_echo_server() -> asyncio.Server:
    async def echo(reader, writer):
        try:
            while data := await reader.read(1024):
                writer.write(data)
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()

    return await asyncio.start_server(echo, "127.0.0.1", 0)


async def start_splitter(config: dict) -> tuple[asyncio.Server, int]:
    server = await serve(parse_config({"listen": "127.0.0.1:0", **config}))
    return server, server.sockets[0].getsockname()[1]


async def start_http_connect_proxy() -> asyncio.Server:
    """Minimal HTTP CONNECT proxy: accepts any CONNECT and tunnels it."""

    async def handle(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        target = head.split(b"\r\n", 1)[0].split()[1].decode()
        host, _, port = target.rpartition(":")
        try:
            up_reader, up_writer = await asyncio.open_connection(host, int(port))
        except OSError:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            writer.close()
            return
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await _relay(reader, writer, up_reader, up_writer)
        writer.close()
        up_writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


async def socks5_connect(port: int, host: str, dport: int):
    """Open a SOCKS5 CONNECT through the splitter. Returns (reader, writer, reply_code)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"\x05\x01\x00")
    await writer.drain()
    assert await reader.readexactly(2) == b"\x05\x00"

    raw = host.encode("ascii")
    writer.write(b"\x05\x01\x00\x03" + bytes([len(raw)]) + raw + dport.to_bytes(2, "big"))
    await writer.drain()
    _ver, code, _rsv, atyp = await reader.readexactly(4)
    if atyp == 0x01:
        await reader.readexactly(6)
    elif atyp == 0x04:
        await reader.readexactly(18)
    else:
        await reader.readexactly((await reader.readexactly(1))[0] + 2)
    return reader, writer, code


async def echo_roundtrip(reader, writer) -> bytes:
    writer.write(b"hello")
    await writer.drain()
    data = await reader.readexactly(5)
    writer.close()
    return data


class SplitterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.echo = await start_echo_server()
        self.echo_port = self.echo.sockets[0].getsockname()[1]
        self.addAsyncCleanup(self._close, self.echo)

    async def _close(self, server):
        server.close()

    async def splitter(self, config: dict) -> int:
        server, port = await start_splitter(config)
        self.addAsyncCleanup(self._close, server)
        return port

    async def test_direct_route_relays_bytes(self):
        port = await self.splitter({"default": "direct"})
        reader, writer, code = await socks5_connect(port, "127.0.0.1", self.echo_port)
        self.assertEqual(code, SOCKS_OK)
        self.assertEqual(await echo_roundtrip(reader, writer), b"hello")

    async def test_block_rule_refuses_connection(self):
        port = await self.splitter({"rules": [{"action": "block", "match": {"port": [self.echo_port]}}]})
        _reader, writer, code = await socks5_connect(port, "127.0.0.1", self.echo_port)
        self.assertEqual(code, SOCKS_NOT_ALLOWED)
        writer.close()

    async def test_domain_rule_selects_socks5_upstream(self):
        # Upstream is another splitter that blocks everything, so a successful echo
        # proves the connection really went through it.
        upstream_port = await self.splitter({"default": "block"})
        port = await self.splitter({
            "upstreams": {"up": {"type": "socks5", "host": "127.0.0.1", "port": upstream_port}},
            "rules": [{"action": "up", "match": {"domain": ["localhost"]}}],
        })
        _reader, writer, code = await socks5_connect(port, "localhost", self.echo_port)
        self.assertEqual(code, SOCKS_HOST_UNREACHABLE)
        writer.close()

    async def test_socks5_upstream_chain_relays_bytes(self):
        upstream_port = await self.splitter({"default": "direct"})
        port = await self.splitter({
            "upstreams": {"up": {"type": "socks5", "host": "127.0.0.1", "port": upstream_port}},
            "rules": [{"action": "up", "match": {"domain": ["localhost"]}}],
        })
        reader, writer, code = await socks5_connect(port, "localhost", self.echo_port)
        self.assertEqual(code, SOCKS_OK)
        self.assertEqual(await echo_roundtrip(reader, writer), b"hello")

    async def test_http_connect_upstream_relays_bytes(self):
        proxy = await start_http_connect_proxy()
        self.addAsyncCleanup(self._close, proxy)
        proxy_port = proxy.sockets[0].getsockname()[1]
        port = await self.splitter({
            "upstreams": {"corp": {"type": "http", "host": "127.0.0.1", "port": proxy_port}},
            "rules": [{"action": "corp", "match": {"cidr": ["127.0.0.0/8"]}}],
        })
        reader, writer, code = await socks5_connect(port, "127.0.0.1", self.echo_port)
        self.assertEqual(code, SOCKS_OK)
        self.assertEqual(await echo_roundtrip(reader, writer), b"hello")

    async def test_unreachable_target_reports_host_unreachable(self):
        port = await self.splitter({"default": "direct"})
        closed = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        dead_port = closed.sockets[0].getsockname()[1]
        closed.close()
        _reader, writer, code = await socks5_connect(port, "127.0.0.1", dead_port)
        self.assertEqual(code, SOCKS_HOST_UNREACHABLE)
        writer.close()

    async def test_non_socks_client_is_dropped(self):
        port = await self.splitter({"default": "direct"})
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET / HTTP/1.1\r\n\r\n")
        await writer.drain()
        self.assertEqual(await reader.read(), b"")
        writer.close()


if __name__ == "__main__":
    unittest.main()
