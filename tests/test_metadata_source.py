"""Choosing where a file's metadata comes from.

Splice's own row is authoritative. The filename is the fallback. A single
method decides, so every code path -- convert, dry run, table, directory
walk -- agrees about what a file is.
"""

import sqlite3
from pathlib import Path

import pytest

from convert_to_apple_loops import AppleLoopConverter
from splice_db import SpliceLibrary

from test_splice_db import make_db


@pytest.fixture
def library(tmp_path):
    db = make_db(tmp_path, [{
        "filename": "SP_bass_120_Am_loop.wav",
        "local_path": "/Splice/SP_bass_120_Am_loop.wav",
        "bpm": 128, "audio_key": "F", "chord_type": "min",
        "sample_type": "loop", "genre": "techno", "tags": "bass",
        "duration": 7.5,
    }, {
        "filename": "SP_kick_loop_140.wav",
        "local_path": "/Splice/SP_kick_loop_140.wav",
        "bpm": 140, "sample_type": "oneshot", "tags": "kicks,drums",
    }])
    return SpliceLibrary(db)


class TestMetadataFor:
    def test_falls_back_to_the_filename_with_no_library(self, tmp_path):
        converter = AppleLoopConverter()
        meta = converter.metadata_for(Path("SP_TS_128_Am_bass_loop.wav"))
        assert meta.tempo == 128
        assert meta.metadata_source == "filename"

    def test_the_database_row_wins_over_the_filename(self, library):
        # The filename says 120 in Am; Splice recorded 128 in Fm.
        converter = AppleLoopConverter(splice_library=library)
        meta = converter.metadata_for(Path("/anywhere/SP_bass_120_Am_loop.wav"))
        assert meta.tempo == 128
        assert (meta.key_signature, meta.key_type) == ("F", "minor")
        assert meta.metadata_source == "splice"

    def test_the_database_wins_on_one_shot_even_when_the_name_says_loop(self, library):
        converter = AppleLoopConverter(splice_library=library)
        meta = converter.metadata_for(Path("/anywhere/SP_kick_loop_140.wav"))
        assert meta.is_one_shot is True

    def test_a_file_missing_from_the_database_falls_back(self, library):
        converter = AppleLoopConverter(splice_library=library)
        meta = converter.metadata_for(Path("/anywhere/some_other_140_loop.wav"))
        assert meta.tempo == 140
        assert meta.metadata_source == "filename"


class TestForcedOverrides:
    def test_forcing_one_shot_beats_the_database(self, library):
        converter = AppleLoopConverter(splice_library=library, force_one_shot=True)
        meta = converter.metadata_for(Path("/anywhere/SP_bass_120_Am_loop.wav"))
        assert meta.is_one_shot is True

    def test_forcing_loop_beats_the_database(self, library):
        converter = AppleLoopConverter(splice_library=library, force_one_shot=False)
        meta = converter.metadata_for(Path("/anywhere/SP_kick_loop_140.wav"))
        assert meta.is_one_shot is False


class TestSpliceOnly:
    """--splice-only converts what Splice can vouch for and skips the rest."""

    def test_a_file_in_the_database_is_converted(self, library):
        converter = AppleLoopConverter(splice_library=library, splice_only=True)
        assert converter.should_convert(Path("/x/SP_bass_120_Am_loop.wav"))

    def test_a_file_missing_from_the_database_is_skipped(self, library):
        converter = AppleLoopConverter(splice_library=library, splice_only=True)
        assert not converter.should_convert(Path("/x/some_other.wav"))

    def test_without_the_flag_everything_is_converted(self, library):
        converter = AppleLoopConverter(splice_library=library)
        assert converter.should_convert(Path("/x/some_other.wav"))

    def test_without_a_library_everything_is_converted(self):
        converter = AppleLoopConverter()
        assert converter.should_convert(Path("/x/some_other.wav"))
