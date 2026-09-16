"""Both metadata paths must stay inside Apple's vocabulary.

The Splice mapper was written against these lists from the start; the filename
extractor predates them. Anything either one emits that Logic does not
recognise is a loop no Loop Browser filter will show -- and the tool's own
`decode_apple_loops.py --verify` would flag files this tool produced.
"""

import pytest

from apple_loops_vocabulary import (
    APPLE_CATEGORIES,
    APPLE_DESCRIPTORS,
    APPLE_GENRES,
    APPLE_SUBCATEGORIES,
)
from convert_to_apple_loops import MetadataExtractor


@pytest.fixture
def extractor():
    return MetadataExtractor()


class TestFilenameExtractorVocabulary:
    def test_every_category_it_can_emit_is_known_to_apple(self, extractor):
        offenders = {c for c, _ in extractor.INSTRUMENT_MAP.values()
                     if c not in APPLE_CATEGORIES}
        assert not offenders

    def test_every_subcategory_it_can_emit_is_known_to_apple(self, extractor):
        offenders = {s for _, s in extractor.INSTRUMENT_MAP.values()
                     if s not in APPLE_SUBCATEGORIES}
        assert not offenders, sorted(offenders)

    def test_every_genre_it_can_emit_is_known_to_apple(self, extractor):
        offenders = {g for g in extractor.GENRE_MAP.values()
                     if g not in APPLE_GENRES}
        assert not offenders, sorted(offenders)

    def test_the_general_midi_program_map_is_also_checked(self, extractor):
        """PROGRAM_MAP is a second source of categories and went unchecked.

        It mapped GM programs 104-111 to ('World/Ethnic', 'Other') -- and
        World/Ethnic is one of Apple's GENRES, not a category.
        """
        from apple_loops_vocabulary import APPLE_CATEGORY_SUBCATEGORIES
        offenders = {
            (cat, sub) for cat, sub in extractor.PROGRAM_MAP.values()
            if sub not in APPLE_CATEGORY_SUBCATEGORIES.get(cat, ())
        }
        assert not offenders, sorted(offenders)

    def test_the_fallback_it_returns_is_a_value_apple_ships(self, extractor):
        # extract_instrument had its own hardcoded ('Other Instrument',
        # 'Other') fallback, and "Other" is a category, never a subcategory.
        category, subcategory = extractor.extract_instrument("no_keywords_here_xyz")
        assert subcategory in APPLE_SUBCATEGORIES, subcategory

    def test_every_descriptor_it_can_emit_is_known_to_apple(self, extractor):
        offenders = {d for d in extractor.DESCRIPTOR_MAP.values()
                     if d not in APPLE_DESCRIPTORS}
        assert not offenders, sorted(offenders)


class TestSpecificCorrections:
    def test_a_generic_vocal_tag_does_not_assert_a_singers_sex(self, extractor):
        # "vox" says nothing about who is singing. Apple ships "Male" and
        # "Female" under Vocals but nothing neutral, so the fallback is used.
        assert extractor.extract_instrument("VOX_hook_140_Cmin") == (
            "Vocals", "Other Instrument")

    def test_hi_hat_uses_apples_own_spelling(self, extractor):
        # Loop Browser filtering is exact-string, and the census says Apple
        # ships 371 loops spelled "Hi-hat" and none spelled "Hi-Hat". An
        # earlier pass "corrected" this the wrong way on a hand-written list.
        assert extractor.extract_instrument("closed_hihat_01") == ("Drums", "Hi-hat")
