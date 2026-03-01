import Foundation

class ConversionSettings: ObservableObject {
    @Published var outputDirectory: URL
    @Published var useLossyCodec: Bool = false
    @Published var bitrate: Int = 256000
    @Published var useTransientDetection: Bool = true
    @Published var onsetThreshold: Double = 0.3
    @Published var minMarkersPerBeat: Double = 1.0
    @Published var preserveDirectoryStructure: Bool = false

    static let defaultOutputDirectory: URL = {
        let home = FileManager.default.homeDirectoryForCurrentUser
        return home.appendingPathComponent("Library/Audio/Apple Loops/User Loops")
    }()

    static let bitrateOptions: [(label: String, value: Int)] = [
        ("128 kbps", 128000),
        ("160 kbps", 160000),
        ("192 kbps", 192000),
        ("256 kbps (Recommended)", 256000),
        ("320 kbps", 320000)
    ]

    init() {
        self.outputDirectory = Self.defaultOutputDirectory
    }

    var codecDescription: String {
        useLossyCodec ? "AAC (Lossy)" : "ALAC (Lossless)"
    }

    var bitrateDescription: String {
        "\(bitrate / 1000) kbps"
    }
}
