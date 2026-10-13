import SwiftUI

/// Live state for one device screen: mirror loop, metrics stream and log stream.
@MainActor
@Observable
final class DeviceSession {
    let client: NetScopeClient
    let device: Device

    var info: DeviceInfo?
    var package = ""
    var packages: [String] = []
    var status: String?

    var screen: UIImage?
    var screenError: String?
    var mirrorFPS: Double?
    var liveScreen = true

    var latest: [String: Double] = [:]
    var history: [String: [Double?]] = [:]
    var appRunning: Bool?
    var metricsError: String?

    var logs: [LogLine] = []
    var logsPaused = false
    var logStatus: String?

    @ObservationIgnored private var screenTask: Task<Void, Never>?
    @ObservationIgnored private var metricsTask: Task<Void, Never>?
    @ObservationIgnored private var logsTask: Task<Void, Never>?
    @ObservationIgnored private var pausedLogs: [LogLine] = []
    @ObservationIgnored private var nextLogID = 0
    @ObservationIgnored private var started = false

    init(client: NetScopeClient, device: Device) {
        self.client = client
        self.device = device
    }

    var canInput: Bool {
        info?.capabilities?.contains("input") ?? !device.isIOS
    }

    var subtitle: String {
        status ?? info?.subtitle ?? device.id
    }

    // MARK: Lifecycle

    func start() {
        guard !started else { return }
        started = true
        Task { await loadInfo() }
        startScreen()
        watch()
    }

    func stop() {
        started = false
        screenTask?.cancel()
        metricsTask?.cancel()
        logsTask?.cancel()
    }

    private func loadInfo() async {
        do {
            info = try await client.info(device.id)
        } catch {
            status = error.localizedDescription
        }
        if !device.isIOS {
            packages = (try? await client.packages(device.id)) ?? []
        }
    }

    // MARK: Screen

    private func startScreen() {
        screenTask?.cancel()
        screenTask = Task {
            while !Task.isCancelled {
                guard liveScreen else {
                    try? await Task.sleep(for: .milliseconds(500))
                    continue
                }
                let began = Date()
                do {
                    let image = try await client.screenshot(device.id)
                    guard !Task.isCancelled else { return }
                    screen = image
                    screenError = nil
                    mirrorFPS = 1 / max(Date().timeIntervalSince(began), 0.001)
                } catch {
                    if Task.isCancelled { return }
                    screenError = error.localizedDescription
                    try? await Task.sleep(for: .seconds(2))
                }
                // Cap at ~10 fps so a fast network doesn't hammer the phone.
                let elapsedMS = Int(Date().timeIntervalSince(began) * 1000)
                if elapsedMS < 100 {
                    try? await Task.sleep(for: .milliseconds(100 - elapsedMS))
                }
            }
        }
    }

    // MARK: Metrics and logs

    /// (Re)starts both streams for the package currently entered.
    func watch() {
        metricsTask?.cancel()
        logsTask?.cancel()
        latest = [:]
        history = [:]
        appRunning = nil
        metricsError = nil
        logs = []
        pausedLogs = []
        logStatus = nil

        let pkg = package.trimmingCharacters(in: .whitespaces)
        let query = pkg.isEmpty ? [] : [URLQueryItem(name: "package", value: pkg)]
        status = pkg.isEmpty ? nil : "Watching \(pkg)"

        metricsTask = Task {
            while !Task.isCancelled {
                do {
                    for try await event in client.events(NetScopeClient.devicePath(device.id, "metrics"), query: query) {
                        handleMetrics(event)
                    }
                } catch {
                    if Task.isCancelled { return }
                    metricsError = error.localizedDescription
                }
                try? await Task.sleep(for: .seconds(3))  // reconnect
            }
        }

        logsTask = Task {
            do {
                for try await event in client.events(NetScopeClient.devicePath(device.id, "logs"), query: query) {
                    if event.name == "problem" {
                        logStatus = Self.errorText(event.data)
                    } else if let lines = (try? JSONSerialization.jsonObject(with: Data(event.data.utf8))) as? [String] {
                        appendLogs(lines)
                    }
                }
                if !Task.isCancelled { logStatus = logStatus ?? "Log stream ended" }
            } catch {
                if Task.isCancelled { return }
                logStatus = error.localizedDescription + (pkg.isEmpty ? "" : " (is \(pkg) running?)")
            }
        }
    }

    private func handleMetrics(_ event: SSEEvent) {
        guard let obj = (try? JSONSerialization.jsonObject(with: Data(event.data.utf8))) as? [String: Any] else {
            return
        }
        if event.name == "problem" {
            metricsError = obj["error"] as? String
            return
        }
        metricsError = nil
        appRunning = obj["app_running"] as? Bool
        var values: [String: Double] = [:]
        for kind in MetricKind.all {
            let value = (obj[kind.key] as? NSNumber)?.doubleValue
            values[kind.key] = value
            var series = history[kind.key, default: []]
            series.append(value)
            if series.count > Limits.metricHistory {
                series.removeFirst(series.count - Limits.metricHistory)
            }
            history[kind.key] = series
        }
        latest = values
    }

    private func appendLogs(_ lines: [String]) {
        let new = lines.map { text -> LogLine in
            nextLogID += 1
            return LogLine(id: nextLogID, text: text)
        }
        if logsPaused {
            pausedLogs.append(contentsOf: new)
            if pausedLogs.count > Limits.logLines {
                pausedLogs.removeFirst(pausedLogs.count - Limits.logLines)
            }
        } else {
            logs.append(contentsOf: new)
            trimLogs()
        }
    }

    func setLogsPaused(_ paused: Bool) {
        logsPaused = paused
        if !paused {
            logs.append(contentsOf: pausedLogs)
            pausedLogs = []
            trimLogs()
        }
    }

    func clearLogs() {
        logs = []
        pausedLogs = []
    }

    private func trimLogs() {
        if logs.count > Limits.logLines {
            logs.removeFirst(logs.count - Limits.logLines)
        }
    }

    private static func errorText(_ json: String) -> String {
        let obj = (try? JSONSerialization.jsonObject(with: Data(json.utf8))) as? [String: Any]
        return obj?["error"] as? String ?? json
    }

    // MARK: Control

    func send(_ action: [String: Any]) {
        Task {
            do {
                try await client.input(device.id, action)
            } catch {
                status = error.localizedDescription
            }
        }
    }

    func key(_ name: String) {
        send(["type": "key", "key": name])
    }

    func typeText(_ text: String) {
        guard !text.isEmpty else { return }
        send(["type": "text", "text": text])
    }

    func app(_ action: String) {
        let pkg = package.trimmingCharacters(in: .whitespaces)
        guard !pkg.isEmpty else {
            status = device.isIOS ? "Enter the app's bundle id or process name first" : "Enter an app package first"
            return
        }
        Task {
            do {
                try await client.app(device.id, action: action, package: pkg)
                status = action == "launch" ? "Launched \(pkg)" : "Stopped \(pkg)"
                try? await Task.sleep(for: .milliseconds(1200))
                watch()  // the app's pid changed
            } catch {
                status = error.localizedDescription
            }
        }
    }
}
