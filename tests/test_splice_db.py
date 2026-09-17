"""Reading Splice's own sounds.db.

Splice knows the real BPM, key and loop/one-shot status of every sample it
downloaded. Guessing those from a filename when the app already recorded them
is how a kick drum ends up time-stretched.
"""

import sqlite3

import pytest

from splice_db import SpliceLibrary, SpliceSample, parse_splice_key, _normalise_scale

# Columns as they appear in the real sounds.db `samples` table.
SAMPLES_COLUMNS = [
    "id", "local_path", "attr_hash", "dir", "audio_key", "bpm", "chord_type",
    "duration", "file_hash", "sas_id", "filename", "genre", "pack_uuid",
    "sample_type", "tags", "popularity", "purchased_at", "last_modified_at",
    "waveform_url", "provider_name",
]


def make_db(tmp_path, rows):
    db_path = tmp_path / "sounds.db"
    conn = sqlite3.connect(db_path)
    conn.execute(f"CREATE TABLE samples ({', '.join(SAMPLES_COLUMNS)})")
    for row in rows:
        values = [row.get(col) for col in SAMPLES_COLUMNS]
        placeholders = ", ".join("?" * len(SAMPLES_COLUMNS))
        conn.execute(f"INSERT INTO samples VALUES ({placeholders})", values)
    conn.commit()
    conn.close()
    return db_path


class TestParseSpliceKey:
    def test_a_dedicated_chord_type_column_settles_the_scale(self):
        assert parse_splice_key("A", "min") == ("A", "minor")

    def test_a_scale_suffix_on_the_key_is_used_when_no_chord_type(self):
        assert parse_splice_key("Cm", "") == ("C", "minor")

    def test_a_flat_is_preserved(self):
        assert parse_splice_key("Bbm", None) == ("Bb", "minor")

    def test_a_sharp_is_preserved_and_case_normalised(self):
        assert parse_splice_key("g#m", None) == ("G#", "minor")

    def test_a_bare_note_reports_both_rather_than_guessing(self):
        # Key type drives Loop Browser filtering, not transposition, so an
        # honest "both" beats a coin-flip "major".
        assert parse_splice_key("C", "") == ("C", "both")

    def test_no_key_at_all_is_empty(self):
        assert parse_splice_key("", "") == ("", "")
        assert parse_splice_key(None, None) == ("", "")

    def test_an_unparseable_key_is_discarded_rather_than_passed_through(self):
        assert parse_splice_key("H#$", "") == ("", "")


class TestNormaliseScale:
    """`_normalise_scale()` matches every multi-character token
    case-insensitively, with one deliberate exception: a bare single
    letter is matched case-*sensitively*, since 'M' (major) and 'm'
    (minor) would otherwise collide when lower-cased."""

    def test_bare_capital_m_is_major(self):
        assert _normalise_scale("M") == "major"

    def test_bare_lowercase_m_is_minor(self):
        assert _normalise_scale("m") == "minor"

    @pytest.mark.parametrize("token", ["maj", "Maj", "MAJ", "major", "Major", "MAJOR", "MaJoR"])
    def test_major_tokens_are_case_insensitive(self, token):
        assert _normalise_scale(token) == "major"

    @pytest.mark.parametrize("token", ["min", "Min", "MIN", "minor", "Minor", "MINOR", "mIn"])
    def test_minor_tokens_are_case_insensitive(self, token):
        assert _normalise_scale(token) == "minor"

    def test_surrounding_whitespace_is_stripped(self):
        assert _normalise_scale(" M ") == "major"
        assert _normalise_scale(" m ") == "minor"

    def test_unknown_token_is_empty(self):
        assert _normalise_scale("x") == ""
        assert _normalise_scale("mm") == ""
        assert _normalise_scale("both") == ""

    def test_empty_or_none_is_empty(self):
        assert _normalise_scale("") == ""
        assert _normalise_scale(None) == ""


class TestSpliceLibrary:
    def test_reads_only_rows_that_have_a_local_file(self, tmp_path):
        db = make_db(tmp_path, [
            {"filename": "kick.wav", "local_path": "/Splice/kick.wav"},
            {"filename": "never_downloaded.wav", "local_path": None},
        ])
        library = SpliceLibrary(db)
        assert [s.filename for s in library.samples()] == ["kick.wav"]

    def test_looks_a_file_up_by_its_basename(self, tmp_path):
        db = make_db(tmp_path, [{
            "filename": "SP_bass_128_Am.wav",
            "local_path": "/Users/x/Splice/sounds/packs/p/SP_bass_128_Am.wav",
            "bpm": 128, "audio_key": "A", "chord_type": "min",
            "sample_type": "loop", "genre": "deep house",
            "tags": "bass,deep house", "duration": 7.5,
        }])
        library = SpliceLibrary(db)
        found = library.lookup("/somewhere/else/SP_bass_128_Am.wav")
        assert found is not None
        assert found.bpm == 128
        assert found.is_loop

    def test_lookup_misses_return_none(self, tmp_path):
        db = make_db(tmp_path, [{"filename": "a.wav", "local_path": "/x/a.wav"}])
        assert SpliceLibrary(db).lookup("/x/b.wav") is None

    def test_a_null_sample_type_is_treated_as_a_one_shot(self, tmp_path):
        # splicecrate does the same: absence means one-shot, not loop. Guessing
        # "loop" here would put beat markers on an unknown sample.
        db = make_db(tmp_path, [{
            "filename": "hit.wav", "local_path": "/x/hit.wav", "sample_type": None,
        }])
        sample = SpliceLibrary(db).lookup("/x/hit.wav")
        assert not sample.is_loop

    def test_tags_are_split_most_specific_first(self, tmp_path):
        db = make_db(tmp_path, [{
            "filename": "k.wav", "local_path": "/x/k.wav",
            "tags": "kicks, drums, techno",
        }])
        sample = SpliceLibrary(db).lookup("/x/k.wav")
        assert sample.tag_list == ["kicks", "drums", "techno"]

    def test_a_missing_database_is_a_clear_error(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            SpliceLibrary(tmp_path / "nope.db")

    def test_a_database_without_a_samples_table_is_a_clear_error(self, tmp_path):
        db_path = tmp_path / "wrong.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE something_else (id)")
        conn.commit()
        conn.close()
        with pytest.raises(ValueError, match="samples"):
            SpliceLibrary(db_path)


class TestSpliceSampleKey:
    def test_exposes_the_parsed_key(self):
        sample = SpliceSample(filename="a.wav", local_path="/x/a.wav",
                              audio_key="Bb", chord_type="maj")
        assert sample.key_signature == "Bb"
        assert sample.key_type == "major"
