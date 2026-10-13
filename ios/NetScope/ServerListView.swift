import SwiftUI

struct ServerListView: View {
    @Environment(ServerStore.self) private var store
    @State private var showingAdd = false

    var body: some View {
        NavigationStack {
            List {
                ForEach(store.servers) { server in
                    NavigationLink(value: server) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(server.name)
                            Text(server.url).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                .onDelete { store.remove(at: $0) }
            }
            .overlay {
                if store.servers.isEmpty {
                    ContentUnavailableView {
                        Label("No servers yet", systemImage: "desktopcomputer")
                    } description: {
                        Text("On your computer run `netscope serve --host 0.0.0.0`, then add the URL it prints.")
                    } actions: {
                        Button("Add server") { showingAdd = true }
                            .buttonStyle(.borderedProminent)
                    }
                }
            }
            .navigationTitle("NetScope")
            .navigationDestination(for: Server.self) { server in
                DeviceListView(server: server, client: store.client(for: server))
            }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { showingAdd = true } label: { Label("Add server", systemImage: "plus") }
                }
            }
            .sheet(isPresented: $showingAdd) {
                AddServerView()
            }
        }
    }
}

struct AddServerView: View {
    @Environment(ServerStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var url = ""
    @State private var name = ""
    @State private var token = ""
    @State private var checking = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("http://100.x.y.z:8765/?token=…", text: $url, axis: .vertical)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } header: {
                    Text("Server URL")
                } footer: {
                    Text("Paste the URL that `netscope serve` prints. A Tailscale address (100.x.y.z or a MagicDNS name) works from anywhere on your tailnet.")
                }
                Section("Optional") {
                    TextField("Name", text: $name)
                    SecureField("Token (if it isn't in the URL)", text: $token)
                }
                if let error {
                    Section {
                        Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Add Server")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    if checking {
                        ProgressView()
                    } else {
                        Button("Add") { Task { await add() } }
                            .disabled(url.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
            }
        }
    }

    private func add() async {
        guard let link = ServerLink.parse(url, token: token) else {
            error = "That doesn't look like a server URL."
            return
        }
        checking = true
        defer { checking = false }
        do {
            _ = try await NetScopeClient(link: link).health()
            store.add(name: name, link: link)
            dismiss()
        } catch {
            self.error = "Couldn't reach NetScope: \(error.localizedDescription)"
        }
    }
}
