import SwiftUI
import UniformTypeIdentifiers

struct FileListView: View {
    @ObservedObject var viewModel: ConverterViewModel
    @State private var isTargeted: Bool = false

    var body: some View {
        VStack(spacing: 0) {
            headerView

            Divider()

            if viewModel.files.isEmpty {
                dropZoneView
            } else {
                fileTableView
            }

            Divider()

            footerView
        }
        .background(Color(NSColor.controlBackgroundColor))
    }

    private var headerView: some View {
        HStack {
            Text("Files")
                .font(.headline)

            Spacer()

            Text("\(viewModel.files.count) files")
                .foregroundColor(.secondary)
                .font(.caption)
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
    }

    private var dropZoneView: some View {
        VStack(spacing: 16) {
            Image(systemName: "square.and.arrow.down")
                .font(.system(size: 48))
                .foregroundColor(isTargeted ? .accentColor : .secondary)

            Text("Drop Audio Files Here")
                .font(.title2)
                .foregroundColor(isTargeted ? .accentColor : .primary)

            Text("or click to browse")
                .font(.caption)
                .foregroundColor(.secondary)

            Text("Supported: WAV, AIFF, MP3, M4A, FLAC, CAF, OGG")
                .font(.caption2)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(
                    style: StrokeStyle(lineWidth: 2, dash: [8])
                )
                .foregroundColor(isTargeted ? .accentColor : .secondary.opacity(0.5))
                .padding()
        )
        .onTapGesture {
            viewModel.openFilePicker()
        }
        .onDrop(of: [.fileURL], isTargeted: $isTargeted) { providers in
            handleDrop(providers: providers)
        }
    }

    private var fileTableView: some View {
        List(selection: $viewModel.selectedFiles) {
            ForEach(viewModel.files) { file in
                FileRowView(file: file)
                    .tag(file.id)
            }
            .onDelete { indexSet in
                viewModel.files.remove(atOffsets: indexSet)
            }
        }
        .listStyle(.inset(alternatesRowBackgrounds: true))
        .onDrop(of: [.fileURL], isTargeted: $isTargeted) { providers in
            handleDrop(providers: providers)
        }
    }

    private var footerView: some View {
        HStack {
            Button(action: viewModel.openFilePicker) {
                Label("Add Files", systemImage: "plus")
            }

            Button(action: viewModel.removeSelectedFiles) {
                Label("Remove", systemImage: "minus")
            }
            .disabled(!viewModel.hasSelection)

            Spacer()

            Button(action: viewModel.selectAll) {
                Text("Select All")
            }
            .disabled(viewModel.files.isEmpty)

            Button(action: viewModel.removeAllFiles) {
                Text("Clear All")
            }
            .disabled(viewModel.files.isEmpty)
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
    }

    private func handleDrop(providers: [NSItemProvider]) -> Bool {
        var urls: [URL] = []

        let group = DispatchGroup()

        for provider in providers {
            group.enter()
            provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
                defer { group.leave() }
                if let data = item as? Data,
                   let url = URL(dataRepresentation: data, relativeTo: nil) {
                    urls.append(url)
                }
            }
        }

        group.notify(queue: .main) {
            viewModel.addFiles(urls: urls)
        }

        return true
    }
}

struct FileRowView: View {
    @ObservedObject var file: AudioFile

    var body: some View {
        HStack(spacing: 12) {
            statusIcon
                .frame(width: 20)

            VStack(alignment: .leading, spacing: 2) {
                Text(file.filename)
                    .lineLimit(1)
                    .truncationMode(.middle)

                HStack(spacing: 8) {
                    Text(file.displayDuration)
                    Text("•")
                    Text(file.displayTempo)
                    Text("•")
                    Text(file.displayKey)
                }
                .font(.caption)
                .foregroundColor(.secondary)
            }

            Spacer()

            Text(file.fileExtension.uppercased())
                .font(.caption)
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(Color.secondary.opacity(0.2))
                .cornerRadius(4)
        }
        .padding(.vertical, 4)
    }

    @ViewBuilder
    private var statusIcon: some View {
        switch file.status {
        case .pending:
            Image(systemName: "circle")
                .foregroundColor(.secondary)
        case .queued:
            Image(systemName: "clock")
                .foregroundColor(.orange)
        case .converting:
            ProgressView()
                .scaleEffect(0.6)
        case .completed:
            Image(systemName: "checkmark.circle.fill")
                .foregroundColor(.green)
        case .failed:
            Image(systemName: "exclamationmark.circle.fill")
                .foregroundColor(.red)
        }
    }
}

#Preview {
    FileListView(viewModel: ConverterViewModel())
        .frame(width: 400, height: 500)
}
