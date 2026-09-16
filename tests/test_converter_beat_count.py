"""AppleLoopConverter.calculate_beat_count must produce musical lengths."""

from convert_to_apple_loops import AppleLoopConverter


class TestCalculateBeatCount:
    def setup_method(self):
        self.converter = AppleLoopConverter()

    def test_absorbs_a_decay_tail_into_a_four_bar_loop(self):
        # 4 bars at 128 BPM = 7.50s. This file runs 7.85s because of the tail,
        # which is 16.75 raw beats -- plain rounding would call it 17 and make
        # Logic play the loop 4% slow.
        assert self.converter.calculate_beat_count(128, 7.85) == 16

    def test_respects_the_time_signature_when_snapping(self):
        # 10.0s at 120 BPM is 20.0 raw beats. 6 bars of 3/4 is 18, two beats
        # away -- too far to be tail slop, so the odd length stands.
        assert self.converter.calculate_beat_count(120, 10.0, time_signature='3/4') == 20

    def test_snaps_to_a_three_four_bar_line(self):
        # 9.1s at 120 BPM = 18.2 raw beats = 6 bars of 3/4.
        assert self.converter.calculate_beat_count(120, 9.1, time_signature='3/4') == 18

    def test_zero_duration_is_zero_beats(self):
        assert self.converter.calculate_beat_count(120, 0.0) == 0


from convert_to_apple_loops import LoopMetadata


class TestFinalizeBeatCount:
    """One place decides the beat count, so every code path agrees."""

    def setup_method(self):
        self.converter = AppleLoopConverter()

    def test_derives_the_beat_count_from_tempo_and_duration(self):
        meta = LoopMetadata(tempo=128, duration=7.85)
        self.converter.finalize_beat_count(meta)
        assert meta.beat_count == 16

    def test_uses_the_metadata_time_signature(self):
        meta = LoopMetadata(tempo=120, duration=9.1, time_signature='3/4')
        self.converter.finalize_beat_count(meta)
        assert meta.beat_count == 18

    def test_a_one_shot_ends_up_with_no_beats(self):
        meta = LoopMetadata(tempo=128, duration=7.5, is_one_shot=True)
        self.converter.finalize_beat_count(meta)
        assert meta.beat_count == 0

    def test_a_beat_count_already_known_survives_a_missing_tempo(self):
        # MIDI files carry their own beat count even with no tempo in the name.
        meta = LoopMetadata(beat_count=32, tempo=None, duration=16.0)
        self.converter.finalize_beat_count(meta)
        assert meta.beat_count == 32
