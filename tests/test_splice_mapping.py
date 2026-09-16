"""Mapping a Splice row onto Apple Loops metadata.

Apple's category, genre and descriptor lists are closed vocabularies -- the
Loop Browser filters on exact strings. Splice's tags are a different, open
vocabulary. Everything here maps into Apple's documented values or falls back
to a documented default; nothing invents a value Logic has never heard of.
"""

import pytest

from convert_to_apple_loops import (
    APPLE_CATEGORIES,
    APPLE_GENRES,
    APPLE_SUBCATEGORIES,
    splice_sample_to_metadata,
)
from splice_db import SpliceSample


def sample(**kwargs):
    return SpliceSample(**{"filename": "x.wav", "local_path": "/x/x.wav", **kwargs})


class TestLoopVersusOneShot:
    def test_a_splice_loop_carries_its_tempo(self):
        meta = splice_sample_to_metadata(sample(bpm=128, sample_type="loop"))
        assert meta.tempo == 128
        assert meta.is_one_shot is False

    def test_a_splice_one_shot_is_marked_as_one(self):
        meta = splice_sample_to_metadata(sample(bpm=128, sample_type="oneshot"))
        assert meta.is_one_shot is True

    def test_a_loop_with_no_bpm_recorded_has_no_tempo(self):
        meta = splice_sample_to_metadata(sample(bpm=0, sample_type="loop"))
        assert meta.tempo is None


class TestKey:
    def test_a_melodic_loop_keeps_its_key(self):
        meta = splice_sample_to_metadata(
            sample(audio_key="F#", chord_type="min", tags="leads,synth"))
        assert (meta.key_signature, meta.key_type) == ("F#", "minor")

    def test_a_drum_sample_has_its_key_stripped(self):
        # Apple's format says key must be omitted for drums and percussion.
        # Splice records a key for plenty of drum hits anyway.
        meta = splice_sample_to_metadata(
            sample(audio_key="C", chord_type="min", tags="kicks,drums"))
        assert meta.key_signature == ""
        assert meta.key_type == ""


class TestCategory:
    def test_kicks_are_drums(self):
        assert splice_sample_to_metadata(sample(tags="kicks,drums")).category == "Drums"

    def test_shakers_are_percussion(self):
        assert splice_sample_to_metadata(sample(tags="shakers")).category == "Percussion"

    def test_a_synth_lead_is_a_keyboard(self):
        # Apple has no "Synth" category; synthesizers live under Keyboards.
        meta = splice_sample_to_metadata(sample(tags="leads,synth"))
        assert meta.category == "Keyboards"
        assert meta.subcategory == "Synthesizer"

    def test_a_riser_is_a_sound_effect(self):
        assert splice_sample_to_metadata(sample(tags="risers,fx")).category == "Sound Effect"

    def test_a_pad_is_a_texture(self):
        meta = splice_sample_to_metadata(sample(tags="pads"))
        assert meta.category == "Texture/Atmosphere"
        # Apple pairs Texture/Atmosphere only with Synthesizer, Electric Piano
        # and Other Instrument -- there is no "Textures" subcategory.
        assert meta.subcategory == "Synthesizer"

    def test_a_saxophone_is_horn_wind(self):
        assert splice_sample_to_metadata(sample(tags="saxophone")).category == "Horn/Wind"

    def test_the_most_specific_tag_wins(self):
        # Splice puts the most specific tag first.
        assert splice_sample_to_metadata(sample(tags="cello,strings")).subcategory == "Cello"

    def test_an_unknown_tag_falls_back_to_a_shipped_default(self):
        # "Other" is a CATEGORY in Apple's data, never a subcategory.
        meta = splice_sample_to_metadata(sample(tags="theremin_wobbler"))
        assert meta.category == "Other Instrument"
        assert meta.subcategory == "Other Instrument"

    def test_every_mapped_subcategory_is_one_apple_recognises(self):
        for tag in ["kicks", "shakers", "leads", "risers", "pads", "saxophone",
                    "cello", "piano", "electric guitar", "sub", "female vocals",
                    "theremin_wobbler"]:
            meta = splice_sample_to_metadata(sample(tags=tag))
            assert meta.subcategory in APPLE_SUBCATEGORIES, f"{tag} -> {meta.subcategory}"

    def test_every_mapped_category_is_one_apple_recognises(self):
        for tag in ["kicks", "shakers", "leads", "risers", "pads", "saxophone",
                    "cello", "piano", "electric guitar", "sub", "female vocals"]:
            meta = splice_sample_to_metadata(sample(tags=tag))
            assert meta.category in APPLE_CATEGORIES, f"{tag} -> {meta.category}"


class TestGenre:
    def test_deep_house_keeps_its_own_apple_genre(self):
        assert splice_sample_to_metadata(sample(genre="deep house")).genre == "Deep House"

    def test_boom_bap_is_hip_hop(self):
        assert splice_sample_to_metadata(sample(genre="boom bap")).genre == "Hip Hop"

    def test_neo_soul_is_modern_rnb(self):
        assert splice_sample_to_metadata(sample(genre="neo soul")).genre == "Modern RnB"

    def test_afrobeat_is_world_ethnic(self):
        assert splice_sample_to_metadata(sample(genre="afrobeat")).genre == "World/Ethnic"

    def test_an_unknown_genre_falls_back(self):
        assert splice_sample_to_metadata(sample(genre="skweee")).genre == "Other Genre"

    def test_a_genre_can_come_from_the_tags_when_the_column_is_empty(self):
        assert splice_sample_to_metadata(sample(genre="", tags="bass,techno")).genre == "Techno"

    def test_every_mapped_genre_is_one_apple_recognises(self):
        for g in ["deep house", "boom bap", "neo soul", "afrobeat", "jazz",
                  "indie rock", "ambient", "idm", "bluegrass", "grime", ""]:
            meta = splice_sample_to_metadata(sample(genre=g))
            assert meta.genre in APPLE_GENRES, f"{g} -> {meta.genre}"


class TestDescriptors:
    def test_descriptors_are_drawn_from_apples_own_list(self):
        meta = splice_sample_to_metadata(sample(tags="acoustic,guitar,melody"))
        assert "Acoustic" in meta.descriptors.split(",")

    def test_unknown_tags_do_not_become_descriptors(self):
        meta = splice_sample_to_metadata(sample(tags="theremin_wobbler"))
        assert meta.descriptors == ""


class TestGenresMapStraightAcrossWhereAppleHasThem:
    """The census found Apple ships 30 genres, not the 13 we had listed.

    Collapsing "deep house" into "Electronic/Dance" threw away a distinction
    Apple itself makes -- and the Loop Browser has a Deep House filter.
    """

    @pytest.mark.parametrize("splice_genre,expected", [
        ("deep house", "Deep House"),
        ("tech house", "Tech House"),
        ("techno", "Techno"),
        ("dubstep", "Dubstep"),
        ("house", "House"),
        ("future bass", "Future Bass"),
        ("chillwave", "Chillwave"),
        ("electro house", "Electro House"),
        ("chinese traditional", "Chinese Traditional"),
        ("reggaeton", "Reggaeton Pop"),
        ("indie rock", "Indie"),
    ])
    def test_a_genre_apple_also_ships_is_kept(self, splice_genre, expected):
        assert splice_sample_to_metadata(sample(genre=splice_genre)).genre == expected

    def test_a_genre_apple_does_not_ship_still_maps_to_the_nearest(self):
        # Apple has no "psytrance"; Electronic/Dance is the nearest it ships.
        assert splice_sample_to_metadata(sample(genre="psytrance")).genre == "Electronic/Dance"
