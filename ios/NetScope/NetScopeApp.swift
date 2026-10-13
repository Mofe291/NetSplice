import SwiftUI

@main
struct NetScopeApp: App {
    @State private var store = ServerStore()

    var body: some Scene {
        WindowGroup {
            ServerListView()
                .environment(store)
        }
    }
}
