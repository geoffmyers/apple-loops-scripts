import SwiftUI

struct MetadataEditorView: View {
    @ObservedObject var viewModel: ConverterViewModel

    var body: some View {
        VStack(spacing: 0) {
            headerView

            Divider()

            if viewModel.hasSelection {
                metadataForm
            } else {
                noSelectionView
            }
        }
        .background(Color(NSColor.controlBackgroundColor))
    }

    private var headerView: some View {
        HStack {
            Text("Metadata")
                .font(.headline)

            Spacer()

            if viewModel.hasSelection {
                Text("\(viewModel.selectedFiles.count) selected")
                    .foregroundColor(.secondary)
                    .font(.caption)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
    }

    private var noSelectionView: some View {
        VStack(spacing: 12) {
            Image(systemName: "tag")
                .font(.system(size: 32))
                .foregroundColor(.secondary)

            Text("Select files to edit metadata")
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var metadataForm: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                tempoSection
                keySection
                categorySection
                genreSection
                descriptorsSection
                timeSignatureSection

                Divider()

                applyButton
            }
            .padding()
        }
    }

    private var tempoSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Tempo (BPM)")
                .font(.caption)
                .foregroundColor(.secondary)

            HStack {
                TextField("Auto-detect", value: $viewModel.batchMetadata.tempo, format: .number)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 100)

                if viewModel.batchMetadata.tempo != nil {
                    Button(action: { viewModel.batchMetadata.tempo = nil }) {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)
                }

                Spacer()
            }
        }
    }

    private var keySection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Key")
                .font(.caption)
                .foregroundColor(.secondary)

            HStack(spacing: 8) {
                Picker("Key", selection: $viewModel.batchMetadata.key) {
                    Text("Auto-detect").tag(nil as KeySignature?)
                    ForEach(KeySignature.allCases) { key in
                        Text(key.rawValue).tag(key as KeySignature?)
                    }
                }
                .labelsHidden()
                .frame(width: 80)

                Picker("Type", selection: $viewModel.batchMetadata.keyType) {
                    ForEach(KeyType.allCases) { type in
                        Text(type.rawValue).tag(type)
                    }
                }
                .labelsHidden()
                .frame(width: 80)
                .disabled(viewModel.batchMetadata.key == nil)

                Spacer()
            }
        }
    }

    private var categorySection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Category")
                .font(.caption)
                .foregroundColor(.secondary)

            Picker("Category", selection: $viewModel.batchMetadata.category) {
                Text("Auto-detect").tag(nil as LoopCategory?)
                ForEach(LoopCategory.allCases) { category in
                    Text(category.rawValue).tag(category as LoopCategory?)
                }
            }
            .labelsHidden()

            TextField("Subcategory", text: $viewModel.batchMetadata.subcategory)
                .textFieldStyle(.roundedBorder)
        }
    }

    private var genreSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Genre")
                .font(.caption)
                .foregroundColor(.secondary)

            Picker("Genre", selection: $viewModel.batchMetadata.genre) {
                Text("Auto-detect").tag(nil as LoopGenre?)
                ForEach(LoopGenre.allCases) { genre in
                    Text(genre.rawValue).tag(genre as LoopGenre?)
                }
            }
            .labelsHidden()
        }
    }

    private var descriptorsSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Descriptors")
                .font(.caption)
                .foregroundColor(.secondary)

            FlowLayout(spacing: 4) {
                ForEach(LoopDescriptor.allCases) { descriptor in
                    DescriptorChip(
                        descriptor: descriptor,
                        isSelected: viewModel.batchMetadata.descriptors.contains(descriptor),
                        action: {
                            if viewModel.batchMetadata.descriptors.contains(descriptor) {
                                viewModel.batchMetadata.descriptors.remove(descriptor)
                            } else {
                                viewModel.batchMetadata.descriptors.insert(descriptor)
                            }
                        }
                    )
                }
            }
        }
    }

    private var timeSignatureSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Time Signature")
                .font(.caption)
                .foregroundColor(.secondary)

            Picker("Time Signature", selection: $viewModel.batchMetadata.timeSignature) {
                ForEach(TimeSignature.allCases) { sig in
                    Text(sig.rawValue).tag(sig)
                }
            }
            .labelsHidden()
            .frame(width: 100)
        }
    }

    private var applyButton: some View {
        Button(action: viewModel.applyBatchMetadata) {
            HStack {
                Image(systemName: "checkmark")
                Text("Apply to Selected (\(viewModel.selectedFiles.count))")
            }
            .frame(maxWidth: .infinity)
        }
        .buttonStyle(.borderedProminent)
        .disabled(!viewModel.hasSelection)
    }
}

struct DescriptorChip: View {
    let descriptor: LoopDescriptor
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(descriptor.rawValue)
                .font(.caption)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(isSelected ? Color.accentColor : Color.secondary.opacity(0.2))
                .foregroundColor(isSelected ? .white : .primary)
                .cornerRadius(12)
        }
        .buttonStyle(.plain)
    }
}

struct FlowLayout: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let result = FlowResult(
            in: proposal.replacingUnspecifiedDimensions().width,
            subviews: subviews,
            spacing: spacing
        )
        return result.size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let result = FlowResult(
            in: bounds.width,
            subviews: subviews,
            spacing: spacing
        )

        for (index, subview) in subviews.enumerated() {
            subview.place(at: CGPoint(
                x: bounds.minX + result.positions[index].x,
                y: bounds.minY + result.positions[index].y
            ), proposal: .unspecified)
        }
    }

    struct FlowResult {
        var size: CGSize = .zero
        var positions: [CGPoint] = []

        init(in maxWidth: CGFloat, subviews: Subviews, spacing: CGFloat) {
            var currentX: CGFloat = 0
            var currentY: CGFloat = 0
            var lineHeight: CGFloat = 0

            for subview in subviews {
                let size = subview.sizeThatFits(.unspecified)

                if currentX + size.width > maxWidth, currentX > 0 {
                    currentX = 0
                    currentY += lineHeight + spacing
                    lineHeight = 0
                }

                positions.append(CGPoint(x: currentX, y: currentY))
                lineHeight = max(lineHeight, size.height)
                currentX += size.width + spacing
                self.size.width = max(self.size.width, currentX)
            }

            self.size.height = currentY + lineHeight
        }
    }
}

#Preview {
    MetadataEditorView(viewModel: ConverterViewModel())
        .frame(width: 300, height: 600)
}
