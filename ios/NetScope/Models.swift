import Foundation

/// A NetScope server the user has added. The access token lives in the Keychain.
struct Server: Codable, Hashable, Identifiable {
    let id: UUID
    var name: String
    var url: String
}

struct Device: Decodable, Hashable, Identifiable {
    let id: String
    let name: String
    let platform: String
    var state: String?
    var network: Bool?

    var isReady: Bool { state == nil || state == "device" }
    var isIOS: Bool { platform == "ios" }
}

struct DeviceInfo: Decodable {
    var name: String?
    var model: String?
    var manufacturer: String?
    var os: String?
    var screen: String?
    var warning: String?
    var capabilities: [String]?

    var subtitle: String {
        [manufacturer, model, os, screen].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
    }
}

struct Health: Decodable {
    let ok: Bool
    let version: String?
}

struct MetricKind: Identifiable {
    let key: String
    let label: String
    let unit: String
    var id: String { key }

    static let all: [MetricKind] = [
        MetricKind(key: "app_cpu_pct", label: "App CPU", unit: "%"),
        MetricKind(key: "app_rss_mb", label: "App memory", unit: "MB"),
        MetricKind(key: "fps", label: "Frames / s", unit: "fps"),
        MetricKind(key: "jank_pct", label: "Janky frames", unit: "%"),
        MetricKind(key: "frame_p90_ms", label: "Frame time p90", unit: "ms"),
        MetricKind(key: "sys_cpu_pct", label: "Device CPU", unit: "%"),
        MetricKind(key: "net_rx_kbps", label: "Network in", unit: "KB/s"),
        MetricKind(key: "net_tx_kbps", label: "Network out", unit: "KB/s"),
        MetricKind(key: "battery_level", label: "Battery", unit: "%"),
        MetricKind(key: "battery_temp_c", label: "Battery temp", unit: "°C"),
    ]
}

enum Limits {
    static let metricHistory = 90
    static let logLines = 3000
}

struct LogLine: Identifiable {
    let id: Int
    let text: String
    let level: Character?

    init(id: Int, text: String) {
        self.id = id
        self.text = text
        self.level = LogLine.level(of: text)
    }

    static let levels: [Character] = ["V", "D", "I", "W", "E", "F"]

    /// Android `threadtime` ("10-13 09:00:00.000  1234  1234 E Tag: msg") or iOS syslog ("<Error>").
    static func level(of line: String) -> Character? {
        let parts = line.split(separator: " ", maxSplits: 5, omittingEmptySubsequences: true)
        if parts.count >= 5, parts[4].count == 1, let c = parts[4].first, levels.contains(c),
           parts[2].allSatisfy(\.isNumber) {
            return c
        }
        let tags: [(String, Character)] = [
            ("<Fault>", "F"), ("<Error>", "E"), ("<Notice>", "I"),
            ("<Info>", "I"), ("<Default>", "I"), ("<Debug>", "D"),
        ]
        return tags.first { line.contains($0.0) }?.1
    }
}

enum LogLevelFilter: String, CaseIterable, Identifiable {
    case all, info, warn, error
    var id: Self { self }

    var title: String {
        switch self {
        case .all: "All"
        case .info: "Info+"
        case .warn: "Warn+"
        case .error: "Error+"
        }
    }

    func allows(_ level: Character?) -> Bool {
        guard let level, let rank = LogLine.levels.firstIndex(of: level) else { return true }
        switch self {
        case .all: return true
        case .info: return rank >= 2
        case .warn: return rank >= 3
        case .error: return rank >= 4
        }
    }
}
