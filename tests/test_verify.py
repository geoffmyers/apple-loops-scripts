"""Verifying that a converted file will actually behave as an Apple Loop.

Every one of these is a way a file can carry Apple Loop metadata, open without
error, and still not do the thing the metadata claims: not follow tempo, not
appear under any Loop Browser filter, or stretch to the wrong length.
"""

import pytest

from decode_apple_loops import (
    AppleLoopInfo,
    AudioInfo,
    BeatMarkers,
    LoopMetadata,
    SpotlightInfo,
    verify_apple_loop,
)


def loop(**kwargs):
    """A well-formed four-bar 128 BPM audio loop, before any damage."""
    info = AppleLoopInfo(
        file_path="x.caf",
        file_format="CAF",
        loop_type="audio",
        audio=AudioInfo(sample_rate=44100, num_frames=330750, duration=7.5),
        metadata=LoopMetadata(
            category="Bass", subcategory="Synthetic Bass", genre="Electronic/Dance",
            beat_count=16, time_signature="4/4", key_signature="F",
            key_type="minor", descriptors="Grooving",
        ),
        beat_markers=BeatMarkers(marker_count=65, positions=[0, 330750]),
        spotlight=SpotlightInfo(entries={"genre": "Electronic/Dance"}),
    )
    for key, value in kwargs.items():
        setattr(info, key, value)
    return info


def messages(findings):
    return " | ".join(m for _, m in findings)


def errors(findings):
    return [m for sev, m in findings if sev == "error"]


class TestAWellFormedLoop:
    def test_has_nothing_to_report(self):
        assert verify_apple_loop(loop()) == []


class TestTempoFollowing:
    def test_a_beat_count_with_no_markers_cannot_follow_tempo(self):
        info = loop(beat_markers=BeatMarkers())
        assert "will not follow project tempo" in messages(verify_apple_loop(info))

    def test_markers_with_no_beat_count_leave_logic_no_tempo(self):
        # A warning, not an error: Apple ships 563 loops like this (1.6% of
        # its library), so it is unusual rather than broken.
        info = loop()
        info.metadata.beat_count = 0
        findings = verify_apple_loop(info)
        assert "no beat count" in messages(findings)
        assert not errors(findings)

    def test_a_one_shot_with_neither_is_fine(self):
        info = loop(beat_markers=BeatMarkers())
        info.metadata.beat_count = 0
        assert verify_apple_loop(info) == []

    def test_a_derived_tempo_outside_musical_range_is_flagged(self):
        # 16 beats over 0.4s is 2400 BPM: the beat count is wrong.
        info = loop(audio=AudioInfo(sample_rate=44100, num_frames=17640, duration=0.4))
        assert "implausible" in messages(verify_apple_loop(info))

    def test_the_tempo_range_admits_what_apple_actually_ships(self):
        # Apple's own library runs from 21 BPM to 360 BPM. A 40-300 range,
        # which is what this check first used, calls 42 Apple loops broken.
        for duration, beats in ((22.6, 8), (4.0, 24)):     # ~21 BPM and 360 BPM
            info = loop(audio=AudioInfo(sample_rate=44100,
                                        num_frames=int(44100 * duration),
                                        duration=duration))
            info.metadata.beat_count = beats
            assert not [m for m in messages(verify_apple_loop(info)).split(" | ")
                        if "implausible" in m], (duration, beats)


class TestBrowserVisibility:
    def test_a_category_apple_does_not_know_is_an_error(self):
        info = loop()
        info.metadata.category = "Synth"
        assert errors(verify_apple_loop(info))
        assert "category" in messages(verify_apple_loop(info))

    def test_a_genre_apple_does_not_know_is_an_error(self):
        info = loop()
        info.metadata.genre = "Skweee"
        assert "genre" in messages(verify_apple_loop(info))

    def test_a_missing_spotlight_genre_is_not_reported_at_all(self):
        # Only 21.6% of Apple's own loops put a genre in the info chunk, so
        # its absence is normal, not a defect. Warning on it fired against
        # 78% of Apple's library.
        info = loop(spotlight=SpotlightInfo())
        assert "Spotlight" not in messages(verify_apple_loop(info))


class TestKeyRules:
    def test_a_drum_loop_carrying_a_key_breaks_apples_own_rule(self):
        info = loop()
        info.metadata.category = "Drums"
        info.metadata.subcategory = "Kick"
        assert "key" in messages(verify_apple_loop(info)).lower()

    def test_a_key_type_without_a_key_signature_is_flagged(self):
        info = loop()
        info.metadata.key_signature = ""
        assert "key type" in messages(verify_apple_loop(info))


class TestMarkerAlignment:
    def test_marker_placement_is_not_judged(self):
        """Apple places markers on transients, not on a grid that spans the file.

        Across its 34,928 loops the last marker lands anywhere from 0.14x to
        8.8x the audio length, with only the median at exactly 1.0. A check
        that the grid must reach the end flagged 4,093 of Apple's own loops
        as errors -- it was a rule we invented, not one the format has.
        """
        info = loop(beat_markers=BeatMarkers(marker_count=65, positions=[0, 100000]))
        assert "last beat marker" not in messages(verify_apple_loop(info))


class TestNotALoopAtAll:
    def test_a_file_with_no_apple_loop_metadata_says_so(self):
        # A warning: Apple itself ships 148 untagged loops.
        info = AppleLoopInfo(file_path="x.caf", file_format="CAF", loop_type="audio")
        findings = verify_apple_loop(info)
        assert "no Apple Loop metadata" in messages(findings)
        assert not errors(findings)
