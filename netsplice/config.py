"""Load and validate a NetSplice JSON config."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .rules import BLOCK, DIRECT, Router, parse_rule

UPSTREAM_KINDS = frozenset({"socks5", "http"})
DEFAULT_LISTEN = "127.0.0.1:1080"


@dataclass(frozen=True)
class Upstream:
    name: str
    kind: str  # "socks5" or "http" (HTTP CONNECT proxy)
    host: str
    port: int


@dataclass(frozen=True)
class Config:
    listen_host: str
    listen_port: int
    router: Router
    upstreams: dict[str, Upstream] = field(default_factory=dict)


def load_config(path: str | Path) -> Config:
    return parse_config(json.loads(Path(path).read_text(encoding="utf-8")))


def parse_config(data: Any) -> Config:
    """Build a Config from decoded JSON.

    Example::

        {
          "listen": "127.0.0.1:1080",
          "default": "direct",
          "upstreams": {"corp": {"type": "http", "host": "proxy.corp", "port": 3128}},
          "rules": [
            {"action": "direct", "match": {"domain": ["intranet.example"]}},
            {"action": "block",  "match": {"port": [25]}},
            {"action": "corp",   "match": {"domain": ["example.com"]}},
            {"action": "direct", "match": {"cidr": ["10.0.0.0/8", "192.168.0.0/16"]}}
          ]
        }
    """
    if not isinstance(data, dict):
        raise ValueError("config must be a JSON object")

    listen_host, listen_port = _split_hostport(data.get("listen", DEFAULT_LISTEN))

    upstreams: dict[str, Upstream] = {}
    for name, spec in (data.get("upstreams") or {}).items():
        if name in (DIRECT, BLOCK):
            raise ValueError(f"upstream name {name!r} is reserved")
        if not isinstance(spec, dict):
            raise ValueError(f"upstream {name!r} must be a JSON object")
        kind = spec.get("type")
        if kind not in UPSTREAM_KINDS:
            raise ValueError(f"upstream {name!r} type must be one of {sorted(UPSTREAM_KINDS)}")
        upstreams[name] = Upstream(name, kind, _required_str(spec, "host", name), _port(spec, name))

    rules = tuple(parse_rule(r) for r in data.get("rules") or [])
    default = data.get("default", DIRECT)
    router = Router(rules=rules, default=default)

    known = {DIRECT, BLOCK, *upstreams}
    for action in (default, *(r.action for r in rules)):
        if action not in known:
            raise ValueError(f"unknown action {action!r}; expected direct, block or an upstream name")

    return Config(listen_host, listen_port, router, upstreams)


def _split_hostport(value: str) -> tuple[str, int]:
    host, sep, port = str(value).rpartition(":")
    if not sep or not host:
        raise ValueError(f"listen address must look like host:port, got {value!r}")
    try:
        number = int(port)
    except ValueError:
        raise ValueError(f"invalid listen port in {value!r}") from None
    if not 0 <= number <= 65535:
        raise ValueError(f"listen port out of range in {value!r}")
    return host, number


def _required_str(spec: dict[str, Any], key: str, name: str) -> str:
    value = spec.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"upstream {name!r} needs a string {key!r}")
    return value


def _port(spec: dict[str, Any], name: str) -> int:
    value = spec.get("port")
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
        raise ValueError(f"upstream {name!r} needs an integer 'port' 1-65535")
    return value
