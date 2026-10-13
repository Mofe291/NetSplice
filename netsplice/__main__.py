"""Command line: `python -m netsplice run CONFIG` or `python -m netsplice route CONFIG HOST PORT`."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import sys

from .config import Config, load_config
from .rules import Destination
from .server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="netsplice", description="Route connections direct or via upstream proxies by rule.")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="start the SOCKS5 listener")
    run.add_argument("config", help="path to a JSON config file")
    run.add_argument(
        "--allow-remote",
        action="store_true",
        help="permit listening on a non-loopback address; anyone who can reach it can use this proxy",
    )

    route = commands.add_parser("route", help="print the action a destination would get, without connecting")
    route.add_argument("config", help="path to a JSON config file")
    route.add_argument("host")
    route.add_argument("port", type=int)

    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except (OSError, ValueError) as err:
        parser.error(f"cannot load config: {err}")

    if args.command == "route":
        print(config.router.decide(Destination(args.host, args.port)))
        return 0

    if not _is_loopback(config.listen_host) and not args.allow_remote:
        parser.error(f"refusing to listen on {config.listen_host!r} without --allow-remote")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        pass
    return 0


async def _run(config: Config) -> None:
    server = await serve(config)
    port = server.sockets[0].getsockname()[1]
    logging.info("listening on %s:%d", config.listen_host, port)
    async with server:
        await server.serve_forever()


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


if __name__ == "__main__":
    sys.exit(main())
