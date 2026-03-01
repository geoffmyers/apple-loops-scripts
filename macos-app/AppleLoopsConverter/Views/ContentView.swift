import SwiftUI

struct ContentView: View {
    @StateObject private var viewModel = ConverterViewModel()
    @State private var showingProgressSheet = false

    var body: some View {
        NavigationSplitView {
            FileListView(viewModel: viewModel)
                .frame(minWidth: 300)
        } content: {
            MetadataEditorView(viewModel: viewModel)
                .frame(minWidth: 250)
        } detail: {
            SettingsView(viewModel: viewModel)
                .frame(minWidth: 280)
        }
        .navigationTitle("Apple Loops Converter")
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button(action: viewModel.openFilePicker) {
                    Label("Add Files", systemImage: "plus")
                }

                if viewModel.isConverting {
                    Button(action: { showingProgressSheet = true }) {
                        Label("Progress", systemImage: "list.bullet.rectangle")
                    }
                }
            }
        }
        .alert(viewModel.alertTitle, isPresented: $viewModel.showingAlert) {
            Button("OK", role: .cancel) {}
            if viewModel.alertTitle == "Conversion Complete" {
                Button("Open in Finder") {
                    NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: viewModel.settings.outputDirectory.path)
                }
            }
        } message: {
            Text(viewModel.alertMessage)
        }
        .sheet(isPresented: $showingProgressSheet) {
            ConversionProgressView(
                pythonBridge: viewModel.pythonBridge,
                isPresented: $showingProgressSheet
            )
        }
        .onDrop(of: [.fileURL], isTargeted: nil) { providers in
            handleDrop(providers: providers)
        }
    }

    private func handleDrop(providers: [NSItemProvider]) -> Bool {
        var urls: [URL] = []
        let group = DispatchGroup()

        for provider in providers {
            group.enter()
            provider.loadItem(forTypeIdentifier: "public.file-url", options: nil) { item, _ in
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

#Preview {
    ContentView()
        .frame(width: 1000, height: 600)
}
