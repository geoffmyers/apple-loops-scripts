import SwiftUI

struct SettingsView: View {
    @ObservedObject var viewModel: ConverterViewModel

    var body: some View {
        VStack(spacing: 0) {
            headerView

            Divider()

            settingsForm

            Divider()

            conversionControls
        }
        .background(Color(NSColor.controlBackgroundColor))
    }

    private var headerView: some View {
        HStack {
            Text("Settings")
                .font(.headline)
            Spacer()
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
    }

    private var settingsForm: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                outputSection
                codecSection
                transientSection
            }
            .padding()
        }
    }

    private var outputSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Output")
                .font(.subheadline)
                .fontWeight(.semibold)

            VStack(alignment: .leading, spacing: 4) {
                Text("Output Directory")
                    .font(.caption)
                    .foregroundColor(.secondary)

                HStack {
                    Text(viewModel.settings.outputDirectory.path)
                        .lineLimit(1)
                        .truncationMode(.head)
                        .font(.caption)
                        .foregroundColor(.secondary)

                    Spacer()

                    Button("Choose...") {
                        viewModel.selectOutputDirectory()
                    }
                }
            }

            Toggle("Preserve directory structure", isOn: $viewModel.settings.preserveDirectoryStructure)
                .font(.caption)
        }
    }

    private var codecSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Audio Codec")
                .font(.subheadline)
                .fontWeight(.semibold)

            Picker("Codec", selection: $viewModel.settings.useLossyCodec) {
                Text("ALAC (Lossless)").tag(false)
                Text("AAC (Lossy)").tag(true)
            }
            .pickerStyle(.segmented)
            .labelsHidden()

            if viewModel.settings.useLossyCodec {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Bitrate: \(viewModel.settings.bitrate / 1000) kbps")
                        .font(.caption)
                        .foregroundColor(.secondary)

                    Picker("Bitrate", selection: $viewModel.settings.bitrate) {
                        ForEach(ConversionSettings.bitrateOptions, id: \.value) { option in
                            Text(option.label).tag(option.value)
                        }
                    }
                    .labelsHidden()
                }
            }
        }
    }

    private var transientSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Beat Detection")
                .font(.subheadline)
                .fontWeight(.semibold)

            Toggle("Enable transient detection", isOn: $viewModel.settings.useTransientDetection)
                .font(.caption)

            if viewModel.settings.useTransientDetection {
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text("Onset Threshold")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        Spacer()
                        Text(String(format: "%.2f", viewModel.settings.onsetThreshold))
                            .font(.caption)
                            .monospacedDigit()
                    }

                    Slider(value: $viewModel.settings.onsetThreshold, in: 0.0...1.0, step: 0.05)
                }

                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text("Min Markers/Beat")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        Spacer()
                        Text(String(format: "%.1f", viewModel.settings.minMarkersPerBeat))
                            .font(.caption)
                            .monospacedDigit()
                    }

                    Slider(value: $viewModel.settings.minMarkersPerBeat, in: 0.5...4.0, step: 0.5)
                }

                Text("Higher threshold = fewer markers, lower = more sensitive detection")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            } else {
                Text("Quarter-note markers will be used instead")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }

    private var conversionControls: some View {
        VStack(spacing: 12) {
            if viewModel.isConverting {
                progressView
            }

            HStack {
                if viewModel.isConverting {
                    Button("Cancel") {
                        viewModel.cancelConversion()
                    }
                    .buttonStyle(.bordered)
                }

                Spacer()

                Button(action: viewModel.startConversion) {
                    HStack {
                        Image(systemName: "waveform.badge.plus")
                        Text("Convert \(viewModel.files.count) Files")
                    }
                    .frame(minWidth: 150)
                }
                .buttonStyle(.borderedProminent)
                .disabled(!viewModel.canConvert)
            }
        }
        .padding()
    }

    @ViewBuilder
    private var progressView: some View {
        if let progress = viewModel.pythonBridge.currentProgress {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text(progress.message)
                        .font(.caption)
                        .lineLimit(1)
                    Spacer()
                    Text("\(progress.currentIndex)/\(progress.totalFiles)")
                        .font(.caption)
                        .monospacedDigit()
                }

                ProgressView(value: progress.percentage)
                    .progressViewStyle(.linear)
            }
        }
    }
}

#Preview {
    SettingsView(viewModel: ConverterViewModel())
        .frame(width: 300, height: 500)
}
