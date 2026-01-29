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
import numpy as np


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


@dataclass
class LoopMetadata:
    """Apple Loop metadata structure."""
    category: str = "Other Instrument"
    subcategory: str = "Other"
    genre: str = "Other Genre"
    beat_count: int = 0
    time_signature: str = "4/4"
    key_signature: str = ""  # Empty for drums/percussion
    key_type: str = ""  # major, minor, both, neither
    descriptors: str = ""
    tempo: Optional[int] = None  # Used for beat_count calculation
    duration: Optional[float] = None  # Duration in seconds
    loop_type: str = "audio"  # "audio" or "midi"


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
    min_markers_per_beat: float = 1.0


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
            min_markers = max(beat_count + 1, int(beat_count * self.config.min_markers_per_beat) + 1)

        return self._build_marker_list(onset_samples, num_frames, beat_count, min_markers)

    def _build_marker_list(self, onsets: np.ndarray, num_frames: int,
                           beat_count: int, min_markers: int) -> List[int]:
        markers = [0]

        for onset in onsets:
            if onset > 0 and onset < num_frames:
                markers.append(int(onset))

        markers.append(num_frames)
        markers = sorted(set(markers))

        if len(markers) < min_markers:
            markers = self._add_fallback_markers(markers, num_frames, min_markers)

        return markers

    def _add_fallback_markers(self, existing: List[int], num_frames: int,
                              min_markers: int) -> List[int]:
        if min_markers <= 1:
            return existing

        interval = num_frames / (min_markers - 1)

        for i in range(min_markers):
            pos = int(round(i * interval))
            existing.append(min(pos, num_frames))

        return sorted(set(existing))


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
        'strings': ('Strings', 'Ensemble Strings'),
        'violin': ('Strings', 'Violin'),
        'viola': ('Strings', 'Viola'),
        'cello': ('Strings', 'Cello'),
        'orchestral': ('Strings', 'Ensemble Strings'),
        'brass': ('Horn/Wind', 'Brass Section'),
        'horn': ('Horn/Wind', 'French Horn'),
        'horns': ('Horn/Wind', 'Brass Section'),
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
        'clap': ('Percussion', 'Clap'),
        'claps': ('Percussion', 'Clap'),
        'vibraphone': ('Mallets', 'Vibraphone'),
        'vibes': ('Mallets', 'Vibraphone'),
        'marimba': ('Mallets', 'Marimba'),
        'xylophone': ('Mallets', 'Xylophone'),
        'glockenspiel': ('Mallets', 'Glockenspiel'),
        'vocal': ('Vocals', 'Male'),
        'vocals': ('Vocals', 'Male'),
        'voice': ('Vocals', 'Male'),
        'vox': ('Vocals', 'Male'),
        'choir': ('Vocals', 'Choir'),
        'fx': ('Sound Effect', 'Motions & Transitions'),
        'effect': ('Sound Effect', 'Motions & Transitions'),
        'effects': ('Sound Effect', 'Motions & Transitions'),
        'riser': ('Sound Effect', 'Motions & Transitions'),
        'sweep': ('Sound Effect', 'Motions & Transitions'),
        'impact': ('Sound Effect', 'Motions & Transitions'),
        'hit': ('Sound Effect', 'Motions & Transitions'),
        'transition': ('Sound Effect', 'Motions & Transitions'),
        'ambient': ('Texture/Atmosphere', 'Ambient'),
        'atmosphere': ('Texture/Atmosphere', 'Ambient'),
        'texture': ('Texture/Atmosphere', 'Ambient'),
        'drone': ('Texture/Atmosphere', 'Ambient'),
        'noise': ('Texture/Atmosphere', 'Ambient'),
    }

    # MIDI program to category/subcategory mapping (General MIDI)
    PROGRAM_MAP = {
        range(0, 8): ('Keyboards', 'Piano'),
        range(8, 16): ('Mallets', 'Vibraphone'),
        range(16, 24): ('Keyboards', 'Organ'),
        range(24, 32): ('Guitars', 'Electric Guitar'),
        range(32, 40): ('Bass', 'Electric Bass'),
        range(40, 48): ('Strings', 'Ensemble Strings'),
        range(48, 56): ('Strings', 'Ensemble Strings'),
        range(56, 64): ('Horn/Wind', 'Brass Section'),
        range(64, 72): ('Horn/Wind', 'Saxophone'),
        range(72, 80): ('Horn/Wind', 'Flute'),
        range(80, 88): ('Keyboards', 'Synthesizer'),
        range(88, 96): ('Keyboards', 'Synthesizer'),
        range(96, 104): ('Sound Effect', 'Motions & Transitions'),
        range(104, 112): ('World/Ethnic', 'Other'),
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

    def extract_key(self, text: str) -> Tuple[str, str]:
        """Extract key signature and key type from text."""
        text_clean = text.replace('_', ' ').replace('-', ' ')

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

        return 'Other Instrument', 'Other'

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

        return metadata


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


def get_convert_table_columns() -> List[Tuple[str, int]]:
    """Get table column definitions for conversion output."""
    return [
        ("Filename", 30),
        ("Type", 5),
        ("Tempo", 7),
        ("Key", 5),
        ("Scale", 7),
        ("Beats", 5),
        ("Duration", 8),
        ("Category", 18),
        ("Genre", 18),
        ("Markers", 7),
        ("Status", 8),
    ]


def metadata_to_table_row(filename: str, metadata: LoopMetadata,
                          markers: int = 0, status: str = "") -> List[str]:
    """Convert metadata to table row values."""
    loop_type = "MIDI" if metadata.loop_type == "midi" else "Audio"
    tempo = f"{metadata.tempo}" if metadata.tempo else "-"
    key = metadata.key_signature if metadata.key_signature else "-"
    scale = metadata.key_type if metadata.key_type else "-"
    beats = str(metadata.beat_count) if metadata.beat_count > 0 else "-"
    duration = f"{metadata.duration:.2f}s" if metadata.duration else "-"
    category = metadata.subcategory or metadata.category or "-"
    genre = metadata.genre if metadata.genre else "-"
    markers_str = str(markers) if markers > 0 else "-"

    return [filename, loop_type, tempo, key, scale, beats, duration, category, genre, markers_str, status]


class AppleLoopConverter:
    """Convert audio and MIDI files to Apple Loop CAF format."""

    def __init__(self, output_dir: Optional[Path] = None, bitrate: int = 256000,
                 lossy: bool = False, use_transient_detection: bool = True,
                 onset_config: Optional[OnsetDetectionConfig] = None):
        self.output_dir = output_dir or Path.home() / "Library/Audio/Apple Loops/User Loops"
        self.bitrate = bitrate
        self.lossy = lossy
        self.extractor = MetadataExtractor()
        self.midi_parser = MIDIParser()
        self.use_transient_detection = use_transient_detection
        self.transient_detector = TransientDetector(onset_config)

    def is_midi_file(self, file_path: Path) -> bool:
        """Check if file is a MIDI file."""
        return file_path.suffix.lower() in MIDI_EXTENSIONS

    def get_audio_duration(self, audio_file: Path) -> Optional[float]:
        """Get audio file duration in seconds using afinfo."""
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

            return None
        except Exception as e:
            print(f"Warning: Could not get duration: {e}", file=sys.stderr)
            return None

    def calculate_beat_count(self, tempo: int, duration: float) -> int:
        """Calculate beat count from tempo and duration."""
        return int(round((tempo * duration) / 60.0))

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
        if metadata.beat_count > 0:
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

    def create_beat_markers_chunk(self, num_valid_frames: int, beat_count: int,
                                   audio_path: Optional[Path] = None,
                                   subdivisions: int = 4) -> bytes:
        """Create Apple Loop beat markers UUID chunk."""
        if self.use_transient_detection and audio_path and not self.is_midi_file(audio_path):
            try:
                marker_positions = self.transient_detector.detect(
                    audio_path, beat_count, num_frames=num_valid_frames,
                    min_markers=beat_count + 1
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

            if num_valid_frames and metadata.beat_count > 0:
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
        if metadata.beat_count > 0 and metadata.duration and metadata.duration > 0:
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
            output_file = self.output_dir / f"{input_file.stem}.caf"
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
                metadata = self.extractor.extract_all(
                    input_file.stem, str(input_file.parent), midi_info
                )

            if metadata_overrides:
                for key, value in metadata_overrides.items():
                    if hasattr(metadata, key) and value is not None:
                        setattr(metadata, key, value)

            metadata.duration = midi_info.duration
            metadata.loop_type = "midi"

            if metadata.tempo and metadata.duration:
                metadata.beat_count = self.calculate_beat_count(metadata.tempo, metadata.duration)
            elif midi_info.beat_count:
                metadata.beat_count = midi_info.beat_count

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
            metadata = self.extractor.extract_all(input_file.stem, str(input_file.parent))

        if metadata_overrides:
            for key, value in metadata_overrides.items():
                if hasattr(metadata, key) and value is not None:
                    setattr(metadata, key, value)

        metadata.duration = self.get_audio_duration(input_file)
        if metadata.duration is None:
            metadata.duration = 4.0

        metadata.loop_type = "audio"

        if metadata.tempo and metadata.duration:
            metadata.beat_count = self.calculate_beat_count(metadata.tempo, metadata.duration)

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
                 'audio': 0, 'midi': 0}

        pattern = '**/*' if recursive else '*'
        files = []
        for ext in extensions:
            files.extend(input_dir.glob(f"{pattern}{ext}"))
            files.extend(input_dir.glob(f"{pattern}{ext.upper()}"))

        files = sorted(set(files))
        stats['total'] = len(files)

        table = None
        if use_table and not verbose:
            table = TablePrinter(get_convert_table_columns())
            table.print_header()

        for i, input_file in enumerate(files, 1):
            if preserve_structure:
                rel_path = input_file.relative_to(input_dir)
                output_file = self.output_dir / rel_path.parent / f"{input_file.stem}.caf"
            else:
                output_file = self.output_dir / f"{input_file.stem}.caf"

            is_midi = self.is_midi_file(input_file)
            midi_info = None

            if is_midi:
                try:
                    midi_info = self.midi_parser.parse_file(input_file)
                except Exception:
                    pass

            metadata = self.extractor.extract_all(
                input_file.stem,
                str(input_file.relative_to(input_dir).parent),
                midi_info
            )

            if verbose:
                print(f"\n[{i}/{stats['total']}] {input_file.name}")
                print(f"  Type: {'MIDI' if is_midi else 'Audio'}")
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
                    markers = metadata.beat_count * 4 + 1 if metadata.beat_count > 0 else 0
                    table.print_row(metadata_to_table_row(
                        input_file.name, metadata, markers, "OK"
                    ))
                stats['converted'] += 1
                if is_midi:
                    stats['midi'] += 1
                else:
                    stats['audio'] += 1
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
            print(f"  (Audio: {stats['audio']}, MIDI: {stats['midi']})")

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
    parser.add_argument('--min-markers-per-beat', type=float, default=1.0,
                        metavar='FLOAT',
                        help='Minimum markers per beat (default: 1.0)')
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

    # Create converter
    converter = AppleLoopConverter(
        output_dir=output_dir,
        bitrate=args.bitrate,
        lossy=args.lossy,
        use_transient_detection=not args.no_transient_detection,
        onset_config=onset_config
    )

    # Process input
    if args.input.is_file():
        # Single file conversion
        is_midi = converter.is_midi_file(args.input)
        midi_info = None

        if is_midi:
            try:
                midi_info = converter.midi_parser.parse_file(args.input)
            except Exception:
                pass

        if args.dry_run:
            metadata = converter.extractor.extract_all(
                args.input.stem, str(args.input.parent), midi_info
            )
            for key, value in overrides.items():
                if hasattr(metadata, key):
                    setattr(metadata, key, value)

            if not is_midi:
                metadata.duration = converter.get_audio_duration(args.input)
            elif midi_info:
                metadata.duration = midi_info.duration

            if metadata.tempo and metadata.duration:
                metadata.beat_count = converter.calculate_beat_count(metadata.tempo, metadata.duration)

            print(f"File: {args.input.name}")
            print(f"  Type: {'MIDI' if is_midi else 'Audio'}")
            print(f"  Duration: {metadata.duration:.2f}s" if metadata.duration else "  Duration: Unknown")
            print(f"  Tempo: {metadata.tempo or 'Unknown'} BPM")
            print(f"  Beat Count: {metadata.beat_count}")
            print(f"  Key: {metadata.key_signature or 'None'} {metadata.key_type}")
            print(f"  Category: {metadata.category}")
            print(f"  Subcategory: {metadata.subcategory}")
            print(f"  Genre: {metadata.genre}")
            print(f"  Time Signature: {metadata.time_signature}")
            print(f"  Descriptors: {metadata.descriptors or 'None'}")
            if is_midi and midi_info:
                print(f"  MIDI Tracks: {midi_info.num_tracks}")
                print(f"  MIDI Notes: {midi_info.num_notes}")
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

            files = sorted(set(files))
            audio_count = sum(1 for f in files if f.suffix.lower() in AUDIO_EXTENSIONS)
            midi_count = sum(1 for f in files if f.suffix.lower() in MIDI_EXTENSIONS)
            print(f"Found {len(files)} files (Audio: {audio_count}, MIDI: {midi_count})\n")

            if use_table:
                dry_run_columns = [
                    ("Filename", 30), ("Type", 5), ("Tempo", 7), ("Key", 5),
                    ("Scale", 7), ("Beats", 5), ("Duration", 8), ("Category", 18), ("Genre", 18),
                ]
                table = TablePrinter(dry_run_columns)
                table.print_header()

                for f in files:
                    is_midi = converter.is_midi_file(f)
                    midi_info = None

                    if is_midi:
                        try:
                            midi_info = converter.midi_parser.parse_file(f)
                        except Exception:
                            pass

                    metadata = converter.extractor.extract_all(f.stem, str(f.parent), midi_info)
                    for key, value in overrides.items():
                        if hasattr(metadata, key):
                            setattr(metadata, key, value)

                    if not is_midi:
                        duration = converter.get_audio_duration(f)
                        metadata.duration = duration
                    elif midi_info:
                        metadata.duration = midi_info.duration

                    if metadata.tempo and metadata.duration:
                        metadata.beat_count = converter.calculate_beat_count(metadata.tempo, metadata.duration)

                    row = metadata_to_table_row(f.name, metadata, 0, "")[:-2]
                    table.print_row(row)

                table.print_footer(len(files), 0, 0)
            else:
                for f in files:
                    is_midi = converter.is_midi_file(f)
                    midi_info = None

                    if is_midi:
                        try:
                            midi_info = converter.midi_parser.parse_file(f)
                        except Exception:
                            pass

                    metadata = converter.extractor.extract_all(f.stem, str(f.parent), midi_info)
                    for key, value in overrides.items():
                        if hasattr(metadata, key):
                            setattr(metadata, key, value)

                    if not is_midi:
                        duration = converter.get_audio_duration(f)
                    elif midi_info:
                        duration = midi_info.duration
                    else:
                        duration = None

                    if metadata.tempo and duration:
                        metadata.beat_count = converter.calculate_beat_count(metadata.tempo, duration)

                    print(f"{f.name} [{'MIDI' if is_midi else 'Audio'}]")
                    print(f"  Tempo: {metadata.tempo or 'Unknown'} BPM → {metadata.beat_count} beats")
                    print(f"  Key: {metadata.key_signature or 'None'} {metadata.key_type}")
                    print(f"  {metadata.category} / {metadata.subcategory}")
                    print(f"  {metadata.genre}")
                    print()
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
