#!/usr/bin/env python3
"""Stand-in for `adb` used by the end-to-end tests. Records calls to $FAKE_ADB_LOG."""
import os
import sys
import time

args = sys.argv[1:]
with open(os.environ["FAKE_ADB_LOG"], "a") as log:
    log.write(" ".join(args) + "\n")

if args[:1] == ["devices"]:
    print("List of devices attached\n100.64.0.7:5555  device product:p model:Pixel_8 transport_id:1\n")
elif args[:1] == ["connect"]:
    print(f"connected to {args[1]}")
elif args[2:4] == ["exec-out", "screencap"]:
    sys.stdout.buffer.write(b"\x89PNG\r\n\x1a\nfake")
elif args[2:3] == ["logcat"]:
    for i in range(3):
        print(f"10-13 09:00:0{i}.000  1234  1234 E MyTag: boom {i}", flush=True)
    time.sleep(30)
elif args[2:3] == ["shell"]:
    cmd = args[3]
    if cmd.startswith("pidof"):
        print("1234")
    elif "getprop ro.product.model" in cmd:
        print("@@NS:model\nPixel 8\n@@NS:manufacturer\nGoogle\n@@NS:release\n15\n@@NS:sdk\n35\n"
              "@@NS:size\nPhysical size: 1080x2400\n@@NS:density\nPhysical density: 420")
    elif "/proc/stat" in cmd:
        n = int(time.time() * 100)
        print(f"@@NS:pid\n1234\n@@NS:pidstat\n1234 (app) S 1 2 3 4 5 6 7 8 9 10 {n // 10} 0 0\n"
              f"@@NS:status\nVmRSS:\t102400 kB\n@@NS:gfx\nTotal frames rendered: {n // 2}\nJanky frames: 0\n"
              f"@@NS:stat\ncpu  {n} 0 0 {n} 0 0 0 0\ncpu0 1 1 1 1\n@@NS:net\n wlan0: {n} 0 0 0 0 0 0 0 {n} 0\n"
              "@@NS:battery\n  level: 50\n  temperature: 300\n@@NS:meminfo\nMemTotal: 1 kB")
    elif cmd.startswith("pm list packages"):
        print("package:com.example.app")
