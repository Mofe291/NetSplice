import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from netscope.devices import Registry, android_devices
from netscope.server import serve

HERE = os.path.dirname(os.path.abspath(__file__))
SERIAL = "100.64.0.7:5555"
DEV = "/api/devices/" + urllib.request.quote(SERIAL, safe="")


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.log = tempfile.NamedTemporaryFile(delete=False)
        os.environ["NETSCOPE_ADB"] = os.path.join(HERE, "fake_adb.py")
        os.environ["FAKE_ADB_LOG"] = cls.log.name
        cls.server, cls.token = serve("127.0.0.1", 0, token="s3cret", interval=0.3,
                                      registry=Registry(discoverers=(android_devices,)))
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        os.unlink(cls.log.name)

    def req(self, path, body=None, token=True):
        sep = "&" if "?" in path else "?"
        url = self.base + path + (f"{sep}token={self.token}" if token else "")
        data = json.dumps(body).encode() if body is not None else None
        return urllib.request.urlopen(urllib.request.Request(url, data=data,
                                      headers={"Content-Type": "application/json"}), timeout=10)

    def get_json(self, path, body=None):
        with self.req(path, body) as r:
            return json.load(r)

    def sse_events(self, path, count):
        events = []
        with self.req(path) as r:
            for raw in r:
                line = raw.decode().strip()
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
                    if len(events) == count:
                        return events
        return events

    def adb_calls(self):
        with open(self.log.name) as f:
            return f.read().splitlines()

    def test_dashboard_served_without_token(self):
        with urllib.request.urlopen(self.base + "/", timeout=5) as r:
            self.assertIn(b"NetScope", r.read())

    def test_api_requires_token(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.req("/api/devices", token=False)
        self.assertEqual(cm.exception.code, 401)

    def test_devices_and_info(self):
        devices = self.get_json("/api/devices")
        self.assertEqual(devices[0]["id"], SERIAL)
        self.assertTrue(devices[0]["network"])
        info = self.get_json(DEV + "/info")
        self.assertEqual(info["screen"], "1080x2400")
        self.assertIn("Android 15", info["os"])

    def test_screenshot(self):
        with self.req(DEV + "/screen.png") as r:
            self.assertEqual(r.headers["Content-Type"], "image/png")
            self.assertTrue(r.read().startswith(b"\x89PNG"))

    def test_metrics_stream(self):
        events = self.sse_events(DEV + "/metrics?package=com.example.app", 3)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["app_rss_mb"], 100.0)
        self.assertIn("fps", events[-1])
        self.assertIn("app_cpu_pct", events[-1])

    def test_logs_stream(self):
        batches = self.sse_events(DEV + "/logs?package=com.example.app", 1)
        self.assertIn("MyTag: boom 0", batches[0][0])
        self.assertTrue(any("--pid=1234" in c for c in self.adb_calls()))

    def test_input_and_app_control(self):
        self.get_json(DEV + "/input", {"type": "tap", "x": 10, "y": 20})
        self.get_json(DEV + "/input", {"type": "text", "text": "hi there; rm -rf /"})
        self.get_json(DEV + "/app", {"action": "launch", "package": "com.example.app"})
        calls = self.adb_calls()
        self.assertIn(f"-s {SERIAL} shell input tap 10 20", calls)
        self.assertIn(f"-s {SERIAL} shell input text 'hi%sthere;%srm%s-rf%s/'", calls)
        self.assertTrue(any("monkey -p com.example.app" in c for c in calls))

    def test_rejects_shell_injection_in_package(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get_json(DEV + "/app", {"action": "stop", "package": "x; reboot"})
        self.assertEqual(cm.exception.code, 400)

    def test_connect(self):
        out = self.get_json("/api/connect", {"address": "100.64.0.9"})
        self.assertIn("connected to 100.64.0.9:5555", out["output"])


if __name__ == "__main__":
    unittest.main()
