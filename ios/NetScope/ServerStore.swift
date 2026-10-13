import SwiftUI

@Observable
final class ServerStore {
    private(set) var servers: [Server] = []
    private let defaultsKey = "servers"

    init() {
        if let data = UserDefaults.standard.data(forKey: defaultsKey),
           let saved = try? JSONDecoder().decode([Server].self, from: data) {
            servers = saved
        }
    }

    func add(name: String, link: ServerLink) {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        let server = Server(id: UUID(),
                            name: trimmed.isEmpty ? (link.baseURL.host() ?? "Server") : trimmed,
                            url: link.baseURL.absoluteString)
        Keychain.set(link.token, account: server.id.uuidString)
        servers.append(server)
        save()
    }

    func remove(at offsets: IndexSet) {
        for index in offsets {
            Keychain.delete(servers[index].id.uuidString)
        }
        servers.remove(atOffsets: offsets)
        save()
    }

    func client(for server: Server) -> NetScopeClient {
        let url = URL(string: server.url) ?? URL(string: "http://127.0.0.1:8765/")!
        return NetScopeClient(link: ServerLink(baseURL: url, token: Keychain.get(server.id.uuidString) ?? ""))
    }

    private func save() {
        if let data = try? JSONEncoder().encode(servers) {
            UserDefaults.standard.set(data, forKey: defaultsKey)
        }
    }
}
