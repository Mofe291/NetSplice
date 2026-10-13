# NetSplice

Selective network direction control. NetSplice is a local SOCKS5 proxy that decides, per
connection, whether traffic goes **direct**, is **blocked**, or is sent through a named
**upstream proxy** (SOCKS5 or HTTP CONNECT). Decisions come from an ordered list of rules
matching the destination's domain, IP range, or port.

Standard library only, Python 3.10+.

## Quick start

```sh
python -m netsplice route netsplice.example.json intranet.example 443   # which route would this get?
python -m netsplice run netsplice.example.json                          # start the listener
curl --socks5-hostname 127.0.0.1:1080 https://example.com/              # use it
```

Point any SOCKS5-aware client (browser, `curl --socks5-hostname`, `ssh -D`, etc.) at the listen
address. Use `--socks5-hostname` (or the equivalent) so names are sent to NetSplice rather than
resolved locally. Otherwise domain rules never see a hostname.

## Config

```json
{
  "listen": "127.0.0.1:1080",
  "default": "direct",
  "upstreams": {
    "corp": {"type": "http", "host": "proxy.corp.example", "port": 3128},
    "tor":  {"type": "socks5", "host": "127.0.0.1", "port": 9050}
  },
  "rules": [
    {"action": "block",  "match": {"port": [25, 465]}},
    {"action": "direct", "match": {"cidr": ["10.0.0.0/8", "192.168.0.0/16"]}},
    {"action": "corp",   "match": {"domain": ["example.com"]}},
    {"action": "tor",    "match": {"domain": ["onion.example"]}}
  ]
}
```

- **Actions:** `direct`, `block`, or the name of an upstream. `default` applies when no rule matches.
- **Rules are tried in order; the first match wins.**
- **Match keys:** `domain`, `cidr`, `port`. Within a rule all given keys must match (AND); within a
  key, any listed value matches (OR). A rule with no `match` is a catch-all.
- **`domain`** matches the name and all its subdomains (`example.com` matches `www.example.com`).
  Case and trailing dots are ignored.
- **`cidr`** only matches destinations given as IP addresses. Domain names are not resolved to check
  CIDR rules, so a domain that resolves into a range is not matched by a `cidr` rule.
- **Upstreams** of type `socks5` receive hostnames unresolved. `http` upstreams must accept
  `CONNECT`; authentication is not supported yet.

## Safety

`run` refuses to listen on a non-loopback address unless `--allow-remote` is given, because an
open SOCKS5 proxy lets anyone who can reach it make connections as you. The listener supports
no-auth only.

## Limitations

- TCP only (SOCKS5 `CONNECT`). UDP, `BIND`, and SOCKS5 auth are not implemented.
- Plain HTTP clients that use absolute-URI proxying (not SOCKS) are not routed.
- When one side of a connection closes, the other direction is torn down immediately, so
  half-closed streams are not preserved.

## Tests

```sh
python -m unittest
```

Covers rule matching and config validation, plus end-to-end SOCKS5 tests through direct,
SOCKS5-chained, and HTTP CONNECT routes.
