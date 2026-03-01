import Foundation
import Combine

enum PythonBridgeError: LocalizedError {
    case scriptNotFound
    case pythonNotFound
    case conversionFailed(String)
    case cancelled

    var errorDescription: String? {
        switch self {
        case .scriptNotFound:
            return "Could not find convert_to_apple_loops.py script"
        case .pythonNotFound:
            return "Python 3 not found. Please install Python 3.9 or later."
        case .conversionFailed(let message):
            return "Conversion failed: \(message)"
        case .cancelled:
            return "Conversion was cancelled"
        }
    }
}

struct ConversionProgress {
    let currentFile: String
    let currentIndex: Int
    let totalFiles: Int
    let message: String

    var percentage: Double {
        guard totalFiles > 0 else { return 0 }
        return Double(currentIndex) / Double(totalFiles)
    }
}

struct ConversionResult {
    let successCount: Int
    let failureCount: Int
    let skippedCount: Int
    let totalDuration: TimeInterval
    let outputDirectory: URL
}

class PythonBridge: ObservableObject {
    @Published var isRunning: Bool = false
    @Published var currentProgress: ConversionProgress?
    @Published var logOutput: String = ""

    private var process: Process?
    private var cancellables = Set<AnyCancellable>()

    // MARK: - Bundled Resources Paths

    /// Path to the bundled Python virtual environment
    private var bundledPythonPath: URL? {
        guard let resourcesPath = Bundle.main.resourcePath else { return nil }
        let venvPython = URL(fileURLWithPath: resourcesPath)
            .appendingPathComponent("python/venv/bin/python3")
        if FileManager.default.fileExists(atPath: venvPython.path) {
            return venvPython
        }
        return nil
    }

    /// Path to the bundled conversion script
    private var bundledScriptPath: URL? {
        guard let resourcesPath = Bundle.main.resourcePath else { return nil }
        let scriptPath = URL(fileURLWithPath: resourcesPath)
            .appendingPathComponent("Scripts/convert_to_apple_loops.py")
        if FileManager.default.fileExists(atPath: scriptPath.path) {
            return scriptPath
        }
        return nil
    }

    /// Whether the app has bundled Python environment
    var hasBundledPython: Bool {
        bundledPythonPath != nil && bundledScriptPath != nil
    }

    // MARK: - Script Path Resolution

    private var scriptPath: URL? {
        // 1. Check for bundled script in Scripts folder (self-contained mode)
        if let bundled = bundledScriptPath {
            return bundled
        }

        // 2. Check for bundled script in app resources (legacy)
        if let bundledPath = Bundle.main.url(forResource: "convert_to_apple_loops", withExtension: "py") {
            return bundledPath
        }

        // 3. Check user-configured path from Settings
        let userConfiguredPath = UserDefaults.standard.string(forKey: "pythonScriptPath") ?? ""
        if !userConfiguredPath.isEmpty {
            let url = URL(fileURLWithPath: userConfiguredPath)
            if FileManager.default.fileExists(atPath: url.path) {
                return url
            }
        }

        // 4. When running from Xcode DerivedData, navigate to repo location
        let appPath = Bundle.main.bundleURL.path
        if appPath.contains("DerivedData") {
            let possibleRepoPaths = [
                FileManager.default.homeDirectoryForCurrentUser
                    .appendingPathComponent("Dropbox/SimDex/Repositories/Geoff Myers Mono Repo/python-scripts/apple-loops-scripts/convert_to_apple_loops.py"),
                FileManager.default.homeDirectoryForCurrentUser
                    .appendingPathComponent("Developer/Geoff Myers Mono Repo/python-scripts/apple-loops-scripts/convert_to_apple_loops.py"),
                FileManager.default.homeDirectoryForCurrentUser
                    .appendingPathComponent("Projects/Geoff Myers Mono Repo/python-scripts/apple-loops-scripts/convert_to_apple_loops.py")
            ]

            for path in possibleRepoPaths {
                if FileManager.default.fileExists(atPath: path.path) {
                    return path
                }
            }
        }

        // 5. Check relative to app bundle (when app is in macos-apps/AppleLoopsConverter/)
        let appDirectory = Bundle.main.bundleURL.deletingLastPathComponent()
        let repoRelativeScript = appDirectory
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("python-scripts/apple-loops-scripts/convert_to_apple_loops.py")

        if FileManager.default.fileExists(atPath: repoRelativeScript.path) {
            return repoRelativeScript
        }

        // 6. Check in /usr/local/bin (if installed system-wide)
        let systemPath = URL(fileURLWithPath: "/usr/local/bin/convert_to_apple_loops.py")
        if FileManager.default.fileExists(atPath: systemPath.path) {
            return systemPath
        }

        return nil
    }

    // MARK: - Python Path Resolution

    private var pythonPath: String? {
        // 1. Check for bundled Python first (self-contained mode)
        if let bundled = bundledPythonPath {
            return bundled.path
        }

        // 2. Check user-configured path from Settings
        let userConfiguredPython = UserDefaults.standard.string(forKey: "pythonPath") ?? ""
        if !userConfiguredPython.isEmpty && FileManager.default.fileExists(atPath: userConfiguredPython) {
            return userConfiguredPython
        }

        // 3. Fall back to system Python
        let possiblePaths = [
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3"
        ]

        for path in possiblePaths {
            if FileManager.default.fileExists(atPath: path) {
                return path
            }
        }

        return nil
    }

    // MARK: - Environment Setup

    /// Get environment variables for Python process
    private func pythonEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment

        // If using bundled Python, set up the virtual environment
        if let bundledPython = bundledPythonPath {
            let venvDir = bundledPython.deletingLastPathComponent().deletingLastPathComponent()
            env["VIRTUAL_ENV"] = venvDir.path
            env["PATH"] = "\(venvDir.appendingPathComponent("bin").path):\(env["PATH"] ?? "")"

            // Remove PYTHONHOME to avoid conflicts
            env.removeValue(forKey: "PYTHONHOME")
        }

        return env
    }

    // MARK: - Public Methods

    func runDryRun(for file: AudioFile) async throws -> LoopMetadata {
        guard let scriptPath = scriptPath else {
            throw PythonBridgeError.scriptNotFound
        }

        guard let pythonPath = pythonPath else {
            throw PythonBridgeError.pythonNotFound
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: pythonPath)
        process.environment = pythonEnvironment()
        process.arguments = [
            scriptPath.path,
            file.url.path,
            "--dry-run",
            "--detailed"
        ]

        let outputPipe = Pipe()
        let errorPipe = Pipe()
        process.standardOutput = outputPipe
        process.standardError = errorPipe

        try process.run()
        process.waitUntilExit()

        let outputData = outputPipe.fileHandleForReading.readDataToEndOfFile()
        let output = String(data: outputData, encoding: .utf8) ?? ""

        return parseMetadataFromDryRun(output)
    }

    func convert(
        files: [AudioFile],
        settings: ConversionSettings,
        metadataOverrides: [UUID: LoopMetadata]
    ) async throws -> ConversionResult {
        guard let scriptPath = scriptPath else {
            throw PythonBridgeError.scriptNotFound
        }

        guard let pythonPath = pythonPath else {
            throw PythonBridgeError.pythonNotFound
        }

        await MainActor.run {
            isRunning = true
            logOutput = ""
            currentProgress = ConversionProgress(
                currentFile: "",
                currentIndex: 0,
                totalFiles: files.count,
                message: "Starting conversion..."
            )
        }

        var successCount = 0
        var failureCount = 0
        let skippedCount = 0
        let startTime = Date()

        for (index, file) in files.enumerated() {
            if !isRunning {
                throw PythonBridgeError.cancelled
            }

            await MainActor.run {
                file.status = .converting
                currentProgress = ConversionProgress(
                    currentFile: file.filename,
                    currentIndex: index,
                    totalFiles: files.count,
                    message: "Converting \(file.filename)..."
                )
            }

            do {
                try await convertSingleFile(
                    file: file,
                    settings: settings,
                    metadata: metadataOverrides[file.id] ?? file.metadata,
                    pythonPath: pythonPath,
                    scriptPath: scriptPath
                )
                successCount += 1
                await MainActor.run {
                    file.status = .completed
                }
            } catch {
                failureCount += 1
                await MainActor.run {
                    file.status = .failed(error.localizedDescription)
                }
            }
        }

        let duration = Date().timeIntervalSince(startTime)

        await MainActor.run {
            isRunning = false
            currentProgress = ConversionProgress(
                currentFile: "",
                currentIndex: files.count,
                totalFiles: files.count,
                message: "Conversion complete!"
            )
        }

        return ConversionResult(
            successCount: successCount,
            failureCount: failureCount,
            skippedCount: skippedCount,
            totalDuration: duration,
            outputDirectory: settings.outputDirectory
        )
    }

    private func convertSingleFile(
        file: AudioFile,
        settings: ConversionSettings,
        metadata: LoopMetadata,
        pythonPath: String,
        scriptPath: URL
    ) async throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: pythonPath)
        process.environment = pythonEnvironment()

        var arguments = [
            scriptPath.path,
            file.url.path,
            "--output-dir", settings.outputDirectory.path
        ]

        if settings.useLossyCodec {
            arguments.append("--lossy")
            arguments.append(contentsOf: ["--bitrate", String(settings.bitrate)])
        }

        if !settings.useTransientDetection {
            arguments.append("--no-transient-detection")
        } else {
            arguments.append(contentsOf: ["--onset-threshold", String(settings.onsetThreshold)])
            arguments.append(contentsOf: ["--min-markers-per-beat", String(settings.minMarkersPerBeat)])
        }

        if let tempo = metadata.tempo {
            arguments.append(contentsOf: ["--tempo", String(tempo)])
        }

        if let key = metadata.key {
            let keyString = metadata.keyType == .minor ? "\(key.rawValue)m" : key.rawValue
            arguments.append(contentsOf: ["--key", keyString])
        }

        if let category = metadata.category {
            arguments.append(contentsOf: ["--category", category.rawValue])
        }

        if !metadata.subcategory.isEmpty {
            arguments.append(contentsOf: ["--subcategory", metadata.subcategory])
        }

        if let genre = metadata.genre {
            arguments.append(contentsOf: ["--genre", genre.rawValue])
        }

        if !metadata.descriptors.isEmpty {
            let descriptorString = metadata.descriptors.map { $0.rawValue }.joined(separator: ",")
            arguments.append(contentsOf: ["--descriptors", descriptorString])
        }

        arguments.append(contentsOf: ["--time-signature", metadata.timeSignature.rawValue])

        if let beatCount = metadata.beatCount {
            arguments.append(contentsOf: ["--beat-count", String(beatCount)])
        }

        process.arguments = arguments

        let outputPipe = Pipe()
        let errorPipe = Pipe()
        process.standardOutput = outputPipe
        process.standardError = errorPipe

        self.process = process

        outputPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if let output = String(data: data, encoding: .utf8), !output.isEmpty {
                Task { @MainActor in
                    self?.logOutput += output
                }
            }
        }

        errorPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if let output = String(data: data, encoding: .utf8), !output.isEmpty {
                Task { @MainActor in
                    self?.logOutput += "[ERROR] \(output)"
                }
            }
        }

        try process.run()
        process.waitUntilExit()

        outputPipe.fileHandleForReading.readabilityHandler = nil
        errorPipe.fileHandleForReading.readabilityHandler = nil

        if process.terminationStatus != 0 {
            let errorData = errorPipe.fileHandleForReading.readDataToEndOfFile()
            let errorOutput = String(data: errorData, encoding: .utf8) ?? "Unknown error"
            throw PythonBridgeError.conversionFailed(errorOutput)
        }
    }

    func cancel() {
        process?.terminate()
        isRunning = false
    }

    private func parseMetadataFromDryRun(_ output: String) -> LoopMetadata {
        var metadata = LoopMetadata()

        let lines = output.components(separatedBy: "\n")
        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            if trimmed.hasPrefix("Tempo:") {
                if let tempoStr = trimmed.components(separatedBy: ":").last?.trimmingCharacters(in: .whitespaces),
                   let tempo = Double(tempoStr.replacingOccurrences(of: " BPM", with: "")) {
                    metadata.tempo = tempo
                    metadata.isAutoDetectedTempo = true
                }
            } else if trimmed.hasPrefix("Key:") {
                if let keyStr = trimmed.components(separatedBy: ":").last?.trimmingCharacters(in: .whitespaces) {
                    let isMinor = keyStr.contains("m") || keyStr.contains("Minor")
                    let keyNote = keyStr.replacingOccurrences(of: "m", with: "")
                        .replacingOccurrences(of: " Minor", with: "")
                        .replacingOccurrences(of: " Major", with: "")
                        .trimmingCharacters(in: .whitespaces)

                    metadata.key = KeySignature.allCases.first { $0.rawValue == keyNote }
                    metadata.keyType = isMinor ? .minor : .major
                    metadata.isAutoDetectedKey = true
                }
            } else if trimmed.hasPrefix("Category:") {
                if let catStr = trimmed.components(separatedBy: ":").last?.trimmingCharacters(in: .whitespaces) {
                    metadata.category = LoopCategory.allCases.first { $0.rawValue == catStr }
                    metadata.isAutoDetectedCategory = true
                }
            } else if trimmed.hasPrefix("Genre:") {
                if let genreStr = trimmed.components(separatedBy: ":").last?.trimmingCharacters(in: .whitespaces) {
                    metadata.genre = LoopGenre.allCases.first { $0.rawValue == genreStr }
                    metadata.isAutoDetectedGenre = true
                }
            } else if trimmed.hasPrefix("Subcategory:") {
                metadata.subcategory = trimmed.components(separatedBy: ":").last?.trimmingCharacters(in: .whitespaces) ?? ""
            }
        }

        return metadata
    }
}
