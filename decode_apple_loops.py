#!/usr/bin/env python3
"""
Decode and display Apple Loop metadata from CAF, AIFF, and MIDI files.

This tool reads Apple Loop files and extracts all embedded metadata including:
- Loop metadata (category, genre, key, tempo, beat count, etc.)
- Beat marker positions for time-stretching
- Audio format information (sample rate, channels, codec)
- MIDI information (tracks, notes, programs) for MIDI loops
- Spotlight indexing metadata

Supports:
- Modern CAF-based Apple Loops (audio and MIDI)
- Legacy AIFF-based Apple Loops
- Standard MIDI Files (.mid, .midi)

Usage:
    # Decode a single file
    decode_apple_loops.py loop.caf

    # Decode a MIDI file
    decode_apple_loops.py loop.mid

    # Decode with JSON output
    decode_apple_loops.py loop.caf --json

    # Decode all loops in a directory
    decode_apple_loops.py /path/to/loops/ --recursive

    # Show beat marker positions
    decode_apple_loops.py loop.caf --show-markers

See APPLE_LOOPS_FORMAT.md for detailed format documentation.
"""

import os
import sys
import struct
import argparse
import json
import subprocess
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any
from dataclasses import dataclass, field, asdict


# Apple Loop metadata UUID (CAF format)
APPLE_LOOP_META_UUID = bytes.fromhex('29819273b5bf4aefb78d62d1ef90bb2c')

# Apple Loop beat markers UUID (CAF format)
BEAT_MARKERS_UUID = bytes.fromhex('0352811b9d5d42e1882d6af61a6b330c')

# Supported file extensions
AUDIO_EXTENSIONS = ('.caf', '.aif', '.aiff')
MIDI_EXTENSIONS = ('.mid', '.midi', '.smf')
SUPPORTED_EXTENSIONS = AUDIO_EXTENSIONS + MIDI_EXTENSIONS


@dataclass
class AudioInfo:
    """Audio format information."""
    sample_rate: float = 0.0
    channels: int = 0
    bits_per_sample: int = 0
    codec: str = ""
    duration: float = 0.0
    num_frames: int = 0
    file_size: int = 0


@dataclass
class LoopMetadata:
    """Apple Loop metadata structure."""
    category: str = ""
    subcategory: str = ""
    genre: str = ""
    beat_count: int = 0
    time_signature: str = ""
    key_signature: str = ""
    key_type: str = ""
    descriptors: str = ""
    tempo: float = 0.0
    # Legacy AIFF fields
    loopable: int = 0
    root_note_midi: int = 0
    scale_type: int = 0


@dataclass
class BeatMarkers:
    """Beat marker information."""
    marker_count: int = 0
    positions: List[int] = field(default_factory=list)
    positions_seconds: List[float] = field(default_factory=list)


@dataclass
class SpotlightInfo:
    """Spotlight indexing metadata from info chunk."""
    entries: Dict[str, str] = field(default_factory=dict)


@dataclass
class MIDIInfo:
    """MIDI file information."""
    present: bool = False
    data_size: int = 0
    tracks: int = 0
    ticks_per_beat: int = 0
    notes: int = 0
    tempo: int = 0
    duration: float = 0.0
    key_signature: str = ""
    time_signature: str = ""
    programs: List[int] = field(default_factory=list)


@dataclass
class AppleLoopInfo:
    """Complete Apple Loop file information."""
    file_path: str = ""
    file_format: str = ""  # "CAF", "AIFF", or "MIDI"
    loop_type: str = ""  # "audio" or "midi"
    audio: AudioInfo = field(default_factory=AudioInfo)
    midi: MIDIInfo = field(default_factory=MIDIInfo)
    metadata: LoopMetadata = field(default_factory=LoopMetadata)
    beat_markers: BeatMarkers = field(default_factory=BeatMarkers)
    spotlight: SpotlightInfo = field(default_factory=SpotlightInfo)
    raw_chunks: Dict[str, int] = field(default_factory=dict)  # chunk_name -> size


class AppleLoopDecoder:
    """Decode Apple Loop files (CAF and AIFF formats)."""

    # MIDI note to key name mapping
    NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

    # Scale type mapping (AIFF basc chunk)
    SCALE_TYPES = {
        0: 'neither',
        1: 'minor',
        2: 'major',
        3: 'neither',
        4: 'both'
    }

    # CAF audio format codes
    CAF_FORMAT_CODES = {
        'lpcm': 'Linear PCM',
        'ima4': 'IMA 4:1 ADPCM',
        'aac ': 'AAC',
        'aac': 'AAC',
        'alac': 'Apple Lossless',
        'mp3 ': 'MP3',
        '.mp3': 'MP3',
        'ulaw': 'uLaw 2:1',
        'alaw': 'aLaw 2:1',
    }

    def __init__(self, verbose: bool = False):
        """Initialize decoder."""
        self.verbose = verbose

    # MIDI key signature mapping
    KEY_SIGNATURES = {
        (-7, 0): 'Cb', (-6, 0): 'Gb', (-5, 0): 'Db', (-4, 0): 'Ab',
        (-3, 0): 'Eb', (-2, 0): 'Bb', (-1, 0): 'F', (0, 0): 'C',
        (1, 0): 'G', (2, 0): 'D', (3, 0): 'A', (4, 0): 'E',
        (5, 0): 'B', (6, 0): 'F#', (7, 0): 'C#',
        (-7, 1): 'Abm', (-6, 1): 'Ebm', (-5, 1): 'Bbm', (-4, 1): 'Fm',
        (-3, 1): 'Cm', (-2, 1): 'Gm', (-1, 1): 'Dm', (0, 1): 'Am',
        (1, 1): 'Em', (2, 1): 'Bm', (3, 1): 'F#m', (4, 1): 'C#m',
        (5, 1): 'G#m', (6, 1): 'D#m', (7, 1): 'A#m',
    }

    def decode_file(self, file_path: Path) -> AppleLoopInfo:
        """
        Decode an Apple Loop or MIDI file.

        Args:
            file_path: Path to the audio/MIDI file

        Returns:
            AppleLoopInfo with all extracted metadata
        """
        file_path = Path(file_path)
        info = AppleLoopInfo(file_path=str(file_path))
        info.audio.file_size = file_path.stat().st_size

        with open(file_path, 'rb') as f:
            data = f.read()

        # Detect file format
        if data[:4] == b'caff':
            info.file_format = 'CAF'
            self._decode_caf(data, info)
        elif data[:4] == b'FORM' and data[8:12] == b'AIFF':
            info.file_format = 'AIFF'
            self._decode_aiff(data, info)
        elif data[:4] == b'MThd':
            info.file_format = 'MIDI'
            info.loop_type = 'midi'
            self._decode_midi(data, info)
        else:
            raise ValueError(f"Unknown file format: {file_path}")

        # Determine loop type based on content
        if not info.loop_type:
            info.loop_type = 'midi' if info.midi.present else 'audio'

        # Calculate tempo if we have beat count and duration
        duration = info.audio.duration or info.midi.duration
        if info.metadata.beat_count > 0 and duration > 0:
            info.metadata.tempo = round(
                (info.metadata.beat_count * 60) / duration, 2
            )
        elif info.midi.tempo > 0 and info.metadata.tempo == 0:
            info.metadata.tempo = info.midi.tempo

        # Convert beat marker positions to seconds
        sample_rate = info.audio.sample_rate if info.audio.sample_rate > 0 else 44100
        if info.beat_markers.positions:
            info.beat_markers.positions_seconds = [
                round(pos / sample_rate, 6)
                for pos in info.beat_markers.positions
            ]

        return info

    def _decode_caf(self, data: bytes, info: AppleLoopInfo) -> None:
        """Decode CAF (Core Audio Format) file."""
        # CAF header: 'caff' (4) + version (2) + flags (2) = 8 bytes
        if len(data) < 8:
            raise ValueError("Invalid CAF file: too short")

        version = struct.unpack('>H', data[4:6])[0]
        if self.verbose:
            print(f"  CAF version: {version}")

        # Parse chunks
        pos = 8
        while pos < len(data) - 12:
            chunk_type = data[pos:pos+4].decode('ascii', errors='replace')
            chunk_size = struct.unpack('>Q', data[pos+4:pos+12])[0]

            info.raw_chunks[chunk_type] = chunk_size

            if chunk_type == 'desc':
                self._decode_caf_desc(data[pos+12:pos+12+chunk_size], info)
            elif chunk_type == 'pakt':
                self._decode_caf_pakt(data[pos+12:pos+12+chunk_size], info)
            elif chunk_type == 'info':
                self._decode_caf_info(data[pos+12:pos+12+chunk_size], info)
            elif chunk_type == 'uuid':
                self._decode_caf_uuid(data[pos+12:pos+12+chunk_size], info)
            elif chunk_type == 'midi':
                self._decode_caf_midi(data[pos+12:pos+12+chunk_size], info)

            pos += 12 + chunk_size

    def _decode_caf_desc(self, data: bytes, info: AppleLoopInfo) -> None:
        """Decode CAF desc (audio description) chunk."""
        if len(data) < 32:
            return

        info.audio.sample_rate = struct.unpack('>d', data[0:8])[0]
        format_id = data[8:12].decode('ascii', errors='replace').strip()
        format_flags = struct.unpack('>I', data[12:16])[0]
        bytes_per_packet = struct.unpack('>I', data[16:20])[0]
        frames_per_packet = struct.unpack('>I', data[20:24])[0]
        info.audio.channels = struct.unpack('>I', data[24:28])[0]
        info.audio.bits_per_sample = struct.unpack('>I', data[28:32])[0]

        info.audio.codec = self.CAF_FORMAT_CODES.get(format_id, format_id)

    def _decode_caf_pakt(self, data: bytes, info: AppleLoopInfo) -> None:
        """Decode CAF pakt (packet table) chunk for duration calculation."""
        if len(data) < 24:
            return

        num_packets = struct.unpack('>q', data[0:8])[0]
        num_valid_frames = struct.unpack('>q', data[8:16])[0]
        priming_frames = struct.unpack('>i', data[16:20])[0]
        remainder_frames = struct.unpack('>i', data[20:24])[0]

        info.audio.num_frames = num_valid_frames
        if info.audio.sample_rate > 0:
            info.audio.duration = num_valid_frames / info.audio.sample_rate

    def _decode_caf_info(self, data: bytes, info: AppleLoopInfo) -> None:
        """Decode CAF info chunk (Spotlight metadata)."""
        if len(data) < 4:
            return

        num_entries = struct.unpack('>I', data[0:4])[0]
        pos = 4

        for _ in range(num_entries):
            # Read key (null-terminated)
            key_end = data.find(b'\x00', pos)
            if key_end == -1:
                break
            key = data[pos:key_end].decode('ascii', errors='replace')
            pos = key_end + 1

            # Read value (null-terminated)
            val_end = data.find(b'\x00', pos)
            if val_end == -1:
                break
            value = data[pos:val_end].decode('ascii', errors='replace')
            pos = val_end + 1

            info.spotlight.entries[key] = value

            # Also set genre in metadata if found
            if key == 'genre':
                info.metadata.genre = value

    def _decode_caf_uuid(self, data: bytes, info: AppleLoopInfo) -> None:
        """Decode CAF uuid chunk (Apple Loop metadata or beat markers)."""
        if len(data) < 16:
            return

        uuid = data[0:16]

        if uuid == APPLE_LOOP_META_UUID:
            self._decode_apple_loop_metadata(data[16:], info)
        elif uuid == BEAT_MARKERS_UUID:
            self._decode_beat_markers(data[16:], info)

    def _decode_apple_loop_metadata(self, data: bytes, info: AppleLoopInfo) -> None:
        """Decode Apple Loop metadata UUID chunk."""
        if len(data) < 4:
            return

        num_pairs = struct.unpack('>I', data[0:4])[0]
        pos = 4

        for _ in range(num_pairs):
            # Read key (null-terminated)
            key_end = data.find(b'\x00', pos)
            if key_end == -1:
                break
            key = data[pos:key_end].decode('ascii', errors='replace')
            pos = key_end + 1

            # Read value (null-terminated)
            val_end = data.find(b'\x00', pos)
            if val_end == -1:
                break
            value = data[pos:val_end].decode('ascii', errors='replace')
            pos = val_end + 1

            # Map to metadata fields
            if key == 'category':
                info.metadata.category = value
            elif key == 'subcategory':
                info.metadata.subcategory = value
            elif key == 'genre':
                info.metadata.genre = value
            elif key == 'beat count':
                info.metadata.beat_count = int(value) if value.isdigit() else 0
            elif key == 'time signature':
                info.metadata.time_signature = value
            elif key == 'key signature':
                info.metadata.key_signature = value
            elif key == 'key type':
                info.metadata.key_type = value
            elif key == 'descriptors':
                info.metadata.descriptors = value

    def _decode_beat_markers(self, data: bytes, info: AppleLoopInfo) -> None:
        """Decode beat markers UUID chunk."""
        if len(data) < 20:
            return

        # Header: 20 bytes
        # [0-3]:   Unknown (always 0)
        # [4-7]:   Flags (always 0x00010000)
        # [8-9]:   Version? (always 0x0032)
        # [10-11]: Unknown (always 0x0010)
        # [12-15]: Unknown (always 0)
        # [16-19]: Marker count

        marker_count = struct.unpack('>I', data[16:20])[0]
        info.beat_markers.marker_count = marker_count

        # Each marker entry is 12 bytes:
        # [0-1]:  Flags (0x0001)
        # [2-3]:  Padding
        # [4-7]:  Padding
        # [8-11]: Sample position

        pos = 20
        for _ in range(marker_count):
            if pos + 12 > len(data):
                break
            position = struct.unpack('>I', data[pos+8:pos+12])[0]
            info.beat_markers.positions.append(position)
            pos += 12

    def _decode_caf_midi(self, data: bytes, info: AppleLoopInfo) -> None:
        """Decode CAF midi chunk containing embedded MIDI data."""
        if len(data) < 14:
            return

        info.midi.present = True
        info.midi.data_size = len(data)
        info.loop_type = 'midi'

        # Parse embedded MIDI file
        self._parse_midi_data(data, info)

    def _decode_midi(self, data: bytes, info: AppleLoopInfo) -> None:
        """Decode standalone MIDI file."""
        info.midi.present = True
        info.midi.data_size = len(data)
        self._parse_midi_data(data, info)

    def _parse_midi_data(self, data: bytes, info: AppleLoopInfo) -> None:
        """Parse MIDI file data and extract metadata."""
        if len(data) < 14 or data[:4] != b'MThd':
            return

        header_length = struct.unpack('>I', data[4:8])[0]
        format_type = struct.unpack('>H', data[8:10])[0]
        num_tracks = struct.unpack('>H', data[10:12])[0]
        division = struct.unpack('>H', data[12:14])[0]

        info.midi.tracks = num_tracks
        if not (division & 0x8000):
            info.midi.ticks_per_beat = division

        pos = 8 + header_length
        total_ticks = 0
        tempo = 500000  # Default 120 BPM
        programs = set()

        for _ in range(num_tracks):
            if pos + 8 > len(data) or data[pos:pos+4] != b'MTrk':
                break

            track_length = struct.unpack('>I', data[pos+4:pos+8])[0]
            track_end = pos + 8 + track_length
            track_pos = pos + 8
            track_ticks = 0

            while track_pos < track_end and track_pos < len(data):
                # Parse delta time (variable length)
                delta = 0
                while track_pos < track_end:
                    byte = data[track_pos]
                    track_pos += 1
                    delta = (delta << 7) | (byte & 0x7F)
                    if not (byte & 0x80):
                        break
                track_ticks += delta

                if track_pos >= track_end:
                    break

                status = data[track_pos]

                if status == 0xFF:  # Meta event
                    if track_pos + 2 >= len(data):
                        break
                    meta_type = data[track_pos + 1]
                    meta_length = data[track_pos + 2]
                    track_pos += 3

                    if meta_type == 0x51 and meta_length == 3:  # Tempo
                        if track_pos + 3 <= len(data):
                            tempo = (data[track_pos] << 16 |
                                    data[track_pos + 1] << 8 |
                                    data[track_pos + 2])
                            info.midi.tempo = round(60000000 / tempo)
                    elif meta_type == 0x58 and meta_length >= 2:  # Time signature
                        if track_pos + 2 <= len(data):
                            num = data[track_pos]
                            denom = 2 ** data[track_pos + 1]
                            info.midi.time_signature = f"{num}/{denom}"
                    elif meta_type == 0x59 and meta_length == 2:  # Key signature
                        if track_pos + 2 <= len(data):
                            sf = data[track_pos]
                            if sf > 127:
                                sf -= 256
                            mi = data[track_pos + 1]
                            key = self.KEY_SIGNATURES.get((sf, mi), "")
                            if key:
                                info.midi.key_signature = key

                    track_pos += meta_length
                elif status >= 0xF0:  # System events
                    track_pos += 1
                    if status == 0xF0 or status == 0xF7:  # SysEx
                        while track_pos < track_end and data[track_pos] != 0xF7:
                            track_pos += 1
                        track_pos += 1
                else:  # Channel events
                    if status >= 0x80:
                        track_pos += 1
                        if status >= 0x80 and status < 0xC0:
                            track_pos += 2
                            if status >= 0x90 and status < 0xA0:
                                info.midi.notes += 1
                        elif status >= 0xC0 and status < 0xE0:
                            if status >= 0xC0 and status < 0xD0:
                                if track_pos < len(data):
                                    programs.add(data[track_pos])
                            track_pos += 1
                        elif status >= 0xE0:
                            track_pos += 2
                    else:
                        track_pos += 1

            total_ticks = max(total_ticks, track_ticks)
            pos = track_end

        # Calculate duration
        if info.midi.ticks_per_beat > 0 and tempo > 0:
            info.midi.duration = total_ticks * (tempo / 1e6) / info.midi.ticks_per_beat

        info.midi.programs = sorted(programs)

    def _decode_aiff(self, data: bytes, info: AppleLoopInfo) -> None:
        """Decode AIFF file format."""
        # AIFF header: 'FORM' (4) + size (4) + 'AIFF' (4) = 12 bytes
        if len(data) < 12:
            raise ValueError("Invalid AIFF file: too short")

        # Parse chunks
        pos = 12
        while pos < len(data) - 8:
            chunk_type = data[pos:pos+4].decode('ascii', errors='replace')
            chunk_size = struct.unpack('>I', data[pos+4:pos+8])[0]

            info.raw_chunks[chunk_type] = chunk_size

            if chunk_type == 'COMM':
                self._decode_aiff_comm(data[pos+8:pos+8+chunk_size], info)
            elif chunk_type == 'basc':
                self._decode_aiff_basc(data[pos+8:pos+8+chunk_size], info)
            elif chunk_type == 'cate':
                self._decode_aiff_cate(data[pos+8:pos+8+chunk_size], info)

            # AIFF chunks are padded to even byte boundaries
            pos += 8 + chunk_size
            if chunk_size % 2 == 1:
                pos += 1

    def _decode_aiff_comm(self, data: bytes, info: AppleLoopInfo) -> None:
        """Decode AIFF COMM (common) chunk."""
        if len(data) < 18:
            return

        info.audio.channels = struct.unpack('>H', data[0:2])[0]
        num_frames = struct.unpack('>I', data[2:6])[0]
        info.audio.bits_per_sample = struct.unpack('>H', data[6:8])[0]

        # Sample rate is stored as 80-bit extended precision float
        # Simplified extraction (works for common rates)
        exponent = struct.unpack('>H', data[8:10])[0]
        mantissa = struct.unpack('>Q', data[10:18])[0]
        if exponent == 0 and mantissa == 0:
            info.audio.sample_rate = 0
        else:
            # Convert 80-bit extended to double
            sign = (exponent >> 15) & 1
            exp = (exponent & 0x7FFF) - 16383
            info.audio.sample_rate = mantissa * (2.0 ** (exp - 63))
            if sign:
                info.audio.sample_rate = -info.audio.sample_rate

        info.audio.num_frames = num_frames
        info.audio.codec = 'Linear PCM'

        if info.audio.sample_rate > 0:
            info.audio.duration = num_frames / info.audio.sample_rate

    def _decode_aiff_basc(self, data: bytes, info: AppleLoopInfo) -> None:
        """
        Decode AIFF basc chunk (Apple Loop basic info).

        Structure (84 bytes total):
        [0-3]:   Loopable flag (1 = loop, 0 = one-shot)
        [4-7]:   Number of beats
        [8-9]:   Root key (MIDI note 0-127, or 0 for drums)
        [10-11]: Scale type (0=neither, 1=minor, 2=major, 3=neither, 4=both)
        [12-13]: Time signature numerator
        [14-15]: Time signature denominator
        [16-83]: Reserved/unknown
        """
        if len(data) < 16:
            return

        info.metadata.loopable = struct.unpack('>I', data[0:4])[0]
        info.metadata.beat_count = struct.unpack('>I', data[4:8])[0]
        info.metadata.root_note_midi = struct.unpack('>H', data[8:10])[0]
        info.metadata.scale_type = struct.unpack('>H', data[10:12])[0]
        time_sig_num = struct.unpack('>H', data[12:14])[0]
        time_sig_denom = struct.unpack('>H', data[14:16])[0]

        # Convert root note to key name
        if info.metadata.root_note_midi == 0:
            info.metadata.key_signature = ""
            info.metadata.key_type = "neither"
        else:
            note_index = (info.metadata.root_note_midi - 48) % 12
            info.metadata.key_signature = self.NOTE_NAMES[note_index]
            info.metadata.key_type = self.SCALE_TYPES.get(
                info.metadata.scale_type, 'neither'
            )

        if time_sig_num > 0 and time_sig_denom > 0:
            info.metadata.time_signature = f"{time_sig_num}/{time_sig_denom}"

    def _decode_aiff_cate(self, data: bytes, info: AppleLoopInfo) -> None:
        """
        Decode AIFF cate chunk (category/instrument info).

        Structure varies, but typically contains null-terminated strings
        for instrument category and subcategory.
        """
        if len(data) < 4:
            return

        # Try to extract null-terminated strings
        pos = 0
        strings = []

        while pos < len(data):
            null_pos = data.find(b'\x00', pos)
            if null_pos == -1:
                break
            s = data[pos:null_pos].decode('ascii', errors='ignore').strip()
            if s:
                strings.append(s)
            pos = null_pos + 1

        # Map extracted strings to metadata
        if len(strings) >= 1:
            info.metadata.category = strings[0]
        if len(strings) >= 2:
            info.metadata.subcategory = strings[1]
        if len(strings) >= 3:
            info.metadata.genre = strings[2]

    def get_audio_info_afinfo(self, file_path: Path) -> Optional[AudioInfo]:
        """
        Get audio info using macOS afinfo command.

        This provides additional/fallback information when parsing fails.
        """
        try:
            result = subprocess.run(
                ['afinfo', str(file_path)],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return None

            info = AudioInfo()

            for line in result.stdout.split('\n'):
                line_lower = line.lower()
                if 'sample rate:' in line_lower:
                    match = re.search(r'(\d+\.?\d*)', line)
                    if match:
                        info.sample_rate = float(match.group(1))
                elif 'channels:' in line_lower:
                    match = re.search(r'(\d+)', line)
                    if match:
                        info.channels = int(match.group(1))
                elif 'estimated duration:' in line_lower:
                    match = re.search(r'(\d+\.?\d*)\s*sec', line)
                    if match:
                        info.duration = float(match.group(1))
                elif 'data format:' in line_lower:
                    # Extract codec info
                    parts = line.split(':')
                    if len(parts) > 1:
                        info.codec = parts[1].strip()

            return info

        except Exception:
            return None


def format_duration(seconds: float) -> str:
    """Format duration as MM:SS.mmm"""
    if seconds <= 0:
        return "0:00.000"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}:{secs:06.3f}"


def format_file_size(size: int) -> str:
    """Format file size with appropriate unit."""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.2f} MB"


class TablePrinter:
    """Print a formatted table with real-time row output."""

    def __init__(self, columns: List[Tuple[str, int]], separator: str = " | "):
        """
        Initialize table printer.

        Args:
            columns: List of (header_name, width) tuples
            separator: Column separator string
        """
        self.columns = columns
        self.separator = separator
        self.header_printed = False

    def _truncate(self, text: str, width: int) -> str:
        """Truncate text to fit within width."""
        if len(text) <= width:
            return text.ljust(width)
        return text[:width-2] + ".."

    def print_header(self) -> None:
        """Print table header row."""
        if self.header_printed:
            return

        # Header row
        header_parts = [self._truncate(name, width) for name, width in self.columns]
        header_line = self.separator.join(header_parts)
        print(header_line)

        # Separator row
        sep_parts = ["-" * width for _, width in self.columns]
        sep_line = self.separator.join(sep_parts)
        print(sep_line)

        self.header_printed = True

    def print_row(self, values: List[str]) -> None:
        """Print a single data row."""
        if not self.header_printed:
            self.print_header()

        row_parts = []
        for i, (_, width) in enumerate(self.columns):
            value = values[i] if i < len(values) else ""
            row_parts.append(self._truncate(str(value), width))

        print(self.separator.join(row_parts))

    def print_footer(self, total: int, errors: int = 0) -> None:
        """Print table footer with summary."""
        sep_parts = ["-" * width for _, width in self.columns]
        sep_line = self.separator.join(sep_parts)
        print(sep_line)
        if errors > 0:
            print(f"Total: {total} files ({errors} errors)")
        else:
            print(f"Total: {total} files")


def print_loop_info(info: AppleLoopInfo, show_markers: bool = False) -> None:
    """Print Apple Loop information in formatted text."""
    print("=" * 80)
    print(f"File: {Path(info.file_path).name}")
    print(f"Path: {info.file_path}")
    print("=" * 80)

    # File format info
    loop_type_display = f" ({info.loop_type.upper()} Loop)" if info.loop_type else ""
    print(f"\nFORMAT: {info.file_format}{loop_type_display}")
    print("-" * 40)
    print(f"  File size:      {format_file_size(info.audio.file_size)}")
    print(f"  Codec:          {info.audio.codec}")
    print(f"  Sample rate:    {info.audio.sample_rate:.0f} Hz")
    print(f"  Channels:       {info.audio.channels}")
    if info.audio.bits_per_sample > 0:
        print(f"  Bit depth:      {info.audio.bits_per_sample}-bit")
    print(f"  Duration:       {format_duration(info.audio.duration)} ({info.audio.duration:.3f}s)")
    if info.audio.num_frames > 0:
        print(f"  Total frames:   {info.audio.num_frames:,}")

    # Loop metadata
    print(f"\nLOOP METADATA:")
    print("-" * 40)
    if info.metadata.tempo > 0:
        print(f"  Tempo:          {info.metadata.tempo:.1f} BPM")
    if info.metadata.beat_count > 0:
        print(f"  Beat count:     {info.metadata.beat_count}")
    if info.metadata.time_signature:
        print(f"  Time signature: {info.metadata.time_signature}")
    if info.metadata.key_signature:
        print(f"  Key:            {info.metadata.key_signature} {info.metadata.key_type}")
    elif info.metadata.key_type:
        print(f"  Key type:       {info.metadata.key_type}")
    if info.metadata.category:
        print(f"  Category:       {info.metadata.category}")
    if info.metadata.subcategory:
        print(f"  Subcategory:    {info.metadata.subcategory}")
    if info.metadata.genre:
        print(f"  Genre:          {info.metadata.genre}")
    if info.metadata.descriptors:
        print(f"  Descriptors:    {info.metadata.descriptors}")

    # MIDI information
    if info.midi.present:
        print(f"\nMIDI INFORMATION:")
        print("-" * 40)
        print(f"  Data size:      {info.midi.data_size:,} bytes")
        print(f"  Tracks:         {info.midi.tracks}")
        print(f"  Ticks/beat:     {info.midi.ticks_per_beat}")
        print(f"  Notes:          {info.midi.notes}")
        if info.midi.tempo > 0:
            print(f"  Tempo:          {info.midi.tempo} BPM")
        if info.midi.duration > 0:
            print(f"  Duration:       {info.midi.duration:.3f}s")
        if info.midi.time_signature:
            print(f"  Time signature: {info.midi.time_signature}")
        if info.midi.key_signature:
            print(f"  Key signature:  {info.midi.key_signature}")
        if info.midi.programs:
            print(f"  Programs:       {info.midi.programs}")

    # Legacy AIFF fields
    if info.file_format == 'AIFF':
        print(f"\nAIFF LEGACY FIELDS:")
        print("-" * 40)
        print(f"  Loopable:       {info.metadata.loopable}")
        if info.metadata.root_note_midi > 0:
            print(f"  Root note MIDI: {info.metadata.root_note_midi}")
        print(f"  Scale type:     {info.metadata.scale_type}")

    # Beat markers
    if info.beat_markers.marker_count > 0:
        print(f"\nBEAT MARKERS: {info.beat_markers.marker_count} markers")
        print("-" * 40)
        if show_markers and info.beat_markers.positions:
            for i, (pos, secs) in enumerate(zip(
                info.beat_markers.positions,
                info.beat_markers.positions_seconds
            )):
                print(f"  [{i:3d}] Sample {pos:>10,}  ({secs:.6f}s)")
        else:
            first_pos = info.beat_markers.positions[0] if info.beat_markers.positions else 0
            last_pos = info.beat_markers.positions[-1] if info.beat_markers.positions else 0
            print(f"  First marker:   {first_pos:,} samples")
            print(f"  Last marker:    {last_pos:,} samples")
            if not show_markers:
                print(f"  (use --show-markers to see all positions)")

    # Spotlight metadata
    if info.spotlight.entries:
        print(f"\nSPOTLIGHT METADATA:")
        print("-" * 40)
        for key, value in info.spotlight.entries.items():
            print(f"  {key}: {value}")

    # Raw chunks
    if info.raw_chunks:
        print(f"\nRAW CHUNKS:")
        print("-" * 40)
        for chunk_name, size in info.raw_chunks.items():
            print(f"  {chunk_name}: {size:,} bytes")

    print()


def get_table_columns() -> List[Tuple[str, int]]:
    """Get table column definitions."""
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
    ]


def info_to_table_row(info: AppleLoopInfo) -> List[str]:
    """Convert AppleLoopInfo to table row values."""
    filename = Path(info.file_path).name

    # Format loop type
    loop_type = info.loop_type.upper() if info.loop_type else "Audio"
    if loop_type == "AUDIO":
        loop_type = "Audio"
    elif loop_type == "MIDI":
        loop_type = "MIDI"

    # Format tempo
    tempo = f"{info.metadata.tempo:.0f}" if info.metadata.tempo > 0 else "-"

    # Format key
    key = info.metadata.key_signature if info.metadata.key_signature else "-"
    if not key and info.midi.key_signature:
        key = info.midi.key_signature

    # Format scale/key type
    scale = info.metadata.key_type if info.metadata.key_type else "-"

    # Format beats
    beats = str(info.metadata.beat_count) if info.metadata.beat_count > 0 else "-"

    # Format duration
    duration_val = info.audio.duration if info.audio.duration > 0 else info.midi.duration
    duration = f"{duration_val:.2f}s" if duration_val > 0 else "-"

    # Category (prefer subcategory if available)
    category = info.metadata.subcategory or info.metadata.category or "-"

    # Genre
    genre = info.metadata.genre if info.metadata.genre else "-"

    # Markers count
    markers = str(info.beat_markers.marker_count) if info.beat_markers.marker_count > 0 else "-"

    return [filename, loop_type, tempo, key, scale, beats, duration, category, genre, markers]


def main():
    parser = argparse.ArgumentParser(
        description='Decode and display Apple Loop metadata',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Decode a single file (detailed output)
  %(prog)s loop.caf

  # Decode directory with table output (default for multiple files)
  %(prog)s /path/to/loops/ --recursive

  # Force table output for single file
  %(prog)s loop.caf --table

  # Force detailed output for directory
  %(prog)s /path/to/loops/ --detailed

  # Decode with JSON output
  %(prog)s loop.caf --json

  # Show all beat marker positions
  %(prog)s loop.caf --show-markers

Supported formats: CAF (modern Apple Loops), AIFF (legacy Apple Loops)
        """
    )

    parser.add_argument('input', type=Path,
                        help='Input audio file or directory')
    parser.add_argument('--json', '-j', action='store_true',
                        help='Output as JSON instead of formatted text')
    parser.add_argument('--table', '-t', action='store_true',
                        help='Force table output (default for multiple files)')
    parser.add_argument('--detailed', '-d', action='store_true',
                        help='Force detailed output (default for single file)')
    parser.add_argument('--show-markers', '-m', action='store_true',
                        help='Show all beat marker positions (detailed mode only)')
    parser.add_argument('--recursive', '-r', action='store_true',
                        help='Process directories recursively')
    parser.add_argument('--quiet', '-q', action='store_true',
                        help='Suppress non-error output (useful with --json)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Show verbose parsing information')

    args = parser.parse_args()

    decoder = AppleLoopDecoder(verbose=args.verbose)

    # Collect files to process
    files = []
    if args.input.is_file():
        files = [args.input]
    elif args.input.is_dir():
        pattern = '**/*' if args.recursive else '*'
        for ext in SUPPORTED_EXTENSIONS:
            files.extend(args.input.glob(f"{pattern}{ext}"))
            files.extend(args.input.glob(f"{pattern}{ext.upper()}"))
        files = sorted(set(files))
    else:
        print(f"Error: Input not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    if not files:
        print(f"Error: No Apple Loop files found", file=sys.stderr)
        sys.exit(1)

    # Determine output mode
    # Default: table for multiple files, detailed for single file
    use_table = (len(files) > 1 or args.table) and not args.detailed

    # Initialize table printer if using table mode
    table = None
    if use_table and not args.json and not args.quiet:
        table = TablePrinter(get_table_columns())
        table.print_header()

    # Process files
    results = []
    errors = []

    for file_path in files:
        try:
            info = decoder.decode_file(file_path)
            results.append(info)

            if not args.json and not args.quiet:
                if use_table:
                    table.print_row(info_to_table_row(info))
                else:
                    print_loop_info(info, show_markers=args.show_markers)

        except Exception as e:
            errors.append({'file': str(file_path), 'error': str(e)})
            if not args.quiet:
                print(f"Error processing {file_path}: {e}", file=sys.stderr)

    # Print table footer
    if table and not args.json and not args.quiet:
        table.print_footer(len(results), len(errors))

    # JSON output
    if args.json:
        output = {
            'files': [asdict(r) for r in results],
            'errors': errors,
            'summary': {
                'total_files': len(files),
                'successful': len(results),
                'failed': len(errors)
            }
        }
        print(json.dumps(output, indent=2))

    # Summary for detailed mode with multiple files
    if len(files) > 1 and not use_table and not args.json and not args.quiet:
        print("=" * 80)
        print(f"SUMMARY: Processed {len(results)}/{len(files)} files")
        if errors:
            print(f"         {len(errors)} errors")
        print("=" * 80)

    sys.exit(0 if not errors else 1)


if __name__ == '__main__':
    # Import re for afinfo parsing
    import re
    main()
