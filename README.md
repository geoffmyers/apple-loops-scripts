# Apple Loops Scripts

Python utilities for converting audio and MIDI files to Apple Loop CAF format and decoding Apple Loop metadata. These scripts create loops compatible with Logic Pro, GarageBand, and Final Cut Pro.

## Features

### convert_to_apple_loops.py
- **Audio + MIDI Support**: Convert both audio files and MIDI files to Apple Loops
- **Batch Conversion**: Convert multiple files in a directory
- **Auto Metadata**: Extract key, tempo, and category from filenames and MIDI content
- **Transient Detection**: Smart beat marker generation using librosa (audio only)
- **Encoding Options**: ALAC (lossless) or AAC (lossy) for audio
- **Real-Time Progress**: Table output for batch processing
- **Dry Run Mode**: Preview changes without converting

### decode_apple_loops.py
- **Metadata Reading**: Extract all embedded loop metadata
- **Format Support**: Modern CAF, legacy AIFF, and MIDI files
- **MIDI Analysis**: Display MIDI tracks, notes, programs
- **Beat Markers**: Display beat marker positions
- **JSON Export**: Machine-readable output

## Supported Input Formats

**Audio**: WAV, AIFF, MP3, M4A, AAC, FLAC, ALAC, CAF, OGG, WMA

**MIDI**: .mid, .midi, .smf

## Installation

### Prerequisites

- Python 3.9+
- macOS (required for `afconvert` - audio conversion only)

### Setup

```bash
git clone https://github.com/geoffmyers/apple-loops-scripts.git
cd apple-loops-scripts
pip install -r requirements.txt
```

### Dependencies

```
librosa>=0.10.0    # Audio analysis and transient detection
soundfile>=0.12.0  # Audio I/O
numpy>=1.20.0      # Numerical computing
mido>=1.2.0        # MIDI parsing (optional, enhances MIDI support)
```

## Usage

### Convert Audio Files

```bash
# Single file with auto-detected metadata
./convert_to_apple_loops.py input.wav

# With explicit metadata
./convert_to_apple_loops.py input.wav -o output.caf --tempo 120 --key Am

# Batch convert directory
./convert_to_apple_loops.py /path/to/loops/ --output-dir ~/Library/Audio/Apple\ Loops/User\ Loops/
```

### Convert MIDI Files

```bash
# Single MIDI file
./convert_to_apple_loops.py input.mid

# With metadata override
./convert_to_apple_loops.py input.mid --category Keyboards --genre "Electronic/Dance"

# Batch convert MIDI files only
./convert_to_apple_loops.py /path/to/midi/ --midi-only
```

### Mixed Audio + MIDI Conversion

```bash
# Convert all audio and MIDI files in a directory
./convert_to_apple_loops.py /path/to/loops/

# Convert only audio files
./convert_to_apple_loops.py /path/to/loops/ --audio-only
```

### With Metadata Override

```bash
./convert_to_apple_loops.py input.wav \
    --tempo 120 \
    --key "Am" \
    --category "Bass" \
    --genre "Electronic/Dance" \
    --descriptors "Funky,Groovy"
```

### Dry Run Preview

```bash
./convert_to_apple_loops.py /path/to/loops/ --dry-run
```

### Lossy Encoding (Audio Only)

```bash
./convert_to_apple_loops.py input.wav --lossy --bitrate 256000
```

### Decode Apple Loop

```bash
# Decode CAF file (audio or MIDI loop)
./decode_apple_loops.py loop.caf

# Decode MIDI file
./decode_apple_loops.py loop.mid

# JSON output
./decode_apple_loops.py loop.caf --json

# Show beat marker positions
./decode_apple_loops.py loop.caf --show-markers

# Batch decode directory
./decode_apple_loops.py /path/to/loops/ --recursive
```

## Command Line Options

### convert_to_apple_loops.py

| Option | Description |
|--------|-------------|
| `input` | Audio/MIDI file or directory |
| `-o, --output` | Output file path |
| `--output-dir` | Batch output directory |
| `--tempo` | BPM override |
| `--key` | Key signature (e.g., "Am", "F#") |
| `--category` | Instrument category |
| `--subcategory` | Specific instrument |
| `--genre` | Music genre |
| `--descriptors` | Comma-separated tags |
| `--time-signature` | Time signature (default: 4/4) |
| `--beat-count` | Beat count override |
| `--bitrate` | AAC bitrate (default: 256000) |
| `--lossy` | Use AAC instead of ALAC (audio only) |
| `--recursive` | Process subdirectories |
| `--no-recursive` | Disable recursion |
| `--preserve-structure` | Maintain directory structure |
| `--audio-only` | Only process audio files |
| `--midi-only` | Only process MIDI files |
| `--table, -t` | Force table output |
| `--detailed, -d` | Force detailed output |
| `--dry-run` | Preview without converting |
| `-v, --verbose` | Verbose output |
| `--no-transient-detection` | Simple quarter-note markers |
| `--onset-threshold` | Detection threshold (0.0-1.0) |
| `--min-markers-per-beat` | Min markers per beat |

### decode_apple_loops.py

| Option | Description |
|--------|-------------|
| `input` | File or directory to decode |
| `--json, -j` | JSON output format |
| `--table, -t` | Force table output |
| `--detailed, -d` | Force detailed output |
| `--recursive, -r` | Process directories recursively |
| `--show-markers, -m` | Display beat marker positions |
| `-v, --verbose` | Verbose output |

## Output Formats

### Audio Loops
- CAF container with ALAC (default) or AAC audio
- Apple Loop metadata UUID chunk
- Beat markers UUID chunk (transient-detected or quarter-note)
- Info chunk with genre for Spotlight

### MIDI Loops
- CAF container with embedded MIDI data ('midi' chunk)
- Apple Loop metadata UUID chunk
- Beat markers UUID chunk (beat-aligned)
- Info chunk with genre for Spotlight

## Apple Loops Format

See `APPLE_LOOPS_FORMAT.md` for detailed specification including:
- CAF file structure
- Metadata UUID chunks
- Beat marker generation
- Valid metadata values

## Metadata Extraction

The converter automatically extracts metadata from:

### Filename Patterns
- `_120bpm` or `120BPM` → Tempo: 120
- `_Am` or `A_minor` → Key: Am
- Instrument keywords (`bass`, `synth`, `drum`) → Category
- Genre keywords (`funk`, `jazz`, `electronic`) → Genre

### MIDI Content
- Set Tempo events → BPM
- Key Signature events → Key
- Time Signature events → Time signature
- Program Change events → Instrument category

## Project Structure

```
apple-loops-scripts/
├── convert_to_apple_loops.py   # Unified converter (audio + MIDI)
├── decode_apple_loops.py       # Unified decoder (all formats)
├── APPLE_LOOPS_FORMAT.md       # Format specification
├── CLAUDE.md                   # Development guide
├── README.md                   # This file
├── requirements.txt            # Python dependencies
└── macos-app/                  # macOS SwiftUI GUI application
    ├── AppleLoopsConverter.xcodeproj
    ├── AppleLoopsConverter/
    │   ├── Models/             # Data models (AudioFile, ConversionSettings, LoopMetadata)
    │   ├── ViewModels/         # State management and Python bridge
    │   ├── Views/              # SwiftUI views
    │   └── Services/           # Service layer (PythonBridge)
    └── Scripts/                # Build and packaging scripts
```

## macOS GUI Application

The `macos-app/` directory contains a native macOS SwiftUI application that provides a graphical interface for the conversion scripts.

### Requirements

- macOS 12.0+
- Xcode 14+
- Python 3.9+ (for the conversion backend)

### Usage

```bash
# Open in Xcode
open macos-app/AppleLoopsConverter.xcodeproj

# Build from command line
xcodebuild -project macos-app/AppleLoopsConverter.xcodeproj -scheme AppleLoopsConverter build
```

The app calls `convert_to_apple_loops.py` via `Process()`. Configure the Python script path in the app's Settings panel, or the app will attempt to auto-detect it.

## License

This project is licensed under the GNU General Public License v2.0 - see the [LICENSE.md](LICENSE.md) file for details.
