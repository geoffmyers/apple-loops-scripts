#!/usr/bin/env python3
"""
Convert audio and MIDI files to Apple Loop CAF format with metadata.

This tool converts audio files (WAV, AIFF, MP3, M4A, ALAC, FLAC, etc.) and
MIDI files (.mid, .midi) to the Apple Loop CAF format used by Logic Pro and
GarageBand. It extracts metadata from filenames and embeds it in the correct
format for the Loop Browser.

For AUDIO files, the output format matches official Apple Loops files exactly:
- CAF container format with AAC/ALAC audio encoding
- UUID chunk with Apple Loop metadata (29819273-b5bf-4aef-b78d-62d1ef90bb2c)
- UUID chunk with beat markers (0352811b-9d5d-42e1-882d-6af61a6b330c)
- info chunk with genre for Spotlight indexing

For MIDI files, the output contains:
- CAF container with embedded MIDI data in standard 'midi' chunk
- Same UUID chunks for Apple Loop metadata and beat markers
- Allows MIDI editing in Logic Pro's Piano Roll

Usage:
    # Convert a single audio file
    ./convert_to_apple_loops.py input.wav -o output.caf --tempo 120 --key Am

    # Convert a single MIDI file
    ./convert_to_apple_loops.py input.mid -o output.caf --category Keyboards

    # Bulk convert a directory (auto-detects audio and MIDI files)
    ./convert_to_apple_loops.py /path/to/loops/ --output-dir "~/Library/Audio/Apple Loops/User Loops/"

    # Dry run to preview metadata extraction
    ./convert_to_apple_loops.py /path/to/loops/ --dry-run

See APPLE_LOOPS_FORMAT.md for detailed format documentation.
"""

import os
import re
import struct
import subprocess
import argparse
import tempfile
import sys
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Set
from dataclasses import dataclass, field

# Apple's metadata vocabularies, censused from Apple's own shipped loops and
# shared with the decoder so both sides check against the same lists.
from apple_loops_vocabulary import (
    APPLE_CATEGORIES,
    APPLE_CATEGORY_SUBCATEGORIES,
    APPLE_DESCRIPTORS,
    APPLE_FALLBACK_SUBCATEGORY,
    APPLE_GENRES,
    APPLE_SUBCATEGORIES,
    PERCUSSIVE_APPLE_CATEGORIES,
)

# numpy and librosa are needed only for transient detection, which is one
# optional feature. Importing them at module level made them a hard
# requirement for converting a single MIDI file -- and for running at all on a
# stock macOS Python, which is where this tool runs. TransientDetector imports
# them when it is actually used.


# Apple Loop metadata UUID
APPLE_LOOP_META_UUID = bytes.fromhex('29819273b5bf4aefb78d62d1ef90bb2c')

# Apple Loop beat markers UUID
BEAT_MARKERS_UUID = bytes.fromhex('0352811b9d5d42e1882d6af61a6b330c')

# CAF file header
CAF_HEADER = b'caff' + struct.pack('>H', 1) + struct.pack('>H', 0)

# Supported audio input formats
AUDIO_EXTENSIONS = (
    '.wav', '.aif', '.aiff', '.mp3', '.m4a', '.aac',
    '.flac', '.alac', '.caf', '.ogg', '.wma'
)

# Supported MIDI input formats
MIDI_EXTENSIONS = ('.mid', '.midi', '.smf')

# All supported extensions
SUPPORTED_EXTENSIONS = AUDIO_EXTENSIONS + MIDI_EXTENSIONS

# Loop lengths that occur in real music, expressed in bars. A sample whose
# derived beat count lands near one of these almost always IS that length --
# the difference is a decay tail or a trim that missed the zero crossing.
MUSICAL_BAR_LENGTHS = (
    0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64,
)

# How far a derived beat count may sit from a musical length and still be
# snapped to it, as a fraction of that length. Keeps short fragments honest.
BEAT_COUNT_SNAP_TOLERANCE = 0.12

# Hard ceiling on that tolerance, in beats. The error being absorbed is a decay
# tail or a trim that missed the zero crossing, and that is roughly constant in
# time no matter how long the loop is -- it does not scale with the loop. A
# purely proportional tolerance would swallow two whole beats on a 32-beat loop
# and shift the recovered tempo by 7%, which is audibly wrong in the opposite
# direction. Anything bigger than sub-beat slop is left alone.
BEAT_COUNT_SNAP_MAX_BEATS = 0.75


def beats_per_bar(time_signature: str) -> int:
    """Beats per bar from a time signature string. Defaults to 4."""
    match = re.match(r'\s*(\d+)\s*/\s*\d+\s*$', time_signature or '')
    if not match:
        return 4
    beats = int(match.group(1))
    return beats if beats > 0 else 4


def musical_beat_counts(beats_per_bar_value: int) -> List[int]:
    """Whole-number beat counts corresponding to musical bar lengths."""
    counts = []
    for bars in MUSICAL_BAR_LENGTHS:
        beats = beats_per_bar_value * bars
        if beats >= 1 and float(beats).is_integer():
            counts.append(int(beats))
    return sorted(set(counts))


def snap_beat_count(raw_beats: float, beats_per_bar: int = 4,
                    tolerance: float = BEAT_COUNT_SNAP_TOLERANCE) -> int:
    """Round a derived beat count to the nearest musical length.

    Apple Loops carry no tempo field -- Logic recovers it as
    beat_count * 60 / duration -- so an off-by-one beat count is an
    off-by-one-beat tempo error. Snapping only happens when a musical
    length is within `tolerance`; a genuinely odd length is left alone.
    """
    if raw_beats <= 0:
        return 0

    candidates = musical_beat_counts(beats_per_bar)
    in_range = [
        c for c in candidates
        if abs(raw_beats - c) <= min(tolerance * c, BEAT_COUNT_SNAP_MAX_BEATS)
    ]
    if in_range:
        return min(in_range, key=lambda c: abs(raw_beats - c))

    return int(round(raw_beats))



@dataclass
class LoopMetadata:
    """Apple Loop metadata structure."""
    category: str = "Other Instrument"
    subcategory: str = APPLE_FALLBACK_SUBCATEGORY
    genre: str = "Other Genre"
    beat_count: int = 0
    time_signature: str = "4/4"
    key_signature: str = ""  # Empty for drums/percussion
    key_type: str = ""  # major, minor, both, neither
    descriptors: str = ""
    tempo: Optional[int] = None  # Used for beat_count calculation
    duration: Optional[float] = None  # Duration in seconds
    loop_type: str = "audio"  # "audio" or "midi"
    # A one-shot is a single hit, not a rhythmic phrase. It must carry no beat
    # count and no beat markers, or Logic will time-stretch it to the project
    # tempo -- which turns a kick drum into a different kick drum.
    is_one_shot: bool = False
    # Where this metadata came from: "splice" (the app's own database) or
    # "filename" (inferred). Reported so a bulk run shows how much of the
    # library got authoritative values rather than guesses.
    metadata_source: str = "filename"


@dataclass
class MIDIInfo:
    """Information extracted from a MIDI file."""
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


@dataclass
class OnsetDetectionConfig:
    """Configuration for onset/transient detection."""
    hop_length: int = 512
    backtrack: bool = True
    threshold: float = 0.3
    wait: float = 0.03
    # Off by default. Forcing a marker per beat contradicts Apple, which ships
    # 13.3% of its tempo-following loops with fewer markers than beats -- down
    # to one marker on a 16-beat sweep. Sustained material has nothing to
    # slice, and Logic stretches it as a single segment.
    min_markers_per_beat: float = 0.0


class MIDIParser:
    """Parse MIDI files and extract metadata."""

    KEY_SIGNATURES = {
        (-7, 0): ('Cb', 'major'), (-6, 0): ('Gb', 'major'), (-5, 0): ('Db', 'major'),
        (-4, 0): ('Ab', 'major'), (-3, 0): ('Eb', 'major'), (-2, 0): ('Bb', 'major'),
        (-1, 0): ('F', 'major'), (0, 0): ('C', 'major'), (1, 0): ('G', 'major'),
        (2, 0): ('D', 'major'), (3, 0): ('A', 'major'), (4, 0): ('E', 'major'),
        (5, 0): ('B', 'major'), (6, 0): ('F#', 'major'), (7, 0): ('C#', 'major'),
        (-7, 1): ('Ab', 'minor'), (-6, 1): ('Eb', 'minor'), (-5, 1): ('Bb', 'minor'),
        (-4, 1): ('F', 'minor'), (-3, 1): ('C', 'minor'), (-2, 1): ('G', 'minor'),
        (-1, 1): ('D', 'minor'), (0, 1): ('A', 'minor'), (1, 1): ('E', 'minor'),
        (2, 1): ('B', 'minor'), (3, 1): ('F#', 'minor'), (4, 1): ('C#', 'minor'),
        (5, 1): ('G#', 'minor'), (6, 1): ('D#', 'minor'), (7, 1): ('A#', 'minor'),
    }

    def parse_file(self, midi_path: Path) -> MIDIInfo:
        """Parse a MIDI file and extract metadata."""
        with open(midi_path, 'rb') as f:
            raw_data = f.read()

        info = MIDIInfo(raw_data=raw_data)

        try:
            import mido
            midi = mido.MidiFile(str(midi_path))
            info = self._parse_with_mido(midi, raw_data)
        except ImportError:
            info = self._parse_basic(raw_data)

        return info

    def _parse_with_mido(self, midi, raw_data: bytes) -> MIDIInfo:
        """Parse MIDI file using mido library."""
        import mido

        info = MIDIInfo(raw_data=raw_data)
        info.ticks_per_beat = midi.ticks_per_beat
        info.num_tracks = len(midi.tracks)

        tempo = 500000
        total_ticks = 0

        for track in midi.tracks:
            track_ticks = 0
            for msg in track:
                track_ticks += msg.time

                if msg.type == 'set_tempo':
                    tempo = msg.tempo
                elif msg.type == 'time_signature':
                    info.time_signature = (msg.numerator, msg.denominator)
                elif msg.type == 'key_signature':
                    key_info = self._parse_key_signature_mido(msg)
                    if key_info:
                        info.key_signature, info.key_type = key_info
                elif msg.type == 'note_on' and msg.velocity > 0:
                    info.num_notes += 1
                    info.channels.add(msg.channel)
                elif msg.type == 'program_change':
                    info.programs.add(msg.program)

            total_ticks = max(total_ticks, track_ticks)

        info.tempo = round(60000000 / tempo)
        info.duration = total_ticks * (tempo / 1e6) / info.ticks_per_beat

        if info.duration > 0 and info.tempo > 0:
            info.beat_count = round((info.tempo * info.duration) / 60)

        return info

    def _parse_key_signature_mido(self, msg) -> Optional[Tuple[str, str]]:
        """Parse key signature from mido message."""
        try:
            key = msg.key
            if key.endswith('m'):
                return key[:-1], 'minor'
            else:
                return key, 'major'
        except Exception:
            return None

    def _parse_basic(self, raw_data: bytes) -> MIDIInfo:
        """Basic MIDI parsing without mido library."""
        info = MIDIInfo(raw_data=raw_data)

        if len(raw_data) < 14 or raw_data[0:4] != b'MThd':
            return info

        header_length = struct.unpack('>I', raw_data[4:8])[0]
        num_tracks = struct.unpack('>H', raw_data[10:12])[0]
        division = struct.unpack('>H', raw_data[12:14])[0]

        info.num_tracks = num_tracks
        info.ticks_per_beat = division if not (division & 0x8000) else 480

        pos = 8 + header_length
        total_ticks = 0
        tempo = 500000

        for _ in range(num_tracks):
            if pos + 8 > len(raw_data) or raw_data[pos:pos+4] != b'MTrk':
                break

            track_length = struct.unpack('>I', raw_data[pos+4:pos+8])[0]
            track_end = pos + 8 + track_length
            track_pos = pos + 8
            track_ticks = 0

            while track_pos < track_end:
                delta = 0
                while track_pos < track_end:
                    byte = raw_data[track_pos]
                    track_pos += 1
                    delta = (delta << 7) | (byte & 0x7F)
                    if not (byte & 0x80):
                        break
                track_ticks += delta

                if track_pos >= track_end:
                    break

                status = raw_data[track_pos]

                if status == 0xFF:
                    if track_pos + 2 >= len(raw_data):
                        break
                    meta_type = raw_data[track_pos + 1]
                    meta_length = raw_data[track_pos + 2]
                    track_pos += 3

                    if meta_type == 0x51 and meta_length == 3 and track_pos + 3 <= len(raw_data):
                        tempo = (raw_data[track_pos] << 16 |
                                raw_data[track_pos + 1] << 8 |
                                raw_data[track_pos + 2])
                    elif meta_type == 0x58 and meta_length >= 2 and track_pos + 2 <= len(raw_data):
                        num = raw_data[track_pos]
                        denom = 2 ** raw_data[track_pos + 1]
                        info.time_signature = (num, denom)
                    elif meta_type == 0x59 and meta_length == 2 and track_pos + 2 <= len(raw_data):
                        sf = raw_data[track_pos]
                        if sf > 127:
                            sf -= 256
                        mi = raw_data[track_pos + 1]
                        key_info = self.KEY_SIGNATURES.get((sf, mi))
                        if key_info:
                            info.key_signature, info.key_type = key_info

                    track_pos += meta_length
                elif status >= 0xF0:
                    track_pos += 1
                    if status == 0xF0 or status == 0xF7:
                        while track_pos < track_end and raw_data[track_pos] != 0xF7:
                            track_pos += 1
                        track_pos += 1
                else:
                    if status >= 0x80:
                        track_pos += 1
                        if status >= 0x80 and status < 0xC0:
                            track_pos += 2
                            if status >= 0x90 and status < 0xA0:
                                info.num_notes += 1
                        elif status >= 0xC0 and status < 0xE0:
                            track_pos += 1
                        elif status >= 0xE0:
                            track_pos += 2
                    else:
                        track_pos += 1

            total_ticks = max(total_ticks, track_ticks)
            pos = track_end

        info.tempo = round(60000000 / tempo) if tempo > 0 else 120
        info.duration = total_ticks * (tempo / 1e6) / info.ticks_per_beat if info.ticks_per_beat > 0 else 0

        if info.duration > 0 and info.tempo > 0:
            info.beat_count = round((info.tempo * info.duration) / 60)

        return info


class TransientDetector:
    """Detect transients in audio files using librosa onset detection."""

    def __init__(self, config: Optional[OnsetDetectionConfig] = None):
        self.config = config or OnsetDetectionConfig()
        self._librosa_available = None

    def _check_librosa(self) -> bool:
        if self._librosa_available is None:
            try:
                import librosa
                self._librosa_available = True
            except ImportError:
                self._librosa_available = False
        return self._librosa_available

    def detect(self, audio_path: Path, beat_count: int,
               sample_rate: Optional[int] = None,
               num_frames: Optional[int] = None,
               min_markers: Optional[int] = None) -> List[int]:
        """Detect transients in audio file."""
        if not self._check_librosa():
            raise ImportError("librosa is required for transient detection")

        import librosa

        y, sr = librosa.load(audio_path, sr=sample_rate, mono=True)
        total_frames = len(y)

        if num_frames is None:
            num_frames = total_frames

        onset_env = librosa.onset.onset_strength(
            y=y, sr=sr, hop_length=self.config.hop_length
        )

        onset_frames = librosa.onset.onset_detect(
            y=y, sr=sr, hop_length=self.config.hop_length,
            backtrack=self.config.backtrack, units='frames',
            onset_envelope=onset_env,
            wait=int(self.config.wait * sr / self.config.hop_length)
        )

        onset_samples = librosa.frames_to_samples(
            onset_frames, hop_length=self.config.hop_length
        )

        if min_markers is None:
            min_markers = (int(beat_count * self.config.min_markers_per_beat) + 1
                           if self.config.min_markers_per_beat else 0)

        return self._build_marker_list(
            onset_samples, num_frames, beat_count, min_markers,
            min_separation=int(self.config.wait * sr),
        )

    def _build_marker_list(self, onsets: 'np.ndarray', num_frames: int,
                           beat_count: int, min_markers: int = 0,
                           min_separation: int = 0) -> List[int]:
        """Detected onsets, anchored at both ends of the audio.

        No marker is invented unless `min_markers` explicitly asks for one.
        Marker density is a property of the audio: a pad has no transients, and
        Apple ships such loops with two or three markers rather than a grid.
        """
        markers = [0]

        for onset in onsets:
            if onset > 0 and onset < num_frames:
                markers.append(int(onset))

        markers.append(num_frames)
        markers = sorted(set(markers))

        if min_markers and len(markers) < min_markers:
            markers = self._add_fallback_markers(
                markers, num_frames, min_markers, min_separation)

        return markers

    def _add_fallback_markers(self, existing: List[int], num_frames: int,
                              min_markers: int, min_separation: int = 0) -> List[int]:
        """Pad a sparse marker set out to `min_markers` with an even grid.

        Opt-in only (`--min-markers-per-beat`). A grid point closer than
        `min_separation` to a marker already present is dropped: appending one
        blindly put markers 0.18 ms apart on a real drum loop whose own onsets
        were 209 ms apart, which is an eight-sample stretch segment. The
        detected transient always wins -- it is where the attack actually is.
        """
        if min_markers <= 1:
            return existing

        interval = num_frames / (min_markers - 1)
        merged = list(existing)

        for i in range(min_markers):
            pos = min(int(round(i * interval)), num_frames)
            if min_separation and any(abs(pos - m) < min_separation for m in merged):
                continue
            merged.append(pos)

        return sorted(set(merged))


class MetadataExtractor:
    """Extract Apple Loop metadata from filenames, paths, and MIDI content."""

    CATEGORIES = {
        'Bass', 'Drums', 'Guitars', 'Horn/Wind', 'Keyboards', 'Mallets',
        'Mixed', 'Other Instrument', 'Percussion', 'Sound Effect',
        'Strings', 'Texture/Atmosphere', 'Vocals'
    }

    GENRES = {
        'Cinematic/New Age', 'Country/Folk', 'Electronic/Dance', 'Experimental',
        'Funk', 'Hip Hop', 'Jazz', 'Modern RnB', 'Orchestral', 'Other Genre',
        'Rock/Blues', 'Urban', 'World/Ethnic'
    }

    KEY_TYPES = {'major', 'minor', 'both', 'neither'}

    DESCRIPTORS = {
        'Acoustic', 'Arrhythmic', 'Cheerful', 'Clean', 'Dark', 'Dissonant',
        'Distorted', 'Dry', 'Electric', 'Ensemble', 'Fill', 'Grooving',
        'Intense', 'Melodic', 'Part', 'Processed', 'Relaxed', 'Single'
    }

    INSTRUMENT_MAP = {
        'bass': ('Bass', 'Electric Bass'),
        'electric bass': ('Bass', 'Electric Bass'),
        'acoustic bass': ('Bass', 'Acoustic Bass'),
        'synth bass': ('Bass', 'Synthetic Bass'),
        'sub bass': ('Bass', 'Synthetic Bass'),
        '808': ('Bass', 'Synthetic Bass'),
        'drum': ('Drums', 'Drum Kit'),
        'drums': ('Drums', 'Drum Kit'),
        'beat': ('Drums', 'Electronic Beats'),
        'beats': ('Drums', 'Electronic Beats'),
        'kick': ('Drums', 'Kick'),
        'snare': ('Drums', 'Snare'),
        'hihat': ('Drums', 'Hi-hat'),
        'hi-hat': ('Drums', 'Hi-hat'),
        'hi hat': ('Drums', 'Hi-hat'),
        'cymbal': ('Drums', 'Cymbal'),
        'tom': ('Drums', 'Drum Kit'),
        'guitar': ('Guitars', 'Electric Guitar'),
        'electric guitar': ('Guitars', 'Electric Guitar'),
        'acoustic guitar': ('Guitars', 'Acoustic Guitar'),
        'slide guitar': ('Guitars', 'Slide Guitar'),
        'clean guitar': ('Guitars', 'Electric Guitar'),
        'distorted guitar': ('Guitars', 'Electric Guitar'),
        'piano': ('Keyboards', 'Piano'),
        'electric piano': ('Keyboards', 'Electric Piano'),
        'rhodes': ('Keyboards', 'Electric Piano'),
        'wurlitzer': ('Keyboards', 'Electric Piano'),
        'organ': ('Keyboards', 'Organ'),
        'clav': ('Keyboards', 'Clavinet'),
        'clavinet': ('Keyboards', 'Clavinet'),
        'keys': ('Keyboards', 'Piano'),
        'keyboard': ('Keyboards', 'Piano'),
        'synth': ('Keyboards', 'Synthesizer'),
        'synthesizer': ('Keyboards', 'Synthesizer'),
        'pad': ('Keyboards', 'Synthesizer'),
        'lead': ('Keyboards', 'Synthesizer'),
        'arp': ('Keyboards', 'Synthesizer'),
        'arpeggio': ('Keyboards', 'Synthesizer'),
        'strings': ('Strings', 'Other Instrument'),
        'violin': ('Strings', 'Violin'),
        'viola': ('Strings', 'Viola'),
        'cello': ('Strings', 'Cello'),
        'orchestral': ('Strings', 'Other Instrument'),
        'brass': ('Horn/Wind', 'Other Instrument'),
        'horn': ('Horn/Wind', 'French Horn'),
        'horns': ('Horn/Wind', 'Other Instrument'),
        'trumpet': ('Horn/Wind', 'Trumpet'),
        'trombone': ('Horn/Wind', 'Trombone'),
        'sax': ('Horn/Wind', 'Saxophone'),
        'saxophone': ('Horn/Wind', 'Saxophone'),
        'flute': ('Horn/Wind', 'Flute'),
        'clarinet': ('Horn/Wind', 'Clarinet'),
        'percussion': ('Percussion', 'Shaker'),
        'shaker': ('Percussion', 'Shaker'),
        'tambourine': ('Percussion', 'Tambourine'),
        'conga': ('Percussion', 'Conga'),
        'bongo': ('Percussion', 'Bongo'),
        'cowbell': ('Percussion', 'Cowbell'),
        'clap': ('Percussion', 'Other Instrument'),
        'claps': ('Percussion', 'Other Instrument'),
        'vibraphone': ('Mallets', 'Vibraphone'),
        'vibes': ('Mallets', 'Vibraphone'),
        'marimba': ('Mallets', 'Marimba'),
        'xylophone': ('Mallets', 'Xylophone'),
        'glockenspiel': ('Mallets', 'Bell'),
        'vocal': ('Vocals', 'Other Instrument'),
        'vocals': ('Vocals', 'Other Instrument'),
        'voice': ('Vocals', 'Other Instrument'),
        'vox': ('Vocals', 'Other Instrument'),
        'choir': ('Vocals', 'Choir'),
        'fx': ('Sound Effect', 'Motions & Transitions'),
        'effect': ('Sound Effect', 'Motions & Transitions'),
        'effects': ('Sound Effect', 'Motions & Transitions'),
        'riser': ('Sound Effect', 'Motions & Transitions'),
        'sweep': ('Sound Effect', 'Motions & Transitions'),
        'impact': ('Sound Effect', 'Motions & Transitions'),
        'hit': ('Sound Effect', 'Motions & Transitions'),
        'transition': ('Sound Effect', 'Motions & Transitions'),
        'ambient': ('Texture/Atmosphere', 'Other Instrument'),
        'atmosphere': ('Texture/Atmosphere', 'Other Instrument'),
        'texture': ('Texture/Atmosphere', 'Other Instrument'),
        'drone': ('Texture/Atmosphere', 'Other Instrument'),
        'noise': ('Texture/Atmosphere', 'Other Instrument'),
    }

    # MIDI program to category/subcategory mapping (General MIDI)
    PROGRAM_MAP = {
        range(0, 8): ('Keyboards', 'Piano'),
        range(8, 16): ('Mallets', 'Vibraphone'),
        range(16, 24): ('Keyboards', 'Organ'),
        range(24, 32): ('Guitars', 'Electric Guitar'),
        range(32, 40): ('Bass', 'Electric Bass'),
        range(40, 48): ('Strings', 'Other Instrument'),
        range(48, 56): ('Strings', 'Other Instrument'),
        range(56, 64): ('Horn/Wind', 'Other Instrument'),
        range(64, 72): ('Horn/Wind', 'Saxophone'),
        range(72, 80): ('Horn/Wind', 'Flute'),
        range(80, 88): ('Keyboards', 'Synthesizer'),
        range(88, 96): ('Keyboards', 'Synthesizer'),
        range(96, 104): ('Sound Effect', 'Motions & Transitions'),
        # GM 104-111 are eight distinct instruments, not one bucket, and
        # Apple ships a real subcategory for most of them.
        range(104, 105): ('Strings', 'Sitar'),          # sitar
        range(105, 106): ('Guitars', 'Banjo'),          # banjo
        range(106, 107): ('Strings', 'Other Instrument'),  # shamisen
        range(107, 108): ('Strings', 'Koto'),           # koto
        range(108, 109): ('Mallets', 'Kalimba'),        # kalimba
        range(109, 110): ('Horn/Wind', 'Bagpipe'),      # bagpipe
        range(110, 111): ('Strings', 'Violin'),         # fiddle
        range(111, 112): ('Horn/Wind', 'Other Instrument'),  # shanai
        range(112, 120): ('Percussion', 'Shaker'),
        range(120, 128): ('Sound Effect', 'Motions & Transitions'),
    }

    GENRE_MAP = {
        'edm': 'Electronic/Dance',
        'electronic': 'Electronic/Dance',
        'house': 'Electronic/Dance',
        'techno': 'Electronic/Dance',
        'trance': 'Electronic/Dance',
        'dubstep': 'Electronic/Dance',
        'dnb': 'Electronic/Dance',
        'drum and bass': 'Electronic/Dance',
        'electro': 'Electronic/Dance',
        'dance': 'Electronic/Dance',
        'hip hop': 'Hip Hop',
        'hiphop': 'Hip Hop',
        'hip-hop': 'Hip Hop',
        'rap': 'Hip Hop',
        'trap': 'Hip Hop',
        'boom bap': 'Hip Hop',
        'lofi': 'Hip Hop',
        'lo-fi': 'Hip Hop',
        'lo fi': 'Hip Hop',
        'funk': 'Funk',
        'funky': 'Funk',
        'disco': 'Funk',
        'soul': 'Funk',
        'rock': 'Rock/Blues',
        'blues': 'Rock/Blues',
        'metal': 'Rock/Blues',
        'punk': 'Rock/Blues',
        'alternative': 'Rock/Blues',
        'indie': 'Rock/Blues',
        'grunge': 'Rock/Blues',
        'jazz': 'Jazz',
        'swing': 'Jazz',
        'bebop': 'Jazz',
        'fusion': 'Jazz',
        'country': 'Country/Folk',
        'folk': 'Country/Folk',
        'bluegrass': 'Country/Folk',
        'americana': 'Country/Folk',
        'acoustic': 'Country/Folk',
        'rnb': 'Modern RnB',
        'r&b': 'Modern RnB',
        'neo soul': 'Modern RnB',
        'urban': 'Urban',
        'grime': 'Urban',
        'uk garage': 'Urban',
        'afrobeat': 'Urban',
        'world': 'World/Ethnic',
        'ethnic': 'World/Ethnic',
        'latin': 'World/Ethnic',
        'reggae': 'World/Ethnic',
        'african': 'World/Ethnic',
        'indian': 'World/Ethnic',
        'asian': 'World/Ethnic',
        'middle eastern': 'World/Ethnic',
        'cinematic': 'Cinematic/New Age',
        'film': 'Cinematic/New Age',
        'movie': 'Cinematic/New Age',
        'trailer': 'Cinematic/New Age',
        'new age': 'Cinematic/New Age',
        'chill': 'Cinematic/New Age',
        'chillout': 'Cinematic/New Age',
        'meditation': 'Cinematic/New Age',
        'orchestral': 'Orchestral',
        'classical': 'Orchestral',
        'symphony': 'Orchestral',
        'epic': 'Orchestral',
        'experimental': 'Experimental',
        'avant garde': 'Experimental',
        'glitch': 'Experimental',
        'idm': 'Experimental',
    }

    DESCRIPTOR_MAP = {
        'clean': 'Clean',
        'dirty': 'Distorted',
        'distorted': 'Distorted',
        'wet': 'Processed',
        'dry': 'Dry',
        'acoustic': 'Acoustic',
        'electric': 'Electric',
        'funky': 'Grooving',
        'groovy': 'Grooving',
        'groove': 'Grooving',
        'melodic': 'Melodic',
        'melody': 'Melodic',
        'harmonic': 'Melodic',
        'chords': 'Melodic',
        'chord': 'Melodic',
        'rhythmic': 'Grooving',
        'rhythm': 'Grooving',
        'dark': 'Dark',
        'bright': 'Cheerful',
        'happy': 'Cheerful',
        'sad': 'Dark',
        'mellow': 'Relaxed',
        'chill': 'Relaxed',
        'relaxed': 'Relaxed',
        'intense': 'Intense',
        'aggressive': 'Intense',
        'hard': 'Intense',
        'soft': 'Relaxed',
        'processed': 'Processed',
        'effected': 'Processed',
        'filtered': 'Processed',
        'fill': 'Fill',
        'single': 'Single',
        'ensemble': 'Ensemble',
        'part': 'Part',
        'dissonant': 'Dissonant',
    }

    KEY_PATTERNS = [
        r'\b([A-Ga-g][#b]?)\s*(maj(?:or)?|min(?:or)?)\b',
        r'\b([A-Ga-g][#b]?)m\b',
        r'\b([A-Ga-g][#b])\b',
        r'(?:^|[_\s\-])([A-Ga-g])(?:[_\s\-]|$)',
    ]

    TEMPO_PATTERNS = [
        r'(\d{2,3})\s*_?bpm',
        r'bpm\s*_?(\d{2,3})',
        r'\[(\d{2,3})\]',
        r'\((\d{2,3})\)',
    ]

    def extract_tempo(self, text: str) -> Optional[int]:
        """Extract tempo (BPM) from text."""
        text_lower = text.lower()

        for pattern in self.TEMPO_PATTERNS:
            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                tempo = int(match.group(1))
                if 40 <= tempo <= 300:
                    return tempo

        underscore_key = re.findall(r'_(\d{2,3})_[A-Ga-g][#b]?(?:[_.\s]|m|$)', text)
        for num_str in underscore_key:
            num = int(num_str)
            if 60 <= num <= 200:
                return num

        underscore_delimited = re.findall(r'_(\d{2,3})_', text)
        tempo_candidates = [int(n) for n in underscore_delimited if 60 <= int(n) <= 200]
        if tempo_candidates:
            for t in tempo_candidates:
                if 90 <= t <= 150:
                    return t
            return tempo_candidates[0]

        end_underscore = re.search(r'_(\d{2,3})(?:\.[^.]+)?$', text)
        if end_underscore:
            num = int(end_underscore.group(1))
            if 60 <= num <= 200:
                return num

        return None

    # Sample packs routinely spell accidentals out, because '#' gets mangled by
    # some filesystems and upload tools.
    SPELLED_ACCIDENTALS = (
        (re.compile(r'\b([A-Ga-g])[\s_-]*sharp\b', re.IGNORECASE), r'\1#'),
        (re.compile(r'\b([A-Ga-g])[\s_-]*flat\b', re.IGNORECASE), r'\1b'),
    )

    def extract_key(self, text: str) -> Tuple[str, str]:
        """Extract key signature and key type from text."""
        text_clean = text.replace('_', ' ').replace('-', ' ')
        for pattern, replacement in self.SPELLED_ACCIDENTALS:
            text_clean = pattern.sub(replacement, text_clean)

        for pattern in self.KEY_PATTERNS:
            match = re.search(pattern, text_clean, re.IGNORECASE)
            if match:
                key = match.group(1).upper()
                if len(key) > 1:
                    key = key[0].upper() + key[1].lower()

                if len(match.groups()) > 1 and match.group(2):
                    scale = match.group(2).lower()
                    if scale.startswith('min'):
                        return key, 'minor'
                    elif scale.startswith('maj'):
                        return key, 'major'

                if re.search(rf'\b{re.escape(key)}m\b', text_clean, re.IGNORECASE):
                    return key, 'minor'

                return key, 'major'

        return '', ''

    # Underscores are word characters to `re`, so "bass_loop" has no word
    # boundary before "loop". Separators are normalised to spaces before these
    # are applied.
    ONE_SHOT_RE = re.compile(r'\b(?:one\s?shots?|1\s?shots?)\b', re.IGNORECASE)
    LOOP_RE = re.compile(r'\bloops?\b', re.IGNORECASE)

    def extract_one_shot(self, text: str) -> Optional[bool]:
        """Whether a path says one-shot (True) or loop (False).

        Returns None when the path says neither, so callers can tell "not
        stated" from "stated as a loop" -- Splice's database, when available,
        should win over a guess but not over silence.

        One-shot is checked first: a pack called "Loopcloud" must not override
        the "oneshots" folder inside it.
        """
        normalised = re.sub(r'[_\-]+', ' ', text or '')
        if self.ONE_SHOT_RE.search(normalised):
            return True
        if self.LOOP_RE.search(normalised):
            return False
        return None

    def extract_instrument(self, text: str,
                           midi_programs: Optional[Set[int]] = None) -> Tuple[str, str]:
        """Extract instrument category and subcategory from text and MIDI programs."""
        text_lower = text.lower().replace('_', ' ').replace('-', ' ')

        sorted_keywords = sorted(self.INSTRUMENT_MAP.keys(), key=len, reverse=True)
        for keyword in sorted_keywords:
            if keyword in text_lower:
                return self.INSTRUMENT_MAP[keyword]

        if midi_programs:
            for program_range, category_info in self.PROGRAM_MAP.items():
                for program in midi_programs:
                    if program in program_range:
                        return category_info

        return 'Other Instrument', APPLE_FALLBACK_SUBCATEGORY

    def extract_genre(self, text: str, path: str = "") -> str:
        """Extract genre from text and path."""
        combined = f"{path} {text}".lower().replace('_', ' ').replace('-', ' ')

        sorted_keywords = sorted(self.GENRE_MAP.keys(), key=len, reverse=True)
        for keyword in sorted_keywords:
            if keyword in combined:
                return self.GENRE_MAP[keyword]

        return 'Other Genre'

    def extract_descriptors(self, text: str) -> List[str]:
        """Extract descriptor tags from text."""
        text_lower = text.lower().replace('_', ' ').replace('-', ' ')
        descriptors = set()

        for keyword, descriptor in self.DESCRIPTOR_MAP.items():
            if keyword in text_lower:
                descriptors.add(descriptor)

        return sorted(descriptors)

    def extract_all(self, filename: str, filepath: str = "",
                    midi_info: Optional[MIDIInfo] = None) -> LoopMetadata:
        """Extract all metadata from filename, path, and optional MIDI info."""
        metadata = LoopMetadata()

        # Extract tempo from filename first (takes precedence)
        filename_tempo = self.extract_tempo(filename)
        if filename_tempo:
            metadata.tempo = filename_tempo
        elif midi_info and midi_info.tempo:
            metadata.tempo = midi_info.tempo

        # Extract key signature
        if midi_info and midi_info.key_signature:
            metadata.key_signature = midi_info.key_signature
            metadata.key_type = midi_info.key_type
        else:
            metadata.key_signature, metadata.key_type = self.extract_key(filename)

        # Extract time signature from MIDI
        if midi_info and midi_info.time_signature:
            metadata.time_signature = f"{midi_info.time_signature[0]}/{midi_info.time_signature[1]}"

        # Extract duration and beat count from MIDI
        if midi_info:
            metadata.duration = midi_info.duration
            if midi_info.beat_count:
                metadata.beat_count = midi_info.beat_count
            metadata.loop_type = "midi"

        # Extract instrument (category and subcategory)
        midi_programs = midi_info.programs if midi_info else None
        metadata.category, metadata.subcategory = self.extract_instrument(filename, midi_programs)

        # Extract genre
        metadata.genre = self.extract_genre(filename, filepath)

        # Extract descriptors
        descriptors = self.extract_descriptors(filename)
        metadata.descriptors = ','.join(descriptors) if descriptors else ''

        # Drums/percussion don't have key signatures
        if metadata.category in ('Drums', 'Percussion'):
            metadata.key_signature = ''
            metadata.key_type = 'neither'

        # Loop or one-shot, read from the filename and the folder it sits in.
        if self.extract_one_shot(f"{filepath} {filename}") is True:
            metadata.is_one_shot = True
            # A serial number ("kick_128") reads exactly like a tempo. On a
            # single hit that tempo would become a beat count, and a beat count
            # is what makes Logic stretch the file.
            metadata.tempo = None
            metadata.beat_count = 0

        return metadata


# ---------------------------------------------------------------------------
# Splice vocabulary -> Apple vocabulary
#
# Tag vocabulary follows Splice's own sounds page taxonomy. Splice orders the
# tag list most-specific-first, so the first tag that maps wins.
# ---------------------------------------------------------------------------

SPLICE_TAG_TO_INSTRUMENT = {
    # Drums
    "kicks": ("Drums", "Kick"),
    "808": ("Drums", "Kick"),
    "snares": ("Drums", "Snare"),
    "rims": ("Drums", "Snare"),
    "sidestick": ("Drums", "Snare"),
    "claps": ("Drums", "Drum Kit"),
    "snaps": ("Drums", "Drum Kit"),
    "hats": ("Drums", "Hi-hat"),
    "closed": ("Drums", "Hi-hat"),
    "open": ("Drums", "Hi-hat"),
    "tops": ("Drums", "Hi-hat"),
    "toms": ("Drums", "Tom"),
    "cymbals": ("Drums", "Cymbal"),
    "rides": ("Drums", "Cymbal"),
    "crash": ("Drums", "Cymbal"),
    "rolls": ("Drums", "Cymbal"),
    "breaks": ("Drums", "Electronic Beats"),
    "fills": ("Drums", "Drum Kit"),
    "acoustic drums": ("Drums", "Drum Kit"),
    "drums": ("Drums", "Drum Kit"),
    # Percussion
    "shakers": ("Percussion", "Shaker"),
    "tambourine": ("Percussion", "Tambourine"),
    "bongos": ("Percussion", "Bongo"),
    "congas": ("Percussion", "Conga"),
    "conga": ("Percussion", "Conga"),
    "cowbells": ("Percussion", "Cowbell"),
    "claves": ("Percussion", "Other Instrument"),
    "woodblock": ("Percussion", "Other Instrument"),
    "djembe": ("Percussion", "Other Instrument"),
    "timbales": ("Percussion", "Other Instrument"),
    "brushes": ("Percussion", "Other Instrument"),
    "grooves": ("Percussion", "Other Instrument"),
    "percussion": ("Percussion", "Other Instrument"),
    # Mallets
    "mallets": ("Mallets", "Bell"),
    "bells": ("Mallets", "Bell"),
    "marimba": ("Mallets", "Marimba"),
    "vibraphone": ("Mallets", "Vibraphone"),
    "xylophone": ("Mallets", "Xylophone"),
    # Bass
    "sub": ("Bass", "Synthetic Bass"),
    "electric bass": ("Bass", "Electric Bass"),
    "acid bass": ("Bass", "Synthetic Bass"),
    "wobble": ("Bass", "Synthetic Bass"),
    "reese": ("Bass", "Synthetic Bass"),
    "pulse": ("Bass", "Synthetic Bass"),
    "bass": ("Bass", "Synthetic Bass"),
    # Keyboards / synths -- Apple has no "Synth" category.
    "leads": ("Keyboards", "Synthesizer"),
    "arp": ("Keyboards", "Synthesizer"),
    "stabs": ("Keyboards", "Synthesizer"),
    "plucks": ("Keyboards", "Synthesizer"),
    "analog": ("Keyboards", "Synthesizer"),
    "synth melody": ("Keyboards", "Synthesizer"),
    "synth": ("Keyboards", "Synthesizer"),
    "chords": ("Keyboards", "Synthesizer"),
    "piano": ("Keyboards", "Piano"),
    "electric piano": ("Keyboards", "Electric Piano"),
    "wurlitzer": ("Keyboards", "Electric Piano"),
    "organ": ("Keyboards", "Organ"),
    "hammond": ("Keyboards", "Organ"),
    "clavinet": ("Keyboards", "Clavinet"),
    "keys melody": ("Keyboards", "Piano"),
    "keys": ("Keyboards", "Piano"),
    # Guitars
    "electric guitar": ("Guitars", "Electric Guitar"),
    "acoustic guitar": ("Guitars", "Acoustic Guitar"),
    "slide": ("Guitars", "Slide Guitar"),
    "riffs": ("Guitars", "Electric Guitar"),
    "guitar melody": ("Guitars", "Electric Guitar"),
    "guitar": ("Guitars", "Electric Guitar"),
    # Horn / Wind
    "saxophone": ("Horn/Wind", "Saxophone"),
    "trumpet": ("Horn/Wind", "Trumpet"),
    "trombone": ("Horn/Wind", "Trombone"),
    "flute": ("Horn/Wind", "Flute"),
    "clarinet": ("Horn/Wind", "Clarinet"),
    "harmonica": ("Horn/Wind", "Harmonica"),
    "horns": ("Horn/Wind", "Other Instrument"),
    "brass & woodwinds": ("Horn/Wind", "Other Instrument"),
    # Strings
    "violin": ("Strings", "Violin"),
    "viola": ("Strings", "Viola"),
    "cello": ("Strings", "Cello"),
    "harp": ("Strings", "Harp"),
    "orchestral": ("Strings", "Other Instrument"),
    "staccato": ("Strings", "Other Instrument"),
    "strings melody": ("Strings", "Other Instrument"),
    "strings": ("Strings", "Other Instrument"),
    # Vocals
    "female vocals": ("Vocals", "Female"),
    "male vocals": ("Vocals", "Male"),
    "vocal fx": ("Vocals", "Other Instrument"),
    "fx vocals": ("Vocals", "Other Instrument"),
    "vocoder": ("Vocals", "Other Instrument"),
    "choir": ("Vocals", "Choir"),
    "chants": ("Vocals", "Choir"),
    "spoken word": ("Vocals", "Other Instrument"),
    "dialogue": ("Vocals", "Other Instrument"),
    "screams": ("Vocals", "Other Instrument"),
    "vocal shouts": ("Vocals", "Other Instrument"),
    "shouts": ("Vocals", "Other Instrument"),
    "whisper vocals": ("Vocals", "Other Instrument"),
    "vocal phrases": ("Vocals", "Other Instrument"),
    "phrases": ("Vocals", "Other Instrument"),
    "adlib": ("Vocals", "Other Instrument"),
    "hooks": ("Vocals", "Other Instrument"),
    "vocals": ("Vocals", "Other Instrument"),
    # Texture / atmosphere -- pads and drones belong here, not under Keyboards.
    "pads": ("Texture/Atmosphere", "Synthesizer"),
    "atmospheres": ("Texture/Atmosphere", "Other Instrument"),
    "textures": ("Texture/Atmosphere", "Synthesizer"),
    "drones": ("Texture/Atmosphere", "Synthesizer"),
    "field recordings": ("Texture/Atmosphere", "Other Instrument"),
    # Sound effects
    "risers": ("Sound Effect", "Motions & Transitions"),
    "downers": ("Sound Effect", "Motions & Transitions"),
    "sweeps": ("Sound Effect", "Motions & Transitions"),
    "impacts": ("Sound Effect", "Motions & Transitions"),
    "transitions": ("Sound Effect", "Motions & Transitions"),
    "noise": ("Sound Effect", "Motions & Transitions"),
    "reverse": ("Sound Effect", "Motions & Transitions"),
    "siren": ("Sound Effect", "Motions & Transitions"),
    "lasers": ("Sound Effect", "Motions & Transitions"),
    "foley": ("Sound Effect", "Motions & Transitions"),
    "found sounds": ("Sound Effect", "Motions & Transitions"),
    "fx": ("Sound Effect", "Motions & Transitions"),
    # --- Added from a census of a real 1,725-sample library. Splice's tag
    # vocabulary is open; these are the tags that actually occur.
    "female": ("Vocals", "Female"),
    "male": ("Vocals", "Male"),
    "beatbox": ("Vocals", "Other Instrument"),
    "vocalsynth": ("Vocals", "Other Instrument"),
    "nylon": ("Guitars", "Acoustic Guitar"),
    "fingerpicked": ("Guitars", "Acoustic Guitar"),
    "strum": ("Guitars", "Acoustic Guitar"),
    "upright": ("Bass", "Acoustic Bass"),
    "drum machine": ("Drums", "Electronic Beats"),
    "flam": ("Drums", "Drum Kit"),
    "sticks": ("Percussion", "Other Instrument"),
    "cajon": ("Percussion", "Other Instrument"),
    "guiro": ("Percussion", "Other Instrument"),
    "rainstick": ("Percussion", "Other Instrument"),
    "flexatone": ("Percussion", "Other Instrument"),
    "moog": ("Keyboards", "Synthesizer"),
    "juno": ("Keyboards", "Synthesizer"),
    "saw": ("Keyboards", "Synthesizer"),
    "fm": ("Keyboards", "Synthesizer"),
    "pianet": ("Keyboards", "Electric Piano"),
    "prepared piano": ("Keyboards", "Piano"),
    "flugelhorn": ("Horn/Wind", "Trumpet"),
    "duduk": ("Horn/Wind", "Other Instrument"),
    "whistle": ("Horn/Wind", "Other Instrument"),
    "violin": ("Strings", "Violin"),
    "viola": ("Strings", "Viola"),
    "sitar": ("Strings", "Sitar"),
    "glockenspiel": ("Mallets", "Bell"),
    "timbales": ("Percussion", "Other Instrument"),
    "ambience": ("Sound Effect", "Ambience"),
    "water": ("Sound Effect", "Misc."),
    "stream": ("Sound Effect", "Misc."),
    "crowd": ("Sound Effect", "People"),
    "braaam": ("Sound Effect", "Impacts & Crashes"),
}

SPLICE_GENRE_TO_APPLE = {
    # Electronic / Dance
    "acid house": "Electronic/Dance",
    "bass house": "Bass House",
    "chinese traditional": "Chinese Traditional",
    "electronic": "Electronic",
    "electronic pop": "Electronic Pop", "bass music": "Electronic/Dance",
    "big room house": "Electronic/Dance", "breakbeat": "Electronic/Dance",
    "deep house": "Deep House", "drum and bass": "Electronic/Dance",
    "drumstep": "Electronic/Dance", "dubstep": "Dubstep",
    "edm": "Electronic/Dance", "electro": "Electronic/Dance",
    "electro house": "Electro House", "fidget house": "Electronic/Dance",
    "footwork": "Electronic/Dance", "future bass": "Future Bass",
    "future house": "Electronic/Dance", "gabber": "Electronic/Dance",
    "hard techno": "Electronic/Dance", "hardcore techno": "Electronic/Dance",
    "hardstyle": "Electronic/Dance", "house": "House",
    "indie dance": "Electronic/Dance", "indie electronic": "Indie",
    "juke": "Electronic/Dance", "jungle": "Electronic/Dance",
    "leftfield bass": "Electronic/Dance", "minimal techno": "Electronic/Dance",
    "moombahton": "Electronic/Dance", "nu-disco": "Electronic/Dance",
    "progressive house": "Electronic/Dance", "psytrance": "Electronic/Dance",
    "rave": "Electronic/Dance", "tearout dubstep": "Dubstep",
    "tech house": "Tech House", "techno": "Techno",
    "trance": "Electronic/Dance", "trap edm": "Electronic/Dance",
    "tropical house": "Electronic/Dance", "uk garage": "Electronic/Dance",
    "future garage": "Electronic/Dance",
    # Hip Hop
    "boom bap": "Hip Hop", "g-funk": "Hip Hop", "gangster rap": "Hip Hop",
    "hip hop": "Hip Hop", "lo-fi hip hop": "Hip Hop", "trap": "Hip Hop",
    "trip hop": "Hip Hop", "miami bass": "Hip Hop",
    # Urban
    "baltimore club": "Urban", "dancehall": "Urban", "ghettotech": "Urban",
    "grime": "Urban", "jersey club": "Urban", "philly club": "Urban",
    "reggaeton": "Reggaeton Pop",
    # Modern RnB
    "r&b": "Modern RnB", "rnb": "Modern RnB", "neo soul": "Modern RnB",
    # Funk
    "disco": "Funk", "funk": "Funk", "soul": "Funk", "gospel": "Funk",
    # Rock / Blues
    "blues": "Rock/Blues", "emo": "Rock/Blues", "heavy metal": "Rock/Blues",
    "indie rock": "Indie", "post-punk": "Rock/Blues",
    "punk rock": "Rock/Blues", "rock": "Rock/Blues", "indie": "Indie",
    # Jazz
    "jazz": "Jazz",
    # Country / Folk
    "bluegrass": "Country/Folk", "country": "Country/Folk",
    "folk": "Country/Folk",
    # Orchestral
    "classical": "Orchestral", "orchestral": "Orchestral",
    # Cinematic / New Age
    "ambient": "Cinematic/New Age", "chillout": "Cinematic/New Age",
    "chillwave": "Chillwave", "cinematic": "Cinematic/New Age",
    "downtempo": "Cinematic/New Age", "new age": "Cinematic/New Age",
    "game audio": "Cinematic/New Age",
    # Experimental
    "chiptune": "Experimental", "experimental": "Experimental",
    "glitch hop": "Experimental", "idm": "Experimental",
    "industrial": "Experimental", "vaporwave": "Experimental",
    "synth-pop": "Electronic Pop",
    # World / Ethnic
    "african": "World/Ethnic", "afrobeat": "World/Ethnic",
    "afropop": "World/Ethnic", "asian": "World/Ethnic",
    "baile funk": "World/Ethnic", "brazilian": "World/Ethnic",
    "calypso": "World/Ethnic", "caribbean": "World/Ethnic",
    "dub": "World/Ethnic", "indian": "World/Ethnic",
    "j-pop": "World/Ethnic", "k-pop": "World/Ethnic",
    "latin american": "World/Ethnic", "middle eastern": "World/Ethnic",
    "reggae": "World/Ethnic", "rocksteady": "World/Ethnic",
    "ska": "World/Ethnic", "soca": "World/Ethnic",
    "south asian": "World/Ethnic",
    # Deliberately unmapped -> Other Genre
    "acapella": "Other Genre", "spoken word": "Other Genre",
    # Apple ships no plain "Pop" and no "Synthwave". These are the
    # nearest it does ship, chosen to under-claim: synthwave is usually
    # instrumental, so it takes the broader "Electronic".
    "pop": "Electronic Pop",
    "synthwave": "Electronic",
    # --- Added from a census of a real 1,725-sample library. Apple's 30
    # genres are specific enough that most of these land on a real one.
    "nu jazz": "Jazz",
    "soul jazz": "Jazz",
    "bebop": "Jazz",
    "nu disco": "Funk",
    "italo disco": "Funk",
    "city pop": "Funk",
    "future soul": "Modern RnB",
    "electronica": "Electronic",
    "minimal": "Techno",
    "melodic techno": "Techno",
    "bossa nova": "World/Ethnic",
    "samba": "World/Ethnic",
    "cuban": "World/Ethnic",
    "son cubano": "World/Ethnic",
    "latin": "World/Ethnic",
    "afro latin": "World/Ethnic",
    "latin trap": "World/Ethnic",
    "afropop & afrobeats": "World/Ethnic",
    "amapiano": "World/Ethnic",
    "bachata": "World/Ethnic",
    "west african": "World/Ethnic",
    "arabic": "World/Ethnic",
    "dream pop": "Indie",
    "indie pop": "Indie",
    "bedroom pop": "Indie",
    "shoegaze": "Indie",
    "emo": "Indie",
    "funky house": "House",
    "french house": "House",
    "big room house": "House",
    "cloud rap": "Hip Hop",
    "west coast": "Hip Hop",
    "drift phonk": "Hip Hop",
    "hyperpop": "Electronic Pop",
    "witch house": "Experimental",
    "glitch": "Experimental",
    "breaks": "Vintage Breaks",
    "liquid dnb": "Electronic/Dance",
    "neuro": "Electronic/Dance",
    "color bass": "Electronic/Dance",
    "deep dubstep": "Dubstep",
    "psy trance": "Electronic/Dance",
    "acid": "Electronic/Dance",
    "garage": "Electronic/Dance",
    "classical": "Orchestral",
}

SPLICE_TAG_TO_DESCRIPTOR = {
    "acoustic": "Acoustic", "acoustic drums": "Acoustic",
    "acoustic guitar": "Acoustic", "acoustic bass": "Acoustic",
    "electric": "Electric", "electric guitar": "Electric",
    "electric piano": "Electric", "electric bass": "Electric",
    "distorted": "Distorted", "distortion": "Distorted",
    "clean": "Clean", "dry": "Dry",
    "processed": "Processed", "reverse": "Processed",
    "dark": "Dark", "moody": "Dark",
    "melody": "Melodic", "melodic": "Melodic", "synth melody": "Melodic",
    "guitar melody": "Melodic", "keys melody": "Melodic",
    "strings melody": "Melodic",
    "grooves": "Grooving", "groove": "Grooving", "grooving": "Grooving",
    "fills": "Fill", "ensemble": "Ensemble",
    "intense": "Intense", "aggressive": "Intense", "hard": "Intense",
    "relaxed": "Relaxed", "chill": "Relaxed", "mellow": "Relaxed",
    "cheerful": "Cheerful", "happy": "Cheerful", "uplifting": "Cheerful",
    "dissonant": "Dissonant", "arrhythmic": "Arrhythmic",
    "single": "Single", "phrases": "Part", "vocal phrases": "Part",
    # --- Added from a census of a real 1,725-sample library.
    "wet": "Processed",
    "organic": "Acoustic",
    "live sounds": "Acoustic",
    "funky": "Grooving",
    "percussive": "Grooving",
    "tonal": "Melodic",
    "pitched": "Melodic",
    "bright": "Cheerful",
    "dirty": "Distorted",
    "gritty": "Distorted",
    "soft": "Relaxed",
    "peaceful": "Relaxed",
    "dreamy": "Relaxed",
    "warm": "Relaxed",
    "deep": "Dark",
    "hard": "Intense",
    "tight": "Dry",
    "muted": "Dry",
    "sidechained": "Processed",
    "delay": "Processed",
    "reverb": "Processed",
    "filtered": "Processed",
    "tape": "Processed",
    "cassette": "Processed",
    "lofi": "Processed",
    "lo-fi": "Processed",
    "vinyl": "Processed",
    "wah": "Processed",
    "slap": "Grooving",
    "solo": "Single",
    "quartet": "Ensemble",
    "chamber": "Ensemble",
}


# Splice tags for a full arrangement rather than a single instrument. Apple
# ships 767 loops under "Mixed", which is exactly this.
SPLICE_MULTI_INSTRUMENT_TAGS = frozenset({
    "songstarters", "melodic stack", "music", "full", "arrangement",
})


def splice_tags_to_instrument(tags: List[str]) -> Tuple[str, str]:
    """Map a Splice tag list onto an Apple (category, subcategory) pair.

    Splice orders tags most-specific-first, so the first tag that maps wins.
    A named instrument always beats "this is a full arrangement", which is
    only consulted once every tag has failed to name one.
    """
    for tag in tags:
        mapped = SPLICE_TAG_TO_INSTRUMENT.get(tag)
        if mapped:
            category, subcategory = mapped
            if subcategory not in APPLE_CATEGORY_SUBCATEGORIES.get(category, ()):
                subcategory = APPLE_FALLBACK_SUBCATEGORY
            return category, subcategory

    if any(tag in SPLICE_MULTI_INSTRUMENT_TAGS for tag in tags):
        return "Mixed", APPLE_FALLBACK_SUBCATEGORY

    return "Other Instrument", APPLE_FALLBACK_SUBCATEGORY


def splice_genre_to_apple(genre: str, tags: Optional[List[str]] = None) -> str:
    """Map a Splice genre onto one of Apple's thirteen.

    Falls back to scanning the tag list, because the `genre` column is empty
    for a good share of the catalogue while a genre tag is still present.
    """
    mapped = SPLICE_GENRE_TO_APPLE.get((genre or '').strip().lower())
    if mapped:
        return mapped

    for tag in tags or []:
        mapped = SPLICE_GENRE_TO_APPLE.get(tag)
        if mapped:
            return mapped

    return "Other Genre"


def splice_tags_to_descriptors(tags: List[str]) -> str:
    """Apple descriptors implied by a Splice tag list, in Apple's own order."""
    found = {SPLICE_TAG_TO_DESCRIPTOR[t] for t in tags if t in SPLICE_TAG_TO_DESCRIPTOR}
    return ",".join(d for d in APPLE_DESCRIPTORS if d in found)


# Reused rather than rebuilt per sample; it only holds lookup tables.
_FILENAME_EXTRACTOR = MetadataExtractor()


def splice_sample_to_metadata(sample, time_signature: str = "4/4") -> LoopMetadata:
    """Build Apple Loop metadata from a row of Splice's sounds.db.

    Everything here comes from what Splice actually recorded at download time,
    so nothing is inferred from the filename. The one place this tool overrides
    Splice is the key on percussion: Apple's format says drums and percussion
    carry no key signature, and Splice tags plenty of drum hits with one.
    """
    tags = sample.tag_list
    category, subcategory = splice_tags_to_instrument(tags)

    # Splice's tags and the filename are different sources, not rivals. Where
    # the tag list names no instrument -- true of every "songstarter", which
    # carries only genre tags -- the filename usually still does.
    if category in ("Other Instrument", "Mixed"):
        from_name = _FILENAME_EXTRACTOR.extract_instrument(sample.filename)
        if from_name[0] != "Other Instrument":
            category, subcategory = from_name

    key_signature = sample.key_signature
    key_type = sample.key_type
    if category in PERCUSSIVE_APPLE_CATEGORIES:
        key_signature, key_type = '', ''

    tempo = int(round(sample.bpm)) if sample.bpm else None

    return LoopMetadata(
        category=category,
        subcategory=subcategory,
        genre=splice_genre_to_apple(sample.genre, tags),
        time_signature=time_signature,
        key_signature=key_signature,
        key_type=key_type,
        descriptors=splice_tags_to_descriptors(tags),
        tempo=tempo,
        duration=sample.duration or None,
        is_one_shot=not sample.is_loop,
    )


class TablePrinter:
    """Print a formatted table with real-time row output."""

    def __init__(self, columns: List[Tuple[str, int]], separator: str = " | "):
        self.columns = columns
        self.separator = separator
        self.header_printed = False

    def _truncate(self, text: str, width: int) -> str:
        if len(text) <= width:
            return text.ljust(width)
        return text[:width-2] + ".."

    def print_header(self) -> None:
        if self.header_printed:
            return

        header_parts = [self._truncate(name, width) for name, width in self.columns]
        print(self.separator.join(header_parts))

        sep_parts = ["-" * width for _, width in self.columns]
        print(self.separator.join(sep_parts))

        self.header_printed = True

    def print_row(self, values: List[str]) -> None:
        if not self.header_printed:
            self.print_header()

        row_parts = []
        for i, (_, width) in enumerate(self.columns):
            value = values[i] if i < len(values) else ""
            row_parts.append(self._truncate(str(value), width))

        print(self.separator.join(row_parts))

    def print_footer(self, total: int, converted: int = 0, errors: int = 0) -> None:
        sep_parts = ["-" * width for _, width in self.columns]
        print(self.separator.join(sep_parts))
        if errors > 0:
            print(f"Total: {total} files, {converted} converted, {errors} errors")
        else:
            print(f"Total: {total} files, {converted} converted")


def get_convert_table_columns(include_status: bool = True) -> List[Tuple[str, int]]:
    """Table column definitions for conversion output.

    The dry run omits the trailing Markers/Status pair. It shares this
    definition rather than keeping its own copy, so a column added here cannot
    misalign the dry-run headings.
    """
    columns = [
        ("Filename", 26),
        ("Type", 5),
        ("Kind", 6),
        ("Tempo", 6),
        ("Key", 4),
        ("Scale", 6),
        ("Beats", 5),
        ("Duration", 8),
        ("Category", 16),
        ("Genre", 16),
        ("Source", 8),
    ]
    if include_status:
        columns += [("Markers", 7), ("Status", 8)]
    return columns


def metadata_to_table_row(filename: str, metadata: LoopMetadata,
                          markers: int = 0, status: str = "",
                          include_status: bool = True) -> List[str]:
    """Convert metadata to table row values, matching the column definitions."""
    loop_type = "MIDI" if metadata.loop_type == "midi" else "Audio"
    kind = "1-Shot" if metadata.is_one_shot else "Loop"
    tempo = f"{metadata.tempo}" if metadata.tempo else "-"
    key = metadata.key_signature if metadata.key_signature else "-"
    scale = metadata.key_type if metadata.key_type else "-"
    beats = str(metadata.beat_count) if metadata.beat_count > 0 else "-"
    duration = f"{metadata.duration:.2f}s" if metadata.duration else "-"
    category = metadata.subcategory or metadata.category or "-"
    genre = metadata.genre if metadata.genre else "-"
    source = metadata.metadata_source or "-"

    row = [filename, loop_type, kind, tempo, key, scale, beats, duration,
           category, genre, source]
    if include_status:
        row += [str(markers) if markers > 0 else "-", status]
    return row


class AppleLoopConverter:
    """Convert audio and MIDI files to Apple Loop CAF format."""

    def __init__(self, output_dir: Optional[Path] = None, bitrate: int = 256000,
                 lossy: bool = False, use_transient_detection: bool = True,
                 onset_config: Optional[OnsetDetectionConfig] = None,
                 splice_library=None, force_one_shot: Optional[bool] = None,
                 default_time_signature: str = '4/4', splice_only: bool = False,
                 duration_probe=None, overwrite: bool = False):
        self.output_dir = output_dir or Path.home() / "Library/Audio/Apple Loops/User Loops"
        self.bitrate = bitrate
        self.lossy = lossy
        self.extractor = MetadataExtractor()
        self.midi_parser = MIDIParser()
        self.use_transient_detection = use_transient_detection
        self.transient_detector = TransientDetector(onset_config)
        # Optional SpliceLibrary. When a file is in it, its row is the truth.
        self.splice_library = splice_library
        # True forces every file to a one-shot, False forces every file to a
        # loop, None leaves the decision to the database and the filename.
        self.force_one_shot = force_one_shot
        self.default_time_signature = default_time_signature
        # Convert only what the Splice database can vouch for.
        self.splice_only = splice_only
        # How to measure a file's length. Injectable so the behaviour can be
        # tested off macOS, where afinfo does not exist.
        self._duration_probe = duration_probe or self.get_audio_duration
        # Replace an existing output file instead of writing beside it.
        self.overwrite = overwrite
        # Distinct duration-probe failure causes already warned about.
        self._duration_warnings = set()
        # Every file whose length could not be measured, for the run summary.
        self.duration_failures: List[Path] = []

    def resolve_output_path(self, input_file: Path,
                            relative_to: Optional[Path] = None) -> Path:
        """Where this file's .caf should be written.

        Sample libraries are full of repeated basenames -- every pack has a
        Kick_01 -- and a flat output directory used to let the second one
        silently replace the first. Unless overwrite was asked for, a taken
        name gets a numeric suffix.
        """
        if relative_to is not None:
            try:
                parent = self.output_dir / input_file.relative_to(relative_to).parent
            except ValueError:
                parent = self.output_dir
        else:
            parent = self.output_dir

        candidate = parent / f"{input_file.stem}.caf"
        if self.overwrite:
            return candidate

        index = 2
        while candidate.exists():
            candidate = parent / f"{input_file.stem}_{index}.caf"
            index += 1
        return candidate

    def resolve_duration(self, input_file: Path,
                         metadata: LoopMetadata) -> Optional[float]:
        """The file's length in seconds, or None if it cannot be established.

        Measuring the actual file wins; Splice's recorded duration is the
        fallback. Nothing is invented, because duration is half of the tempo
        Logic recovers -- a made-up length is a made-up tempo, shipped with a
        full set of beat markers asserting it.
        """
        measured = self._duration_probe(input_file)
        if measured:
            return measured
        return metadata.duration or None

    def _is_in_output_dir(self, path: Path) -> bool:
        """Whether a candidate input already lives under the output directory."""
        try:
            path.resolve().relative_to(self.output_dir.resolve())
            return True
        except (ValueError, OSError):
            return False

    def should_convert(self, input_file: Path) -> bool:
        """Whether this file passes the --splice-only filter."""
        if not self.splice_only or self.splice_library is None:
            return True
        return self.splice_library.lookup(input_file) is not None

    def metadata_for(self, input_file: Path,
                     midi_info: Optional[MIDIInfo] = None,
                     relative_to: Optional[Path] = None) -> LoopMetadata:
        """Resolve one file's metadata from the best source available.

        Splice's own database first, the filename second. Every code path goes
        through here so a dry run and a real run cannot disagree about what a
        file is.
        """
        input_file = Path(input_file)

        sample = None
        if self.splice_library is not None:
            sample = self.splice_library.lookup(input_file)

        if sample is not None:
            metadata = splice_sample_to_metadata(sample, self.default_time_signature)
            metadata.metadata_source = "splice"
        else:
            try:
                folder = str(input_file.relative_to(relative_to).parent) if relative_to \
                    else str(input_file.parent)
            except ValueError:
                folder = str(input_file.parent)
            metadata = self.extractor.extract_all(input_file.stem, folder, midi_info)
            metadata.metadata_source = "filename"

        if self.force_one_shot is not None:
            metadata.is_one_shot = self.force_one_shot

        if metadata.is_one_shot:
            metadata.beat_count = 0

        return metadata

    def is_midi_file(self, file_path: Path) -> bool:
        """Check if file is a MIDI file."""
        return file_path.suffix.lower() in MIDI_EXTENSIONS

    def get_audio_duration(self, audio_file: Path) -> Optional[float]:
        """Get audio file duration in seconds using afinfo.

        `afinfo` fails two different ways and only one of them raises. On a
        machine without it, the subprocess call throws. On macOS with an
        unreadable file it runs fine and exits non-zero -- which used to return
        None in silence, and since the converter no longer invents a duration
        that meant a file quietly converting with no beat markers and no
        explanation.
        """
        try:
            result = subprocess.run(
                ['afinfo', str(audio_file)],
                capture_output=True,
                text=True,
                timeout=10
            )

            for line in result.stdout.split('\n'):
                if 'estimated duration:' in line.lower():
                    match = re.search(r'(\d+\.?\d*)\s*sec', line)
                    if match:
                        return float(match.group(1))

            self._record_duration_failure(audio_file, self._afinfo_reason(result))
            return None
        except Exception as e:
            self._record_duration_failure(audio_file, str(e))
            return None

    @staticmethod
    def _afinfo_reason(result) -> str:
        """The most informative line afinfo produced, for grouping failures."""
        for stream in (result.stderr, result.stdout):
            for line in (stream or '').splitlines():
                if line.strip().lower().startswith('fail'):
                    return line.strip()
        return f"afinfo exited {result.returncode} without reporting a duration"

    def _record_duration_failure(self, audio_file: Path, cause: str) -> None:
        """Note a file whose length could not be measured.

        Warned once per distinct cause, not per file: a missing afinfo fails
        identically for every file, and one warning each buries the output of a
        few thousand-sample run. Every affected file is still recorded so the
        run can report a total.
        """
        self.duration_failures.append(Path(audio_file))
        if cause not in self._duration_warnings:
            self._duration_warnings.add(cause)
            print(f"Warning: could not determine duration ({cause}). "
                  f"Affected files are converted without beat markers rather "
                  f"than with a guessed length.", file=sys.stderr)

    def calculate_beat_count(self, tempo: int, duration: float,
                             time_signature: str = '4/4') -> int:
        """Calculate beat count from tempo and duration, snapped to a bar line.

        Logic recovers tempo from beat_count / duration, so this number has to
        be the loop's real musical length, not the raw arithmetic.
        """
        raw_beats = (tempo * duration) / 60.0
        return snap_beat_count(raw_beats, beats_per_bar(time_signature))

    def finalize_beat_count(self, metadata: LoopMetadata) -> None:
        """Set the beat count in place, once, from whatever is known.

        Every caller used to do this inline and they had drifted apart -- the
        dry run reported one number and the conversion wrote another.
        """
        if metadata.is_one_shot:
            metadata.beat_count = 0
            return

        if metadata.tempo and metadata.duration:
            metadata.beat_count = self.calculate_beat_count(
                metadata.tempo, metadata.duration, metadata.time_signature
            )

    def create_info_chunk(self, genre: str) -> bytes:
        """Create CAF info chunk with genre for Spotlight indexing."""
        data = struct.pack('>I', 1)
        data += b'genre\x00' + genre.encode('ascii', errors='replace') + b'\x00'
        return data

    def create_uuid_chunk(self, metadata: LoopMetadata) -> bytes:
        """Create Apple Loop metadata UUID chunk."""
        data = bytearray(APPLE_LOOP_META_UUID)

        kv_pairs = []

        if metadata.subcategory:
            kv_pairs.append(('subcategory', metadata.subcategory))
        if metadata.category:
            kv_pairs.append(('category', metadata.category))
        if metadata.key_signature:
            kv_pairs.append(('key signature', metadata.key_signature))
        if metadata.time_signature:
            kv_pairs.append(('time signature', metadata.time_signature))
        if metadata.beat_count > 0 and not metadata.is_one_shot:
            kv_pairs.append(('beat count', str(metadata.beat_count)))
        if metadata.descriptors:
            kv_pairs.append(('descriptors', metadata.descriptors))
        if metadata.genre:
            kv_pairs.append(('genre', metadata.genre))
        if metadata.key_type:
            kv_pairs.append(('key type', metadata.key_type))
        if metadata.loop_type == "midi":
            kv_pairs.append(('loop type', 'midi'))

        data.extend(struct.pack('>I', len(kv_pairs)))

        for key, value in kv_pairs:
            data.extend(key.encode('ascii', errors='replace') + b'\x00')
            data.extend(str(value).encode('ascii', errors='replace') + b'\x00')

        return bytes(data)

    def wants_beat_markers(self, metadata: LoopMetadata) -> bool:
        """Whether this file should carry a beat markers chunk.

        The markers are what let Logic time-stretch to the project tempo, so
        they belong on rhythmic phrases and on nothing else.
        """
        return metadata.beat_count > 0 and not metadata.is_one_shot

    def create_beat_markers_chunk(self, num_valid_frames: int, beat_count: int,
                                   audio_path: Optional[Path] = None,
                                   subdivisions: int = 4) -> bytes:
        """Create Apple Loop beat markers UUID chunk."""
        if self.use_transient_detection and audio_path and not self.is_midi_file(audio_path):
            try:
                marker_positions = self.transient_detector.detect(
                    audio_path, beat_count, num_frames=num_valid_frames
                )
            except (ImportError, Exception):
                marker_positions = self._generate_simple_markers(
                    num_valid_frames, beat_count, subdivisions
                )
        else:
            marker_positions = self._generate_simple_markers(
                num_valid_frames, beat_count, subdivisions
            )

        return self._encode_beat_markers(marker_positions)

    def _generate_simple_markers(self, num_valid_frames: int, beat_count: int,
                                  subdivisions: int) -> List[int]:
        """Generate evenly-spaced markers at quarter-note subdivisions."""
        if beat_count <= 0:
            return [0, num_valid_frames]

        samples_per_beat = num_valid_frames / beat_count
        samples_per_subdivision = samples_per_beat / subdivisions

        total_markers = beat_count * subdivisions + 1
        marker_positions = []

        for i in range(total_markers):
            position = int(round(i * samples_per_subdivision))
            marker_positions.append(min(position, num_valid_frames))

        marker_positions[-1] = num_valid_frames

        return marker_positions

    def _encode_beat_markers(self, marker_positions: List[int]) -> bytes:
        """Encode marker positions into beat markers chunk binary format."""
        data = bytearray(BEAT_MARKERS_UUID)

        header = struct.pack('>I', 0)
        header += struct.pack('>I', 0x00010000)
        header += struct.pack('>H', 0x0032)
        header += struct.pack('>H', 0x0010)
        header += struct.pack('>I', 0)
        header += struct.pack('>I', len(marker_positions))
        data.extend(header)

        for position in marker_positions:
            entry = struct.pack('>H', 0x0001)
            entry += struct.pack('>H', 0x0000)
            entry += struct.pack('>I', 0x0000)
            entry += struct.pack('>I', position)
            data.extend(entry)

        return bytes(data)

    def get_caf_audio_info(self, caf_data: bytes) -> Tuple[Optional[float], Optional[int]]:
        """Extract sample rate and valid frame count from CAF file data."""
        try:
            desc_pos = caf_data.find(b'desc')
            if desc_pos < 0:
                return None, None

            sample_rate = struct.unpack('>d', caf_data[desc_pos+12:desc_pos+20])[0]

            pakt_pos = caf_data.find(b'pakt')
            if pakt_pos < 0:
                return sample_rate, None

            num_valid_frames = struct.unpack('>q', caf_data[pakt_pos+20:pakt_pos+28])[0]

            return sample_rate, num_valid_frames

        except Exception:
            return None, None

    def convert_to_caf(self, input_file: Path, output_file: Path) -> bool:
        """Convert audio file to CAF format using afconvert."""
        try:
            if self.lossy:
                cmd = [
                    'afconvert', '-f', 'caff', '-d', 'aac',
                    '-b', str(self.bitrate), '-q', '127',
                    str(input_file), str(output_file)
                ]
            else:
                cmd = [
                    'afconvert', '-f', 'caff', '-d', 'alac',
                    str(input_file), str(output_file)
                ]

            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                print(f"afconvert error: {result.stderr}", file=sys.stderr)
                return False

            return True

        except FileNotFoundError:
            print("Error: afconvert not found. This tool requires macOS.", file=sys.stderr)
            return False
        except Exception as e:
            print(f"Conversion error: {e}", file=sys.stderr)
            return False

    def inject_metadata(self, caf_file: Path, metadata: LoopMetadata,
                        original_audio_path: Optional[Path] = None) -> bool:
        """Inject Apple Loop metadata and beat markers into CAF file."""
        try:
            with open(caf_file, 'rb') as f:
                caf_data = bytearray(f.read())

            if caf_data[0:4] != b'caff':
                print(f"Error: Not a valid CAF file", file=sys.stderr)
                return False

            sample_rate, num_valid_frames = self.get_caf_audio_info(bytes(caf_data))

            info_pos = caf_data.find(b'info')
            if info_pos >= 0:
                old_size = struct.unpack('>Q', caf_data[info_pos+4:info_pos+12])[0]
                new_info = self.create_info_chunk(metadata.genre)
                new_chunk = b'info' + struct.pack('>Q', len(new_info)) + new_info
                caf_data[info_pos:info_pos+12+old_size] = new_chunk

            data_pos = caf_data.find(b'data')
            if data_pos == -1:
                print(f"Error: Could not find data chunk in CAF file", file=sys.stderr)
                return False

            data_size = struct.unpack('>Q', caf_data[data_pos+4:data_pos+12])[0]
            insert_pos = data_pos + 12 + data_size

            chunks_to_insert = bytearray()

            if info_pos < 0:
                info_data = self.create_info_chunk(metadata.genre)
                chunks_to_insert.extend(b'info' + struct.pack('>Q', len(info_data)) + info_data)

            uuid_data = self.create_uuid_chunk(metadata)
            chunks_to_insert.extend(b'uuid' + struct.pack('>Q', len(uuid_data)) + uuid_data)

            if num_valid_frames and self.wants_beat_markers(metadata):
                beat_markers_data = self.create_beat_markers_chunk(
                    num_valid_frames, metadata.beat_count,
                    audio_path=original_audio_path,
                    subdivisions=4
                )
                chunks_to_insert.extend(b'uuid' + struct.pack('>Q', len(beat_markers_data)) + beat_markers_data)

            caf_data[insert_pos:insert_pos] = chunks_to_insert

            with open(caf_file, 'wb') as f:
                f.write(caf_data)

            return True

        except Exception as e:
            print(f"Error injecting metadata: {e}", file=sys.stderr)
            return False

    def create_midi_caf(self, midi_info: MIDIInfo, metadata: LoopMetadata) -> bytes:
        """Create a CAF file with embedded MIDI data."""
        caf_data = bytearray(CAF_HEADER)

        # desc chunk (virtual audio format for MIDI)
        desc_data = struct.pack('>d', 44100.0)  # Sample rate
        desc_data += b'midi'  # Format ID
        desc_data += struct.pack('>I', 0)  # Format flags
        desc_data += struct.pack('>I', 0)  # Bytes per packet
        desc_data += struct.pack('>I', 0)  # Frames per packet
        desc_data += struct.pack('>I', 2)  # Channels per frame
        desc_data += struct.pack('>I', 0)  # Bits per channel
        caf_data.extend(b'desc')
        caf_data.extend(struct.pack('>Q', len(desc_data)))
        caf_data.extend(desc_data)

        # midi chunk
        caf_data.extend(b'midi')
        caf_data.extend(struct.pack('>Q', len(midi_info.raw_data)))
        caf_data.extend(midi_info.raw_data)

        # info chunk
        info_data = self.create_info_chunk(metadata.genre)
        caf_data.extend(b'info')
        caf_data.extend(struct.pack('>Q', len(info_data)))
        caf_data.extend(info_data)

        # UUID chunk with Apple Loop metadata
        uuid_data = self.create_uuid_chunk(metadata)
        caf_data.extend(b'uuid')
        caf_data.extend(struct.pack('>Q', len(uuid_data)))
        caf_data.extend(uuid_data)

        # UUID chunk with beat markers
        if self.wants_beat_markers(metadata) and metadata.duration and metadata.duration > 0:
            VIRTUAL_SAMPLE_RATE = 44100
            total_frames = int(metadata.duration * VIRTUAL_SAMPLE_RATE)
            marker_positions = self._generate_simple_markers(
                total_frames, metadata.beat_count, 4
            )
            beat_markers_data = self._encode_beat_markers(marker_positions)
            caf_data.extend(b'uuid')
            caf_data.extend(struct.pack('>Q', len(beat_markers_data)))
            caf_data.extend(beat_markers_data)

        return bytes(caf_data)

    def convert_file(self, input_file: Path, output_file: Optional[Path] = None,
                     metadata: Optional[LoopMetadata] = None,
                     metadata_overrides: Optional[Dict] = None) -> Optional[Path]:
        """Convert a single audio or MIDI file to Apple Loop format."""
        input_file = Path(input_file)

        if not input_file.exists():
            print(f"Error: Input file not found: {input_file}", file=sys.stderr)
            return None

        if output_file is None:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            output_file = self.resolve_output_path(input_file)
            output_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            output_file = Path(output_file)
            output_file.parent.mkdir(parents=True, exist_ok=True)

        is_midi = self.is_midi_file(input_file)

        if is_midi:
            return self._convert_midi_file(input_file, output_file, metadata, metadata_overrides)
        else:
            return self._convert_audio_file(input_file, output_file, metadata, metadata_overrides)

    def _convert_midi_file(self, input_file: Path, output_file: Path,
                           metadata: Optional[LoopMetadata] = None,
                           metadata_overrides: Optional[Dict] = None) -> Optional[Path]:
        """Convert a MIDI file to Apple Loop CAF format."""
        try:
            midi_info = self.midi_parser.parse_file(input_file)

            if metadata is None:
                metadata = self.metadata_for(input_file, midi_info)

            if metadata_overrides:
                for key, value in metadata_overrides.items():
                    if hasattr(metadata, key) and value is not None:
                        setattr(metadata, key, value)

            metadata.duration = midi_info.duration
            metadata.loop_type = "midi"

            if not metadata.beat_count and midi_info.beat_count:
                metadata.beat_count = midi_info.beat_count
            self.finalize_beat_count(metadata)

            caf_data = self.create_midi_caf(midi_info, metadata)

            with open(output_file, 'wb') as f:
                f.write(caf_data)

            return output_file

        except Exception as e:
            print(f"Error converting MIDI file {input_file}: {e}", file=sys.stderr)
            return None

    def _convert_audio_file(self, input_file: Path, output_file: Path,
                            metadata: Optional[LoopMetadata] = None,
                            metadata_overrides: Optional[Dict] = None) -> Optional[Path]:
        """Convert an audio file to Apple Loop CAF format."""
        if metadata is None:
            metadata = self.metadata_for(input_file)

        if metadata_overrides:
            for key, value in metadata_overrides.items():
                if hasattr(metadata, key) and value is not None:
                    setattr(metadata, key, value)

        metadata.duration = self.resolve_duration(input_file, metadata)
        metadata.loop_type = "audio"

        if metadata.duration is None and metadata.tempo:
            print(f"Warning: could not determine duration of {input_file.name}; "
                  f"writing it without beat markers rather than guessing a "
                  f"length. It will not follow project tempo.", file=sys.stderr)

        self.finalize_beat_count(metadata)

        temp_caf = tempfile.NamedTemporaryFile(suffix='.caf', delete=False)
        temp_caf.close()

        try:
            if not self.convert_to_caf(input_file, Path(temp_caf.name)):
                return None

            if not self.inject_metadata(Path(temp_caf.name), metadata,
                                        original_audio_path=input_file):
                return None

            import shutil
            shutil.move(temp_caf.name, output_file)

            return output_file

        finally:
            if os.path.exists(temp_caf.name):
                os.remove(temp_caf.name)

    def convert_directory(self, input_dir: Path, recursive: bool = True,
                          preserve_structure: bool = False,
                          extensions: Optional[Tuple[str, ...]] = None,
                          use_table: bool = True,
                          verbose: bool = False) -> Dict:
        """Convert all audio and MIDI files in a directory."""
        input_dir = Path(input_dir)
        extensions = extensions or SUPPORTED_EXTENSIONS

        stats = {'total': 0, 'converted': 0, 'skipped': 0, 'errors': 0,
                 'audio': 0, 'midi': 0, 'loops': 0, 'one_shots': 0,
                 'from_splice': 0}

        pattern = '**/*' if recursive else '*'
        files = []
        for ext in extensions:
            files.extend(input_dir.glob(f"{pattern}{ext}"))
            files.extend(input_dir.glob(f"{pattern}{ext.upper()}"))

        # A real Splice library contains directories whose names end in .wav,
        # and a glob matches those exactly like a file. Files already inside
        # the output directory are skipped too: .caf is a supported input, so
        # an output directory nested in the input one otherwise makes every
        # run re-convert its own output (observed: 6 files, then 12, then 24).
        files = sorted(f for f in set(files)
                       if f.is_file() and not self._is_in_output_dir(f))
        stats['total'] = len(files)

        table = None
        if use_table and not verbose:
            table = TablePrinter(get_convert_table_columns())
            table.print_header()

        for i, input_file in enumerate(files, 1):
            if not self.should_convert(input_file):
                stats['skipped'] += 1
                continue

            output_file = self.resolve_output_path(
                input_file, relative_to=input_dir if preserve_structure else None)

            is_midi = self.is_midi_file(input_file)
            midi_info = None

            if is_midi:
                try:
                    midi_info = self.midi_parser.parse_file(input_file)
                except Exception:
                    pass

            metadata = self.metadata_for(input_file, midi_info, relative_to=input_dir)

            if verbose:
                print(f"\n[{i}/{stats['total']}] {input_file.name}")
                print(f"  Type: {'MIDI' if is_midi else 'Audio'}"
                      f"{' one-shot' if metadata.is_one_shot else ''}")
                print(f"  Metadata from: {metadata.metadata_source}")
                print(f"  Tempo: {metadata.tempo or 'Unknown'} BPM")
                print(f"  Key: {metadata.key_signature or 'None'} {metadata.key_type}")
                print(f"  Category: {metadata.category}")
                print(f"  Subcategory: {metadata.subcategory}")
                print(f"  Genre: {metadata.genre}")
                print(f"  Descriptors: {metadata.descriptors or 'None'}")

            result = self.convert_file(input_file, output_file, metadata)

            if result:
                if verbose:
                    print(f"  → {result}")
                elif table:
                    markers = metadata.beat_count * 4 + 1 \
                        if self.wants_beat_markers(metadata) else 0
                    table.print_row(metadata_to_table_row(
                        input_file.name, metadata, markers, "OK"
                    ))
                stats['converted'] += 1
                if is_midi:
                    stats['midi'] += 1
                else:
                    stats['audio'] += 1
                if metadata.is_one_shot:
                    stats['one_shots'] += 1
                else:
                    stats['loops'] += 1
                if metadata.metadata_source == 'splice':
                    stats['from_splice'] += 1
            else:
                if verbose:
                    print(f"  ✗ Conversion failed")
                elif table:
                    table.print_row(metadata_to_table_row(
                        input_file.name, metadata, 0, "FAILED"
                    ))
                stats['errors'] += 1

        if table:
            table.print_footer(stats['total'], stats['converted'], stats['errors'])
            print(f"  (Audio: {stats['audio']}, MIDI: {stats['midi']}, "
                  f"Loops: {stats['loops']}, One-shots: {stats['one_shots']})")
            if self.splice_library is not None:
                print(f"  Metadata from Splice: {stats['from_splice']}/"
                      f"{stats['converted']}")
            if stats['skipped']:
                print(f"  Skipped (not in Splice library): {stats['skipped']}")
            if self.duration_failures:
                print(f"  No measurable duration (converted without beat "
                      f"markers): {len(self.duration_failures)}")

        return stats


def main():
    parser = argparse.ArgumentParser(
        description='Convert audio and MIDI files to Apple Loop CAF format',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Convert a single audio file with auto-detected metadata
  %(prog)s input.wav

  # Convert a MIDI file
  %(prog)s input.mid

  # Convert with explicit metadata
  %(prog)s input.wav -o output.caf --tempo 120 --key Am --category Bass

  # Convert a directory (auto-detects audio and MIDI files)
  %(prog)s /path/to/loops/ --output-dir ~/Music/Apple\\ Loops/

  # Dry run to preview metadata extraction
  %(prog)s /path/to/loops/ --dry-run

  # Lossy AAC conversion for audio (smaller files)
  %(prog)s input.wav --lossy

Supported audio formats: WAV, AIFF, MP3, M4A, AAC, FLAC, ALAC, CAF, OGG, WMA
Supported MIDI formats: .mid, .midi, .smf
Default audio codec: ALAC (Apple Lossless)
        """
    )

    parser.add_argument('input', type=Path,
                        help='Input audio/MIDI file or directory')
    parser.add_argument('-o', '--output', type=Path,
                        help='Output file path (for single file conversion)')
    parser.add_argument('--output-dir', type=Path,
                        help='Output directory (default: ~/Library/Audio/Apple Loops/User Loops/)')

    # Metadata options
    parser.add_argument('--tempo', type=int, metavar='BPM',
                        help='Override tempo in BPM')
    parser.add_argument('--key', type=str, metavar='KEY',
                        help='Override key signature (e.g., "Am", "F#", "Bb")')
    parser.add_argument('--category', type=str,
                        help='Override instrument category')
    parser.add_argument('--subcategory', type=str,
                        help='Override instrument subcategory')
    parser.add_argument('--genre', type=str,
                        help='Override genre')
    parser.add_argument('--descriptors', type=str,
                        help='Override descriptors (comma-separated)')
    parser.add_argument('--time-signature', type=str, default='4/4',
                        help='Time signature (default: 4/4)')
    parser.add_argument('--beat-count', type=int,
                        help='Override beat count')

    # Splice library integration
    splice_group = parser.add_argument_group(
        'Splice library',
        "Read tempo, key, genre, tags and loop/one-shot status from the Splice "
        "desktop app's own sounds.db instead of guessing them from filenames."
    )
    splice_group.add_argument('--splice-db', type=str, metavar='PATH',
                              nargs='?', const='auto',
                              help='Path to Splice sounds.db, or pass with no '
                                   'value to search the usual locations')
    splice_group.add_argument('--splice-only', action='store_true',
                              help='Skip files that have no row in sounds.db')

    # Loop vs one-shot
    kind_group = parser.add_mutually_exclusive_group()
    kind_group.add_argument('--one-shot', dest='force_one_shot',
                            action='store_true', default=None,
                            help='Treat every input as a one-shot: no beat '
                                 'count, no beat markers, no time-stretching')
    kind_group.add_argument('--loop', dest='force_one_shot',
                            action='store_false',
                            help='Treat every input as a loop')

    # Conversion options
    parser.add_argument('--bitrate', type=int, default=256000,
                        help='AAC bitrate in bps, only used with --lossy (default: 256000)')
    parser.add_argument('--lossy', action='store_true',
                        help='Use lossy AAC encoding instead of lossless ALAC (audio only)')
    parser.add_argument('--recursive', action='store_true', default=True,
                        help='Process subdirectories recursively (default: True)')
    parser.add_argument('--no-recursive', dest='recursive', action='store_false',
                        help='Do not process subdirectories')
    parser.add_argument('--preserve-structure', action='store_true',
                        help='Preserve directory structure in output')
    parser.add_argument('--overwrite', action='store_true',
                        help='Replace an existing .caf of the same name. By '
                             'default a colliding name gets a numeric suffix, '
                             'because sample packs repeat basenames')
    parser.add_argument('--extensions', type=str,
                        help='Comma-separated list of file extensions to process')
    parser.add_argument('--audio-only', action='store_true',
                        help='Only process audio files (skip MIDI)')
    parser.add_argument('--midi-only', action='store_true',
                        help='Only process MIDI files (skip audio)')

    # Output format options
    parser.add_argument('--table', '-t', action='store_true',
                        help='Force table output (default for directories)')
    parser.add_argument('--detailed', '-d', action='store_true',
                        help='Force detailed output instead of table')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview metadata extraction without converting')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Verbose output')

    # Transient detection options (audio only)
    parser.add_argument('--no-transient-detection', action='store_true',
                        help='Disable transient detection, use simple quarter-note markers')
    parser.add_argument('--onset-threshold', type=float, default=0.3,
                        metavar='FLOAT',
                        help='Onset detection threshold (0.0-1.0, default: 0.3)')
    parser.add_argument('--min-markers-per-beat', type=float, default=0.0,
                        metavar='FLOAT',
                        help='Pad sparse marker sets out to this many markers '
                             'per beat. Off by default, which matches Apple: '
                             'it ships 13%% of its loops with fewer markers '
                             'than beats because pads have nothing to slice')
    parser.add_argument('--onset-hop-length', type=int, default=512,
                        metavar='INT',
                        help='Hop length for onset detection STFT (default: 512)')

    args = parser.parse_args()

    # Parse key override
    key_signature = ''
    key_type = ''
    if args.key:
        extractor = MetadataExtractor()
        key_signature, key_type = extractor.extract_key(args.key)
        if not key_signature:
            key_signature = args.key.upper()
            key_type = 'minor' if 'm' in args.key.lower() else 'major'

    # Build metadata overrides
    overrides = {}
    if args.tempo:
        overrides['tempo'] = args.tempo
    if key_signature:
        overrides['key_signature'] = key_signature
        overrides['key_type'] = key_type
    if args.category:
        overrides['category'] = args.category
    if args.subcategory:
        overrides['subcategory'] = args.subcategory
    if args.genre:
        overrides['genre'] = args.genre
    if args.descriptors:
        overrides['descriptors'] = args.descriptors
    if args.time_signature:
        overrides['time_signature'] = args.time_signature
    if args.beat_count:
        overrides['beat_count'] = args.beat_count

    # Parse extensions
    extensions = None
    if args.extensions:
        extensions = tuple(
            ext.strip() if ext.startswith('.') else f'.{ext.strip()}'
            for ext in args.extensions.split(',')
        )
    elif args.audio_only:
        extensions = AUDIO_EXTENSIONS
    elif args.midi_only:
        extensions = MIDI_EXTENSIONS

    # Determine output directory
    output_dir = args.output_dir
    if output_dir is None:
        output_dir = Path.home() / "Library/Audio/Apple Loops/User Loops"

    # Create onset detection config
    onset_config = OnsetDetectionConfig(
        hop_length=args.onset_hop_length,
        threshold=args.onset_threshold,
        min_markers_per_beat=args.min_markers_per_beat
    )

    # Open the Splice library, if asked for
    splice_library = None
    if args.splice_db:
        from splice_db import SpliceLibrary, find_sounds_db

        if args.splice_db == 'auto':
            db_path = find_sounds_db()
            if db_path is None:
                print("Error: no Splice sounds.db found. Pass an explicit path, "
                      "or export one from the Splice app: Settings -> Download "
                      "logs, then users/default/<username>/sounds.db.",
                      file=sys.stderr)
                sys.exit(1)
        else:
            db_path = Path(args.splice_db).expanduser()

        try:
            splice_library = SpliceLibrary(db_path)
        except (FileNotFoundError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

        print(f"Splice library: {db_path} ({len(splice_library)} downloaded samples)")
    elif args.splice_only:
        print("Error: --splice-only needs --splice-db.", file=sys.stderr)
        sys.exit(1)

    # Create converter
    converter = AppleLoopConverter(
        output_dir=output_dir,
        bitrate=args.bitrate,
        lossy=args.lossy,
        use_transient_detection=not args.no_transient_detection,
        onset_config=onset_config,
        splice_library=splice_library,
        force_one_shot=args.force_one_shot,
        default_time_signature=args.time_signature or '4/4',
        splice_only=args.splice_only,
        overwrite=args.overwrite,
    )

    def resolve_metadata(path: Path, relative_to: Optional[Path] = None) -> LoopMetadata:
        """Metadata exactly as the conversion would compute it.

        A dry run that disagrees with the run it previews is worse than no dry
        run at all, so both go through the same three calls.
        """
        midi_info = None
        if converter.is_midi_file(path):
            try:
                midi_info = converter.midi_parser.parse_file(path)
            except Exception:
                pass

        metadata = converter.metadata_for(path, midi_info, relative_to=relative_to)
        for key, value in overrides.items():
            if hasattr(metadata, key):
                setattr(metadata, key, value)

        if midi_info is not None:
            metadata.duration = midi_info.duration
            if not metadata.beat_count and midi_info.beat_count:
                metadata.beat_count = midi_info.beat_count
        else:
            metadata.duration = converter.resolve_duration(path, metadata)

        converter.finalize_beat_count(metadata)
        return metadata

    # Process input
    if args.input.is_file():
        # Single file conversion
        is_midi = converter.is_midi_file(args.input)

        if not converter.should_convert(args.input):
            print(f"Skipped (not in the Splice library): {args.input.name}")
            sys.exit(0)

        if args.dry_run:
            metadata = resolve_metadata(args.input)

            print(f"File: {args.input.name}")
            print(f"  Type: {'MIDI' if is_midi else 'Audio'}")
            print(f"  Kind: {'One-shot' if metadata.is_one_shot else 'Loop'}")
            print(f"  Metadata from: {metadata.metadata_source}")
            print(f"  Duration: {metadata.duration:.2f}s" if metadata.duration else "  Duration: Unknown")
            print(f"  Tempo: {metadata.tempo or 'Unknown'} BPM")
            print(f"  Beat Count: {metadata.beat_count}")
            print(f"  Beat Markers: {'yes' if converter.wants_beat_markers(metadata) else 'no'}")
            print(f"  Key: {metadata.key_signature or 'None'} {metadata.key_type}")
            print(f"  Category: {metadata.category}")
            print(f"  Subcategory: {metadata.subcategory}")
            print(f"  Genre: {metadata.genre}")
            print(f"  Time Signature: {metadata.time_signature}")
            print(f"  Descriptors: {metadata.descriptors or 'None'}")
            if is_midi:
                try:
                    midi_info = converter.midi_parser.parse_file(args.input)
                    print(f"  MIDI Tracks: {midi_info.num_tracks}")
                    print(f"  MIDI Notes: {midi_info.num_notes}")
                except Exception:
                    pass
        else:
            result = converter.convert_file(
                args.input,
                args.output,
                metadata_overrides=overrides
            )
            if result:
                print(f"Created: {result}")
                sys.exit(0)
            else:
                sys.exit(1)

    elif args.input.is_dir():
        # Directory conversion
        use_table = not args.detailed

        if args.dry_run:
            print(f"Scanning: {args.input}")
            print(f"Output to: {output_dir}\n")

            exts = extensions or SUPPORTED_EXTENSIONS
            pattern = '**/*' if args.recursive else '*'
            files = []
            for ext in exts:
                files.extend(args.input.glob(f"{pattern}{ext}"))
                files.extend(args.input.glob(f"{pattern}{ext.upper()}"))

            files = sorted(f for f in set(files)
                           if f.is_file() and not converter._is_in_output_dir(f))
            audio_count = sum(1 for f in files if f.suffix.lower() in AUDIO_EXTENSIONS)
            midi_count = sum(1 for f in files if f.suffix.lower() in MIDI_EXTENSIONS)
            print(f"Found {len(files)} files (Audio: {audio_count}, MIDI: {midi_count})\n")

            selected = [f for f in files if converter.should_convert(f)]
            skipped = len(files) - len(selected)

            counts = {'loops': 0, 'one_shots': 0, 'from_splice': 0}

            if use_table:
                table = TablePrinter(get_convert_table_columns(include_status=False))
                table.print_header()

            for f in selected:
                metadata = resolve_metadata(f, relative_to=args.input)

                counts['one_shots' if metadata.is_one_shot else 'loops'] += 1
                if metadata.metadata_source == 'splice':
                    counts['from_splice'] += 1

                if use_table:
                    table.print_row(metadata_to_table_row(
                        f.name, metadata, include_status=False))
                else:
                    kind = 'One-shot' if metadata.is_one_shot else 'Loop'
                    print(f"{f.name} [{'MIDI' if converter.is_midi_file(f) else 'Audio'}, {kind}]")
                    print(f"  Metadata from: {metadata.metadata_source}")
                    print(f"  Tempo: {metadata.tempo or 'Unknown'} BPM → {metadata.beat_count} beats")
                    print(f"  Beat markers: {'yes' if converter.wants_beat_markers(metadata) else 'no'}")
                    print(f"  Key: {metadata.key_signature or 'None'} {metadata.key_type}")
                    print(f"  {metadata.category} / {metadata.subcategory}")
                    print(f"  {metadata.genre}")
                    print()

            if use_table:
                table.print_footer(len(selected), 0, 0)

            print(f"  (Loops: {counts['loops']}, One-shots: {counts['one_shots']})")
            if splice_library is not None:
                print(f"  Metadata from Splice: {counts['from_splice']}/{len(selected)}")
            if skipped:
                print(f"  Skipped (not in Splice library): {skipped}")
        else:
            stats = converter.convert_directory(
                args.input,
                recursive=args.recursive,
                preserve_structure=args.preserve_structure,
                extensions=extensions,
                use_table=use_table,
                verbose=args.detailed
            )

            if args.detailed:
                print("\n" + "=" * 60)
                print("CONVERSION SUMMARY")
                print("=" * 60)
                print(f"Total files: {stats['total']}")
                print(f"Converted: {stats['converted']} (Audio: {stats['audio']}, MIDI: {stats['midi']})")
                print(f"Errors: {stats['errors']}")

            sys.exit(0 if stats['errors'] == 0 else 1)

    else:
        print(f"Error: Input not found: {args.input}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
