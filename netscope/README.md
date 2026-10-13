# NetScope

Watch and drive a phone over Wi-Fi or Tailscale while you test your app, with
**nothing installed on the phone**. Run one Python process on your computer and
open a browser dashboard that shows:

- **Live screen mirror**. Click to tap, drag to swipe, hold to long-press, type text, and use Back/Home/Recents.
- **App metrics** with sparklines: CPU, memory, frames per second, janky-frame %, p50/p90/p99 frame time.
- **Device metrics**: overall CPU, network in/out, battery level, battery temperature.
- **Live logs** for your app (logcat / iOS syslog), with level and text/regex filters.
- **App control**: launch, force-stop.

It uses the debugging channels the phone OS already has: ADB on Android, and
Apple's developer services (through `pymobiledevice3`) on iOS.

| | Android | iOS (experimental) |
|---|---|---|
| Transport | ADB over Wi-Fi (Wireless debugging) | pymobiledevice3 `tunneld` (iOS 17+) |
| Screen mirror | ✅ | ✅ (slower, ~1 fps) |
| Tap / swipe / type | ✅ | ❌ (Apple has no input API without an on-device agent) |
| CPU / memory | ✅ | ✅ |
| FPS / jank / frame times | ✅ | ❌ |
| Network / battery | ✅ | ❌ |
| Logs | ✅ | ✅ |
| Launch / stop | ✅ | ✅ |

## Isn't there already a tool for this?

Partly. Each piece exists, but in separate tools:

- **Android:** [scrcpy](https://github.com/Genymobile/scrcpy) mirrors and controls the screen over Wi-Fi ADB.
  Android Studio's profiler and Perfetto give deep profiling. Neither one is a single, lightweight dashboard you can open from another machine.
- **iOS:** Xcode with "Connect via network" plus Instruments works over Wi-Fi, but only on a Mac.
- **Commercial device clouds** (BrowserStack, Firebase Test Lab, AWS Device Farm) are for *their* devices, not the phone in your hand.

NetScope puts these into one browser dashboard that you can reach from any machine on your Tailscale network.

There is also a native iPhone/iPad app that does the same thing. See [`../ios`](../ios/).

## Install

```bash
cd netscope
pip install .            # Android only, no dependencies
pip install '.[ios]'     # adds pymobiledevice3 for iPhones
```

Android also needs `adb` from [Android platform-tools](https://developer.android.com/tools/releases/platform-tools)
on your PATH (or set `NETSCOPE_ADB=/path/to/adb`).

## Android setup (Android 11+)

1. On the phone, open **Settings → About phone** and tap **Build number** 7 times to enable Developer options.
2. Open **Developer options → Wireless debugging** and turn it on.
3. Tap **Pair device with pairing code**, then pair once from your computer:
   ```bash
   netscope connect <ip>:<connect-port> --pair <ip>:<pairing-port> --code 123456
   ```
   You can also use the "Connect Android" form in the dashboard.
4. Later, connect with just `netscope connect <ip>:<connect-port>`.

**Over Tailscale:** install Tailscale on the phone and on the computer, then use the
phone's `100.x.y.z` address instead of its LAN IP. Wireless debugging switches itself off when
the phone leaves Wi-Fi, so the phone must be on *some* Wi-Fi network. It does not have to be
the same network as your computer.

Older Android (≤10): connect a USB cable once and run `adb tcpip 5555`. After that,
`netscope connect <ip>:5555` works without the cable until the phone reboots.

## iOS setup (iOS 17+, experimental)

1. Turn on **Settings → Privacy & Security → Developer Mode** (it only shows up after
   the phone has been connected to Xcode or a developer tool once).
2. Pair once over USB so the phone trusts the computer: `pymobiledevice3 lockdown pair`.
3. Mount the developer image: `pymobiledevice3 mounter auto-mount`.
4. Keep the tunnel daemon running: `sudo pymobiledevice3 remote tunneld`.
   It finds the phone over Wi-Fi, and the phone then shows up in NetScope on its own.

In the app field, enter the **process name** (the executable name, e.g. `MyApp`) to collect
metrics and logs. Enter the **bundle id** to launch the app.

`tunneld` finds iPhones with Bonjour, which does not cross Tailscale. To work from somewhere else,
run NetScope on a computer on the same Wi-Fi as the phone and open the dashboard over Tailscale (see below).

## Run

```bash
netscope serve                       # http://127.0.0.1:8765 (this machine only)
netscope serve --host 0.0.0.0        # reachable from your LAN / tailnet, with an access token
netscope serve --host 100.x.y.z      # bind only to this machine's Tailscale IP
netscope devices                     # list what NetScope can see
```

When NetScope is bound to anything other than localhost, it creates an access token and
prints the full URL to open. Anyone who has that URL can see the phone's screen and control it.
Keep the URL private, or bind NetScope to your Tailscale IP only.

A typical remote setup:

```
 phone ──Wi-Fi/Tailscale──> computer running `netscope serve` <──Tailscale── your laptop's browser
```

## How it works

- **Android:** each metrics tick runs one batched `adb shell` command that reads `/proc/stat`,
  `/proc/<pid>/stat`, `/proc/<pid>/status`, `/proc/net/dev`, `dumpsys gfxinfo <pkg>` and `dumpsys battery`.
  NetScope turns the differences between ticks into rates.
  - App CPU is a percent of one core, the same as `top`, so it can go above 100.
  - App memory is RSS.
  - FPS counts the frames the app actually rendered. An app sitting idle correctly shows 0.
  - The screen comes from `screencap`, and input goes through `input tap|swipe|text|keyevent`.
- **iOS:** NetScope calls the `pymobiledevice3` CLI: `developer dvt screenshot`,
  `developer dvt sysmon process single`, `syslog live`, `developer dvt launch|pkill`.
- **Server:** Python standard library only. It serves a JSON API and Server-Sent Event
  streams (`/api/devices/<id>/metrics`, `/api/devices/<id>/logs`) and a single-page dashboard.

## Limits

- The screen mirror runs at about 2–10 fps over Wi-Fi, which is enough for watching a test but not
  smooth video. For smooth, low-latency mirroring of an Android phone, run `scrcpy --tcpip=<ip>:<port>`
  next to NetScope. It uses the same ADB connection.
- The log stream follows the app's PID at the time you click "Watch app". If the app restarts,
  click it again.

## Tests

```bash
python -m unittest discover -s tests
```

The end-to-end tests run the real server against a fake `adb` (`tests/fake_adb.py`).
