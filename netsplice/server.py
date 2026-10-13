"""SOCKS5 listener that routes each CONNECT request by rule (RFC 1928, no-auth, CONNECT only)."""

from __future__ import annotations

import asyncio
import ipaddress
import logging

from .config import Config, Upstream
from .rules import BLOCK, DIRECT, Destination

log = logging.getLogger("netsplice")

SOCKS_VERSION = 0x05
NO_AUTH = 0x00
NO_ACCEPTABLE_AUTH = 0xFF
CMD_CONNECT = 0x01

REP_SUCCEEDED = 0x00
REP_GENERAL_FAILURE = 0x01
REP_NOT_ALLOWED = 0x02
REP_HOST_UNREACHABLE = 0x04
REP_COMMAND_NOT_SUPPORTED = 0x07
REP_ATYP_NOT_SUPPORTED = 0x08


class SocksError(Exception):
    """A request we refuse; `code` is the SOCKS5 reply sent back to the client."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


async def serve(config: Config) -> asyncio.Server:
    async def on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await handle_client(reader, writer, config)

    return await asyncio.start_server(on_client, config.listen_host, config.listen_port)


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, config: Config) -> None:
    try:
        try:
            dest = await _read_request(reader, writer)
            action = config.router.decide(dest)
            log.info("%s:%d -> %s", dest.host, dest.port, action)
            if action == BLOCK:
                raise SocksError(REP_NOT_ALLOWED)
            up_reader, up_writer = await _open_route(action, dest, config)
        except SocksError as err:
            await _reply(writer, err.code)
            return

        await _reply(writer, REP_SUCCEEDED)
        try:
            await _relay(reader, writer, up_reader, up_writer)
        finally:
            up_writer.close()
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        pass  # client went away or spoke something other than SOCKS5
    finally:
        writer.close()


async def _read_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> Destination:
    ver, nmethods = await reader.readexactly(2)
    if ver != SOCKS_VERSION:
        raise ConnectionError(f"not a SOCKS5 client (version {ver})")
    methods = await reader.readexactly(nmethods)
    if NO_AUTH not in methods:
        writer.write(bytes([SOCKS_VERSION, NO_ACCEPTABLE_AUTH]))
        await writer.drain()
        raise ConnectionError("client offered no supported auth method")
    writer.write(bytes([SOCKS_VERSION, NO_AUTH]))
    await writer.drain()

    ver, cmd, _reserved, atyp = await reader.readexactly(4)
    if ver != SOCKS_VERSION:
        raise ConnectionError("bad SOCKS5 request header")
    if atyp == 0x01:
        host = str(ipaddress.IPv4Address(await reader.readexactly(4)))
    elif atyp == 0x04:
        host = str(ipaddress.IPv6Address(await reader.readexactly(16)))
    elif atyp == 0x03:
        length = (await reader.readexactly(1))[0]
        try:
            host = (await reader.readexactly(length)).decode("ascii")
        except UnicodeDecodeError:
            raise SocksError(REP_GENERAL_FAILURE) from None
    else:
        raise SocksError(REP_ATYP_NOT_SUPPORTED)
    port = int.from_bytes(await reader.readexactly(2), "big")
    if cmd != CMD_CONNECT:
        raise SocksError(REP_COMMAND_NOT_SUPPORTED)
    return Destination(host, port)


async def _reply(writer: asyncio.StreamWriter, code: int) -> None:
    # The bound address is not meaningful for a proxied connection, so report 0.0.0.0:0.
    writer.write(bytes([SOCKS_VERSION, code, 0x00, 0x01]) + bytes(6))
    await writer.drain()


async def _open_route(action: str, dest: Destination, config: Config) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    try:
        if action == DIRECT:
            return await asyncio.open_connection(dest.host, dest.port)
        upstream = config.upstreams[action]
        if upstream.kind == "socks5":
            return await _via_socks5(upstream, dest)
        return await _via_http_connect(upstream, dest)
    except (OSError, ValueError, EOFError) as err:
        log.warning("%s:%d via %s failed: %s", dest.host, dest.port, action, err)
        raise SocksError(REP_HOST_UNREACHABLE) from err


async def _via_socks5(upstream: Upstream, dest: Destination) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection(upstream.host, upstream.port)
    try:
        writer.write(bytes([SOCKS_VERSION, 1, NO_AUTH]))
        await writer.drain()
        if await reader.readexactly(2) != bytes([SOCKS_VERSION, NO_AUTH]):
            raise OSError("upstream SOCKS5 proxy requires authentication")

        # Send the hostname unresolved when we have one, so the upstream does the DNS lookup.
        request = bytes([SOCKS_VERSION, CMD_CONNECT, 0x00]) + _encode_address(dest.host)
        writer.write(request + dest.port.to_bytes(2, "big"))
        await writer.drain()

        ver, rep, _reserved, atyp = await reader.readexactly(4)
        if ver != SOCKS_VERSION or rep != REP_SUCCEEDED:
            raise OSError(f"upstream SOCKS5 proxy refused connection (reply {rep})")
        await _skip_bound_address(reader, atyp)
    except BaseException:
        writer.close()
        raise
    return reader, writer


async def _via_http_connect(upstream: Upstream, dest: Destination) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection(upstream.host, upstream.port)
    try:
        target = f"[{dest.host}]" if ":" in dest.host else dest.host
        authority = f"{target}:{dest.port}"
        writer.write(f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode("ascii"))
        await writer.drain()
        head = await reader.readuntil(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].split()
        if len(status) < 2 or status[1] != b"200":
            raise OSError(f"upstream HTTP proxy refused CONNECT: {status[:2]!r}")
    except BaseException:
        writer.close()
        raise
    return reader, writer


def _encode_address(host: str) -> bytes:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raw = host.encode("ascii")
        if not 0 < len(raw) <= 255:
            raise ValueError(f"hostname length out of range: {host!r}") from None
        return bytes([0x03, len(raw)]) + raw
    return (b"\x01" if ip.version == 4 else b"\x04") + ip.packed


async def _skip_bound_address(reader: asyncio.StreamReader, atyp: int) -> None:
    if atyp == 0x01:
        length = 4
    elif atyp == 0x04:
        length = 16
    elif atyp == 0x03:
        length = (await reader.readexactly(1))[0]
    else:
        raise OSError(f"upstream SOCKS5 sent unknown address type {atyp}")
    await reader.readexactly(length + 2)


async def _relay(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    up_reader: asyncio.StreamReader,
    up_writer: asyncio.StreamWriter,
) -> None:
    """Copy bytes both ways until either side closes. The other direction is then cancelled."""
    tasks = [
        asyncio.create_task(_pipe(client_reader, up_writer)),
        asyncio.create_task(_pipe(up_reader, client_writer)),
    ]
    _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
