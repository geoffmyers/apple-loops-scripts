import SwiftUI
import UniformTypeIdentifiers

@main
struct AppleLoopsConverterApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
                .frame(minWidth: 900, minHeight: 500)
        }
        .windowStyle(.titleBar)
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .newItem) {}

            CommandGroup(after: .newItem) {
                Button("Open Files...") {
                    NotificationCenter.default.post(name: .openFiles, object: nil)
                }
                .keyboardShortcut("o", modifiers: .command)
            }
        }

        Settings {
            SettingsWindowView()
        }
    }
}

struct SettingsWindowView: View {
    @AppStorage("pythonPath") private var pythonPath: String = ""
    @AppStorage("pythonScriptPath") private var pythonScriptPath: String = ""
    @AppStorage("defaultOutputDirectory") private var defaultOutputDirectory: String = ""

    var body: some View {
        Form {
            Section("Python Script") {
                HStack {
                    TextField("Script Path", text: $pythonScriptPath)
                        .textFieldStyle(.roundedBorder)

                    Button("Browse...") {
                        let panel = NSOpenPanel()
                        panel.allowedContentTypes = [.pythonScript]
                        panel.allowsMultipleSelection = false
                        panel.canChooseDirectories = false
                        if panel.runModal() == .OK, let url = panel.url {
                            pythonScriptPath = url.path
                        }
                    }
                }

                Text("Path to convert_to_apple_loops.py (leave empty to auto-detect)")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            Section("Python Interpreter") {
                TextField("Python Path (optional)", text: $pythonPath)
                    .textFieldStyle(.roundedBorder)

                Text("Leave empty to auto-detect Python 3")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            Section("Output") {
                TextField("Default Output Directory", text: $defaultOutputDirectory)
                    .textFieldStyle(.roundedBorder)

                Text("Leave empty to use ~/Library/Audio/Apple Loops/User Loops/")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .formStyle(.grouped)
        .frame(width: 500, height: 280)
        .padding()
    }
}

extension Notification.Name {
    static let openFiles = Notification.Name("openFiles")
}
