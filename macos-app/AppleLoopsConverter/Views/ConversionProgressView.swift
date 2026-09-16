import SwiftUI

struct ConversionProgressView: View {
    @ObservedObject var pythonBridge: PythonBridge
    @Binding var isPresented: Bool

    var body: some View {
        VStack(spacing: 16) {
            headerView

            Divider()

            progressSection

            Divider()

            logSection

            Divider()

            footerView
        }
        .frame(width: 500, height: 400)
        .background(Color(NSColor.windowBackgroundColor))
    }

    private var headerView: some View {
        HStack {
            Image(systemName: "waveform.badge.plus")
                .font(.title2)
                .foregroundColor(.accentColor)

            Text("Converting to Apple Loops")
                .font(.headline)

            Spacer()

            if pythonBridge.isRunning {
                ProgressView()
                    .scaleEffect(0.7)
            } else {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green)
            }
        }
        .padding()
    }

    private var progressSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let progress = pythonBridge.currentProgress {
                HStack {
                    Text(progress.currentFile.isEmpty ? "Preparing..." : progress.currentFile)
                        .lineLimit(1)
                        .truncationMode(.middle)

                    Spacer()

                    Text("\(progress.currentIndex) of \(progress.totalFiles)")
                        .monospacedDigit()
                        .foregroundColor(.secondary)
                }
                .font(.subheadline)

                ProgressView(value: progress.percentage)
                    .progressViewStyle(.linear)

                Text(progress.message)
                    .font(.caption)
                    .foregroundColor(.secondary)
            } else {
                Text("Waiting to start...")
                    .foregroundColor(.secondary)
            }
        }
        .padding(.horizontal)
    }

    private var logSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("Log Output")
                    .font(.caption)
                    .foregroundColor(.secondary)

                Spacer()

                Button(action: copyLog) {
                    Image(systemName: "doc.on.doc")
                }
                .buttonStyle(.plain)
                .help("Copy log to clipboard")
            }

            ScrollViewReader { proxy in
                ScrollView {
                    Text(pythonBridge.logOutput.isEmpty ? "No output yet..." : pythonBridge.logOutput)
                        .font(.system(.caption, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .id("logBottom")
                }
                .background(Color(NSColor.textBackgroundColor))
                .cornerRadius(4)
                .onChange(of: pythonBridge.logOutput) { _ in
                    withAnimation {
                        proxy.scrollTo("logBottom", anchor: .bottom)
                    }
                }
            }
        }
        .padding(.horizontal)
        .frame(maxHeight: .infinity)
    }

    private var footerView: some View {
        HStack {
            if pythonBridge.isRunning {
                Button("Cancel") {
                    pythonBridge.cancel()
                }
                .keyboardShortcut(.escape)
            }

            Spacer()

            Button(pythonBridge.isRunning ? "Running..." : "Done") {
                isPresented = false
            }
            .keyboardShortcut(.return)
            .disabled(pythonBridge.isRunning)
        }
        .padding()
    }

    private func copyLog() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(pythonBridge.logOutput, forType: .string)
    }
}

#Preview {
    ConversionProgressView(
        pythonBridge: PythonBridge(),
        isPresented: .constant(true)
    )
}
