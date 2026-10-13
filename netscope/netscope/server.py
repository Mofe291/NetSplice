"""HTTP server: JSON API, Server-Sent Event streams, and the dashboard page."""

import hmac
import json
import queue
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .devices import DeviceError, Registry, Unsupported, adb_connect, adb_disconnect

HEARTBEAT_SECONDS = 15


class NetScopeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, registry=None, token=None, interval=1.0):
        super().__init__(address, Handler)
        self.registry = registry or Registry()
        self.token = token
        self.interval = interval
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = "NetScope/0.1"

    def log_message(self, fmt, *args):  # keep the console quiet; streams are long-lived
        pass

    # ------------------------------------------------------------ plumbing

    def _authorized(self, query):
        token = self.server.token
        if not token:
            return True
        given = self.headers.get("X-NetScope-Token") or query.get("token", [""])[0]
        return hmac.compare_digest(given.encode(), token.encode())

    def _send(self, status, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message):
        self._send(status, {"error": message})

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except ValueError:
            raise DeviceError("request body must be JSON")

    def _route(self, method):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]

        if method == "GET" and parts in ([], ["index.html"]):
            page = resources.files("netscope").joinpath("static/index.html").read_bytes()
            return self._send(200, page, "text/html; charset=utf-8")
        if not parts or parts[0] != "api":
            return self._error(404, "not found")
        if not self._authorized(query):
            return self._error(401, "missing or invalid token")

        try:
            self._api(method, parts[1:], query)
        except Unsupported as e:
            self._error(501, str(e))
        except (DeviceError, KeyError, TypeError, ValueError) as e:
            self._error(400, str(e))
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    # ----------------------------------------------------------------- API

    def _api(self, method, parts, query):
        reg = self.server.registry
        q = lambda name, default="": query.get(name, [default])[0]

        if parts == ["health"] and method == "GET":
            return self._send(200, {"ok": True, "version": __version__})
        if parts == ["devices"] and method == "GET":
            with self.server.lock:
                devices = reg.refresh()
            return self._send(200, [d.summary() for d in devices])
        if parts == ["connect"] and method == "POST":
            body = self._json_body()
            out = adb_connect(body.get("address"), body.get("pair_address"), body.get("pair_code"))
            return self._send(200, {"ok": True, "output": out})
        if parts == ["disconnect"] and method == "POST":
            return self._send(200, {"ok": True, "output": adb_disconnect(self._json_body()["address"])})

        if len(parts) < 3 or parts[0] != "devices":
            return self._error(404, "not found")
        with self.server.lock:
            dev = reg.get(parts[1])
        action = parts[2]

        if method == "GET" and action == "info":
            return self._send(200, dev.info())
        if method == "GET" and action == "screen.png":
            return self._send(200, dev.screenshot(), "image/png")
        if method == "GET" and action == "packages":
            return self._send(200, dev.packages())
        if method == "GET" and action == "metrics":
            return self._stream_metrics(dev, q("package"), float(q("interval", self.server.interval)))
        if method == "GET" and action == "logs":
            return self._stream_logs(dev, q("package"))
        if method == "POST" and action == "input":
            dev.input(self._json_body())
            return self._send(200, {"ok": True})
        if method == "POST" and action == "app":
            body = self._json_body()
            dev.app(body.get("action"), body.get("package"))
            return self._send(200, {"ok": True})
        return self._error(404, "not found")

    # ------------------------------------------------------------- streams

    def _start_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _event(self, data, event=None):
        msg = (f"event: {event}\n" if event else "") + f"data: {json.dumps(data)}\n\n"
        self.wfile.write(msg.encode())
        self.wfile.flush()

    def _stream_metrics(self, dev, package, interval):
        sample = dev.new_sampler(package)
        interval = max(0.25, interval)
        self._start_sse()
        while True:
            started = time.monotonic()
            try:
                data = sample()
                data["ts"] = time.time()
                self._event(data)
            except DeviceError as e:
                self._event({"error": str(e)}, "problem")
            time.sleep(max(0.0, interval - (time.monotonic() - started)))

    def _stream_logs(self, dev, package):
        proc = dev.log_process(package)
        lines = queue.Queue(maxsize=5000)

        def pump():
            for raw in proc.stdout:
                lines.put(raw.decode(errors="replace").rstrip("\n"))
            lines.put(None)

        threading.Thread(target=pump, daemon=True).start()
        self._start_sse()
        try:
            while True:
                try:
                    line = lines.get(timeout=HEARTBEAT_SECONDS)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")  # detects closed clients
                    self.wfile.flush()
                    continue
                if line is None:
                    self._event({"error": "log stream ended"}, "problem")
                    return
                batch = [line]
                while len(batch) < 200:
                    try:
                        nxt = lines.get_nowait()
                    except queue.Empty:
                        break
                    if nxt is None:
                        lines.put(None)
                        break
                    batch.append(nxt)
                self._event(batch)
        finally:
            proc.kill()


def serve(host="127.0.0.1", port=8765, token=None, interval=1.0, registry=None):
    if token is None and host not in ("127.0.0.1", "localhost", "::1"):
        token = secrets.token_urlsafe(16)
    server = NetScopeServer((host, port), registry=registry, token=token, interval=interval)
    return server, token
