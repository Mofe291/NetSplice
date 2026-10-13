"""Pure parsers for the text that Android shell commands print.

Kept free of I/O so they can be unit-tested with captured output.
"""

import re

SECTION_MARK = "@@NS:"


def split_sections(text):
    """Split output of a compound command into {name: body} by `@@NS:<name>` marker lines."""
    sections = {}
    name = None
    lines = []
    for line in text.splitlines():
        if line.startswith(SECTION_MARK):
            if name is not None:
                sections[name] = "\n".join(lines)
            name = line[len(SECTION_MARK):].strip()
            lines = []
        elif name is not None:
            lines.append(line)
    if name is not None:
        sections[name] = "\n".join(lines)
    return sections


def parse_proc_stat(text):
    """Return (total_jiffies, idle_jiffies, cpu_count) from /proc/stat."""
    total = idle = None
    cpus = 0
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "cpu":
            values = [int(v) for v in parts[1:]]
            total = sum(values[:8])  # user..steal; guest is already counted in user
            idle = values[3] + (values[4] if len(values) > 4 else 0)  # idle + iowait
        elif re.fullmatch(r"cpu\d+", parts[0]):
            cpus += 1
    return total, idle, max(cpus, 1)


def parse_pid_stat(text):
    """Return utime + stime (jiffies) from /proc/<pid>/stat, or None."""
    text = text.strip()
    if not text or ")" not in text:
        return None
    # comm (field 2) may contain spaces, so split after the last ')'.
    fields = text[text.rfind(")") + 2:].split()
    try:
        return int(fields[11]) + int(fields[12])
    except (IndexError, ValueError):
        return None


def parse_status_rss_kb(text):
    m = re.search(r"^VmRSS:\s+(\d+)\s*kB", text, re.M)
    return int(m.group(1)) if m else None


def parse_net_dev(text):
    """Return (rx_bytes, tx_bytes) summed over all non-loopback interfaces."""
    rx = tx = 0
    found = False
    for line in text.splitlines():
        if ":" not in line:
            continue
        iface, _, data = line.partition(":")
        iface = iface.strip()
        fields = data.split()
        if iface == "lo" or len(fields) < 9:
            continue
        try:
            rx += int(fields[0])
            tx += int(fields[8])
            found = True
        except ValueError:
            continue
    return (rx, tx) if found else (None, None)


def parse_battery(text):
    out = {}
    m = re.search(r"^\s*level:\s*(\d+)", text, re.M)
    if m:
        out["battery_level"] = int(m.group(1))
    m = re.search(r"^\s*temperature:\s*(-?\d+)", text, re.M)
    if m:
        out["battery_temp_c"] = int(m.group(1)) / 10.0
    m = re.search(r"^\s*status:\s*(\d+)", text, re.M)
    if m:
        out["charging"] = m.group(1) in ("2", "5")  # CHARGING, FULL
    return out


def parse_gfxinfo(text):
    """Extract cumulative frame stats from `dumpsys gfxinfo <pkg>`."""
    out = {}
    m = re.search(r"Total frames rendered:\s*(\d+)", text)
    if m:
        out["total_frames"] = int(m.group(1))
    m = re.search(r"Janky frames:\s*(\d+)", text)
    if m:
        out["janky_frames"] = int(m.group(1))
    for pct in ("50", "90", "95", "99"):
        m = re.search(rf"^\s*{pct}th percentile:\s*(\d+)ms", text, re.M)
        if m:
            out[f"frame_p{pct}_ms"] = int(m.group(1))
    return out


def parse_meminfo(text):
    out = {}
    for key, name in (("MemTotal", "mem_total_kb"), ("MemAvailable", "mem_available_kb")):
        m = re.search(rf"^{key}:\s+(\d+)", text, re.M)
        if m:
            out[name] = int(m.group(1))
    return out


_DEVICE_LINE = re.compile(r"^(\S+)\s+(device|offline|unauthorized|no permissions|connecting|authorizing)\b(.*)$")


def parse_adb_devices(text):
    """Parse `adb devices -l` into a list of dicts."""
    devices = []
    for line in text.splitlines():
        m = _DEVICE_LINE.match(line.strip())
        if not m:
            continue
        serial, state, rest = m.groups()
        props = dict(kv.split(":", 1) for kv in rest.split() if ":" in kv)
        devices.append({
            "serial": serial,
            "state": state,
            "model": props.get("model", "").replace("_", " "),
            "network": ":" in serial or "._adb-tls-connect." in serial,
        })
    return devices


def parse_packages(text):
    return sorted(line[len("package:"):].strip() for line in text.splitlines() if line.startswith("package:"))


class AndroidSampler:
    """Turns successive raw snapshots into rates (CPU %, FPS, network throughput)."""

    def __init__(self):
        self._prev = None

    def update(self, sections, now):
        total, idle, ncpu = parse_proc_stat(sections.get("stat", ""))
        pid_text = sections.get("pid", "").strip()
        cur = {
            "t": now,
            "pid": int(pid_text) if pid_text.isdigit() else None,
            "total": total,
            "idle": idle,
            "proc": parse_pid_stat(sections.get("pidstat", "")),
        }
        cur["rx"], cur["tx"] = parse_net_dev(sections.get("net", ""))
        gfx = parse_gfxinfo(sections.get("gfx", "")) if cur["pid"] else {}
        cur["frames"] = gfx.get("total_frames")
        cur["janky"] = gfx.get("janky_frames")

        metrics = {"pid": cur["pid"], "app_running": cur["pid"] is not None, "cpu_count": ncpu}
        metrics.update(parse_battery(sections.get("battery", "")))
        metrics.update(parse_meminfo(sections.get("meminfo", "")))
        rss = parse_status_rss_kb(sections.get("status", ""))
        if rss is not None:
            metrics["app_rss_mb"] = round(rss / 1024.0, 1)
        for key in ("frame_p50_ms", "frame_p90_ms", "frame_p99_ms"):
            if key in gfx:
                metrics[key] = gfx[key]

        prev = self._prev
        if prev is not None:
            dt = cur["t"] - prev["t"]
            dtotal = _delta(cur["total"], prev["total"])
            if dtotal:
                didle = _delta(cur["idle"], prev["idle"]) or 0
                metrics["sys_cpu_pct"] = round(100.0 * (dtotal - didle) / dtotal, 1)
                if cur["pid"] is not None and cur["pid"] == prev["pid"]:
                    dproc = _delta(cur["proc"], prev["proc"])
                    if dproc is not None:
                        # Percent of one core, like `top` (can exceed 100 on multi-core).
                        metrics["app_cpu_pct"] = round(100.0 * dproc * ncpu / dtotal, 1)
            if dt > 0:
                drx, dtx = _delta(cur["rx"], prev["rx"]), _delta(cur["tx"], prev["tx"])
                if drx is not None:
                    metrics["net_rx_kbps"] = round(drx / 1024.0 / dt, 1)
                if dtx is not None:
                    metrics["net_tx_kbps"] = round(dtx / 1024.0 / dt, 1)
                if cur["pid"] is not None and cur["pid"] == prev["pid"]:
                    dframes = _delta(cur["frames"], prev["frames"])
                    if dframes is not None:
                        metrics["fps"] = round(dframes / dt, 1)
                        djank = _delta(cur["janky"], prev["janky"])
                        if dframes and djank is not None:
                            metrics["jank_pct"] = round(100.0 * djank / dframes, 1)
        self._prev = cur
        return metrics


def _delta(a, b):
    """a - b, or None if either is missing or the counter went backwards (reset)."""
    if a is None or b is None or a < b:
        return None
    return a - b
