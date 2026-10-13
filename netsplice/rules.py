"""Routing rules: decide, per destination, whether a connection goes direct, is blocked, or uses a named upstream."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any, Union

DIRECT = "direct"
BLOCK = "block"
MATCH_KEYS = frozenset({"domain", "cidr", "port"})

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]
IPNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


@dataclass(frozen=True)
class Destination:
    host: str
    port: int


def parse_ip(host: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def domain_matches(host: str, pattern: str) -> bool:
    """`example.com` matches `example.com` and any subdomain of it."""
    host = host.lower().rstrip(".")
    return host == pattern or host.endswith("." + pattern)


@dataclass(frozen=True)
class Rule:
    """All given conditions must match (AND); within one condition any listed value matches (OR).

    A rule with no conditions matches every destination, so it works as a catch-all.
    """

    action: str
    domains: tuple[str, ...] = ()
    networks: tuple[IPNetwork, ...] = ()
    ports: frozenset[int] = frozenset()

    def matches(self, dest: Destination) -> bool:
        if self.ports and dest.port not in self.ports:
            return False
        if self.domains and not any(domain_matches(dest.host, d) for d in self.domains):
            return False
        if self.networks:
            ip = parse_ip(dest.host)
            if ip is None or not any(ip in net for net in self.networks):
                return False
        return True


@dataclass(frozen=True)
class Router:
    rules: tuple[Rule, ...]
    default: str = DIRECT

    def decide(self, dest: Destination) -> str:
        """Return the action for the first matching rule, or the default action."""
        for rule in self.rules:
            if rule.matches(dest):
                return rule.action
        return self.default


def parse_rule(data: dict[str, Any]) -> Rule:
    if not isinstance(data, dict):
        raise ValueError("each rule must be a JSON object")
    action = data.get("action")
    if not isinstance(action, str) or not action:
        raise ValueError(f"rule needs a string 'action': {data!r}")
    match = data.get("match", {})
    if not isinstance(match, dict):
        raise ValueError(f"'match' must be a JSON object: {data!r}")
    unknown = set(match) - MATCH_KEYS
    if unknown:
        raise ValueError(f"unknown match keys {sorted(unknown)} in rule {data!r}")
    return Rule(
        action=action,
        domains=tuple(_domain(d) for d in _as_list(match.get("domain", []))),
        networks=tuple(ipaddress.ip_network(c, strict=False) for c in _as_list(match.get("cidr", []))),
        ports=frozenset(_port(p) for p in _as_list(match.get("port", []))),
    )


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (str, int)):
        return [value]
    if isinstance(value, list):
        return value
    raise ValueError(f"expected a string or a list, got {value!r}")


def _domain(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"domain must be a string, got {value!r}")
    domain = value.lower().strip(".")
    if domain.startswith("*."):
        domain = domain[2:]
    if not domain:
        raise ValueError("empty domain in rule")
    return domain


def _port(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
        raise ValueError(f"port must be an integer 1-65535, got {value!r}")
    return value
