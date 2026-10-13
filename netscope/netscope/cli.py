import argparse
import socket
import sys

from . import __version__
from .devices import DeviceError, Registry, adb_connect
from .server import serve


def _lan_addresses():
    addrs = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


def cmd_serve(args):
    server, token = serve(args.host, args.port, args.token, args.interval)
    suffix = f"/?token={token}" if token else "/"
    hosts = ["127.0.0.1"] if args.host in ("127.0.0.1", "localhost") else ["127.0.0.1", *_lan_addresses()]
    print(f"NetScope {__version__} dashboard:")
    for h in hosts:
        print(f"  http://{h}:{args.port}{suffix}")
    if token:
        print("Anyone with this token can see and control connected phones; keep it private.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def cmd_connect(args):
    try:
        print(adb_connect(args.address, args.pair, args.code))
    except DeviceError as e:
        sys.exit(f"error: {e}")


def cmd_devices(args):
    devices = Registry().refresh()
    if not devices:
        print("No devices. Connect one with `netscope connect <phone-ip>:<port>`.")
    for d in devices:
        s = d.summary()
        print(f"{s['platform']:8} {s['id']:40} {s.get('state', ''):12} {s['name']}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="netscope", description="Watch and drive phones over Wi-Fi/Tailscale.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the dashboard")
    s.add_argument("--host", default="127.0.0.1",
                   help="bind address (use 0.0.0.0 or your Tailscale IP to open it to other machines)")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--token", help="access token (auto-generated when not bound to localhost)")
    s.add_argument("--interval", type=float, default=1.0, help="metrics sample interval in seconds")
    s.set_defaults(func=cmd_serve)

    c = sub.add_parser("connect", help="pair/connect an Android phone over the network")
    c.add_argument("address", nargs="?", help="phone IP:port shown under Wireless debugging")
    c.add_argument("--pair", metavar="IP:PORT", help="pairing address from 'Pair device with pairing code'")
    c.add_argument("--code", help="6-digit pairing code")
    c.set_defaults(func=cmd_connect)

    d = sub.add_parser("devices", help="list reachable devices")
    d.set_defaults(func=cmd_devices)

    args = p.parse_args(argv)
    args.func(args)
