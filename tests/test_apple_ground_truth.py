"""Checked against Apple's own 34,928 shipped loops.

reference/apple-loops-shipped-vocabulary.json is a census of every category,
subcategory, genre and descriptor value in /Library/Audio/Apple Loops on
macOS 27, produced by scripts/validate-against-apple-loops.py.

It exists because the hand-curated lists were wrong in both directions: they
invented values Apple has never shipped ('Hi-Hat' where Apple writes 'Hi-hat',
'Male Vocal' where Apple writes 'Male') and they missed most of what Apple
does ship (13 genres listed against 30 in the library). A value the Loop
Browser does not recognise is a loop no filter will ever show, so guessing is
not a small error.
"""

import json
from pathlib import Path

import pytest

from apple_loops_vocabulary import (
    APPLE_CATEGORIES,
    APPLE_CATEGORY_SUBCATEGORIES,
    APPLE_DESCRIPTORS,
    APPLE_GENRES,
    APPLE_SUBCATEGORIES,
)
from convert_to_apple_loops import (
    SPLICE_TAG_TO_INSTRUMENT,
    MetadataExtractor,
)

REFERENCE = (Path(__file__).resolve().parent.parent
             / "reference" / "apple-loops-shipped-vocabulary.json")


@pytest.fixture(scope="module")
def shipped():
    return json.loads(REFERENCE.read_text())["shipped"]


class TestOurListsMatchTheLibrary:
    def test_categories_match_exactly(self, shipped):
        assert set(APPLE_CATEGORIES) == set(shipped["categories"])

    def test_subcategories_match_exactly(self, shipped):
        assert set(APPLE_SUBCATEGORIES) == set(shipped["subcategories"])

    def test_genres_match_exactly(self, shipped):
        assert set(APPLE_GENRES) == set(shipped["genres"])

    def test_descriptors_match_exactly(self, shipped):
        assert set(APPLE_DESCRIPTORS) == set(shipped["descriptors"])


class TestEmittedPairsAreOnesAppleShips:
    """A valid category with a valid subcategory can still be a pair Apple
    never ships -- Texture/Atmosphere never carries 'Ambience', which lives
    under Sound Effect. That is just as invisible in the browser."""

    def test_the_splice_mapper_emits_only_shipped_pairs(self):
        offenders = {
            (cat, sub) for cat, sub in SPLICE_TAG_TO_INSTRUMENT.values()
            if sub not in APPLE_CATEGORY_SUBCATEGORIES.get(cat, ())
        }
        assert not offenders, sorted(offenders)

    def test_the_filename_extractor_emits_only_shipped_pairs(self):
        extractor = MetadataExtractor()
        offenders = {
            (cat, sub) for cat, sub in extractor.INSTRUMENT_MAP.values()
            if sub not in APPLE_CATEGORY_SUBCATEGORIES.get(cat, ())
        }
        assert not offenders, sorted(offenders)

    def test_the_pairing_table_covers_every_category(self):
        assert set(APPLE_CATEGORY_SUBCATEGORIES) == set(APPLE_CATEGORIES)


class TestKnownApplesValuesWeGotWrong:
    """Regression guards for the specific values the census corrected."""

    def test_apple_spells_it_hi_hat_lowercase_h(self, shipped):
        assert "Hi-hat" in shipped["subcategories"]
        assert "Hi-Hat" not in shipped["subcategories"]

    def test_apple_uses_bare_male_and_female(self, shipped):
        assert {"Male", "Female"} <= set(shipped["subcategories"])
        assert not {"Male Vocal", "Female Vocal"} & set(shipped["subcategories"])

    def test_motions_and_transitions_is_a_real_apple_value(self, shipped):
        assert shipped["subcategories"]["Motions & Transitions"] > 100

    def test_apple_ships_specific_dance_genres_not_one_umbrella(self, shipped):
        assert {"Deep House", "Tech House", "Techno", "Dubstep"} <= set(shipped["genres"])
