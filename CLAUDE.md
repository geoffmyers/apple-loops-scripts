---
title: CLAUDE.md - Apple Loops Scripts Development Guide
created: 2026-01-28
modified: 2026-02-01
description: "Purpose: Convert audio and MIDI files to Apple Loop CAF format with metadata and beat markers for use in Logic Pro, GarageBand, and Final Cut Pro."
tags: [music, claude]
---

# CLAUDE.md - Apple Loops Scripts Development Guide

## Project Overview

**Purpose**: Convert audio and MIDI files to Apple Loop CAF format with metadata and beat markers for use in Logic Pro, GarageBand, and Final Cut Pro.

**Target User**: Music producers creating sample libraries or converting existing samples/MIDI loops to Apple Loops format.

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│              convert_to_apple_loops.py                      │
├────────────────────────────────────────────────────────────┤
│  Classes:                                                   │
│  - LoopMetadata (dataclass) - Apple Loop metadata          │
│  - MIDIInfo (dataclass) - Parsed MIDI information          │
│  - OnsetDetectionConfig - Transient detection settings     │
│  - TransientDetector - Audio onset detection via librosa   │
│  - MIDIParser - MIDI file parsing (mido or basic)          │
│  - MetadataExtractor - Extract metadata from filenames     │
│  - AppleLoopConverter - Main conversion orchestrator       │
│  - TablePrinter - Formatted console output                 │
└───────────────────────────┬────────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────────┐
│                    External Tools                           │
│  afconvert (macOS audio) | librosa (transients) | mido     │
└────────────────────────────────────────────────────────────┘
```

## Key Classes

### LoopMetadata

```python
@dataclass
class LoopMetadata:
    category: str = "Other Instrument"
    subcategory: str = "Other"
    genre: str = "Other Genre"
    beat_count: int = 0
    time_signature: str = "4/4"
    key_signature: str = ""  # Empty for drums/percussion
    key_type: str = ""  # major, minor, both, neither
    descriptors: str = ""
    tempo: Optional[int] = None
    duration: Optional[float] = None
    loop_type: str = "audio"  # "audio" or "midi"
```

### MIDIInfo

```python
@dataclass
class MIDIInfo:
    tempo: int = 120
    time_signature: Tuple[int, int] = (4, 4)
    key_signature: str = ""
    key_type: str = ""
    duration: float = 0.0
    beat_count: int = 0
    ticks_per_beat: int = 480
    num_tracks: int = 0
    num_notes: int = 0
    channels: Set[int] = field(default_factory=set)
    programs: Set[int] = field(default_factory=set)
    raw_data: bytes = b''
```

### MIDIParser

Parses MIDI files using mido (if available) or basic binary parsing:

```python
class MIDIParser:
    def parse_file(self, midi_path: Path) -> MIDIInfo:
        # Extracts tempo, time signature, key signature
        # from MIDI meta events
        # Falls back to basic parsing if mido not installed
```

### TransientDetector

Uses librosa for audio beat marker placement:

```python
class TransientDetector:
    def detect(self, audio_path: Path, beat_count: int,
               num_frames: int) -> List[int]:
        # Uses librosa.onset.onset_detect() for transient detection
        # Returns marker positions in samples
```

### MetadataExtractor

Parses filenames and MIDI content for metadata:

```python
class MetadataExtractor:
    # Extensive keyword maps
    INSTRUMENT_MAP = {
        'bass': ('Bass', 'Electric Bass'),
        'synth': ('Keyboards', 'Synthesizer'),
        # ... 70+ keywords
    }

    PROGRAM_MAP = {
        range(0, 8): ('Keyboards', 'Piano'),
        range(32, 40): ('Bass', 'Electric Bass'),
        # ... General MIDI program mapping
    }

    def extract_all(self, filename: str, filepath: str,
                    midi_info: Optional[MIDIInfo]) -> LoopMetadata:
        # Combines filename parsing and MIDI content analysis
```

### AppleLoopConverter

Main conversion orchestrator:

```python
class AppleLoopConverter:
    def convert_file(self, input_file: Path, output_file: Path) -> Optional[Path]:
        if self.is_midi_file(input_file):
            return self._convert_midi_file(...)  # Pure Python
        else:
            return self._convert_audio_file(...)  # Uses afconvert
```

## CAF File Structure

### Audio Loops
```
CAF Header (8 bytes)
├── 'caff' magic
└── version (1), flags (0)

Chunks:
├── desc - Audio description (sample rate, codec, channels)
├── pakt - Packet table (frame count for compressed audio)
├── data - Audio samples (ALAC or AAC encoded)
├── info - Spotlight metadata (genre)
├── uuid - Apple Loop metadata (29819273-...)
└── uuid - Beat markers (0352811b-...)
```

### MIDI Loops
```
CAF Header (8 bytes)
├── 'caff' magic
└── version (1), flags (0)

Chunks:
├── desc - Audio description (virtual format for MIDI)
├── midi - Standard MIDI File data
├── info - Spotlight metadata (genre)
├── uuid - Apple Loop metadata (29819273-...)
└── uuid - Beat markers (0352811b-...)
```

## UUID Chunk Formats

### Metadata UUID (29819273-b5bf-4aef-b78d-62d1ef90bb2c)

```
Structure:
- UUID (16 bytes)
- num_pairs (4 bytes, big-endian)
- key-value pairs (null-terminated strings)

Fields:
- category (string) - e.g., "Bass", "Drums", "Keyboards"
- subcategory (string) - e.g., "Electric Bass", "Drum Kit"
- genre (string) - e.g., "Electronic/Dance", "Hip Hop"
- beat count (string) - e.g., "8", "16"
- time signature (string) - e.g., "4/4", "3/4"
- key signature (string) - e.g., "A", "F#", "Bb"
- key type (string) - "major", "minor", "both", "neither"
- descriptors (string) - comma-separated, e.g., "Grooving,Clean"
- loop type (string) - "midi" (only for MIDI loops)
```

### Beat Markers UUID (0352811b-9d5d-42e1-882d-6af61a6b330c)

```
Structure:
- UUID (16 bytes)
- Header (20 bytes):
  - Unknown (4 bytes, always 0)
  - Flags (4 bytes, always 0x00010000)
  - Version (2 bytes, 0x0032)
  - Unknown (2 bytes, 0x0010)
  - Unknown (4 bytes, always 0)
  - Marker count (4 bytes, big-endian)
- Marker entries (12 bytes each):
  - Flags (2 bytes, 0x0001)
  - Padding (2 bytes)
  - Padding (4 bytes)
  - Sample position (4 bytes, big-endian)
```

## Development Guidelines

### Code Style

- Python 3.9+ with type hints
- Dataclasses for data models
- Argparse for CLI
- Comprehensive docstrings
- No external dependencies for core MIDI functionality

### Adding New Metadata Field

1. Add to `LoopMetadata` dataclass
2. Update `MetadataExtractor` if parseable
3. Add to `create_uuid_chunk()` method
4. Update `decode_apple_loops.py` reader
5. Update `APPLE_LOOPS_FORMAT.md`

### Adding Instrument/Genre Mappings

Edit the dictionaries in `MetadataExtractor`:
```python
INSTRUMENT_MAP = {
    'new_keyword': ('Category', 'Subcategory'),
}

GENRE_MAP = {
    'new_keyword': 'Genre Name',
}

PROGRAM_MAP = {
    range(start, end): ('Category', 'Subcategory'),
}
```

## Testing

```bash
# Test single audio conversion
./convert_to_apple_loops.py test.wav -o test.caf --verbose

# Test single MIDI conversion
./convert_to_apple_loops.py test.mid -o test_midi.caf

# Verify output
./decode_apple_loops.py test.caf --show-markers
./decode_apple_loops.py test_midi.caf --show-markers

# Test batch conversion
./convert_to_apple_loops.py /path/to/loops/ --dry-run

# Test in Logic Pro
# - Open output.caf in Logic
# - Verify tempo/key detection
# - Check beat marker alignment
# - For MIDI loops, verify Piano Roll editing works
```

## Transient Detection Tuning (Audio Only)

```bash
# Higher threshold = fewer markers
./convert_to_apple_loops.py input.wav --onset-threshold 0.5

# More markers per beat
./convert_to_apple_loops.py input.wav --min-markers-per-beat 2.0

# Disable for simple quarter-note markers
./convert_to_apple_loops.py input.wav --no-transient-detection
```

## Input Type Filtering

```bash
# Only audio files
./convert_to_apple_loops.py /path/to/loops/ --audio-only

# Only MIDI files
./convert_to_apple_loops.py /path/to/loops/ --midi-only

# Custom extensions
./convert_to_apple_loops.py /path/to/loops/ --extensions .wav,.mid
```

## Known Limitations

1. **macOS Only for Audio**: Requires `afconvert` system utility for audio conversion
2. **MIDI Loops**: Pure Python, works on any platform
3. **Metadata Values**: Must use Apple's predefined category/genre values
4. **Beat Detection**: Quality depends on audio content
5. **MIDI Rendering**: No audio preview generation (requires external synthesizer)

## Differences: Audio vs MIDI Conversion

| Feature | Audio | MIDI |
|---------|-------|------|
| External tools | afconvert | None (pure Python) |
| Platform | macOS only | Any |
| Beat markers | Transient detection | Beat-aligned |
| Encoding options | ALAC/AAC | N/A |
| File size | Depends on audio | Small (MIDI data only) |
| Metadata source | Filename only | Filename + MIDI content |

## Future Enhancement Ideas

> **GitHub Issue:** [#218 — Apple Loops Scripts: future enhancements](https://github.com/geoffmyers/apple-loops-scripts/issues)

- [ ] Linux/Windows audio support via FFmpeg
- [ ] Audio preview generation for MIDI loops
- [ ] GUI application (SwiftUI wrapper exists in macos-apps/)
- [ ] Integration with DAW APIs
- [ ] Batch metadata editing
- [ ] MIDI quantization options
