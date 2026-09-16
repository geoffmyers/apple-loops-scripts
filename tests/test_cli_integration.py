"""End-to-end run of the CLI over a real Splice-shaped library.

Covers the seams the unit tests do not reach: argument parsing, opening the
database, and the dry-run report. Real WAV files, a real SQLite database, the
script invoked as a subprocess.
"""

import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from test_splice_db import make_db

SCRIPT = Path(__file__).resolve().parent.parent / "convert_to_apple_loops.py"


def write_wav(path: Path, seconds: float, sample_rate: int = 44100):
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack("<h", 0) * frames)


@pytest.fixture
def library(tmp_path):
    """A four-bar loop and a kick one-shot, both present in sounds.db."""
    sounds = tmp_path / "sounds"
    loop = sounds / "SP_bass_loop.wav"
    kick = sounds / "SP_kick_128.wav"
    write_wav(loop, 7.5)
    write_wav(kick, 0.4)

    db = make_db(tmp_path, [
        {"filename": "SP_bass_loop.wav", "local_path": str(loop),
         "bpm": 128, "audio_key": "F", "chord_type": "min",
         "sample_type": "loop", "genre": "techno", "tags": "bass",
         "duration": 7.5},
        {"filename": "SP_kick_128.wav", "local_path": str(kick),
         "bpm": 128, "sample_type": "oneshot", "tags": "kicks,drums",
         "duration": 0.4},
    ])
    return {"dir": sounds, "db": db}


def run_cli(*args):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *[str(a) for a in args]],
        capture_output=True, text=True, timeout=120,
    )
    return result


class TestDryRunWithSpliceDb:
    def test_the_loop_gets_four_bars_and_the_one_shot_gets_none(self, library):
        result = run_cli(library["dir"], "--dry-run", "--detailed",
                         "--splice-db", library["db"])
        assert result.returncode == 0, result.stderr

        out = result.stdout
        loop_block = out.split("SP_bass_loop.wav")[1].split("\n\n")[0]
        kick_block = out.split("SP_kick_128.wav")[1].split("\n\n")[0]

        # 4 bars at 128 BPM.
        assert "128 BPM → 16 beats" in loop_block
        assert "beat markers: yes" in loop_block.lower()
        assert "F minor" in loop_block

        # A kick, despite carrying a BPM in the database and in its name.
        assert "One-shot" in kick_block
        assert "→ 0 beats" in kick_block
        assert "beat markers: no" in kick_block.lower()

    def test_it_reports_how_much_came_from_splice(self, library):
        result = run_cli(library["dir"], "--dry-run", "--splice-db", library["db"])
        assert result.returncode == 0, result.stderr
        assert "Metadata from Splice: 2/2" in result.stdout
        assert "Loops: 1, One-shots: 1" in result.stdout

    def test_splice_only_skips_files_the_database_does_not_know(self, library):
        write_wav(library["dir"] / "downloaded_elsewhere_120_loop.wav", 8.0)
        result = run_cli(library["dir"], "--dry-run", "--splice-db", library["db"],
                         "--splice-only")
        assert result.returncode == 0, result.stderr
        assert "Skipped (not in Splice library): 1" in result.stdout


class TestDryRunWithoutSpliceDb:
    def test_filenames_alone_still_produce_metadata(self, library):
        result = run_cli(library["dir"], "--dry-run", "--detailed")
        assert result.returncode == 0, result.stderr
        assert "Metadata from: filename" in result.stdout

    def test_forcing_one_shot_suppresses_every_beat_count(self, library):
        result = run_cli(library["dir"], "--dry-run", "--detailed", "--one-shot")
        assert result.returncode == 0, result.stderr
        assert "beat markers: yes" not in result.stdout.lower()


class TestErrorPaths:
    def test_a_missing_database_fails_loudly(self, tmp_path):
        result = run_cli(tmp_path, "--dry-run", "--splice-db", tmp_path / "nope.db")
        assert result.returncode == 1
        assert "not found" in result.stderr

    def test_splice_only_without_a_database_is_rejected(self, tmp_path):
        result = run_cli(tmp_path, "--dry-run", "--splice-only")
        assert result.returncode == 1
        assert "--splice-only needs --splice-db" in result.stderr
