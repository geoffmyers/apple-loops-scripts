# Apple Loops Metadata Format Analysis

This document provides a comprehensive reverse-engineered specification of the Apple Loops metadata format used by Logic Pro, GarageBand, and other Apple audio applications.

## Overview

Apple Loops are audio files with embedded metadata that enables:
- Automatic tempo and key matching
- Categorization in the Loop Browser
- Spotlight indexing for search
- Time-stretching and pitch-shifting

## File Format: CAF (Core Audio Format)

Apple Loops are stored as **`.caf` files**, not AIFF files. While Logic Pro can read older AIFF-based loops, modern Apple Loops use CAF.

### CAF File Header

| Offset | Size | Description |
|--------|------|-------------|
| 0 | 4 | Magic: `caff` |
| 4 | 2 | Version: `1` (big-endian) |
| 6 | 2 | Flags: `0` (big-endian) |

### CAF Chunk Structure

Each chunk follows this format:

| Offset | Size | Description |
|--------|------|-------------|
| 0 | 4 | Chunk type (4 ASCII characters) |
| 4 | 8 | Chunk size (big-endian 64-bit) |
| 12 | N | Chunk data |

## Chunks in Apple Loop CAF Files

| Chunk | Required | Purpose |
|-------|----------|---------|
| `desc` | Yes | Audio description (sample rate, format, channels) |
| `kuki` | For AAC | Codec-specific "cookie" data |
| `pakt` | For AAC | Packet table (frame counts for duration calculation) |
| `chan` | No | Channel layout |
| `free` | No | Padding/alignment (can be any size) |
| `data` | Yes | Audio data |
| `info` | No | CAF info with genre. Only 21.6% of Apple's own loops carry one |
| `uuid` | Yes* | Apple Loop metadata (the critical chunk) |
| `ovvw` | No | Waveform overview for display |

The `uuid` metadata chunk is what makes a CAF an Apple Loop rather than plain
audio, and the beat markers `uuid` chunk is what lets Logic follow tempo.
Neither is required for the file to open.

## How this document was checked

Everything below about *values* — categories, subcategories, genres,
descriptors, key types, time signatures, plausible tempo ranges, marker
placement — is a census of the **34,928 Apple Loops shipped with Logic on
macOS 27**, produced by `scripts/validate-against-apple-loops.py`. The raw
counts live in `reference/apple-loops-shipped-vocabulary.json`.

This matters because the hand-written version of those sections was wrong in
both directions: it listed 13 genres where Apple ships 30, marked five optional
fields as required, and invented subcategories Apple has never used (`Hi-Hat`
for Apple's `Hi-hat`, `Male Vocal` for Apple's `Male`). Reasoning about the
format from a document rather than from the files produced confident errors.

---

## The Apple Loop Metadata UUID Chunk

The primary metadata is stored in a `uuid` chunk with a specific identifier.

### UUID Identifier

```
29819273-b5bf-4aef-b78d-62d1ef90bb2c
```

As raw bytes (16 bytes):
```
29 81 92 73 b5 bf 4a ef b7 8d 62 d1 ef 90 bb 2c
```

### Chunk Structure

```
+------------------+
| 'uuid' (4 bytes) |  Chunk type identifier
+------------------+
| Size (8 bytes)   |  Big-endian 64-bit chunk size
+------------------+
| UUID (16 bytes)  |  29819273-b5bf-4aef-b78d-62d1ef90bb2c
+------------------+
| Count (4 bytes)  |  Big-endian 32-bit pair count
+------------------+
| Key\0Value\0...  |  Null-terminated string pairs
+------------------+
```

### Metadata Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `category` | String | No | Main instrument category |
| `subcategory` | String | No | Specific instrument type — an empty value is common |
| `genre` | String | No | Music genre |
| `beat count` | String (int) | Loops only | Number of beats; absent on one-shots |
| `time signature` | String | No | Time signature (e.g., "4/4") |
| `key signature` | String | No* | Root note of the loop |
| `key type` | String | No* | Scale type |
| `descriptors` | String | No | Comma-separated descriptor tags |

*Omitted for drums and percussion.

**None of these are strictly required.** Apple ships 148 loops of its own with
no metadata chunk at all, and 2,616 with no time signature. What a field's
presence changes is whether the loop can be found in the Loop Browser and
whether it follows tempo — not whether the file is valid. An earlier version of
this table marked five of these "Yes" on no evidence.

### Example Hex Dump

```
75 75 69 64                                     uuid
00 00 00 00 00 00 00 83                         chunk size (131 bytes)
29 81 92 73 b5 bf 4a ef b7 8d 62 d1 ef 90 bb 2c UUID
00 00 00 08                                     8 key-value pairs
73 75 62 63 61 74 65 67 6f 72 79 00             subcategory\0
45 6c 65 63 74 72 69 63 20 42 61 73 73 00       Electric Bass\0
63 61 74 65 67 6f 72 79 00                      category\0
42 61 73 73 00                                  Bass\0
...
```

---

## Tempo Calculation

**Tempo is NOT stored explicitly.** It is derived from:

```
tempo_bpm = (beat_count × 60) / duration_seconds
```

### Examples

| Beat Count | Duration | Calculated Tempo |
|------------|----------|------------------|
| 8 | 4.0 sec | 120 BPM |
| 16 | 8.0 sec | 120 BPM |
| 8 | 3.2 sec | 150 BPM |
| 4 | 2.0 sec | 120 BPM |
| 8 | 5.0 sec | 96 BPM |

### The consequence: beat count *is* tempo

Because tempo is derived rather than stored, **an off-by-one beat count is an
off-by-one-beat tempo error**, and both halves of the division have to be right:

- A **wrong beat count** misreports the tempo. A 4-bar loop at 128 BPM runs
  7.50s; add a 0.35s decay tail and the arithmetic gives 16.75 beats. Rounding
  that to 17 tells Logic the file is 128 → 122 BPM, and every copy in the
  project plays 4% slow.
- A **wrong duration** does the same. Never substitute a placeholder length
  when the real one cannot be measured — write the file with no beat count and
  no markers instead. A loop that does not follow tempo is recoverable; a loop
  that follows the wrong tempo is silently wrong.

`convert_to_apple_loops.py` therefore snaps derived beat counts onto musical
bar lengths, but only across sub-beat gaps (`BEAT_COUNT_SNAP_MAX_BEATS`, 0.75
of a beat). That is the size of a tail or a trim that missed the zero crossing.
A file sitting two whole beats from a bar line is genuinely an odd length and
keeps it — snapping there would shift the tempo by 7% in the other direction.

---

## Loops vs One-Shots

There is **no one-shot flag** in the metadata chunk. The distinction is
expressed by omission, and Logic reads it from two things:

| | Beat count | Beat markers chunk | Behaviour in Logic |
|---|---|---|---|
| **Loop** | present, > 0 | present | Time-stretches to project tempo; transposes to project key if a key signature is set |
| **One-shot** | absent | absent | Plays at its recorded length and pitch |

A one-shot may still carry category, subcategory, genre, descriptors and even a
key signature — those only drive Loop Browser filtering and cost nothing.

**Getting this wrong in the damaging direction is silent.** Write a beat
markers chunk onto a kick drum and Logic will happily stretch that kick to fit
the project tempo. When a source is ambiguous, one-shot is the safe default:
the worst case is a loop that does not follow tempo, which is visible and
fixable, rather than a hit that quietly changes shape.

### Getting Duration

Duration is calculated from the `pakt` chunk:

```python
# pakt chunk structure (first 24 bytes)
num_packets = struct.unpack('>q', pakt_data[0:8])[0]
num_valid_frames = struct.unpack('>q', pakt_data[8:16])[0]
priming_frames = struct.unpack('>i', pakt_data[16:20])[0]
remainder_frames = struct.unpack('>i', pakt_data[20:24])[0]

duration_seconds = num_valid_frames / sample_rate
```

---

## The `info` Chunk (Spotlight Integration)

For Spotlight to index the loop's genre and make it searchable, the `info`
chunk must contain the genre.

**It is optional.** Only 7,535 of Apple's 34,928 shipped loops (21.6%) carry a
genre here, so its absence is normal. `decode_apple_loops.py --verify` used to
warn about it and fired against 78% of Apple's own library; the check is gone.

### Structure

```
+------------------+
| 'info' (4 bytes) |
+------------------+
| Size (8 bytes)   |
+------------------+
| Count (4 bytes)  |  Number of entries (big-endian 32-bit)
+------------------+
| Key\0Value\0...  |  Null-terminated key-value pairs
+------------------+
```

### Example

```
info chunk for genre "Funk":

00 00 00 01                 1 entry
67 65 6e 72 65 00           genre\0
46 75 6e 6b 00              Funk\0
```

### Spotlight Attributes

When properly formatted, Spotlight will index:
- `kMDItemMusicalGenre` - The genre value from the info chunk

---

## Valid Metadata Values

**These are a census, not a guess.** Every table below is the complete set of
values present across the 34,928 Apple Loops shipped with Logic on macOS 27,
counted by `scripts/validate-against-apple-loops.py --mode vocabulary`. An
earlier hand-written version of this section was wrong in both directions: it
listed 13 genres where Apple ships 30, and it invented subcategories Apple has
never used (`Hi-Hat` for Apple's `Hi-hat`, `Male Vocal` for Apple's `Male`).

The Loop Browser filters on exact strings, so a value outside these lists is
not a custom tag: it is a loop no filter will ever show.

### Categories

| Value | Loops |
|---|---|
| `Drums` | 7,967 |
| `Keyboards` | 7,029 |
| `Bass` | 4,118 |
| `Guitars` | 2,701 |
| `Vocals` | 2,644 |
| `Sound Effect` | 2,602 |
| `Strings` | 2,078 |
| `Percussion` | 1,726 |
| `Horn/Wind` | 1,409 |
| `Mixed` | 767 |
| `Mallets` | 460 |
| `Texture/Atmosphere` | 233 |
| `Other Instrument` | 76 |
| `Woodwind` | 36 |
| `Brass` | 11 |
| `Other` | 2 |

### Subcategories

| Value | Loops |
|---|---|
| `Synthesizer` | 4,551 |
| `Electronic Beats` | 4,491 |
| `Drum Kit` | 2,532 |
| `Synthetic Bass` | 2,279 |
| `Electric Guitar` | 1,727 |
| `Electric Bass` | 1,445 |
| `Male` | 1,349 |
| `Piano` | 1,078 |
| `Female` | 1,011 |
| `Acoustic Guitar` | 633 |
| `Electric Piano` | 628 |
| `Motions & Transitions` | 511 |
| `Violin` | 436 |
| `Acoustic Bass` | 377 |
| `Hi-hat` | 371 |
| `Organ` | 357 |
| `Impacts & Crashes` | 279 |
| `Flute` | 263 |
| `Transportation` | 244 |
| `Harp` | 239 |
| `Shaker` | 230 |
| `Work/Home` | 206 |
| `Kick` | 196 |
| `Choir` | 195 |
| `Snare` | 193 |
| `Misc.` | 191 |
| `Animals` | 178 |
| `Foley` | 177 |
| `Ambience` | 167 |
| `Trumpet` | 158 |
| `Conga` | 157 |
| `Harpsichord` | 143 |
| `Other Instrument` | 130 |
| `Clarinet` | 128 |
| `People` | 127 |
| `Tambourine` | 125 |
| `Sci-Fi` | 123 |
| `Cello` | 122 |
| `Clavinet` | 108 |
| `Saxophone` | 107 |
| `French Horn` | 106 |
| `Viola` | 104 |
| `Bell` | 98 |
| `Bongo` | 91 |
| `Accordion` | 85 |
| `Double Bass` | 83 |
| `Cymbal` | 82 |
| `Cowbell` | 81 |
| `Mech/Tech` | 79 |
| `Slide Guitar` | 78 |
| `Sitar` | 75 |
| `Vibraphone` | 69 |
| `Sports & Leisure` | 69 |
| `Mandolin` | 68 |
| `Timpani` | 63 |
| `Oboe` | 61 |
| `Marimba` | 59 |
| `Trombone` | 58 |
| `Banjo` | 58 |
| `Kalimba` | 48 |
| `Clave` | 43 |
| `Weapons` | 43 |
| `Tuba` | 40 |
| `Steel Drum` | 37 |
| `Xylophone` | 37 |
| `Vinyl/Scratch` | 35 |
| `Harmonica` | 32 |
| `Tom` | 30 |
| `Recorder` | 30 |
| `Bassoon` | 28 |
| `Koto` | 25 |
| `Chime` | 20 |
| `Celesta` | 14 |
| `Piccolo` | 13 |
| `Rattler` | 12 |
| `Pan Flute` | 12 |
| `Gong` | 12 |
| `English Horn` | 11 |
| `Bagpipe` | 11 |
| `Explosions` | 10 |
| `Pedal Steel Guitar` | 9 |
| `Shaker2` | 1 |


An **empty subcategory is legitimate** and common. `Other` is a *category*,
never a subcategory; the value to write when nothing more specific is known is
`Other Instrument`.

### Category / subcategory pairings

A valid category with a valid subcategory can still be a pair Apple never
ships, which is just as invisible. `Texture/Atmosphere` never carries
`Ambience` — that lives under `Sound Effect`.

| Category | Subcategories Apple pairs with it |
|---|---|
| `Bass` | `Acoustic Bass`, `Electric Bass`, `Synthetic Bass` |
| `Brass` | `Trombone`, `Trumpet`, `Tuba` |
| `Drums` | `Cymbal`, `Drum Kit`, `Electronic Beats`, `Hi-hat`, `Kick`, `Snare`, `Tom` |
| `Guitars` | `Acoustic Guitar`, `Banjo`, `Electric Guitar`, `Mandolin`, `Other Instrument`, `Pedal Steel Guitar`, `Slide Guitar` |
| `Horn/Wind` | `Bagpipe`, `Bassoon`, `Clarinet`, `English Horn`, `Flute`, `French Horn`, `Harmonica`, `Oboe`, `Other Instrument`, `Pan Flute`, `Piccolo`, `Recorder`, `Saxophone`, `Trombone`, `Trumpet`, `Tuba` |
| `Keyboards` | `Accordion`, `Celesta`, `Clavinet`, `Electric Piano`, `Harpsichord`, `Organ`, `Piano`, `Synthesizer` |
| `Mallets` | `Bell`, `Kalimba`, `Marimba`, `Steel Drum`, `Synthesizer`, `Timpani`, `Vibraphone`, `Xylophone` |
| `Mixed` | `Other Instrument` |
| `Other` | (none) |
| `Other Instrument` | `Other Instrument`, `Synthesizer` |
| `Percussion` | `Bongo`, `Chime`, `Clave`, `Conga`, `Cowbell`, `Electronic Beats`, `Gong`, `Kick`, `Other Instrument`, `Rattler`, `Shaker`, `Shaker2`, `Synthesizer`, `Tambourine`, `Vinyl/Scratch` |
| `Sound Effect` | `Ambience`, `Animals`, `Explosions`, `Foley`, `Impacts & Crashes`, `Mech/Tech`, `Misc.`, `Motions & Transitions`, `Other Instrument`, `People`, `Sci-Fi`, `Sports & Leisure`, `Synthesizer`, `Transportation`, `Weapons`, `Work/Home` |
| `Strings` | `Cello`, `Double Bass`, `Harp`, `Koto`, `Other Instrument`, `Sitar`, `Viola`, `Violin` |
| `Texture/Atmosphere` | `Electric Piano`, `Other Instrument`, `Synthesizer` |
| `Vocals` | `Choir`, `Female`, `Male`, `Other Instrument` |
| `Woodwind` | `Clarinet`, `Flute`, `Pan Flute`, `Recorder`, `Saxophone` |

### Genres

| Value | Loops |
|---|---|
| `Rock/Blues` | 4,231 |
| `Electronic/Dance` | 4,221 |
| `World/Ethnic` | 3,850 |
| `Hip Hop` | 3,110 |
| `Orchestral` | 2,371 |
| `Cinematic/New Age` | 1,548 |
| `Modern RnB` | 1,209 |
| `Urban` | 1,193 |
| `Electro House` | 1,125 |
| `Hip Hop/RnB` | 977 |
| `Funk` | 902 |
| `Techno` | 754 |
| `Dubstep` | 752 |
| `Indie` | 737 |
| `Sound_Effects` | 700 |
| `Other Genre` | 619 |
| `Tech House` | 603 |
| `House` | 541 |
| `Jazz` | 514 |
| `Chillwave` | 468 |
| `Country/Folk` | 458 |
| `Electronic Pop` | 397 |
| `Deep House` | 358 |
| `Future Bass` | 335 |
| `Bass House` | 311 |
| `Reggaeton Pop` | 301 |
| `Chinese Traditional` | 300 |
| `Experimental` | 179 |
| `Vintage Breaks` | 101 |
| `Electronic` | 22 |


Note how specific these are. Apple ships `Deep House`, `Tech House` and
`Techno` separately rather than under one `Electronic/Dance` umbrella, so a
source genre usually maps straight across instead of being collapsed.

### Descriptors

| Value | Loops |
|---|---|
| `Grooving` | 20,484 |
| `Part` | 18,453 |
| `Single` | 16,502 |
| `Melodic` | 14,896 |
| `Clean` | 14,622 |
| `Electric` | 14,446 |
| `Processed` | 12,636 |
| `Acoustic` | 12,615 |
| `Dry` | 11,137 |
| `Relaxed` | 7,317 |
| `Cheerful` | 6,786 |
| `Intense` | 6,717 |
| `Ensemble` | 4,930 |
| `Dark` | 4,389 |
| `Distorted` | 2,720 |
| `Fill` | 925 |
| `Arrhythmic` | 729 |
| `Dissonant` | 535 |


### Key Signatures

All 12 chromatic notes with enharmonic equivalents:

```
A, A#, Ab, B, Bb, C, D, Db, E, Eb, F, F#, G, G#, Gb
```

For drums and percussion the key signature is omitted or empty — though Apple
itself breaks this on 124 of its own loops.

### Key Types

| Key Type | Loops | Meaning |
|---|---|---|
| *(empty)* | 13,809 | No key stated — the most common case of all |
| `minor` | 12,540 | Minor scale |
| `major` | 6,010 | Major scale |
| `both` | 1,562 | Works with either |
| `neither` | 882 | Atonal / percussive |
| `any` | 125 | Undocumented, but shipped |

### Time Signatures

`4/4` (31,771), *empty* (2,616), `3/4` (450), `5/4` (38), `6/8` (34),
`7/8` (11), `4/8` (5), `2/4` (1), `7/4` (1), `13/8` (1).

### Codecs

Apple ships AAC for 33,879 loops and Apple Lossless for 1,049. Either is a
valid Apple Loop; this toolkit defaults to ALAC so nothing is re-compressed.

---

## Beat Markers UUID Chunk (Required for Tempo Sync)

A second UUID chunk contains beat marker data for time-stretching. **This chunk is essential** for Logic Pro and GarageBand to automatically adjust the loop's tempo to match the project tempo.

### UUID Identifier

```
0352811b-9d5d-42e1-882d-6af61a6b330c
```

As raw bytes (16 bytes):
```
03 52 81 1b 9d 5d 42 e1 88 2d 6a f6 1a 6b 33 0c
```

### Chunk Structure

```
+------------------+
| 'uuid' (4 bytes) |  Chunk type identifier
+------------------+
| Size (8 bytes)   |  Big-endian 64-bit chunk size
+------------------+
| UUID (16 bytes)  |  0352811b-9d5d-42e1-882d-6af61a6b330c
+------------------+
| Header (20 bytes)|  Beat marker header
+------------------+
| Entries (12×N)   |  Beat marker entries
+------------------+
```

### Header Structure (20 bytes)

| Offset | Size | Description |
|--------|------|-------------|
| 0 | 4 | Unknown (always 0x00000000) |
| 4 | 4 | Flags (always 0x00010000) |
| 8 | 2 | Version? (always 0x0032 = 50) |
| 10 | 2 | Unknown (always 0x0010 = 16) |
| 12 | 4 | Unknown (always 0x00000000) |
| 16 | 4 | Marker count (big-endian 32-bit) |

### Marker Entry Structure (12 bytes each)

| Offset | Size | Description |
|--------|------|-------------|
| 0 | 2 | Flags (always 0x0001) |
| 2 | 2 | Padding (0x0000) |
| 4 | 4 | Padding (0x00000000) |
| 8 | 4 | Sample position (big-endian 32-bit) |

### Marker Positions

Markers define sample positions where the audio can be "stretched" during tempo changes. For basic tempo sync, you need at minimum:
- A marker at sample position 0 (start)
- A marker at the total sample count (end)
- Ideally, markers at each beat or beat subdivision

**Example for an 8-beat loop at 44100 Hz, 120 BPM (4 seconds = 176400 samples):**

With quarter-note subdivisions (4 markers per beat = 33 markers total):
```
Marker 0:  position 0      (beat 0.00)
Marker 1:  position 5512   (beat 0.25)
Marker 2:  position 11025  (beat 0.50)
Marker 3:  position 16537  (beat 0.75)
Marker 4:  position 22050  (beat 1.00)
...
Marker 32: position 176400 (beat 8.00 - end)
```

### Python Implementation

```python
BEAT_MARKERS_UUID = bytes.fromhex('0352811b9d5d42e1882d6af61a6b330c')

def create_beat_markers_chunk(num_valid_frames: int, beat_count: int,
                               subdivisions: int = 4) -> bytes:
    """
    Create Apple Loop beat markers UUID chunk.

    Args:
        num_valid_frames: Total number of audio sample frames
        beat_count: Number of beats in the loop
        subdivisions: Markers per beat (1=beats only, 4=quarter notes)

    Returns:
        Complete beat markers chunk data (including UUID)
    """
    data = bytearray(BEAT_MARKERS_UUID)

    # Header
    header = struct.pack('>I', 0)           # Unknown
    header += struct.pack('>I', 0x00010000) # Flags
    header += struct.pack('>H', 0x0032)     # Version?
    header += struct.pack('>H', 0x0010)     # Unknown
    header += struct.pack('>I', 0)          # Unknown

    # Calculate marker positions
    samples_per_beat = num_valid_frames / beat_count
    samples_per_subdivision = samples_per_beat / subdivisions

    # Generate markers (including start and end)
    total_markers = beat_count * subdivisions + 1
    marker_positions = []

    for i in range(total_markers):
        position = int(round(i * samples_per_subdivision))
        position = min(position, num_valid_frames)
        marker_positions.append(position)

    marker_positions[-1] = num_valid_frames  # Ensure exact end

    # Write marker count
    header += struct.pack('>I', len(marker_positions))
    data.extend(header)

    # Write marker entries
    for position in marker_positions:
        entry = struct.pack('>H', 0x0001)   # flags
        entry += struct.pack('>H', 0x0000)  # padding
        entry += struct.pack('>I', 0x0000)  # padding
        entry += struct.pack('>I', position)  # sample position
        data.extend(entry)

    return bytes(data)
```

### Note on Transient Detection

Official Apple Loops often have more sophisticated marker placement based on transient detection (audio peaks, drum hits, etc.). The simple evenly-spaced approach works for basic tempo sync, but may not produce as clean results with complex audio material.

---

## Transient Detection for Beat Markers

The `convert_to_apple_loops.py` script supports automatic transient detection using [librosa](https://librosa.org/) to generate beat markers that align with actual audio events (drum hits, note attacks, etc.) rather than simple evenly-spaced positions.

### Benefits of Transient-Based Markers

- **Cleaner time-stretching**: Markers at actual transients allow Logic Pro to preserve attacks during tempo changes
- **Better loop quality**: Reduces artifacts and "smearing" when loops are stretched
- **Matches official Apple Loops**: Apple's loops use similar transient-based placement

### Requirements

Install the optional dependencies for transient detection:

```bash
pip install -r requirements.txt
# Or individually:
pip install librosa soundfile numpy
```

### Usage

Transient detection is **enabled by default** when librosa is available. The converter will automatically:
1. Detect audio transients using librosa's onset detection
2. Use detected transients as beat marker positions
3. Fall back to evenly-spaced markers if too few transients are found

### CLI Options

```bash
# Disable transient detection (use simple quarter-note markers)
./convert_to_apple_loops.py input.wav --no-transient-detection

# Adjust onset detection sensitivity (0.0-1.0, higher = fewer markers)
./convert_to_apple_loops.py input.wav --onset-threshold 0.5

# Set minimum markers per beat (fallback if detection finds fewer)
./convert_to_apple_loops.py input.wav --min-markers-per-beat 2.0

# Adjust hop length for onset detection STFT
./convert_to_apple_loops.py input.wav --onset-hop-length 256
```

### Fallback Behavior

If librosa is not installed or transient detection fails, the converter automatically falls back to evenly-spaced markers:

```
Transient detection unavailable: librosa is required...
Using evenly-spaced markers (install librosa for better results)
```

### How It Works

The `TransientDetector` class uses librosa's onset detection:

```python
# Load audio and detect onsets
y, sr = librosa.load(audio_path, sr=None, mono=True)
onset_frames = librosa.onset.onset_detect(
    y=y, sr=sr,
    hop_length=512,
    backtrack=True,  # Roll back to local energy minimum
    units='frames'
)

# Convert frames to sample positions
onset_samples = librosa.frames_to_samples(onset_frames, hop_length=512)
```

The detected transients are then used as beat marker positions, with additional markers added if the minimum count isn't met.

---

## Conversion Process

### Step 1: Convert to CAF

Use Apple's `afconvert` tool:

```bash
# Lossless ALAC (recommended - preserves full audio quality)
afconvert -f caff -d alac input.wav output.caf

# Lossy AAC (smaller files, some quality loss)
afconvert -f caff -d aac -b 256000 -q 127 input.wav output.caf
```

Options:
- `-f caff` - Output format: CAF
- `-d alac` - Data format: ALAC (Apple Lossless Audio Codec, recommended)
- `-d aac` - Data format: AAC (lossy compression)
- `-b 256000` - Bitrate: 256 kbps (AAC only)
- `-q 127` - Quality: Maximum (AAC only)

### Step 2: Replace/Add `info` Chunk

Replace the auto-generated `info` chunk with one containing the genre:

```python
def create_info_chunk(genre):
    data = struct.pack('>I', 1)  # 1 entry
    data += b'genre\x00' + genre.encode('ascii') + b'\x00'
    return data
```

### Step 3: Add Apple Loop UUID Chunk

Insert the metadata UUID chunk after the `data` chunk:

```python
APPLE_LOOP_META_UUID = bytes.fromhex('29819273b5bf4aefb78d62d1ef90bb2c')

def create_apple_loop_uuid_chunk(metadata):
    data = bytearray(APPLE_LOOP_META_UUID)

    kv_pairs = [
        ('subcategory', metadata['subcategory']),
        ('category', metadata['category']),
        ('key signature', metadata.get('key_signature', '')),
        ('time signature', metadata['time_signature']),
        ('beat count', str(metadata['beat_count'])),
        ('descriptors', metadata['descriptors']),
        ('genre', metadata['genre']),
        ('key type', metadata.get('key_type', '')),
    ]

    # Filter out empty values
    kv_pairs = [(k, v) for k, v in kv_pairs if v]

    data.extend(struct.pack('>I', len(kv_pairs)))
    for key, value in kv_pairs:
        data.extend(key.encode('ascii') + b'\x00')
        data.extend(value.encode('ascii') + b'\x00')

    return bytes(data)
```

---

## Python Code Example

```python
import struct
import subprocess
import tempfile
import os

APPLE_LOOP_META_UUID = bytes.fromhex('29819273b5bf4aefb78d62d1ef90bb2c')

def convert_wav_to_apple_loop(wav_path, output_path, metadata):
    """
    Convert a WAV file to Apple Loop CAF format.

    metadata should contain:
    - category: str (e.g., "Bass")
    - subcategory: str (e.g., "Electric Bass")
    - genre: str (e.g., "Funk")
    - beat_count: int (e.g., 8)
    - time_signature: str (e.g., "4/4")
    - key_signature: str (optional, e.g., "A")
    - key_type: str (optional, e.g., "minor")
    - descriptors: str (e.g., "Single,Electric,Grooving")
    """

    temp_caf = tempfile.mktemp(suffix='.caf')

    try:
        # Convert to CAF using afconvert (ALAC lossless)
        subprocess.run([
            'afconvert', '-f', 'caff', '-d', 'alac',
            wav_path, temp_caf
        ], check=True)

        with open(temp_caf, 'rb') as f:
            caf_data = bytearray(f.read())

        # Find and replace info chunk
        info_pos = caf_data.find(b'info')
        if info_pos >= 0:
            old_size = struct.unpack('>Q', caf_data[info_pos+4:info_pos+12])[0]
            new_info = create_info_chunk(metadata['genre'])
            new_chunk = b'info' + struct.pack('>Q', len(new_info)) + new_info
            caf_data[info_pos:info_pos+12+old_size] = new_chunk

        # Find insert position (after data chunk)
        data_pos = caf_data.find(b'data')
        data_size = struct.unpack('>Q', caf_data[data_pos+4:data_pos+12])[0]
        insert_pos = data_pos + 12 + data_size

        # Create and insert UUID chunk
        uuid_data = create_apple_loop_uuid_chunk(metadata)
        uuid_chunk = b'uuid' + struct.pack('>Q', len(uuid_data)) + uuid_data
        caf_data[insert_pos:insert_pos] = uuid_chunk

        with open(output_path, 'wb') as f:
            f.write(caf_data)

        return True

    finally:
        if os.path.exists(temp_caf):
            os.remove(temp_caf)

def create_info_chunk(genre):
    data = struct.pack('>I', 1)
    data += b'genre\x00' + genre.encode('ascii') + b'\x00'
    return data

def create_apple_loop_uuid_chunk(metadata):
    data = bytearray(APPLE_LOOP_META_UUID)

    kv_pairs = []
    if metadata.get('subcategory'):
        kv_pairs.append(('subcategory', metadata['subcategory']))
    if metadata.get('category'):
        kv_pairs.append(('category', metadata['category']))
    if metadata.get('key_signature'):
        kv_pairs.append(('key signature', metadata['key_signature']))
    if metadata.get('time_signature'):
        kv_pairs.append(('time signature', metadata['time_signature']))
    if metadata.get('beat_count'):
        kv_pairs.append(('beat count', str(metadata['beat_count'])))
    if metadata.get('descriptors'):
        kv_pairs.append(('descriptors', metadata['descriptors']))
    if metadata.get('genre'):
        kv_pairs.append(('genre', metadata['genre']))
    if metadata.get('key_type'):
        kv_pairs.append(('key type', metadata['key_type']))

    data.extend(struct.pack('>I', len(kv_pairs)))
    for key, value in kv_pairs:
        data.extend(key.encode('ascii') + b'\x00')
        data.extend(value.encode('ascii') + b'\x00')

    return bytes(data)
```

---

## Verification

### Check with afinfo

```bash
afinfo your_loop.caf
```

### Check Spotlight Indexing

```bash
mdimport -t -d2 your_loop.caf 2>&1 | grep Genre
```

Should show: `kMDItemMusicalGenre = "Your Genre";`

### Parse Metadata

```python
def read_apple_loop_metadata(caf_path):
    with open(caf_path, 'rb') as f:
        data = f.read()

    uuid_pos = data.find(APPLE_LOOP_META_UUID)
    if uuid_pos < 0:
        return None

    chunk_start = uuid_pos - 12
    chunk_size = struct.unpack('>Q', data[chunk_start+4:chunk_start+12])[0]
    uuid_data = data[uuid_pos+16:uuid_pos+16+chunk_size-16]

    metadata = {}
    pos = 0
    current_key = None
    while pos < len(uuid_data):
        end = uuid_data.find(b'\x00', pos)
        if end == -1:
            break
        s = uuid_data[pos:end].decode('ascii')
        if s:
            if current_key is None:
                current_key = s
            else:
                metadata[current_key] = s
                current_key = None
        pos = end + 1

    return metadata
```

---

## References

- Apple Core Audio Format Specification
- Reverse-engineered from `/Library/Audio/Apple Loops/Apple/` files
- Tested with Logic Pro and GarageBand loop browsers

---

*Document created: January 2026*
*Based on analysis of Apple Loops from Logic Pro 11 / macOS Sequoia*
