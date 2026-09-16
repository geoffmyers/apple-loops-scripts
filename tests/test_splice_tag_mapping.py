"""Splice's tag vocabulary mapped onto Apple's closed lists.

Derived from the 305 distinct tags in a real 1,725-sample Splice library
(scripts/splice-tag-coverage.py). Every expectation here is a tag that
actually occurs, not an invented one.
"""

import pytest

from apple_loops_vocabulary import APPLE_CATEGORY_SUBCATEGORIES, APPLE_GENRES
from convert_to_apple_loops import (
    splice_genre_to_apple,
    splice_tags_to_descriptors,
    splice_tags_to_instrument,
)


class TestVocalSexIsUsedWhenSpliceStatesIt:
    """Apple ships bare "Male" and "Female" under Vocals, and Splice tags them.

    An earlier pass mapped every vocal tag to "Male", which asserted a
    singer's sex from nothing. The fix went too far the other way and
    discarded the tag even when Splice said so explicitly.
    """

    def test_a_female_tag_is_honoured(self):
        assert splice_tags_to_instrument(["female", "vocals"]) == ("Vocals", "Female")

    def test_a_male_tag_is_honoured(self):
        assert splice_tags_to_instrument(["male", "vocals"]) == ("Vocals", "Male")

    def test_an_unqualified_vocal_tag_still_asserts_nothing(self):
        assert splice_tags_to_instrument(["vocals"]) == ("Vocals", "Other Instrument")


class TestInstrumentTagsFromTheRealLibrary:
    @pytest.mark.parametrize("tag,expected", [
        ("nylon", ("Guitars", "Acoustic Guitar")),
        ("fingerpicked", ("Guitars", "Acoustic Guitar")),
        ("upright", ("Bass", "Acoustic Bass")),
        ("drum machine", ("Drums", "Electronic Beats")),
        ("wurlitzer", ("Keyboards", "Electric Piano")),
        ("moog", ("Keyboards", "Synthesizer")),
        ("violin", ("Strings", "Violin")),
        ("sitar", ("Strings", "Sitar")),
        ("glockenspiel", ("Mallets", "Bell")),
        ("cajon", ("Percussion", "Other Instrument")),
        ("flugelhorn", ("Horn/Wind", "Trumpet")),
        ("ambience", ("Sound Effect", "Ambience")),
        ("braaam", ("Sound Effect", "Impacts & Crashes")),
    ])
    def test_the_tag_lands_on_a_shipped_pair(self, tag, expected):
        assert splice_tags_to_instrument([tag]) == expected


class TestDescriptorTagsFromTheRealLibrary:
    @pytest.mark.parametrize("tag,expected", [
        ("wet", "Processed"),
        ("organic", "Acoustic"),
        ("live sounds", "Acoustic"),
        ("funky", "Grooving"),
        ("tonal", "Melodic"),
        ("bright", "Cheerful"),
        ("dirty", "Distorted"),
        ("sidechained", "Processed"),
        ("vinyl", "Processed"),
        ("lo-fi", "Processed"),
        ("soft", "Relaxed"),
        ("solo", "Single"),
    ])
    def test_the_tag_becomes_an_apple_descriptor(self, tag, expected):
        assert expected in splice_tags_to_descriptors([tag]).split(",")


class TestGenreTagsFromTheRealLibrary:
    @pytest.mark.parametrize("genre,expected", [
        ("nu jazz", "Jazz"),
        ("soul jazz", "Jazz"),
        ("bebop", "Jazz"),
        ("nu disco", "Funk"),
        ("electronica", "Electronic"),
        ("minimal", "Techno"),
        ("future soul", "Modern RnB"),
        ("bossa nova", "World/Ethnic"),
        ("samba", "World/Ethnic"),
        ("afro latin", "World/Ethnic"),
        ("son cubano", "World/Ethnic"),
        ("dream pop", "Indie"),
        ("shoegaze", "Indie"),
        ("funky house", "House"),
        ("french house", "House"),
        ("cloud rap", "Hip Hop"),
        ("west coast", "Hip Hop"),
        ("hyperpop", "Electronic Pop"),
        ("glitch", "Experimental"),
        ("breaks", "Vintage Breaks"),
        ("liquid dnb", "Electronic/Dance"),
        ("deep dubstep", "Dubstep"),
    ])
    def test_the_genre_maps_to_a_shipped_apple_genre(self, genre, expected):
        assert splice_genre_to_apple(genre) == expected
        assert expected in APPLE_GENRES


class TestNothingEscapesApplesVocabulary:
    def test_every_new_pair_is_one_apple_ships(self):
        from convert_to_apple_loops import SPLICE_TAG_TO_INSTRUMENT
        offenders = {
            (cat, sub) for cat, sub in SPLICE_TAG_TO_INSTRUMENT.values()
            if sub not in APPLE_CATEGORY_SUBCATEGORIES.get(cat, ())
        }
        assert not offenders, sorted(offenders)


class TestMultiInstrumentLoops:
    """Splice's "songstarters" are full arrangements, not one instrument.

    119 of a real library's 274 unplaceable samples carry only genre tags plus
    "songstarters", "music" or "melodic stack". Apple has a category for
    exactly this and ships 767 loops in it: Mixed.
    """

    @pytest.mark.parametrize("tag", ["songstarters", "melodic stack", "music"])
    def test_a_full_arrangement_is_mixed_not_other(self, tag):
        assert splice_tags_to_instrument(["soul", "rnb", tag]) == (
            "Mixed", "Other Instrument")

    def test_a_named_instrument_still_wins_over_mixed(self):
        assert splice_tags_to_instrument(["piano", "soul", "songstarters"]) == (
            "Keyboards", "Piano")


class TestFilenameFallsInWhereSpliceTagsAreSilent:
    """Splice's tags and the filename are different sources, not rivals.

    When the tag list names no instrument the filename usually does --
    "SLS_CS_75_songstarter_guitars_lead_warmtheory_Gmin" is a guitar loop
    whose tags say only "hip hop, soul, rnb, neo soul, songstarters".
    """

    def test_the_filename_supplies_an_instrument_the_tags_omit(self):
        from splice_db import SpliceSample
        from convert_to_apple_loops import splice_sample_to_metadata

        sample = SpliceSample(
            filename="SLS_CS_75_songstarter_guitars_lead_warmtheory_Gmin.wav",
            local_path="/x/SLS_CS_75_songstarter_guitars_lead_warmtheory_Gmin.wav",
            tags="hip hop,soul,rnb,neo soul,songstarters",
            bpm=75, audio_key="G", chord_type="min", sample_type="loop")
        meta = splice_sample_to_metadata(sample)
        assert meta.category == "Guitars"
        # Everything Splice does know is still taken from Splice.
        assert meta.tempo == 75
        assert (meta.key_signature, meta.key_type) == ("G", "minor")

    def test_splice_tags_still_win_when_they_name_an_instrument(self):
        from splice_db import SpliceSample
        from convert_to_apple_loops import splice_sample_to_metadata

        sample = SpliceSample(
            filename="something_guitar_named.wav", local_path="/x/a.wav",
            tags="piano,soul", sample_type="loop")
        assert splice_sample_to_metadata(sample).category == "Keyboards"

    def test_mixed_is_used_when_neither_source_names_an_instrument(self):
        from splice_db import SpliceSample
        from convert_to_apple_loops import splice_sample_to_metadata

        sample = SpliceSample(
            filename="jmh_full_88_numb_D#m.wav", local_path="/x/a.wav",
            tags="jazz,songstarters", sample_type="loop")
        assert splice_sample_to_metadata(sample).category == "Mixed"


class TestGenresWithNoExactAppleEquivalent:
    """Apple ships no plain "Pop" and no "Synthwave".

    113 samples in a real library are tagged only "pop" and 48 only
    "synthwave". Leaving both at "Other Genre" hides them from every genre
    filter, so each takes the nearest thing Apple does ship -- and the nearest
    is chosen to under-claim rather than over-claim.
    """

    def test_pop_takes_apples_electronic_pop(self):
        assert splice_genre_to_apple("pop") == "Electronic Pop"

    def test_synthwave_takes_the_broader_electronic(self):
        # Not "Electronic Pop": synthwave is usually instrumental, so the
        # broader label claims less.
        assert splice_genre_to_apple("synthwave") == "Electronic"

    def test_a_more_specific_tag_still_wins_over_pop(self):
        # Splice orders tags most-specific-first.
        assert splice_genre_to_apple("", ["deep house", "pop"]) == "Deep House"
