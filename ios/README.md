# NetScope for iOS

A native SwiftUI app for iPhone and iPad that connects to a [NetScope server](../netscope/) over
Wi-Fi or Tailscale. Use it to watch and drive the phone you're testing from your own phone or iPad.

```
 test phone ──ADB / tunneld──> computer running `netscope serve` <──Wi-Fi / Tailscale── NetScope iOS app
```

The phone you're testing still needs nothing installed. The app is only the viewer.

**Why it needs the computer:** iOS doesn't let an app run `adb` or open Apple's developer
tunnels. So the server does the device work and the app shows the results.

## Features

- **Servers**: paste the URL `netscope serve` prints. Tokens are kept in the Keychain.
- **Devices**: lists connected devices and refreshes every 10 s. Pull to refresh.
  **+** connects or pairs an Android phone over Wireless debugging.
- **Screen**: live mirror. Tap, drag to swipe, hold to long-press. Back/Home/Recents buttons and a text field to type on the phone.
- **Metrics**: app CPU, memory, FPS, janky frames, frame time, device CPU, network, battery, each with a sparkline (Swift Charts).
- **Logs**: live app logs, colored by level, with a text filter, minimum level, pause and clear.
- **App control**: pick an installed app, watch it, launch it, force-stop it.
- **iPad**: screen, metrics and logs side by side. **iPhone**: Screen / Metrics / Logs tabs.

## Build

Requires **Xcode 16+** and an iOS 17+ device or simulator.

1. Open `ios/NetScope.xcodeproj`.
2. Select the **NetScope** target → *Signing & Capabilities*. Choose your Team and change the
   bundle id (`com.example.netscope`) to one you own.
3. Run it on your iPhone or iPad.

If the project won't open, generate a fresh one with [XcodeGen](https://github.com/yonaskolb/XcodeGen):

```bash
cd ios && brew install xcodegen && xcodegen
```

## Use

1. On your computer, start the server so other devices can reach it:
   ```bash
   netscope serve --host 0.0.0.0          # or --host <your Tailscale IP>
   ```
2. In the app, tap **+**. Paste the printed URL, for example `http://100.101.1.2:8765/?token=…`.
   - Universal Clipboard lets you copy on your Mac and paste on your iPhone.
   - Over Tailscale, use the computer's `100.x` address or its MagicDNS name, and keep Tailscale
     on in the iPhone too.
3. Open the server, pick a device, enter your app's package name (Android) or process name
   (iOS), and tap **Watch**.

The first connection over the LAN shows iOS's *Local Network* permission prompt. Allow it.

## Notes

- The app connects over plain HTTP, so `Config/Info.plist` allows non-HTTPS connections.
  Tailscale already encrypts the connection. On a shared LAN the token is sent in the clear,
  so prefer Tailscale.
- No app icon is included. Add an `Assets.xcassets` with an `AppIcon` set under `NetScope/` if you want one.
- Every `.swift` file in `NetScope/` is compiled automatically (Xcode 16 synchronized folder).
  New files need no project edits.
