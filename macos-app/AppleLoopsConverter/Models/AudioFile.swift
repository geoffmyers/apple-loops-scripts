import Foundation

enum ConversionStatus: Equatable {
    case pending
    case queued
    case converting
    case completed
    case failed(String)

    var displayName: String {
        switch self {
        case .pending: return "Pending"
        case .queued: return "Queued"
        case .converting: return "Converting..."
        case .completed: return "Completed"
        case .failed(let error): return "Failed: \(error)"
        }
    }

    var systemImage: String {
        switch self {
        case .pending: return "circle"
        case .queued: return "clock"
        case .converting: return "arrow.triangle.2.circlepath"
        case .completed: return "checkmark.circle.fill"
        case .failed: return "exclamationmark.circle.fill"
        }
    }
}

class AudioFile: Identifiable, ObservableObject, Hashable {
    let id = UUID()
    let url: URL
    let filename: String
    let fileExtension: String

    @Published var duration: Double?
    @Published var metadata: LoopMetadata = .empty
    @Published var status: ConversionStatus = .pending
    @Published var isSelected: Bool = false

    init(url: URL) {
        self.url = url
        self.filename = url.deletingPathExtension().lastPathComponent
        self.fileExtension = url.pathExtension.lowercased()
    }

    var displayDuration: String {
        guard let duration = duration else { return "--:--" }
        let minutes = Int(duration) / 60
        let seconds = Int(duration) % 60
        let milliseconds = Int((duration.truncatingRemainder(dividingBy: 1)) * 100)
        return String(format: "%d:%02d.%02d", minutes, seconds, milliseconds)
    }

    var displayTempo: String {
        if let tempo = metadata.tempo {
            let autoIndicator = metadata.isAutoDetectedTempo ? " (auto)" : ""
            return String(format: "%.1f BPM%@", tempo, autoIndicator)
        }
        return "—"
    }

    var displayKey: String {
        if let key = metadata.key {
            let type = metadata.keyType == .minor ? "m" : ""
            let autoIndicator = metadata.isAutoDetectedKey ? " (auto)" : ""
            return "\(key.rawValue)\(type)\(autoIndicator)"
        }
        return "—"
    }

    static func == (lhs: AudioFile, rhs: AudioFile) -> Bool {
        lhs.id == rhs.id
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(id)
    }

    static let supportedExtensions: Set<String> = [
        "wav", "aif", "aiff", "mp3", "m4a", "aac", "flac", "alac", "caf", "ogg", "wma"
    ]

    static func isSupported(url: URL) -> Bool {
        supportedExtensions.contains(url.pathExtension.lowercased())
    }
}
