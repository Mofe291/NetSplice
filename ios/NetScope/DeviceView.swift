import Charts
import SwiftUI

struct DeviceView: View {
    @State private var session: DeviceSession
    @State private var tab: Tab = .screen
    @Environment(\.horizontalSizeClass) private var sizeClass

    enum Tab: String, CaseIterable, Identifiable {
        case screen = "Screen", metrics = "Metrics", logs = "Logs"
        var id: Self { self }
    }

    init(client: NetScopeClient, device: Device) {
        _session = State(initialValue: DeviceSession(client: client, device: device))
    }

    var body: some View {
        VStack(spacing: 0) {
            AppBar(session: session)
                .padding(.horizontal)
                .padding(.vertical, 8)
            if sizeClass == .regular {
                // iPad / large iPhone landscape: everything at once.
                HStack(alignment: .top, spacing: 16) {
                    ScreenPanel(session: session)
                        .frame(maxWidth: 420)
                    VStack(spacing: 12) {
                        ScrollView { MetricsPanel(session: session) }
                            .frame(maxHeight: 360)
                        LogsPanel(session: session)
                            .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
                    }
                }
                .padding([.horizontal, .bottom])
            } else {
                Picker("View", selection: $tab) {
                    ForEach(Tab.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .padding(.horizontal)
                .padding(.bottom, 8)
                switch tab {
                case .screen:
                    ScreenPanel(session: session).padding([.horizontal, .bottom])
                case .metrics:
                    ScrollView { MetricsPanel(session: session).padding(.horizontal) }
                case .logs:
                    LogsPanel(session: session)
                }
            }
        }
        .navigationTitle(session.info?.name ?? session.device.name)
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { session.start() }
        .onDisappear { session.stop() }
    }
}

// MARK: - App bar

private struct AppBar: View {
    @Bindable var session: DeviceSession

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                TextField(session.device.isIOS ? "Process name or bundle id" : "App package, e.g. com.example.app",
                          text: $session.package)
                    .textFieldStyle(.roundedBorder)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .submitLabel(.go)
                    .onSubmit { session.watch() }
                if !session.packages.isEmpty {
                    Menu {
                        ForEach(session.packages, id: \.self) { pkg in
                            Button(pkg) {
                                session.package = pkg
                                session.watch()
                            }
                        }
                    } label: {
                        Image(systemName: "list.bullet")
                    }
                    .accessibilityLabel("Installed apps")
                }
                Button("Watch") { session.watch() }
                    .buttonStyle(.borderedProminent)
                Menu {
                    Button { session.app("launch") } label: { Label("Launch", systemImage: "play") }
                    Button(role: .destructive) { session.app("stop") } label: { Label("Force stop", systemImage: "stop") }
                } label: {
                    Image(systemName: "ellipsis.circle").font(.title3)
                }
                .accessibilityLabel("App actions")
            }
            Text(session.subtitle)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
    }
}

// MARK: - Screen

private struct ScreenPanel: View {
    @Bindable var session: DeviceSession
    @State private var text = ""
    @State private var touchStart: Date?

    var body: some View {
        VStack(spacing: 10) {
            GeometryReader { geo in
                let pixels = session.screen.map { CGSize(width: $0.size.width * $0.scale, height: $0.size.height * $0.scale) }
                let rect = pixels.map { fittedRect($0, in: geo.size) } ?? .zero
                ZStack {
                    if let image = session.screen {
                        Image(uiImage: image)
                            .resizable()
                            .interpolation(.medium)
                            .aspectRatio(contentMode: .fit)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                            .shadow(radius: 4)
                    } else if let error = session.screenError {
                        ContentUnavailableView("No screen", systemImage: "rectangle.slash", description: Text(error))
                    } else {
                        ProgressView()
                    }
                }
                .frame(width: geo.size.width, height: geo.size.height)
                .contentShape(Rectangle())
                .gesture(touchGesture(rect: rect, pixels: pixels))
                .allowsHitTesting(session.canInput && session.screen != nil)
            }

            if session.canInput {
                HStack(spacing: 24) {
                    navButton("chevron.backward", "back", "Back")
                    navButton("circle", "home", "Home")
                    navButton("square.on.square", "recents", "Recent apps")
                }
                HStack {
                    TextField("Type on the phone", text: $text)
                        .textFieldStyle(.roundedBorder)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                        .onSubmit(sendText)
                    Button("Send", action: sendText).disabled(text.isEmpty)
                }
            }

            HStack {
                Toggle("Live", isOn: $session.liveScreen).fixedSize()
                Spacer()
                Text(footer).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
        }
    }

    private var footer: String {
        if let error = session.screenError, session.screen != nil { return error }
        let hint = session.canInput ? "Tap, drag to swipe, hold to long-press" : "View only"
        guard let fps = session.mirrorFPS else { return hint }
        return String(format: "%.1f fps · %@", fps, hint)
    }

    private func navButton(_ symbol: String, _ key: String, _ label: String) -> some View {
        Button { session.key(key) } label: {
            Image(systemName: symbol).font(.title3).frame(width: 44, height: 32)
        }
        .buttonStyle(.bordered)
        .accessibilityLabel(label)
    }

    private func sendText() {
        session.typeText(text)
        text = ""
    }

    private func touchGesture(rect: CGRect, pixels: CGSize?) -> some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { _ in
                if touchStart == nil { touchStart = Date() }
            }
            .onEnded { value in
                let heldMS = Int(Date().timeIntervalSince(touchStart ?? Date()) * 1000)
                touchStart = nil
                guard let pixels,
                      let a = toDevice(value.startLocation, rect: rect, pixels: pixels),
                      let b = toDevice(value.location, rect: rect, pixels: pixels, clamp: true) else { return }
                if hypot(b.x - a.x, b.y - a.y) > 15 {
                    session.send(["type": "swipe", "x1": Int(a.x), "y1": Int(a.y), "x2": Int(b.x), "y2": Int(b.y),
                                  "ms": min(max(heldMS, 80), 2000)])
                } else if heldMS > 500 {
                    session.send(["type": "swipe", "x1": Int(a.x), "y1": Int(a.y), "x2": Int(a.x), "y2": Int(a.y),
                                  "ms": min(heldMS, 3000)])
                } else {
                    session.send(["type": "tap", "x": Int(a.x), "y": Int(a.y)])
                }
            }
    }
}

/// The rect an aspect-fit image of `content` size occupies, centered in `box`.
func fittedRect(_ content: CGSize, in box: CGSize) -> CGRect {
    guard content.width > 0, content.height > 0 else { return .zero }
    let scale = min(box.width / content.width, box.height / content.height)
    let size = CGSize(width: content.width * scale, height: content.height * scale)
    return CGRect(x: (box.width - size.width) / 2, y: (box.height - size.height) / 2,
                  width: size.width, height: size.height)
}

/// Maps a point in the view to device pixels; nil when it falls outside the image.
func toDevice(_ point: CGPoint, rect: CGRect, pixels: CGSize, clamp: Bool = false) -> CGPoint? {
    guard rect.width > 0, rect.height > 0 else { return nil }
    var x = (point.x - rect.minX) / rect.width
    var y = (point.y - rect.minY) / rect.height
    if clamp {
        x = min(max(x, 0), 1)
        y = min(max(y, 0), 1)
    } else if !(0...1).contains(x) || !(0...1).contains(y) {
        return nil
    }
    return CGPoint(x: min(x * pixels.width, pixels.width - 1), y: min(y * pixels.height, pixels.height - 1))
}

// MARK: - Metrics

private struct MetricsPanel: View {
    let session: DeviceSession
    private let columns = [GridItem(.adaptive(minimum: 150), spacing: 10)]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let error = session.metricsError {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(.orange)
            } else if session.appRunning == false, !session.package.isEmpty {
                Label("\(session.package) is not running", systemImage: "moon.zzz")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            LazyVGrid(columns: columns, spacing: 10) {
                ForEach(MetricKind.all) { kind in
                    MetricCard(kind: kind, value: session.latest[kind.key], history: session.history[kind.key] ?? [])
                }
            }
        }
    }
}

private struct MetricCard: View {
    let kind: MetricKind
    let value: Double?
    let history: [Double?]

    private struct Point: Identifiable {
        let id: Int
        let value: Double
    }

    private var points: [Point] {
        history.enumerated().compactMap { index, value in value.map { Point(id: index, value: $0) } }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(kind.label).font(.caption).foregroundStyle(.secondary)
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(value.map(Self.format) ?? "–").font(.title2.monospacedDigit())
                Text(kind.unit).font(.caption).foregroundStyle(.secondary)
            }
            Chart(points) { point in
                LineMark(x: .value("Sample", point.id), y: .value(kind.label, point.value))
                    .interpolationMethod(.monotone)
            }
            .chartXAxis(.hidden)
            .chartYAxis(.hidden)
            .chartXScale(domain: 0...(Limits.metricHistory - 1))
            .frame(height: 32)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 10))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(kind.label): \(value.map(Self.format) ?? "no data") \(kind.unit)")
    }

    static func format(_ value: Double) -> String {
        value == value.rounded() && abs(value) < 1_000_000 ? String(Int(value)) : String(format: "%.1f", value)
    }
}

// MARK: - Logs

private struct LogsPanel: View {
    let session: DeviceSession
    @State private var filter = ""
    @State private var minLevel: LogLevelFilter = .all

    private var visible: [LogLine] {
        session.logs.filter { line in
            minLevel.allows(line.level) && (filter.isEmpty || line.text.localizedCaseInsensitiveContains(filter))
        }
    }

    var body: some View {
        let lines = visible
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                TextField("Filter logs", text: $filter)
                    .textFieldStyle(.roundedBorder)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Picker("Level", selection: $minLevel) {
                    ForEach(LogLevelFilter.allCases) { Text($0.title).tag($0) }
                }
                Button { session.setLogsPaused(!session.logsPaused) } label: {
                    Image(systemName: session.logsPaused ? "play.fill" : "pause.fill")
                }
                .accessibilityLabel(session.logsPaused ? "Resume logs" : "Pause logs")
                Button { session.clearLogs() } label: { Image(systemName: "trash") }
                    .accessibilityLabel("Clear logs")
            }
            .padding(8)
            Divider()
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 1) {
                        ForEach(lines) { line in
                            Text(line.text)
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(Self.color(for: line.level))
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .id(line.id)
                        }
                        if let status = session.logStatus {
                            Text(status).font(.caption).foregroundStyle(.secondary).padding(.top, 4)
                        }
                    }
                    .padding(8)
                    .textSelection(.enabled)
                }
                .onChange(of: lines.last?.id) { _, id in
                    if let id { proxy.scrollTo(id, anchor: .bottom) }
                }
            }
        }
    }

    static func color(for level: Character?) -> Color {
        switch level {
        case "E"?, "F"?: .red
        case "W"?: .orange
        case "D"?, "V"?: .secondary
        default: .primary
        }
    }
}
