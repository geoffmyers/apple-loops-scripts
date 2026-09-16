"""Filename parsing, for files the Splice database does not cover.

Splice filenames carry tempo and key without ever writing the token "bpm",
and the folder tree says loop or one-shot. This is strictly the fallback path:
when sounds.db has the row, the row wins.
"""

import pytest

from convert_to_apple_loops import MetadataExtractor


class TestTempoFromSpliceStyleNames:
    def setup_method(self):
        self.extractor = MetadataExtractor()

    @pytest.mark.parametrize("filename,expected", [
        ("SP_TS_128_Am_bass_loop", 128),
        ("OS_ASM_140_Melody_Loop_Cmin", 140),
        ("MEDA_percussion_loop_120_dry", 120),
        ("vocal_loop_Fmin_140", 140),
        ("KSHMR_Kick_01", None),
    ])
    def test_reads_tempo_without_a_bpm_token(self, filename, expected):
        assert self.extractor.extract_tempo(filename) == expected

    def test_a_sample_rate_is_not_a_tempo(self):
        assert self.extractor.extract_tempo("pad_44100_Cmaj") is None

    def test_a_bit_depth_is_not_a_tempo(self):
        assert self.extractor.extract_tempo("snare_24_bit") is None


class TestKeyFromSpliceStyleNames:
    def setup_method(self):
        self.extractor = MetadataExtractor()

    @pytest.mark.parametrize("filename,expected", [
        ("SP_TS_128_Am_bass_loop", ("A", "minor")),
        ("OS_ASM_140_Melody_Loop_Cmin", ("C", "minor")),
        ("lead_Fsharp_maj", ("F#", "major")),
        ("keys_Bbmaj_90", ("Bb", "major")),
    ])
    def test_reads_key_and_scale(self, filename, expected):
        assert self.extractor.extract_key(filename) == expected


class TestOneShotDetection:
    def setup_method(self):
        self.extractor = MetadataExtractor()

    def test_a_folder_named_oneshots_marks_a_one_shot(self):
        assert self.extractor.extract_one_shot("Splice/packs/x/oneshots/kick_01") is True

    @pytest.mark.parametrize("text", ["one shot", "one-shot", "One_Shots", "1shot"])
    def test_spelling_variants_are_recognised(self, text):
        assert self.extractor.extract_one_shot(f"pack/{text}/hit") is True

    def test_the_word_loop_marks_a_loop(self):
        assert self.extractor.extract_one_shot("SP_TS_128_Am_bass_loop") is False

    def test_an_unmarked_file_is_unknown_rather_than_assumed(self):
        assert self.extractor.extract_one_shot("SP_TS_128_Am_bass") is None

    def test_one_shot_wins_over_an_incidental_loop_in_the_pack_name(self):
        # "Loopmasters" style pack names must not override an explicit
        # oneshots folder.
        assert self.extractor.extract_one_shot("Loopcloud/oneshots/kick") is True


class TestExtractAllAppliesOneShot:
    def setup_method(self):
        self.extractor = MetadataExtractor()

    def test_a_one_shot_path_sets_the_flag(self):
        meta = self.extractor.extract_all("kick_01", "Splice/packs/x/oneshots")
        assert meta.is_one_shot is True

    def test_a_one_shot_never_keeps_a_tempo_from_its_filename(self):
        # A serial number that reads like a tempo must not become a beat count
        # on a single hit.
        meta = self.extractor.extract_all("kick_128", "packs/x/one shots")
        assert meta.is_one_shot is True
        assert meta.tempo is None
