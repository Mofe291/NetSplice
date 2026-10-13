import Foundation
import UIKit

struct APIError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// Server address plus access token, parsed from what `netscope serve` prints,
/// e.g. `http://100.101.1.2:8765/?token=abc`.
struct ServerLink: Equatable {
    var baseURL: URL
    var token: String

    static func parse(_ text: String, token explicitToken: String = "") -> ServerLink? {
        var raw = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty else { return nil }
        if !raw.contains("://") {
            raw = "http://" + raw
        }
        guard let comps = URLComponents(string: raw),
              let scheme = comps.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = comps.host, !host.isEmpty else {
            return nil
        }
        var base = URLComponents()
        base.scheme = scheme
        base.host = host
        base.port = comps.port ?? (scheme == "http" ? 8765 : nil)
        base.path = "/"
        guard let url = base.url else { return nil }

        let queryToken = comps.queryItems?.first { $0.name == "token" }?.value ?? ""
        let given = explicitToken.trimmingCharacters(in: .whitespacesAndNewlines)
        return ServerLink(baseURL: url, token: given.isEmpty ? queryToken : given)
    }
}

struct SSEEvent {
    let name: String
    let data: String
}

/// NetScope sends each event as an optional `event:` line followed by one `data:` line,
/// so an event is complete as soon as its `data:` line arrives. (This also sidesteps
/// `AsyncLineSequence` dropping the blank separator lines.)
struct SSEParser {
    private var pendingName: String?

    mutating func feed(_ line: String) -> SSEEvent? {
        if line.hasPrefix("event:") {
            pendingName = Self.value(line, after: "event:")
        } else if line.hasPrefix("data:") {
            let event = SSEEvent(name: pendingName ?? "message", data: Self.value(line, after: "data:"))
            pendingName = nil
            return event
        }
        return nil
    }

    private static func value(_ line: String, after prefix: String) -> String {
        var rest = line.dropFirst(prefix.count)
        if rest.first == " " {
            rest = rest.dropFirst()
        }
        return String(rest)
    }
}

struct NetScopeClient {
    let link: ServerLink

    private static let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 60  // log streams send a heartbeat every 15 s
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()

    /// Percent-encodes one path segment (device ids contain `:` and `.`).
    static func segment(_ value: String) -> String {
        var allowed = CharacterSet.urlPathAllowed
        allowed.remove(charactersIn: "/?#%")
        return value.addingPercentEncoding(withAllowedCharacters: allowed) ?? value
    }

    static func devicePath(_ id: String, _ rest: String) -> String {
        "devices/\(segment(id))/\(rest)"
    }

    // MARK: Requests

    private func request(_ path: String, query: [URLQueryItem] = [], body: [String: Any]? = nil) throws -> URLRequest {
        guard var comps = URLComponents(url: link.baseURL, resolvingAgainstBaseURL: false) else {
            throw APIError(message: "Invalid server address")
        }
        comps.percentEncodedPath = "/api/" + path
        comps.queryItems = query.isEmpty ? nil : query
        guard let url = comps.url else { throw APIError(message: "Invalid request path") }

        var req = URLRequest(url: url)
        req.setValue(link.token, forHTTPHeaderField: "X-NetScope-Token")
        if let body {
            req.httpMethod = "POST"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        return req
    }

    private func send(_ req: URLRequest) async throws -> Data {
        let (data, response) = try await Self.session.data(for: req)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            throw APIError(message: Self.errorMessage(data, status: status))
        }
        return data
    }

    private static func errorMessage(_ data: Data, status: Int) -> String {
        if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let message = obj["error"] as? String {
            return message
        }
        if status == 401 {
            return "The access token is missing or wrong."
        }
        return "Server returned \(status) (\(HTTPURLResponse.localizedString(forStatusCode: status)))"
    }

    private func get<T: Decodable>(_ path: String, as type: T.Type) async throws -> T {
        let data = try await send(request(path))
        return try JSONDecoder().decode(T.self, from: data)
    }

    @discardableResult
    private func post(_ path: String, _ body: [String: Any]) async throws -> [String: Any] {
        let data = try await send(request(path, body: body))
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }

    // MARK: API

    func health() async throws -> Health {
        try await get("health", as: Health.self)
    }

    func devices() async throws -> [Device] {
        try await get("devices", as: [Device].self)
    }

    func info(_ deviceID: String) async throws -> DeviceInfo {
        try await get(Self.devicePath(deviceID, "info"), as: DeviceInfo.self)
    }

    func packages(_ deviceID: String) async throws -> [String] {
        try await get(Self.devicePath(deviceID, "packages"), as: [String].self)
    }

    func screenshot(_ deviceID: String) async throws -> UIImage {
        let data = try await send(request(Self.devicePath(deviceID, "screen.png")))
        guard let image = UIImage(data: data) else { throw APIError(message: "The screenshot could not be decoded") }
        return image
    }

    func input(_ deviceID: String, _ action: [String: Any]) async throws {
        try await post(Self.devicePath(deviceID, "input"), action)
    }

    func app(_ deviceID: String, action: String, package: String) async throws {
        try await post(Self.devicePath(deviceID, "app"), ["action": action, "package": package])
    }

    func connectAndroid(address: String, pairAddress: String, pairCode: String) async throws -> String {
        var body: [String: Any] = [:]
        if !address.isEmpty { body["address"] = address }
        if !pairAddress.isEmpty { body["pair_address"] = pairAddress }
        if !pairCode.isEmpty { body["pair_code"] = pairCode }
        return try await post("connect", body)["output"] as? String ?? ""
    }

    /// A Server-Sent Event stream; cancelling the consuming task closes the connection.
    func events(_ path: String, query: [URLQueryItem] = []) -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var req = try request(path, query: query)
                    req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    let (bytes, response) = try await Self.session.bytes(for: req)
                    let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                    if status != 200 {
                        var data = Data()
                        for try await byte in bytes {
                            data.append(byte)
                        }
                        throw APIError(message: Self.errorMessage(data, status: status))
                    }
                    var parser = SSEParser()
                    for try await line in bytes.lines {
                        if let event = parser.feed(line) {
                            continuation.yield(event)
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
