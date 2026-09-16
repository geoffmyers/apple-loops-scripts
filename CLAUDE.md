# CLAUDE.md - Apple Loops Scripts Development Guide

## Project Overview

**Purpose**: Convert audio and MIDI files to Apple Loop CAF format with metadata and beat markers for use in Logic Pro, GarageBand, and Final Cut Pro.

**Target User**: Music producers creating sample libraries or converting existing samples/MIDI loops to Apple Loops format.

## Check claims about the format against Apple's own loops, not this document

Logic installs ~35,000 Apple Loops that Apple authored. They are the only
authority on what the format is, and `scripts/validate-against-apple-loops.py`
reads them. **Before changing anything about metadata values, thresholds or
what "correct" means, run it.**

```bash
python3 scripts/validate-against-apple-loops.py --mode all --limit 3000
```

Every hand-written assumption this repo held was wrong in a way that looked
right:

- The **vocabulary lists were curated by hand** and listed 13 genres where the
  library ships 30, marked five optional metadata fields as required, and
  invented values Apple has never used — `Hi-Hat` for Apple's `Hi-hat`,
  `Male Vocal` for Apple's `Male`, `Sound Effects`, `Textures`, `Other`. Worse,
  a pass that "corrected" the code against that list replaced three of Apple's
  *real* values with invented ones. `apple_loops_vocabulary.py` is now
  generated from `reference/apple-loops-shipped-vocabulary.json`, and
  `tests/test_apple_ground_truth.py` fails if they diverge.
- **`--verify` had a 12.6% false-positive rate** on Apple's own files. It
  required beat markers to span the file (Apple places them on transients; its
  last marker lands between 0.14x and 8.8x the audio length) and assumed
  40-300 BPM (Apple ships 21 to 360). Recalibrated from measured percentiles it
  is 0.12%. Use `--mode calibrate` before touching a threshold.
- A **valid category with a valid subcategory can still be a pair Apple never
  ships** — `Texture/Atmosphere` never carries `Ambience`, which lives under
  `Sound Effect`. `APPLE_CATEGORY_SUBCATEGORIES` holds the real pairings and
  both metadata paths are asserted against it.

Two maps produce categories, and only one was originally checked:
`MetadataExtractor.INSTRUMENT_MAP` **and** `PROGRAM_MAP` (General MIDI). The
unchecked one mapped GM programs 104-111 to `('World/Ethnic', 'Other')` —
a genre used as a category, and a subcategory Apple has never shipped. When a
vocabulary guard exists, grep for every table that should be behind it.

## Marker density is a property of the audio, not a quota

`TransientDetector` used to force at least one marker per beat, appending an
even grid over the detected onsets whenever detection found fewer. Both halves
of that were wrong, and the census showed it:

- **Apple ships 11.9% of its tempo-following loops with fewer markers than
  beats**, down to a single marker on a 16-beat sweep. Pads, risers and noise
  have no transients; Logic stretches them as one segment and that is correct.
- **Appending a grid over real onsets crowds them.** On a real Splice library
  it fired on 11% of files and left markers under 10 ms apart on 9% of them --
  one drum loop whose own onsets were 209 ms apart ended up with markers
  0.18 ms apart, an eight-sample stretch segment.

`min_markers_per_beat` now defaults to 0. The floor remains available via
`--min-markers-per-beat`, and when it does fire a grid point closer than the
detector's own `wait` (30 ms) to an existing marker is dropped -- the detected
transient always wins, because it is where the attack actually is.

Compare any converted directory against Apple with
`validate-against-apple-loops.py --compare-markers DIR`. After the change:

| | Apple | before | after |
|---|---|---|---|
| markers per beat (median) | 2.47 | 2.19 | 2.19 |
| loops sparser than 1 marker/beat | 11.9% | 0.0% | 11.1% |
| smallest gap, p5 | 25.7 ms | 1.7 ms | 21.3 ms |
| gaps under 10 ms | 0.03% | 0.57% | 0.00% |

## The sync: one output per source, or a sample disappears

`scripts/sync_splice_to_apple_loops.py` (behind the two `.command` launchers)
must be safe to run repeatedly. The converter suffixes a taken output name
(`Kick_01_2.caf`), which is right when two packs both hold a `Kick_01` and
catastrophic on a re-run: every sync would duplicate the library. So the sync
keeps a manifest keyed by absolute source path.

**Every bug it has had was the same one: two sources mapped to one `.caf`, so
one sample's audio silently overwrote another's.** Three variants, all found on
the real library and all now regression-tested in `tests/test_sync.py`:

1. A plain duplicate key — 4 of 1,812 outputs claimed twice.
2. **Adoption matched a name without claiming it.** Adoption exists because the
   output folder can already hold `.caf` from an earlier run; allocating around
   them would suffix every one and double the library. But two same-stem
   sources both adopted the same file.
3. **Case.** macOS treats `Foo.caf` and `foo.caf` as one file, and the
   allocator compared case-sensitively, so `DropChordsDRY.wav` and
   `DropchordsDRY.wav` were given two "distinct" names that are one file. One
   of 1,810 owned samples was simply absent.

Two rules fall out. **Assert uniqueness of the mapping, not of each allocation
step** — a `claimed` dict keyed case-folded, checked on every path including
adoption. And **do not ask the filesystem about case**: adoption resolves
against a case-folded listing of the output directory, so behaviour is
identical on Linux and macOS rather than inherited from the volume. Otherwise
the tests pass on the NAS and the bug lives on the Mac.

Each variant was found by arithmetic that would not close (managed + orphans
never equalled the file count), never by an error. **Reconcile counts against
the filesystem, not against your own bookkeeping** — the bookkeeping was
self-consistent and wrong. A later run repairs a manifest that already
double-booked, so the fix is retroactive.

## Bugs that only real libraries expose

Four defects survived a full unit suite and appeared the moment the tool met
real data. `tests/test_directory_walk.py` and `tests/test_duration_probe_warning.py`
are the regressions:

- A real Splice tree contains **directories whose names end in `.wav`**, and a
  glob matches them exactly like files.
- **An output directory nested inside the input directory** makes every run
  re-convert its own output, because `.caf` is a supported input. Observed on a
  real run: 6 files, then 12, then 24.
- **`afinfo` fails two different ways and only one raises.** Absent, it throws;
  on macOS with an unreadable file it exits non-zero with no exception. The
  second path returned None in silence, which after the "never invent a
  duration" change meant a file converting with no beat markers and no reason
  given.
- **numpy was imported at module level for a single type annotation**, making a
  large dependency mandatory to convert one MIDI file — and making the tool
  uninstallable on the stock macOS Python, which is where it runs.

## The two things that go silently wrong

Read these before changing anything in the conversion path. Both produce files
that open without error, decode cleanly, and are wrong.

**1. Beat count is tempo.** Apple Loops store no tempo field; Logic derives it
as `beat_count × 60 ÷ duration`. So a beat count off by one is a tempo off by
one beat, and a *fabricated duration* is a fabricated tempo. This is why
`resolve_duration()` returns `None` rather than a placeholder when the length
cannot be measured, and why `snap_beat_count()` only closes sub-beat gaps.
See APPLE_LOOPS_FORMAT.md § *The consequence: beat count is tempo*.

**2. A one-shot with beat markers gets time-stretched.** There is no one-shot
flag in the format — the state is the *absence* of a beat count and a beat
markers chunk. Put markers on a kick drum and Logic stretches the kick. When a
source is ambiguous, one-shot is the safe default: a loop that fails to follow
tempo is visible and fixable, a hit that changed shape is not. `sample_type`
in Splice's database is the authority when it is available.

Every decision about a file's kind and length routes through three methods, so
no code path can disagree with another:

| Method | Answers |
|---|---|
| `AppleLoopConverter.metadata_for()` | where does this file's metadata come from — Splice's database or the filename |
| `AppleLoopConverter.finalize_beat_count()` | how many beats is it |
| `AppleLoopConverter.wants_beat_markers()` | does it get a stretch grid |

The dry run and the real conversion both call all three. They used to compute
this inline in six places and had drifted apart.

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
│  Module functions:                                          │
│  - snap_beat_count / beats_per_bar - musical lengths       │
│  - splice_sample_to_metadata - Splice row -> Apple metadata│
└──────────┬──────────────────────────────┬──────────────────┘
           │                              │
┌──────────▼─────────────┐   ┌────────────▼──────────────────┐
│    splice_db.py        │   │  apple_loops_vocabulary.py    │
│  SpliceLibrary         │   │  APPLE_CATEGORIES / _GENRES / │
│  SpliceSample          │   │  _SUBCATEGORIES / _DESCRIPTORS│
│  parse_splice_key      │   │  (shared with the decoder)    │
└────────────────────────┘   └───────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────────┐
│                    External Tools                           │
│  afconvert (macOS audio) | librosa (transients) | mido     │
└────────────────────────────────────────────────────────────┘
```

## Tests

```bash
python3 -m pytest            # 233 tests, no macOS required
```

The suite runs anywhere: the MIDI conversion path is pure Python, so
`tests/test_roundtrip.py` converts, decodes and verifies real files end to end
without `afconvert`. `duration_probe` is injectable on `AppleLoopConverter`
precisely so the duration-fallback behaviour is testable off macOS — inject a
real function, don't mock.

Apple's category, genre, subcategory and descriptor lists are closed
vocabularies (`apple_loops_vocabulary.py`). A value outside them is not a
custom tag, it is a loop no Loop Browser filter will ever show — so the Splice
mapper is asserted against them and `decode_apple_loops.py --verify` checks
files on the way back out.

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

## Splice integration

`splice_db.py` reads the Splice desktop app's `sounds.db`. It deliberately
knows nothing about Apple Loops — it reports what Splice recorded, and
`splice_sample_to_metadata()` in the converter does the vocabulary mapping.

The `samples` table columns, as shipped:

```
id, local_path, attr_hash, dir, audio_key, bpm, chord_type, duration,
file_hash, sas_id, filename, genre, pack_uuid, sample_type, tags,
popularity, purchased_at, last_modified_at, waveform_url, provider_name
```

Traps found in that data:

- **`sample_type` is nullable**, and a NULL is common. It is read as *one-shot*,
  never as loop, because assuming loop is the direction that writes beat
  markers onto a single hit.
- **`audio_key` is sometimes a bare note** (`"C"`) with the scale in
  `chord_type`, and sometimes carries the scale itself (`"Cm"`, `"Bbm"`).
  `parse_splice_key()` prefers `chord_type`, falls back to the suffix, and
  reports `"both"` rather than guessing — key type only drives browser
  filtering, so an honest "either" costs nothing and a wrong guess mis-files.
- **`tags` is a comma-separated string, most specific first.** First tag that
  maps wins.
- **Splice records a key for plenty of drum hits.** Apple's format says drums
  and percussion carry none, so the mapper strips it.
- **Lookup is by basename, not `local_path`.** People re-organise their Splice
  folder and the path recorded at download time then points at nothing while
  the file sits there under a name that still matches.

### Mapping Splice's tags onto Apple's closed lists

Splice's tag vocabulary is open — 305 distinct tags in one real 1,725-sample
library. Apple's is closed. `scripts/splice-tag-coverage.py` measures the gap
against a real `sounds.db`, weighted by how often each tag occurs, so the
mapping is extended from data rather than imagination:

```bash
python3 scripts/splice-tag-coverage.py --splice-db ~/path/to/sounds.db
```

Current coverage on that library: **99.85%** of tag occurrences map, and
**99.25%** of samples get a real Apple category (up from 84%). Three things got
it there:

- **`Mixed` is the answer for a "songstarter".** 119 of the 274 originally
  unplaceable samples carry only genre tags plus `songstarters`, `music` or
  `melodic stack` — they are full arrangements, and Apple ships 767 loops under
  `Mixed` for exactly that.
- **The filename is a second source, not a rival.** Where Splice's tags name no
  instrument the filename usually does, so `splice_sample_to_metadata()` falls
  through to the filename extractor for the category only, while still taking
  tempo, key, genre and loop status from the database.
- **Some tags correctly map to nothing** — artist names (`kshmr`, `medasin`),
  eras (`80s`), workflow labels (`arrangement`). The coverage tool reports
  those separately from genuine gaps, so they do not look like work remaining.

Where Apple has no exact counterpart the mapping takes the nearest thing Apple
does ship and deliberately under-claims: `synthwave` becomes the broader
`Electronic` rather than `Electronic Pop`, because synthwave is usually
instrumental.

One-shots vastly outnumber loops in a real Splice library, which is why the
loop/one-shot decision matters more here than any other field.

## Known Limitations

1. **macOS Only for Audio**: Requires `afconvert` system utility for audio conversion
2. **MIDI Loops**: Pure Python, works on any platform
3. **Metadata Values**: Must use Apple's predefined category/genre values
4. **Beat Detection**: Quality depends on audio content
5. **MIDI Rendering**: No audio preview generation (requires external synthesizer)
6. **`--verify` is calibrated to Apple's shipped library**, so it will not
   flag things Apple itself does. Re-run `--mode verifier` after changing it.
7. **Splice `sounds.db` is read-only here** and may not be in the app's live
   location; `--splice-db` with no value searches the usual paths, otherwise
   export a copy (Splice → Settings → Download logs)

## macOS GUI Application (macos-app/)

A native macOS SwiftUI app that wraps the Python conversion script.

### Architecture

- **MVVM pattern**: Views → ViewModels → Models
- **PythonBridge** (`Services/PythonBridge.swift`): Spawns `convert_to_apple_loops.py` via `Process()`, handles progress reporting and output parsing
- **Script path resolution**: Checks bundled resources → user settings → DerivedData fallbacks → relative paths → system paths
- **Settings persistence**: `@AppStorage` (UserDefaults)

### Key Files

| File | Purpose |
|------|---------|
| `AppleLoopsConverterApp.swift` | App entry point and window management |
| `Services/PythonBridge.swift` | Python script integration (459 lines) |
| `ViewModels/ConverterViewModel.swift` | Main view model |
| `Models/LoopMetadata.swift` | Swift metadata model |
| `Scripts/build_distributable.sh` | Build distributable app package |
| `Scripts/bundle_python.sh` | Bundle Python runtime for standalone distribution |

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

> Tracked upstream in the mono repo this toolkit is developed in; the public
> `apple-loops-scripts` repo does not carry these as issues.

- [x] Splice `sounds.db` integration (`--splice-db`)
- [x] Loop vs one-shot handling
- [x] Validation against Apple's own shipped loops
- [x] Double-clickable incremental sync (`scripts/*.command`)
- [ ] Linux/Windows audio support via FFmpeg
- [ ] Audio preview generation for MIDI loops
- [ ] **`macos-app/` (SwiftUI) is out of date** — it shells out to
      `convert_to_apple_loops.py` without `--splice-db`, `--one-shot`/`--loop`
      or `--min-markers-per-beat`, so it still produces pre-fix output. Update
      it to drive `scripts/sync_splice_to_apple_loops.py`, or retire it in
      favour of the `.command` launchers
- [ ] Run the sync automatically (launchd agent watching `~/Splice/sounds`)
- [ ] Integration with DAW APIs
- [ ] Batch metadata editing
- [ ] MIDI quantization options
