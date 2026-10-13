import unittest

from netscope import android_parse as ap

STAT_1 = """cpu  100 0 100 800 0 0 0 0 0 0
cpu0 50 0 50 400 0 0 0 0 0 0
cpu1 50 0 50 400 0 0 0 0 0 0
intr 123"""
STAT_2 = """cpu  200 0 200 1000 0 0 0 0 0 0
cpu0 100 0 100 500 0 0 0 0 0 0
cpu1 100 0 100 500 0 0 0 0 0 0"""
PIDSTAT_1 = "1234 (com.example.my app) S 1 2 3 4 5 6 7 8 9 10 20 30 0 0"
PIDSTAT_2 = "1234 (com.example.my app) S 1 2 3 4 5 6 7 8 9 10 60 40 0 0"
NET_1 = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:  9999 1 0 0 0 0 0 0  9999 1 0 0 0 0 0 0
 wlan0:  10240 10 0 0 0 0 0 0  2048 5 0 0 0 0 0 0"""
NET_2 = NET_1.replace("10240", "30720").replace("2048 5", "4096 5")
GFX_1 = """Stats since: 123ns
Total frames rendered: 100
Janky frames: 10 (10.00%)
50th percentile: 8ms
90th percentile: 14ms
95th percentile: 20ms
99th percentile: 40ms"""
GFX_2 = GFX_1.replace("rendered: 100", "rendered: 160").replace("Janky frames: 10", "Janky frames: 13")
BATTERY = """Current Battery Service state:
  AC powered: false
  status: 2
  level: 87
  temperature: 312
"""


def snapshot(stat, pidstat, net, gfx):
    return (f"@@NS:pid\n1234\n@@NS:pidstat\n{pidstat}\n@@NS:status\nName:\tx\nVmRSS:\t  204800 kB\n"
            f"@@NS:gfx\n{gfx}\n@@NS:stat\n{stat}\n@@NS:net\n{net}\n@@NS:battery\n{BATTERY}\n"
            "@@NS:meminfo\nMemTotal:  8000000 kB\nMemFree: 1 kB\nMemAvailable:  3000000 kB\n")


class ParserTests(unittest.TestCase):
    def test_split_sections(self):
        self.assertEqual(ap.split_sections("junk\n@@NS:a\n1\n2\n@@NS:b\n"), {"a": "1\n2", "b": ""})

    def test_proc_stat(self):
        self.assertEqual(ap.parse_proc_stat(STAT_1), (1000, 800, 2))

    def test_pid_stat_with_spaces_in_comm(self):
        self.assertEqual(ap.parse_pid_stat(PIDSTAT_1), 50)
        self.assertIsNone(ap.parse_pid_stat(""))

    def test_net_dev_skips_loopback(self):
        self.assertEqual(ap.parse_net_dev(NET_1), (10240, 2048))

    def test_battery(self):
        self.assertEqual(ap.parse_battery(BATTERY), {"battery_level": 87, "battery_temp_c": 31.2, "charging": True})

    def test_gfxinfo(self):
        g = ap.parse_gfxinfo(GFX_1)
        self.assertEqual((g["total_frames"], g["janky_frames"], g["frame_p90_ms"]), (100, 10, 14))

    def test_adb_devices(self):
        text = ("List of devices attached\n"
                "192.168.1.20:5555       device product:panther model:Pixel_7 device:panther transport_id:1\n"
                "adb-2A1B-x._adb-tls-connect._tcp device model:SM_S918B transport_id:2\n"
                "emulator-5554   offline\n\n")
        devs = ap.parse_adb_devices(text)
        self.assertEqual([d["serial"] for d in devs],
                         ["192.168.1.20:5555", "adb-2A1B-x._adb-tls-connect._tcp", "emulator-5554"])
        self.assertEqual(devs[0]["model"], "Pixel 7")
        self.assertEqual([d["network"] for d in devs], [True, True, False])
        self.assertEqual(devs[2]["state"], "offline")

    def test_packages(self):
        self.assertEqual(ap.parse_packages("package:b.app\npackage:a.app\n"), ["a.app", "b.app"])


class SamplerTests(unittest.TestCase):
    def test_rates_from_two_snapshots(self):
        s = ap.AndroidSampler()
        first = s.update(ap.split_sections(snapshot(STAT_1, PIDSTAT_1, NET_1, GFX_1)), now=10.0)
        self.assertNotIn("app_cpu_pct", first)  # needs two samples
        self.assertEqual(first["app_rss_mb"], 200.0)
        self.assertEqual(first["battery_level"], 87)

        m = s.update(ap.split_sections(snapshot(STAT_2, PIDSTAT_2, NET_2, GFX_2)), now=12.0)
        # total delta 400 jiffies (200 idle), process delta 50 jiffies, 2 cores -> 25% of one core
        self.assertEqual(m["app_cpu_pct"], 25.0)
        self.assertEqual(m["sys_cpu_pct"], 50.0)
        self.assertEqual(m["fps"], 30.0)
        self.assertEqual(m["jank_pct"], 5.0)
        self.assertEqual(m["net_rx_kbps"], 10.0)
        self.assertEqual(m["net_tx_kbps"], 1.0)

    def test_restarted_app_does_not_produce_bogus_rates(self):
        s = ap.AndroidSampler()
        s.update(ap.split_sections(snapshot(STAT_1, PIDSTAT_2, NET_1, GFX_2)), now=1.0)
        restarted = snapshot(STAT_2, PIDSTAT_1, NET_2, GFX_1).replace("@@NS:pid\n1234", "@@NS:pid\n999")
        m = s.update(ap.split_sections(restarted), now=2.0)
        self.assertNotIn("fps", m)
        self.assertNotIn("app_cpu_pct", m)
        self.assertIn("sys_cpu_pct", m)

    def test_app_not_running(self):
        m = ap.AndroidSampler().update(ap.split_sections("@@NS:pid\n\n@@NS:stat\n" + STAT_1), now=1.0)
        self.assertFalse(m["app_running"])


if __name__ == "__main__":
    unittest.main()
