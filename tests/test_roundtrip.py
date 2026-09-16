"""Convert, then decode, then verify -- the whole pipeline in one pass.

The MIDI path is pure Python, so this runs anywhere. It is the only test that
proves the bytes the converter writes are the bytes the decoder reads and that
the result passes the native-behaviour checks.
"""

import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONVERT = ROOT / "convert_to_apple_loops.py"
DECODE = ROOT / "decode_apple_loops.py"


def minimal_midi(beats: int = 16, ticks_per_beat: int = 480) -> bytes:
    """A format-0 SMF: one note held for `beats` beats at 120 BPM."""
    def vlq(value):
        out = bytearray([value & 0x7F])
        value >>= 7
        while value:
            out.insert(0, (value & 0x7F) | 0x80)
            value >>= 7
        return bytes(out)

    track = (
        b'\x00\xff\x51\x03' + struct.pack('>I', 500000)[1:]   # 120 BPM
        + b'\x00\x90\x3c\x40'                                 # note on
        + vlq(beats * ticks_per_beat) + b'\x80\x3c\x40'       # note off
        + b'\x00\xff\x2f\x00'                                 # end of track
    )
    header = b'MThd' + struct.pack('>IHHH', 6, 0, 1, ticks_per_beat)
    return header + b'MTrk' + struct.pack('>I', len(track)) + track


@pytest.fixture
def midi_loop(tmp_path):
    path = tmp_path / "Keys_Loop_120_Am.mid"
    path.write_bytes(minimal_midi(beats=16))
    return path


def run(script, *args):
    return subprocess.run(
        [sys.executable, str(script), *[str(a) for a in args]],
        capture_output=True, text=True, timeout=120,
    )


class TestMidiRoundTrip:
    def test_a_converted_midi_loop_verifies_as_native(self, midi_loop, tmp_path):
        out = tmp_path / "out.caf"

        converted = run(CONVERT, midi_loop, "-o", out)
        assert converted.returncode == 0, converted.stderr
        assert out.exists()

        verified = run(DECODE, out, "--verify")
        assert verified.returncode == 0, verified.stdout + verified.stderr
        assert "look like native Apple Loops" in verified.stdout

    def test_the_decoder_reads_back_what_the_converter_wrote(self, midi_loop, tmp_path):
        out = tmp_path / "out.caf"
        assert run(CONVERT, midi_loop, "-o", out).returncode == 0

        decoded = run(DECODE, out, "--json")
        assert decoded.returncode == 0, decoded.stderr

        import json
        info = json.loads(decoded.stdout)["files"][0]
        # 16 beats at 120 BPM over 8 seconds.
        assert info["metadata"]["beat_count"] == 16
        assert info["metadata"]["key_signature"] == "A"
        assert info["metadata"]["key_type"] == "minor"
        assert info["beat_markers"]["marker_count"] > 16

    def test_a_forced_one_shot_round_trips_with_no_markers(self, midi_loop, tmp_path):
        out = tmp_path / "shot.caf"
        assert run(CONVERT, midi_loop, "-o", out, "--one-shot").returncode == 0

        import json
        decoded = run(DECODE, out, "--json")
        info = json.loads(decoded.stdout)["files"][0]
        assert info["metadata"]["beat_count"] == 0
        assert info["beat_markers"]["marker_count"] == 0

        # And it must still pass verification: a one-shot with neither a beat
        # count nor markers is a legitimate file, not a broken loop.
        verified = run(DECODE, out, "--verify")
        assert verified.returncode == 0, verified.stdout


class TestCollidingNamesSurvive:
    def test_two_packs_with_the_same_basename_both_convert(self, tmp_path):
        library = tmp_path / "lib"
        for pack in ("pack_a", "pack_b"):
            path = library / pack / "Kick_01.mid"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(minimal_midi(beats=4))

        out = tmp_path / "out"
        result = run(CONVERT, library, "--output-dir", out, "--midi-only")
        assert result.returncode == 0, result.stderr

        produced = sorted(p.name for p in out.glob("*.caf"))
        assert produced == ["Kick_01.caf", "Kick_01_2.caf"], produced
