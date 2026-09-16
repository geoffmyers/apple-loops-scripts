import Foundation
import Combine
import UniformTypeIdentifiers
import AppKit

@MainActor
class ConverterViewModel: ObservableObject {
    @Published var files: [AudioFile] = []
    @Published var selectedFiles: Set<UUID> = []
    @Published var settings = ConversionSettings()
    @Published var pythonBridge = PythonBridge()

    @Published var isConverting: Bool = false
    @Published var showingAlert: Bool = false
    @Published var alertTitle: String = ""
    @Published var alertMessage: String = ""

    @Published var batchMetadata = LoopMetadata()

    private var cancellables = Set<AnyCancellable>()

    init() {
        pythonBridge.$isRunning
            .receive(on: RunLoop.main)
            .assign(to: &$isConverting)
    }

    var selectedAudioFiles: [AudioFile] {
        files.filter { selectedFiles.contains($0.id) }
    }

    var hasSelection: Bool {
        !selectedFiles.isEmpty
    }

    var canConvert: Bool {
        !files.isEmpty && !isConverting
    }

    func addFiles(urls: [URL]) {
        for url in urls {
            if url.hasDirectoryPath {
                addDirectory(url: url)
            } else if AudioFile.isSupported(url: url) {
                let file = AudioFile(url: url)
                if !files.contains(where: { $0.url == url }) {
                    files.append(file)
                    Task {
                        await extractMetadata(for: file)
                    }
                }
            }
        }
    }

    private func addDirectory(url: URL) {
        let fileManager = FileManager.default
        guard let enumerator = fileManager.enumerator(
            at: url,
            includingPropertiesForKeys: [.isRegularFileKey],
            options: [.skipsHiddenFiles]
        ) else { return }

        while let fileURL = enumerator.nextObject() as? URL {
            if AudioFile.isSupported(url: fileURL) {
                let file = AudioFile(url: fileURL)
                if !files.contains(where: { $0.url == fileURL }) {
                    files.append(file)
                    Task {
                        await extractMetadata(for: file)
                    }
                }
            }
        }
    }

    func extractMetadata(for file: AudioFile) async {
        do {
            let metadata = try await pythonBridge.runDryRun(for: file)
            file.metadata = metadata
            getDuration(for: file)
        } catch {
            print("Failed to extract metadata for \(file.filename): \(error)")
        }
    }

    private func getDuration(for file: AudioFile) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/afinfo")
        process.arguments = [file.url.path]

        let pipe = Pipe()
        process.standardOutput = pipe

        do {
            try process.run()
            process.waitUntilExit()

            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            if let output = String(data: data, encoding: .utf8) {
                for line in output.components(separatedBy: "\n") {
                    if line.contains("estimated duration:") {
                        let parts = line.components(separatedBy: ":")
                        if parts.count >= 2,
                           let duration = Double(parts[1].trimmingCharacters(in: .whitespaces).components(separatedBy: " ").first ?? "") {
                            file.duration = duration
                        }
                    }
                }
            }
        } catch {
            print("Failed to get duration: \(error)")
        }
    }

    func removeSelectedFiles() {
        files.removeAll { selectedFiles.contains($0.id) }
        selectedFiles.removeAll()
    }

    func removeAllFiles() {
        files.removeAll()
        selectedFiles.removeAll()
    }

    func selectAll() {
        selectedFiles = Set(files.map { $0.id })
    }

    func deselectAll() {
        selectedFiles.removeAll()
    }

    func applyBatchMetadata() {
        for file in selectedAudioFiles {
            if batchMetadata.tempo != nil {
                file.metadata.tempo = batchMetadata.tempo
                file.metadata.isAutoDetectedTempo = false
            }
            if batchMetadata.key != nil {
                file.metadata.key = batchMetadata.key
                file.metadata.keyType = batchMetadata.keyType
                file.metadata.isAutoDetectedKey = false
            }
            if batchMetadata.category != nil {
                file.metadata.category = batchMetadata.category
                file.metadata.isAutoDetectedCategory = false
            }
            if batchMetadata.genre != nil {
                file.metadata.genre = batchMetadata.genre
                file.metadata.isAutoDetectedGenre = false
            }
            if !batchMetadata.subcategory.isEmpty {
                file.metadata.subcategory = batchMetadata.subcategory
            }
            if !batchMetadata.descriptors.isEmpty {
                file.metadata.descriptors = batchMetadata.descriptors
            }
            file.metadata.timeSignature = batchMetadata.timeSignature
        }
    }

    func openFilePicker() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = true
        panel.canChooseFiles = true
        panel.allowedContentTypes = AudioFile.supportedExtensions.compactMap {
            UTType(filenameExtension: $0)
        }

        if panel.runModal() == .OK {
            addFiles(urls: panel.urls)
        }
    }

    func selectOutputDirectory() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.prompt = "Select Output Folder"

        if panel.runModal() == .OK, let url = panel.url {
            settings.outputDirectory = url
        }
    }

    func startConversion() {
        guard canConvert else { return }

        for file in files {
            file.status = .queued
        }

        Task {
            do {
                let result = try await pythonBridge.convert(
                    files: files,
                    settings: settings,
                    metadataOverrides: [:]
                )

                showAlert(
                    title: "Conversion Complete",
                    message: """
                    Successfully converted: \(result.successCount) files
                    Failed: \(result.failureCount) files
                    Duration: \(String(format: "%.1f", result.totalDuration)) seconds

                    Output: \(result.outputDirectory.path)
                    """
                )
            } catch PythonBridgeError.cancelled {
                showAlert(title: "Cancelled", message: "Conversion was cancelled.")
            } catch {
                showAlert(title: "Error", message: error.localizedDescription)
            }
        }
    }

    func cancelConversion() {
        pythonBridge.cancel()
    }

    private func showAlert(title: String, message: String) {
        alertTitle = title
        alertMessage = message
        showingAlert = true
    }
}
