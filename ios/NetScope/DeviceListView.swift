import SwiftUI

struct DeviceListView: View {
    let server: Server
    let client: NetScopeClient
    @State private var devices: [Device] = []
    @State private var error: String?
    @State private var loaded = false
    @State private var showingConnect = false

    var body: some View {
        List {
            if let error {
                Section {
                    Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
                }
            }
            Section {
                ForEach(devices) { device in
                    NavigationLink(value: device) {
                        DeviceRow(device: device)
                    }
                    .disabled(!device.isReady)
                }
            } header: {
                Text("Devices")
            } footer: {
                if loaded && devices.isEmpty {
                    Text("No devices yet. Tap + to connect an Android phone, or run `sudo pymobiledevice3 remote tunneld` on the server for iPhones.")
                }
            }
        }
        .navigationTitle(server.name)
        .navigationDestination(for: Device.self) { device in
            DeviceView(client: client, device: device)
        }
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { showingConnect = true } label: { Label("Connect Android", systemImage: "plus") }
            }
        }
        .sheet(isPresented: $showingConnect) {
            ConnectAndroidView(client: client) {
                Task { await load() }
            }
        }
        .refreshable { await load() }
        .task {
            while !Task.isCancelled {
                await load()
                try? await Task.sleep(for: .seconds(10))
            }
        }
    }

    private func load() async {
        do {
            devices = try await client.devices()
            error = nil
        } catch {
            if (error as? URLError)?.code == .cancelled { return }
            self.error = error.localizedDescription
        }
        loaded = true
    }
}

private struct DeviceRow: View {
    let device: Device

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: device.isIOS ? "iphone" : "smartphone")
                .font(.title2)
                .foregroundStyle(device.isReady ? Color.accentColor : .secondary)
                .frame(width: 30)
            VStack(alignment: .leading, spacing: 2) {
                Text(device.name)
                Text(device.isReady ? device.id : "\(device.id) · \(device.state ?? "")")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
        }
    }
}

struct ConnectAndroidView: View {
    let client: NetScopeClient
    let onConnected: () -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var address = ""
    @State private var pairAddress = ""
    @State private var pairCode = ""
    @State private var working = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("IP:port", text: $address)
                        .keyboardType(.numbersAndPunctuation)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } header: {
                    Text("Phone address")
                } footer: {
                    Text("On the Android phone: Developer options → Wireless debugging. Use the IP address & port shown there; the phone's Tailscale 100.x address works too.")
                }
                Section {
                    TextField("Pairing IP:port", text: $pairAddress)
                        .keyboardType(.numbersAndPunctuation)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    TextField("6-digit code", text: $pairCode)
                        .keyboardType(.numberPad)
                } header: {
                    Text("First time only")
                } footer: {
                    Text("Tap \u{201C}Pair device with pairing code\u{201D} on the phone and enter what it shows.")
                }
                if let error {
                    Section {
                        Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Connect Android")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    if working {
                        ProgressView()
                    } else {
                        Button("Connect") { Task { await connect() } }
                            .disabled(address.isEmpty && (pairAddress.isEmpty || pairCode.isEmpty))
                    }
                }
            }
        }
    }

    private func connect() async {
        working = true
        defer { working = false }
        do {
            _ = try await client.connectAndroid(
                address: address.trimmingCharacters(in: .whitespaces),
                pairAddress: pairAddress.trimmingCharacters(in: .whitespaces),
                pairCode: pairCode.trimmingCharacters(in: .whitespaces))
            onConnected()
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
