"""Device backends. Nothing is installed on the phone: Android is driven through
ADB over Wi-Fi/Tailscale, iOS through pymobiledevice3's developer services.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.request

from . import android_parse as ap

PACKAGE_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,200}$")
KEYCODES = {
    "home": "KEYCODE_HOME", "back": "KEYCODE_BACK", "recents": "KEYCODE_APP_SWITCH",
    "power": "KEYCODE_POWER", "volume_up": "KEYCODE_VOLUME_UP", "volume_down": "KEYCODE_VOLUME_DOWN",
    "enter": "KEYCODE_ENTER", "delete": "KEYCODE_DEL",
}


class DeviceError(Exception):
    pass


class Unsupported(DeviceError):
    pass


def check_package(name):
    if not name or not PACKAGE_RE.match(name):
        raise DeviceError(f"invalid package/process name: {name!r}")
    return name


class Device:
    platform = "unknown"

    def __init__(self, device_id, name=""):
        self.id = device_id
        self.name = name or device_id

    def summary(self):
        return {"id": self.id, "name": self.name, "platform": self.platform}

    def info(self):
        return self.summary()

    def screenshot(self):
        raise Unsupported("screenshot not supported")

    def new_sampler(self, package):
        """Return a zero-arg callable producing one metrics dict per call."""
        raise Unsupported("metrics not supported")

    def log_process(self, package):
        """Return a Popen whose stdout yields log lines."""
        raise Unsupported("logs not supported")

    def packages(self):
        return []

    def input(self, action):
        raise Unsupported("remote input is not available for this device")

    def app(self, action, package):
        raise Unsupported("app control is not available for this device")


# --------------------------------------------------------------------- Android

def adb_path():
    return os.environ.get("NETSCOPE_ADB") or shutil.which("adb")


def run_adb(args, timeout=15, binary=False):
    adb = adb_path()
    if not adb:
        raise DeviceError("adb not found; install Android platform-tools or set NETSCOPE_ADB")
    try:
        proc = subprocess.run([adb, *args], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise DeviceError(f"adb {' '.join(args[:3])} timed out")
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).decode(errors="replace").strip()
        raise DeviceError(msg or f"adb exited with {proc.returncode}")
    return proc.stdout if binary else proc.stdout.decode(errors="replace")


def adb_connect(address, pair_address=None, pair_code=None):
    """Optionally pair (Android 11+ Wireless debugging), then connect."""
    out = []
    if pair_address and pair_code:
        out.append(run_adb(["pair", pair_address, str(pair_code)], timeout=30).strip())
    if address:
        if ":" not in address:
            address += ":5555"
        result = run_adb(["connect", address], timeout=20).strip()
        out.append(result)
        if "connected" not in result or "cannot" in result or "failed" in result:
            raise DeviceError(result)
    return "\n".join(out)


def adb_disconnect(address):
    return run_adb(["disconnect", address]).strip()


class AndroidDevice(Device):
    platform = "android"

    def __init__(self, serial, model="", state="device", network=False):
        super().__init__(serial, model or serial)
        self.state = state
        self.network = network

    def summary(self):
        s = super().summary()
        s.update(state=self.state, network=self.network)
        return s

    def shell(self, cmd, timeout=15):
        return run_adb(["-s", self.id, "shell", cmd], timeout=timeout)

    def info(self):
        script = (
            "echo @@NS:model; getprop ro.product.model; "
            "echo @@NS:manufacturer; getprop ro.product.manufacturer; "
            "echo @@NS:release; getprop ro.build.version.release; "
            "echo @@NS:sdk; getprop ro.build.version.sdk; "
            "echo @@NS:size; wm size; "
            "echo @@NS:density; wm density"
        )
        s = ap.split_sections(self.shell(script))
        size = re.findall(r"(\d+)x(\d+)", s.get("size", ""))
        info = self.summary()
        info.update(
            model=s.get("model", "").strip(),
            manufacturer=s.get("manufacturer", "").strip(),
            os=f"Android {s.get('release', '').strip()} (SDK {s.get('sdk', '').strip()})",
            screen=f"{size[-1][0]}x{size[-1][1]}" if size else None,
            capabilities=["screen", "metrics", "logs", "input", "app"],
        )
        return info

    def screenshot(self):
        return run_adb(["-s", self.id, "exec-out", "screencap", "-p"], timeout=20, binary=True)

    def packages(self):
        return ap.parse_packages(self.shell("pm list packages -3"))

    def new_sampler(self, package):
        if package:
            check_package(package)
            pkg = shlex.quote(package)
            app_part = (
                f"pid=$(pidof -s {pkg} 2>/dev/null); "
                "echo @@NS:pid; echo $pid; "
                'echo @@NS:pidstat; [ -n "$pid" ] && cat /proc/$pid/stat; '
                'echo @@NS:status; [ -n "$pid" ] && cat /proc/$pid/status; '
                f'echo @@NS:gfx; [ -n "$pid" ] && dumpsys gfxinfo {pkg}; '
            )
        else:
            app_part = ""
        script = (
            app_part
            + "echo @@NS:stat; cat /proc/stat; "
            "echo @@NS:net; cat /proc/net/dev; "
            "echo @@NS:battery; dumpsys battery; "
            "echo @@NS:meminfo; head -3 /proc/meminfo; true"
        )
        sampler = ap.AndroidSampler()

        def sample():
            return sampler.update(ap.split_sections(self.shell(script)), time.monotonic())

        return sample

    def log_process(self, package):
        args = [adb_path() or "adb", "-s", self.id, "logcat", "-v", "threadtime", "-T", "300"]
        if package:
            pid = self.shell(f"pidof -s {shlex.quote(check_package(package))} 2>/dev/null").strip()
            if not pid.isdigit():
                raise DeviceError(f"{package} is not running")
            args.append(f"--pid={pid}")
        return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def input(self, action):
        kind = action.get("type")
        if kind == "tap":
            cmd = f"input tap {int(action['x'])} {int(action['y'])}"
        elif kind == "swipe":
            cmd = "input swipe {} {} {} {} {}".format(
                int(action["x1"]), int(action["y1"]), int(action["x2"]), int(action["y2"]),
                int(action.get("ms", 250)))
        elif kind == "text":
            # `input text` treats %s as a space.
            cmd = "input text " + shlex.quote(str(action["text"]).replace(" ", "%s"))
        elif kind == "key":
            code = KEYCODES.get(action.get("key"))
            if not code:
                raise DeviceError(f"unknown key {action.get('key')!r}")
            cmd = f"input keyevent {code}"
        else:
            raise DeviceError(f"unknown input type {kind!r}")
        self.shell(cmd)

    def app(self, action, package):
        pkg = shlex.quote(check_package(package))
        if action == "launch":
            self.shell(f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
        elif action == "stop":
            self.shell(f"am force-stop {pkg}")
        elif action == "clear":
            self.shell(f"pm clear {pkg}")
        else:
            raise DeviceError(f"unknown app action {action!r}")


def android_devices():
    if not adb_path():
        return []
    try:
        text = run_adb(["devices", "-l"], timeout=10)
    except DeviceError:
        return []
    return [AndroidDevice(d["serial"], d["model"], d["state"], d["network"]) for d in ap.parse_adb_devices(text)]


# ------------------------------------------------------------------------- iOS
#
# iOS 17+ exposes developer services over a network tunnel created by
# `sudo pymobiledevice3 remote tunneld`. We drive the pymobiledevice3 CLI so
# NetScope itself stays dependency-free and survives its API churn.

TUNNELD_URL = os.environ.get("NETSCOPE_TUNNELD", "http://127.0.0.1:49151")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def pmd3_path():
    return os.environ.get("NETSCOPE_PMD3") or shutil.which("pymobiledevice3")


def run_pmd3(args, timeout=30):
    pmd3 = pmd3_path()
    if not pmd3:
        raise DeviceError("pymobiledevice3 not found; `pip install pymobiledevice3`")
    try:
        proc = subprocess.run([pmd3, *args], capture_output=True, timeout=timeout,
                              env={**os.environ, "NO_COLOR": "1"})
    except subprocess.TimeoutExpired:
        raise DeviceError(f"pymobiledevice3 {' '.join(args[:3])} timed out")
    if proc.returncode != 0:
        msg = _ANSI.sub("", (proc.stderr or proc.stdout).decode(errors="replace")).strip()
        raise DeviceError(msg.splitlines()[-1] if msg else f"pymobiledevice3 exited with {proc.returncode}")
    return _ANSI.sub("", proc.stdout.decode(errors="replace"))


def parse_json_output(text):
    start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
    if start < 0:
        raise DeviceError("unexpected pymobiledevice3 output")
    return json.loads(text[start:])


class IOSDevice(Device):
    platform = "ios"

    def _run(self, args, timeout=30):
        return run_pmd3([*args, "--tunnel", self.id], timeout=timeout)

    def info(self):
        info = self.summary()
        try:
            data = parse_json_output(self._run(["lockdown", "info"]))
            self.name = data.get("DeviceName") or self.name
            info.update(name=self.name, model=data.get("ProductType"),
                        os=f"iOS {data.get('ProductVersion', '')}")
        except (DeviceError, ValueError) as e:
            info["warning"] = str(e)
        info["capabilities"] = ["screen", "metrics", "logs", "app"]
        return info

    def screenshot(self):
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            self._run(["developer", "dvt", "screenshot", path])
            with open(path, "rb") as f:
                return f.read()
        finally:
            os.unlink(path)

    def new_sampler(self, package):
        if not package:
            raise DeviceError("on iOS, enter the app's process (executable) name to collect metrics")
        check_package(package)

        def sample():
            out = self._run(["developer", "dvt", "sysmon", "process", "single", "-f", f"name={package}",
                             "-k", "pid", "-k", "name", "-k", "cpuUsage", "-k", "physFootprint"])
            procs = parse_json_output(out)
            if not procs:
                return {"app_running": False}
            p = procs[0]
            return {
                "app_running": True,
                "pid": p.get("pid"),
                "app_cpu_pct": round(float(p.get("cpuUsage") or 0), 1),
                "app_rss_mb": round(float(p.get("physFootprint") or 0) / 1048576.0, 1),
            }

        return sample

    def log_process(self, package):
        args = [pmd3_path() or "pymobiledevice3", "syslog", "live", "--tunnel", self.id]
        if package:
            args += ["-pn", check_package(package)]
        return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env={**os.environ, "NO_COLOR": "1"})

    def app(self, action, package):
        check_package(package)
        if action == "launch":  # expects a bundle id, e.g. com.example.MyApp
            self._run(["developer", "dvt", "launch", package])
        elif action == "stop":  # process name or bundle id
            self._run(["developer", "dvt", "pkill", package])
        else:
            raise Unsupported(f"{action!r} is not available on iOS")


def ios_devices():
    try:
        with urllib.request.urlopen(TUNNELD_URL, timeout=1.5) as resp:
            tunnels = json.load(resp)
    except (OSError, ValueError):
        return []
    return [IOSDevice(udid, f"iPhone {udid[:8]}") for udid in tunnels]


# -------------------------------------------------------------------- registry

class Registry:
    """Discovers devices; caches instances so per-device state survives requests."""

    def __init__(self, discoverers=(android_devices, ios_devices)):
        self._discoverers = discoverers
        self._devices = {}

    def refresh(self):
        found = {}
        for discover in self._discoverers:
            for dev in discover():
                cached = self._devices.get(dev.id)
                if cached is not None and type(cached) is type(dev):
                    for attr in ("state", "network"):
                        if hasattr(dev, attr):
                            setattr(cached, attr, getattr(dev, attr))
                    dev = cached
                found[dev.id] = dev
        self._devices = found
        return list(found.values())

    def get(self, device_id):
        if device_id not in self._devices:
            self.refresh()
        dev = self._devices.get(device_id)
        if dev is None:
            raise DeviceError(f"device {device_id!r} not found")
        return dev
